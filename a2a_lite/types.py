"""Oggetti del protocollo A2A 1.0 (forma JSON "on the wire").

Riferimento: https://a2a-protocol.org/latest/specification/ (sez. 4 e 9).
In JSON i campi sono camelCase; in Python usiamo snake_case grazie agli alias.

Più in basso ci sono i convertitori da/verso la forma 0.3 (quella con il
campo "kind", i metodi "message/send" e gli stati "input-required"), così
il nostro server parla anche con team che usano SDK più vecchi.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PROTOCOL_VERSION = "1.0"


class _Wire(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    def wire(self) -> dict[str, Any]:
        """Dizionario pronto da serializzare in JSON (camelCase, senza campi vuoti)."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- enum

class Role(str, Enum):
    USER = "ROLE_USER"
    AGENT = "ROLE_AGENT"


class TaskState(str, Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    REJECTED = "TASK_STATE_REJECTED"

    @property
    def terminal(self) -> bool:
        return self in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}

    @property
    def interrupted(self) -> bool:
        """Il task è in pausa e aspetta qualcosa dal chiamante."""
        return self in {TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED}


# --------------------------------------------------------------------------- messaggi

class Part(_Wire):
    """Esattamente uno tra text, data, url, raw."""
    text: str | None = None
    data: Any | None = None
    url: str | None = None
    raw: str | None = None  # base64
    media_type: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] | None = None


class Message(_Wire):
    message_id: str = Field(default_factory=new_id)
    role: Role
    parts: list[Part]
    context_id: str | None = None
    task_id: str | None = None
    reference_task_ids: list[str] | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def user(cls, text: str | None = None, data: Any | None = None, **kw) -> "Message":
        return cls(role=Role.USER, parts=_parts(text, data), **kw)

    @classmethod
    def agent(cls, text: str | None = None, data: Any | None = None, **kw) -> "Message":
        return cls(role=Role.AGENT, parts=_parts(text, data), **kw)

    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if p.text)

    def data(self) -> dict[str, Any] | None:
        """Unisce tutte le DataPart che sono dizionari."""
        out: dict[str, Any] = {}
        for p in self.parts:
            if isinstance(p.data, dict):
                out.update(p.data)
        return out or None


def _parts(text: str | None, data: Any | None) -> list[Part]:
    parts = []
    if text:
        parts.append(Part(text=text))
    if data is not None:
        parts.append(Part(data=data, media_type="application/json"))
    return parts


class TaskStatus(_Wire):
    state: TaskState
    message: Message | None = None
    timestamp: str = Field(default_factory=now_iso)


class Artifact(_Wire):
    artifact_id: str = Field(default_factory=new_id)
    name: str | None = None
    description: str | None = None
    parts: list[Part]
    metadata: dict[str, Any] | None = None


class Task(_Wire):
    id: str = Field(default_factory=new_id)
    context_id: str = Field(default_factory=new_id)
    status: TaskStatus
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    metadata: dict[str, Any] | None = None

    # comodità per chi legge il risultato
    def result_data(self) -> dict[str, Any] | None:
        out: dict[str, Any] = {}
        for a in self.artifacts or []:
            for p in a.parts:
                if isinstance(p.data, dict):
                    out.update(p.data)
        if not out and self.status.message:
            return self.status.message.data()
        return out or None

    def result_text(self) -> str:
        texts = [p.text for a in self.artifacts or [] for p in a.parts if p.text]
        if not texts and self.status.message:
            return self.status.message.text()
        return "\n".join(texts)


# --------------------------------------------------------------------------- agent card

class AgentSkill(_Wire):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None


class AgentInterface(_Wire):
    url: str
    protocol_binding: str = "JSONRPC"
    protocol_version: str = PROTOCOL_VERSION


class AgentExtension(_Wire):
    """Estensione dichiarata nella card (es. il profilo MACH)."""
    uri: str
    description: str | None = None
    required: bool = False
    params: dict[str, Any] | None = None


class AgentCapabilities(_Wire):
    streaming: bool = False
    push_notifications: bool = False
    extended_agent_card: bool = False
    extensions: list[AgentExtension] | None = None


class AgentProvider(_Wire):
    organization: str
    url: str | None = None


class AgentCard(_Wire):
    name: str
    description: str
    version: str = "0.1.0"
    supported_interfaces: list[AgentInterface]
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    skills: list[AgentSkill]
    provider: AgentProvider | None = None
    security_schemes: dict[str, Any] | None = None
    security: list[dict[str, list[str]]] | None = None

    def jsonrpc_url(self) -> str:
        for itf in self.supported_interfaces:
            if itf.protocol_binding.upper() == "JSONRPC":
                return itf.url
        return self.supported_interfaces[0].url

    def wire(self, legacy_fields: bool = True) -> dict[str, Any]:
        d = super().wire()
        if legacy_fields:
            # Campi della 0.3: permettono a client vecchi di leggere la card.
            d.setdefault("url", self.jsonrpc_url())
            d.setdefault("preferredTransport", "JSONRPC")
            d.setdefault("protocolVersion", "0.3.0")
        return d

    @classmethod
    def parse(cls, d: dict[str, Any]) -> "AgentCard":
        """Accetta card 1.0 e card 0.3 (che hanno `url` al posto di supportedInterfaces)."""
        d = dict(d)
        if "supportedInterfaces" not in d and "url" in d:
            d["supportedInterfaces"] = [{
                "url": d["url"],
                "protocolBinding": d.get("preferredTransport", "JSONRPC"),
                "protocolVersion": d.get("protocolVersion", "0.3.0"),
            }]
        for s in d.get("skills", []):
            s.setdefault("tags", [])
        return cls.model_validate(d)


# --------------------------------------------------------------------------- compatibilità 0.3

_STATE_TO_LEGACY = {
    TaskState.SUBMITTED: "submitted", TaskState.WORKING: "working",
    TaskState.INPUT_REQUIRED: "input-required", TaskState.AUTH_REQUIRED: "auth-required",
    TaskState.COMPLETED: "completed", TaskState.FAILED: "failed",
    TaskState.CANCELED: "canceled", TaskState.REJECTED: "rejected",
}
_STATE_FROM_LEGACY = {v: k.value for k, v in _STATE_TO_LEGACY.items()}
_ROLE_TO_LEGACY = {"ROLE_USER": "user", "ROLE_AGENT": "agent"}
_ROLE_FROM_LEGACY = {v: k for k, v in _ROLE_TO_LEGACY.items()}


def part_to_legacy(p: dict[str, Any]) -> dict[str, Any]:
    if "text" in p:
        return {"kind": "text", "text": p["text"], **({"metadata": p["metadata"]} if "metadata" in p else {})}
    if "data" in p:
        return {"kind": "data", "data": p["data"]}
    f: dict[str, Any] = {}
    if "url" in p:
        f["uri"] = p["url"]
    if "raw" in p:
        f["bytes"] = p["raw"]
    if "mediaType" in p:
        f["mimeType"] = p["mediaType"]
    if "filename" in p:
        f["name"] = p["filename"]
    return {"kind": "file", "file": f}


def part_from_legacy(p: dict[str, Any]) -> dict[str, Any]:
    kind = p.get("kind")
    if kind is None:  # è già in forma 1.0
        return p
    if kind == "text":
        return {"text": p.get("text", "")}
    if kind == "data":
        return {"data": p.get("data")}
    f = p.get("file", {})
    out: dict[str, Any] = {}
    if "uri" in f:
        out["url"] = f["uri"]
    if "bytes" in f:
        out["raw"] = f["bytes"]
    if "mimeType" in f:
        out["mediaType"] = f["mimeType"]
    if "name" in f:
        out["filename"] = f["name"]
    return out


def message_to_legacy(m: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in m.items() if k != "parts"}
    out["kind"] = "message"
    out["role"] = _ROLE_TO_LEGACY.get(m.get("role"), m.get("role"))
    out["parts"] = [part_to_legacy(p) for p in m.get("parts", [])]
    return out


def message_from_legacy(m: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in m.items() if k not in ("parts", "kind")}
    out["role"] = _ROLE_FROM_LEGACY.get(m.get("role"), m.get("role"))
    out["parts"] = [part_from_legacy(p) for p in m.get("parts", [])]
    return out


def task_to_legacy(t: dict[str, Any]) -> dict[str, Any]:
    out = dict(t)
    out["kind"] = "task"
    status = dict(t["status"])
    status["state"] = _STATE_TO_LEGACY.get(TaskState(status["state"]), status["state"])
    if "message" in status:
        status["message"] = message_to_legacy(status["message"])
    out["status"] = status
    if "history" in t:
        out["history"] = [message_to_legacy(m) for m in t["history"]]
    if "artifacts" in t:
        out["artifacts"] = [{**a, "parts": [part_to_legacy(p) for p in a["parts"]]} for a in t["artifacts"]]
    return out


def task_from_legacy(t: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k != "kind"}
    status = dict(t["status"])
    status["state"] = _STATE_FROM_LEGACY.get(status["state"], status["state"])
    if "message" in status and status["message"]:
        status["message"] = message_from_legacy(status["message"])
    out["status"] = status
    if "history" in t:
        out["history"] = [message_from_legacy(m) for m in t["history"]]
    if "artifacts" in t:
        out["artifacts"] = [{**a, "parts": [part_from_legacy(p) for p in a["parts"]]} for a in t["artifacts"]]
    return out
