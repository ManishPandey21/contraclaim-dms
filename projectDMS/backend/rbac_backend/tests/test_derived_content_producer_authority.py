"""G37 write side: a blocked document must not father a new derivative.

Read-time gating (`consumable_derived_text`) makes a STALE `relevance_note`
unusable once its source is blocked. It says nothing about the other half of
containment: whether a document that is *already* blocked can still be fed to
the LLM and produce a brand-new note.

The producer is `_document_understanding` (`agents/llm.py:549`). It reads the
`documents` collection through `_find_source_rows`, condenses
`subject/summary/description/full_content/extracted_text` into a `sources`
array, and puts that array verbatim into the prompt (`_build_prompt:199`). No
authority check existed anywhere on that path, so an extraction the quality
gate had rejected was still summarised into `arbitration_document_index` - a
row that then looks like ordinary approved matrix evidence.

The deterministic agent has the same shape at `_document_indexing:171`, which
writes `relevance_note` straight from those raw fields.

These tests assert the ACTUAL prompt text and the ACTUAL persisted row, not
that a policy helper was called - a helper whose result is discarded is the
failure mode this whole gap was made of.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.arbitration_drafting.agents.deterministic import (
    DeterministicArbitrationAgent,
)
from rbac_backend.services.arbitration_drafting.agents.llm import LLMArbitrationAgent

CLEAN_TEXT = "CLEAN SOURCE BODY about late access"
BLOCKED_TEXT = "BLOCKED SOURCE BODY about late access"

CASE = {
    "_id": "case-1",
    "organization_id": "org-1",
    "project_id": "project-1",
    "title": "EOT claim",
    "case_summary": "Delay and prolongation claim.",
}


# --- minimal Mongo double ------------------------------------------------------


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def limit(self, value: int) -> "_Cursor":
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = list(rows or [])

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor([row for row in self.rows if _matches(row, query or {})])

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *a: Any, **k: Any):
        rows = await self.find(query).to_list()
        return rows[0] if rows else None

    async def insert_one(self, row: Dict[str, Any]):
        self.rows.append(dict(row))
        return type("R", (), {"inserted_id": row.get("_id")})()

    async def update_one(self, query=None, update=None, *a: Any, **k: Any):
        row = await self.find_one(query)
        if row and update and "$set" in update:
            row.update(update["$set"])
        return type("R", (), {"matched_count": 1 if row else 0})()


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$exists" in expected:
            if bool(expected["$exists"]) != (key in row):
                return False
            continue
        if isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
            continue
        if row.get(key) != expected:
            return False
    return True


class _DB:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._collections = {
            "documents": _Collection(documents),
            "arbitration_document_index": _Collection([]),
            "arbitration_cases": _Collection([CASE]),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _document(doc_id: str, text: str, **extra: Any) -> Dict[str, Any]:
    document = {
        "_id": doc_id,
        "organization_id": "org-1",
        "project_id": "project-1",
        "subject": text,
        "full_content": text,
        "filename": f"{doc_id}.pdf",
    }
    document.update(extra)
    return document


# --- the seam: what actually reaches the model ---------------------------------


class _CapturingGenerator:
    """Stands in for the real LLM and keeps every prompt it is handed."""

    available = True

    def __init__(self) -> None:
        self.prompts: List[str] = []

    async def generate(self, prompt: str, *a: Any, **k: Any) -> str:
        self.prompts.append(prompt)
        return "[]"


def _prompt_for(documents: List[Dict[str, Any]]) -> str:
    generator = _CapturingGenerator()
    agent = LLMArbitrationAgent(
        db=_DB(documents),
        case=CASE,
        draft_id="draft-1",
        options={},
        current_user={"id": "user-1"},
        generator=generator,
    )
    asyncio.run(agent._document_understanding("document-understanding"))
    return "\n".join(generator.prompts)


def test_a_clean_source_may_enter_the_prompt() -> None:
    """The guard must not empty the pipeline: clean work still flows."""
    prompt = _prompt_for([_document("doc-clean", CLEAN_TEXT, processing_status="metadata_extracted")])

    assert CLEAN_TEXT in prompt
    assert "doc-clean" in prompt


def test_an_adverse_quality_verdict_keeps_the_source_out_of_the_prompt() -> None:
    """The core write-side property.

    `human_review_required` is the gate's verdict that the extraction itself is
    not trustworthy. Summarising it into a new matrix row launders exactly the
    content the verdict rejected.
    """
    prompt = _prompt_for(
        [_document("doc-blocked", BLOCKED_TEXT, processing_status="human_review_required")]
    )

    assert BLOCKED_TEXT not in prompt
    assert "doc-blocked" not in prompt


def test_a_quarantined_duplicate_is_kept_out_of_the_prompt() -> None:
    prompt = _prompt_for(
        [
            _document(
                "doc-dupe",
                BLOCKED_TEXT,
                processing_status="metadata_extracted",
                duplicate_status="duplicate",
            )
        ]
    )

    assert BLOCKED_TEXT not in prompt


def test_a_deleted_document_is_kept_out_of_the_prompt() -> None:
    prompt = _prompt_for(
        [
            _document(
                "doc-gone",
                BLOCKED_TEXT,
                processing_status="metadata_extracted",
                lifecycle_state="deleted",
            )
        ]
    )

    assert BLOCKED_TEXT not in prompt


def test_an_operational_failure_still_contributes_under_last_known_good() -> None:
    """Model B. A worker crash is not a verdict about the content.

    If `failed` suppressed the source here, every transient OCR or Mongo error
    would silently shrink the evidence base of a live arbitration draft.
    """
    prompt = _prompt_for([_document("doc-failed", CLEAN_TEXT, processing_status="failed")])

    assert CLEAN_TEXT in prompt


def test_a_foreign_tenants_document_never_reaches_the_prompt() -> None:
    prompt = _prompt_for(
        [
            _document(
                "doc-foreign",
                BLOCKED_TEXT,
                organization_id="org-2",
                processing_status="metadata_extracted",
            )
        ]
    )

    assert BLOCKED_TEXT not in prompt


def test_the_blocked_source_is_removed_while_its_clean_neighbour_survives() -> None:
    """Containment must be per-document, not all-or-nothing."""
    prompt = _prompt_for(
        [
            _document("doc-clean", CLEAN_TEXT, processing_status="metadata_extracted"),
            _document("doc-blocked", BLOCKED_TEXT, processing_status="human_review_required"),
        ]
    )

    assert CLEAN_TEXT in prompt
    assert BLOCKED_TEXT not in prompt


# --- the deterministic producer writes the derivative directly -----------------


def _deterministic_index_rows(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    db = _DB(documents)
    agent = DeterministicArbitrationAgent(
        db=db,
        case=CASE,
        draft_id="draft-1",
        options={},
        current_user={"id": "user-1"},
    )
    asyncio.run(agent._document_indexing("document-indexing"))
    return db["arbitration_document_index"].rows


def test_the_deterministic_agent_does_not_index_a_blocked_document() -> None:
    """`_document_indexing:171` condenses raw extracted text into the row."""
    rows = _deterministic_index_rows(
        [_document("doc-blocked", BLOCKED_TEXT, processing_status="human_review_required")]
    )

    assert not [row for row in rows if BLOCKED_TEXT in str(row.get("relevance_note") or "")]
    assert not [row for row in rows if str(row.get("source_id")) == "doc-blocked"]


def test_the_deterministic_agent_still_indexes_a_clean_document() -> None:
    rows = _deterministic_index_rows(
        [_document("doc-clean", CLEAN_TEXT, processing_status="metadata_extracted")]
    )

    assert [row for row in rows if str(row.get("source_id")) == "doc-clean"]


@pytest.mark.parametrize(
    "status,expected",
    [("failed", True), ("metadata_extracted", True), ("human_review_required", False)],
)
def test_deterministic_indexing_follows_the_same_authority_model(
    status: str, expected: bool
) -> None:
    """One model, both producers - they must not drift apart."""
    rows = _deterministic_index_rows([_document("doc-1", CLEAN_TEXT, processing_status=status)])

    assert bool(rows) is expected
