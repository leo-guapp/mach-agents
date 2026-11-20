"""Record firmati del whitepaper: AgentAddr (Ed25519) e AgentFacts come Verifiable Credential.

Dal whitepaper (§3.1): il NANDA Index pubblica per ogni agente un record AgentAddr compatto
(< 120 byte) firmato Ed25519 dal registry, con id, nome, URL dell'AgentFacts e TTL; gli
AgentFacts sono documenti JSON-LD firmati come W3C Verifiable Credentials v2.

Qui:
  * AgentAddr: JSON compatto con chiavi corte {id, n, f, t, iat} + firma Ed25519 (base64url);
  * AgentFacts VC: credenziale VC v2 con prova DataIntegrityProof / eddsa-jcs-2022
    (canonicalizzazione JCS, hash SHA-256 di prova e documento, firma Ed25519, multibase);
  * did:web: documento DID con la chiave pubblica (Multikey), servito dal gateway.

Nota onesta: è un'implementazione fatta per il prototipo e testata contro sé stessa.
Prima di dichiarare interoperabilità con altri verificatori VC, provatela con una libreria
VC esterna; la JCS qui copre bene stringhe, interi, booleani, liste e oggetti.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_MULTICODEC = b"\xed\x01"


# ---------------------------------------------------------------------------- utilità
def jcs(obj: Any) -> bytes:
    """JSON Canonicalization Scheme (RFC 8785) per i tipi che usiamo."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------------------- chiavi
class SigningKey:
    def __init__(self, private: Ed25519PrivateKey, did: str) -> None:
        self.private = private
        self.public: Ed25519PublicKey = private.public_key()
        self.did = did

    @classmethod
    def load_or_create(cls, path: str | Path | None, did: str) -> "SigningKey":
        """Chiave da file PEM (creata se manca). Senza path: chiave effimera (solo test)."""
        if path is None:
            return cls(Ed25519PrivateKey.generate(), did)
        p = Path(path)
        if p.exists():
            key = serialization.load_pem_private_key(p.read_bytes(), password=None)
            assert isinstance(key, Ed25519PrivateKey)
            return cls(key, did)
        key = Ed25519PrivateKey.generate()
        p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
        os.chmod(p, 0o600)
        return cls(key, did)

    @property
    def raw_public(self) -> bytes:
        return self.public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def public_multibase(self) -> str:
        return "z" + b58encode(_ED25519_MULTICODEC + self.raw_public)

    @property
    def verification_method(self) -> str:
        return f"{self.did}#key-1"

    def sign(self, data: bytes) -> bytes:
        return self.private.sign(data)

    def did_document(self) -> dict[str, Any]:
        vm = {"id": self.verification_method, "type": "Multikey", "controller": self.did,
              "publicKeyMultibase": self.public_multibase}
        return {"@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"],
                "id": self.did, "verificationMethod": [vm],
                "assertionMethod": [self.verification_method], "authentication": [self.verification_method]}


def public_key_from_multibase(mb: str) -> Ed25519PublicKey:
    raw = b58decode(mb[1:])
    if not raw.startswith(_ED25519_MULTICODEC):
        raise ValueError("chiave non Ed25519")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


def did_web(host: str) -> str:
    """did:web per un host (le ':' della porta vanno codificate come %3A)."""
    return "did:web:" + host.replace(":", "%3A")


# ---------------------------------------------------------------------------- AgentAddr
def make_agentaddr(key: SigningKey, agent_id: str, name: str, facts_url: str, ttl_s: int = 3600) -> dict[str, Any]:
    record = {"id": agent_id, "n": name[:40], "f": facts_url, "t": ttl_s, "iat": int(time.time())}
    body = jcs(record)
    return {"record": record, "size_bytes": len(body), "alg": "Ed25519", "kid": key.verification_method,
            "sig": _b64u(key.sign(body))}


def verify_agentaddr(signed: dict[str, Any], public: Ed25519PublicKey, now: float | None = None) -> dict[str, Any]:
    """Verifica firma e scadenza (iat + TTL). Restituisce il record o solleva ValueError."""
    record = signed["record"]
    try:
        public.verify(_b64u_dec(signed["sig"]), jcs(record))
    except InvalidSignature as exc:
        raise ValueError("firma AgentAddr non valida") from exc
    if (now or time.time()) > record["iat"] + record["t"]:
        raise ValueError("AgentAddr scaduto: va risolto di nuovo")
    return record


# ---------------------------------------------------------------------------- AgentFacts VC
VC_CONTEXT = ["https://www.w3.org/ns/credentials/v2",
              {"@vocab": "https://github.com/projnanda/agentfacts-format#"}]


def make_agentfacts_vc(key: SigningKey, facts: dict[str, Any], *, valid_days: int = 7) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    doc = {"@context": VC_CONTEXT, "type": ["VerifiableCredential", "AgentFacts"], "issuer": key.did,
           "validFrom": now.isoformat().replace("+00:00", "Z"),
           "validUntil": (now + timedelta(days=valid_days)).isoformat().replace("+00:00", "Z"),
           "credentialSubject": facts}
    proof_cfg = {"@context": VC_CONTEXT, "type": "DataIntegrityProof", "cryptosuite": "eddsa-jcs-2022",
                 "created": doc["validFrom"], "verificationMethod": key.verification_method,
                 "proofPurpose": "assertionMethod"}
    hash_data = hashlib.sha256(jcs(proof_cfg)).digest() + hashlib.sha256(jcs(doc)).digest()
    proof = {k: v for k, v in proof_cfg.items() if k != "@context"}
    proof["proofValue"] = "z" + b58encode(key.sign(hash_data))
    return {**doc, "proof": proof}


def verify_agentfacts_vc(vc: dict[str, Any], public: Ed25519PublicKey) -> dict[str, Any]:
    """Verifica la prova eddsa-jcs-2022 e la validità temporale; restituisce i facts."""
    proof = dict(vc.get("proof") or {})
    if proof.get("cryptosuite") != "eddsa-jcs-2022":
        raise ValueError("cryptosuite non supportata")
    doc = {k: v for k, v in vc.items() if k != "proof"}
    proof_value = proof.pop("proofValue", "")
    proof_cfg = {"@context": doc["@context"], **proof}
    hash_data = hashlib.sha256(jcs(proof_cfg)).digest() + hashlib.sha256(jcs(doc)).digest()
    try:
        public.verify(b58decode(proof_value[1:]), hash_data)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("firma AgentFacts non valida") from exc
    until = doc.get("validUntil")
    if until and datetime.fromisoformat(until.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise ValueError("AgentFacts scaduti")
    return doc["credentialSubject"]
