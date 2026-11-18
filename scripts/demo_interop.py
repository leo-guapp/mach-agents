"""Demo di interoperabilità su rete vera (prova generale per il gateway e lo spoke esterno).

Prima avviate, in due terminali:
    python scripts/run_local.py        # noi: registry :8000, agenti, gateway :9000
    python scripts/fake_team_b.py      # Team B finto: agente :8101, API :8102, indice :8103
Poi:
    python scripts/demo_interop.py

Mostra le tre direzioni:
  1. indici federati: noi importiamo l'indice del Team B, il Team B importa il nostro;
  2. inbound: il Team B chiama il nostro Agente Accessibilità tramite il gateway
     (con API key, scope, consenso per le scritture);
  3. outbound: deleghiamo al Team B (A2A), poi simuliamo guasti: API documentata, poi escalation.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a_lite import A2AClient, A2AError  # noqa: E402
from a2a_lite._http import httpx  # noqa: E402
from interop.foreign import ForeignAgentAdapter  # noqa: E402
from interop.mach import MachContext  # noqa: E402
from registry import DiscoveryClient, RegistryClient  # noqa: E402

OUR_REGISTRY, GATEWAY = "http://localhost:8000", "http://localhost:9000"
TB_AGENT, TB_API, TB_INDEX = "http://localhost:8101", "http://localhost:8102", "http://localhost:8103"
DEMO_KEY = "demo-key-cambiami"  # team-demo in gateway/config.example.json


def title(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        ours = RegistryClient(OUR_REGISTRY, http=http)
        theirs = RegistryClient(TB_INDEX, http=http)
        events = ours.post_event

        title("1. Indici federati")
        peer = await ours.add_peer(TB_INDEX, "team-b")
        print(f"   noi importiamo il Team B: {peer['agents']} agente/i, errore={peer['last_error']}")
        found = await ours.search("eventi culturali accessibili", origin="external")
        print(f"   ricerca esterna -> {found[0]['id']} (trust={found[0]['trust']})")
        peer = await theirs.add_peer(GATEWAY, "mach")
        print(f"   il Team B importa il nostro gateway: {peer['agents']} agenti")
        found = await theirs.search("accessibilità di una sede")
        print(f"   ricerca del Team B -> {found[0]['id']} su {found[0]['rpc_url']}")

        title("2. Inbound: il Team B chiama il nostro Agente Accessibilità via gateway")
        acc = A2AClient(f"{GATEWAY}/agents/accessibility", http=http, auth_token=DEMO_KEY, caller="team-demo")
        t = await acc.send(data={"skill": "accessibilita_sede", "args": {"struttura_id": "cdc_loreto"}},
                           metadata=MachContext(caller="teamb:planner", hops=1).to_metadata())
        print(f"   {t.status.state.value}: {t.result_text()}")
        try:
            await A2AClient(f"{GATEWAY}/agents/accessibility", http=http, auth_token="sbagliata").send("ciao")
        except A2AError as exc:
            print(f"   senza API key valida -> rifiutato: {exc.message}")
        hc = A2AClient(f"{GATEWAY}/agents/healthcare", http=http, auth_token=DEMO_KEY)
        try:
            await hc.send(data={"skill": "prenota", "args": {"slot_id": "S6", "chiave_idempotenza": "tb-1"}})
        except A2AError as exc:
            print(f"   prenotazione senza scope di scrittura -> {exc.message}")

        title("3. Outbound: deleghiamo un sub-task al Team B")
        ad = ForeignAgentAdapter(DiscoveryClient([ours]), http=http, on_event=events, timeout_s=10)
        ctx = MachContext()
        req = dict(ctx=ctx, skill="eventi_accessibili", q="eventi vicini",
                   data={"args": {"zona": "loreto"}, "codice_fiscale": "NON-DEVE-USCIRE"})
        r = await ad.delegate(**req)
        print(f"   canale={r.channel} esito={r.busta.status.value} trust={r.busta.provenance[0].trust}")
        print(f"   campi trattenuti dal gate dati: {ad.policy.log[-1]['dropped']}")
        await http.post(f"{TB_AGENT}/mode/fail")
        r = await ad.delegate(**req)
        print(f"   agente del Team B guasto -> canale={r.channel} esito={r.busta.status.value}")
        await http.post(f"{TB_API}/mode/down")
        r = await ad.delegate(**req)
        print(f"   anche l'API giù -> canale={r.channel}: {r.busta.user_message.text}")
        await http.post(f"{TB_AGENT}/mode/ok")
        await http.post(f"{TB_API}/mode/up")
        print("\n   Dettaglio dell'ultima busta:")
        print("   " + json.dumps(r.busta.to_json(), ensure_ascii=False, indent=2)[:700].replace("\n", "\n   "))
        print(f"\nApri {OUR_REGISTRY} per vedere tutti gli eventi, e gateway_audit.jsonl per l'audit.")


if __name__ == "__main__":
    asyncio.run(main())
