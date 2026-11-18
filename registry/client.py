"""Client del registry. Se cambiate registry (es. NANDA Index ufficiale),
riscrivete solo questa classe mantenendo gli stessi metodi."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from a2a_lite._http import httpx
from a2a_lite.types import AgentCard


class RegistryClient:
    def __init__(self, base_url: str, *, http: "httpx.AsyncClient | None" = None,
                 token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=10)
        self.token = token or os.getenv("MACH_REGISTRY_TOKEN") or None

    def _headers(self) -> dict[str, str]:
        return {"X-Registry-Token": self.token} if self.token else {}

    async def register(self, card: AgentCard | dict, card_url: str, *, agent_id: str | None = None,
                       team: str | None = None, http_api: dict[str, Any] | None = None) -> str:
        card_dict = card.wire() if isinstance(card, AgentCard) else card
        body = {"card": card_dict, "card_url": card_url, "agent_id": agent_id, "team": team, "http_api": http_api}
        r = await self._http.post(f"{self.base_url}/agents", json={k: v for k, v in body.items() if v is not None},
                                  headers=self._headers())
        r.raise_for_status()
        return r.json()["id"]

    async def heartbeat(self, agent_id: str) -> bool:
        r = await self._http.post(f"{self.base_url}/agents/{agent_id}/heartbeat")
        return r.status_code == 200

    async def keep_alive(self, agent_id: str, card: AgentCard, card_url: str, every_s: float = 30) -> None:
        """Da lanciare come task in background: heartbeat periodico, si ri-registra se il registry riparte."""
        while True:
            await asyncio.sleep(every_s)
            try:
                if not await self.heartbeat(agent_id):
                    await self.register(card, card_url, agent_id=agent_id)
            except httpx.HTTPError:
                pass

    async def search(self, q: str | None = None, *, tag: str | None = None, skill: str | None = None,
                     origin: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"q": q, "tag": tag, "skill": skill, "origin": origin}.items() if v}
        r = await self._http.get(f"{self.base_url}/search", params=params)
        r.raise_for_status()
        return r.json()

    async def resolve(self, agent_id: str) -> dict[str, Any]:
        r = await self._http.get(f"{self.base_url}/agents/{agent_id}")
        r.raise_for_status()
        return r.json()

    async def import_card(self, card_url: str, team: str, http_api: dict[str, Any] | None = None) -> str:
        r = await self._http.post(f"{self.base_url}/import",
                                  json={"card_url": card_url, "team": team, "http_api": http_api})
        r.raise_for_status()
        return r.json()["id"]

    async def add_peer(self, url: str, team: str, sync: bool = True) -> dict[str, Any]:
        r = await self._http.post(f"{self.base_url}/peers", json={"url": url, "team": team, "sync": sync})
        r.raise_for_status()
        return r.json()

    async def index(self) -> dict[str, Any]:
        r = await self._http.get(f"{self.base_url}/index.json")
        r.raise_for_status()
        return r.json()

    async def post_event(self, event: dict[str, Any]) -> None:
        try:
            await self._http.post(f"{self.base_url}/events", json=event)
        except httpx.HTTPError:
            pass


class DiscoveryClient:
    """Cerca in più indici insieme (il nostro, quello degli organizzatori, quelli dei peer)
    e unisce i risultati, eliminando i doppioni per card_url.

    Un indice che non risponde viene saltato: la discovery non deve mai bloccare il resto."""

    def __init__(self, registries: list[RegistryClient]) -> None:
        self.registries = registries

    async def search(self, q: str | None = None, *, tag: str | None = None, skill: str | None = None,
                     origin: str | None = None, exclude_ids: set[str] | None = None) -> list[dict[str, Any]]:
        async def one(rc: RegistryClient) -> list[dict[str, Any]]:
            try:
                return await rc.search(q, tag=tag, skill=skill, origin=origin)
            except (httpx.HTTPError, ValueError):
                return []

        merged: dict[str, dict[str, Any]] = {}
        for results in await asyncio.gather(*(one(rc) for rc in self.registries)):
            for r in results:
                if exclude_ids and r["id"] in exclude_ids:
                    continue
                key = r["card_url"]
                if key not in merged or r.get("score", 0) > merged[key].get("score", 0):
                    merged[key] = r
        return sorted(merged.values(), key=lambda r: r.get("score", 0), reverse=True)
