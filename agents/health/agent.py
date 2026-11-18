"""Agente Sanità: orientamento, ricerca disponibilità, prenotazione (tutto su dati simulati).

Avvio:  python -m agents.health.agent --port 8001 --registry http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from a2a_lite import Reply, TaskContext

from ..base import BaseAgent, _norm_tokens, run, skill
from ..envelope import Esito, Fonte, Status

DATA = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
FONTE_SIM = Fonte("Disponibilità simulate del prototipo MACH", simulato=True)


class HealthAgent(BaseAgent):
    name = "Agente Sanità"
    agent_id = "mach:healthcare"
    domain = "healthcare"
    description = ("Orienta il cittadino tra i servizi sanitari di Milano (medico curante, PrenotaSalute/CUP, "
                   "Case di Comunità), cerca disponibilità per visite e prenota. Non fa diagnosi. Dati simulati.")
    domande = {
        "bisogno": "Di che cosa hai bisogno? Descrivilo con parole tue.",
        "prestazione": "Quale visita o esame ti serve?",
        "slot_id": "Quale appuntamento scegli (codice slot)?",
        "chiave_idempotenza": "Manca la chiave di idempotenza della richiesta.",
    }

    def setup(self) -> None:
        self.data = DATA
        self.strutture = {s["id"]: s for s in DATA["strutture"]}
        self.prenotazioni: dict[str, dict] = {}   # chiave_idempotenza -> prenotazione
        self.slot_occupati: dict[str, str] = {}   # slot_id -> chiave_idempotenza

    # Il controllo di emergenza viene prima di qualunque skill.
    async def handle(self, ctx: TaskContext) -> Reply:
        testo = ctx.text.lower()
        if any(p in testo for p in self.data["emergenza_parole"]):
            return self.reply(Esito(Status.EMERGENCY,
                                    "Quello che descrivi potrebbe essere un'emergenza: chiama subito il 112. "
                                    "Interrompo il percorso amministrativo.",
                                    data={"numero": "112"}), ctx, "emergenza")
        return await super().handle(ctx)

    @skill("orienta", "Orientamento sanitario",
           "Dato un bisogno descritto a parole, indica i passi da seguire, chi è responsabile e quali dati servono.",
           tags=["sanita", "orientamento", "aiuto", "medico"],
           examples=["Ho mal di schiena, a chi mi rivolgo?", "Ho una ricetta ma non so come prenotare"])
    async def orienta(self, ctx: TaskContext, bisogno: str) -> Esito:
        q = _norm_tokens(bisogno)
        scored = sorted(self.data["percorsi"],
                        key=lambda p: len(q & _norm_tokens(" ".join(p["parole_chiave"]))), reverse=True)
        best = scored[0]
        if not q & _norm_tokens(" ".join(best["parole_chiave"])):
            return Esito(Status.PARTIAL, "Non sono sicuro di aver capito. Puoi descrivere meglio il bisogno?",
                         data={"percorsi_possibili": [p["titolo"] for p in self.data["percorsi"]]})
        fonti = [Fonte(p["fonte"], p["url"]) for p in best["passi"]]
        testo = best["titolo"] + ": " + " → ".join(f"{p['chi']} ({p['cosa']})" for p in best["passi"])
        return Esito.ok(testo, {"percorso": best["id"], "passi": best["passi"]}, fonti)

    @skill("cerca_disponibilita", "Cerca disponibilità visita",
           "Cerca appuntamenti disponibili per una prestazione, opzionalmente in una zona, e può verificare "
           "l'accessibilità delle sedi chiedendo all'agente accessibilità.",
           tags=["sanita", "prenotazione", "disponibilita", "cup", "visita"],
           examples=["Cerca una visita ortopedica", "Disponibilità visita cardiologica a Niguarda"])
    async def cerca_disponibilita(self, ctx: TaskContext, prestazione: str, zona: str | None = None,
                                  ha_prescrizione: bool | None = None,
                                  verifica_accessibilita: bool = False) -> Esito:
        if ha_prescrizione is None:
            return Esito.chiedi("Hai già una prescrizione (ricetta) del medico per questa visita?",
                                ["ha_prescrizione"])
        if not ha_prescrizione:
            return Esito.ok("Per prenotare una visita specialistica serve prima la prescrizione: "
                            "rivolgiti al tuo medico curante.",
                            {"passo_successivo": "medico_curante"}, [Fonte("Indicazione generale (da verificare)")])
        q = _norm_tokens(prestazione)
        slots = [s for s in self.data["slot"]
                 if q & _norm_tokens(s["prestazione"]) - {"visita"} and s["id"] not in self.slot_occupati]
        if zona:
            z = zona.lower()
            slots.sort(key=lambda s: 0 if self.strutture[s["struttura_id"]]["zona"] in z
                       or z in self.strutture[s["struttura_id"]]["zona"] else 1)
        if not slots:
            return Esito(Status.PARTIAL, f"Nessuna disponibilità trovata per «{prestazione}».",
                         {"alternative": ["Contact Center regionale", "Sportello CUP"]}, [FONTE_SIM],
                         mancanti=["disponibilita"])
        opzioni = [{**s, "struttura": self.strutture[s["struttura_id"]]} for s in slots[:3]]

        if verifica_accessibilita:
            # Esempio di chiamata agente -> agente: chi sa verificare l'accessibilità?
            async def check(o):
                task = await self.call_agent(
                    data={"skill": "accessibilita_sede", "args": {"struttura_id": o["struttura_id"]}},
                    skill_id="accessibilita_sede", context_id=ctx.context_id, ctx=ctx)
                o["accessibilita"] = task.result_data().get("data") if task and task.result_data() else None
            await asyncio.gather(*(check(o) for o in opzioni))

        testo = "Disponibilità: " + "; ".join(
            f"{o['id']} – {o['struttura']['nome']}, {o['data']} ore {o['ora']}" for o in opzioni)
        return Esito.ok(testo, {"prestazione": prestazione, "opzioni": opzioni}, [FONTE_SIM])

    @skill("prenota", "Prenota appuntamento",
           "Prenota uno slot. È idempotente: la stessa chiave_idempotenza non crea mai due prenotazioni.",
           tags=["sanita", "prenotazione"], examples=["Prenota lo slot S1"], writes=True)
    async def prenota(self, ctx: TaskContext, slot_id: str, chiave_idempotenza: str) -> Esito:
        if chiave_idempotenza in self.prenotazioni:
            p = self.prenotazioni[chiave_idempotenza]
            return Esito.ok(f"Prenotazione già registrata: {p['codice']}.", {**p, "duplicato_evitato": True},
                            [FONTE_SIM])
        slot = next((s for s in self.data["slot"] if s["id"] == slot_id), None)
        if not slot:
            return Esito.errore(f"Lo slot {slot_id} non esiste.")
        if slot_id in self.slot_occupati:
            return Esito.errore(f"Lo slot {slot_id} non è più disponibile.", {"riprova_con": "cerca_disponibilita"})
        codice = "PRN-" + uuid.uuid4().hex[:6].upper()
        p = {"codice": codice, "slot": slot, "struttura": self.strutture[slot["struttura_id"]]}
        self.prenotazioni[chiave_idempotenza] = p
        self.slot_occupati[slot_id] = chiave_idempotenza
        return Esito.ok(f"Prenotazione confermata: {codice}, {p['struttura']['nome']} il {slot['data']} "
                        f"alle {slot['ora']}.", p, [FONTE_SIM])

    def consent_summary(self, spec, kwargs) -> str:
        if spec.id == "prenota":
            slot = next((s for s in self.data["slot"] if s["id"] == kwargs.get("slot_id")), None)
            if slot:
                st = self.strutture[slot["struttura_id"]]
                return f"prenotare {slot['prestazione']} il {slot['data']} alle {slot['ora']} presso {st['nome']}"
        return super().consent_summary(spec, kwargs)

    def extract_args(self, skill_id: str, text: str) -> dict:
        if skill_id == "orienta":
            return {"bisogno": text}
        if skill_id == "cerca_disponibilita":
            t = text.lower()
            args = {}
            for p in self.data["prestazioni"]:
                if p.split()[-1][:6] in t:
                    args["prestazione"] = p
            for s in self.strutture.values():
                if s["zona"] in t:
                    args["zona"] = s["zona"]
            return args
        return {}


if __name__ == "__main__":
    run(HealthAgent, 8001)
