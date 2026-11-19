"""T-4: the live ``skill_version_unique`` index, verified before relying on it.

One stored document per ``(skill_key, version)`` is held by the unique index that
migration ``20261114_0001`` creates. Nothing creates it at start-up, and by owner decision
nothing will: a production index is created or repaired only by the governed migration
(Gate 6 evidence), never as a side effect of serving a request. So the operations whose
correctness depends on that uniqueness read the live index first and fail closed (503,
nothing written) when it is absent or differs from the migration's definition:

- importing a draft (``SkillRegistry.create_draft``: an upsert-or-insert outside a
  transaction, where only the index stops a concurrent import writing a second document);
- deprecating a version (``SkillRegistry.deprecate``), which keeps its deterministic-``_id``
  safeguard for bundled versions as a second line.

Read on every call, never cached: a cached "valid" would outlive a dropped index (the
pattern of ``child_document_service.require_child_document_indexes``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from fastapi import HTTPException

#: The migration's definition (``migrations/v20261114_0001_skill_registry.py``); the
#: test ``tests/test_skill_version_index_gate.py`` keeps the two identical.
SKILL_VERSION_UNIQUE_INDEX: Dict[str, Any] = {
    "collection": "skill_versions",
    "keys": [("skill_key", 1), ("version", 1)],
    "name": "skill_version_unique",
    "unique": True,
}


def _keys(live: Mapping[str, Any]) -> List[tuple]:
    return [(str(field), int(direction)) for field, direction in (live.get("key") or [])]


def skill_version_index_problems(index_information: Mapping[str, Any]) -> List[str]:
    """Everything wrong with the live index; empty means the uniqueness holds."""
    name = SKILL_VERSION_UNIQUE_INDEX["name"]
    live = index_information.get(name)
    if live is None:
        return [f"{name} is missing"]
    problems: List[str] = []
    expected = [(field, int(direction)) for field, direction in SKILL_VERSION_UNIQUE_INDEX["keys"]]
    if _keys(live) != expected:
        problems.append(f"{name} has keys {_keys(live)}, expected {expected}")
    if live.get("unique") is not True:
        problems.append(f"{name} is not unique")
    if live.get("partialFilterExpression") is not None:
        problems.append(f"{name} is partial; the migration defines it over every document")
    return problems


class SkillVersionIndexUnavailable(HTTPException):
    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__(
            status_code=503,
            detail=(
                "Skill versions cannot be changed: the skill_version_unique index is not in place "
                "(apply migration 20261114_0001)"
            ),
        )


async def require_skill_version_index(db: Any) -> None:
    """Fail closed unless the live unique index matches the migration's definition."""
    try:
        info = await db[SKILL_VERSION_UNIQUE_INDEX["collection"]].index_information()
    except Exception as exc:  # the gate cannot be evaluated: refuse
        raise SkillVersionIndexUnavailable(["index information unavailable"]) from exc
    problems = skill_version_index_problems(info)
    if problems:
        raise SkillVersionIndexUnavailable(problems)


__all__ = [
    "SKILL_VERSION_UNIQUE_INDEX",
    "SkillVersionIndexUnavailable",
    "require_skill_version_index",
    "skill_version_index_problems",
]
