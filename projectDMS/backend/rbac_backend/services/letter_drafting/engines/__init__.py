"""Engine-selection boundary for letter drafting.

The legacy v2 service remains the only executable engine until Phase 3 wires
the official LangGraph worker.  Keeping selection here prevents the old
frontend feature flag or experimental sidecar from becoming a hidden route.
"""

from .base import DraftEngineDecision, DraftingEngine
from .policy import DraftEngineSelector, canonical_request_hash
from .v2 import V2DraftingEngine

__all__ = [
    "DraftEngineDecision",
    "DraftEngineSelector",
    "DraftingEngine",
    "V2DraftingEngine",
    "canonical_request_hash",
]
