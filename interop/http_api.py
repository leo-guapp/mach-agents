"""Client per le API REST *documentate* di un team esterno (secondo canale dello spoke esterno).

Perché esiste: non tutti i team esporranno un agente A2A; alcuni avranno solo un'API REST.
Perché "documentate": chiamare endpoint interni non pubblicati (sniffing, scraping) aggira
i controlli dell'altro team e si rompe senza preavviso (rilievo G). Qui si chiamano solo
operazioni dichiarate: dal team stesso nei suoi AgentFacts (extensions.http_api) o
concordate e scritte nella nostra configurazione.

Robustezza: timeout, retry con backoff solo per errori temporanei, circuit breaker
(dopo N errori di fila si smette di chiamare per un po'), nessun retry sulle scritture.

Formato della descrizione (lo stesso in AgentFacts e in configurazione):
    {"base_url": "https://team-b.example/api",
     "auth": {"header": "X-API-Key", "env": "TEAM_B_API_KEY"},
     "operations": {
        "eventi_vicini": {"method": "GET", "path": "/events", "query": ["zona", "data"],
                          "description": "Eventi culturali in una zona"},
        "prenota_posto": {"method": "POST", "path": "/bookings", "body": ["evento_id"], "writes": true}}}
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from a2a_lite._http import httpx


class ExternalAPIError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class Operation:
    name: str
    method: str = "GET"
    path: str = "/"
    query: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    writes: bool = False
    description: str = ""


class ExternalAPIClient:
    def __init__(self, team: str, base_url: str, operations: dict[str, Operation], *,
                 http: "httpx.AsyncClient | None" = None, headers: dict[str, str] | None = None,
                 timeout_s: float = 8.0, retries: int = 2, backoff_s: float = 0.3,
                 breaker_threshold: int = 3, breaker_cooldown_s: float = 30.0) -> None:
        self.team = team
        self.base_url = base_url.rstrip("/")
        self.operations = operations
        self.http = http or httpx.AsyncClient()
        self.headers = headers or {}
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.breaker_threshold = breaker_threshold
        self.breaker_cooldown_s = breaker_cooldown_s
        self._failures = 0
        self._open_until = 0.0

    @classmethod
    def from_description(cls, team: str, desc: dict[str, Any], **kw) -> "ExternalAPIClient":
        ops = {name: Operation(name=name, **{k: v for k, v in o.items() if k in Operation.__dataclass_fields__})
               for name, o in (desc.get("operations") or {}).items()}
        headers = {}
        auth = desc.get("auth") or {}
        if auth.get("header") and auth.get("env") and os.getenv(auth["env"]):
            headers[auth["header"]] = os.environ[auth["env"]]
        return cls(team, desc["base_url"], ops, headers=headers, **kw)

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._open_until

    def find_operation(self, hint: str | None) -> Operation | None:
        """Sceglie l'operazione: per nome esatto, altrimenti per parole in comune con la descrizione."""
        if not hint:
            return next(iter(self.operations.values()), None)
        if hint in self.operations:
            return self.operations[hint]
        words = set(hint.lower().replace("_", " ").split())
        scored = [(len(words & set(f"{o.name} {o.description}".lower().replace("_", " ").split())), o)
                  for o in self.operations.values()]
        scored = [x for x in scored if x[0] > 0]
        return max(scored, key=lambda x: x[0])[1] if scored else None

    async def call(self, op_name: str, args: dict[str, Any]) -> Any:
        op = self.operations.get(op_name)
        if op is None:
            raise ExternalAPIError(f"Operazione non documentata: {op_name}")
        if not self.available:
            raise ExternalAPIError(f"Circuit breaker aperto per {self.team}", retryable=True)
        params = {k: args[k] for k in op.query if k in args}
        body = {k: args[k] for k in op.body if k in args} if op.body else None
        attempts = 1 if op.writes else self.retries + 1  # mai ripetere una scrittura alla cieca
        last: ExternalAPIError | None = None
        for i in range(attempts):
            try:
                r = await self.http.request(op.method, f"{self.base_url}{op.path}", params=params or None,
                                            json=body, headers=self.headers, timeout=self.timeout_s)
                if r.status_code >= 500 or r.status_code == 429:
                    raise ExternalAPIError(f"{self.team} {op.name}: HTTP {r.status_code}", retryable=True,
                                           status=r.status_code)
                if r.status_code >= 400:
                    raise ExternalAPIError(f"{self.team} {op.name}: HTTP {r.status_code}", status=r.status_code)
                self._failures = 0
                return r.json()
            except httpx.HTTPError as exc:
                last = ExternalAPIError(f"{self.team} {op.name}: {exc.__class__.__name__}", retryable=True)
            except ExternalAPIError as exc:
                last = exc
                if not exc.retryable:
                    break
            if i < attempts - 1:
                await asyncio.sleep(self.backoff_s * (2 ** i))
        self._failures += 1
        if self._failures >= self.breaker_threshold:
            self._open_until = time.monotonic() + self.breaker_cooldown_s
        raise last or ExternalAPIError("errore sconosciuto")
