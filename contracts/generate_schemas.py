"""Rigenera i JSON Schema in contracts/schemas/ a partire dai modelli pydantic.

    python -m contracts.generate_schemas
"""
import json
from pathlib import Path

from .models import AccessibilityData, AccessibilityRequest, Busta, HealthcareData, MobilityData

OUT = Path(__file__).parent / "schemas"
MODELS = {
    "busta": Busta,
    "mobility_data": MobilityData,
    "healthcare_data": HealthcareData,
    "accessibility_data": AccessibilityData,
    "accessibility_request": AccessibilityRequest,
}


def build() -> dict[str, dict]:
    out = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema(by_alias=True)
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "$id": f"https://mach.local/contracts/1.1/{name}.schema.json", **schema}
        out[name] = schema
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, schema in build().items():
        (OUT / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        print("scritto", name)


if __name__ == "__main__":
    main()
