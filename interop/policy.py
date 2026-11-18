"""Gate di condivisione dati (rilievo H): cosa può uscire verso quale team, PRIMA della chiamata.

Principi
  * minimizzazione: escono solo i campi elencati per quel team (o per "*");
  * alcuni campi non escono mai (identificativi personali), qualunque regola ci sia;
  * i campi sensibili (es. profile_tags) escono solo con consenso dell'utente;
  * ogni condivisione viene registrata (chi, verso chi, quali campi, quali scartati).

Le regole stanno in un dizionario (o in un JSON) così si cambiano in gara senza toccare codice.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Mai fuori dal nostro sistema, nemmeno con consenso: per questi dati servono canali ufficiali.
NEVER_SHARE = {"codice_fiscale", "nome", "cognome", "telefono", "email", "indirizzo", "data_nascita",
               "nre", "tessera_sanitaria", "diagnosi", "documento"}

DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "*": {
        "allowed": ["zona", "area", "quartiere", "orario", "data", "lingua", "prestazione", "service_code",
                    "origine", "destinazione", "destination_ref", "skill", "args", "testo", "query",
                    "profile_tags", "senza_barriere"],
        "sensitive": ["profile_tags", "senza_barriere"],  # escono solo con consenso
        "allow_text": True,                              # testo libero ammesso (va scritto senza dati personali)
    },
}


@dataclass
class SharingDecision:
    allowed: bool
    data: dict[str, Any]
    text: str | None
    dropped: list[str] = field(default_factory=list)
    needs_consent: list[str] = field(default_factory=list)
    reason: str = ""


class SharingPolicy:
    def __init__(self, rules: dict[str, dict[str, Any]] | None = None,
                 audit: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.rules = rules or DEFAULT_RULES
        self.audit = audit
        self.log: list[dict[str, Any]] = []

    def _rule(self, team: str) -> dict[str, Any]:
        return self.rules.get(team) or self.rules.get("*") or {"allowed": [], "sensitive": [], "allow_text": False}

    def check(self, team: str, data: dict[str, Any] | None, text: str | None = None, *,
              user_consent: bool = False) -> SharingDecision:
        rule = self._rule(team)
        allowed, sensitive = set(rule.get("allowed", [])), set(rule.get("sensitive", []))
        out: dict[str, Any] = {}
        dropped: list[str] = []
        needs_consent: list[str] = []

        def filt(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
            res: dict[str, Any] = {}
            for k, v in d.items():
                name = f"{prefix}{k}"
                if k in NEVER_SHARE or k not in allowed:
                    dropped.append(name)
                elif k in sensitive and not user_consent:
                    needs_consent.append(name)
                elif isinstance(v, dict):
                    res[k] = filt(v, f"{name}.")
                else:
                    res[k] = v
            return res

        out = filt(data or {})
        if text and not rule.get("allow_text", False):
            dropped.append("testo_libero")
            text = None
        decision = SharingDecision(allowed=not needs_consent, data=out, text=text, dropped=dropped,
                                   needs_consent=needs_consent,
                                   reason="serve il consenso per: " + ", ".join(needs_consent) if needs_consent else "")
        entry = {"ts": time.time(), "team": team, "shared": sorted(out), "dropped": dropped,
                 "needs_consent": needs_consent, "text": bool(text)}
        self.log.append(entry)
        if self.audit:
            self.audit(entry)
        return decision
