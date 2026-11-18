"""Ponte tra l'Esito usato oggi dalle skill (agents/envelope.py) e la Busta 1.1.

Serve finché le skill non producono direttamente la Busta. Per attivarla negli
agenti basta una riga in Esito.to_reply (vedi contracts/README.md, "Integrazione").
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.envelope import Esito
from agents.envelope import Status as EsitoStatus

from .models import (AgentRef, Busta, Channel, ErrorItem, NeedsInput, Provenance, Status, UserMessage)

_STATUS = {
    EsitoStatus.SUCCESS: Status.SUCCESS,
    EsitoStatus.PARTIAL: Status.PARTIAL,
    EsitoStatus.NEEDS_INPUT: Status.NEEDS_INPUT,
    EsitoStatus.EMERGENCY: Status.EMERGENCY,
    EsitoStatus.OFFLINE: Status.UNAVAILABLE,
    EsitoStatus.ERROR: Status.ERROR,
    EsitoStatus.OUT_OF_SCOPE: Status.REJECTED,
}

_BASE_CONFIDENCE = {
    Status.SUCCESS: 1.0, Status.PARTIAL: 0.7, Status.DEGRADED: 0.6, Status.NEEDS_INPUT: 1.0,
    Status.EMERGENCY: 1.0, Status.UNAVAILABLE: 0.0, Status.ERROR: 0.0, Status.REJECTED: 0.0,
}


def compute_confidence(status: Status, provenance: list[Provenance]) -> float:
    """Regola semplice e spiegabile (meglio di un numero "inventato"):
    base per stato, x0.8 se c'è un dato stale o da cache/fallback, x0.9 se c'è un dato via RPA."""
    c = _BASE_CONFIDENCE[status]
    if any(p.stale or p.channel in (Channel.CACHE, Channel.FALLBACK) for p in provenance):
        c *= 0.8
    if any(p.channel == Channel.RPA for p in provenance):
        c *= 0.9
    return round(c, 2)


def busta_from_esito(esito: Esito, agent: AgentRef, trace_id: str, *, skill: str | None = None,
                     subtask_id: str | None = None, ttl_seconds: int | None = None,
                     lang: str = "it") -> Busta:
    status = _STATUS[esito.status]
    provenance = [
        Provenance(source=f.nome, url=f.url, simulated=f.simulato,
                   channel=Channel.MOCK if f.simulato else Channel.API,
                   fetched_at=datetime.fromisoformat(f.recuperato))
        for f in esito.fonti
    ]
    errors: list[ErrorItem] = []
    if status == Status.PARTIAL:
        errors = [ErrorItem(code="INCOMPLETE", source=m) for m in esito.mancanti] or \
                 [ErrorItem(code="INCOMPLETE", message=esito.message)]
    elif status == Status.UNAVAILABLE:
        errors = [ErrorItem(code="SOURCE_UNAVAILABLE", message=esito.message, retryable=True)]
    elif status == Status.ERROR:
        errors = [ErrorItem(code="REQUEST_ERROR", message=esito.message)]
    elif status == Status.REJECTED:
        errors = [ErrorItem(code="OUT_OF_SCOPE", message=esito.message)]
    needs_input = NeedsInput(question=esito.message, fields=esito.mancanti or ["input"]) \
        if status == Status.NEEDS_INPUT else None
    return Busta(
        agent=agent, trace_id=trace_id, subtask_id=subtask_id, skill=skill,
        generated_at=datetime.now(timezone.utc), status=status,
        confidence=compute_confidence(status, provenance), ttl_seconds=ttl_seconds,
        provenance=provenance, data=esito.data, needs_input=needs_input, errors=errors,
        user_message=UserMessage(lang=lang, text=esito.message),
    )


def busta_json(esito: Esito, agent: AgentRef, trace_id: str, **kw: Any) -> dict[str, Any]:
    return busta_from_esito(esito, agent, trace_id, **kw).to_json()
