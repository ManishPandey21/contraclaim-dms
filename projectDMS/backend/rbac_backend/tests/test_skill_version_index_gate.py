"""T-4: the skill_version_unique gate matches the governed migration and fails closed.

Real-replica-set proof (dropped, non-unique and wrong-key index; nothing written, the
index never created by the gate): ``integration/test_skill_publication_transactions_mongo.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.migrations.v20261114_0001_skill_registry import INDEXES as MIGRATION_INDEXES
from rbac_backend.services.skills.version_index import (
    SKILL_VERSION_UNIQUE_INDEX,
    SkillVersionIndexUnavailable,
    require_skill_version_index,
    skill_version_index_problems,
)

GOOD: Dict[str, Any] = {
    "_id_": {"key": [("_id", 1)]},
    "skill_version_unique": {"key": [("skill_key", 1), ("version", 1)], "unique": True},
}


def test_the_gate_checks_exactly_the_migrations_definition() -> None:
    governed = next(index for index in MIGRATION_INDEXES if index["name"] == "skill_version_unique")
    assert governed == SKILL_VERSION_UNIQUE_INDEX


def test_a_correct_index_passes() -> None:
    assert skill_version_index_problems(GOOD) == []


@pytest.mark.parametrize(
    ("live", "problem"),
    [
        ({"_id_": {"key": [("_id", 1)]}}, "missing"),
        ({"skill_version_unique": {"key": [("skill_key", 1), ("version", 1)]}}, "not unique"),
        ({"skill_version_unique": {"key": [("skill_key", 1)], "unique": True}}, "has keys"),
        ({"skill_version_unique": {"key": [("version", 1), ("skill_key", 1)], "unique": True}}, "has keys"),
        ({"skill_version_unique": {"key": [("skill_key", 1), ("version", 1)], "unique": True,
                                   "partialFilterExpression": {"status": "draft"}}}, "partial"),
    ],
)
def test_a_missing_or_different_index_is_a_problem(live: Dict[str, Any], problem: str) -> None:
    problems = skill_version_index_problems(live)
    assert problems and problem in problems[0], problems


class _Collection:
    def __init__(self, info: Any) -> None:
        self.info = info

    async def index_information(self) -> Any:
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


class _Db:
    def __init__(self, info: Any) -> None:
        self.collection = _Collection(info)

    def __getitem__(self, name: str) -> _Collection:
        assert name == "skill_versions"
        return self.collection


def test_the_gate_refuses_with_503_and_passes_a_correct_index() -> None:
    asyncio.run(require_skill_version_index(_Db(GOOD)))
    with pytest.raises(SkillVersionIndexUnavailable) as missing:
        asyncio.run(require_skill_version_index(_Db({})))
    assert missing.value.status_code == 503


def test_an_unreadable_index_list_is_refused() -> None:
    with pytest.raises(SkillVersionIndexUnavailable) as unreadable:
        asyncio.run(require_skill_version_index(_Db(RuntimeError("not authorised to list indexes"))))
    assert unreadable.value.problems == ["index information unavailable"]
