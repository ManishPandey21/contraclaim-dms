"""Skill output schema registry (re-exported from the models layer)."""

from ...models.skill_output_schemas import get_schema, register_schema, registered_schema_keys

__all__ = ["get_schema", "register_schema", "registered_schema_keys"]
