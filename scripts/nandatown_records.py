"""Genera le schede YAML per Nanda Town 2 dai nostri agenti pubblicati sul gateway.

    python scripts/nandatown_records.py --gateway https://<gateway-pubblico> --github leo-guapp --out town/
    -> town/records/agents/<slug>.yaml, da proporre con una pull request a projnanda/nanda-town-2

Attenzione: la Town sonda solo URL pubblici su porte 80/443 (niente localhost).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a_lite._http import httpx  # noqa: E402
from nanda.town import town_record, write_records  # noqa: E402


async def main(a: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=20) as http:
        catalog = (await http.get(f"{a.gateway.rstrip('/')}/.well-known/ai-catalog.json")).json()
    records = [town_record(f"{a.prefix} {e['displayName']}", e.get("description", ""), e["url"], a.github,
                           ["milano", "mach"] + e.get("tags", []))
               for e in catalog.get("entries", [])]
    for p in write_records(records, a.out):
        print("scritto", p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--github", required=True, help="utente GitHub proprietario delle schede")
    ap.add_argument("--prefix", default="MACH")
    ap.add_argument("--out", default="town")
    asyncio.run(main(ap.parse_args()))
