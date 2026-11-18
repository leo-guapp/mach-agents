"""AgentFacts (formato NANDA) costruiti a partire da una Agent Card A2A.

Schema di riferimento: https://github.com/projnanda/agentfacts-format
I campi label/description/version/provider/endpoints/capabilities/skills
corrispondono a quelli della Agent Card. In più indichiamo:
  * origin: "local" per i nostri agenti, altrimenti il nome del team;
  * trust:  "internal" | "partner" | "external" (vedi contracts/README.md);
  * mach_profile: true se l'agente dichiara l'estensione del profilo MACH.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from a2a_lite.types import AgentCard

ID_PATTERN = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def namespace_for(team: str) -> str:
    """Namespace valido per gli id ("team-b" -> "teamb"): solo lettere minuscole e cifre."""
    return re.sub(r"[^a-z0-9]", "", slugify(team)) or "ext"


def mach_params(card: AgentCard) -> dict[str, Any] | None:
    """Parametri dell'estensione MACH, se la card la dichiara."""
    for ext in card.capabilities.extensions or []:
        if "mach" in ext.uri:
            return ext.params or {}
    return None


def resolve_agent_id(card: AgentCard, namespace: str, agent_id: str | None = None) -> str:
    """Ordine: id esplicito -> id nell'estensione MACH -> namespace:slug del nome."""
    for candidate in (agent_id, (mach_params(card) or {}).get("agent_id")):
        if candidate and ID_PATTERN.match(candidate):
            return candidate
    return f"{namespace_for(namespace)}:{slugify(card.name)}"


def agentfacts_from_card(card: AgentCard, card_url: str, namespace: str = "mach", *,
                         agent_id: str | None = None, origin: str = "local", trust: str = "internal",
                         http_api: dict[str, Any] | None = None, provider_url: str | None = None) -> dict[str, Any]:
    aid = resolve_agent_id(card, namespace, agent_id)
    provider = card.provider.organization if card.provider else namespace
    facts = {
        "id": aid,
        "agent_name": f"urn:agent:{aid}",
        "label": card.name,
        "description": card.description,
        "version": card.version,
        "provider": {"name": provider, "url": provider_url or (card.provider.url if card.provider else None)},
        "origin": origin,
        "trust": trust,
        "mach_profile": mach_params(card) is not None,
        "endpoints": {"static": [card.jsonrpc_url()]},
        "capabilities": {
            "modalities": ["text"],
            "streaming": card.capabilities.streaming,
            "batch": False,
            "authentication": {"methods": ["bearer"] if card.security_schemes else []},
        },
        "skills": [
            {
                "id": s.id,
                "description": f"{s.name}: {s.description}",
                "inputModes": s.input_modes or card.default_input_modes,
                "outputModes": s.output_modes or card.default_output_modes,
                "tags": s.tags,
                "examples": s.examples or [],
            }
            for s in card.skills
        ],
        # estensione nostra: dove trovare la card A2A completa ed eventuali API documentate
        "extensions": {"a2a": {"agentCardUrl": card_url, "protocolVersion": "1.0"}},
    }
    if http_api:
        facts["extensions"]["http_api"] = http_api
    return facts
