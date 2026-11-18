"""Formato comune delle risposte degli agenti (il "JSON Formatter" dello schema di Giovanni).

Ogni skill restituisce un Esito. Il BaseAgent lo traduce nello stato A2A giusto
e lo mette nel risultato come DataPart, così l'aggregatore riceve sempre la
stessa struttura, qualunque agente risponda:

    {"status": "SUCCESS", "data": {...}, "message": "...",
     "fonti": [{"nome": "...", "url": "...", "simulato": true, "recuperato": "..."}],
     "mancanti": ["zona"]}
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from a2a_lite import Reply, TaskState
from a2a_lite.types import now_iso


class Status(str, Enum):
    SUCCESS = "SUCCESS"          # tutto ok
    PARTIAL = "PARTIAL"          # risultato utile ma incompleto (vedi "mancanti")
    NEEDS_INPUT = "NEEDS_INPUT"  # serve un dato dal chiamante -> A2A INPUT_REQUIRED
    EMERGENCY = "EMERGENCY"      # interrompere il percorso normale (es. indicare il 112)
    OFFLINE = "OFFLINE"          # fonte non raggiungibile
    ERROR = "ERROR"              # errore nella richiesta o nell'esecuzione
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # non è compito di questo agente -> A2A REJECTED


_TO_STATE = {
    Status.SUCCESS: TaskState.COMPLETED,
    Status.PARTIAL: TaskState.COMPLETED,
    Status.EMERGENCY: TaskState.COMPLETED,
    Status.NEEDS_INPUT: TaskState.INPUT_REQUIRED,
    Status.OFFLINE: TaskState.FAILED,
    Status.ERROR: TaskState.FAILED,
    Status.OUT_OF_SCOPE: TaskState.REJECTED,
}


@dataclass
class Fonte:
    nome: str
    url: str | None = None
    simulato: bool = False
    recuperato: str = field(default_factory=now_iso)


@dataclass
class Esito:
    status: Status
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    fonti: list[Fonte] = field(default_factory=list)
    mancanti: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls, message: str, data: dict | None = None, fonti: list[Fonte] | None = None) -> "Esito":
        return cls(Status.SUCCESS, message, data or {}, fonti or [])

    @classmethod
    def chiedi(cls, domanda: str, mancanti: list[str]) -> "Esito":
        return cls(Status.NEEDS_INPUT, domanda, mancanti=mancanti)

    @classmethod
    def errore(cls, message: str, data: dict | None = None) -> "Esito":
        return cls(Status.ERROR, message, data or {})

    def payload(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_reply(self, skill_id: str | None = None) -> Reply:
        payload = self.payload()
        if skill_id:
            payload["skill"] = skill_id
        return Reply(state=_TO_STATE[self.status], text=self.message, data=payload,
                     artifact_name=skill_id or "result")
