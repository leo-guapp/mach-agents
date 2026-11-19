"""Ponte verso il kit di partenza NANDA (o qualunque framework che vuole "testo -> testo").

Il kit ufficiale del hackathon non è ancora pubblico. Le versioni note degli strumenti NANDA
(es. nanda-adapter) chiedono una funzione che riceve il testo di un messaggio e restituisce
una stringa: l'adapter si occupa di rete, registrazione e traduzione di protocollo.
Questo modulo trasforma un nostro agente proprio in quella funzione, così la logica
(skill, consenso, Busta) si riusa dentro qualunque scaffolding ci diano.

    from agents.accessibility.agent import AccessibilityAgent
    from interop.nanda_bridge import text_handler, sync_text_handler
    handle = sync_text_handler(AccessibilityAgent())     # per adapter sincroni
    print(handle("L'ospedale Niguarda è accessibile in carrozzina?"))

Regole:
  * testo libero -> routing dell'agente; risposta = user_message.text della Busta;
  * JSON {"skill": ..., "args": ...} -> chiamata strutturata; risposta = Busta in JSON;
  * se l'agente chiede un dato (INPUT_REQUIRED), il messaggio successivo lo continua
    (una conversazione alla volta per handler: per più utenti create un handler ciascuno).
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Awaitable, Callable

from agents.base import BaseAgent


def text_handler(agent: BaseAgent, metadata: dict[str, Any] | None = None) -> Callable[[str], Awaitable[str]]:
    state: dict[str, str | None] = {"pending": None}

    async def handle(message: str) -> str:
        structured = None
        stripped = (message or "").strip()
        if stripped.startswith("{"):
            try:
                structured = json.loads(stripped)
            except json.JSONDecodeError:
                structured = None
        if isinstance(structured, dict) and "skill" in structured:
            res = await agent.invoke(structured["skill"], structured.get("args") or {},
                                     metadata=structured.get("metadata") or metadata, task_id=state["pending"])
        else:
            res = await agent.invoke(text=message, metadata=metadata, task_id=state["pending"])
        state["pending"] = res.task_id if res.state.endswith("INPUT_REQUIRED") else None
        if structured is not None:
            return json.dumps(res.busta, ensure_ascii=False)
        return res.text or json.dumps(res.busta, ensure_ascii=False)

    return handle


class _LoopThread:
    """Un event loop dedicato, per chiamare codice async da funzioni sincrone senza conflitti."""
    _instance: "_LoopThread | None" = None

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    @classmethod
    def get(cls) -> "_LoopThread":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def sync_text_handler(agent: BaseAgent, metadata: dict[str, Any] | None = None,
                      timeout_s: float = 120) -> Callable[[str], str]:
    ahandle = text_handler(agent, metadata)

    def handle(message: str) -> str:
        fut = asyncio.run_coroutine_threadsafe(ahandle(message), _LoopThread.get().loop)
        return fut.result(timeout=timeout_s)

    return handle
