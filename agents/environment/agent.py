"""Agente Ambiente: impronta di carbonio degli spostamenti e budget personale (traccia Environment).

Ricalca lo scenario "Personal Carbon Budget Agent" del whitepaper: dalla consapevolezza
all'azione, collegando le emissioni ad alternative concrete. Per confrontare i mezzi
chiede il percorso all'Agente Mobilità via A2A (trovato nel registry): è un esempio
di passaggio di compiti tra agenti.

Avvio:  python -m agents.environment.agent --port 8004 --registry http://localhost:8000
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from a2a_lite import TaskContext

from ..base import BaseAgent, run, skill
from ..envelope import Esito, Fonte, Status

DATA = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
FONTE = Fonte("Fattori di emissione indicativi del prototipo MACH", simulato=True)


class EnvironmentAgent(BaseAgent):
    name = "Agente Ambiente"
    agent_id = "mach:environment"
    domain = "environment"
    description = ("Stima le emissioni di CO2 degli spostamenti, confronta i mezzi per un tragitto "
                   "(chiedendo il percorso all'agente mobilità) e aiuta a rispettare un budget di carbonio.")
    domande = {"mezzo": "Con quale mezzo (auto, metro, bus, bici, a piedi...)?",
               "km": "Quanti chilometri?", "origine": "Da dove parti?", "destinazione": "Dove devi arrivare?",
               "budget_settimanale_kg": "Qual è il tuo budget settimanale di CO2, in kg?",
               "consumato_kg": "Quanta CO2 hai già emesso questa settimana, in kg?"}

    def setup(self) -> None:
        self.fattori: dict[str, float] = DATA["fattori_g_per_km"]

    def _mezzo(self, testo: str) -> str | None:
        t = testo.lower().strip()
        if t in self.fattori:
            return t
        for k, v in DATA["sinonimi"].items():
            if k in t:
                return v
        return next((m for m in self.fattori if m in t), None)

    # ------------------------------------------------------------------ skill
    @skill("impronta_spostamento", "Impronta di uno spostamento",
           "Calcola i grammi di CO2 equivalente di uno spostamento dato il mezzo e i chilometri.",
           tags=["ambiente", "co2", "emissioni", "carbonio"],
           examples=["Quanta CO2 emetto facendo 12 km in auto?"])
    async def impronta_spostamento(self, ctx: TaskContext, mezzo: str, km: float) -> Esito:
        m = self._mezzo(mezzo)
        if m is None:
            return Esito.errore(f"Mezzo non riconosciuto: {mezzo}", {"mezzi_noti": sorted(self.fattori)})
        g = round(self.fattori[m] * km)
        auto = round(self.fattori["auto"] * km)
        confronto = "" if m == "auto" else f" (in auto sarebbero {auto} g)"
        return Esito.ok(f"{km:g} km in {m}: circa {g} g di CO2{confronto}.",
                        {"mezzo": m, "km": km, "co2_g": g, "co2_auto_g": auto, "risparmio_vs_auto_g": auto - g},
                        [FONTE])

    @skill("confronta_mezzi", "Confronto tra mezzi",
           "Per un tragitto in città confronta tempo ed emissioni di metro, auto, bici e a piedi; "
           "il percorso con i mezzi pubblici lo chiede all'agente mobilità.",
           tags=["ambiente", "co2", "mobilita", "confronto"],
           examples=["Conviene la metro o l'auto da Loreto a Niguarda?"])
    async def confronta_mezzi(self, ctx: TaskContext, origine: str, destinazione: str,
                              senza_barriere: bool = False) -> Esito:
        task = await self.call_agent(
            data={"skill": "percorso", "args": {"origine": origine, "destinazione": destinazione,
                                                "senza_barriere": senza_barriere}},
            skill_id="percorso", ctx=ctx)
        busta = task.result_data() if task else None
        route = (busta or {}).get("data") or {}
        if not route.get("tratte") and route.get("minuti_stimati") != 0:
            return Esito(Status.PARTIAL, "Non riesco a ottenere il percorso dall'agente mobilità: "
                                         "dimmi quanti km è il tragitto e lo calcolo con impronta_spostamento.",
                         {"agente_mobilita": "non disponibile" if not task else (busta or {}).get("status")},
                         [FONTE], mancanti=["percorso"])
        fermate = sum(t.get("fermate", 0) for t in route.get("tratte", []))
        km = round(max(fermate * 0.9, 0.5), 1)
        km_strada = round(km * DATA["fattore_percorso_stradale"], 1)
        opzioni = [{"mezzo": "metro", "km": km, "minuti": route.get("minuti_stimati"),
                    "co2_g": round(self.fattori["metro"] * km), "note": route.get("avvisi") or []}]
        opzioni.append({"mezzo": "auto", "km": km_strada, "minuti": round(km_strada / DATA["velocita_kmh"]["auto"] * 60),
                        "co2_g": round(self.fattori["auto"] * km_strada), "note": ["traffico e parcheggio esclusi"]})
        for mezzo, limite in (("bici", 8), ("piedi", 2.5)):
            if km_strada <= limite:
                opzioni.append({"mezzo": mezzo, "km": km_strada,
                                "minuti": round(km_strada / DATA["velocita_kmh"][mezzo] * 60), "co2_g": 0,
                                "note": ["non adatto con mobilità ridotta"] if senza_barriere else []})
        opzioni.sort(key=lambda o: (o["co2_g"], o["minuti"] or 999))
        migliore = next((o for o in opzioni if not (senza_barriere and o["mezzo"] in ("bici", "piedi"))), opzioni[0])
        auto_g = next(o["co2_g"] for o in opzioni if o["mezzo"] == "auto")
        return Esito.ok(
            f"Da {route.get('origine', origine)} a {route.get('destinazione', destinazione)}: consiglio "
            f"{migliore['mezzo']} ({migliore['minuti']} min, {migliore['co2_g']} g CO2) invece dell'auto "
            f"({auto_g} g).",
            {"opzioni": opzioni, "consigliato": migliore["mezzo"], "risparmio_vs_auto_g": auto_g - migliore["co2_g"],
             "percorso_da": "mach:mobility"},
            [FONTE, Fonte("Percorso dall'Agente Mobilità (A2A)", simulato=True)])

    @skill("budget_carbonio", "Budget di carbonio",
           "Dato un budget settimanale e quanto già emesso, dice quanto resta e come ridurre.",
           tags=["ambiente", "co2", "budget", "carbonio"],
           examples=["Ho un budget di 20 kg di CO2 a settimana e ne ho già emessi 14"])
    async def budget_carbonio(self, ctx: TaskContext, budget_settimanale_kg: float, consumato_kg: float) -> Esito:
        resto = round(budget_settimanale_kg - consumato_kg, 2)
        quota = consumato_kg / budget_settimanale_kg if budget_settimanale_kg else 1
        km_auto_rimasti = max(round(resto * 1000 / self.fattori["auto"], 1), 0)
        consigli = []
        if quota >= 0.7:
            consigli.append("Per gli spostamenti sotto i 3 km preferisci bici o piedi.")
            consigli.append("Sostituisci un tragitto in auto con la metro: circa -140 g per km.")
        stato = "superato" if resto < 0 else ("attenzione" if quota >= 0.7 else "in linea")
        return Esito.ok(f"Budget {stato}: restano {resto} kg di CO2 (circa {km_auto_rimasti} km in auto).",
                        {"budget_kg": budget_settimanale_kg, "consumato_kg": consumato_kg, "residuo_kg": resto,
                         "stato": stato, "km_auto_equivalenti": km_auto_rimasti, "consigli": consigli}, [FONTE])

    def extract_args(self, skill_id: str, text: str) -> dict:
        args: dict = {}
        if skill_id == "impronta_spostamento":
            km = re.search(r"(\d+(?:[.,]\d+)?)\s*km", text, re.I)
            if km:
                args["km"] = float(km.group(1).replace(",", "."))
            m = self._mezzo(text)
            if m:
                args["mezzo"] = m
        elif skill_id == "confronta_mezzi":
            m = re.search(r"\bda\s+(.+?)\s+(?:a|al|alla|all'|fino a)\s+(.+?)[?.!]*$", text, re.I)
            if m:
                args = {"origine": m.group(1), "destinazione": m.group(2)}
        elif skill_id == "budget_carbonio":
            nums = [float(x.replace(",", ".")) for x in re.findall(r"(\d+(?:[.,]\d+)?)\s*kg", text, re.I)]
            if len(nums) >= 2:
                args = {"budget_settimanale_kg": nums[0], "consumato_kg": nums[1]}
        return args


if __name__ == "__main__":
    run(EnvironmentAgent, 8004)
