"""Test del contratto di output (contracts/): esempi, schemi, regole di coerenza, ponte da Esito."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import A2A_STATE, AccessibilityRequest, AgentRef, Busta, MobilityData, Status, validate_busta
from contracts.adapter import busta_from_esito, compute_confidence
from contracts.generate_schemas import build
from contracts.models import AccessResult, Channel, Provenance
from agents.envelope import Esito, Fonte
from agents.envelope import Status as EsitoStatus
from agents.health.agent import HealthAgent
from agents.mobility.agent import MobilityAgent

ROOT = Path(__file__).resolve().parent.parent / "contracts"
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


def load(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ esempi e schemi

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    if "richiesta" in path.name:
        AccessibilityRequest.model_validate(d["parts"][0]["data"]["args"])
    else:
        validate_busta(d)


def test_schema_files_are_up_to_date():
    for name, schema in build().items():
        on_disk = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert on_disk == schema, f"{name}: rigenera con python -m contracts.generate_schemas"


def test_examples_valid_against_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schemas = build()
    for path in EXAMPLES:
        d = json.loads(path.read_text(encoding="utf-8"))
        if "richiesta" in path.name:
            jsonschema.validate(d["parts"][0]["data"]["args"], schemas["accessibility_request"])
            continue
        jsonschema.validate(d, schemas["busta"])
        domain = d["agent"]["domain"]
        if d["data"]:
            jsonschema.validate(d["data"], schemas[f"{domain}_data"])


def test_story_is_consistent():
    """Gli esempi raccontano la stessa pratica: gli id si richiamano tra un agente e l'altro."""
    hc, acc, mob = load("01_healthcare_risposta.json"), load("03_accessibility_risposta.json"), load("04_mobility_risposta.json")
    assert {hc["trace_id"], acc["trace_id"], mob["trace_id"]} == {"tr-7f3a"}
    fac = hc["data"]["slots"][0]["facility"]
    assert mob["data"]["query"]["destination"]["ref"] == fac["id"]
    acc_ids = {r["ref_id"] for r in acc["data"]["results"]}
    assert {o["id"] for o in mob["data"]["options"]} | {fac["id"]} == acc_ids
    assert hc["actions"][0]["target"] == hc["data"]["slots"][0]["id"]


# ------------------------------------------------------------------ errori della bozza 1.0 che ora vengono scartati

def test_needs_input_requires_block():
    d = load("05_healthcare_needs_input.json")
    del d["needs_input"]
    with pytest.raises(ValidationError, match="needs_input"):
        validate_busta(d)


def test_partial_requires_errors():
    d = load("04_mobility_risposta.json")
    d["errors"] = []
    with pytest.raises(ValidationError, match="errors"):
        validate_busta(d)


def test_features_are_tri_state():
    """Bozza 1.0: elevator = "out_of_service" violava la regola yes|no|unknown."""
    r = load("03_accessibility_risposta.json")["data"]["results"][0]
    r["features"]["elevator"] = "out_of_service"
    with pytest.raises(ValidationError):
        AccessResult.model_validate(r)


def test_transfers_must_match_legs():
    """Bozza 1.0: transfers=1 con una sola tratta in metro."""
    d = load("04_mobility_risposta.json")["data"]
    d["options"][1]["transfers"] = 1
    with pytest.raises(ValidationError, match="transfers"):
        MobilityData.model_validate(d)


def test_blocked_option_cannot_rank_first():
    d = load("04_mobility_risposta.json")["data"]
    d["options"][0]["rank"], d["options"][1]["rank"] = 2, 1
    with pytest.raises(ValidationError, match="blocked"):
        MobilityData.model_validate(d)


def test_profile_tags_need_consent_and_closed_list():
    args = copy.deepcopy(load("02_accessibility_richiesta.json")["parts"][0]["data"]["args"])
    args["consent"] = False
    with pytest.raises(ValidationError, match="consent"):
        AccessibilityRequest.model_validate(args)
    args["consent"] = True
    args["profile_tags"] = ["diabete"]  # una diagnosi non è un tag ammesso
    with pytest.raises(ValidationError):
        AccessibilityRequest.model_validate(args)


def test_unknown_top_level_field_rejected():
    d = load("00_busta_comune.json")
    d["task_id"] = "subtask-1"  # nome della bozza 1.0, ora subtask_id
    with pytest.raises(ValidationError):
        validate_busta(d)


def test_a2a_state_mapping():
    assert A2A_STATE[Status.NEEDS_INPUT] == "TASK_STATE_INPUT_REQUIRED"
    assert A2A_STATE[Status.DEGRADED] == "TASK_STATE_COMPLETED"
    assert A2A_STATE[Status.UNAVAILABLE] == "TASK_STATE_FAILED"
    assert set(A2A_STATE) == set(Status)


def test_confidence_rule():
    now = "2026-11-21T10:00:00Z"
    fresh = [Provenance(source="ATM", channel=Channel.API, fetched_at=now)]
    cached = [Provenance(source="ATM", channel=Channel.CACHE, fetched_at=now, stale=True)]
    assert compute_confidence(Status.SUCCESS, fresh) == 1.0
    assert compute_confidence(Status.SUCCESS, cached) == 0.8
    assert compute_confidence(Status.ERROR, fresh) == 0.0


# ------------------------------------------------------------------ ponte dagli agenti attuali

MOB = AgentRef(id="mach:mobility", domain="mobility", version="0.1.0")
HC = AgentRef(id="mach:healthcare", domain="healthcare", version="0.1.0")


async def test_real_mobility_output_wraps_into_valid_busta():
    esito = await MobilityAgent(llm=None).percorso(None, "Loreto", "niguarda", senza_barriere=True)
    busta = busta_from_esito(esito, MOB, "tr-test", skill="percorso", ttl_seconds=300)
    again = Busta.model_validate(busta.to_json())  # andata e ritorno in JSON
    assert again.status == Status.SUCCESS and again.provenance[0].simulated is True
    assert again.provenance[0].channel == Channel.MOCK


async def test_real_health_needs_input_maps_to_needs_input():
    esito = await HealthAgent(llm=None).cerca_disponibilita(None, "visita ortopedica")
    busta = busta_from_esito(esito, HC, "tr-test", skill="cerca_disponibilita")
    assert busta.status == Status.NEEDS_INPUT and busta.needs_input.fields == ["ha_prescrizione"]
    assert busta.a2a_state == "TASK_STATE_INPUT_REQUIRED"


def test_offline_esito_maps_to_unavailable_with_error():
    esito = Esito(EsitoStatus.OFFLINE, "CUP non raggiungibile", fonti=[Fonte("CUP")])
    busta = busta_from_esito(esito, HC, "tr-test")
    assert busta.status == Status.UNAVAILABLE and busta.errors[0].retryable is True
