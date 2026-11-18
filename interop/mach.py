"""Profilo MACH: le nostre convenzioni sopra A2A 1.0 (vedi docs/PROTOCOLLO_MACH.md).

"MACH protocol" non è un protocollo nuovo: è A2A 1.0 + queste regole.

1. Richieste strutturate: DataPart {"skill": "<id>", "args": {...}} (testo libero sempre accettato).
2. Contesto di tracciamento in message.metadata["mach"]:
       {"trace_id", "hops", "caller", "on_behalf_of", "deadline", "consent": [...]}
   - chi inoltra una richiesta a un altro agente incrementa "hops" (next_hop);
   - oltre MAX_HOPS o dopo "deadline" la richiesta viene rifiutata (niente loop tra team).
3. Consenso per le skill con effetti reali: un elemento di "consent" con la skill
   (e, se c'è, la chiave di idempotenza dell'azione). Senza: INPUT_REQUIRED.
4. Il gateway aggiunge metadata["mach_gateway"] = {team, scopes, ts, sig}: chi lo
   riceve sa che team chiama e con quali permessi. Il client NON può impostarlo
   (il gateway lo sovrascrive) e la firma HMAC impedisce di falsificarlo.
5. Output: Busta 1.1 (contracts/) come DataPart dell'artifact.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PROFILE_VERSION = "0.1"
# Sostituire con l'URL pubblico di docs/PROTOCOLLO_MACH.md quando il repo sarà pubblicato
EXTENSION_URI = "urn:mach-hackathon:a2a-profile:v0.1"
MAX_HOPS = 4
META_KEY = "mach"
GATEWAY_KEY = "mach_gateway"


class MachError(Exception):
    code = "MACH_ERROR"


class HopLimitExceeded(MachError):
    code = "HOP_LIMIT_EXCEEDED"


class DeadlineExpired(MachError):
    code = "DEADLINE_EXPIRED"


class InvalidGatewayClaims(MachError):
    code = "INVALID_GATEWAY_CLAIMS"


def new_trace_id() -> str:
    return "tr-" + uuid.uuid4().hex[:8]


@dataclass
class Consent:
    """Prova di consenso allegata a una richiesta con effetti reali."""
    skill: str
    granted_by: str               # "user" (la persona dietro il chiamante) | "mandate" (accordo tra sistemi)
    reference: str                # id della decisione presso chi ha raccolto il consenso
    idempotency_key: str | None = None
    granted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None

    def valid_for(self, skill: str, idempotency_key: str | None) -> bool:
        if self.skill != skill or self.granted_by not in ("user", "mandate") or not self.reference:
            return False
        if self.idempotency_key and idempotency_key and self.idempotency_key != idempotency_key:
            return False
        if self.expires_at:
            try:
                if datetime.fromisoformat(self.expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    return False
            except ValueError:
                return False
        return True


@dataclass
class MachContext:
    trace_id: str = field(default_factory=new_trace_id)
    hops: int = 0
    caller: str | None = None
    on_behalf_of: str | None = None   # pseudonimo dell'utente finale, mai dati personali
    deadline: str | None = None       # ISO 8601: oltre, non iniziare nuovo lavoro
    consent: list[Consent] = field(default_factory=list)

    # ------------------------------------------------------------------ (de)serializzazione
    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None, *, default_trace: str | None = None) -> "MachContext":
        raw = (metadata or {}).get(META_KEY) or {}
        consents = []
        for c in raw.get("consent") or []:
            try:
                consents.append(Consent(**{k: v for k, v in c.items() if k in Consent.__dataclass_fields__}))
            except TypeError:
                continue
        return cls(trace_id=raw.get("trace_id") or default_trace or new_trace_id(),
                   hops=int(raw.get("hops") or 0), caller=raw.get("caller"),
                   on_behalf_of=raw.get("on_behalf_of"), deadline=raw.get("deadline"), consent=consents)

    def to_metadata(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v not in (None, [], "")}
        d["hops"] = self.hops
        return {META_KEY: d}

    # ------------------------------------------------------------------ regole
    def next_hop(self, caller: str, *, keep_consent: bool = False) -> "MachContext":
        """Contesto da allegare quando si inoltra la richiesta a un altro agente.
        Il consenso NON si propaga per default: vale per chi l'ha ricevuto."""
        return MachContext(trace_id=self.trace_id, hops=self.hops + 1, caller=caller,
                           on_behalf_of=self.on_behalf_of, deadline=self.deadline,
                           consent=list(self.consent) if keep_consent else [])

    def check(self, max_hops: int = MAX_HOPS) -> None:
        if self.hops > max_hops:
            raise HopLimitExceeded(f"Troppi passaggi tra agenti ({self.hops} > {max_hops}) per {self.trace_id}")
        if self.deadline:
            try:
                dl = datetime.fromisoformat(self.deadline.replace("Z", "+00:00"))
            except ValueError:
                return
            if dl < datetime.now(timezone.utc):
                raise DeadlineExpired(f"Scadenza superata ({self.deadline}) per {self.trace_id}")

    def consent_for(self, skill: str, idempotency_key: str | None = None) -> Consent | None:
        return next((c for c in self.consent if c.valid_for(skill, idempotency_key)), None)


# ---------------------------------------------------------------------------- claims del gateway

def _canonical(claims: dict[str, Any]) -> bytes:
    body = {k: v for k, v in claims.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_claims(claims: dict[str, Any], secret: str) -> dict[str, Any]:
    claims = {**claims, "ts": int(claims.get("ts") or time.time())}
    claims["sig"] = hmac.new(secret.encode(), _canonical(claims), hashlib.sha256).hexdigest()
    return claims


def verify_claims(claims: dict[str, Any], secret: str | None, max_age_s: int = 300) -> dict[str, Any]:
    """Restituisce i claims se validi. Senza segreto configurato li accetta (solo sviluppo locale)."""
    if not secret:
        return claims
    sig = claims.get("sig", "")
    expected = hmac.new(secret.encode(), _canonical(claims), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise InvalidGatewayClaims("Firma del gateway non valida")
    if time.time() - int(claims.get("ts", 0)) > max_age_s:
        raise InvalidGatewayClaims("Claims del gateway scaduti")
    return claims


def scope_allows(scopes: list[str], agent_id: str, skill_id: str, writes: bool) -> bool:
    """Scope nella forma "<agent_id>:<skill>" oppure "<agent_id>:*" oppure "*".
    I caratteri jolly coprono solo le skill di lettura: per scrivere serve lo scope esplicito."""
    if f"{agent_id}:{skill_id}" in scopes:
        return True
    if writes:
        return False
    return "*" in scopes or f"{agent_id}:*" in scopes
