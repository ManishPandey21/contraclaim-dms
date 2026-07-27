"""Phase 4 of the multi-agent letter-drafting workflow: the drafter ->
reviewer -> final approval chain — stage order, per-stage permissions,
separation of duties, assigned-reviewer enforcement, final-stage locking, and
the legacy /approve endpoint walking the chain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.models.letter_drafting import (
    ApprovalStep,
    ApproveStageRequest,
    DraftArtifact,
    DraftRun,
    LegalRiskFlag,
    LegalRiskReport,
    ProbingQuestion,
    UserDirectionAnswer,
    UserDirectionRequest,
    ValidationReport,
)
from rbac_backend.services.letter_drafting.service import DraftRunService


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeRepo:
    def __init__(self, run: DraftRun):
        self.run = run
        self.events: List[Dict[str, Any]] = []
        self.accepted: List[str] = []
        self.locked: List[str] = []

    async def get(self, letter_id, run_id):
        return self.run

    async def latest(self, letter_id, mode=None):
        return self.run

    async def update_fields(self, letter_id, run_id, fields):
        self.run = self.run.model_copy(update=fields)
        return self.run

    async def append_event(self, letter_id, run_id, event_type, **kwargs):
        self.events.append({"type": event_type, **kwargs})

    async def accept_draft(self, letter_id, run, user_id):
        self.accepted.append(user_id)
        return 1

    async def lock_approved_draft_version(self, letter_id, run_id, user_id):
        self.locked.append(user_id)
        return 1

    async def finalize_draft_approval(
        self, letter_id, run, approvals, user_id, approved_at
    ):
        self.accepted.append(user_id)
        self.locked.append(user_id)
        self.run = self.run.model_copy(
            update={
                "approvals": approvals,
                "approval_status": "approved",
                "status": "approved",
                "approved_by": user_id,
                "approved_at": approved_at,
            }
        )
        return self.run, 1


class FakePolicy:
    def __init__(self, admins: Optional[set] = None):
        self.admins = admins or set()

    async def has_permission(self, user, permission):
        return getattr(user, "id", None) in self.admins


def _user(user_id: str):
    return SimpleNamespace(id=user_id)


def _run(**overrides) -> DraftRun:
    base = dict(
        run_id="run-1",
        letter_id="letter-1",
        mode="draft",
        status="completed",
        role="contractor",
        plan="1. Drafting posture ...",
        draft_artifact=DraftArtifact(draft_letter="Dear Sir, ..."),
        validation_report=ValidationReport(blocking=False, findings=[]),
        created_by="drafter-1",
    )
    base.update(overrides)
    return DraftRun(**base)


def _service(run: DraftRun, admins: Optional[set] = None) -> DraftRunService:
    service = DraftRunService.__new__(DraftRunService)
    service.repository = FakeRepo(run)
    service.policy_service = FakePolicy(admins)
    service.authorized: List[str] = []

    async def _load_and_authorize(letter_id, current_user, action, drafting_permission=None, **kw):
        service.authorized.append(drafting_permission)
        return SimpleNamespace(id=letter_id)

    async def _emit_notification(*args, **kwargs):
        service.notified = True

    service._load_and_authorize = _load_and_authorize
    service._emit_notification = _emit_notification
    return service


async def _approve(service, stage, user_id, comment=None):
    return await service.approve_stage(
        "letter-1", "run-1", ApproveStageRequest(stage=stage, comment=comment), _user(user_id)
    )


# --------------------------------------------------------------------------- #
# Full chain
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_full_chain_drafter_reviewer_final():
    service = _service(_run())

    run = await _approve(service, "drafter", "drafter-1", comment="ready")
    assert [s.stage for s in run.approvals] == ["drafter"]
    assert run.approval_status == "drafter_approved"
    assert run.status == "completed"  # not yet approved

    run = await _approve(service, "reviewer", "reviewer-1")
    assert [s.stage for s in run.approvals] == ["drafter", "reviewer"]
    assert run.approval_status == "reviewer_approved"

    run = await _approve(service, "final", "manager-1")
    assert [s.stage for s in run.approvals] == ["drafter", "reviewer", "final"]
    assert run.status == "approved"
    assert run.approval_status == "approved"
    assert run.approved_by == "manager-1"

    # Version locked + accepted at final only; both audit event styles emitted.
    assert service.repository.accepted == ["manager-1"]
    assert service.repository.locked == ["manager-1"]
    events = [e["type"] for e in service.repository.events]
    assert events == ["drafter_approved", "reviewer_approved", "final_approved", "approved"]
    # Per-stage permissions were used in order.
    assert service.authorized == [
        "drafting.draft.submit_for_review",
        "drafting.review.approve",
        "drafting.final.approve",
    ]
    assert getattr(service, "notified", False) is True


# --------------------------------------------------------------------------- #
# Order + duplicates
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_out_of_order_stage_rejected():
    service = _service(_run())
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "reviewer", "reviewer-1")
    assert exc.value.status_code == 409
    assert "drafter" in exc.value.detail


@pytest.mark.asyncio
async def test_duplicate_stage_is_idempotent():
    service = _service(_run())
    first = await _approve(service, "drafter", "drafter-1")
    replay = await _approve(service, "drafter", "drafter-1")
    assert replay == first
    assert [step.stage for step in replay.approvals] == ["drafter"]


@pytest.mark.asyncio
async def test_chain_complete_final_replay_is_idempotent():
    approvals = [
        ApprovalStep(stage="drafter", approved_by="d"),
        ApprovalStep(stage="reviewer", approved_by="r"),
        ApprovalStep(stage="final", approved_by="f"),
    ]
    service = _service(_run(approvals=approvals, status="approved"))
    replay = await _approve(service, "final", "manager-1")
    assert replay.status == "approved"
    assert [step.stage for step in replay.approvals] == ["drafter", "reviewer", "final"]


# --------------------------------------------------------------------------- #
# Separation of duties + assigned reviewer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_drafter_stage_restricted_to_creator():
    service = _service(_run())
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "drafter", "someone-else")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_be_the_drafter():
    service = _service(_run())
    await _approve(service, "drafter", "drafter-1")
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "reviewer", "drafter-1")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_final_cannot_be_the_reviewer_but_admin_may():
    service = _service(_run(), admins={"reviewer-1"})
    await _approve(service, "drafter", "drafter-1")
    await _approve(service, "reviewer", "reviewer-1")
    # reviewer-1 is admin here, so the same-actor final approval is allowed.
    run = await _approve(service, "final", "reviewer-1")
    assert run.status == "approved"

    service2 = _service(_run())
    await _approve(service2, "drafter", "drafter-1")
    await _approve(service2, "reviewer", "reviewer-1")
    with pytest.raises(HTTPException) as exc:
        await _approve(service2, "final", "reviewer-1")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_assigned_reviewer_enforced():
    service = _service(_run(assigned_reviewer_id="assigned-1"))
    await _approve(service, "drafter", "drafter-1")
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "reviewer", "other-reviewer")
    assert exc.value.status_code == 403
    run = await _approve(service, "reviewer", "assigned-1")
    assert run.approval_status == "reviewer_approved"


# --------------------------------------------------------------------------- #
# Guards + legacy endpoint
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_blocked_run_cannot_enter_chain():
    service = _service(_run(status="needs_attention"))
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "drafter", "drafter-1")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_high_legal_risk_requires_recorded_human_review_before_approval():
    risk = LegalRiskReport(
        human_review_required=True,
        reviewed_at=datetime.now(timezone.utc),
        flags=[
            LegalRiskFlag(
                flag_id="risk-1", category="admission", severity="high",
                excerpt="We accept liability", explanation="Potential admission",
            )
        ],
    )
    service = _service(_run(legal_risk_report=risk))
    with pytest.raises(HTTPException) as exc:
        await _approve(service, "drafter", "drafter-1")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_legacy_approve_walks_the_chain():
    service = _service(_run())
    run = await service.approve_run("letter-1", "run-1", _user("drafter-1"))
    assert [s.stage for s in run.approvals] == ["drafter"]
    run = await service.approve_run("letter-1", "run-1", _user("reviewer-1"))
    assert [s.stage for s in run.approvals] == ["drafter", "reviewer"]
    run = await service.approve_run("letter-1", "run-1", _user("manager-1"))
    assert run.status == "approved"


@pytest.mark.asyncio
async def test_legacy_submit_binds_exact_governed_artifact():
    service = _service(_run())
    run = await service.approve_legacy_workflow_stage(
        "letter-1",
        "drafter",
        "Dear Sir, ...",
        _user("drafter-1"),
        run_id="run-1",
    )
    assert run.approval_status == "drafter_approved"
    assert service.repository.accepted == ["drafter-1"]


@pytest.mark.asyncio
async def test_legacy_submit_rejects_mutated_content():
    service = _service(_run())
    with pytest.raises(HTTPException) as exc:
        await service.approve_legacy_workflow_stage(
            "letter-1",
            "drafter",
            "Dear Sir, changed outside the governed run.",
            _user("drafter-1"),
            run_id="run-1",
        )
    assert exc.value.status_code == 409
    assert service.repository.accepted == []


@pytest.mark.asyncio
async def test_v2_direction_resume_requires_every_required_answer():
    service = _service(
        _run(
            status="awaiting_user_direction",
            execution_status="awaiting_user_direction",
            probing_questions=[
                ProbingQuestion(
                    question_id="deadline",
                    question="What deadline should apply?",
                    required=True,
                )
            ],
        )
    )
    with pytest.raises(HTTPException) as exc:
        await service.provide_user_direction(
            "letter-1",
            "run-1",
            UserDirectionRequest(directions="Proceed firmly."),
            _user("drafter-1"),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_v2_direction_resume_creates_linked_child_after_answers():
    original = _run(
        status="awaiting_user_direction",
        execution_status="awaiting_user_direction",
        probing_questions=[
            ProbingQuestion(
                question_id="deadline",
                question="What deadline should apply?",
                question_version=2,
                required=True,
            )
        ],
    )
    child = _run(run_id="run-2", parent_run_id="run-1")
    service = _service(original)
    captured = {}

    async def create_run(letter_id, request, current_user, **kwargs):
        captured["request"] = request
        captured["metadata"] = kwargs["engine_metadata"]
        return child

    service.create_run = create_run
    resumed = await service.provide_user_direction(
        "letter-1",
        "run-1",
        UserDirectionRequest(
            answers=[
                UserDirectionAnswer(
                    question_id="deadline",
                    question_version=2,
                    answer="Seven calendar days.",
                )
            ]
        ),
        _user("drafter-1"),
    )
    assert resumed.run_id == "run-2"
    assert captured["metadata"]["parent_run_id"] == "run-1"
    assert captured["request"].user_direction_answers[0].answer == "Seven calendar days."
    assert service.repository.run.status == "completed"
