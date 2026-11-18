"""Interoperabilità con gli altri team: profilo MACH, gate dati, discovery federata, spoke esterno."""
from .mach import Consent, MachContext, new_trace_id

__all__ = ["Consent", "MachContext", "new_trace_id"]
