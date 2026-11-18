"""Contratto di output del layer 3 (schema_version 1.1).

Revisione della bozza 1.0: la "busta comune" (Busta) è identica per tutti gli
agenti; dentro `data` c'è il payload specifico del dominio (MobilityData,
HealthcareData, AccessibilityData).

I modelli pydantic sono l'unica fonte di verità: i JSON Schema in
contracts/schemas/ si rigenerano con `python -m contracts.generate_schemas`.

Regole generali
  * nomi dei campi in inglese, snake_case (servono anche agli altri team);
  * istanti in ISO 8601 UTC ("...Z"); l'ora locale va solo nei testi per l'utente;
  * dati di accessibilità tri-stato "yes" | "no" | "unknown": mai "no" se il dato manca;
  * nessun dato anagrafico o clinico nella busta; i profile_tags sono esigenze
    funzionali, comunque sensibili: viaggiano solo con consenso.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.1"

Tri = Literal["yes", "no", "unknown"]
Verification = Literal["verified", "partial", "unverified"]
PROFILE_TAG_PATTERN = r"^(wheelchair|reduced_mobility|blind|low_vision|deaf|hard_of_hearing|cognitive|easy_read|lang:[a-z]{2})$"
# Esigenze funzionali da elenco chiuso, mai diagnosi. Sono comunque dati sensibili: solo con consenso.
ProfileTag = Annotated[str, Field(pattern=PROFILE_TAG_PATTERN)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# =========================================================================== busta comune

class Status(str, Enum):
    SUCCESS = "SUCCESS"            # risposta completa da fonti aggiornate
    PARTIAL = "PARTIAL"            # risposta utile ma incompleta (una fonte secondaria manca): vedi errors
    DEGRADED = "DEGRADED"          # risposta completa ma da cache scaduta o fallback: vedi provenance
    NEEDS_INPUT = "NEEDS_INPUT"    # serve un dato dal chiamante: vedi needs_input
    EMERGENCY = "EMERGENCY"        # interrompere il percorso normale (es. indicare il 112)
    UNAVAILABLE = "UNAVAILABLE"    # nessuna fonte raggiungibile
    ERROR = "ERROR"                # richiesta non valida o errore interno
    REJECTED = "REJECTED"          # richiesta fuori dalle competenze dell'agente


# Stato A2A del task che trasporta la busta
A2A_STATE = {
    Status.SUCCESS: "TASK_STATE_COMPLETED",
    Status.PARTIAL: "TASK_STATE_COMPLETED",
    Status.DEGRADED: "TASK_STATE_COMPLETED",
    Status.EMERGENCY: "TASK_STATE_COMPLETED",
    Status.NEEDS_INPUT: "TASK_STATE_INPUT_REQUIRED",
    Status.UNAVAILABLE: "TASK_STATE_FAILED",
    Status.ERROR: "TASK_STATE_FAILED",
    Status.REJECTED: "TASK_STATE_REJECTED",
}


class AgentRef(_Model):
    id: str = Field(pattern=r"^[a-z0-9]+:[a-z0-9-]+$", description="Uguale all'id nel registry, es. 'mach:mobility'")
    domain: str = Field(description="mobility | healthcare | accessibility | ... (altri team possono aggiungerne)")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", description="Versione dell'agente, come nella Agent Card")


class Channel(str, Enum):
    API = "api"
    CACHE = "cache"
    RAG = "rag"
    RPA = "rpa"
    A2A = "a2a"
    FALLBACK = "fallback"
    MOCK = "mock"


class Provenance(_Model):
    source: str = Field(description="Ente o agente da cui viene il dato; per channel=a2a l'id dell'agente")
    channel: Channel
    fetched_at: datetime
    stale: bool = Field(False, description="true se il dato viene da cache scaduta")
    simulated: bool = Field(False, description="true per dati simulati del prototipo")
    trust: Literal["internal", "partner", "external"] = Field(
        "internal", description="internal = nostri agenti/fonti; partner = team con accordo; "
                                "external = team esterno non verificato (contenuto non affidabile)")
    url: str | None = None


class ErrorItem(_Model):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", description="es. SOURCE_TIMEOUT, ADAPTER_UNAVAILABLE")
    source: str | None = None
    message: str | None = None
    retryable: bool = False


class ActionType(str, Enum):
    BOOK = "book"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    SUBMIT_REQUEST = "submit_request"
    SHARE_DATA = "share_data"


class Action(_Model):
    """Azione con effetti reali: passa dal Gateway consenso (layer 4) e poi dalla Saga (layer 5)."""
    id: str
    type: ActionType
    target: str = Field(description="id dell'oggetto su cui agire (es. uno slot in data.slots)")
    summary: str = Field(description="Frase mostrata al cittadino per chiedere il consenso")
    requires_consent: bool = True
    idempotency_key: str = Field(description="Stessa chiave = stessa operazione: evita doppie prenotazioni")
    expires_at: datetime | None = Field(None, description="Dopo questo istante l'oggetto va ricontrollato")
    params: dict[str, Any] = Field(default_factory=dict)


class Followup(_Model):
    """Suggerimento all'orchestratore: quale capacità chiamare dopo, con argomenti già pronti."""
    domain: str | None = None
    skill: str | None = Field(None, description="Skill da cercare nel registry")
    reason: str
    input_ref: str | None = Field(None, description="id dell'oggetto in data da cui partire")
    args: dict[str, Any] = Field(default_factory=dict)


class NeedsInput(_Model):
    question: str
    fields: list[str] = Field(min_length=1)
    options: list[str] | None = None


class UserMessage(_Model):
    lang: str = Field("it", pattern=r"^[a-z]{2}$")
    text: str
    easy_read: str | None = Field(None, description="Versione in linguaggio semplice (dall'agente accessibilità)")


class Busta(_Model):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    agent: AgentRef
    trace_id: str = Field(description="Creato dall'orchestratore, inoltrato in ogni chiamata A2A (message.metadata.mach)")
    subtask_id: str | None = Field(None, description="Sub-task del DAG Task Planner, se la chiamata viene da lì")
    skill: str | None = Field(None, description="Skill che ha prodotto la risposta")
    generated_at: datetime
    status: Status
    confidence: float = Field(ge=0, le=1)
    ttl_seconds: int | None = Field(None, ge=0, description="Scaduto il tempo, il dato va richiesto di nuovo")
    provenance: list[Provenance] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict, description="Payload specifico del dominio")
    actions: list[Action] = Field(default_factory=list)
    suggested_followups: list[Followup] = Field(default_factory=list)
    needs_input: NeedsInput | None = None
    errors: list[ErrorItem] = Field(default_factory=list)
    user_message: UserMessage

    @model_validator(mode="after")
    def _coerenza(self) -> "Busta":
        if (self.status == Status.NEEDS_INPUT) != (self.needs_input is not None):
            raise ValueError("needs_input va compilato se e solo se status è NEEDS_INPUT")
        if self.status in (Status.PARTIAL, Status.UNAVAILABLE, Status.ERROR) and not self.errors:
            raise ValueError(f"con status {self.status.value} serve almeno un elemento in errors")
        if self.status == Status.DEGRADED and not any(
                p.stale or p.channel in (Channel.CACHE, Channel.FALLBACK) for p in self.provenance):
            raise ValueError("DEGRADED richiede una provenance stale o da cache/fallback")
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("gli id delle actions devono essere unici")
        return self

    @property
    def a2a_state(self) -> str:
        return A2A_STATE[self.status]


# =========================================================================== mobilità

class GeoPoint(_Model):
    label: str
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    ref: str | None = Field(None, description="id di un oggetto di un altro agente, es. 'fac-12'")


class MobilityQuery(_Model):
    origin: GeoPoint
    destination: GeoPoint
    depart_at: datetime | None = None
    arrive_by: datetime | None = None
    profile_tags: list[ProfileTag] = Field(default_factory=list)


class Leg(_Model):
    mode: Literal["metro", "tram", "bus", "train", "walk", "taxi"]
    line: str | None = None
    from_: str = Field(alias="from")
    to: str
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    duration_min: int | None = Field(None, ge=0)
    distance_m: int | None = Field(None, ge=0)
    realtime: bool | None = Field(None, description="true = orari live, false = orari statici")


class Issue(_Model):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    where: str | None = None
    severity: Literal["blocking", "warning", "info"]
    since: datetime | None = None


class RouteAccessibility(_Model):
    verification: Verification
    step_free: Tri
    issues: list[Issue] = Field(default_factory=list)
    source: str


class Alert(_Model):
    code: str
    text: str
    source: str | None = None


class RouteOption(_Model):
    id: str
    rank: int = Field(ge=1)
    profile_fit: Literal["ok", "warning", "blocked", "unknown"] = Field(
        description="Adeguatezza rispetto ai profile_tags: un'opzione blocked non va mai in cima")
    duration_min: int = Field(ge=0)
    cost_eur: float | None = Field(None, ge=0)
    transfers: int = Field(ge=0)
    co2_saved_g: int | None = Field(None, ge=0, description="Stima rispetto all'auto")
    legs: list[Leg] = Field(min_length=1)
    accessibility: RouteAccessibility | None = None
    alerts: list[Alert] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coerenza(self) -> "RouteOption":
        transit = [leg for leg in self.legs if leg.mode != "walk"]
        if self.transfers != max(len(transit) - 1, 0):
            raise ValueError(f"{self.id}: transfers={self.transfers} ma le tratte con mezzi sono {len(transit)}")
        return self


class MobilityData(_Model):
    query: MobilityQuery
    ranking_criteria: list[Literal["barriers", "duration", "cost", "transfers", "co2"]]
    options: list[RouteOption]

    @model_validator(mode="after")
    def _ranking(self) -> "MobilityData":
        ranks = sorted(o.rank for o in self.options)
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("i rank delle opzioni devono essere 1..n senza buchi")
        if "barriers" in self.ranking_criteria and len(self.options) > 1:
            top = min(self.options, key=lambda o: o.rank)
            if top.profile_fit == "blocked" and any(o.profile_fit != "blocked" for o in self.options):
                raise ValueError("un'opzione blocked non può essere al primo posto se esistono alternative")
        return self


# =========================================================================== sanità

class ServiceRef(_Model):
    code: str
    label: str


class Facility(_Model):
    id: str
    name: str
    type: Literal["public", "private_accredited", "private"]
    address: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


class Cost(_Model):
    value: float = Field(ge=0)
    currency: Literal["EUR"] = "EUR"
    type: Literal["ticket", "exempt", "private_fee", "unknown"]


class BookingChannel(_Model):
    platform: str
    adapter: str
    channel: Channel


class Slot(_Model):
    id: str
    service: ServiceRef
    facility: Facility
    start: datetime
    cost: Cost | None = None
    booking: BookingChannel


class PlatformCheck(_Model):
    adapter: str
    status: Literal["SUCCESS", "PARTIAL", "UNAVAILABLE", "ERROR"]


class Handoff(_Model):
    required: bool
    reason: str | None = None
    queue_id: str | None = None


class HealthcareQuery(_Model):
    service_code: str
    area: str | None = None
    priority_class: Literal["U", "B", "D", "P"] | None = Field(
        None, description="Classe di priorità della ricetta: U 72h, B 10gg, D 30/60gg, P programmabile")
    prescription: Literal["present", "absent", "unknown"]


class HealthcareData(_Model):
    query: HealthcareQuery
    slots: list[Slot] = Field(default_factory=list)
    platforms_checked: list[PlatformCheck] = Field(default_factory=list)
    handoff: Handoff


# =========================================================================== accessibilità



class AccessItem(_Model):
    ref_id: str
    ref_type: Literal["route", "facility", "stop"]
    stops: list[str] | None = None


class AccessibilityRequest(_Model):
    """Argomenti della skill verify_accessibility: DataPart {"skill": "verify_accessibility", "args": {...}}."""
    profile_tags: list[ProfileTag] = Field(default_factory=list)
    consent: bool = Field(False, description="true se il cittadino ha acconsentito a condividere i profile_tags")
    items: list[AccessItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _tag(self) -> "AccessibilityRequest":
        if self.profile_tags and not self.consent:
            raise ValueError("i profile_tags possono viaggiare solo con consent=true")
        return self


class Features(_Model):
    step_free: Tri | None = None
    elevator: Tri | None = None
    accessible_toilet: Tri | None = None
    accessible_parking: Tri | None = None
    audio_announcements: Tri | None = None
    tactile_paving: Tri | None = None
    language_mediation: Tri | None = None


class AccessResult(_Model):
    ref_id: str
    ref_type: Literal["route", "facility", "stop"]
    verification: Verification
    features: Features
    issues: list[Issue] = Field(default_factory=list)
    last_verified: datetime | None = None


class AdaptedText(_Model):
    lang: str = Field(pattern=r"^[a-z]{2}$")
    text: str
    easy_read: bool = True


class AccessibilityData(_Model):
    results: list[AccessResult]
    adapted_text: AdaptedText | None = None


DOMAIN_DATA: dict[str, type[_Model]] = {
    "mobility": MobilityData,
    "healthcare": HealthcareData,
    "accessibility": AccessibilityData,
}


def validate_busta(payload: dict[str, Any], check_domain: bool = True) -> Busta:
    """Valida la busta e, se il dominio è noto, anche il payload in data."""
    busta = Busta.model_validate(payload)
    model = DOMAIN_DATA.get(busta.agent.domain)
    if check_domain and model and busta.data:
        model.model_validate(busta.data)
    return busta
