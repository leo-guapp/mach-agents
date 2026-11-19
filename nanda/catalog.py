"""Formato AI Catalog usato dal NANDA Index v2 ("hop 2" della risoluzione).

Catena di risoluzione dello stack NANDA attuale (projnanda/nanda-index-v2,
projnanda/nanda-registry-server-repo):

    Richiedente -> NANDA Index -> AI Catalog dell'organizzazione -> Agent Card A2A -> agente

  1. GET <index>/api/v1/resolve?locator=urn:ai:domain:<dominio>:agent:<slug>
     -> IndexRecord con registry_url e identifier
  2. GET <registry_url>/agents/<identifier>  -> CatalogEntry con `url` (la card)
  3. GET <entry.url>                         -> Agent Card A2A
  4. chiamata all'agente secondo la card

Il nostro gateway serve gli endpoint del catalogo (/.well-known/ai-catalog.json, /agents,
/agents/search, /agents/<id>), quindi il suo URL è il `registry_url` da dare all'Index.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

A2A_CARD_MEDIA_TYPE = "application/a2a-agent-card+json"
AI_CATALOG_MEDIA_TYPE = "application/ai-catalog+json"
NANDA_EXT = "org.projectnanda"
MACH_EXT = "org.mach-hackathon"


def catalog_identifier(domain: str | None, slug: str) -> str:
    """urn:air:<dominio>:agent:<slug> se c'è un dominio, altrimenti lo slug (come nanda-registry-server)."""
    return f"urn:air:{domain}:agent:{slug}" if domain else slug


def index_identifier(domain: str, slug: str | None = None) -> str:
    """Identificatori dell'Index v2: urn:ai:domain:<dominio>[:agent:<slug>]."""
    return f"urn:ai:domain:{domain}" + (f":agent:{slug}" if slug else "")


def slug_from_identifier(identifier: str) -> str:
    """Accetta 'accessibility', 'urn:air:dom:agent:accessibility' o 'urn:ai:domain:dom:agent:accessibility'."""
    return identifier.rsplit(":", 1)[-1] if identifier.startswith("urn:") else identifier


def _tags(card: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for s in card.get("skills", []):
        for t in [s.get("id", "")] + list(s.get("tags", [])):
            t = str(t)[:64]
            if t and not t.startswith("mach:") and t not in seen:
                seen.append(t)
    return seen[:32]


def catalog_entry(slug: str, card: dict[str, Any], card_url: str, *, domain: str | None = None,
                  publisher_name: str | None = None, agent_id: str | None = None,
                  ttl_seconds: int = 3600, status: str = "active") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "identifier": catalog_identifier(domain, slug),
        "displayName": card.get("name", slug),
        "type": A2A_CARD_MEDIA_TYPE,
        "mediaType": A2A_CARD_MEDIA_TYPE,
        "url": card_url,
        "description": (card.get("description") or "")[:2048],
        "tags": _tags(card),
        "version": card.get("version", "0.1.0"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "extensions": {NANDA_EXT: {"ttlSeconds": ttl_seconds, "status": status},
                       MACH_EXT: {"agent_id": agent_id, "skills": [s.get("id") for s in card.get("skills", [])]}},
    }
    if domain:
        entry["publisher"] = {"identifier": domain, "displayName": publisher_name or domain, "identityType": "dns"}
    return entry


def catalog_document(entries: list[dict[str, Any]], *, domain: str | None = None,
                     publisher_name: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"specVersion": "1.0", "entries": entries}
    if domain:
        doc["identifier"] = f"urn:air:{domain}:catalog:root"
        doc["displayName"] = publisher_name or domain
        doc["publisher"] = {"identifier": domain, "displayName": publisher_name or domain, "identityType": "dns"}
    return doc


def search_entries(entries: list[dict[str, Any]], q: str | None, limit: int = 50) -> list[dict[str, Any]]:
    """Stessa logica di nanda-registry-server: termini >= 3 caratteri, max 8, ordinati per corrispondenze."""
    if not q:
        return entries[:limit]
    if q.startswith("urn:"):
        slug = slug_from_identifier(q)
        return [e for e in entries if slug_from_identifier(e["identifier"]) == slug][:1]
    stop = {"the", "and", "per", "con", "che", "una", "del", "della"}
    terms = [t for t in q.lower().split() if len(t) >= 3 and t not in stop][:8]
    scored = []
    for e in entries:
        hay = " ".join([e["identifier"], e.get("displayName", ""), e.get("description", ""), " ".join(e.get("tags", []))]).lower()
        n = sum(1 for t in terms if t in hay)
        if n:
            scored.append((n, e))
    return [e for _, e in sorted(scored, key=lambda x: -x[0])][:limit]
