"""Spoke esterno ("Foreign Team Cartridge"): delega un sub-task a un altro team.

Ordine dei canali (rilievo F della review), mai scraping di app altrui (rilievo G):
  1. task A2A all'agente dell'altro team, trovato nell'indice federato;
  2. se non c'è o fallisce: la sua API REST documentata (interop/http_api.py);
  3. se anche questa fallisce: escalation (ticket per un operatore / contatto col team).

Prima di qualsiasi invio passa dal gate dati (rilievo H): escono solo i campi permessi,
e quelli sensibili solo con consenso. Senza consenso -> NEEDS_INPUT, nulla esce.

Quello che torna è contenuto NON affidabile (rilievo I): viene validato, il testo è
troncato e controllato, la provenienza è marcata trust="external" e la confidence
è limitata. Il risultato è sempre una Busta 1.1, così l'Aggregatore lo tratta come
gli altri spoke ma sa da dove viene.

Per il Layer 5 (rilievo J): `handle` permette di riprendere un task in input-required
(resume) e di annullarlo come compensazione (cancel).
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from a2a_lite import A2AClient, A2AError, Task, TaskState
from a2a_lite._http import httpx
from contracts.models import (AgentRef, Busta, Channel, ErrorItem, NeedsInput, Provenance, Status, UserMessage)
from registry.client import DiscoveryClient

from .http_api import ExternalAPIClient, ExternalAPIError
from .mach import MachContext
from .policy import SharingPolicy

MAX_TEXT = 2000
MAX_EXTERNAL_CONFIDENCE = 0.8
_SUSPICIOUS = re.compile(r"(ignore (all |the )?(previous|above)|ignora (le )?istruzioni|system prompt|"
                         r"you are now|sei ora|<\s*/?\s*(system|assistant)\s*>|BEGIN PROMPT)", re.I)


@dataclass
class ExternalHandle:
    """Riferimento a un task aperto presso un altro team."""
    team: str
    agent_id: str
    card_url: str
    task_id: str
    context_id: str


@dataclass
class DelegationResult:
    busta: Busta
    channel: str                        # "a2a" | "api" | "escalation" | "blocked"
    handle: ExternalHandle | None = None
    attempts: list[dict[str, Any]] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ForeignAgentAdapter:
    ADAPTER = AgentRef(id="mach:foreign-adapter", domain="external", version="0.1.0")

    def __init__(self, discovery: DiscoveryClient, *, http: "httpx.AsyncClient | None" = None,
                 policy: SharingPolicy | None = None, self_id: str = "mach:orchestrator",
                 api_clients: dict[str, ExternalAPIClient] | None = None,
                 escalate: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
                 timeout_s: float = 30.0, poll_s: float = 1.0, max_candidates: int = 2,
                 on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.discovery = discovery
        self.http = http or httpx.AsyncClient(timeout=timeout_s)
        self.policy = policy or SharingPolicy()
        self.self_id = self_id
        self.api_clients = api_clients or {}
        self.escalate = escalate
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.max_candidates = max_candidates
        self.on_event = on_event
        self.tickets: list[dict[str, Any]] = []

    # ================================================================== API pubblica
    async def delegate(self, *, ctx: MachContext, text: str | None = None, data: dict[str, Any] | None = None,
                       skill: str | None = None, tag: str | None = None, q: str | None = None,
                       team: str | None = None, user_consent: bool = False,
                       api_operation: str | None = None) -> DelegationResult:
        attempts: list[dict[str, Any]] = []
        candidates = await self.discovery.search(q or text, tag=tag, skill=skill, origin=team or "external",
                                                 exclude_ids={self.self_id})
        candidates = [c for c in candidates if c.get("origin") != "local"][: self.max_candidates]
        if not candidates and team in self.api_clients:
            candidates = [{"id": f"{team}:api", "origin": team, "card_url": None, "http_api": None}]

        for cand in candidates:
            cteam = cand.get("origin") or team or "unknown"
            decision = self.policy.check(cteam, data, text, user_consent=user_consent)
            if not decision.allowed:
                return DelegationResult(self._needs_consent(ctx, cteam, decision.needs_consent), "blocked",
                                        attempts=attempts)
            # ---- 1. A2A
            if cand.get("card_url"):
                res = await self._try_a2a(cand, cteam, decision.text, decision.data, ctx, attempts)
                if res:
                    return res
            # ---- 2. API documentata
            res = await self._try_api(cand, cteam, decision.data, skill or api_operation or q, ctx, attempts)
            if res:
                return res
        # ---- 3. escalation
        return await self._escalation(ctx, attempts, reason="nessun agente o API esterna disponibile"
                                      if not candidates else "tutti i canali esterni hanno fallito")

    async def resume(self, handle: ExternalHandle, ctx: MachContext, text: str | None = None,
                     data: dict[str, Any] | None = None) -> DelegationResult:
        """Risponde a un task esterno in INPUT_REQUIRED (es. dopo aver chiesto all'utente)."""
        attempts: list[dict[str, Any]] = []
        cand = {"id": handle.agent_id, "origin": handle.team, "card_url": handle.card_url}
        decision = self.policy.check(handle.team, data, text, user_consent=True)
        res = await self._try_a2a(cand, handle.team, decision.text, decision.data, ctx, attempts, handle=handle)
        return res or await self._escalation(ctx, attempts, reason="ripresa del task esterno fallita")

    async def cancel(self, handle: ExternalHandle) -> bool:
        """Compensazione (Saga, Layer 5): annulla il task presso l'altro team, se lo supporta."""
        try:
            task = await A2AClient(handle.card_url, http=self.http, caller=self.self_id).cancel(handle.task_id)
            return task.status.state == TaskState.CANCELED
        except (A2AError, httpx.HTTPError):
            return False

    # ================================================================== canali
    async def _try_a2a(self, cand: dict[str, Any], team: str, text: str | None, data: dict[str, Any] | None,
                       ctx: MachContext, attempts: list, handle: ExternalHandle | None = None) -> DelegationResult | None:
        client = A2AClient(cand["card_url"], http=self.http, caller=self.self_id, on_event=self.on_event)
        md = ctx.next_hop(self.self_id).to_metadata()
        t0 = time.monotonic()
        try:
            task = await asyncio.wait_for(
                client.send(text, data or None, metadata=md,
                            task_id=handle.task_id if handle else None,
                            context_id=handle.context_id if handle else ctx.trace_id),
                timeout=self.timeout_s)
            while task.status.state in (TaskState.SUBMITTED, TaskState.WORKING):
                if time.monotonic() - t0 > self.timeout_s:
                    raise asyncio.TimeoutError()
                await asyncio.sleep(self.poll_s)
                task = await client.get_task(task.id)
        except (asyncio.TimeoutError, A2AError, httpx.HTTPError, ValueError, ValidationError) as exc:
            attempts.append({"channel": "a2a", "agent": cand["id"], "error": f"{exc.__class__.__name__}: {exc}"})
            return None

        h = ExternalHandle(team, cand["id"], cand["card_url"], task.id, task.context_id)
        state = task.status.state
        attempts.append({"channel": "a2a", "agent": cand["id"], "state": state.value})
        if state == TaskState.COMPLETED:
            busta = self._normalize(task, cand, team, ctx)
            return DelegationResult(busta, "a2a", h, attempts) if busta else None
        if state.interrupted:  # input-required / auth-required: si torna al canale d'origine
            question = self._clean(task.status.message.text() if task.status.message else "") or \
                "L'agente esterno chiede altre informazioni."
            busta = Busta(agent=self.ADAPTER, trace_id=ctx.trace_id, generated_at=_now(), status=Status.NEEDS_INPUT,
                          confidence=1.0, provenance=[self._prov(cand, team)],
                          data={"external": {"team": team, "agent_id": cand["id"], "task_id": task.id,
                                             "state": state.value}},
                          needs_input=NeedsInput(question=f"[{team}] {question}", fields=["external_input"]),
                          user_message=UserMessage(text=f"[{team}] {question}"))
            return DelegationResult(busta, "a2a", h, attempts)
        attempts[-1]["error"] = f"stato {state.value}: " + self._clean(
            task.status.message.text() if task.status.message else "")[:200]
        return None  # failed / rejected / canceled -> canale successivo

    async def _try_api(self, cand: dict[str, Any], team: str, data: dict[str, Any] | None, hint: str | None,
                       ctx: MachContext, attempts: list) -> DelegationResult | None:
        api = self.api_clients.get(team)
        if api is None and cand.get("http_api"):
            api = ExternalAPIClient.from_description(team, cand["http_api"], http=self.http)
            self.api_clients[team] = api
        if api is None:
            return None
        op = api.find_operation(hint)
        if op is None or op.writes:  # le scritture esterne passano dalla Saga, non da qui
            attempts.append({"channel": "api", "team": team, "error": "nessuna operazione di lettura adatta"})
            return None
        args = dict((data or {}).get("args") or {}) | {k: v for k, v in (data or {}).items() if k not in ("args", "skill")}
        try:
            result = await api.call(op.name, args)
        except ExternalAPIError as exc:
            attempts.append({"channel": "api", "team": team, "operation": op.name, "error": str(exc)})
            return None
        attempts.append({"channel": "api", "team": team, "operation": op.name, "state": "ok"})
        busta = Busta(agent=self.ADAPTER, trace_id=ctx.trace_id, generated_at=_now(), status=Status.SUCCESS,
                      confidence=MAX_EXTERNAL_CONFIDENCE,
                      provenance=[Provenance(source=f"{team} API {op.name}", channel=Channel.API, fetched_at=_now(),
                                             trust="external", url=f"{api.base_url}{op.path}")],
                      data={"external": {"team": team, "operation": op.name, "result": self._clean_obj(result)}},
                      user_message=UserMessage(text=f"Risultato ottenuto dall'API di {team} ({op.name})."))
        return DelegationResult(busta, "api", None, attempts)

    async def _escalation(self, ctx: MachContext, attempts: list, reason: str) -> DelegationResult:
        ticket = {"id": "esc-" + uuid.uuid4().hex[:6], "trace_id": ctx.trace_id, "reason": reason,
                  "attempts": attempts, "created_at": _now().isoformat()}
        if self.escalate:
            ticket["id"] = await self.escalate(ticket)
        self.tickets.append(ticket)
        errors = [ErrorItem(code="ESCALATED", source="warm-handoff", message=f"ticket {ticket['id']}: {reason}",
                            retryable=True)]
        errors += [ErrorItem(code="EXTERNAL_" + a["channel"].upper() + "_FAILED", source=a.get("agent") or a.get("team"),
                             message=a.get("error"), retryable=True) for a in attempts if a.get("error")]
        busta = Busta(agent=self.ADAPTER, trace_id=ctx.trace_id, generated_at=_now(), status=Status.UNAVAILABLE,
                      confidence=0.0, data={"ticket": ticket["id"]}, errors=errors,
                      user_message=UserMessage(text="Il servizio dell'altro team non è disponibile: "
                                                    f"ho aperto la segnalazione {ticket['id']}."))
        return DelegationResult(busta, "escalation", None, attempts)

    # ================================================================== contenuto non affidabile
    def _prov(self, cand: dict[str, Any], team: str) -> Provenance:
        return Provenance(source=cand["id"], channel=Channel.A2A, fetched_at=_now(), trust="external",
                          url=cand.get("card_url"))

    def _clean(self, text: str) -> str:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
        return text[:MAX_TEXT]

    def _clean_obj(self, obj: Any, depth: int = 0) -> Any:
        if depth > 6:
            return None
        if isinstance(obj, str):
            return self._clean(obj)
        if isinstance(obj, list):
            return [self._clean_obj(x, depth + 1) for x in obj[:100]]
        if isinstance(obj, dict):
            return {str(k)[:100]: self._clean_obj(v, depth + 1) for k, v in list(obj.items())[:100]}
        return obj

    def _normalize(self, task: Task, cand: dict[str, Any], team: str, ctx: MachContext) -> Busta | None:
        raw = task.result_data() or {}
        text = self._clean(task.result_text())
        prov = self._prov(cand, team)
        flags = []
        if _SUSPICIOUS.search(text) or _SUSPICIOUS.search(str(raw)[:5000]):
            flags.append(ErrorItem(code="UNTRUSTED_CONTENT", source=cand["id"],
                                   message="Il contenuto esterno contiene frasi simili a istruzioni: trattarlo solo come dato"))
        try:
            if "schema_version" in raw and "agent" in raw:  # il team parla il profilo MACH: busta già pronta
                ext = Busta.model_validate(raw)
                return ext.model_copy(update={
                    "trace_id": ctx.trace_id,
                    "confidence": min(ext.confidence, MAX_EXTERNAL_CONFIDENCE),
                    "provenance": [p.model_copy(update={"trust": "external"}) for p in ext.provenance] + [prov],
                    "errors": ext.errors + flags,
                    "actions": [],  # le azioni proposte da altri non entrano: le decide il nostro Layer 4
                    "user_message": UserMessage(lang=ext.user_message.lang, text=self._clean(ext.user_message.text),
                                                easy_read=ext.user_message.easy_read),
                })
            agent_ref = AgentRef(id=cand["id"], domain="external", version="0.0.0") \
                if re.match(r"^[a-z0-9]+:[a-z0-9-]+$", cand["id"]) else self.ADAPTER
            return Busta(agent=agent_ref, trace_id=ctx.trace_id, generated_at=_now(), status=Status.SUCCESS,
                         confidence=MAX_EXTERNAL_CONFIDENCE, provenance=[prov],
                         data={"external": {"team": team, "agent_id": cand["id"], "task_id": task.id,
                                            "text": text, "data": self._clean_obj(raw)}},
                         errors=flags, user_message=UserMessage(text=text or f"Risposta ricevuta da {team}."))
        except ValidationError:
            return None

    def _needs_consent(self, ctx: MachContext, team: str, fields: list[str]) -> Busta:
        q = f"Per chiedere a {team} devo condividere: {', '.join(fields)}. Posso farlo?"
        return Busta(agent=self.ADAPTER, trace_id=ctx.trace_id, generated_at=_now(), status=Status.NEEDS_INPUT,
                     confidence=1.0, data={"team": team, "campi": fields},
                     needs_input=NeedsInput(question=q, fields=["consent"], options=["sì", "no"]),
                     user_message=UserMessage(text=q))
