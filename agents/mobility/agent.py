"""Agente Mobilità: percorsi in metropolitana con vincoli di accessibilità ed eventi della città.

Avvio:  python -m agents.mobility.agent --port 8002 --registry http://localhost:8000

"La città evolve": con la skill segnala_evento si simulano ascensori guasti e scioperi;
le richieste successive di percorso ne tengono conto (ripianificazione).
"""
from __future__ import annotations

import heapq
import json
import re
import unicodedata
from pathlib import Path

from a2a_lite import TaskContext

from ..base import BaseAgent, run, skill
from ..envelope import Esito, Fonte, Status

DATA = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
FONTE = Fonte("Rete metropolitana ATM (topologia semplificata, stato simulato)", "https://www.atm.it", simulato=True)


def _n(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("'", " ").strip()


class MobilityAgent(BaseAgent):
    name = "Agente Mobilità"
    agent_id = "mach:mobility"
    domain = "mobility"
    description = ("Calcola percorsi con la metropolitana di Milano tenendo conto di accessibilità "
                   "(ascensori), scioperi ed eventi; stima tempi ed emissioni evitate. Dati simulati.")
    domande = {"origine": "Da dove parti (fermata o luogo)?", "destinazione": "Dove devi arrivare?",
               "tipo": "Che tipo di evento? (ascensore_guasto, sciopero, ripristino)"}

    def setup(self) -> None:
        self.linee: dict[str, list[str]] = DATA["linee"]
        self.stazioni = sorted({s for fermate in self.linee.values() for s in fermate})
        self.ascensori_guasti: set[str] = set()
        self.linee_ferme: set[str] = set()

    # ------------------------------------------------------------------ utilità
    def risolvi(self, luogo: str) -> str | None:
        q = _n(luogo)
        for k, v in DATA["luoghi"].items():
            if _n(k) == q or _n(k) in q:
                return v
        for s in self.stazioni:
            if _n(s) == q or _n(s) in q or q in _n(s):
                return s
        return None

    def _vicine(self, stazione: str) -> list[tuple[str, str]]:
        out = []
        for linea, fermate in self.linee.items():
            if stazione in fermate and linea not in self.linee_ferme:
                i = fermate.index(stazione)
                out += [(linea, fermate[j]) for j in (i - 1, i + 1) if 0 <= j < len(fermate)]
        return out

    def _accessibile(self, stazione: str) -> bool:
        return stazione not in self.ascensori_guasti

    def _dijkstra(self, da: str, a: str, senza_barriere: bool) -> list[tuple[str, str]] | None:
        """Stati = (stazione, linea). Costo = fermate + penalità per i cambi.
        Senza barriere: non si può cambiare linea in una stazione con ascensore guasto."""
        cambio = DATA["minuti_per_cambio"] / DATA["minuti_per_fermata"]
        start = [(0.0, da, linea, [(da, linea)]) for linea, f in self.linee.items()
                 if da in f and linea not in self.linee_ferme]
        heapq.heapify(start)
        seen: set[tuple[str, str]] = set()
        while start:
            cost, staz, linea, path = heapq.heappop(start)
            if staz == a:
                return path
            if (staz, linea) in seen:
                continue
            seen.add((staz, linea))
            for l2, nxt in self._vicine(staz):
                if l2 != linea and senza_barriere and not self._accessibile(staz):
                    continue
                extra = 1 + (cambio if l2 != linea else 0)
                heapq.heappush(start, (cost + extra, nxt, l2, path + [(nxt, l2)]))
        return None

    def _tratte(self, path: list[tuple[str, str]]) -> list[dict]:
        tratte: list[dict] = []
        for (s1, _), (s2, l2) in zip(path, path[1:]):
            if tratte and tratte[-1]["linea"] == l2:
                tratte[-1]["a"] = s2
                tratte[-1]["fermate"] += 1
            else:
                tratte.append({"linea": l2, "da": s1, "a": s2, "fermate": 1})
        return tratte

    # ------------------------------------------------------------------ skill
    @skill("percorso", "Percorso con i mezzi",
           "Calcola un percorso in metropolitana tra due luoghi; con senza_barriere evita stazioni con "
           "ascensore guasto. Restituisce tratte, cambi, tempo stimato, emissioni evitate.",
           tags=["mobilita", "trasporti", "percorso", "atm", "accessibilita"],
           examples=["Come arrivo da Loreto a Niguarda?", "Percorso senza barriere da Duomo all'ospedale Niguarda"])
    async def percorso(self, ctx: TaskContext, origine: str, destinazione: str,
                       senza_barriere: bool = False, orario: str | None = None) -> Esito:
        da, a = self.risolvi(origine), self.risolvi(destinazione)
        if not da or not a:
            ignoti = [x for x, r in ((origine, da), (destinazione, a)) if not r]
            return Esito.errore(f"Non riconosco: {', '.join(ignoti)}.", {"stazioni_note": self.stazioni})
        avvisi, arrivo, ultimo_tratto = [], a, None
        if senza_barriere and not self._accessibile(da):
            avvisi.append(f"Ascensore fuori servizio a {da}: parti da una stazione vicina.")
        if senza_barriere and not self._accessibile(a):
            alternative = [s for _, s in self._vicine(a) if self._accessibile(s)]
            if not alternative:
                return Esito(Status.PARTIAL, f"Nessun arrivo accessibile vicino a {a}.", mancanti=["arrivo_accessibile"],
                             fonti=[FONTE])
            arrivo = alternative[0]
            ultimo_tratto = f"Da {arrivo} a {a}: tratto in superficie (bus o taxi accessibile) – da verificare"
            avvisi.append(f"Ascensore fuori servizio a {a}: scendi a {arrivo}.")
        if da == arrivo:
            return Esito.ok(f"La destinazione è già alla fermata {da}: non serve la metropolitana.",
                            {"origine": da, "destinazione": a, "arrivo_metro": arrivo, "tratte": [], "cambi": 0,
                             "minuti_stimati": 0, "ultimo_tratto": ultimo_tratto, "avvisi": avvisi,
                             "senza_barriere": senza_barriere, "co2_evitata_g_stima": 0}, [FONTE])
        path = self._dijkstra(da, arrivo, senza_barriere)
        if path is None:
            return Esito(Status.PARTIAL, "Nessun percorso disponibile con la rete attuale.",
                         {"linee_ferme": sorted(self.linee_ferme)}, [FONTE], mancanti=["percorso"])
        tratte = self._tratte(path)
        fermate = sum(t["fermate"] for t in tratte)
        cambi = len(tratte) - 1
        minuti = fermate * DATA["minuti_per_fermata"] + cambi * DATA["minuti_per_cambio"] + (10 if ultimo_tratto else 0)
        km = fermate * DATA["km_per_fermata"]
        risultato = {
            "origine": da, "destinazione": a, "arrivo_metro": arrivo, "tratte": tratte, "cambi": cambi,
            "minuti_stimati": minuti, "ultimo_tratto": ultimo_tratto, "senza_barriere": senza_barriere,
            "avvisi": avvisi,
            "co2_evitata_g_stima": round(km * 120),  # stima rispetto all'auto (~120 g/km), da affinare
        }
        testo = " → ".join(f"{t['linea']} da {t['da']} a {t['a']}" for t in tratte)
        if ultimo_tratto:
            testo += f"; poi {ultimo_tratto}"
        testo += f". Circa {minuti} minuti."
        if avvisi:
            testo = " ".join(avvisi) + " " + testo
        return Esito(Status.PARTIAL if ultimo_tratto else Status.SUCCESS, testo, risultato, [FONTE],
                     mancanti=["verifica_ultimo_tratto"] if ultimo_tratto else [])

    @skill("segnala_evento", "Segnala evento in città",
           "Registra un evento che cambia la rete: ascensore_guasto (stazione), sciopero (linea), ripristino.",
           tags=["mobilita", "eventi", "sciopero", "guasto"],
           examples=["Ascensore guasto a Ca' Granda", "Sciopero sulla M2"],
           writes=True, requires_consent=False, public=False)  # simula la città: solo uso interno
    async def segnala_evento(self, ctx: TaskContext, tipo: str, stazione: str | None = None,
                             linea: str | None = None) -> Esito:
        tipo = tipo.lower()
        staz = self.risolvi(stazione) if stazione else None
        if tipo == "ascensore_guasto" and staz:
            self.ascensori_guasti.add(staz)
        elif tipo == "sciopero" and linea:
            self.linee_ferme.add(linea.upper())
        elif tipo == "ripristino":
            if staz:
                self.ascensori_guasti.discard(staz)
            if linea:
                self.linee_ferme.discard(linea.upper())
        else:
            return Esito.errore("Evento non valido: usa ascensore_guasto+stazione, sciopero+linea, ripristino.")
        return Esito.ok(f"Evento registrato: {tipo} {staz or linea or ''}".strip(), self._stato())

    @skill("stato_rete", "Stato della rete", "Elenca ascensori guasti e linee ferme in questo momento.",
           tags=["mobilita", "eventi"], examples=["Ci sono guasti o scioperi?"])
    async def stato_rete(self, ctx: TaskContext) -> Esito:
        return Esito.ok("Stato attuale della rete.", self._stato(), [FONTE])

    def _stato(self) -> dict:
        return {"ascensori_guasti": sorted(self.ascensori_guasti), "linee_ferme": sorted(self.linee_ferme)}

    def extract_args(self, skill_id: str, text: str) -> dict:
        if skill_id == "percorso":
            m = re.search(r"\bda\s+(.+?)\s+(?:a|al|alla|all'|fino a)\s+(.+?)[?.!]*$", text, re.I)
            args = {"origine": m.group(1), "destinazione": m.group(2)} if m else {}
            if re.search(r"senza barriere|carrozzina|disabil|ascensor", text, re.I):
                args["senza_barriere"] = True
            return args
        return {}


if __name__ == "__main__":
    run(MobilityAgent, 8002)
