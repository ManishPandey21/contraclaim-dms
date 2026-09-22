"""Link-to-Record search predicates must never drop a row the rendered label matches (CL-3B).

A predicate only narrows before the scan budget; the label check stays the
authority. For composed labels ("<parent> · <event> <n>") a search reaching past
the parent part cannot be expressed by parent fields, so it must not be narrowed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.services.entity_adapter_registry import (
    BankGuaranteeEventEntityAdapter,
    ChronologyEventEntityAdapter,
    KeyDateAchievementEntityAdapter,
    ProgrammeMilestoneEntityAdapter,
)


class _NoDb:
    """Any database access fails the test: these needles must not be narrowed."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected database access: {name}")


def _search(adapter: Any, needle: str) -> Any:
    return asyncio.run(adapter.link_target_search(_NoDb(), needle, organization_id="org", project_id="proj"))


@pytest.mark.parametrize("needle", ["extension 2", "2", "bg-001 · ext", "original 1", "· original"])
def test_bank_guarantee_event_label_tail_is_not_narrowed(needle: str) -> None:
    assert _search(BankGuaranteeEventEntityAdapter(), needle) is None


@pytest.mark.parametrize("needle", ["kd-7 · ach", "achievement", "· achievement", "12"])
def test_key_date_achievement_label_tail_is_not_narrowed(needle: str) -> None:
    assert _search(KeyDateAchievementEntityAdapter(), needle) is None


def test_row_label_adapters_narrow_on_their_label_fields_only() -> None:
    programme = _search(ProgrammeMilestoneEntityAdapter(), "pm-110")
    assert programme == {"$or": [
        {"milestone_ref": {"$regex": "pm\\-110", "$options": "i"}},
        {"title": {"$regex": "pm\\-110", "$options": "i"}},
    ]}
    # letter_no may be lifted from Document text and is not withheld: never searchable.
    chronology = _search(ChronologyEventEntityAdapter(), "notice")
    assert chronology == {"$or": [{"title": {"$regex": "notice", "$options": "i"}}]}


def test_empty_search_is_never_narrowed() -> None:
    for adapter in (BankGuaranteeEventEntityAdapter(), KeyDateAchievementEntityAdapter(),
                    ProgrammeMilestoneEntityAdapter(), ChronologyEventEntityAdapter()):
        assert _search(adapter, "") is None
