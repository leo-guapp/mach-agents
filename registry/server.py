"""Registry / indice federato in stile NANDA.

Avvio:  uvicorn registry.server:app --port 8000

Perché "federato": all'hackathon ogni team avrà i suoi agenti e forse il suo indice.
Il nostro registry tiene i NOSTRI agenti (origin="local") e importa quelli degli altri
(origin=<team>, trust="external"), così l'orchestratore e lo spoke esterno cercano in
un posto solo. Allo stesso tempo pubblica /index.json, che gli altri possono importare.

Endpoint
  POST   /agents                  registra {card, card_url, agent_id?, team?}  -> {id, facts}
  GET    /agents                  elenco (solo vivi; ?all=1 per tutti; ?origin=local|external)
  GET    /agents/{id}             AgentFacts + card + stato
  POST   /agents/{id}/heartbeat   "sono vivo"
  DELETE /agents/{id}
  GET    /search?q=&tag=&skill=&origin=   ricerca per capacità, ordinata per punteggio
  POST   /import                  {card_url, team, http_api?}: il registry scarica la card e la indicizza
  GET    /peers  POST /peers      indici di altri team da cui importare {url, team}
  POST   /peers/sync              aggiorna subito tutti i peer
  GET    /index.json              i nostri agenti (AgentFacts), da far importare agli altri
  POST   /events  GET /events     traccia dei messaggi tra agenti (per la demo)
  GET    /                        pagina HTML con agenti e messaggi in tempo reale
  GET    /.well-known/nanda.json  descrizione del registry

Se gli organizzatori forniscono un indice (NANDA o altro): aggiungetelo come peer
(se espone card o un elenco) oppure riscrivete registry/client.py con gli stessi metodi.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from a2a_lite._http import httpx
from a2a_lite.types import AgentCard

from .agentfacts import agentfacts_from_card, namespace_for, resolve_agent_id, slugify

_STOP = {"il", "lo", "la", "i", "gli", "le", "un", "una", "di", "a", "da", "in", "con", "su", "per", "e", "o",
         "che", "mi", "del", "della", "al", "alla", "the", "a", "of", "to", "and", "for"}


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 2 and t not in _STOP}


class RegistrationError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Registry:
    def __init__(self, ttl_s: float = 120.0, external_ttl_s: float = 900.0, max_events: int = 500,
                 namespace: str = "mach", http: "httpx.AsyncClient | None" = None,
                 local_token: str | None = None, sync_every_s: float | None = None) -> None:
        self.ttl_s = ttl_s                    # nostri agenti: heartbeat ogni 30 s
        self.external_ttl_s = external_ttl_s  # agenti esterni: validi fino al prossimo sync
        self.namespace = namespace
        self.http = http or httpx.AsyncClient(timeout=10)
        self.local_token = local_token        # se impostato, serve per registrare id "mach:..."
        self.sync_every_s = sync_every_s
        self.entries: dict[str, dict[str, Any]] = {}
        self.peers: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._seq = 0

    # ------------------------------------------------------------------ registrazione
    def register(self, card_dict: dict[str, Any], card_url: str, agent_id: str | None = None,
                 team: str | None = None, token: str | None = None,
                 http_api: dict[str, Any] | None = None) -> dict[str, Any]:
        card = AgentCard.parse(card_dict)
        aid = resolve_agent_id(card, namespace_for(team) if team else self.namespace, agent_id)
        is_ours = aid.startswith(f"{self.namespace}:") and not team
        if aid.startswith(f"{self.namespace}:") and team:
            raise RegistrationError(f"Il namespace '{self.namespace}:' è riservato ai nostri agenti", 403)
        if is_ours and self.local_token and token != self.local_token:
            raise RegistrationError("Token di registrazione mancante o errato", 403)
        origin, trust = ("local", "internal") if is_ours else (slugify(team or aid.split(":")[0]), "external")
        facts = agentfacts_from_card(card, card_url, self.namespace, agent_id=aid, origin=origin, trust=trust,
                                     http_api=http_api)
        now = time.time()
        self.entries[aid] = {"facts": facts, "card": card_dict, "card_url": card_url,
                             "registered_at": now, "last_seen": now}
        self.add_event({"type": "registered", "agent": card.name, "id": aid, "origin": origin})
        return facts

    async def import_card(self, card_url: str, team: str, http_api: dict[str, Any] | None = None) -> dict[str, Any]:
        """Scarica la card di un agente esterno e la indicizza (verifica anche che risponda)."""
        r = await self.http.get(card_url)
        r.raise_for_status()
        return self.register(r.json(), card_url, team=team, http_api=http_api)

    def _store_facts(self, facts: dict[str, Any], team: str) -> None:
        """Indicizza AgentFacts già pronti (da /index.json di un altro team)."""
        aid = facts.get("id") or f"{namespace_for(team)}:{slugify(facts.get('label', 'agente'))}"
        if aid.startswith(f"{self.namespace}:"):
            return  # nessuno può "iniettare" agenti nel nostro namespace
        card_url = ((facts.get("extensions") or {}).get("a2a") or {}).get("agentCardUrl")
        if not card_url:
            return
        facts = {**facts, "id": aid, "origin": slugify(team), "trust": "external"}
        now = time.time()
        self.entries[aid] = {"facts": facts, "card": None, "card_url": card_url,
                             "registered_at": now, "last_seen": now}

    # ------------------------------------------------------------------ federazione
    def add_peer(self, url: str, team: str) -> dict[str, Any]:
        peer = {"url": url.rstrip("/"), "team": slugify(team), "last_sync": None, "last_error": None, "agents": 0}
        self.peers[peer["url"]] = peer
        return peer

    async def sync_peer(self, url: str) -> dict[str, Any]:
        """Prova, in ordine: /index.json (AgentFacts) -> elenco di card URL -> una singola card."""
        peer = self.peers[url.rstrip("/")]
        try:
            imported = await self._sync_from(peer["url"], peer["team"])
            peer.update(last_sync=time.time(), last_error=None, agents=imported)
        except (httpx.HTTPError, ValueError, ValidationError, KeyError, TypeError) as exc:
            peer.update(last_error=str(exc))
        self.add_event({"type": "peer_sync", "agent": peer["team"], "text": peer["last_error"] or f"{peer['agents']} agenti"})
        return peer

    async def _sync_from(self, url: str, team: str) -> int:
        if not url.endswith(".json"):
            r = await self.http.get(f"{url}/index.json")
            if r.status_code == 200 and isinstance(r.json(), dict) and "agents" in r.json():
                agents = r.json()["agents"]
                for facts in agents:
                    self._store_facts(facts, team)
                return len(agents)
        r = await self.http.get(url)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):  # elenco di card URL (es. un file condiviso tra team)
            for card_url in body:
                await self.import_card(card_url, team)
            return len(body)
        if isinstance(body, dict) and "agents" in body:
            for facts in body["agents"]:
                self._store_facts(facts, team)
            return len(body["agents"])
        self.register(body, url, team=team)  # una singola Agent Card
        return 1

    async def sync_all(self) -> list[dict[str, Any]]:
        return [await self.sync_peer(u) for u in list(self.peers)]

    def index_json(self) -> dict[str, Any]:
        return {"registry": "MACH team registry", "namespace": self.namespace, "generated_at": time.time(),
                "agents": [e["facts"] for e in self.entries.values()
                           if e["facts"]["origin"] == "local" and self.alive(e)]}

    # ------------------------------------------------------------------ ricerca
    def alive(self, entry: dict[str, Any]) -> bool:
        ttl = self.ttl_s if entry["facts"].get("origin") == "local" else self.external_ttl_s
        return time.time() - entry["last_seen"] < ttl

    def summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        f = entry["facts"]
        return {"id": f["id"], "label": f["label"], "description": f["description"],
                "rpc_url": f["endpoints"]["static"][0], "card_url": entry["card_url"],
                "origin": f.get("origin", "local"), "trust": f.get("trust", "internal"),
                "mach_profile": f.get("mach_profile", False),
                "http_api": (f.get("extensions") or {}).get("http_api"),
                "skills": [{"id": s["id"], "tags": s["tags"]} for s in f["skills"]],
                "alive": self.alive(entry)}

    def search(self, q: str | None = None, tag: str | None = None, skill: str | None = None,
               include_stale: bool = False, origin: str | None = None) -> list[dict[str, Any]]:
        """origin: None = tutti, "local" = solo nostri, "external" = solo altri team, oppure il nome di un team."""
        q_tokens = _tokens(q or "")
        results = []
        for entry in self.entries.values():
            if not include_stale and not self.alive(entry):
                continue
            f = entry["facts"]
            eo = f.get("origin", "local")
            if origin == "local" and eo != "local" or origin == "external" and eo == "local" \
                    or origin not in (None, "local", "external") and eo != origin:
                continue
            score, matched = 0.0, []
            for s in f["skills"]:
                if "mach:private" in s["tags"]:
                    continue
                s_score = 0.0
                if skill and s["id"] == skill:
                    s_score += 10
                if tag and tag.lower() in [t.lower() for t in s["tags"]]:
                    s_score += 5
                if q_tokens:
                    text = " ".join([s["description"], " ".join(s["tags"]), " ".join(s.get("examples", []))])
                    s_score += len(q_tokens & _tokens(text))
                if s_score:
                    matched.append(s["id"])
                    score += s_score
            if q_tokens:
                score += 0.5 * len(q_tokens & _tokens(f["label"] + " " + f["description"]))
            if (skill or tag) and not matched:
                continue
            if score > 0 or not (q or tag or skill):
                results.append({**self.summary(entry), "score": score, "matched_skills": matched})
        return sorted(results, key=lambda r: r["score"], reverse=True)

    def add_event(self, event: dict[str, Any]) -> None:
        self._seq += 1
        self.events.append({"seq": self._seq, "ts": time.time(), **event})

    # ------------------------------------------------------------------ HTTP
    def app(self) -> Starlette:
        @asynccontextmanager
        async def lifespan(app):
            task = None
            if self.sync_every_s:
                async def loop():
                    while True:
                        await self.sync_all()
                        await asyncio.sleep(self.sync_every_s)
                task = asyncio.create_task(loop())
            yield
            if task:
                task.cancel()

        return Starlette(lifespan=lifespan, routes=[
            Route("/", self._ui, methods=["GET"]),
            Route("/.well-known/nanda.json", self._wellknown, methods=["GET"]),
            Route("/index.json", self._index, methods=["GET"]),
            Route("/agents", self._agents, methods=["GET", "POST"]),
            Route("/agents/{agent_id:str}", self._agent, methods=["GET", "DELETE"]),
            Route("/agents/{agent_id:str}/heartbeat", self._heartbeat, methods=["POST"]),
            Route("/search", self._search, methods=["GET"]),
            Route("/import", self._import, methods=["POST"]),
            Route("/peers", self._peers, methods=["GET", "POST"]),
            Route("/peers/sync", self._peers_sync, methods=["POST"]),
            Route("/events", self._events, methods=["GET", "POST"]),
        ])

    async def _wellknown(self, request: Request) -> JSONResponse:
        return JSONResponse({"name": "MACH team registry", "namespace": self.namespace, "format": "agentfacts",
                             "endpoints": {"register": "/agents", "search": "/search", "resolve": "/agents/{id}",
                                           "index": "/index.json", "import": "/import", "peers": "/peers"}})

    async def _index(self, request: Request) -> JSONResponse:
        return JSONResponse(self.index_json())

    async def _agents(self, request: Request) -> JSONResponse:
        if request.method == "POST":
            try:
                body = await request.json()
                facts = self.register(body["card"], body["card_url"], body.get("agent_id"), body.get("team"),
                                      request.headers.get("x-registry-token"), body.get("http_api"))
            except RegistrationError as exc:
                return JSONResponse({"error": str(exc)}, status_code=exc.status)
            except (KeyError, ValueError, ValidationError) as exc:
                return JSONResponse({"error": f"registrazione non valida: {exc}"}, status_code=400)
            return JSONResponse({"id": facts["id"], "facts": facts}, status_code=201)
        p = request.query_params
        show_all = p.get("all") in ("1", "true")
        origin = p.get("origin")
        out = [self.summary(e) for e in self.entries.values() if show_all or self.alive(e)]
        if origin == "local":
            out = [x for x in out if x["origin"] == "local"]
        elif origin == "external":
            out = [x for x in out if x["origin"] != "local"]
        return JSONResponse(out)

    async def _agent(self, request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        entry = self.entries.get(agent_id)
        if not entry:
            return JSONResponse({"error": "agente non trovato"}, status_code=404)
        if request.method == "DELETE":
            del self.entries[agent_id]
            return JSONResponse({"deleted": agent_id})
        return JSONResponse({**entry, "alive": self.alive(entry)})

    async def _heartbeat(self, request: Request) -> JSONResponse:
        entry = self.entries.get(request.path_params["agent_id"])
        if not entry:
            return JSONResponse({"error": "agente non trovato, registrati di nuovo"}, status_code=404)
        entry["last_seen"] = time.time()
        return JSONResponse({"ok": True})

    async def _search(self, request: Request) -> JSONResponse:
        p = request.query_params
        return JSONResponse(self.search(p.get("q"), p.get("tag"), p.get("skill"), p.get("all") in ("1", "true"),
                                        p.get("origin")))

    async def _import(self, request: Request) -> JSONResponse:
        body = await request.json()
        try:
            facts = await self.import_card(body["card_url"], body["team"], body.get("http_api"))
        except RegistrationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except (KeyError, ValueError, ValidationError, httpx.HTTPError) as exc:
            return JSONResponse({"error": f"import fallito: {exc}"}, status_code=400)
        return JSONResponse({"id": facts["id"], "facts": facts}, status_code=201)

    async def _peers(self, request: Request) -> JSONResponse:
        if request.method == "POST":
            body = await request.json()
            peer = self.add_peer(body["url"], body["team"])
            if body.get("sync", True):
                peer = await self.sync_peer(peer["url"])
            return JSONResponse(peer, status_code=201)
        return JSONResponse(list(self.peers.values()))

    async def _peers_sync(self, request: Request) -> JSONResponse:
        return JSONResponse(await self.sync_all())

    async def _events(self, request: Request) -> JSONResponse:
        if request.method == "POST":
            event = await request.json()
            self.add_event(event if isinstance(event, dict) else {"payload": event})
            return JSONResponse({"ok": True})
        since = int(request.query_params.get("since", 0))
        return JSONResponse([e for e in self.events if e["seq"] > since])

    async def _ui(self, request: Request) -> HTMLResponse:
        return HTMLResponse(_UI)


_UI = """<!doctype html><html lang="it"><meta charset="utf-8"><title>MACH registry</title>
<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}h2{font-size:18px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}
.ok{color:#0a7d3b}.ko{color:#a11}pre{margin:0;white-space:pre-wrap;font-size:12px}</style>
<h2>Agenti registrati</h2><table id="a"></table><h2>Messaggi tra agenti</h2><table id="e"></table>
<script>
let since=0;const rows=[];
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function tick(){
 const a=await (await fetch('/agents?all=1')).json();
 document.getElementById('a').innerHTML='<tr><th>id</th><th>origine</th><th>skill</th><th>stato</th></tr>'+a.map(x=>
  `<tr><td>${esc(x.id)}<br><small>${esc(x.rpc_url)}</small></td><td>${esc(x.origin)} (${esc(x.trust)})</td><td>${x.skills.map(s=>esc(s.id)).join(', ')}</td>
  <td class="${x.alive?'ok':'ko'}">${x.alive?'vivo':'non risponde'}</td></tr>`).join('');
 const ev=await (await fetch('/events?since='+since)).json();
 for(const e of ev){since=e.seq;rows.unshift(e)}
 document.getElementById('e').innerHTML='<tr><th>#</th><th>tipo</th><th>agente</th><th>contenuto</th></tr>'+rows.slice(0,80).map(e=>
  `<tr><td>${e.seq}</td><td>${esc(e.type)}</td><td>${esc(e.agent||e.from||'')}${e.to?' → '+esc(e.to):''}</td>
  <td>${esc(e.state||'')} ${esc(e.text||'')}${e.data?'<pre>'+esc(JSON.stringify(e.data,null,1))+'</pre>':''}</td></tr>`).join('');
}
tick();setInterval(tick,1500);
</script></html>"""

registry = Registry(local_token=os.getenv("MACH_REGISTRY_TOKEN") or None,
                    sync_every_s=float(os.getenv("MACH_REGISTRY_SYNC_S", "0")) or None)
app = registry.app()
