"""Agente Accessibilità: servizio orizzontale che anche gli agenti degli altri team possono chiamare.

Avvio:  python -m agents.accessibility.agent --port 8003 --registry http://localhost:8000

Skill:
  accessibilita_sede  – barriere e servizi di una struttura (ingresso, ascensore, mediazione linguistica)
  profilo_bisogni     – profilo delle esigenze del cittadino, salvato SOLO con consenso esplicito
  semplifica          – riscrive un testo in linguaggio semplice e/o in un'altra lingua (usa l'LLM)
"""
from __future__ import annotations

import json
from pathlib import Path

from a2a_lite import TaskContext

from ..base import BaseAgent, _norm_tokens, run, skill
from ..envelope import Esito, Fonte, Status

DATA = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
FONTE = Fonte("Schede accessibilità del prototipo MACH", simulato=True)
CAMPI_PROFILO = ("lingua", "mobilita_ridotta", "ipovisione", "linguaggio_semplice")


class AccessibilityAgent(BaseAgent):
    name = "Agente Accessibilità"
    agent_id = "mach:accessibility"
    domain = "accessibility"
    description = ("Verifica l'accessibilità delle sedi, gestisce con consenso il profilo delle esigenze "
                   "del cittadino e riscrive testi in linguaggio semplice o in altre lingue.")
    domande = {"struttura_id": "Di quale struttura vuoi sapere l'accessibilità?",
               "cittadino_id": "Manca l'identificativo (pseudonimo) del cittadino.",
               "testo": "Quale testo devo semplificare?"}

    def setup(self) -> None:
        self.sedi = DATA["sedi"]
        self.profili: dict[str, dict] = {}

    @skill("accessibilita_sede", "Accessibilità di una sede",
           "Dice se una struttura ha ingresso senza gradini, ascensore, parcheggio disabili, mediazione linguistica.",
           tags=["accessibilita", "barriere", "sede", "disabilita"],
           examples=["L'ospedale Niguarda è accessibile in carrozzina?"])
    async def accessibilita_sede(self, ctx: TaskContext, struttura_id: str) -> Esito:
        sede_id = struttura_id if struttura_id in self.sedi else next(
            (k for k, v in self.sedi.items() if _norm_tokens(struttura_id) & _norm_tokens(v["nome"] + " " + k)), None)
        if not sede_id:
            return Esito(Status.PARTIAL, f"Non ho informazioni di accessibilità per «{struttura_id}».",
                         mancanti=["scheda_accessibilita"])
        s = self.sedi[sede_id]
        barriere = [k for k in ("ingresso_senza_gradini", "ascensore", "parcheggio_disabili") if not s[k]]
        testo = f"{s['nome']}: " + ("nessuna barriera segnalata" if not barriere else
                                    "attenzione a " + ", ".join(b.replace("_", " ") for b in barriere))
        return Esito.ok(testo, {"struttura_id": sede_id, **s, "barriere": barriere}, [FONTE])

    @skill("profilo_bisogni", "Profilo delle esigenze",
           "Legge o aggiorna le esigenze del cittadino (lingua, mobilità ridotta, ipovisione, linguaggio "
           "semplice). L'aggiornamento richiede consenso=true.",
           tags=["accessibilita", "profilo", "consenso"])
    async def profilo_bisogni(self, ctx: TaskContext, cittadino_id: str, consenso: bool | None = None,
                              lingua: str | None = None, mobilita_ridotta: bool | None = None,
                              ipovisione: bool | None = None, linguaggio_semplice: bool | None = None) -> Esito:
        nuovi = {k: v for k, v in dict(lingua=lingua, mobilita_ridotta=mobilita_ridotta, ipovisione=ipovisione,
                                       linguaggio_semplice=linguaggio_semplice).items() if v is not None}
        if nuovi and consenso is not True:
            return Esito.chiedi("Posso salvare queste esigenze per adattare il servizio? Rispondi sì o no.",
                                ["consenso"])
        if nuovi:
            self.profili.setdefault(cittadino_id, {}).update(nuovi)
        profilo = self.profili.get(cittadino_id, {})
        return Esito.ok("Profilo aggiornato." if nuovi else "Profilo attuale.",
                        {"cittadino_id": cittadino_id, "profilo": profilo})

    @skill("semplifica", "Linguaggio semplice e traduzione",
           "Riscrive un testo in linguaggio semplice, nella lingua indicata (codice ISO, es. it, en, ar).",
           tags=["accessibilita", "lingua", "traduzione", "linguaggio semplice"],
           examples=["Semplifica queste istruzioni", "Traduci in inglese semplice"])
    async def semplifica(self, ctx: TaskContext, testo: str, lingua: str = "it") -> Esito:
        if self.llm is None:
            return Esito(Status.PARTIAL, testo, {"testo": testo, "lingua": "it", "nota": "LLM non configurato"},
                         mancanti=["semplificazione"])
        msg = await self.llm.chat([
            {"role": "system", "content": "Riscrivi il testo in linguaggio semplice: frasi brevi, parole comuni, "
                                          "un'azione per frase, nessuna informazione aggiunta o tolta. "
                                          f"Lingua di uscita: {lingua}. Rispondi solo con il testo riscritto."},
            {"role": "user", "content": testo}])
        out = (msg.get("content") or "").strip()
        return Esito.ok(out, {"testo": out, "lingua": lingua, "originale": testo},
                        [Fonte("Riscrittura automatica (LLM): verificare")])

    def extract_args(self, skill_id: str, text: str) -> dict:
        if skill_id == "accessibilita_sede":
            for k, v in self.sedi.items():
                if _norm_tokens(text) & (_norm_tokens(v["nome"]) - {"dati", "simulati", "simulata"}):
                    return {"struttura_id": k}
        return {}


if __name__ == "__main__":
    run(AccessibilityAgent, 8003)
