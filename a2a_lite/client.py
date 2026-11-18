"""Client A2A: legge la Agent Card, invia messaggi, segue i task.

Parla A2A 1.0 per default. Se il server risponde "metodo sconosciuto"
(-32601) riprova automaticamente con i nomi 0.3 ("message/send" ecc.):
così potete chiamare agenti di altri team qualunque SDK usino.
"""
from __future__ import annotations

import itertools
from typing import Any, Awaitable, Callable

from . import errors as E
from ._http import httpx
from .types import (PROTOCOL_VERSION, AgentCard, Message, Task, message_to_legacy,
                    task_from_legacy)

EventHook = Callable[[dict[str, Any]], Awaitable[None]]


class A2AClient:
    def __init__(self, base_url: str, *, http: "httpx.AsyncClient | None" = None,
                 auth_token: str | None = None, timeout_s: float = 120.0,
                 caller: str | None = None, on_event: EventHook | None = None) -> None:
        # accetta sia l'URL base dell'agente sia l'URL completo della sua card
        if "/.well-known/" in base_url:
            self._card_url = base_url
            self.base_url = base_url.split("/.well-known/")[0]
        else:
            self.base_url = base_url.rstrip("/")
            self._card_url = f"{self.base_url}/.well-known/agent-card.json"
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._own_http = http is None
        self.auth_token = auth_token
        self.caller = caller
        self.on_event = on_event
        self.card: AgentCard | None = None
        self._rpc_url: str | None = None
        self._legacy = False
        self._ids = itertools.count(1)

    async def __aenter__(self) -> "A2AClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ card
    async def get_card(self) -> AgentCard:
        if self.card is None:
            r = await self._http.get(self._card_url)
            if r.status_code == 404:  # agenti molto vecchi
                r = await self._http.get(f"{self.base_url}/.well-known/agent.json")
            r.raise_for_status()
            self.card = AgentCard.parse(r.json())
            self._rpc_url = self.card.jsonrpc_url()
            iface = self.card.supported_interfaces[0]
            self._legacy = iface.protocol_version.startswith("0.")
        return self.card

    # ------------------------------------------------------------------ RPC
    async def _call(self, method: str, legacy_method: str, params: dict[str, Any]) -> Any:
        await self.get_card()
        for attempt_legacy in ([True] if self._legacy else [False, True]):
            name = legacy_method if attempt_legacy else method
            p = params
            if attempt_legacy and "message" in params:
                p = {**params, "message": message_to_legacy(params["message"])}
            headers = {"A2A-Version": "0.3" if attempt_legacy else PROTOCOL_VERSION}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
            body = {"jsonrpc": "2.0", "id": next(self._ids), "method": name, "params": p}
            r = await self._http.post(self._rpc_url, json=body, headers=headers)
            try:
                payload = r.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict) or ("error" not in payload and "result" not in payload):
                r.raise_for_status()  # nessun corpo JSON-RPC: errore HTTP puro
                raise E.InternalError(f"Risposta non JSON-RPC da {self._rpc_url}")
            if "error" in payload:  # anche con HTTP 401/403/429 dal gateway: errore leggibile
                err = payload["error"]
                if err.get("code") in (-32601, -32009) and not attempt_legacy:
                    continue  # proviamo la forma 0.3
                raise E.from_jsonrpc(err)
            if attempt_legacy:
                self._legacy = True
            return payload["result"], attempt_legacy
        raise E.MethodNotFound(method)

    @staticmethod
    def _to_task(result: Any, legacy: bool) -> Task:
        if legacy:
            if result.get("kind") == "message":  # alcuni agenti rispondono con un messaggio diretto
                result = {"id": result.get("taskId") or "no-task", "contextId": result.get("contextId") or "",
                          "status": {"state": "completed", "message": result}, "kind": "task"}
            return Task.model_validate(task_from_legacy(result))
        if "task" in result:
            return Task.model_validate(result["task"])
        if "message" in result:  # risposta "Message" senza task
            m = result["message"]
            return Task.model_validate({"id": m.get("taskId") or "no-task", "contextId": m.get("contextId") or "",
                                        "status": {"state": "TASK_STATE_COMPLETED", "message": m}})
        return Task.model_validate(result)  # GetTask/CancelTask: Task diretto

    async def send(self, text: str | None = None, data: dict[str, Any] | None = None, *,
                   context_id: str | None = None, task_id: str | None = None,
                   history_length: int | None = None, metadata: dict[str, Any] | None = None) -> Task:
        """Invia un messaggio. Passa task_id per rispondere a un task in INPUT_REQUIRED.
        `metadata` viaggia nel Message (es. il blocco "mach" con trace_id, hops, consenso)."""
        msg = Message.user(text, data, context_id=context_id, task_id=task_id, metadata=metadata)
        params: dict[str, Any] = {"message": msg.wire()}
        if history_length is not None:
            params["configuration"] = {"historyLength": history_length}
        await self._emit({"type": "call", "from": self.caller, "to": self.base_url, "text": text, "data": data,
                          "task_id": task_id, "context_id": context_id})
        result, legacy = await self._call("SendMessage", "message/send", params)
        task = self._to_task(result, legacy)
        await self._emit({"type": "result", "from": self.base_url, "to": self.caller, "task_id": task.id,
                          "state": task.status.state.value, "text": task.result_text(),
                          "data": task.result_data()})
        return task

    async def get_task(self, task_id: str, history_length: int | None = None) -> Task:
        params: dict[str, Any] = {"id": task_id}
        if history_length is not None:
            params["historyLength"] = history_length
        result, legacy = await self._call("GetTask", "tasks/get", params)
        return self._to_task(result, legacy)

    async def cancel(self, task_id: str) -> Task:
        result, legacy = await self._call("CancelTask", "tasks/cancel", {"id": task_id})
        return self._to_task(result, legacy)

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception:
                pass
