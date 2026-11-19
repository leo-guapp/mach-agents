"""Schede per Nanda Town 2 (projnanda/nanda-town-2): l'altra strada possibile per il "nodo".

In Nanda Town 2 un agente si elenca aprendo una pull request con un file YAML in
records/agents/<slug>.yaml. Il campo entry punta all'endpoint: a2a_card_url, mcp_url,
openapi_url, http_url...; consent.probes è obbligatorio se l'URL è live, perché la
Town lo sonda ogni 6 ore e pubblica le osservazioni.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def town_record(name: str, summary: str, card_url: str, github_owner: str, tags: list[str],
                mcp_url: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"a2a_card_url": card_url}
    if mcp_url:
        entry = {"mcp_url": mcp_url}  # la Town accetta un solo tipo di entry per scheda
    return {"slug": slug(name), "kind": "agent", "name": name, "summary": summary[:280],
            "owner": {"github": github_owner}, "entry": entry, "consent": {"probes": True},
            "tags": [t for t in tags if not t.startswith("mach:")][:10]}


def write_records(records: list[dict[str, Any]], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir) / "records" / "agents"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for rec in records:
        p = out / f"{rec['slug']}.yaml"
        p.write_text(yaml.safe_dump(rec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        paths.append(p)
    return paths
