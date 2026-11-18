"""a2a_lite: implementazione minimale e leggibile di A2A 1.0 (JSON-RPC su HTTP).

Serve a capire il protocollo e a non dipendere dalle API dell'SDK ufficiale,
che tra 0.3 e 1.x sono cambiate molto. L'interoperabilità con gli altri team
dipende dal formato dei messaggi sul filo, non dalla libreria.
"""
from .client import A2AClient
from .errors import A2AError
from .server import A2AServer, Reply, TaskContext
from .types import (AgentCapabilities, AgentExtension, AgentCard, AgentInterface, AgentProvider, AgentSkill, Artifact,
                    Message, Part, Role, Task, TaskState, TaskStatus)

__all__ = [
    "A2AClient", "A2AError", "A2AServer", "Reply", "TaskContext", "AgentCapabilities", "AgentExtension", "AgentCard",
    "AgentInterface", "AgentProvider", "AgentSkill", "Artifact", "Message", "Part", "Role", "Task",
    "TaskState", "TaskStatus",
]
