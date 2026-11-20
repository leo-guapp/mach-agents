"""Un "nodo di Milano" finto: sottoinsieme compatibile con le API di projnanda/nanda-index-v2.

Serve per provare registrazione e risoluzione PRIMA della gara (e come piano B se il nodo
vero non è raggiungibile). Differenze rispetto all'originale: niente database, niente
verifica di e-mail o dominio (le organizzazioni diventano subito attive), formati di
risposta di search/index scelti da noi (il client li legge comunque in modo tollerante).

    python scripts/fake_milan_node.py      # http://localhost:8300
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .catalog import slug_from_identifier


class LocalNandaIndex:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}     # email -> {password, display_name}
        self.tokens: dict[str, str] = {}               # token -> email
        self.orgs: dict[str, dict[str, Any]] = {}      # org_id -> IndexRecord

    # ------------------------------------------------------------------ logica
    def _token_user(self, request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        return self.tokens.get(auth[7:]) if auth.lower().startswith("bearer ") else None

    def _new_token(self, email: str) -> str:
        tok = secrets.token_hex(16)
        self.tokens[tok] = email
        return tok

    def _record(self, body: dict[str, Any], owner: str) -> dict[str, Any]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        domain = body.get("domain")
        return {
            "org_id": body["org_id"], "display_name": body.get("display_name", body["org_id"]),
            "domain": domain, "registry_url": body.get("registry_url"),
            "ttl_seconds": int(body.get("ttl_seconds", 3600)), "status": "active", "email_verified": True,
            "created_at": now, "updated_at": now,
            "identifier": body.get("identifier") or (f"urn:ai:domain:{domain}" if domain else f"urn:ai:org:{body['org_id']}"),
            "media_type": body.get("media_type", "application/ai-catalog+json"),
            "description": body.get("description"), "tags": body.get("tags", []),
            "publisher": body.get("publisher") or {"identifier": domain or body["org_id"],
                                                   "displayName": body.get("display_name", ""), "identityType": "dns"},
            "metadata": body.get("catalog_metadata") or body.get("metadata") or {}, "data": body.get("data") or {},
            "_owner": owner,
        }

    @staticmethod
    def _public(rec: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in rec.items() if not k.startswith("_")}

    def resolve(self, locator: str) -> dict[str, Any] | None:
        """urn:ai:domain:<d>[:agent:<slug>] oppure l'identifier esatto di un'organizzazione."""
        agent = None
        base = locator
        if ":agent:" in locator:
            base, agent = locator.split(":agent:", 1)
        for rec in self.orgs.values():
            if rec["status"] != "active":
                continue
            dom = f"urn:ai:domain:{rec['domain']}" if rec.get("domain") else None
            if base in (rec["identifier"], dom) or locator == rec["identifier"]:
                return {"locator": locator, "identifier": agent or slug_from_identifier(rec["identifier"]),
                        "index_record": self._public(rec)}
        return None

    def search(self, q: str) -> list[dict[str, Any]]:
        if q.startswith("urn:"):
            r = self.resolve(q)
            return [r["index_record"]] if r else []
        words = [w for w in q.lower().split() if len(w) >= 3]
        out = []
        for rec in self.orgs.values():
            hay = " ".join([rec["org_id"], rec["display_name"], rec.get("description") or "", " ".join(rec["tags"])]).lower()
            if not words or any(w in hay for w in words):
                out.append(self._public(rec))
        return out

    # ------------------------------------------------------------------ HTTP
    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/health", lambda r: JSONResponse({"status": "ok", "db": "memory"})),
            Route("/auth/register", self._register, methods=["POST"]),
            Route("/auth/login", self._login, methods=["POST"]),
            Route("/api/v1/index", lambda r: JSONResponse(
                [self._public(o) for o in self.orgs.values() if o["status"] == "active"])),
            Route("/api/v1/index/{org_id}", self._get_org_public),
            Route("/api/v1/resolve", self._resolve),
            Route("/api/v1/search", lambda r: JSONResponse({"results": self.search(r.query_params.get("q", ""))})),
            Route("/api/v1/orgs", self._create_org, methods=["POST"]),
            Route("/api/v1/orgs/{org_id}", self._org, methods=["GET", "PUT", "DELETE"]),
        ])

    async def _register(self, request: Request) -> JSONResponse:
        b = await request.json()
        if b["email"] in self.users:
            return JSONResponse({"error": "account già esistente"}, status_code=409)
        self.users[b["email"]] = {"password": b["password"], "display_name": b.get("display_name")}
        return JSONResponse({"token": self._new_token(b["email"])}, status_code=201)

    async def _login(self, request: Request) -> JSONResponse:
        b = await request.json()
        u = self.users.get(b.get("email"))
        if not u or u["password"] != b.get("password"):
            return JSONResponse({"error": "credenziali errate"}, status_code=401)
        return JSONResponse({"token": self._new_token(b["email"])})

    async def _get_org_public(self, request: Request) -> JSONResponse:
        rec = self.orgs.get(request.path_params["org_id"])
        if not rec or rec["status"] != "active":
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(self._public(rec))

    async def _resolve(self, request: Request) -> JSONResponse:
        res = self.resolve(request.query_params.get("locator", ""))
        return JSONResponse(res) if res else JSONResponse({"error": "not found"}, status_code=404)

    async def _create_org(self, request: Request) -> JSONResponse:
        user = self._token_user(request)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        b = await request.json()
        if not b.get("org_id"):
            return JSONResponse({"error": "org_id obbligatorio"}, status_code=400)
        if b["org_id"] in self.orgs:
            return JSONResponse({"error": "org_id già usato"}, status_code=409)
        rec = self._record(b, user)
        self.orgs[rec["org_id"]] = rec
        return JSONResponse(self._public(rec), status_code=201)

    async def _org(self, request: Request) -> JSONResponse:
        user = self._token_user(request)
        rec = self.orgs.get(request.path_params["org_id"])
        if not rec:
            return JSONResponse({"error": "not found"}, status_code=404)
        if rec["_owner"] != user:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "DELETE":
            del self.orgs[rec["org_id"]]
            return JSONResponse({"deleted": rec["org_id"]})
        if request.method == "PUT":
            b = await request.json()
            for k in ("display_name", "registry_url", "description", "tags", "media_type", "ttl_seconds"):
                if k in b:
                    rec[k] = b[k]
            rec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return JSONResponse(self._public(rec))
