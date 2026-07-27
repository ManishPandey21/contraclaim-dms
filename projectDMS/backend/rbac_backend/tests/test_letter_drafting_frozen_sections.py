from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from rbac_backend.models.letter_drafting import (
    ApproveStageRequest,
    DraftArtifact,
    DraftRun,
    ReviseSectionsRequest,
    SourceEvidence,
    ValidationReport,
)
from rbac_backend.services.letter_drafting import service as service_module
from rbac_backend.services.letter_drafting.frozen_sections import (
    apply_section_edits,
    content_hash,
    freeze_sections,
    protected_anchor_changes,
    split_draft_sections,
    verify_frozen_sections,
)
from rbac_backend.services.letter_drafting.legal_risk_reviewer import LegalRiskReviewer
from rbac_backend.services.letter_drafting.service import DraftRunService
from rbac_backend.services.letter_drafting.validator import DraftValidator


SOURCE = (
    "Subject: Delay notice\r\nReference: ABC/001\r\n\r\n"
    "The Contractor's position under Clause 8.4 remains unchanged.\r\n"
    "All rights are expressly reserved.\r\n\r\n"
    "Please respond within seven days.\r\n\r\n"
    "Yours faithfully,\r\nContractor"
)


def test_split_sections_round_trips_exact_text() -> None:
    tokens, sections = split_draft_sections(SOURCE)

    assert "".join(tokens) == SOURCE
    assert [section.index for section in sections] == [0, 1, 2, 3]
    assert sections[1].content.endswith("reserved.")


def test_frozen_section_verification_is_character_exact() -> None:
    frozen = freeze_sections(SOURCE, [1], frozen_by="drafter-1")

    assert verify_frozen_sections(SOURCE, frozen) == []
    assert verify_frozen_sections(SOURCE.replace("Contractor's", "contractor's"), frozen) == [1]
    assert verify_frozen_sections(SOURCE.replace("Clause 8.4", "Clause  8.4"), frozen) == [1]


def test_scoped_edit_preserves_every_unselected_character() -> None:
    revised = apply_section_edits(
        SOURCE,
        {2: "Kindly provide your formal response within seven days."},
    )
    source_tokens, source_sections = split_draft_sections(SOURCE)
    revised_tokens, revised_sections = split_draft_sections(revised)

    assert len(source_sections) == len(revised_sections)
    assert source_tokens[0] == revised_tokens[0]
    assert source_tokens[1] == revised_tokens[1]
    assert source_sections[1].content == revised_sections[1].content
    assert source_sections[3].content == revised_sections[3].content
    assert revised_sections[2].content == "Kindly provide your formal response within seven days."


def test_section_edit_action_is_closed_allowlist() -> None:
    with pytest.raises(ValidationError):
        ReviseSectionsRequest(section_indices=[1], action="change_legal_position")


def test_protected_clause_date_amount_and_reference_cannot_drift() -> None:
    source = "Under Clause 8.4, ref ABC/001 dated 17/11/2023 records USD 25,000."
    changed = "Under Clause 8.5, ref ABC/002 dated 18/11/2023 records USD 20,000."

    result = protected_anchor_changes(source, changed)

    assert "clause 8.4" in result["removed"]
    assert "clause 8.5" in result["introduced"]
    assert "17/11/2023" in result["removed"]
    assert "18/11/2023" in result["introduced"]


def _run(**overrides) -> DraftRun:
    base = {
        "run_id": "run-source",
        "letter_id": "letter-1",
        "mode": "draft",
        "status": "completed",
        "role": "contractor",
        "plan": "Preserve the Contractor position and request a response.",
        "draft_artifact": DraftArtifact(
            draft_letter=SOURCE,
            source_integrity_notes="Supported by current instructions.",
        ),
        "sources": [
            SourceEvidence(
                source_id="current:1",
                source_type="current_input",
                allowed_use="fact",
                label="Current drafting input",
            )
        ],
        "validation_report": ValidationReport(blocking=False, findings=[]),
        "created_by": "drafter-1",
    }
    base.update(overrides)
    return DraftRun(**base)


class _FakeEditor:
    async def edit(self, sections, action):
        assert set(sections) == {2}
        assert action == "polish"
        return {2: "Kindly provide your formal response within seven days."}, []


@pytest.mark.asyncio
async def test_service_revises_only_selected_non_frozen_section(monkeypatch) -> None:
    source = _run(frozen_sections=freeze_sections(SOURCE, [1], frozen_by="drafter-1"))
    service = DraftRunService.__new__(DraftRunService)
    service.validator = DraftValidator()
    service.legal_risk_reviewer = LegalRiskReviewer()
    service.repository = SimpleNamespace()

    async def get_run(*_args, **_kwargs):
        return source

    async def create_and_record(run, *_args, **_kwargs):
        return run

    events = []

    async def append_event(*args, **kwargs):
        events.append((args, kwargs))

    service.get_run = get_run
    service._create_and_record = create_and_record
    service.repository.append_event = append_event
    monkeypatch.setattr(service_module, "ScopedSectionEditor", lambda: _FakeEditor())

    revised = await service.revise_selected_sections(
        "letter-1",
        "run-source",
        ReviseSectionsRequest(
            section_indices=[2],
            action="polish",
            expected_draft_hash=content_hash(SOURCE),
        ),
        SimpleNamespace(id="drafter-1"),
    )

    assert revised.revision_of_run_id == "run-source"
    assert revised.section_revision is not None
    assert revised.section_revision.editable_section_indices == [2]
    assert verify_frozen_sections(
        revised.draft_artifact.draft_letter,
        revised.section_revision.preserved_sections,
    ) == []
    assert revised.draft_artifact.draft_letter.replace(
        "Kindly provide your formal response within seven days.",
        "Please respond within seven days.",
    ) == SOURCE
    assert events[-1][0][2] == "sections_revised"


@pytest.mark.asyncio
async def test_service_rejects_editing_a_frozen_section() -> None:
    source = _run(frozen_sections=freeze_sections(SOURCE, [1], frozen_by="drafter-1"))
    service = DraftRunService.__new__(DraftRunService)

    async def get_run(*_args, **_kwargs):
        return source

    service.get_run = get_run

    with pytest.raises(HTTPException) as exc:
        await service.revise_selected_sections(
            "letter-1",
            "run-source",
            ReviseSectionsRequest(section_indices=[1], action="rewrite"),
            SimpleNamespace(id="drafter-1"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["section_indices"] == [1]


@pytest.mark.asyncio
async def test_approval_gate_rejects_tampered_frozen_content() -> None:
    frozen = freeze_sections(SOURCE, [1], frozen_by="drafter-1")
    tampered = SOURCE.replace("Clause 8.4", "Clause 8.5")
    run = _run(
        frozen_sections=frozen,
        draft_artifact=DraftArtifact(
            draft_letter=tampered,
            source_integrity_notes="Supported by current instructions.",
        ),
    )
    service = DraftRunService.__new__(DraftRunService)

    async def authorize(*_args, **_kwargs):
        return SimpleNamespace(id="letter-1")

    async def get(*_args, **_kwargs):
        return run

    service._load_and_authorize = authorize
    service.repository = SimpleNamespace(get=get)

    with pytest.raises(HTTPException) as exc:
        await service.approve_stage(
            "letter-1",
            "run-source",
            ApproveStageRequest(stage="drafter"),
            SimpleNamespace(id="drafter-1"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["section_indices"] == [1]
