"""Espone le skill pubbliche di un agente come server MCP.

Perché: il whitepaper prevede agenti personali dei cittadini basati su MCP, e connettori
come server MCP. Con questo, un agente MCP (o Claude, o un client MCP del kit di partenza)
può usare i nostri agenti come tool, senza conoscere A2A.

    python -m agents.mcp_export accessibility --port 8203
    -> MCP streamable HTTP su http://localhost:8203/mcp

Le skill che scrivono (writes=True, es. prenota) NON vengono esportate: MCP non ha un canale
per il consenso strutturato, che per noi è obbligatorio. Restano disponibili via A2A.
"""
from __future__ import annotations

import argparse
import inspect
from typing import Any

from .base import BaseAgent, SkillSpec


def _tool_for(agent: BaseAgent, spec: SkillSpec):
    async def tool(**kwargs: Any) -> dict:
        res = await agent.invoke(spec.id, {k: v for k, v in kwargs.items() if v is not None})
        return res.busta

    params = [p for n, p in inspect.signature(spec.fn).parameters.items() if n != "ctx"]
    tool.__signature__ = inspect.Signature(params, return_annotation=dict)  # type: ignore[attr-defined]
    tool.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
    tool.__name__ = spec.id
    tool.__doc__ = spec.description
    return tool


def to_mcp(agent: BaseAgent, include_writes: bool = False):
    from mcp.server.mcpserver import MCPServer  # dipendenza opzionale: pip install "mcp>=2"

    server = MCPServer(agent.name, description=agent.description, version=agent.version)
    for spec in agent._skills.values():
        if not spec.public or (spec.writes and not include_writes):
            continue
        server.add_tool(_tool_for(agent, spec), name=spec.id,
                        description=f"{spec.description} (agente {agent.agent_id}, risposta in Busta 1.1)")
    return server


def main() -> None:
    import uvicorn

    from .accessibility.agent import AccessibilityAgent
    from .environment.agent import EnvironmentAgent
    from .health.agent import HealthAgent
    from .mobility.agent import MobilityAgent

    agents = {"accessibility": AccessibilityAgent, "mobility": MobilityAgent, "healthcare": HealthAgent,
              "environment": EnvironmentAgent}
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=sorted(agents))
    ap.add_argument("--port", type=int, default=8200)
    a = ap.parse_args()
    server = to_mcp(agents[a.agent]())
    uvicorn.run(server.streamable_http_app(), port=a.port)


if __name__ == "__main__":
    main()
