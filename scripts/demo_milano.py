"""Prova generale "nodo di Milano": registrazione, risoluzione verificata, discovery, delega.

In tre terminali:
    python scripts/run_local.py          # noi (registry, 4 agenti, gateway :9000)
    python scripts/fake_milan_node.py    # finto NANDA Index :8300 (o usate quello vero con --index)
    python scripts/fake_team_b.py        # un altro team :8101
Poi:
    python scripts/demo_milano.py [--index http://localhost:8300]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a_lite import A2AClient  # noqa: E402
from a2a_lite._http import httpx  # noqa: E402
from interop.foreign import ForeignAgentAdapter  # noqa: E402
from interop.mach import MachContext  # noqa: E402
from nanda.catalog import A2A_CARD_MEDIA_TYPE, AI_CATALOG_MEDIA_TYPE, index_identifier  # noqa: E402
from nanda.index_client import NandaIndexClient, NandaIndexError, NandaIndexSource  # noqa: E402
from nanda.signing import public_key_from_multibase, verify_agentaddr, verify_agentfacts_vc  # noqa: E402
from registry import DiscoveryClient, RegistryClient  # noqa: E402

GW, DOMAIN = "http://localhost:9000", "team-mach.example"


def title(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


async def ensure_org(idx: NandaIndexClient, email: str, org: dict) -> None:
    try:
        await idx.login(email, "demo-password")
    except NandaIndexError:
        await idx.register_account(email, "demo-password")
    try:
        rec = await idx.register_org(org)
        print(f"   {org['org_id']}: registrata (status={rec.get('status')})")
    except NandaIndexError as exc:
        print(f"   {org['org_id']}: {exc}")


async def main(index_url: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        title("1. Registrazione sul nodo (NANDA Index v2)")
        await ensure_org(NandaIndexClient(index_url, http=http), "team-mach@example.com", {
            "org_id": "team-mach", "display_name": "Team MACH", "domain": DOMAIN, "registry_url": GW,
            "media_type": AI_CATALOG_MEDIA_TYPE, "description": "Accessibilità, mobilità, ambiente, sanità",
            "tags": ["accessibilita", "mobilita", "ambiente"]})
        await ensure_org(NandaIndexClient(index_url, http=http), "team-b@example.com", {
            "org_id": "team-b", "display_name": "Team B", "media_type": A2A_CARD_MEDIA_TYPE,
            "registry_url": "http://localhost:8101/.well-known/agent-card.json",
            "description": "Eventi culturali accessibili", "tags": ["eventi", "cultura"]})

        title("2. Risoluzione verificata di un nostro agente partendo dall'URN")
        idx = NandaIndexClient(index_url, http=http)
        locator = index_identifier(DOMAIN, "accessibility")
        res = await idx.resolve_agent(locator)
        print(f"   {locator}\n   -> catalogo: {res.catalog_entry['identifier']}\n   -> card: {res.card_url}")
        did = (await http.get(f"{GW}/.well-known/did.json")).json()
        pub = public_key_from_multibase(did["verificationMethod"][0]["publicKeyMultibase"])
        addr = (await http.get(f"{GW}/agents/accessibility/agentaddr.json")).json()
        rec = verify_agentaddr(addr, pub)
        facts = verify_agentfacts_vc((await http.get(rec["f"])).json(), pub)
        print(f"   AgentAddr firmato ({addr['size_bytes']} byte) e AgentFacts VC verificati con {did['id']}")
        print(f"   endpoint dichiarato: {facts['endpoints']['static'][0]}")

        title("3. Discovery sul nodo: troviamo gli altri team (ma non noi stessi)")
        source = NandaIndexSource(idx, exclude_registry_urls={GW})
        disc = DiscoveryClient([RegistryClient("http://localhost:8000", http=http), source])
        found = await disc.search("eventi culturali", origin="external")
        for f in found[:3]:
            print(f"   {f['id']} ({f['origin']}, via {f.get('source', 'registry')}) -> {f['card_url']}")

        title("4. Delega al Team B tramite lo spoke esterno")
        r = await ForeignAgentAdapter(disc, http=http).delegate(ctx=MachContext(), q="eventi culturali",
                                                                data={"zona": "loreto"})
        print(f"   canale={r.channel} esito={r.busta.status.value}: {r.busta.user_message.text}")

        title("5. Agente Ambiente: chiede il percorso all'Agente Mobilità e confronta i mezzi")
        env = A2AClient(f"{GW}/agents/environment", http=http, auth_token="demo-key-cambiami")
        t = await env.send(data={"skill": "confronta_mezzi", "args": {"origine": "Loreto", "destinazione": "Turati"}})
        print(f"   {t.result_text()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="http://localhost:8300")
    asyncio.run(main(ap.parse_args().index))
