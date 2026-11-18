"""Contratto di output del layer 3 (busta comune + payload per dominio), schema_version 1.1."""
from .models import (A2A_STATE, SCHEMA_VERSION, AccessibilityData, AccessibilityRequest, AgentRef, Busta,
                     HealthcareData, MobilityData, Status, validate_busta)

__all__ = ["A2A_STATE", "SCHEMA_VERSION", "AccessibilityData", "AccessibilityRequest", "AgentRef", "Busta",
           "HealthcareData", "MobilityData", "Status", "validate_busta"]
