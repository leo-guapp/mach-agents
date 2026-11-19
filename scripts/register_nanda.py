"""Registra il nostro team sul NANDA Index (nodo di Milano) e verifica la risoluzione.

Il nostro gateway è un AI Catalog: all'Index basta un'organizzazione con
registry_url = URL pubblico del gateway e media_type = application/ai-catalog+json.

    # 1. controlla che l'Index risponda e com'è fatto
    python scripts/register_nanda.py --index http://localhost:8300 --check

    # 2. registra (crea l'account se manca) e prova la risoluzione completa
    python scripts/register_nanda.py --index http://localhost:8300 \\
        --email team@example.com --password ... --org-id team-mach \\
        --domain team-mach.example --gateway http://localhost:9000

    # solo stampa del payload, senza inviare nulla
    python scripts/register_nanda.py ... --dry-run

Sul nodo vero può servire la verifica dell'e-mail o del dominio (record DNS TXT): lo
script lo segnala; in quel caso chiedete agli organizzatori come attivare l'organizzazione.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a_lite._http import httpx  # noqa: E402
from nanda.catalog import AI_CATALOG_MEDIA_TYPE, index_identifier  # noqa: E402
from nanda.index_client import NandaIndexClient, NandaIndexError  # noqa: E402


def org_payload(a: argparse.Namespace, tags: list[str]) -> dict:
    return {"org_id": a.org_id, "display_name": a.display_name, "hosting_path": "registry",
            "domain": a.domain, "contact_email": a.email, "registry_url": a.gateway.rstrip("/"),
            "identifier": index_identifier(a.domain), "media_type": AI_CATALOG_MEDIA_TYPE,
            "description": a.description, "tags": tags[:32],
            "publisher": {"identifier": a.domain, "displayName": a.display_name, "identityType": "dns"},
            "catalog_metadata": {"profile": "MACH A2A 1.0", "contact": a.email}}


async def main(a: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=20) as http:
        idx = NandaIndexClient(a.index, http=http)
        if a.check:
            print("health:", await idx.health())
            orgs = await idx.list_index()
            print(f"organizzazioni attive: {len(orgs)}")
            for o in orgs[:20]:
                print(f"  - {o.get('org_id')}: {o.get('display_name')} [{o.get('media_type')}] -> {o.get('registry_url')}")
            return

        catalog = (await http.get(f"{a.gateway.rstrip('/')}/.well-known/ai-catalog.json")).json()
        entries = catalog.get("entries", [])
        tags = sorted({t for e in entries for t in e.get("tags", [])})
        payload = org_payload(a, tags)
        print("Catalogo del gateway:", [e["identifier"] for e in entries])
        print("Organizzazione da registrare:\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        if a.dry_run:
            return

        try:
            await idx.login(a.email, a.password)
        except NandaIndexError:
            await idx.register_account(a.email, a.password, a.display_name)
            print("account creato")
        try:
            rec = await idx.register_org(payload)
            print(f"organizzazione registrata: status={rec.get('status')}")
            if rec.get("status") != "active":
                print("ATTENZIONE: non ancora attiva (verifica e-mail/dominio richiesta dal nodo).")
        except NandaIndexError as exc:
            print(f"registrazione: {exc}")

        for e in entries[:3]:
            slug = e["identifier"].rsplit(":", 1)[-1]
            locator = index_identifier(a.domain, slug)
            try:
                res = await idx.resolve_agent(locator)
                print(f"risolto {locator} -> {res.card_url} (runtime {res.runtime_url})")
            except (NandaIndexError, httpx.HTTPError) as exc:
                print(f"risoluzione di {locator} fallita: {exc}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=os.getenv("NANDA_INDEX_URL", "http://localhost:8300"))
    ap.add_argument("--check", action="store_true", help="mostra lo stato dell'Index e esce")
    ap.add_argument("--email", default=os.getenv("NANDA_EMAIL"))
    ap.add_argument("--password", default=os.getenv("NANDA_PASSWORD"))
    ap.add_argument("--org-id", default="team-mach")
    ap.add_argument("--display-name", default="Team MACH")
    ap.add_argument("--domain", default=os.getenv("MACH_PUBLISHER_DOMAIN", "team-mach.example"))
    ap.add_argument("--gateway", default=os.getenv("MACH_GATEWAY_PUBLIC_URL", "http://localhost:9000"))
    ap.add_argument("--description", default="Agenti per accessibilità, mobilità, ambiente e sanità a Milano")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.dry_run and not (args.email and args.password):
        ap.error("servono --email e --password (o NANDA_EMAIL/NANDA_PASSWORD), oppure --check/--dry-run")
    asyncio.run(main(args))
