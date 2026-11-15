"""Output schemas a skill step may produce, keyed by ``<name>.v<n>``.

The engine validates every model reply against the schema its manifest names.
Schemas are owned by the module whose feature produces them and registered
here, so the executor does not depend on any one feature's models. The registry
lives in the models layer so model modules can register without importing services.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

_SCHEMAS: Dict[str, type[BaseModel]] = {}


def register_schema(key: str, model: type[BaseModel]) -> None:
    """Register ``model`` under ``key``. Re-registering the same model is a no-op."""
    existing = _SCHEMAS.get(key)
    if existing is not None and existing is not model:
        raise ValueError(f"Output schema {key} is already registered as {existing.__name__}")
    _SCHEMAS[key] = model


def get_schema(key: str) -> Optional[type[BaseModel]]:
    _load_builtin_schemas()
    return _SCHEMAS.get(key)


def registered_schema_keys() -> List[str]:
    _load_builtin_schemas()
    return sorted(_SCHEMAS)


def _load_builtin_schemas() -> None:
    # Imported for their registration side effect, lazily, so this module stays
    # importable from the models that register with it.
    from . import claim_skill_outputs, letter_bench  # noqa: F401


__all__ = ["get_schema", "register_schema", "registered_schema_keys"]
