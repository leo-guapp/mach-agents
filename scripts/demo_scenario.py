"""Scenario dimostrativo end-to-end, da lanciare con gli agenti avviati (scripts/run_local.py).

Fa le veci di un orchestratore minimale (il vero orchestratore è il layer 2):
  1. trova gli agenti nel registry per capacità (non conosce i loro indirizzi);
  2. chiede disponibilità all'Agente Sanità e risponde alla sua domanda (INPUT_REQUIRED);
  3. in parallelo chiede percorsi accessibili all'Agente Mobilità;
  4. la città cambia (ascensore guasto) e il percorso viene ricalcolato;
  5. prova a prenotare: l'agente chiede il consenso (INPUT_REQUIRED con l'azione proposta);
     con il consenso prenota, e ripetendo la richiesta non crea doppioni.

    python scripts/demo_scenario.py [--registry http://localhost:8000]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a_lite import A2AClient, TaskState  # noqa: E402
from a2a_lite._http import httpx  # noqa: E402
from interop.mach import Consent, MachContext  # noqa: E402
from registry import RegistryClient  # noqa: E402


def show(title: str, task) -> None:
    d = task.result_data() or {}
    print(f"\n== {title}\n   stato A2A: {task.status.state.value} | esito: {d.get('status')}")
    print("   " + (task.result_text() or "").replace("\n", "\n   "))


async def main(registry_url: str) -> None:
    async with httpx.AsyncClient(timeout=60) as http:
        rc = RegistryClient(registry_url, http=http)
        on_event = rc.post_event

        async def agent_for(**query) -> A2AClient:
            found = await rc.search(**query)
            if not found:
                raise SystemExit(f"Nessun agente trovato per {query}: gli agenti sono avviati?")
            print(f"   discovery {query} -> {found[0]['id']} ({found[0]['rpc_url']})")
            return A2AClient(found[0]["card_url"], http=http, caller="demo-orchestratore", on_event=on_event)

        print("== Discovery")
        sanita = await agent_for(q="prenotare visita specialistica")
        mobilita = await agent_for(skill="percorso")

        t = await sanita.send("Ho mal di schiena e non so a chi rivolgermi")
        show("Orientamento (testo libero)", t)

        t = await sanita.send(data={"skill": "cerca_disponibilita",
                                    "args": {"prestazione": "visita ortopedica", "verifica_accessibilita": True}})
        show("Ricerca disponibilità", t)
        if t.status.state == TaskState.INPUT_REQUIRED:
            t = await sanita.send("sì", task_id=t.id, context_id=t.context_id)
            show("Risposta alla domanda dell'agente", t)
        opzioni = t.result_data()["data"]["opzioni"]

        async def percorso(o):
            return await mobilita.send(data={"skill": "percorso", "args": {
                "origine": "Loreto", "destinazione": o["struttura_id"], "senza_barriere": True}})
        percorsi = await asyncio.gather(*(percorso(o) for o in opzioni))
        for o, p in zip(opzioni, percorsi):
            acc = (o.get("accessibilita") or {}).get("barriere")
            show(f"Percorso verso {o['struttura']['nome']} (barriere sede: {acc})", p)

        await mobilita.send(data={"skill": "segnala_evento",
                                  "args": {"tipo": "ascensore_guasto", "stazione": "Ca' Granda"}})
        p = await percorso(opzioni[0])
        show("La città cambia: ascensore guasto a Ca' Granda -> nuovo percorso", p)

        args = {"slot_id": opzioni[0]["id"], "chiave_idempotenza": "pratica-demo-001"}
        b0 = await sanita.send(data={"skill": "prenota", "args": args})
        show("Prenotazione senza consenso: l'agente propone l'azione e chiede", b0)
        # Il consenso lo raccoglie il Layer 4 (Gateway consenso) e viaggia in metadata.mach.consent
        md = MachContext(consent=[Consent(skill="prenota", granted_by="user", reference="decisione-demo-1",
                                          idempotency_key="pratica-demo-001")]).to_metadata()
        b1 = await sanita.send(data={"skill": "prenota", "args": args}, metadata=md)
        b2 = await sanita.send(data={"skill": "prenota", "args": args}, metadata=md)
        show("Prenotazione con consenso", b1)
        show("Stessa richiesta ripetuta (risposta persa?)", b2)
        print("\nDettaglio ultimo esito:\n" + json.dumps(b2.result_data(), ensure_ascii=False, indent=2)[:800])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="http://localhost:8000")
    asyncio.run(main(ap.parse_args().registry))
