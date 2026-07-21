from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from bson import ObjectId

from rbac_backend.models.letter_drafting import (
    DraftReviewAssignment,
    DraftReviewComment,
    DraftContextPack,
    DraftArtifact,
    DraftAssertionSupport,
    DraftContextBundle,
    DraftRunCreateRequest,
    DraftRun,
    SourceEvidence,
    ValidationReport,
)
from rbac_backend.services.letter_drafting.generator import parse_draft_artifact
from rbac_backend.services.letter_drafting.incoming_analyzer import IncomingLetterAnalyzer
from rbac_backend.services.letter_drafting.prompts import (
    DEFAULT_DRAFT_TEMPLATE,
    DEFAULT_STRATEGY_TEMPLATE,
    PromptRegistry,
    REQUIRED_STRATEGY_ROADMAP_SECTIONS,
    UNTRUSTED_GUARD_SENTINEL,
    ensure_strategy_roadmap,
    ensure_untrusted_guard,
    extract_template_variables,
)
from rbac_backend.services.llm_config_service import (
    DEFAULT_PLAN_PROMPT_TEMPLATE,
    LLMConfigService,
)
from rbac_backend.services.letter_drafting.repository import DraftRunRepository
from rbac_backend.services.letter_drafting.service import DraftRunService
from rbac_backend.services.letter_drafting.validator import DraftValidator
from rbac_backend.retrieval.source_metadata import normalize_source_payload


class _InsertResult:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


class _UpdateResult:
    def __init__(self, matched_count: int = 1, modified_count: int = 1) -> None:
        self.matched_count = matched_count
        self.modified_count = modified_count


class _FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, key: str, direction: int):
        self.docs.sort(key=lambda item: item.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length: Optional[int] = None):
        return [dict(doc) for doc in self.docs[:length]]


class _FakeCollection:
    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.docs = docs or []

    async def insert_one(self, doc: Dict[str, Any]) -> _InsertResult:
        stored = dict(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    async def find_one(self, query: Dict[str, Any], projection: Any = None, sort: Any = None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda item: item.get(key), reverse=direction < 0)
        return dict(matches[0]) if matches else None

    def find(self, query: Dict[str, Any]):
        return _FakeCursor([doc for doc in self.docs if self._matches(doc, query)])

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs: Any):
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, []).append(value)
            return _UpdateResult()
        if kwargs.get("upsert"):
            stored = dict(query)
            if "$set" in update:
                stored.update(update["$set"])
            stored.setdefault("_id", ObjectId())
            self.docs.append(stored)
            return _UpdateResult(0, 1)
        return _UpdateResult(0, 0)

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs: Any):
        matched_count = 0
        modified_count = 0
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            matched_count += 1
            if "$set" in update:
                doc.update(update["$set"])
                modified_count += 1
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, []).append(value)
                modified_count += 1
        return _UpdateResult(matched_count, modified_count)

    @staticmethod
    def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for key, value in query.items():
            if doc.get(key) != value:
                return False
        return True


class _FakeDB:
    def __init__(self) -> None:
        self.letter_draft_runs = _FakeCollection()
        self.letter_draft_events = _FakeCollection()
        self.letter_draft_assignments = _FakeCollection()
        self.letter_draft_comments = _FakeCollection()
        self.draft_context_packs = _FakeCollection()
        self.letter_draft_input_snapshots = _FakeCollection()
        self.letter_draft_evidence_snapshots = _FakeCollection()
        self.letter_draft_effects = _FakeCollection()
        self.letter_draft_outbox = _FakeCollection()
        self.letter_draft_shadow_comparisons = _FakeCollection()
        self.issued_letters = _FakeCollection()
        self.letters = _FakeCollection()
        self.users = _FakeCollection()
        self.prompt_templates = _FakeCollection()
        self.app_settings = _FakeCollection()

    def __getitem__(self, name: str):
        return getattr(self, name)


def test_prompt_registry_extracts_required_variables() -> None:
    variables = extract_template_variables(DEFAULT_DRAFT_TEMPLATE)

    assert {"role", "active_workspace", "sources", "plan"}.issubset(variables)
    assert PromptRegistry.validate_template(DEFAULT_DRAFT_TEMPLATE, ["role", "sources"]) == []


def test_strategy_prompt_requires_full_roadmap() -> None:
    for section in REQUIRED_STRATEGY_ROADMAP_SECTIONS:
        assert section in DEFAULT_STRATEGY_TEMPLATE


def test_langgraph_plan_prompt_requires_full_roadmap() -> None:
    assert "Analyze the provided incoming letter context" in DEFAULT_PLAN_PROMPT_TEMPLATE
    assert "OUTPUT FORMAT - Return a structured roadmap" in DEFAULT_PLAN_PROMPT_TEMPLATE
    for section in REQUIRED_STRATEGY_ROADMAP_SECTIONS:
        assert section in DEFAULT_PLAN_PROMPT_TEMPLATE
    assert "Do not invent missing sender details" in DEFAULT_PLAN_PROMPT_TEMPLATE


def test_strategy_prompt_override_gets_roadmap_addendum() -> None:
    template = "Prepare strategy using {role}, {sources}, and {current_materials}."
    patched = ensure_strategy_roadmap(template)

    assert "Required strategic-plan roadmap" in patched
    for section in REQUIRED_STRATEGY_ROADMAP_SECTIONS:
        assert section in patched
    assert {"role", "sources", "current_materials"}.issubset(
        extract_template_variables(patched)
    )


def test_langgraph_plan_prompt_override_gets_roadmap_addendum() -> None:
    db = _FakeDB()
    db.app_settings.docs.append(
        {
            "_id": "langgraph_llm_config",
            "drafter_model": "gpt-4o",
            "reviewer_model": "gpt-4o-mini",
            "plan_model": "grok-4-1-fast",
            "draft_prompt_template": DEFAULT_DRAFT_TEMPLATE,
            "plan_prompt_template": "Plan with {subject}, {role}, and {sources}.",
        }
    )

    config = asyncio.run(LLMConfigService(db).get_config())

    assert "Required strategic-plan roadmap" in config.plan_prompt_template
    for section in REQUIRED_STRATEGY_ROADMAP_SECTIONS:
        assert section in config.plan_prompt_template


def test_default_templates_carry_untrusted_guard() -> None:
    # H1: adversarial counterparty letters flow into these prompts; every
    # default template must instruct the model to treat them as data.
    assert UNTRUSTED_GUARD_SENTINEL in DEFAULT_DRAFT_TEMPLATE
    assert UNTRUSTED_GUARD_SENTINEL in DEFAULT_STRATEGY_TEMPLATE


def test_ensure_untrusted_guard_appends_once_and_stays_renderable() -> None:
    template = "Draft using {role} and {sources}."
    patched = ensure_untrusted_guard(template)

    assert UNTRUSTED_GUARD_SENTINEL in patched
    # Idempotent: applying twice must not duplicate the guard.
    assert patched == ensure_untrusted_guard(patched)
    # Guard must be brace-free so str.format() rendering still works.
    assert patched.format(role="Contractor", sources="- C1").startswith("Draft using Contractor")


def test_prompt_registry_enforces_untrusted_guard_on_overrides() -> None:
    db = _FakeDB()
    registry = PromptRegistry(db)

    # An admin override that drops the guard gets it re-appended on save...
    record = asyncio.run(
        registry.update_prompt(
            "letter_drafting.v2.draft",
            "Draft with {role}, {sources}, {current_materials}.",
            "admin-1",
        )
    )
    assert UNTRUSTED_GUARD_SENTINEL in record.template

    # ...and legacy DB records written before the guard existed get it at read time.
    db.prompt_templates.docs[0]["template"] = "Legacy template with {role} and {sources}."
    active = asyncio.run(registry.get_enabled("letter_drafting.v2.draft"))
    assert UNTRUSTED_GUARD_SENTINEL in active.template


def test_langgraph_templates_enforce_untrusted_guard() -> None:
    db = _FakeDB()
    db.app_settings.docs.append(
        {
            "_id": "langgraph_llm_config",
            "drafter_model": "gpt-4o",
            "reviewer_model": "gpt-4o-mini",
            "plan_model": "grok-4-1-fast",
            "draft_prompt_template": "Draft with {subject} and {sources}.",
            "plan_prompt_template": "Plan with {subject}, {role}, and {sources}.",
        }
    )

    config = asyncio.run(LLMConfigService(db).get_config())

    assert UNTRUSTED_GUARD_SENTINEL in config.draft_prompt_template
    assert UNTRUSTED_GUARD_SENTINEL in config.plan_prompt_template


def test_source_metadata_normalizes_aliases_and_hash() -> None:
    payload = normalize_source_payload(
        {
            "org_id": "org-1",
            "project_id": "proj-1",
            "document_id": "doc-1",
            "letterNo": "ABC-123",
            "clause_no": "4.2",
            "page": 7,
            "text": "Clause text",
        }
    )

    assert payload["organization_id"] == "org-1"
    assert payload["letter_no"] == "ABC-123"
    assert payload["clause_number"] == "4.2"
    assert payload["page_numbers"] == [7]
    assert payload["source_hash"]


def test_draft_artifact_parser_omits_learning_update_until_finalized() -> None:
    raw = """Draft Letter
Letter body

Source Integrity Notes
Supported by current source.

Learning Update
Profile Type: Contractor
"""

    artifact = parse_draft_artifact(raw, "test-model", 3, finalized=False)

    assert artifact.draft_letter == "Letter body"
    assert artifact.source_integrity_notes == "Supported by current source."
    assert artifact.learning_update is None
    assert artifact.prompt_version == 3


def test_validator_blocks_unsupported_clause_citation() -> None:
    report = DraftValidator().validate(
        DraftArtifact(
            draft_letter="The Contractor refers to Clause 8.4.",
            source_integrity_notes="Current input only.",
        ),
        role="contractor",
        sources=[
            SourceEvidence(
                source_id="current:1",
                source_type="current_input",
                allowed_use="fact",
                label="Current input",
            )
        ],
    )

    assert report.blocking is True
    assert any(finding.code == "unsupported_clause_citation" for finding in report.findings)


def test_critique_flags_contractual_red_flags_without_blocking() -> None:
    report = DraftValidator().critique(
        DraftArtifact(
            draft_letter="The Contractor is misusing the Site and payment shall be withheld.",
            source_integrity_notes="Supported by current source.",
        ),
        role="engineer",
        sources=[
            SourceEvidence(
                source_id="current:1",
                source_type="current_input",
                allowed_use="fact",
                label="Current input",
            )
        ],
    )

    assert report.blocking is False
    assert {finding.code for finding in report.findings}.issuperset(
        {"unsupported_allegation", "payment_implication"}
    )


def test_validator_requires_strategy_alignment_for_review() -> None:
    report = DraftValidator().strategy_alignment(
        DraftArtifact(
            draft_letter="The Contractor rejects the claim based on available records.",
            source_integrity_notes="Supported.",
        ),
        plan="",
    )

    assert report.blocking is True
    assert report.findings[0].code == "missing_strategy_plan"


def test_confidence_scores_reflect_supported_assertions() -> None:
    scores = DraftRunService._confidence_scores(
        ValidationReport(blocking=False, findings=[]),
        [
            DraftAssertionSupport(
                assertion_id="a1",
                text="Supported assertion",
                support_status="supported",
                source_ids=["s1"],
                risk_level="low",
            )
        ],
        [
            SourceEvidence(
                source_id="clause:1",
                source_type="contract_clause",
                allowed_use="clause",
                label="Clause 1",
                clause_number="1",
            )
        ],
    )

    assert scores.overall >= 0.9
    assert scores.risk_level == "low"


def test_threshold_report_requires_core_inputs() -> None:
    context = DraftContextBundle(
        threshold_inputs={
            "sender_profile": True,
            "letter_purpose": False,
            "intended_recipient": True,
            "key_issue_or_event": False,
            "main_factual_basis": True,
        }
    )

    report = DraftValidator().threshold_findings(context)

    assert report.blocking is True
    assert {finding.code for finding in report.findings} == {
        "threshold_missing_letter_purpose",
        "threshold_missing_key_issue_or_event",
    }


async def test_incoming_analyzer_extracts_roadmap_fields() -> None:
    letter = SimpleNamespace(
        subject="Delay claim for pier works",
        recipient="Engineer",
        content=(
            "From: JV Contractor\n"
            "Letter No: JV/CLM/001\n"
            "Subject: Delay claim for pier works\n"
            "Contract Ref: KNP-01\n"
            "We request extension of time within 7 days under Clause 8.4."
        ),
        reference=None,
        strategy_role="contractor",
    )
    request = DraftRunCreateRequest(
        draft_type="reply",
        role="contractor",
        subject="Delay claim for pier works",
        recipient="Engineer",
        incoming_letter_id="incoming-1",
    )

    analysis = await IncomingLetterAnalyzer().analyze(letter, request, SimpleNamespace())

    assert analysis.sender == "JV Contractor"
    assert analysis.issue_type == "claim"
    assert analysis.issue_type_source == "ai"
    assert analysis.response_required is True
    assert analysis.priority_flag is True
    assert analysis.clauses_cited == ["8.4"]
    assert analysis.cited_clause_evaluations[0].legal_or_commercial_review_required is True


async def test_repository_creates_and_fetches_latest_run() -> None:
    db = _FakeDB()
    repo = DraftRunRepository(db)
    older = DraftRun(
        run_id="run-1",
        letter_id="letter-1",
        mode="strategy",
        status="completed",
        role="contractor",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = older.model_copy(
        update={
            "run_id": "run-2",
            "mode": "draft",
            "started_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "completed_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
    )

    await repo.create(older)
    await repo.create(newer)

    latest = await repo.latest("letter-1", None)
    latest_draft = await repo.latest("letter-1", "draft")

    assert latest is not None and latest.run_id == "run-2"
    assert latest_draft is not None and latest_draft.mode == "draft"


async def test_repository_accepts_draft_as_immutable_version() -> None:
    db = _FakeDB()
    letter_id = ObjectId()
    db.letters.docs.append({"_id": letter_id, "draft_versions": []})
    repo = DraftRunRepository(db)
    run = DraftRun(
        run_id="run-1",
        letter_id=str(letter_id),
        mode="draft",
        status="completed",
        role="contractor",
        plan="Plan",
        draft_artifact=DraftArtifact(
            draft_letter="The Contractor submits this draft.",
            source_integrity_notes="Supported.",
        ),
        created_by="user-1",
    )

    version = await repo.accept_draft(str(letter_id), run, "user-1")

    stored = db.letters.docs[0]
    assert version == 1
    assert stored["draft_output"] == "The Contractor submits this draft."
    assert stored["current_draft_version"] == 1
    assert stored["draft_versions"][0]["run_id"] == "run-1"


async def test_repository_saves_strategy_versions_and_locks_approved_draft() -> None:
    db = _FakeDB()
    letter_id = ObjectId()
    db.letters.docs.append({"_id": letter_id, "draft_versions": [], "strategy_versions": []})
    repo = DraftRunRepository(db)
    run = DraftRun(
        run_id="run-1",
        letter_id=str(letter_id),
        mode="strategy",
        status="completed",
        role="contractor",
        plan="Strategic plan",
        draft_artifact=DraftArtifact(
            draft_letter="The Contractor submits this draft.",
            source_integrity_notes="Supported.",
        ),
        created_by="drafter-1",
    )

    strategy_version = await repo.save_strategy_plan(str(letter_id), run, "drafter-1")
    await repo.accept_draft(str(letter_id), run.model_copy(update={"mode": "draft"}), "approver-1")
    locked_version = await repo.lock_approved_draft_version(
        str(letter_id),
        "run-1",
        "approver-1",
    )

    stored = db.letters.docs[0]
    assert strategy_version == 1
    assert stored["strategy_plan"] == "Strategic plan"
    assert stored["strategy_versions"][0]["run_id"] == "run-1"
    assert locked_version == 1
    assert stored["approved_version_locked"] is True
    assert stored["draft_versions"][0]["locked"] is True


async def test_repository_appends_lifecycle_event() -> None:
    db = _FakeDB()
    repo = DraftRunRepository(db)

    await repo.append_event(
        "letter-1",
        "run-1",
        "validated",
        actor_user_id="user-1",
        status="completed",
        payload={"finding_count": 0},
    )

    stored = db.letter_draft_events.docs[0]
    assert stored["letter_id"] == "letter-1"
    assert stored["run_id"] == "run-1"
    assert stored["event_type"] == "validated"
    assert stored["actor_user_id"] == "user-1"

    events = await repo.list_events("letter-1", "run-1")

    assert len(events) == 1
    assert events[0].event_type == "validated"


async def test_repository_creates_and_fetches_context_pack() -> None:
    db = _FakeDB()
    repo = DraftRunRepository(db)
    pack = DraftContextPack(
        context_pack_id="ctx-1",
        letter_id="letter-1",
        run_id="run-1",
        project={"organization_id": "org-1", "project_id": "proj-1"},
        facts=["Fact one"],
        source_ids=["current:1"],
    )

    await repo.create_context_pack(pack)
    stored = await repo.get_context_pack("letter-1", "run-1")

    assert stored is not None
    assert stored.context_pack_id == "ctx-1"
    assert stored.facts == ["Fact one"]


async def test_repository_tracks_governance_assignments_and_comments() -> None:
    db = _FakeDB()
    repo = DraftRunRepository(db)
    assignment = DraftReviewAssignment(
        assignment_id="assign-1",
        letter_id="letter-1",
        run_id="run-1",
        reviewer_user_id="reviewer-1",
        assigned_by="manager-1",
        note="Please check entitlement language.",
    )
    comment = DraftReviewComment(
        comment_id="comment-1",
        letter_id="letter-1",
        run_id="run-1",
        body="Clause reference needs confirmation.",
        created_by="reviewer-1",
    )

    await repo.upsert_assignment(assignment)
    await repo.add_comment(comment)

    assignments = await repo.list_assignments("letter-1", "run-1")
    comments = await repo.list_comments("letter-1", "run-1")

    assert len(assignments) == 1
    assert assignments[0].reviewer_user_id == "reviewer-1"
    assert len(comments) == 1
    assert comments[0].body == "Clause reference needs confirmation."


async def test_prompt_registry_updates() -> None:
    db = _FakeDB()
    registry = PromptRegistry(db)
    
    # 1. Update strategic plan prompt and verify it saves and gets enabled
    template_str = "Strategy plan: {active_workspace}, {role}, {recipient}, {subject}, {recipient_focus}, {current_materials}, {sources}"
    record = await registry.update_prompt("letter_drafting.v2.strategy", template_str, "admin-1")
    
    assert record.prompt_key == "letter_drafting.v2.strategy"
    assert record.version == 1
    assert record.enabled is True
    # Verify ensure_strategy_roadmap was automatically applied
    assert "Required strategic-plan roadmap" in record.template
    
    # 2. Add second version and confirm incremental versioning and legacy disablement
    new_template = template_str + "\nAdded custom rules."
    record2 = await registry.update_prompt("letter_drafting.v2.strategy", new_template, "admin-1")
    
    assert record2.version == 2
    assert record2.enabled is True
    assert db.prompt_templates.docs[0]["enabled"] is False
    assert db.prompt_templates.docs[1]["enabled"] is True
    
    # 3. Retrieve enabled and verify version 2 is loaded
    active = await registry.get_enabled("letter_drafting.v2.strategy")
    assert active.version == 2
    assert "Added custom rules" in active.template


async def test_context_pack_gzip_compression() -> None:
    db = _FakeDB()
    repo = DraftRunRepository(db)
    
    pack = DraftContextPack(
        context_pack_id="pack-1",
        letter_id="letter-1",
        run_id="run-1",
        facts=["Fact 1", "Fact 2"],
    )
    
    saved = await repo.create_context_pack(pack)
    assert saved.context_pack_id == "pack-1"
    
    # Assert that stored in fake DB is compressed (contains compressed_data)
    stored_doc = db.draft_context_packs.docs[0]
    assert "compressed_data" in stored_doc
    assert "facts" not in stored_doc
    
    # Fetch and verify it is decompressed successfully
    fetched = await repo.get_context_pack("letter-1", "run-1")
    assert fetched is not None
    assert fetched.context_pack_id == "pack-1"
    assert fetched.facts == ["Fact 1", "Fact 2"]


async def test_scan_and_alert_assignments_logic() -> None:
    db = _FakeDB()
    
    # Setup mock users
    await db.users.insert_one({"_id": "user-drafter", "email": "drafter@example.com", "first_name": "Drafter"})
    await db.users.insert_one({"_id": "user-reviewer", "email": "reviewer@example.com", "first_name": "Reviewer"})
    
    # Setup mock letter with a drafter who has not been notified yet
    await db.letters.insert_one({
        "_id": ObjectId(),
        "assigned_to": "user-drafter",
        "last_notified_drafter": None,
        "subject": "Delay Notice",
    })
    
    # Setup mock reviewer assignment that has not been notified yet
    from datetime import datetime, timedelta, timezone
    due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.letter_draft_assignments.insert_one({
        "_id": ObjectId(),
        "letter_id": "letter-1",
        "run_id": "run-1",
        "reviewer_user_id": "user-reviewer",
        "status": "assigned",
        "due_at": due_at,
        "notified": False,
        "overdue_notified": False,
    })
    
    # Mock EmailService._send and SMTP configuration
    from rbac_backend.services.background_jobs import scan_and_alert_assignments
    from rbac_backend.services.email_service import EmailService
    
    dispatched_emails = []
    
    async def mock_send(self, msg, config=None):
        dispatched_emails.append(msg)
        return True
        
    EmailService._send = mock_send
    EmailService._smtp_enabled = property(lambda self: True)
    
    await scan_and_alert_assignments(db)
    
    # Check that drafter and reviewer alert emails were sent
    assert len(dispatched_emails) >= 2
    
    # Check that flags are updated
    updated_letter = db.letters.docs[0]
    assert updated_letter["last_notified_drafter"] == "user-drafter"
    
    updated_assignment = db.letter_draft_assignments.docs[0]
    assert updated_assignment["notified"] is True
    assert updated_assignment["overdue_notified"] is True
