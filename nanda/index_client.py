"""Client per il NANDA Index v2 (projnanda/nanda-index-v2) e risoluzione completa di un agente.

API pubbliche dell'Index (README di nanda-index-v2):
    GET  /api/v1/index                    organizzazioni attive
    GET  /api/v1/index/<org_id>           un IndexRecord
    GET  /api/v1/resolve?locator=<urn>    URN -> {locator, identifier, index_record}
    GET  /api/v1/search?q=<testo|urn>     ricerca
    POST /auth/register, /auth/login      -> {token}
    POST /api/v1/orgs  (Bearer)           registra un'organizzazione (con registry_url)

I formati di risposta di search/index non sono documentati: il client li legge in modo
tollerante (lista, oppure oggetto con results/items/records/data/orgs).

Il NANDA Index del nodo di Milano potrebbe essere questo o una variante: verificatelo
il primo giorno con `python scripts/register_nanda.py --check <url>`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from a2a_lite._http import httpx

from .catalog import A2A_CARD_MEDIA_TYPE, AI_CATALOG_MEDIA_TYPE, slug_from_identifier


class NandaIndexError(Exception):
    pass


def _items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in ("results", "items", "records", "data", "orgs", "index", "entries", "agents"):
            if isinstance(body.get(k), list):
                return [x for x in body[k] if isinstance(x, dict)]
        if "org_id" in body or "index_record" in body:
            return [body.get("index_record", body)]
    return []


@dataclass
class ResolvedAgent:
    locator: str
    card_url: str
    card: dict[str, Any]
    index_record: dict[str, Any] | None = None
    catalog_entry: dict[str, Any] | None = None

    @property
    def runtime_url(self) -> str:
        itfs = self.card.get("supportedInterfaces") or []
        return (itfs[0].get("url") if itfs else None) or self.card.get("url", "")


class NandaIndexClient:
    def __init__(self, base_url: str, *, http: "httpx.AsyncClient | None" = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=15)
        self.token = token

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _get(self, url: str, **params: Any) -> Any:
        r = await self.http.get(url, params={k: v for k, v in params.items() if v is not None} or None)
        if r.status_code == 404:
            raise NandaIndexError(f"non trovato: {url}")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ account e registrazione
    async def health(self) -> dict[str, Any]:
        return await self._get(f"{self.base_url}/health")

    async def register_account(self, email: str, password: str, display_name: str | None = None) -> str:
        r = await self.http.post(f"{self.base_url}/auth/register",
                                 json={"email": email, "password": password, "display_name": display_name})
        if r.status_code >= 400:
            raise NandaIndexError(f"registrazione account fallita: HTTP {r.status_code} {r.text[:200]}")
        self.token = r.json()["token"]
        return self.token

    async def login(self, email: str, password: str) -> str:
        r = await self.http.post(f"{self.base_url}/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            raise NandaIndexError(f"login fallito: HTTP {r.status_code} {r.text[:200]}")
        self.token = r.json()["token"]
        return self.token

    async def register_org(self, org: dict[str, Any]) -> dict[str, Any]:
        r = await self.http.post(f"{self.base_url}/api/v1/orgs", json=org, headers=self._auth())
        if r.status_code >= 400:
            raise NandaIndexError(f"registrazione organizzazione fallita: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    async def update_org(self, org_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        r = await self.http.put(f"{self.base_url}/api/v1/orgs/{org_id}", json=fields, headers=self._auth())
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ lettura
    async def list_index(self) -> list[dict[str, Any]]:
        return _items(await self._get(f"{self.base_url}/api/v1/index"))

    async def get_record(self, org_id: str) -> dict[str, Any]:
        body = await self._get(f"{self.base_url}/api/v1/index/{org_id}")
        return body.get("index_record", body) if isinstance(body, dict) else body

    async def resolve(self, locator: str) -> dict[str, Any]:
        return await self._get(f"{self.base_url}/api/v1/resolve", locator=locator)

    async def search(self, q: str) -> list[dict[str, Any]]:
        return _items(await self._get(f"{self.base_url}/api/v1/search", q=q))

    # ------------------------------------------------------------------ risoluzione completa
    async def resolve_agent(self, locator: str) -> ResolvedAgent:
        """URN -> IndexRecord -> (AI Catalog) -> Agent Card. Segue i media type dell'Index v2."""
        res = await self.resolve(locator)
        record = res.get("index_record") or res
        identifier = res.get("identifier") or slug_from_identifier(locator)
        media = record.get("media_type") or ""
        registry_url = (record.get("registry_url") or "").rstrip("/")
        if not registry_url:
            raise NandaIndexError(f"{locator}: nessun registry_url (DNS-AID non supportato da questo client)")
        entry = None
        if media == AI_CATALOG_MEDIA_TYPE or not media:
            entry = await self._get(f"{registry_url}/agents/{identifier}")
            card_url = entry["url"]
        elif media == A2A_CARD_MEDIA_TYPE:
            card_url = registry_url
        else:
            raise NandaIndexError(f"{locator}: media type non supportato: {media}")
        card = await self._get(card_url)
        return ResolvedAgent(locator, card_url, card, record, entry)


class NandaIndexSource:
    """Adatta il NANDA Index all'interfaccia di RegistryClient.search, per usarlo nel DiscoveryClient.

    Ricerca: /api/v1/search sull'Index, poi per ogni organizzazione col proprio catalogo
    GET <registry_url>/agents/search?q=...; per le card dirette (SMB/personali) usa la card.
    Le nostre organizzazioni (exclude_org_ids / exclude_registry_urls) vengono saltate,
    così lo spoke esterno non delega a noi stessi."""

    def __init__(self, client: NandaIndexClient, *, exclude_org_ids: set[str] | None = None,
                 exclude_registry_urls: set[str] | None = None, max_orgs: int = 10) -> None:
        self.client = client
        self.exclude_org_ids = exclude_org_ids or set()
        self.exclude_registry_urls = {u.rstrip("/") for u in exclude_registry_urls or set()}
        self.max_orgs = max_orgs

    async def search(self, q: str | None = None, *, tag: str | None = None, skill: str | None = None,
                     origin: str | None = None) -> list[dict[str, Any]]:
        if origin == "local":
            return []  # nell'Index ci sono solo agenti di organizzazioni (per noi: esterni)
        query = " ".join(x for x in (q, tag, skill) if x) or ""
        records = await (self.client.search(query) if query else self.client.list_index())
        records = [r for r in records if r.get("org_id") not in self.exclude_org_ids
                   and (r.get("registry_url") or "").rstrip("/") not in self.exclude_registry_urls][: self.max_orgs]

        async def from_record(rec: dict[str, Any]) -> list[dict[str, Any]]:
            team = rec.get("org_id") or (rec.get("publisher") or {}).get("identifier") or "esterno"
            media, reg = rec.get("media_type") or "", (rec.get("registry_url") or "").rstrip("/")
            try:
                if media == A2A_CARD_MEDIA_TYPE and reg:
                    card = await self.client._get(reg)
                    return [self._summary(rec.get("identifier") or team, card.get("name", team),
                                          card.get("description", ""), reg, team,
                                          [s.get("id") or s.get("name") for s in card.get("skills", [])], query)]
                if reg:
                    body = await self.client._get(f"{reg}/agents/search", q=query or None)
                    return [self._summary(e["identifier"], e.get("displayName", ""), e.get("description", ""),
                                          e["url"], team, e.get("tags", []), query) for e in _items(body)]
            except (httpx.HTTPError, NandaIndexError, KeyError, ValueError):
                return []
            return []

        out: list[dict[str, Any]] = []
        for part in await asyncio.gather(*(from_record(r) for r in records)):
            out.extend(part)
        if skill:  # se si cerca una skill precisa, teniamo chi la dichiara
            out = [o for o in out if skill in {s["id"] for s in o["skills"]}] or out
        return sorted(out, key=lambda o: o["score"], reverse=True)

    @staticmethod
    def _summary(identifier: str, label: str, description: str, card_url: str, team: str,
                 skills: list[str], query: str) -> dict[str, Any]:
        words = {w for w in query.lower().split() if len(w) >= 3}
        hay = f"{label} {description} {' '.join(skills)}".lower()
        return {"id": identifier, "label": label, "description": description, "card_url": card_url,
                "rpc_url": None, "origin": team, "trust": "external", "mach_profile": False, "http_api": None,
                "skills": [{"id": s, "tags": []} for s in skills if s],
                "score": float(sum(1 for w in words if w in hay)) or 0.5, "alive": True, "source": "nanda-index"}
