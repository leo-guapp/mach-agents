"""Registro degli strumenti (tool) di un agente.

Si scrive una normale funzione Python con type hints e docstring; il registro
ne ricava lo schema JSON per il function calling dell'LLM (formato OpenAI,
che vLLM/Ollama usano anche per Gemma 4) e, se serve, la espone come server
MCP con `to_mcp()`. Così i tool si scrivono una volta sola.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import create_model


def schema_from_signature(fn: Callable, skip: tuple[str, ...] = ("self", "ctx")) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, p in inspect.signature(fn).parameters.items():
        if name in skip or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
        fields[name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
    schema = create_model(f"{fn.__name__}_args", **fields).model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    schema: dict[str, Any]

    async def __call__(self, **kwargs) -> Any:
        result = self.fn(**kwargs)
        return await result if inspect.isawaitable(result) else result


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def tool(self, fn: Callable | None = None, *, name: str | None = None, description: str | None = None):
        def deco(f: Callable) -> Callable:
            t = Tool(name or f.__name__, (description or inspect.getdoc(f) or "").strip(), f,
                     schema_from_signature(f))
            self.tools[t.name] = t
            return f
        return deco(fn) if fn else deco

    def openai_schema(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.schema}} for t in self.tools.values()]

    async def call(self, name: str, args: dict[str, Any] | str) -> Any:
        if isinstance(args, str):
            args = json.loads(args or "{}")
        if name not in self.tools:
            return {"error": f"tool sconosciuto: {name}"}
        try:
            return await self.tools[name](**args)
        except TypeError as exc:
            return {"error": f"argomenti non validi per {name}: {exc}"}

    def to_mcp(self, server_name: str):
        """Espone gli stessi tool come server MCP (richiede `pip install "mcp>=2"`).

        Esempio: registry.to_mcp("dati-sanita").run(transport="streamable-http")
        """
        from mcp.server.mcpserver import MCPServer  # import opzionale

        server = MCPServer(server_name)
        for t in self.tools.values():
            server.add_tool(t.fn, name=t.name, description=t.description)
        return server
