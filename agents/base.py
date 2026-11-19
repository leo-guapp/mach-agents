"""BaseAgent: tutto ciò che è uguale per ogni agente di servizio.

Un agente concreto dichiara le sue skill come metodi decorati:

    class MobilityAgent(BaseAgent):
        name = "Agente Mobilità"

        @skill("percorso", "Percorso", "Calcola un percorso con i mezzi ATM", tags=["mobilita"])
        async def percorso(self, ctx, origine: str, destinazione: str, senza_barriere: bool = False) -> Esito:
            ...

Il BaseAgent si occupa di:
  * costruire la Agent Card dalle skill (con la descrizione degli argomenti);
  * capire quale skill chiamare, in due modi:
      - richiesta strutturata: DataPart {"skill": "percorso", "args": {...}}  (veloce, deterministica)
      - linguaggio naturale: routing con l'LLM, o per parole chiave se l'LLM è spento;
  * chiedere i dati mancanti (INPUT_REQUIRED) e riprendere il task quando arrivano;
  * chiamare altri agenti trovati nel registry (call_agent), propagando il contesto MACH;
  * applicare le regole del profilo MACH prima di eseguire una skill:
      - limite di passaggi tra agenti e scadenza (trace_id, hops, deadline);
      - permessi del team chiamante, se la richiesta arriva dal gateway (skill pubbliche e scope);
      - consenso esplicito per le skill con effetti reali (requires_consent);
  * rispondere sempre con la Busta 1.1 (contracts/), qualunque sia l'esito.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError, create_model

from a2a_lite import (A2AClient, A2AServer, AgentCapabilities, AgentCard, AgentExtension, AgentInterface,
                      AgentProvider, AgentSkill, Message, Reply, Task, TaskContext, TaskState, TaskStatus)
from a2a_lite._http import httpx
from contracts.adapter import busta_from_esito
from contracts.models import AgentRef
from interop import mach
from interop.mach import MachContext
from registry.client import RegistryClient

from .envelope import Esito, Status
from .llm import LLM, json_call, llm_from_env
from .tools import ToolRegistry, schema_from_signature

log = logging.getLogger("agents")
_FROM_ENV = object()


def skill(id: str, name: str, description: str, tags: list[str] | None = None,
          examples: list[str] | None = None, *, writes: bool = False, requires_consent: bool | None = None,
          public: bool = True) -> Callable:
    """writes: la skill ha effetti reali (prenota, annulla...).
    requires_consent: serve il consenso nel messaggio (default: uguale a writes).
    public: pubblicata agli altri team tramite il gateway."""
    def deco(fn: Callable) -> Callable:
        fn._skill = {"id": id, "name": name, "description": description,
                     "tags": tags or [], "examples": examples or [], "writes": writes,
                     "requires_consent": writes if requires_consent is None else requires_consent,
                     "public": public}
        return fn
    return deco


@dataclass
class InvokeResult:
    """Risultato di BaseAgent.invoke: stato A2A, id del task (per continuare) e Busta."""
    task_id: str
    state: str
    busta: dict[str, Any]

    @property
    def text(self) -> str:
        return (self.busta.get("user_message") or {}).get("text", "")


@dataclass
class SkillSpec:
    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str]
    fn: Callable
    schema: dict[str, Any]
    writes: bool = False
    requires_consent: bool = False
    public: bool = True

    @property
    def required(self) -> list[str]:
        return list(self.schema.get("required", []))

    def args_doc(self) -> str:
        props = self.schema.get("properties", {})
        parts = []
        for k, v in props.items():
            t = v.get("type") or "/".join(x.get("type", "?") for x in v.get("anyOf", []))
            parts.append(f"{k} ({t}{', obbligatorio' if k in self.required else ''})")
        return ", ".join(parts) or "nessuno"


def _norm_tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 2}


_YES = {"si", "sì", "yes", "true", "certo", "ok", "va bene", "y", "1"}
_NO = {"no", "false", "n", "0", "nessuna", "non"}


class BaseAgent:
    name: str = "Agente"
    description: str = ""
    version: str = "0.1.0"
    provider: str = "Team MACH"
    agent_id: str = "mach:agent"   # uguale all'id nel registry e nella Busta
    domain: str = "generic"
    domande: dict[str, str] = {}  # domanda da fare per ogni argomento mancante

    def __init__(self, llm: LLM | None | object = _FROM_ENV, *, http: "httpx.AsyncClient | None" = None,
                 registry: RegistryClient | None = None) -> None:
        self.llm: LLM | None = llm_from_env() if llm is _FROM_ENV else llm  # type: ignore[assignment]
        self.http = http or httpx.AsyncClient(timeout=60)
        self.registry = registry
        self.tools = ToolRegistry()
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}   # task in attesa -> (skill, argomenti)
        self._last_missing: dict[str, list[str]] = {}                # task in attesa -> campi richiesti
        self._local_tasks: dict[str, Task] = {}                       # task aperti via invoke()
        self._skills: dict[str, SkillSpec] = {}
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            meta = getattr(member, "_skill", None)
            if meta:
                self._skills[meta["id"]] = SkillSpec(fn=member, schema=schema_from_signature(member), **meta)
        self.gateway_secret = os.getenv("MACH_GATEWAY_SECRET") or None
        self.setup()

    @property
    def agent_ref(self) -> AgentRef:
        return AgentRef(id=self.agent_id, domain=self.domain, version=self.version)

    def setup(self) -> None:
        """Da sovrascrivere: caricare dati, registrare tool, ecc."""

    # ------------------------------------------------------------------ card
    def card(self, base_url: str) -> AgentCard:
        return AgentCard(
            name=self.name, description=self.description, version=self.version,
            provider=AgentProvider(organization=self.provider),
            supported_interfaces=[AgentInterface(url=base_url.rstrip("/") + "/")],
            capabilities=AgentCapabilities(extensions=[AgentExtension(
                uri=mach.EXTENSION_URI, required=False,
                description="Profilo MACH: DataPart {skill,args}, metadata.mach (trace_id, hops, consent), "
                            "risposta in Busta 1.1",
                params={"agent_id": self.agent_id, "domain": self.domain, "profile": mach.PROFILE_VERSION})]),
            skills=[AgentSkill(
                id=s.id, name=s.name, examples=s.examples or None,
                tags=s.tags + (["mach:writes"] if s.writes else []) + (["mach:consent"] if s.requires_consent else [])
                + ([] if s.public else ["mach:private"]),
                description=(f"{s.description} Argomenti: {s.args_doc()}. "
                             f"Chiamata strutturata: DataPart {{\"skill\": \"{s.id}\", \"args\": {{...}}}}; "
                             f"accetta anche testo libero."
                             + (" Richiede consenso in metadata.mach.consent." if s.requires_consent else "")),
            ) for s in self._skills.values()],
        )

    # ------------------------------------------------------------------ esecuzione
    async def handle(self, ctx: TaskContext) -> Reply:
        d = ctx.data
        if ctx.is_continuation and ctx.task_id in self._pending:
            skill_id, args = self._pending.pop(ctx.task_id)
            missing = self._missing(skill_id, args) or self._last_missing.pop(ctx.task_id, [])
            if isinstance(d.get("args"), dict):
                args.update(d["args"])
            elif d:
                args.update({k: v for k, v in d.items() if k != "skill"})
            elif missing and ctx.text and missing[0] != "consent":
                args[missing[0]] = self._coerce(skill_id, missing[0], ctx.text)
        elif "skill" in d:
            skill_id, args = d["skill"], dict(d.get("args") or {})
        else:
            skill_id, args = await self.route(ctx)

        if skill_id is None:
            elenco = "; ".join(f"{s.name}: {s.description}" for s in self._skills.values())
            return self.reply(Esito(Status.OUT_OF_SCOPE, f"Non è un compito di {self.name}. So fare: {elenco}"), ctx)
        spec = self._skills.get(skill_id)
        if spec is None:
            return self.reply(Esito.errore(f"Skill sconosciuta: {skill_id}", {"skills": list(self._skills)}), ctx)

        blocked = self._check_access(ctx, spec)
        if blocked is not None:
            return self.reply(blocked, ctx, skill_id)
        missing = self._missing(skill_id, args)
        if missing:
            return self._ask(ctx, skill_id, args, missing)
        try:
            model = create_model(f"{skill_id}_args", **self._fields(spec.fn))
            kwargs = model.model_validate(args).model_dump()
        except ValidationError as exc:
            return self.reply(Esito.errore(f"Argomenti non validi per {skill_id}",
                                           {"dettagli": json.loads(exc.json())}), ctx, skill_id)
        if spec.requires_consent:
            needed = self._consent_missing(ctx, spec, kwargs)
            if needed is not None:
                self._pending[ctx.task_id] = (skill_id, args)
                self._last_missing[ctx.task_id] = ["consent"]
                return self.reply(needed, ctx, skill_id)
        esito = await spec.fn(ctx, **kwargs)
        if esito.status == Status.NEEDS_INPUT:
            self._pending[ctx.task_id] = (skill_id, args)
            self._last_missing[ctx.task_id] = esito.mancanti
        return self.reply(esito, ctx, skill_id)

    # ------------------------------------------------------------------ esecuzione senza rete
    async def invoke(self, skill_id: str | None = None, args: dict[str, Any] | None = None, *,
                     text: str | None = None, metadata: dict[str, Any] | None = None,
                     task_id: str | None = None) -> InvokeResult:
        """Esegue l'agente direttamente, senza HTTP: lo usano l'export MCP, il ponte verso
        l'adapter NANDA e i test. Con task_id si risponde a un INPUT_REQUIRED precedente."""
        data = {"skill": skill_id, "args": args or {}} if skill_id else None
        msg = Message.user(text, data, metadata=metadata)
        prev = self._local_tasks.get(task_id) if task_id else None
        task = prev or Task(status=TaskStatus(state=TaskState.WORKING), history=[])
        msg.task_id, msg.context_id = task.id, task.context_id
        task.history = (task.history or []) + [msg]
        reply = await self.handle(TaskContext(task=task, message=msg, is_continuation=prev is not None))
        task.status = TaskStatus(state=reply.state)
        if reply.state.interrupted:
            self._local_tasks[task.id] = task
        else:
            self._local_tasks.pop(task.id, None)
        return InvokeResult(task.id, reply.state.value, dict(reply.data or {}))

    # ------------------------------------------------------------------ profilo MACH
    def mach_context(self, ctx: TaskContext) -> MachContext:
        return MachContext.from_metadata(ctx.metadata, default_trace=ctx.context_id)

    def _check_access(self, ctx: TaskContext, spec: SkillSpec) -> Esito | None:
        """Loop tra team, scadenze e permessi del team chiamante (se la richiesta arriva dal gateway)."""
        try:
            self.mach_context(ctx).check()
        except mach.MachError as exc:
            return Esito(Status.OUT_OF_SCOPE, str(exc), {"codice": exc.code})
        claims = ctx.metadata.get(mach.GATEWAY_KEY)
        if claims is None:
            # Chiamata interna (orchestratore o altro nostro agente). In produzione gli agenti
            # non sono raggiungibili da fuori se non tramite il gateway, che aggiunge sempre i claims.
            return None
        try:
            claims = mach.verify_claims(claims, self.gateway_secret)
        except mach.InvalidGatewayClaims as exc:
            return Esito(Status.OUT_OF_SCOPE, str(exc), {"codice": exc.code})
        if not spec.public:
            return Esito(Status.OUT_OF_SCOPE, f"La skill {spec.id} non è offerta agli altri team.",
                         {"codice": "FORBIDDEN"})
        if not mach.scope_allows(claims.get("scopes", []), self.agent_id, spec.id, spec.writes):
            return Esito(Status.OUT_OF_SCOPE,
                         f"Il team {claims.get('team')} non ha il permesso per {self.agent_id}:{spec.id}.",
                         {"codice": "FORBIDDEN", "scope_richiesto": f"{self.agent_id}:{spec.id}"})
        return None

    def _consent_missing(self, ctx: TaskContext, spec: SkillSpec, kwargs: dict[str, Any]) -> Esito | None:
        key = kwargs.get("chiave_idempotenza") or kwargs.get("idempotency_key")
        consent_data = ctx.data.get("consent")
        mctx = self.mach_context(ctx)
        if isinstance(consent_data, dict):  # consenso anche come DataPart, oltre che nei metadata
            mctx.consent.append(mach.Consent(**{k: v for k, v in consent_data.items()
                                                if k in mach.Consent.__dataclass_fields__}))
        if mctx.consent_for(spec.id, key):
            return None
        azione = {"id": f"act-{spec.id}", "type": "book" if "prenot" in spec.id else "submit_request",
                  "target": str(kwargs.get("slot_id") or kwargs), "summary": self.consent_summary(spec, kwargs),
                  "requires_consent": True, "idempotency_key": key or f"{ctx.context_id}:{spec.id}"}
        return Esito(Status.NEEDS_INPUT,
                     f"Serve il consenso esplicito dell'utente per: {azione['summary']}. "
                     "Rimanda la richiesta con metadata.mach.consent.",
                     {"azione_proposta": azione,
                      "consent_template": {"skill": spec.id, "granted_by": "user", "reference": "<id decisione>",
                                           "idempotency_key": azione["idempotency_key"]}},
                     mancanti=["consent"])

    def consent_summary(self, spec: SkillSpec, kwargs: dict[str, Any]) -> str:
        """Frase mostrata all'utente per chiedere il consenso: da sovrascrivere per renderla più chiara."""
        dettagli = ", ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None and k != "chiave_idempotenza")
        return f"{spec.name} ({dettagli})"

    def reply(self, esito: Esito, ctx: TaskContext, skill_id: str | None = None) -> Reply:
        """Esito interno -> Busta 1.1 -> risposta A2A con lo stato corrispondente."""
        busta = busta_from_esito(esito, self.agent_ref, self.mach_context(ctx).trace_id, skill=skill_id)
        return Reply(state=TaskState(busta.a2a_state), text=busta.user_message.text, data=busta.to_json(),
                     artifact_name=skill_id or "result")

    def _ask(self, ctx: TaskContext, skill_id: str, args: dict, missing: list[str]) -> Reply:
        self._pending[ctx.task_id] = (skill_id, args)
        self._last_missing[ctx.task_id] = missing
        domanda = " ".join(self.domande.get(m, f"Mi serve il dato «{m}».") for m in missing)
        return self.reply(Esito.chiedi(domanda, missing), ctx, skill_id)

    def _missing(self, skill_id: str, args: dict[str, Any]) -> list[str]:
        spec = self._skills.get(skill_id)
        return [r for r in spec.required if args.get(r) in (None, "")] if spec else []

    @staticmethod
    def _fields(fn: Callable) -> dict[str, Any]:
        out = {}
        for name, p in inspect.signature(fn).parameters.items():
            if name in ("self", "ctx"):
                continue
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
            out[name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
        return out

    def _coerce(self, skill_id: str, field: str, text: str) -> Any:
        prop = self._skills[skill_id].schema.get("properties", {}).get(field, {})
        types = {prop.get("type")} | {x.get("type") for x in prop.get("anyOf", [])}
        t = text.strip().lower().rstrip(".!")
        if "boolean" in types:
            if t in _YES or t.startswith(("si", "sì", "yes")):
                return True
            if t in _NO or t.startswith("no"):
                return False
        if "integer" in types and t.isdigit():
            return int(t)
        return text.strip()

    # ------------------------------------------------------------------ routing
    async def route(self, ctx: TaskContext) -> tuple[str | None, dict[str, Any]]:
        """Sceglie la skill per una richiesta in linguaggio naturale."""
        if self.llm is not None:
            try:
                choice = await json_call(self.llm, self._router_prompt(), ctx.text)
                if choice and (choice.get("skill") in self._skills or choice.get("skill") is None):
                    return choice.get("skill"), choice.get("args") or {}
            except Exception:
                log.warning("routing LLM fallito, uso le parole chiave", exc_info=True)
        return self.route_keywords(ctx.text)

    def _router_prompt(self) -> str:
        skills = "\n".join(f'- "{s.id}": {s.description} Argomenti: {s.args_doc()}' for s in self._skills.values())
        return (f"Sei il router di {self.name}. Scegli la skill adatta alla richiesta ed estrai gli argomenti "
                f"che sono esplicitamente presenti (non inventare nulla).\nSkill:\n{skills}\n"
                'Rispondi SOLO con JSON: {"skill": "<id o null>", "args": {...}}')

    def route_keywords(self, text: str) -> tuple[str | None, dict[str, Any]]:
        q = _norm_tokens(text)
        best, best_score = None, 0
        for s in self._skills.values():
            score = len(q & _norm_tokens(" ".join([s.name, s.description, *s.tags, *s.examples])))
            if score > best_score:
                best, best_score = s.id, score
        return (best, self.extract_args(best, text)) if best else (None, {})

    def extract_args(self, skill_id: str, text: str) -> dict[str, Any]:
        """Estrazione argomenti senza LLM: da sovrascrivere con regole semplici."""
        return {}

    # ------------------------------------------------------------------ agente -> agente
    async def call_agent(self, text: str | None = None, data: dict | None = None, *, q: str | None = None,
                         tag: str | None = None, skill_id: str | None = None,
                         context_id: str | None = None, ctx: TaskContext | None = None) -> Task | None:
        """Trova un agente nel registry per capacità e gli invia un messaggio A2A.
        Se passi ctx, il contesto MACH viene inoltrato con hops+1 (stesso trace_id)."""
        if not self.registry:
            return None
        found = await self.registry.search(q, tag=tag, skill=skill_id, origin="local")
        found = [f for f in found if f["id"] != self.agent_id]
        if not found:
            return None
        mctx = self.mach_context(ctx) if ctx else MachContext(trace_id=context_id or mach.new_trace_id())
        client = A2AClient(found[0]["card_url"], http=self.http, caller=self.agent_id,
                           on_event=self.registry.post_event)
        return await client.send(text, data, context_id=context_id,
                                 metadata=mctx.next_hop(self.agent_id).to_metadata())


# ---------------------------------------------------------------------------- avvio

def build_app(agent: BaseAgent, base_url: str, registry_url: str | None = None):
    registry = RegistryClient(registry_url, http=agent.http) if registry_url else None
    agent.registry = agent.registry or registry
    card = agent.card(base_url)
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"

    @asynccontextmanager
    async def lifespan(app):
        bg = None
        if registry:
            try:
                agent_id = await registry.register(card, card_url, agent_id=agent.agent_id)
                bg = asyncio.create_task(registry.keep_alive(agent_id, card, card_url))
                log.info("registrato come %s", agent_id)
            except Exception as exc:
                log.warning("registry non raggiungibile (%s): l'agente funziona lo stesso", exc)
        yield
        if bg:
            bg.cancel()

    on_event = registry.post_event if registry else None
    return A2AServer(card, agent.handle, on_event=on_event).app(lifespan=lifespan)


def run(agent_cls: type[BaseAgent], default_port: int) -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(description=f"Avvia {agent_cls.name}")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=default_port)
    p.add_argument("--public-url", default=None, help="URL con cui gli altri raggiungono l'agente")
    p.add_argument("--registry", default=os.getenv("REGISTRY_URL"))
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    public = a.public_url or f"http://localhost:{a.port}"
    uvicorn.run(build_app(agent_cls(), public, a.registry), host=a.host, port=a.port)
