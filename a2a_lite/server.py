"""Server A2A minimale: Agent Card + JSON-RPC (SendMessage, GetTask, CancelTask, ListTasks).

Il vostro codice scrive solo una funzione:

    async def handler(ctx: TaskContext) -> Reply

Il server si occupa di: creare e conservare i Task, gestire il dialogo a più
turni (stato INPUT_REQUIRED), tradurre da/verso A2A 0.3, rispondere con gli
errori giusti. Streaming e push notification non sono implementati
(capabilities.streaming = false): per una demo da hackathon il polling basta.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import errors as E
from .types import (AgentCard, Artifact, Message, Task, TaskState, TaskStatus, _parts,
                    message_from_legacy, new_id, task_to_legacy)

log = logging.getLogger("a2a_lite.server")

# nomi dei metodi 0.3 -> nomi 1.0
LEGACY_METHODS = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/get": "GetTask",
    "tasks/cancel": "CancelTask",
    "tasks/list": "ListTasks",
    "tasks/resubscribe": "SubscribeToTask",
}
SUPPORTED_VERSIONS = ("0.3", "1.0", "1.")


@dataclass
class Reply:
    """Quello che il vostro handler restituisce.

    state=COMPLETED       -> text/data finiscono in un Artifact (il risultato)
    state=INPUT_REQUIRED  -> text è la domanda per il chiamante; il task resta aperto
    state=FAILED/REJECTED -> text spiega il motivo
    """
    state: TaskState = TaskState.COMPLETED
    text: str | None = None
    data: dict[str, Any] | None = None
    artifact_name: str = "result"


@dataclass
class TaskContext:
    task: Task
    message: Message
    is_continuation: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.text()

    @property
    def data(self) -> dict[str, Any]:
        return self.message.data() or {}

    @property
    def metadata(self) -> dict[str, Any]:
        return self.message.metadata or {}

    @property
    def context_id(self) -> str:
        return self.task.context_id

    @property
    def task_id(self) -> str:
        return self.task.id

    def history_text(self) -> str:
        return "\n".join(f"{m.role.value}: {m.text()}" for m in self.task.history or [])


Handler = Callable[[TaskContext], Awaitable[Reply]]
EventHook = Callable[[dict[str, Any]], Awaitable[None]]


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def save(self, task: Task) -> None:
        self._tasks[task.id] = task

    def list(self, context_id: str | None = None) -> list[Task]:
        return [t for t in self._tasks.values() if context_id is None or t.context_id == context_id]


class A2AServer:
    def __init__(self, card: AgentCard, handler: Handler, *, auth_token: str | None = None,
                 on_event: EventHook | None = None, timeout_s: float = 90.0,
                 store: InMemoryTaskStore | None = None) -> None:
        self.card = card
        self.handler = handler
        self.auth_token = auth_token
        self.on_event = on_event
        self.timeout_s = timeout_s
        self.store = store or InMemoryTaskStore()

    # ------------------------------------------------------------------ HTTP
    def app(self, lifespan=None) -> Starlette:
        return Starlette(lifespan=lifespan, routes=[
            Route("/.well-known/agent-card.json", self._card, methods=["GET"]),
            Route("/.well-known/agent.json", self._card, methods=["GET"]),  # nome vecchio
            Route("/health", lambda r: JSONResponse({"ok": True, "agent": self.card.name}), methods=["GET"]),
            Route("/", self._rpc, methods=["POST"]),
        ])

    async def _card(self, request: Request) -> JSONResponse:
        return JSONResponse(self.card.wire())

    async def _rpc(self, request: Request) -> JSONResponse:
        if self.auth_token and request.headers.get("authorization") != f"Bearer {self.auth_token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        req_id = None
        try:
            try:
                body = json.loads(await request.body())
            except json.JSONDecodeError as exc:
                raise E.ParseError(str(exc))
            if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
                raise E.InvalidRequest()
            req_id = body.get("id")
            method = body["method"]
            legacy = method in LEGACY_METHODS
            version = request.headers.get("a2a-version", "").strip()
            if version and not version.startswith(SUPPORTED_VERSIONS):
                raise E.VersionNotSupported(f"Versione {version} non supportata", data={"supported": ["0.3", "1.0"]})
            if version.startswith("0.3"):
                legacy = True
            result = await self._dispatch(LEGACY_METHODS.get(method, method), body.get("params") or {}, legacy)
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})
        except E.A2AError as err:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": err.to_jsonrpc()})
        except Exception as exc:  # pragma: no cover - rete di sicurezza
            log.exception("errore non gestito")
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": E.InternalError(str(exc)).to_jsonrpc()})

    async def _dispatch(self, method: str, params: dict[str, Any], legacy: bool) -> Any:
        if method == "SendMessage":
            return await self._send_message(params, legacy)
        if method == "GetTask":
            task = self._get(params)
            return self._out(task, legacy, params.get("historyLength"), wrap=False)
        if method == "CancelTask":
            return self._cancel(params, legacy)
        if method == "ListTasks":
            tasks = self.store.list(params.get("contextId"))
            return {"tasks": [self._out(t, legacy, 0, wrap=False) for t in tasks]}
        if method in ("SendStreamingMessage", "SubscribeToTask"):
            raise E.UnsupportedOperation("Streaming non supportato da questo agente: usa SendMessage")
        raise E.MethodNotFound(f"Metodo sconosciuto: {method}")

    # ------------------------------------------------------------------ metodi
    async def _send_message(self, params: dict[str, Any], legacy: bool) -> Any:
        raw = params.get("message")
        if not isinstance(raw, dict):
            raise E.InvalidParams("Manca 'message'")
        try:
            msg = Message.model_validate(message_from_legacy(raw))
        except ValidationError as exc:
            raise E.InvalidParams("Messaggio non valido", data=json.loads(exc.json()))

        continuation = False
        if msg.task_id and (existing := self.store.get(msg.task_id)):
            if existing.status.state.terminal:
                raise E.UnsupportedOperation(f"Il task {existing.id} è già concluso ({existing.status.state.value})")
            task, continuation = existing, True
        else:
            task = Task(context_id=msg.context_id or new_id(),
                        status=TaskStatus(state=TaskState.SUBMITTED), history=[])
        msg.task_id, msg.context_id = task.id, task.context_id
        task.history = (task.history or []) + [msg]
        task.status = TaskStatus(state=TaskState.WORKING)
        self.store.save(task)
        await self._emit({"type": "request", "agent": self.card.name, "task_id": task.id,
                          "context_id": task.context_id, "text": msg.text(), "data": msg.data(),
                          "continuation": continuation})

        ctx = TaskContext(task=task, message=msg, is_continuation=continuation)
        try:
            reply = await asyncio.wait_for(self.handler(ctx), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            reply = Reply(state=TaskState.FAILED, text=f"Timeout dopo {self.timeout_s}s",
                          data={"status": "OFFLINE", "reason": "timeout"})
        except Exception as exc:
            log.exception("handler fallito")
            reply = Reply(state=TaskState.FAILED, text=f"Errore interno dell'agente: {exc}",
                          data={"status": "ERROR", "reason": str(exc)})

        self._apply(task, reply)
        self.store.save(task)
        await self._emit({"type": "response", "agent": self.card.name, "task_id": task.id,
                          "context_id": task.context_id, "state": task.status.state.value,
                          "text": reply.text, "data": reply.data})
        hist_len = (params.get("configuration") or {}).get("historyLength")
        return self._out(task, legacy, hist_len, wrap=True)

    def _apply(self, task: Task, reply: Reply) -> None:
        agent_msg = Message.agent(reply.text, reply.data, task_id=task.id, context_id=task.context_id)
        if reply.state == TaskState.COMPLETED and (reply.text or reply.data is not None):
            task.artifacts = (task.artifacts or []) + [Artifact(name=reply.artifact_name,
                                                                parts=_parts(reply.text, reply.data))]
            status_msg = Message.agent(reply.text, task_id=task.id, context_id=task.context_id) if reply.text else None
        else:
            status_msg = agent_msg
        task.status = TaskStatus(state=reply.state, message=status_msg)
        task.history = (task.history or []) + [agent_msg]

    def _get(self, params: dict[str, Any]) -> Task:
        task_id = params.get("id")
        task = self.store.get(task_id) if task_id else None
        if not task:
            raise E.TaskNotFound(f"Task {task_id} non trovato")
        return task

    def _cancel(self, params: dict[str, Any], legacy: bool) -> Any:
        task = self._get(params)
        if task.status.state.terminal:
            raise E.TaskNotCancelable(f"Task in stato {task.status.state.value}")
        task.status = TaskStatus(state=TaskState.CANCELED)
        self.store.save(task)
        return self._out(task, legacy, None, wrap=False)

    # ------------------------------------------------------------------ uscita
    def _out(self, task: Task, legacy: bool, history_length: int | None, wrap: bool) -> dict[str, Any]:
        d = task.wire()
        if history_length is not None and "history" in d:
            d["history"] = d["history"][-history_length:] if history_length > 0 else []
        if legacy:
            return task_to_legacy(d)  # in 0.3 il risultato è il Task "nudo" con kind="task"
        return {"task": d} if wrap else d

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception:  # la traccia non deve mai rompere l'agente
                log.debug("on_event fallito", exc_info=True)
