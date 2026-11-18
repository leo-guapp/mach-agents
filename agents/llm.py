"""Accesso all'LLM tramite API compatibile OpenAI (/v1/chat/completions).

Funziona con Gemma 4 servito da vLLM, Ollama, LM Studio, SGLang o qualunque
gateway compatibile: basta cambiare le variabili d'ambiente

    LLM_BASE_URL=http://localhost:8000/v1   (vLLM)  |  http://localhost:11434/v1 (Ollama)
    LLM_MODEL=google/gemma-4-26B-A4B-it      (il nome esatto dipende dal server)
    LLM_API_KEY=...                          (se serve)

Regola d'oro: l'LLM interpreta e scrive, le regole decidono. Ogni agente deve
funzionare anche con LLM spento (fallback deterministico), così la demo non
muore se l'endpoint dell'hackathon rallenta.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

from a2a_lite._http import httpx

from .tools import ToolRegistry


class LLM(Protocol):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
                   **kw) -> dict[str, Any]: ...


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 temperature: float = 0.3, timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def chat(self, messages, tools=None, **kw) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages,
                                "temperature": kw.pop("temperature", self.temperature), **kw}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = await self._http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


class ScriptedLLM:
    """LLM finto per i test: restituisce in ordine le risposte preparate."""

    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None, **kw) -> dict[str, Any]:
        self.calls.append(messages)
        r = self.responses.pop(0) if self.responses else ""
        return {"role": "assistant", "content": r} if isinstance(r, str) else r


def llm_from_env() -> LLM | None:
    base = os.getenv("LLM_BASE_URL")
    if not base:
        return None
    return OpenAICompatLLM(base, os.getenv("LLM_MODEL", "gemma-4"), os.getenv("LLM_API_KEY"),
                           float(os.getenv("LLM_TEMPERATURE", "0.3")))


def extract_json(text: str) -> Any:
    """Estrae il primo oggetto JSON da una risposta (anche se dentro ```json ... ```)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


async def json_call(llm: LLM, system: str, user: str) -> dict[str, Any] | None:
    msg = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         temperature=0)
    data = extract_json(msg.get("content") or "")
    return data if isinstance(data, dict) else None


async def run_tool_loop(llm: LLM, system: str, user: str, tools: ToolRegistry,
                        max_steps: int = 5) -> tuple[str, list[dict[str, Any]]]:
    """Ciclo classico: l'LLM chiede tool, noi li eseguiamo, finché risponde in testo."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    trace: list[dict[str, Any]] = []
    for _ in range(max_steps):
        msg = await llm.chat(messages, tools=tools.openai_schema())
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", trace
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c["function"]
            result = await tools.call(fn["name"], fn.get("arguments") or "{}")
            trace.append({"tool": fn["name"], "args": fn.get("arguments"), "result": result})
            messages.append({"role": "tool", "tool_call_id": c.get("id", fn["name"]),
                             "content": json.dumps(result, ensure_ascii=False, default=str)})
    return "Ho raggiunto il numero massimo di passaggi.", trace
