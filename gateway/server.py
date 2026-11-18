"""A2A Gateway inbound: la porta da cui gli altri team chiamano i nostri agenti.

Decisione architetturale ("porta unica, indirizzi multipli"):
  * gli altri team chiamano DIRETTAMENTE il sub-agente che serve (es. l'Agente
    Accessibilità) all'indirizzo  <gateway>/agents/<nome>/  : niente passaggio
    obbligato dall'orchestratore, come vuole lo spirito NANDA;
  * ma ogni chiamata passa da qui, che NON decide il percorso: applica solo regole.

Cosa fa, e quale rilievo della review risolve
  * identità del team chiamante (API key -> team)                           C
  * permessi per skill (scope) e skill pubblicate (card filtrate)           C
  * quote per team (richieste al minuto)                                    C
  * limite di passaggi tra agenti (hops) e trace_id obbligatorio            E
  * claims firmati verso l'agente: chi chiama e con quali permessi         C
  * isolamento dei task: un team vede/annulla solo i task che ha creato
  * risposta sempre allo stesso canale: stato del task A2A al chiamante    A
  * audit di ogni chiamata (evento al registry + file JSONL)               22
  * /index.json pubblico: gli altri team possono importarci come peer

Avvio:  MACH_GATEWAY_CONFIG=gateway/config.example.json \\
        uvicorn gateway.server:create_app --factory --port 9000
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a_lite._http import httpx
from a2a_lite.server import LEGACY_METHODS
from a2a_lite.types import AgentCard
from interop import mach
from registry.agentfacts import agentfacts_from_card


@dataclass
class TeamConfig:
    team: str
    api_key: str
    scopes: list[str] = field(default_factory=list)
    rate_per_min: int = 60


@dataclass
class UpstreamAgent:
    name: str                               # segmento dell'URL pubblico
    upstream: str                           # URL interno dell'agente
    agent_id: str
    published_skills: list[str] | None = None   # None = tutte tranne quelle "mach:private"


def _rpc_error(req_id: Any, code: int, message: str, status: int = 200, **data: Any) -> JSONResponse:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": err}, status_code=status)


class Gateway:
    def __init__(self, public_url: str, agents: list[UpstreamAgent], teams: list[TeamConfig], *,
                 secret: str | None = None, max_hops: int = mach.MAX_HOPS,
                 http: "httpx.AsyncClient | None" = None,
                 on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
                 audit_path: str | None = None, card_cache_s: float = 30.0) -> None:
        self.public_url = public_url.rstrip("/")
        self.agents = {a.name: a for a in agents}
        self.teams = {t.api_key: t for t in teams}
        self.secret = secret
        self.max_hops = max_hops
        self.http = http or httpx.AsyncClient(timeout=60)
        self.on_event = on_event
        self.audit_path = Path(audit_path) if audit_path else None
        self.card_cache_s = card_cache_s
        self._cards: dict[str, tuple[float, dict[str, Any]]] = {}
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self.task_owner: dict[str, str] = {}
        self.audit_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ configurazione
    @classmethod
    def from_config(cls, cfg: dict[str, Any], **kw: Any) -> "Gateway":
        teams = []
        for t in cfg.get("teams", []):
            key = t.get("api_key") or os.getenv(t.get("api_key_env", ""), "")
            if key:
                teams.append(TeamConfig(t["team"], key, t.get("scopes", []), t.get("rate_per_min", 60)))
        agents = [UpstreamAgent(a["name"], a["upstream"], a["agent_id"], a.get("published_skills"))
                  for a in cfg.get("agents", [])]
        return cls(cfg.get("public_url", "http://localhost:9000"), agents, teams,
                   secret=os.getenv("MACH_GATEWAY_SECRET") or cfg.get("secret"),
                   max_hops=cfg.get("max_hops", mach.MAX_HOPS), audit_path=cfg.get("audit_path"), **kw)

    # ------------------------------------------------------------------ HTTP
    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/", self._home, methods=["GET"]),
            Route("/index.json", self._index, methods=["GET"]),
            Route("/agents/{name}/.well-known/agent-card.json", self._card, methods=["GET"]),
            Route("/agents/{name}/.well-known/agent.json", self._card, methods=["GET"]),
            Route("/agents/{name}/", self._proxy, methods=["POST"]),
            Route("/agents/{name}", self._proxy, methods=["POST"]),
        ])

    async def _home(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "gateway": "Team MACH – A2A inbound", "profile": mach.EXTENSION_URI,
            "auth": "Authorization: Bearer <API key del vostro team>",
            "agents": [{"name": n, "agent_id": a.agent_id,
                        "card_url": f"{self.public_url}/agents/{n}/.well-known/agent-card.json"}
                       for n, a in self.agents.items()],
            "index": f"{self.public_url}/index.json",
        })

    async def _index(self, request: Request) -> JSONResponse:
        """AgentFacts dei nostri agenti pubblici, con URL pubblici: importabile da altri registry."""
        facts = []
        for n, a in self.agents.items():
            try:
                card = AgentCard.parse(await self.public_card(n))
            except (httpx.HTTPError, ValueError):
                continue
            facts.append(agentfacts_from_card(card, f"{self.public_url}/agents/{n}/.well-known/agent-card.json",
                                              agent_id=a.agent_id, origin="mach", trust="external"))
        return JSONResponse({"registry": "Team MACH gateway", "agents": facts})

    async def _card(self, request: Request) -> JSONResponse:
        name = request.path_params["name"]
        if name not in self.agents:
            return JSONResponse({"error": "agente non pubblicato"}, status_code=404)
        try:
            return JSONResponse(await self.public_card(name))
        except httpx.HTTPError:
            return JSONResponse({"error": "agente momentaneamente non raggiungibile"}, status_code=503)

    async def public_card(self, name: str) -> dict[str, Any]:
        """Card dell'agente interno, filtrata (solo skill pubblicate) e con URL pubblico."""
        cached = self._cards.get(name)
        if cached and time.monotonic() - cached[0] < self.card_cache_s:
            return cached[1]
        a = self.agents[name]
        r = await self.http.get(f"{a.upstream.rstrip('/')}/.well-known/agent-card.json")
        r.raise_for_status()
        card = r.json()
        url = f"{self.public_url}/agents/{name}/"
        card["skills"] = [s for s in card.get("skills", []) if self._published(a, s)]
        for itf in card.get("supportedInterfaces", []):
            itf["url"] = url
        if "url" in card:
            card["url"] = url
        card["securitySchemes"] = {"mach_api_key": {"type": "http", "scheme": "bearer",
                                                    "description": "API key per team, rilasciata dal team MACH"}}
        card["security"] = [{"mach_api_key": []}]
        self._cards[name] = (time.monotonic(), card)
        return card

    @staticmethod
    def _published(a: UpstreamAgent, skill: dict[str, Any]) -> bool:
        if "mach:private" in skill.get("tags", []):
            return False
        return a.published_skills is None or skill.get("id") in a.published_skills

    # ------------------------------------------------------------------ proxy
    async def _proxy(self, request: Request) -> JSONResponse:
        t0 = time.monotonic()
        name = request.path_params["name"]
        a = self.agents.get(name)
        if a is None:
            return JSONResponse({"error": "agente non pubblicato"}, status_code=404)

        team = self._auth(request)
        if team is None:
            await self._audit({"type": "inbound_denied", "agent": a.agent_id, "reason": "unauthenticated"})
            return _rpc_error(None, -32600, "Autenticazione richiesta: Authorization: Bearer <API key>", 401)
        if not self._rate_ok(team):
            await self._audit({"type": "inbound_denied", "team": team.team, "agent": a.agent_id, "reason": "rate"})
            return _rpc_error(None, -32600, f"Troppe richieste: limite {team.rate_per_min}/min", 429)

        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _rpc_error(None, -32700, "Parse error")
        req_id, method = body.get("id"), LEGACY_METHODS.get(body.get("method"), body.get("method"))
        params = body.get("params") or {}
        skill_id, mctx = None, None

        if method in ("SendMessage", "SendStreamingMessage"):
            msg = params.get("message")
            if not isinstance(msg, dict):
                return _rpc_error(req_id, -32602, "Manca 'message'")
            metadata = dict(msg.get("metadata") or {})
            metadata.pop(mach.GATEWAY_KEY, None)  # il client non può impersonare il gateway
            mctx = mach.MachContext.from_metadata(metadata)
            if not (metadata.get(mach.META_KEY) or {}).get("caller"):
                mctx.caller = team.team
            try:
                mctx.check(self.max_hops)
            except mach.MachError as exc:
                await self._audit({"type": "inbound_denied", "team": team.team, "agent": a.agent_id,
                                   "reason": exc.code, "trace_id": mctx.trace_id, "hops": mctx.hops})
                return _rpc_error(req_id, -32600, str(exc), mach_code=exc.code)
            skill_id = self._skill_of(msg)
            if skill_id:
                denied = await self._skill_denied(a, team, skill_id)
                if denied:
                    await self._audit({"type": "inbound_denied", "team": team.team, "agent": a.agent_id,
                                       "skill": skill_id, "reason": "forbidden", "trace_id": mctx.trace_id})
                    return _rpc_error(req_id, -32600, denied, 403, mach_code="FORBIDDEN")
            metadata.update(mctx.to_metadata())
            claims = {"team": team.team, "scopes": team.scopes, "trace_id": mctx.trace_id}
            metadata[mach.GATEWAY_KEY] = mach.sign_claims(claims, self.secret) if self.secret else claims
            msg["metadata"] = metadata
            body["params"] = {**params, "message": msg}
        elif method in ("GetTask", "CancelTask", "SubscribeToTask"):
            if self.task_owner.get(params.get("id")) != team.team:
                return _rpc_error(req_id, -32001, "Task not found")  # non riveliamo task di altri team
        elif method == "ListTasks":
            return _rpc_error(req_id, -32004, "ListTasks non è disponibile tramite il gateway")

        headers = {"Content-Type": "application/json"}
        if request.headers.get("a2a-version"):
            headers["A2A-Version"] = request.headers["a2a-version"]
        try:
            r = await self.http.post(a.upstream.rstrip("/") + "/", json=body, headers=headers)
            payload = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            await self._audit({"type": "inbound_error", "team": team.team, "agent": a.agent_id, "error": str(exc)})
            return _rpc_error(req_id, -32603, "Agente momentaneamente non raggiungibile", 502)

        state, task_id = self._task_info(payload)
        if method == "SendMessage" and task_id:
            self.task_owner[task_id] = team.team
        await self._audit({"type": "inbound", "team": team.team, "agent": a.agent_id, "method": method,
                           "skill": skill_id, "state": state, "task_id": task_id,
                           "trace_id": mctx.trace_id if mctx else None, "hops": mctx.hops if mctx else None,
                           "ms": round((time.monotonic() - t0) * 1000)})
        return JSONResponse(payload, status_code=r.status_code)

    # ------------------------------------------------------------------ regole
    def _auth(self, request: Request) -> TeamConfig | None:
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        return self.teams.get(key) if key else None

    def _rate_ok(self, team: TeamConfig) -> bool:
        now = time.monotonic()
        q = self._calls[team.team]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= team.rate_per_min:
            return False
        q.append(now)
        return True

    @staticmethod
    def _skill_of(msg: dict[str, Any]) -> str | None:
        for p in msg.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("skill"), str):
                return d["skill"]
        return None

    async def _skill_denied(self, a: UpstreamAgent, team: TeamConfig, skill_id: str) -> str | None:
        """Controllo anticipato per le richieste strutturate (l'agente ricontrolla comunque)."""
        try:
            card = await self.public_card(a.name)
        except httpx.HTTPError:
            return None  # deciderà l'agente
        skill = next((s for s in card.get("skills", []) if s.get("id") == skill_id), None)
        if skill is None:
            return f"La skill {skill_id} non è offerta da {a.agent_id}"
        writes = "mach:writes" in skill.get("tags", [])
        if not mach.scope_allows(team.scopes, a.agent_id, skill_id, writes):
            return f"Il team {team.team} non ha lo scope {a.agent_id}:{skill_id}"
        return None

    @staticmethod
    def _task_info(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        res = payload.get("result")
        if not isinstance(res, dict):
            return None, None
        task = res.get("task") if "task" in res else res
        if not isinstance(task, dict) or "status" not in task:
            return None, None
        return task["status"].get("state"), task.get("id")

    async def _audit(self, event: dict[str, Any]) -> None:
        event = {"ts": time.time(), **event}
        self.audit_log.append(event)
        if self.audit_path:
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception:
                pass


def create_app() -> Starlette:
    """Factory per uvicorn: legge la configurazione da MACH_GATEWAY_CONFIG."""
    from dotenv import load_dotenv

    from registry.client import RegistryClient

    load_dotenv()
    path = os.getenv("MACH_GATEWAY_CONFIG", str(Path(__file__).parent / "config.example.json"))
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    on_event = None
    if os.getenv("REGISTRY_URL"):
        on_event = RegistryClient(os.environ["REGISTRY_URL"]).post_event
    return Gateway.from_config(cfg, on_event=on_event).app()
