import asyncio
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.rbac_backend.core.config import settings
from backend.rbac_backend.models.arbitration_drafting import (
    ArbitrationAgentRunRequest,
    ArbitrationDraftCreate,
    ArbitrationDisputeType,
    ArbitrationGenerateRequest,
    ArbitrationMatrixRowCreate,
    ArbitrationMatrixRowUpdate,
    ArbitrationMatrixReviewRequest,
    ArbitrationParagraphResponseUpdate,
    ArbitrationPartyRole,
    ArbitrationDraftType,
    ArbitrationWorkflowApprovalRequest,
    ArbitrationWorkflowFallbackRequest,
    ArbitrationWorkflowResumeRequest,
    GenerationRunType,
    ArbitrationProductionAcceptanceRequest,
)
from backend.rbac_backend.services.arbitration_drafting.acceptance import (
    acceptance_hash,
    resolve_server_backed_acceptance,
    sign_acceptance,
    verify_acceptance_receipt,
)
from backend.rbac_backend.services.arbitration_drafting import case_workspace as case_workspace_module
from backend.rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from backend.rbac_backend.services.arbitration_drafting.generator import ArbitrationDraftGenerator
from backend.rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from backend.rbac_backend.services.arbitration_drafting.agents import (
    LLM_FALLBACK_WARNING,
    agent_run_metadata,
    resolve_agent_mode,
    run_arbitration_agent,
)
from backend.rbac_backend.services.arbitration_drafting.agents import llm as llm_agents_module
from backend.rbac_backend.models.arbitration_drafting import ArbitrationSelectedReferenceCreate
from backend.rbac_backend.services.arbitration_drafting.service import (
    ArbitrationDraftingService,
    immutable_version_hash,
    stable_generation_input_hash,
)
from backend.rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator
from backend.rbac_backend.services.arbitration_drafting.engines.policy import (
    ArbitrationEngineSelector,
    canonical_workflow_request_hash,
)
from backend.rbac_backend.services.arbitration_drafting.engines.v2 import ArbitrationV2WorkflowEngine
from backend.rbac_backend.services.arbitration_drafting.langgraph_engine import (
    LangGraphArbitrationEngine,
    build_arbitration_graph,
    redact_checkpoint,
    validate_checkpoint_state,
)
from backend.rbac_backend.services.arbitration_drafting.graph_commands import classify_retry
from backend.rbac_backend.services.arbitration_drafting.workflow_domain import (
    ANALYSIS_BRANCHES,
    ANALYSIS_NODE_BRANCHES,
    ArbitrationWorkflowDomain,
)
from backend.rbac_backend.services.arbitration_drafting.workflow_repository import ArbitrationWorkflowRepository
from backend.rbac_backend.services.arbitration_drafting.workflow_service import ArbitrationWorkflowService
from backend.rbac_backend.services.arbitration_drafting.approval_policy import (
    enforce_author_approver_separation,
    enforce_gate_role,
)
from backend.rbac_backend.services.arbitration_drafting.workflow_validation import (
    VALIDATION_BRANCHES,
    ArbitrationValidationOrchestrator,
)
from backend.rbac_backend.services.arbitration_drafting.workflow_hardening import (
    SHADOW_DIMENSIONS,
    build_rollout_health,
    build_shadow_comparison,
)
from backend.rbac_backend.services.arbitration_drafting.historical_paragraph_review import (
    classify_historical_row,
)
from backend.rbac_backend.models.arbitration_drafting import ArbitrationWorkflowCreateRequest


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None):
        query = query or {}
        return _FakeCursor([row for row in self.rows if self._matches(row, query)])

    async def find_one(self, query=None, *args, **kwargs):
        rows = await self.find(query).to_list()
        sort = kwargs.get("sort")
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda row: row.get(key) or 0, reverse=direction < 0)
        return rows[0] if rows else None

    async def insert_one(self, row):
        self.rows.append(dict(row))
        return type("InsertOneResult", (), {"inserted_id": row.get("_id")})()

    async def insert_many(self, rows):
        self.rows.extend([dict(row) for row in rows])
        return type("InsertManyResult", (), {"inserted_ids": [row.get("_id") for row in rows]})()

    async def delete_many(self, query=None):
        query = query or {}
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self._matches(row, query)]
        return type("DeleteResult", (), {"deleted_count": before - len(self.rows)})()

    async def update_one(self, query=None, update=None, *args, **kwargs):
        row = await self.find_one(query)
        inserted = False
        if row is None and kwargs.get("upsert"):
            row = dict(query or {})
            row.update((update or {}).get("$setOnInsert") or {})
            self.rows.append(row)
            inserted = True
        if row and update and "$set" in update:
            row.update(update["$set"])
        if row and update and "$max" in update:
            for key, value in update["$max"].items():
                row[key] = max(int(row.get(key) or 0), int(value))
        return type(
            "UpdateResult",
            (),
            {
                "matched_count": 0 if inserted else (1 if row else 0),
                "modified_count": 0 if inserted else (1 if row else 0),
                "upserted_id": row.get("_id") if inserted else None,
            },
        )()

    async def update_many(self, query=None, update=None, *args, **kwargs):
        matched = 0
        for row in self.rows:
            if self._matches(row, query or {}):
                matched += 1
                if update and "$set" in update:
                    row.update(update["$set"])
        return type("UpdateResult", (), {"matched_count": matched, "modified_count": matched})()

    async def find_one_and_update(self, query=None, update=None, *args, **kwargs):
        row = await self.find_one(query)
        if row is None and kwargs.get("upsert"):
            row = dict(query or {})
            row.update((update or {}).get("$setOnInsert") or {})
            self.rows.append(row)
        if row and update and "$set" in update:
            row.update(update["$set"])
        if row and update and "$inc" in update:
            for key, value in update["$inc"].items():
                row[key] = int(row.get(key) or 0) + int(value)
        return row

    def _matches(self, row, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(self._matches(row, branch) for branch in expected):
                    return False
                continue
            if isinstance(expected, dict) and "$exists" in expected:
                if bool(expected["$exists"]) != (key in row):
                    return False
                continue
            actual = row.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
                continue
            if isinstance(expected, dict) and "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
                continue
            if isinstance(expected, dict) and "$nin" in expected:
                if actual in expected["$nin"]:
                    return False
                continue
            if isinstance(expected, dict) and "$lte" in expected:
                if actual is None or actual > expected["$lte"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


class _FakeDb:
    def __init__(self):
        self.arbitration_cases = _FakeCollection(
            [
                {
                    "_id": "case-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "contract-1",
                    "title": "EOT claim",
                    "party_perspective": "claimant",
                    "case_summary": "Delay and prolongation claim arising from late access.",
                    "arbitration_clause": "Disputes shall be referred to arbitration.",
                }
            ]
        )
        self.arbitration_document_index = _FakeCollection(
            [
                {
                    "_id": "doc-row-1",
                    "case_id": "case-1",
                    "source_id": "doc-1",
                    "source_type": "document",
                    "title": "Delay notice",
                    "exhibit_id": "C-1",
                    "relevance_note": "Notice records late access and delay.",
                    "verification_status": "verified",
                    "approval_status": "approved",
                    "source_file_link": "/documents/doc-1",
                },
                {
                    "_id": "doc-row-2",
                    "case_id": "case-1",
                    "source_id": "doc-review",
                    "source_type": "document",
                    "title": "Unreviewed note",
                    "exhibit_id": "C-2",
                    "verification_status": "needs_review",
                },
            ]
        )
        self.arbitration_chronology_matrix = _FakeCollection(
            [
                {
                    "_id": "chrono-row-1",
                    "case_id": "case-1",
                    "chronology_id": "chron-1",
                    "chronology_event_id": "event-1",
                    "event": "Access was handed over late.",
                    "document_ref": "C-1",
                    "verification_status": "verified",
                    "pleading_use": "soc_breach",
                }
            ]
        )
        self.matter_chronology_events = _FakeCollection(
            [
                {
                    "_id": "event-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "chronology_id": "chron-1",
                    "title": "Late access",
                    "description": "Access was handed over after the planned start.",
                    "verification_status": "verified",
                    "source_page": 2,
                }
            ]
        )
        self.arbitration_clause_matrix = _FakeCollection(
            [
                {
                    "_id": "clause-row-1",
                    "case_id": "case-1",
                    "topic": "Site access",
                    "clause_number": "Clause 2.1",
                    "clause_text_excerpt": "Employer shall give access to site.",
                    "approval_status": "approved",
                }
            ]
        )
        self.arbitration_issue_matrix = _FakeCollection([])
        self.arbitration_claim_matrix = _FakeCollection([])
        self.arbitration_defence_matrix = _FakeCollection([])
        self.arbitration_counterclaim_matrix = _FakeCollection([])
        self.arbitration_rejoinder_matrix = _FakeCollection([])
        self.arbitration_quantum_annexures = _FakeCollection(
            [
                {
                    "_id": "quantum-row-1",
                    "case_id": "case-1",
                    "calculation_id": "Q-1",
                    "calculation_type": "prolongation",
                    "amount": 1000000,
                    "currency": "INR",
                    "formula": "Delay days x preliminaries rate",
                    "approval_status": "approved",
                }
            ]
        )
        self.arbitration_notice_compliance = _FakeCollection([])
        self.arbitration_jurisdiction_matrix = _FakeCollection([])
        self.arbitration_expert_alignment = _FakeCollection([])
        self.arbitration_readiness_checks = _FakeCollection([])
        self.arbitration_agent_runs = _FakeCollection([])
        self.arbitration_bundle_exports = _FakeCollection([])
        self.arbitration_workflow_approvals = _FakeCollection([])
        self.arbitration_export_authorizations = _FakeCollection([])
        self.arbitration_workflow_runs = _FakeCollection([])
        self.arbitration_workflow_snapshots = _FakeCollection([])
        self.arbitration_workflow_effects = _FakeCollection([])
        self.arbitration_workflow_events = _FakeCollection([])
        self.arbitration_plans = _FakeCollection([])
        self.arbitration_production_acceptance_receipts = _FakeCollection([])
        self.arbitration_acceptance_evidence = _FakeCollection([])
        self.arbitration_acceptance_signoffs = _FakeCollection([])
        self.arbitration_analysis_leases = _FakeCollection([])
        self.arbitration_drafts = _FakeCollection(
            [
                {
                    "_id": "draft-1",
                    "case_id": "case-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "contract-1",
                    "draft_type": "statement_of_claim",
                    "title": "EOT Statement of Claim",
                    "status": "draft",
                    "updated_at": "2026-07-01T00:00:00",
                }
            ]
        )
        self.arbitration_draft_versions = _FakeCollection(
            [
                {
                    "_id": "version-1",
                    "draft_id": "draft-1",
                    "version": 1,
                    "full_markdown": "# EOT Statement of Claim\n\nRelies on C-1 and [S1: Delay notice].\n",
                    "structured_output": {"source_hashes": ["abc"]},
                    "source_ledger": [{"source_key": "S1", "source_id": "doc-1", "citation": "Delay notice"}],
                }
            ]
        )
        self.arbitration_draft_version_counters = _FakeCollection([])
        self.arbitration_selected_references = _FakeCollection([])
        self.arbitration_claim_heads = _FakeCollection([])
        self.arbitration_paragraph_responses = _FakeCollection([])
        self.arbitration_generation_runs = _FakeCollection([])
        self.tasks = _FakeCollection([])
        self.documents = _FakeCollection(
            [
                {
                    "_id": "doc-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "subject": "Delay notice",
                    "filename": "delay-notice.txt",
                    "filepath_local": __file__,
                },
                {
                    "_id": "doc-review",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "subject": "Unreviewed note",
                    "filename": "unreviewed-note.txt",
                    "summary": "Authoritative source awaiting matrix review.",
                },
                {
                    "_id": "doc-evidence-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "subject": "Authoritative site access letter",
                    "filename": "site-access.txt",
                    "summary": "Authoritative record of late access.",
                },
                {
                    "_id": "doc-agent-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "contract-1",
                    "subject": "Notice of delay due to late access",
                    "letter_no": "LTR-001",
                    "document_date": "2025-04-08",
                    "from_company": "Contractor",
                    "to_company": "Employer",
                    "summary": "Contractor notified delay caused by late access.",
                    "file_url": "/documents/doc-agent-1",
                }
            ]
        )
        self.contract_clauses = _FakeCollection([])
        self.document_vectors = _FakeCollection(
            [
                {
                    "_id": "vec-clause-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "contract-1",
                    "clause_number": "Clause 2.1",
                    "text": "Clause 2.1: Employer shall give access to site in accordance with the programme.",
                    "title": "Site access",
                }
            ]
        )
        self.audit_events = _FakeCollection([])
        self.claims = _FakeCollection(
            [
                {
                    "_id": "claim-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "title": "EOT claim",
                    "claim_ref": "CL-001",
                    "status": "submitted",
                    "amount_claimed": 1000000,
                    "currency": "INR",
                    "contract_clauses": ["Clause 2.1"],
                }
            ]
        )
        self.variations = _FakeCollection([])
        self.ipc_bills = _FakeCollection([])
        self.bank_guarantees = _FakeCollection([])

    def __getitem__(self, name):
        return getattr(self, name)


class _FakeUser:
    def __init__(self, user_id="user-1", *, roles=None):
        self.id = user_id
        self.email = f"{user_id}@example.test"
        self.roles = roles or [
            "legal_reviewer",
            "senior_legal_approver",
            "contract_reviewer",
            "delay_reviewer",
            "quantum_reviewer",
            "export_authorizer",
        ]


def _authorize_filing_fixture(db: _FakeDb) -> None:
    service = ArbitrationCaseWorkspaceService(db)
    version = db.arbitration_draft_versions.rows[0]
    version.update({"validation_status": "passed", "missing_evidence": [], "sections": [{"key": "body", "heading": "Body", "body": version["full_markdown"]}]})
    version["version_hash"] = immutable_version_hash(version)
    draft = db.arbitration_drafts.rows[0]
    draft.update(
        {
            "status": "approved",
            "is_locked": True,
            "approved_version_id": version["_id"],
            "approved_version": version["version"],
            "approved_version_hash": version["version_hash"],
        }
    )
    artifact = asyncio.run(service._readiness_artifact_state(db.arbitration_cases.rows[0], "statement_of_claim"))
    db.arbitration_workflow_approvals.rows.append(
        {
            "_id": "readiness-receipt-1",
            "gate": "readiness",
            "case_id": "case-1",
            "draft_type": "statement_of_claim",
            "matrix_revision_hash": artifact["matrix_revision_hash"],
            "evidence_snapshot_hash": artifact["evidence_snapshot_hash"],
            "artifact_hash": artifact["artifact_hash"],
            "receipt_status": "committed",
            "approved_at": "2026-07-21T00:00:00",
        }
    )
    run = {
        "_id": "workflow-filing-1",
        "case_id": "case-1",
        "draft_id": draft["_id"],
        "pleading_type": "statement_of_claim",
        "status": "completed",
        "current_node": "complete",
        "draft_version_id": version["_id"],
        "draft_version_hash": version["version_hash"],
        "readiness_artifact_hash": artifact["artifact_hash"],
        "plan_hash": "approved-plan-hash",
        "updated_at": "2026-07-21T00:00:00",
    }
    db.arbitration_workflow_runs.rows.append(run)
    gate_hashes = {
        "readiness": artifact["artifact_hash"],
        "plan": run["plan_hash"],
        "legal_review": version["version_hash"],
        "draft": version["version_hash"],
        "export": version["version_hash"],
    }
    db.arbitration_workflow_approvals.rows.extend(
        [
            {
                "_id": f"workflow-{gate}-receipt",
                "run_id": run["_id"],
                "case_id": "case-1",
                "draft_id": draft["_id"],
                "gate": gate,
                "artifact_hash": artifact_hash,
                "decision": "approved",
                "receipt_status": "committed",
            }
            for gate, artifact_hash in gate_hashes.items()
        ]
    )


def test_arbitration_draft_create_accepts_rejoinder_payload():
    payload = ArbitrationDraftCreate(
        organization_id="org-1",
        project_id="project-1",
        draft_type=ArbitrationDraftType.REJOINDER,
        party_role=ArbitrationPartyRole.CLAIMANT,
        dispute_type=ArbitrationDisputeType.EOT_DELAY,
        title="Reply to Statement of Defence",
    )

    assert payload.draft_type == "rejoinder"
    assert payload.party_role == "claimant"


def test_agent_auto_approve_and_unknown_options_are_rejected():
    with pytest.raises(ValueError, match="auto_approve is prohibited"):
        ArbitrationAgentRunRequest(options={"auto_approve": True})
    with pytest.raises(ValueError, match="Unsupported arbitration agent options"):
        ArbitrationAgentRunRequest(options={"approve_everything": True})


def test_matrix_review_fields_are_rejected_and_server_defaults_to_needs_review():
    with pytest.raises(ValueError, match="server-controlled"):
        ArbitrationMatrixRowCreate(approval_status="approved")
    with pytest.raises(ValueError, match="server-controlled"):
        ArbitrationMatrixRowUpdate(readiness_status="ready")

    db = _FakeDb()
    row = asyncio.run(
        ArbitrationCaseWorkspaceService(db).create_matrix_row(
            "case-1",
            "issue-matrix",
            ArbitrationMatrixRowCreate(issue="Whether EOT is due"),
            _FakeUser(),
        )
    )
    assert row["approval_status"] == "needs_review"
    assert row["verification_status"] == "needs_review"
    assert row["readiness_status"] == "needs_review"
    assert row["status"] == "needs_review"


def test_prepare_from_case_copies_only_approved_matrix_rows():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.extend(
        [
            {
                "_id": "claim-approved",
                "case_id": "case-1",
                "claim_head": "Approved EOT claim",
                "approval_status": "approved",
                "readiness_status": "ready",
            },
            {
                "_id": "claim-review",
                "case_id": "case-1",
                "claim_head": "Unreviewed cost claim",
                "approval_status": "needs_review",
            },
        ]
    )
    result = asyncio.run(ArbitrationCaseWorkspaceService(db).prepare_draft_from_case("draft-1", _FakeUser()))
    assert result["references_added"] == 2  # approved document + approved clause
    assert result["claim_heads_added"] == 1
    assert db.arbitration_claim_heads.rows[0]["description"] == "Approved EOT claim"
    assert all(ref.get("source_id") != "doc-review" for ref in db.arbitration_selected_references.rows)


def test_matrix_change_invalidates_revision_bound_readiness_receipt():
    db = _FakeDb()
    service = ArbitrationCaseWorkspaceService(db)
    artifact = asyncio.run(service._readiness_artifact_state(db.arbitration_cases.rows[0], "statement_of_claim"))
    db.arbitration_workflow_approvals.rows.append(
        {
            "_id": "receipt-current",
            "gate": "readiness",
            "case_id": "case-1",
            "draft_type": "statement_of_claim",
            **artifact,
            "approved_at": "2026-07-21T00:00:00",
        }
    )
    db.arbitration_workflow_approvals.rows.extend(
        [
            {
                "_id": "matrix-gate-receipt",
                "run_id": "run-drift",
                "case_id": "case-1",
                "gate": "matrix_review",
                "artifact_hash": "matrix-hash-old",
            },
            {
                "_id": "plan-gate-receipt",
                "run_id": "run-drift",
                "case_id": "case-1",
                "gate": "plan",
                "artifact_hash": "plan-hash-old",
            },
        ]
    )
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-drift",
            "case_id": "case-1",
            "status": "awaiting_plan_approval",
            "current_node": "plan_approval_gate",
            "next_action": "approve_plan",
            "state_version": 7,
            "matrix_review_approval_receipt_id": "matrix-gate-receipt",
            "readiness_approval_receipt_id": "receipt-current",
            "plan_approval_receipt_id": "plan-gate-receipt",
            "matrix_revision_set_id": "mrs-old",
            "matrix_revision_hash": "matrix-hash-old",
            "analysis_artifact_set_id": "analysis-old",
            "analysis_artifact_set_hash": "analysis-hash-old",
        }
    )
    asyncio.run(
        service.create_matrix_row(
            "case-1",
            "issue-matrix",
            ArbitrationMatrixRowCreate(issue="New material issue"),
            _FakeUser(),
        )
    )
    receipt = db.arbitration_workflow_approvals.rows[0]
    assert receipt["invalidated_at"] is not None
    assert receipt["invalidation_reason"] == "matrix_dependency_changed"
    assert db.arbitration_cases.rows[0]["readiness_approval_receipt_id"] is None
    run = db.arbitration_workflow_runs.rows[0]
    assert run["state_version"] == 8
    assert run["current_node"] == "matrix_review_gate"
    assert run["status"] == "awaiting_matrix_review"
    assert run["matrix_review_approval_receipt_id"] is None
    assert run["readiness_approval_receipt_id"] is None
    assert run["plan_approval_receipt_id"] is None
    assert run["analysis_artifact_set_hash"] is None
    assert db.arbitration_workflow_events.rows[-1]["event_type"] == "workflow_dependencies_invalidated"


def test_standalone_draft_can_preview_but_cannot_be_approved_or_filed():
    db = _FakeDb()
    db.arbitration_drafts.rows[0]["case_id"] = None
    service = ArbitrationDraftingService(db)
    preview = asyncio.run(service.preview_export("draft-1", "docx", _FakeUser()))
    with zipfile.ZipFile(io.BytesIO(preview), "r") as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "DRAFT — NOT APPROVED FOR FILING" in document_xml
    with pytest.raises(HTTPException) as approve_error:
        asyncio.run(service.approve("draft-1", _FakeUser()))
    assert approve_error.value.status_code == 409
    with pytest.raises(HTTPException) as export_error:
        asyncio.run(service.export("draft-1", "docx", _FakeUser()))
    assert export_error.value.status_code == 409


def test_section_regeneration_preserves_complete_parent_and_lineage():
    db = _FakeDb()
    service = ArbitrationDraftingService(db)
    context = asyncio.run(service._context("draft-1", _FakeUser()))
    parent_generated = service.generator.generate(context)
    parent = db.arbitration_draft_versions.rows[0]
    parent.update(
        {
            "sections": parent_generated["sections"],
            "full_markdown": parent_generated["full_markdown"],
            "source_ledger": context["source_ledger"],
            "missing_evidence": parent_generated["missing_evidence"],
            "annexures": parent_generated["annexures"],
            "validation_status": "needs_review",
        }
    )
    parent_keys = [section["key"] for section in parent["sections"]]

    async def allow_generation(*args, **kwargs):
        return None

    service.case_workspace.assert_case_ready_for_draft = allow_generation
    result = asyncio.run(
        service.generate(
            "draft-1",
            ArbitrationGenerateRequest(section_key="introduction", additional_instruction="Clarify the summary."),
            _FakeUser(),
            run_type=GenerationRunType.SECTION_REGENERATION,
        )
    )
    latest = result["latest_version"]
    assert [section["key"] for section in latest["sections"]] == parent_keys
    assert latest["parent_version_id"] == parent["_id"]
    assert latest["parent_version"] == parent["version"]
    run = db.arbitration_generation_runs.rows[-1]
    assert run["run_type"] == "section_regeneration"


def test_rejoinder_generator_requires_imported_sod_paragraphs():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "rejoinder",
            "title": "Reply to Statement of Defence",
            "relief_sought": "Dismiss all defences.",
        },
        "source_ledger": [],
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": ["Statement of Defence paragraphs must be imported."],
    }

    generated = ArbitrationDraftGenerator().generate(context)

    assert "Paragraph-by-Paragraph Reply to the Statement of Defence" in generated["full_markdown"]
    assert "[Evidence required]" in generated["full_markdown"]
    warnings = ArbitrationDraftValidator().validate_generated(context, generated)
    assert "Rejoinder requires imported SoD paragraph responses." in warnings


def test_statement_of_claim_generator_preserves_source_citations():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "manual_facts": "Basement drawings were issued late.",
            "relief_sought": "Award extension of time.",
        },
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "doc-1",
                "source_type": "document",
                "allowed_use": "fact",
                "label": "Delay notice",
                "citation": "CPL/2025/0142",
                "snippet": "Notice of delay due to late issue of Basement 2 Rev C drawing.",
            }
        ],
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
        "pleading_plan": {
            "_id": "plan-1",
            "plan_hash": "approved-plan-hash",
            "section_structure": [{"order": 1, "key": "introduction"}, {"order": 2, "key": "claims"}],
        },
    }

    generated = ArbitrationDraftGenerator().generate(context)

    assert "[S1: CPL/2025/0142]" in generated["full_markdown"]
    assert "Award extension of time." in generated["full_markdown"]
    assert generated["structured_output"]["pleading_plan_hash"] == "approved-plan-hash"
    assert generated["structured_output"]["planned_section_keys"] == ["introduction", "claims"]


def test_statement_of_claim_generator_uses_case_matrix_context():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "relief_sought": "Award extension of time and prolongation costs.",
        },
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "claim-row-1",
                "source_type": "claim_matrix",
                "source_origin": "case_claim-matrix",
                "label": "EOT claim",
                "citation": "CL-001",
                "snippet": "Late access caused critical delay and prolongation costs.",
                "clause_number": "Clause 2.1",
                "metadata": {"amount_or_days": "INR 1000000"},
                "source_hash": "claim-hash",
            },
            {
                "source_key": "S2",
                "source_id": "Q-CLAIMS-CL-001",
                "source_type": "quantum_annexure",
                "source_origin": "case_quantum_annexure",
                "label": "claim_summary",
                "citation": "Q-CLAIMS-CL-001",
                "snippet": "Amount taken from source register pending reviewer validation. INR 1000000",
                "source_hash": "quantum-hash",
            },
        ],
        "matrix_context": {
            "claims": [
                {
                    "source_key": "S1",
                    "label": "EOT claim",
                    "citation": "CL-001",
                    "snippet": "Late access caused critical delay and prolongation costs.",
                    "clause_number": "Clause 2.1",
                    "metadata": {"amount_or_days": "INR 1000000"},
                }
            ],
            "quantum": [
                {
                    "source_key": "S2",
                    "label": "claim_summary",
                    "citation": "Q-CLAIMS-CL-001",
                    "snippet": "Amount taken from source register pending reviewer validation. INR 1000000",
                }
            ],
        },
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context)

    assert "Late access caused critical delay and prolongation costs. [S1: CL-001]" in generated["full_markdown"]
    assert "Approved quantum/calculation annexures" in generated["full_markdown"]
    assert "[S2: Q-CLAIMS-CL-001]" in generated["full_markdown"]
    assert generated["structured_output"]["matrix_context_counts"]["claims"] == 1


def test_validator_blocks_unknown_source_citation_amount_and_date():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "manual_facts": "The delay notice was sent on 2025-04-08.",
        },
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "doc-1",
                "source_type": "document",
                "citation": "CPL/2025/0142",
                "snippet": "The delay notice was sent on 2025-04-08.",
            }
        ],
        "paragraph_responses": [],
    }
    markdown = "The unsupported amount is INR 5,000,000 on 2025-05-10 [S9: Missing]."

    report = ArbitrationDraftValidator().validation_report(context, markdown)

    assert any("S9" in item for item in report["approval_blockers"])
    assert any("INR 5,000,000" in item for item in report["approval_blockers"])
    assert any("2025-05-10" in item for item in report["approval_blockers"])


def test_statement_of_defence_validator_requires_imported_soc_paragraphs():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "statement_of_defence", "title": "SoD"},
        "source_ledger": [{"source_key": "S1", "source_id": "doc-1", "citation": "SOC", "snippet": "Statement of Claim"}],
        "paragraph_responses": [],
    }

    report = ArbitrationDraftValidator().validation_report(context, "Respondent denies the claim. [S1: SOC]")

    assert "Statement of Defence requires imported SoC paragraph responses." in report["warnings"]


def test_paragraph_denial_without_support_is_marked_evidence_required():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "rejoinder", "title": "Reply"},
        "source_ledger": [],
        "claim_heads": [],
        "paragraph_responses": [
            {
                "source_paragraph_number": "4",
                "response_type": "deny",
                "response_text": "Denied as misleading.",
                "supporting_source_ids": [],
            }
        ],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context, section_key="paragraph_replies")

    assert "4. Deny: Denied as misleading. [Evidence required]" in generated["full_markdown"]


def test_paragraph_denial_with_support_preserves_source_citation():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "rejoinder", "title": "Reply"},
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "letter-1",
                "source_type": "letter",
                "citation": "CPL/2025/0142",
                "snippet": "Late drawing notice.",
                "source_hash": "abc",
            }
        ],
        "claim_heads": [],
        "paragraph_responses": [
            {
                "source_paragraph_number": "4",
                "response_type": "deny",
                "response_text": "Denied as misleading.",
                "supporting_source_ids": ["letter-1"],
            }
        ],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context, section_key="paragraph_replies")

    assert "4. Deny: Denied as misleading. [S1: CPL/2025/0142]" in generated["full_markdown"]


def test_rejoinder_generator_uses_rejoinder_matrix_when_paragraphs_not_imported():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "rejoinder", "title": "Reply"},
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "rejoinder-row-1",
                "source_type": "rejoinder_matrix",
                "source_origin": "case_rejoinder-matrix",
                "label": "Delay defence reply",
                "citation": "SoD para 12",
                "snippet": "The alleged contractor delay is denied because access was handed over late.",
                "metadata": {"new_matter": False},
                "source_hash": "rejoinder-hash",
            }
        ],
        "matrix_context": {
            "rejoinder_replies": [
                {
                    "source_key": "S1",
                    "label": "Delay defence reply",
                    "citation": "SoD para 12",
                    "snippet": "The alleged contractor delay is denied because access was handed over late.",
                    "metadata": {"new_matter": False},
                }
            ]
        },
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context, section_key="paragraph_replies")

    assert "Delay defence reply" in generated["full_markdown"]
    assert "[S1: SoD para 12]" in generated["full_markdown"]


def test_generation_input_hash_is_stable_across_runtime_fields():
    base_context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "manual_facts": "Basement drawings were issued late.",
            "updated_at": "2026-01-01T00:00:00",
            "latest_generation_run_id": "run-1",
        },
        "source_ledger": [{"source_hash": "abc"}],
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
    }
    same_inputs = {
        **base_context,
        "draft": {
            **base_context["draft"],
            "updated_at": "2026-01-02T00:00:00",
            "latest_generation_run_id": "run-2",
        },
    }
    changed_inputs = {
        **base_context,
        "draft": {
            **base_context["draft"],
            "manual_facts": "Basement drawings were issued late and access was restricted.",
        },
    }
    planned_inputs = {
        **base_context,
        "pleading_plan": {"_id": "plan-1", "plan_hash": "approved-plan-hash"},
    }

    assert stable_generation_input_hash(base_context) == stable_generation_input_hash(same_inputs)
    assert stable_generation_input_hash(base_context) != stable_generation_input_hash(changed_inputs)
    assert stable_generation_input_hash(base_context) != stable_generation_input_hash(planned_inputs)


def test_case_workspace_sources_are_added_to_source_ledger():
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
        "claim_amount": 1000000,
    }

    context = asyncio.run(ArbitrationContextBuilder(_FakeDb()).build(draft, [], [], [], object()))

    origins = {row.get("source_origin") for row in context["source_ledger"]}
    assert "case_document_index" in origins
    assert "case_chronology_matrix" in origins
    assert "case_clause_matrix" in origins
    assert "case_quantum_annexure" in origins
    assert "claim_register" in origins
    assert all(row.get("source_id") != "doc-review" for row in context["source_ledger"])
    assert any(row.get("allowed_use") == "quantum" for row in context["source_ledger"])
    assert "Claim amount is entered but no quantum/payment source is selected." not in context["missing_evidence"]


def test_review_mode_can_include_unverified_case_sources_with_warning():
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
    }

    context = asyncio.run(
        ArbitrationContextBuilder(_FakeDb()).build(
            draft,
            [],
            [],
            [],
            object(),
            include_unverified_graph_links=True,
        )
    )

    assert any(row.get("source_id") == "doc-review" for row in context["source_ledger"])
    assert any("Review-only document index source included" in warning for warning in context["context_warnings"])


def test_context_builder_groups_case_matrix_sources_for_generation():
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
    }

    context = asyncio.run(ArbitrationContextBuilder(_FakeDb()).build(draft, [], [], [], object()))

    assert context["matrix_context"]["documents"]
    assert context["matrix_context"]["chronology"]
    assert context["matrix_context"]["clauses"]
    assert context["matrix_context"]["quantum"]


def test_orchestrator_creates_traceable_matrix_rows_and_is_idempotent():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]
    payload = ArbitrationAgentRunRequest()

    first = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "orchestrator",
            payload=payload,
            current_user=_FakeUser(),
        )
    )
    second = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "orchestrator",
            payload=payload,
            current_user=_FakeUser(),
        )
    )

    created_matrices = {record["matrix"] for record in first["created_records"]}
    assert "document-index" in created_matrices
    assert "clause-matrix" in created_matrices
    assert "claim-matrix" in created_matrices
    assert "quantum-annexures" in created_matrices
    assert "notice-compliance" in created_matrices
    assert "issue-matrix" in created_matrices
    assert any(row.get("source_id") == "doc-agent-1" for row in db.arbitration_document_index.rows)
    assert any(row.get("source_claim_id") == "claim-1" for row in db.arbitration_claim_matrix.rows)
    assert any(row.get("calculation_id") == "Q-CLAIMS-CL-001" for row in db.arbitration_quantum_annexures.rows)
    assert len(second["created_records"]) == 0


def test_case_workspace_run_agent_persists_run_and_readiness():
    db = _FakeDb()
    service = ArbitrationCaseWorkspaceService(db)

    run = asyncio.run(
        service.run_agent(
            "case-1",
            "document-indexing",
            ArbitrationAgentRunRequest(),
            _FakeUser(),
        )
    )

    assert run["status"] == "completed"
    assert run["created_records"]
    assert db.arbitration_agent_runs.rows[0]["_id"] == run["_id"]
    assert db.arbitration_cases.rows[0]["readiness_score"] >= 0
    assert db.arbitration_readiness_checks.rows

    rerun = asyncio.run(
        service.run_agent(
            "case-1",
            "document-indexing",
            ArbitrationAgentRunRequest(),
            _FakeUser(),
        )
    )

    assert rerun["input_hash"] == run["input_hash"]
    assert rerun["created_records"] == []


def test_queue_agent_run_persists_queued_status_and_background_job(monkeypatch):
    db = _FakeDb()
    service = ArbitrationCaseWorkspaceService(db)

    async def fake_submit(name, func, *args, **kwargs):
        assert name == "arbitration-agent:orchestrator"
        assert args[0] == "case-1"
        return "job-agent-1"

    monkeypatch.setattr(case_workspace_module, "submit_background_job", fake_submit)

    queued = asyncio.run(
        service.queue_agent_run(
            "case-1",
            "orchestrator",
            ArbitrationAgentRunRequest(),
            _FakeUser(),
        )
    )

    assert queued["status"] == "queued"
    assert queued["background_job_id"] == "job-agent-1"
    assert db.arbitration_agent_runs.rows[0]["status"] == "queued"


def test_execute_queued_agent_run_updates_existing_run(monkeypatch):
    db = _FakeDb()
    service = ArbitrationCaseWorkspaceService(db)

    async def fake_submit(*args, **kwargs):
        return "job-agent-2"

    monkeypatch.setattr(case_workspace_module, "submit_background_job", fake_submit)
    queued = asyncio.run(
        service.queue_agent_run(
            "case-1",
            "document-indexing",
            ArbitrationAgentRunRequest(),
            _FakeUser(),
        )
    )
    completed = asyncio.run(
        service.execute_queued_agent_run(
            "case-1",
            queued["_id"],
            "document-indexing",
            {"options": {}},
            {"id": "user-1", "email": "user@example.test"},
        )
    )

    assert completed["_id"] == queued["_id"]
    assert completed["status"] == "completed"
    assert completed["created_records"]


def test_matrix_review_assignment_creates_task_and_blocks_readiness():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(
        {
            "_id": "claim-row-review",
            "case_id": "case-1",
            "claim_no": "CL-001",
            "claim_head": "EOT claim",
            "facts": "Late access caused delay.",
            "approval_status": "approved",
            "verification_status": "verified",
            "readiness_status": "ready",
        }
    )
    service = ArbitrationCaseWorkspaceService(db)

    reviewed = asyncio.run(
        service.review_matrix_row(
            "case-1",
            "claim-matrix",
            "claim-row-review",
            ArbitrationMatrixReviewRequest(
                action="assign",
                reviewer_role="legal",
                reviewer_user_id="reviewer-1",
                required_roles=["legal", "quantum"],
                comment="Please review entitlement.",
            ),
            _FakeUser(),
        )
    )

    assert reviewed["review_status"] == "under_review"
    assert reviewed["approval_status"] == "needs_review"
    assert reviewed["review_required_roles"] == ["legal", "quantum"]
    assert reviewed["review_comments"][0]["comment"] == "Please review entitlement."
    assert db.tasks.rows[-1]["resource_type"] == "arbitration_matrix_row"
    assert db.tasks.rows[-1]["assigned_to"] == "reviewer-1"
    readiness = asyncio.run(service.readiness("case-1"))
    assert any(check["check_key"] == "matrix_human_review" for check in readiness["blockers"])


def test_matrix_review_approval_requires_all_roles_then_closes_task():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(
        {
            "_id": "claim-row-multirole",
            "case_id": "case-1",
            "claim_no": "CL-002",
            "claim_head": "Prolongation cost",
            "facts": "Late access caused prolongation.",
            "review_status": "under_review",
            "review_required_roles": ["legal", "quantum"],
            "review_completed_roles": [],
            "review_assignments": [
                {"reviewer_role": "legal", "reviewer_user_id": "user-1", "status": "assigned"},
                {"reviewer_role": "quantum", "reviewer_user_id": "user-2", "status": "assigned"},
            ],
            "approval_status": "needs_review",
            "verification_status": "needs_review",
            "readiness_status": "needs_review",
        }
    )
    db.tasks.rows.append(
        {
            "_id": "task-1",
            "resource_type": "arbitration_matrix_row",
            "resource_id": "claim-row-multirole",
            "task_type": "review",
            "status": "open",
        }
    )
    service = ArbitrationCaseWorkspaceService(db)

    partial = asyncio.run(
        service.review_matrix_row(
            "case-1",
            "claim-matrix",
            "claim-row-multirole",
            ArbitrationMatrixReviewRequest(action="approve", reviewer_role="legal", comment="Entitlement supported."),
            _FakeUser(),
        )
    )
    partial_status = partial["review_status"]
    partial_approval_status = partial["approval_status"]
    final = asyncio.run(
        service.review_matrix_row(
            "case-1",
            "claim-matrix",
            "claim-row-multirole",
            ArbitrationMatrixReviewRequest(action="approve", reviewer_role="quantum", comment="Quantum support checked."),
            _FakeUser("user-2"),
        )
    )

    assert partial_status == "partially_approved"
    assert partial_approval_status == "needs_review"
    assert final["review_status"] == "approved"
    assert final["approval_status"] == "approved"
    assert final["readiness_status"] == "ready"
    assert set(final["review_completed_roles"]) == {"legal", "quantum"}
    assert db.tasks.rows[0]["status"] == "done"


def test_filing_bundle_zip_contains_manifest_matrices_and_drafts():
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)

    content = asyncio.run(service.export_filing_bundle_zip("case-1"))

    with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        summary = archive.read("filing-bundle-summary.md").decode("utf-8")

    assert "citation-audit.json" in names
    assert "readiness.json" in names
    assert "matrices/document-index.json" in names
    assert "drafts/draft-1/latest-version.md" in names
    assert manifest["case"]["_id"] == "case-1"
    assert manifest["bundle_version"] == "arbitration-filing-bundle.v1"
    assert "Filing Bundle - EOT claim" in summary
    assert "C-1: Delay notice" in summary


def test_filing_bundle_docx_and_pdf_exports_are_real_files():
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)

    docx_content = asyncio.run(service.export_filing_bundle_docx("case-1"))
    pdf_content = asyncio.run(service.export_filing_bundle_pdf("case-1"))

    assert docx_content[:2] == b"PK"
    assert pdf_content[:4] == b"%PDF"


def test_queue_filing_bundle_export_persists_status_and_content(monkeypatch):
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)

    class _Queue:
        async def enqueue(self, *, job_id, effect_key, payload):
            assert job_id == payload["export_id"]
            assert effect_key == payload["effect_key"]
            assert payload["case_id"] == "case-1"
            return job_id

    monkeypatch.setattr(case_workspace_module, "get_filing_export_queue", lambda: _Queue())

    queued = asyncio.run(service.queue_filing_bundle_export("case-1", "zip", _FakeUser()))
    completed = asyncio.run(service.execute_filing_bundle_export_job("case-1", queued["_id"], "zip"))
    artifact = asyncio.run(service.get_filing_bundle_export_content("case-1", queued["_id"]))

    assert queued["status"] == "queued"
    assert queued["background_job_id"] == queued["_id"]
    assert queued["effect_key"].startswith("arbitration_filing_bundle:")
    assert completed["status"] == "completed"
    assert completed["content_length"] > 0
    assert artifact["content"][:2] == b"PK"
    assert artifact["filename"] == "arbitration-case-bundle.zip"


def test_filing_bundle_export_effect_is_idempotent_after_completion(monkeypatch):
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)

    class _Queue:
        async def enqueue(self, *, job_id, effect_key, payload):
            return job_id

    monkeypatch.setattr(case_workspace_module, "get_filing_export_queue", lambda: _Queue())
    queued = asyncio.run(service.queue_filing_bundle_export("case-1", "zip", _FakeUser()))
    first = asyncio.run(service.execute_filing_bundle_export_job("case-1", queued["_id"], "zip"))

    async def should_not_rebuild(*args, **kwargs):
        raise AssertionError("completed effect must not execute again")

    monkeypatch.setattr(service, "_build_filing_bundle_export_content", should_not_rebuild)
    second = asyncio.run(service.execute_filing_bundle_export_job("case-1", queued["_id"], "zip"))

    assert first["status"] == second["status"] == "completed"
    assert first["content_length"] == second["content_length"]
    assert db.arbitration_bundle_exports.rows[0]["attempts"] == 1


def test_existing_filing_export_fails_closed_when_durable_queue_is_unavailable(monkeypatch):
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)

    class _AvailableQueue:
        async def enqueue(self, *, job_id, effect_key, payload):
            return job_id

    class _UnavailableQueue:
        async def enqueue(self, *, job_id, effect_key, payload):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(case_workspace_module, "get_filing_export_queue", lambda: _AvailableQueue())
    queued = asyncio.run(service.queue_filing_bundle_export("case-1", "zip", _FakeUser()))
    authorization = dict(db.arbitration_export_authorizations.rows[0])

    async def _same_authorization(*args, **kwargs):
        return authorization

    monkeypatch.setattr(service, "_authorize_filing_bundle", _same_authorization)
    monkeypatch.setattr(case_workspace_module, "get_filing_export_queue", lambda: _UnavailableQueue())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.queue_filing_bundle_export("case-1", "zip", _FakeUser()))

    assert exc_info.value.status_code == 503
    stored = asyncio.run(db.arbitration_bundle_exports.find_one({"_id": queued["_id"]}))
    assert stored["status"] == "queue_unavailable"


class _FakeLLMGenerator:
    """Injectable stand-in for retrieval.generator.LLMGenerator."""

    response = "[]"

    def __init__(self, *args, **kwargs):
        pass

    @property
    def available(self):
        return True

    async def generate(self, prompt, max_tokens=512, model=None):
        type(self).last_prompt = prompt
        return type(self).response


def test_agent_mode_option_overrides_environment(monkeypatch):
    monkeypatch.setenv("ARBITRATION_AGENT_MODE", "llm")
    assert resolve_agent_mode({}) == "llm"
    assert resolve_agent_mode({"agent_mode": "deterministic"}) == "deterministic"
    monkeypatch.delenv("ARBITRATION_AGENT_MODE", raising=False)
    assert resolve_agent_mode({}) == "deterministic"
    assert resolve_agent_mode({"agent_mode": "bogus"}) == "deterministic"
    assert agent_run_metadata({"agent_mode": "llm"})["prompt_version"] == "arbitration-agents-llm-1"
    assert agent_run_metadata({})["model"] == "deterministic-matrix-agent"


def test_llm_agent_creates_needs_review_rows_from_mocked_output(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    class _Gen(_FakeLLMGenerator):
        response = json.dumps(
            [
                {
                    "source_id": "claim-1",
                    "claim_head": "Extension of time",
                    "facts": "Late site access delayed critical works.",
                    "causation": "Employer-caused access delay affected the critical path.",
                    "weakness": "Notice timing",
                    "relief": "Award EOT without LD",
                }
            ]
        )

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "claim-identification",
            payload=ArbitrationAgentRunRequest(options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    assert result["prompt_version"] == "arbitration-agents-llm-1"
    assert result["model"] != "deterministic-matrix-agent"
    assert result["errors"] == []
    row = next(r for r in db.arbitration_claim_matrix.rows if r.get("source_claim_id") == "claim-1")
    # LLM output can never be approved by an agent.
    assert row["approval_status"] == "needs_review"
    assert row["verification_status"] == "needs_review"
    assert row["claim_head"] == "Extension of time"
    # Amount is grounded from the claim register, not the model output.
    assert row["amount"] == 1000000.0
    assert row["currency"] == "INR"
    # Prompt contained only scoped sources keyed by id.
    assert "[id=claim-1]" in _Gen.last_prompt
    assert "Return ONLY a JSON array" in _Gen.last_prompt


def test_llm_agent_rejects_rows_citing_unknown_sources(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    class _Gen(_FakeLLMGenerator):
        response = json.dumps(
            [{"source_id": "claim-999", "claim_head": "Fabricated claim", "facts": "Invented facts."}]
        )

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "claim-identification",
            payload=ArbitrationAgentRunRequest(options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    assert result["created_records"] == []
    assert not db.arbitration_claim_matrix.rows
    assert any("unknown source id 'claim-999'" in warning for warning in result["warnings"])


def test_llm_agent_unparseable_output_warns_and_creates_nothing(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    class _Gen(_FakeLLMGenerator):
        response = "I cannot answer that question."

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "claim-identification",
            payload=ArbitrationAgentRunRequest(options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    assert result["created_records"] == []
    assert result["errors"] == []
    assert any("parseable JSON" in warning for warning in result["warnings"])


def test_llm_mode_falls_back_to_deterministic_when_unavailable(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    class _Gen(_FakeLLMGenerator):
        @property
        def available(self):
            return False

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "claim-identification",
                payload=ArbitrationAgentRunRequest(options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    assert result["model"] == "deterministic-matrix-agent"
    assert result["warnings"][0] == LLM_FALLBACK_WARNING
    assert any(row.get("source_claim_id") == "claim-1" for row in db.arbitration_claim_matrix.rows)


def test_llm_defence_analysis_rejects_blanket_denials(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]
    db.arbitration_claim_matrix.rows.extend(
        [
            {
                "_id": "claim-row-a",
                "case_id": "case-1",
                "claim_no": "CL-001",
                "claim_head": "EOT claim",
                "facts": "Late access caused delay.",
                "approval_status": "approved",
            },
            {
                "_id": "claim-row-b",
                "case_id": "case-1",
                "claim_no": "CL-002",
                "claim_head": "Prolongation cost",
                "facts": "Extended stay costs incurred.",
                "approval_status": "approved",
            },
        ]
    )

    class _Gen(_FakeLLMGenerator):
        response = json.dumps(
            [
                {"source_id": "claim-row-a", "admission_denial": "denied", "defence": ""},
                {
                    "source_id": "claim-row-b",
                    "admission_denial": "denied",
                    "defence": "No compensable delay: the claimed period overlaps contractor-caused resource shortfalls.",
                    "quantum_objection": "No actual cost records identified in the source.",
                },
            ]
        )

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "defence-analysis",
            payload=ArbitrationAgentRunRequest(options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    assert any("denial without a stated reason" in warning for warning in result["warnings"])
    defences = db.arbitration_defence_matrix.rows
    assert len(defences) == 1
    assert defences[0]["source_claim_no"] == "CL-002"
    assert defences[0]["approval_status"] == "needs_review"


def test_deterministic_defence_analysis_remains_review_only():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "defence-analysis",
            payload=ArbitrationAgentRunRequest(options={}),
            current_user=_FakeUser(),
        )
    )

    assert result["created_records"] == []
    assert not db.arbitration_defence_matrix.rows


def test_jurisdiction_agent_computes_limitation_and_seeds_prearb_steps():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "jurisdiction",
            payload=ArbitrationAgentRunRequest(
                options={"cause_of_action_date": "2020-01-10", "limitation_period_years": 3}
            ),
            current_user=_FakeUser(),
        )
    )

    rows = db.arbitration_jurisdiction_matrix.rows
    limitation = [row for row in rows if row.get("check_type") == "limitation"]
    assert limitation, "jurisdiction agent must create limitation rows"
    assert limitation[0]["limitation_status"] == "time_barred"
    assert limitation[0]["limitation_expiry_date"] == "2023-01-10"
    assert limitation[0]["limitation_period_years"] == 3
    steps = {row.get("step") for row in rows if row.get("check_type") == "pre_arbitration_step"}
    assert {"dispute_notice", "engineer_decision", "conciliation"}.issubset(steps)
    scope = [row for row in rows if row.get("check_type") == "arbitration_clause_scope"]
    assert scope and scope[0]["scope_status"] == "needs_review"
    assert result["errors"] == []

    # Idempotent on re-run.
    rerun = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "jurisdiction",
            payload=ArbitrationAgentRunRequest(
                options={"cause_of_action_date": "2020-01-10", "limitation_period_years": 3}
            ),
            current_user=_FakeUser(),
        )
    )
    assert rerun["created_records"] == []


def test_jurisdiction_agent_marks_recent_cause_of_action_within_limitation():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "jurisdiction",
            payload=ArbitrationAgentRunRequest(options={"cause_of_action_date": "2026-01-01"}),
            current_user=_FakeUser(),
        )
    )

    limitation = [row for row in db.arbitration_jurisdiction_matrix.rows if row.get("check_type") == "limitation"]
    assert limitation[0]["limitation_status"] == "within_limitation"
    assert limitation[0]["limitation_expiry_date"] == "2029-01-01"


def test_readiness_flags_missing_limitation_and_prearb_records():
    db = _FakeDb()

    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness("case-1"))

    blockers = {check["check_key"]: check["status"] for check in readiness["blockers"]}
    assert blockers.get("limitation_analysis") == "needs_legal_review"
    assert blockers.get("pre_arbitration_compliance") == "needs_legal_review"


def test_readiness_blocks_time_barred_premature_and_out_of_scope_case():
    db = _FakeDb()
    db.arbitration_jurisdiction_matrix.rows.extend(
        [
            {
                "_id": "jur-lim-1",
                "case_id": "case-1",
                "check_type": "limitation",
                "limitation_status": "time_barred",
                "approval_status": "approved",
            },
            {
                "_id": "jur-pre-1",
                "case_id": "case-1",
                "check_type": "pre_arbitration_step",
                "step": "conciliation",
                "required": True,
                "compliance_status": "pending",
                "approval_status": "approved",
            },
            {
                "_id": "jur-scope-1",
                "case_id": "case-1",
                "check_type": "arbitration_clause_scope",
                "scope_status": "out_of_scope",
                "approval_status": "approved",
            },
        ]
    )
    service = ArbitrationCaseWorkspaceService(db)

    readiness = asyncio.run(service.readiness("case-1"))

    blockers = {check["check_key"]: check["status"] for check in readiness["blockers"]}
    assert blockers.get("limitation_analysis") == "blocked"
    assert blockers.get("pre_arbitration_compliance") == "blocked"
    assert blockers.get("arbitration_clause_scope") == "blocked"

    # Acceptance: blockers prevent approve-readiness.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.approve_readiness("case-1", _FakeUser()))
    assert exc_info.value.status_code == 409


def test_readiness_passes_when_limitation_and_prearb_resolved():
    db = _FakeDb()
    db.arbitration_jurisdiction_matrix.rows.extend(
        [
            {
                "_id": "jur-lim-ok",
                "case_id": "case-1",
                "check_type": "limitation",
                "limitation_status": "within_limitation",
                "limitation_expiry_date": "2029-01-01",
                "approval_status": "approved",
            },
            {
                "_id": "jur-pre-ok",
                "case_id": "case-1",
                "check_type": "pre_arbitration_step",
                "step": "dispute_notice",
                "required": True,
                "compliance_status": "complete",
                "approval_status": "approved",
            },
            {
                "_id": "jur-pre-na",
                "case_id": "case-1",
                "check_type": "pre_arbitration_step",
                "step": "conciliation",
                "required": False,
                "compliance_status": "not_applicable",
                "approval_status": "approved",
            },
        ]
    )

    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness("case-1"))

    blocker_keys = {check["check_key"] for check in readiness["blockers"]}
    assert "limitation_analysis" not in blocker_keys
    assert "pre_arbitration_compliance" not in blocker_keys
    assert "arbitration_clause_scope" not in blocker_keys
    checks = {check["check_key"]: check["status"] for check in readiness["checks"]}
    assert checks["limitation_analysis"] == "ready"
    assert checks["pre_arbitration_compliance"] == "ready"


def _eot_claim_row(**overrides):
    row = {
        "_id": "claim-row-eot",
        "case_id": "case-1",
        "claim_no": "CL-001",
        "claim_head": "EOT and prolongation",
        "facts": "Late access caused critical path delay.",
        "amount_or_days": "INR 500000",
        "amount": 500000,
        "calculation_id": "Q-1",
        "approval_status": "approved",
        "readiness_status": "ready",
    }
    row.update(overrides)
    return row


def test_delay_expert_agent_flags_concurrency_and_amount_mismatch():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]
    db.arbitration_claim_matrix.rows.append(_eot_claim_row())
    # Existing annexure Q-1 carries 1,000,000 while the claim pleads 500,000.

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "delay-expert",
            payload=ArbitrationAgentRunRequest(options={}),
            current_user=_FakeUser(),
        )
    )

    rows = db.arbitration_expert_alignment.rows
    delay_row = next(r for r in rows if r["expert_type"] == "delay")
    assert delay_row["claim_no"] == "CL-001"
    assert delay_row["concurrency_addressed"] is False
    assert "concurrency_not_addressed" in delay_row["risk_flags"]
    assert any("Concurrency has not been addressed" in warning for warning in result["warnings"])

    quantum_row = next(r for r in rows if r["expert_type"] == "quantum")
    assert quantum_row["calculation_match"] is False
    assert quantum_row["verified_amount"] == 1000000.0
    assert "amount_mismatch" in quantum_row["risk_flags"]
    assert any("does not match quantum annexure Q-1" in item for item in quantum_row["contradictions"])
    assert any("does not match quantum annexure Q-1" in warning for warning in result["warnings"])

    rerun = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "delay-expert",
            payload=ArbitrationAgentRunRequest(options={}),
            current_user=_FakeUser(),
        )
    )
    assert rerun["created_records"] == []


def test_expert_alignment_readiness_blocks_then_ready():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(_eot_claim_row())
    service = ArbitrationCaseWorkspaceService(db)

    readiness = asyncio.run(service.readiness("case-1"))
    blockers = {check["check_key"]: check["status"] for check in readiness["blockers"]}
    assert blockers.get("expert_alignment") == "needs_legal_review"

    db.arbitration_expert_alignment.rows.extend(
        [
            {
                "_id": "exp-delay",
                "case_id": "case-1",
                "expert_type": "delay",
                "claim_no": "CL-001",
                "concurrency_addressed": True,
                "approval_status": "approved",
            },
            {
                "_id": "exp-quantum",
                "case_id": "case-1",
                "expert_type": "quantum",
                "claim_no": "CL-001",
                "calculation_match": True,
                "contradictions": [],
                "approval_status": "approved",
            },
        ]
    )

    readiness = asyncio.run(service.readiness("case-1"))
    blocker_keys = {check["check_key"] for check in readiness["blockers"]}
    assert "expert_alignment" not in blocker_keys
    checks = {check["check_key"]: check["status"] for check in readiness["checks"]}
    assert checks["expert_alignment"] == "ready"


def test_context_warns_on_expert_amount_mismatch_and_unaddressed_concurrency():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(_eot_claim_row(amount_or_days="INR 1000000", amount=1000000))
    db.arbitration_expert_alignment.rows.extend(
        [
            {
                "_id": "exp-quantum-bad",
                "case_id": "case-1",
                "expert_type": "quantum",
                "claim_no": "CL-001",
                "verified_amount": 800000,
                "calculation_match": False,
                "contradictions": ["Pleaded amount differs from the verified calculation."],
                "approval_status": "approved",
            },
            {
                "_id": "exp-delay-open",
                "case_id": "case-1",
                "expert_type": "delay",
                "claim_no": "CL-001",
                "concurrency_addressed": False,
                "approval_status": "approved",
            },
        ]
    )
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
    }

    context = asyncio.run(ArbitrationContextBuilder(db).build(draft, [], [], [], object()))

    assert context["matrix_context"]["experts"], "expert alignment rows must enter the matrix context"
    assert any(row.get("source_type") == "expert_report" for row in context["source_ledger"])
    warnings = context["context_warnings"]
    assert any(
        "Pleaded amount 1000000.0 for claim CL-001 does not match the expert-verified amount 800000.0" in item
        for item in warnings
    )
    assert any("Concurrency has not been addressed for delay claim CL-001" in item for item in warnings)
    assert any("Expert alignment contradiction" in item for item in warnings)


def test_filing_bundle_zip_embeds_exhibit_files_in_volumes(tmp_path):
    db = _FakeDb()
    _authorize_filing_fixture(db)
    exhibit_file = tmp_path / "delay-notice.pdf"
    exhibit_file.write_bytes(b"%PDF-1.4 exhibit-bytes")
    source = next(row for row in db.documents.rows if row.get("_id") == "doc-1")
    source.update({"filename": "delay-notice.pdf", "filepath_local": str(exhibit_file)})
    service = ArbitrationCaseWorkspaceService(db)

    content = asyncio.run(service.export_filing_bundle_zip("case-1"))

    with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
        names = set(archive.namelist())
        # "Delay notice" maps to the programme/delay records volume (guide §19 Vol 4).
        exhibit_path = "volume-4-programme-delay-records/C-1 - Delay notice.pdf"
        assert exhibit_path in names
        assert archive.read(exhibit_path) == b"%PDF-1.4 exhibit-bytes"
        assert "volume-1-pleadings/draft-1.md" in names
        assert "volume-6-claim-calculations/quantum-annexures.json" in names
        assert "volume-7-expert-reports/expert-alignment.json" in names
        manifest_entries = json.loads(archive.read("exhibits/exhibit-files.json").decode("utf-8"))

    by_exhibit = {entry["exhibit_id"]: entry for entry in manifest_entries}
    assert by_exhibit["C-1"]["error"] is None
    assert by_exhibit["C-1"]["size"] == len(b"%PDF-1.4 exhibit-bytes")
    # Unapproved matrix sources are excluded from the authoritative filing set.
    assert "C-2" not in by_exhibit


def test_citation_audit_flags_missing_exhibit_file_and_invalid_pin_cite():
    db = _FakeDb()
    db.arbitration_document_index.rows[0]["page_numbers"] = [1, 2]
    db.arbitration_draft_versions.rows[0]["full_markdown"] = (
        "# EOT Statement of Claim\n\nRelies on C-1, p.2 and separately on C-1, p.5. [S1: Delay notice]\n"
    )
    service = ArbitrationCaseWorkspaceService(db)

    audit = asyncio.run(service.citation_audit("case-1"))

    issue_types = {issue["issue_type"] for issue in audit["issues"] if issue["severity"] == "blocking"}
    assert "exhibit_file_missing" in issue_types
    assert "invalid_pin_cite" in issue_types
    assert audit["ok"] is False

    pin_issues = [issue for issue in audit["issues"] if issue["issue_type"] == "invalid_pin_cite"]
    assert len(pin_issues) == 1
    assert "page 5" in pin_issues[0]["message"]

    report = audit["drafts"][0]
    assert {cite["page"] for cite in report["pin_cites"]} == {2, 5}


def test_citation_audit_passes_pin_cites_when_file_and_pages_resolve(tmp_path):
    db = _FakeDb()
    exhibit_file = tmp_path / "delay-notice.pdf"
    exhibit_file.write_bytes(b"%PDF-1.4")
    db.documents.rows.append(
        {"_id": "doc-1", "filename": "delay-notice.pdf", "filepath_local": str(exhibit_file)}
    )
    db.arbitration_document_index.rows[0]["page_numbers"] = [1, 2, 3]
    # Remove the unreviewed second row so only the resolvable exhibit remains.
    db.arbitration_document_index.rows = [db.arbitration_document_index.rows[0]]
    db.arbitration_draft_versions.rows[0]["full_markdown"] = (
        "# EOT Statement of Claim\n\nRelies on C-1, p.3. [S1: Delay notice]\n"
    )
    service = ArbitrationCaseWorkspaceService(db)

    audit = asyncio.run(service.citation_audit("case-1"))

    issue_types = {issue["issue_type"] for issue in audit["issues"]}
    assert "exhibit_file_missing" not in issue_types
    assert "invalid_pin_cite" not in issue_types


def test_statement_of_claim_includes_index_interest_costs_and_verification():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "relief_sought": "Award extension of time.",
            "interest_rate": 12,
        },
        "source_ledger": [],
        "matrix_context": {
            "documents": [
                {
                    "source_key": "S1",
                    "label": "Delay notice",
                    "citation": "C-1",
                    "metadata": {"exhibit_id": "C-1"},
                }
            ],
            "quantum": [
                {
                    "source_key": "S2",
                    "label": "interest",
                    "citation": "Q-INTEREST-CLAIMS-CL-001",
                    "snippet": "Simple interest: 1000000 x 12% p.a. x 365/365 days",
                    "metadata": {"calculation_type": "interest"},
                }
            ],
        },
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context)
    markdown = generated["full_markdown"]

    for heading in ["## Index", "## Interest", "## Costs", "## Verification / Statement of Truth"]:
        assert heading in markdown
    assert "12 percent per annum" in markdown
    assert "[S2: Q-INTEREST-CLAIMS-CL-001]" in markdown
    # Index lists the exhibit-backed document index.
    assert "C-1: Delay notice" in markdown
    section_keys = {section["key"] for section in generated["sections"]}
    assert {"index", "interest", "costs", "verification"}.issubset(section_keys)


def test_sod_preliminary_objections_render_from_jurisdiction_matrix():
    context = {
        "draft": {
            "_id": "draft-1",
            "project_id": "project-1",
            "draft_type": "statement_of_defence",
            "title": "SoD",
            "relief_sought": "Dismiss the claim.",
        },
        "source_ledger": [],
        "matrix_context": {
            "jurisdiction": [
                {
                    "source_key": "S1",
                    "label": "Limitation: EOT claim",
                    "citation": "time_barred",
                    "snippet": "Status: time_barred\nExpiry: 2023-01-10",
                }
            ]
        },
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
    }

    generated = ArbitrationDraftGenerator().generate(context)
    markdown = generated["full_markdown"]
    objections = next(section for section in generated["sections"] if section["key"] == "preliminary_objections")

    assert "Limitation: EOT claim" in objections["body"]
    assert "[S1: time_barred]" in objections["body"]
    assert objections["body"] != "[Evidence required]"
    assert "## Reply to Interest and Costs" in markdown


def test_context_builder_ingests_jurisdiction_matrix_rows():
    db = _FakeDb()
    db.arbitration_jurisdiction_matrix.rows.append(
        {
            "_id": "jur-lim-ctx",
            "case_id": "case-1",
            "check_type": "limitation",
            "subject": "EOT claim",
            "limitation_status": "within_limitation",
            "limitation_expiry_date": "2029-01-01",
            "approval_status": "approved",
        }
    )
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_defence",
        "title": "SoD",
    }

    context = asyncio.run(ArbitrationContextBuilder(db).build(draft, [], [], [], object()))

    origins = {row.get("source_origin") for row in context["source_ledger"]}
    assert "case_jurisdiction_matrix" in origins
    assert context["matrix_context"]["jurisdiction"]
    assert "Limitation: EOT claim" in context["matrix_context"]["jurisdiction"][0]["label"]


def test_quantum_agent_computes_interest_and_claim_summary_rollup():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "quantum",
            payload=ArbitrationAgentRunRequest(
                options={"interest_rate": 12, "interest_period_days": 365}
            ),
            current_user=_FakeUser(),
        )
    )

    assert result["errors"] == []
    rows = db.arbitration_quantum_annexures.rows
    by_calc = {str(row.get("calculation_id")): row for row in rows}

    interest_row = by_calc["Q-INTEREST-CLAIMS-CL-001"]
    from backend.rbac_backend.services.arbitration_drafting.agents.deterministic import compute_simple_interest

    assert interest_row["amount"] == compute_simple_interest(1000000, 12, 365) == 120000.0
    assert interest_row["principal_calculation_id"] == "Q-CLAIMS-CL-001"
    assert interest_row["interest_rate_percent"] == 12

    rollup = by_calc["Q-CLAIM-SUMMARY"]
    principals = [
        row
        for row in rows
        if str(row.get("calculation_type")) not in {"interest", "claim_summary_rollup"} and row.get("amount") is not None
    ]
    interests = [row for row in rows if str(row.get("calculation_type")) == "interest"]
    assert rollup["principal_total"] == round(sum(float(row["amount"]) for row in principals), 2)
    assert rollup["interest_total"] == round(sum(float(row["amount"]) for row in interests), 2)
    assert rollup["amount"] == round(rollup["principal_total"] + rollup["interest_total"], 2)
    assert rollup["amount"] == round(sum(item["total"] for item in rollup["line_items"]), 2)


def test_quantum_agent_rollup_is_stable_on_rerun():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]
    options = {"interest_rate": 12, "interest_period_days": 365}

    asyncio.run(
        run_arbitration_agent(db, case, "quantum", payload=ArbitrationAgentRunRequest(options=options), current_user=_FakeUser())
    )
    first_rollup = dict(
        next(row for row in db.arbitration_quantum_annexures.rows if row.get("calculation_id") == "Q-CLAIM-SUMMARY")
    )
    second = asyncio.run(
        run_arbitration_agent(db, case, "quantum", payload=ArbitrationAgentRunRequest(options=options), current_user=_FakeUser())
    )

    rollups = [row for row in db.arbitration_quantum_annexures.rows if row.get("calculation_id") == "Q-CLAIM-SUMMARY"]
    assert len(rollups) == 1
    assert second["created_records"] == []
    assert rollups[0]["amount"] == first_rollup["amount"]


def test_quantum_agent_links_cost_head_and_delay_events():
    db = _FakeDb()
    db.arbitration_chronology_matrix.rows[0]["claim_link"] = "CL-001"
    case = db.arbitration_cases.rows[0]

    asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "quantum",
                payload=ArbitrationAgentRunRequest(),
            current_user=_FakeUser(),
        )
    )

    row = next(r for r in db.arbitration_quantum_annexures.rows if r.get("calculation_id") == "Q-CLAIMS-CL-001")
    # "EOT claim" maps to the prolongation/overheads cost head (guide §11.3 link).
    assert row["cost_head"] == "prolongation_overheads"
    assert row["delay_event_ids"] == ["event-1"]


def test_review_consistency_agent_flags_red_flags():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(
        {
            "_id": "claim-row-rf",
            "case_id": "case-1",
            "claim_no": "CL-9",
            "claim_head": "EOT claim",
            "facts": "Late access caused delay to critical works.",
            "amount_or_days": 500000,
            "notice_ids": [],
            "evidence_ids": [],
            "approval_status": "approved",
        }
    )
    db.arbitration_document_index.rows.append(
        {
            "_id": "doc-row-fb",
            "case_id": "case-1",
            "source_id": "doc-fb",
            "title": "Final bill and no dues certificate",
            "exhibit_id": "C-9",
            "approval_status": "approved",
        }
    )
    case = db.arbitration_cases.rows[0]

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "review-consistency",
            payload=ArbitrationAgentRunRequest(options={}),
            current_user=_FakeUser(),
        )
    )

    assert result["errors"] == []
    row = next(r for r in db.arbitration_claim_matrix.rows if r["_id"] == "claim-row-rf")
    flags = set(row.get("red_flags") or [])
    assert {"no_claim_notice", "no_cost_records", "no_critical_path_impact", "final_bill_or_no_dues_waiver_risk"}.issubset(flags)
    # Approval/readiness statuses are untouched — red flags are guidance only.
    assert row["approval_status"] == "approved"
    assert any("Red flags for claim CL-9" in warning for warning in result["warnings"])


def test_issue_framing_applies_dispute_type_templates():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(
        {
            "_id": "claim-row-tmpl",
            "case_id": "case-1",
            "claim_no": "CL-EOT",
            "claim_head": "EOT claim",
            "facts": "Late access caused delay to the critical path.",
            "approval_status": "approved",
        }
    )
    case = db.arbitration_cases.rows[0]

    asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "issue-framing",
            payload=ArbitrationAgentRunRequest(options={}),
            current_user=_FakeUser(),
        )
    )

    issue = next(row for row in db.arbitration_issue_matrix.rows if row.get("issue_key") == "claim:CL-EOT")
    assert issue["dispute_category"] == "eot_delay"
    assert "concurrent" in issue["respondent_position"]
    assert "critical path" in issue["required_finding"]


def test_validator_warns_on_duplicate_heads_and_global_claims():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "statement_of_claim", "title": "SoC"},
        "source_ledger": [{"source_key": "S1", "source_id": "x", "citation": "x", "snippet": "x"}],
        "matrix_context": {
            "claims": [
                {
                    "source_key": "S2",
                    "label": "Prolongation cost",
                    "citation": "CL-1",
                    "metadata": {"amount_or_days": "INR 1000000", "evidence_ids": [], "notice_ids": []},
                },
                {
                    "source_key": "S3",
                    "label": "Prolongation cost",
                    "citation": "CL-2",
                    "metadata": {"amount_or_days": "INR 500000", "evidence_ids": ["doc-1"], "notice_ids": []},
                },
            ],
            "quantum": [
                {
                    "source_key": "S4",
                    "citation": "Q-1",
                    "metadata": {"calculation_type": "prolongation", "cost_head": "prolongation_overheads"},
                },
                {
                    "source_key": "S5",
                    "citation": "Q-2",
                    "metadata": {"calculation_type": "claim_summary", "cost_head": "prolongation_overheads"},
                },
            ],
        },
        "paragraph_responses": [],
    }

    report = ArbitrationDraftValidator().validation_report(context, "Draft text. [S1: x]")

    assert any("claim head 'prolongation cost'" in warning for warning in report["warnings"])
    assert any("cost head 'prolongation_overheads'" in warning for warning in report["warnings"])
    assert any("Global claim risk: claim CL-1" in warning for warning in report["warnings"])
    # CL-2 has evidence links, so it is not a global-claim risk.
    assert not any("Global claim risk: claim CL-2" in warning for warning in report["warnings"])


def test_validator_strengthened_rejoinder_new_matter_detection():
    context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "rejoinder", "title": "Reply"},
        "source_ledger": [{"source_key": "S1", "source_id": "x", "citation": "x", "snippet": "x"}],
        "matrix_context": {
            "rejoinder_replies": [
                {
                    "source_key": "S2",
                    "citation": "SoD para 12",
                    "metadata": {"new_matter": True, "permission_required": True, "permission_obtained": False},
                }
            ]
        },
        "paragraph_responses": [{"source_paragraph_number": "1", "response_type": "admit"}],
    }

    # Expanded keyword detection: "further sum" is claim-expansion language.
    report = ArbitrationDraftValidator().validation_report(
        context, "The Claimant seeks a further sum by way of compensation. [S1: x]"
    )
    assert any("may introduce a new claim" in item for item in report["approval_blockers"])
    assert any("without an obtained permission receipt" in item for item in report["approval_blockers"])

    context["matrix_context"]["rejoinder_replies"][0]["metadata"]["permission_obtained"] = True
    report = ArbitrationDraftValidator().validation_report(context, "The defences are denied. [S1: x]")
    assert not any("without an obtained permission receipt" in item for item in report["approval_blockers"])
    assert not any("may introduce a new claim" in item for item in report["approval_blockers"])


def test_notice_snippet_reads_ui_and_agent_field_names():
    db = _FakeDb()
    db.arbitration_notice_compliance.rows.extend(
        [
            {
                "_id": "notice-ui",
                "case_id": "case-1",
                "notice_ref": "NTC-UI",
                "requirement": "Serve delay notice within 28 days",
                "risk_note": "Late by two days",
                "approval_status": "approved",
            },
            {
                "_id": "notice-agent",
                "case_id": "case-1",
                "notice_ref": "NTC-AGENT",
                "contractual_requirement": "Confirm notice clause and service method",
                "risk": "Precondition impact unclear",
                "approval_status": "approved",
            },
        ]
    )
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
    }

    context = asyncio.run(ArbitrationContextBuilder(db).build(draft, [], [], [], object()))

    notices = {row["citation"]: row for row in context["source_ledger"] if row.get("source_origin") == "case_notice_compliance"}
    assert "Serve delay notice within 28 days" in notices["NTC-UI"]["snippet"]
    assert "Late by two days" in notices["NTC-UI"]["snippet"]
    assert "Confirm notice clause and service method" in notices["NTC-AGENT"]["snippet"]
    assert "Precondition impact unclear" in notices["NTC-AGENT"]["snippet"]


def test_add_and_remove_draft_references():
    db = _FakeDb()
    service = ArbitrationDraftingService(db)
    payload = [
        ArbitrationSelectedReferenceCreate(
            source_type="document",
            source_id="doc-evidence-1",
            label="Site access letter",
            citation="LTR-042",
            snippet="Workfront handed over late per letter LTR-042.",
        )
    ]

    detail = asyncio.run(service.add_references("draft-1", payload, _FakeUser()))

    refs = detail["selected_references"]
    assert any(ref["source_id"] == "doc-evidence-1" for ref in refs)
    added = next(ref for ref in refs if ref["source_id"] == "doc-evidence-1")
    assert added["selected_by"] == "user-1"

    detail = asyncio.run(service.remove_reference("draft-1", added["_id"], _FakeUser()))
    assert all(ref["source_id"] != "doc-evidence-1" for ref in detail["selected_references"])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.remove_reference("draft-1", "missing-ref", _FakeUser()))
    assert exc_info.value.status_code == 404


def test_reference_mutations_blocked_on_locked_draft():
    db = _FakeDb()
    db.arbitration_drafts.rows[0]["is_locked"] = True
    service = ArbitrationDraftingService(db)
    payload = [
        ArbitrationSelectedReferenceCreate(source_type="document", source_id="doc-x", label="X")
    ]

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.add_references("draft-1", payload, _FakeUser()))
    assert exc_info.value.status_code == 409

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.remove_reference("draft-1", "any-ref", _FakeUser()))
    assert exc_info.value.status_code == 409


def test_validator_flags_ungated_draft_without_case_link():
    base_context = {
        "draft": {"_id": "draft-1", "project_id": "project-1", "draft_type": "statement_of_claim", "title": "SoC"},
        "source_ledger": [{"source_key": "S1", "source_id": "doc-1", "citation": "X", "snippet": "X"}],
        "paragraph_responses": [],
    }

    report = ArbitrationDraftValidator().validation_report(base_context, "Draft body. [S1: X]")
    assert any("not linked to an arbitration case" in item for item in report["warnings"])
    # Working generation remains available, but final approval is blocked.
    assert any("not linked to an arbitration case" in item for item in report["approval_blockers"])

    gated = {**base_context, "draft": {**base_context["draft"], "case_id": "case-1"}}
    report = ArbitrationDraftValidator().validation_report(gated, "Draft body. [S1: X]")
    assert not any("not linked to an arbitration case" in item for item in report["warnings"])


def test_register_sources_respect_draft_controls():
    draft = {
        "_id": "draft-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "draft_type": "statement_of_claim",
        "title": "EOT claim",
    }

    # Default: claim register row (claim-1) joins the ledger.
    context = asyncio.run(ArbitrationContextBuilder(_FakeDb()).build(draft, [], [], [], object()))
    assert any(row.get("source_origin") == "claim_register" for row in context["source_ledger"])

    # Per-row exclusion removes exactly that register row and warns.
    excluded_draft = {**draft, "excluded_register_ids": ["claim-1"]}
    context = asyncio.run(ArbitrationContextBuilder(_FakeDb()).build(excluded_draft, [], [], [], object()))
    assert all(row.get("source_id") != "claim-1" for row in context["source_ledger"])
    assert any("excluded from this draft" in warning for warning in context["context_warnings"])

    # Wholesale opt-out removes every register origin and warns.
    disabled_draft = {**draft, "include_register_sources": False}
    context = asyncio.run(ArbitrationContextBuilder(_FakeDb()).build(disabled_draft, [], [], [], object()))
    register_origins = {"claim_register", "variation_register", "ipc_register", "bank_guarantee_register"}
    assert not any(row.get("source_origin") in register_origins for row in context["source_ledger"])
    assert any("register sources" in warning.lower() for warning in context["context_warnings"])


def _soc_context_with_source():
    return {
        "draft": {
            "_id": "draft-1",
            "case_id": "case-1",
            "project_id": "project-1",
            "draft_type": "statement_of_claim",
            "title": "EOT Claim",
            "relief_sought": "Award extension of time.",
        },
        "source_ledger": [
            {
                "source_key": "S1",
                "source_id": "doc-1",
                "source_type": "document",
                "allowed_use": "fact",
                "label": "Delay notice",
                "citation": "CPL/2025/0142",
                "snippet": "Notice of delay due to late access.",
            }
        ],
        "matrix_context": {},
        "claim_heads": [],
        "paragraph_responses": [],
        "missing_evidence": [],
        "context_warnings": [],
    }


class _FakeDraftLLM:
    def __init__(self, response, available=True):
        self._response = response
        self._available = available

    @property
    def available(self):
        return self._available

    async def generate(self, prompt, max_tokens=512, model=None):
        _FakeDraftLLM.last_prompt = prompt
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_llm_draft_generator_rewrites_sections_preserving_citations():
    from backend.rbac_backend.services.arbitration_drafting.llm_generator import LLMDraftGenerator

    context = _soc_context_with_source()
    fake = _FakeDraftLLM(
        json.dumps(
            {
                "introduction": "By this Statement of Claim the Claimant advances its case on the record. [S1: CPL/2025/0142]",
            }
        )
    )
    gen = LLMDraftGenerator(generator=fake, model="gpt-4o")

    result = asyncio.run(gen.generate(context))

    assert result["ai_prompt_version"] == "arbitration_pleadings_llm.v1"
    assert result["structured_output"]["generation_mode"] == "llm"
    assert result["structured_output"]["llm_rewritten_sections"] >= 1
    assert "the Claimant advances its case on the record. [S1: CPL/2025/0142]" in result["full_markdown"]


def test_llm_draft_generator_rejects_fabricated_citation():
    from backend.rbac_backend.services.arbitration_drafting.llm_generator import LLMDraftGenerator

    context = _soc_context_with_source()
    fake = _FakeDraftLLM(
        json.dumps(
            {
                "introduction": "Fabricated prose citing a nonexistent exhibit. [S1: CPL/2025/0142] [S9: Invented]",
            }
        )
    )
    gen = LLMDraftGenerator(generator=fake, model="gpt-4o")

    result = asyncio.run(gen.generate(context))

    # The rewrite invented [S9], so it is rejected and the deterministic body kept.
    assert "[S9: Invented]" not in result["full_markdown"]
    assert "Fabricated prose" not in result["full_markdown"]


def test_llm_draft_generator_falls_back_on_runtime_error():
    from backend.rbac_backend.services.arbitration_drafting.llm_generator import LLMDraftGenerator

    context = _soc_context_with_source()
    gen = LLMDraftGenerator(generator=_FakeDraftLLM(RuntimeError("boom")), model="gpt-4o")

    result = asyncio.run(gen.generate(context))

    assert result["ai_prompt_version"] == "arbitration_pleadings.v2"  # deterministic
    assert any("LLM draft generation failed" in w for w in context["context_warnings"])


def test_llm_rejoinder_reply_agent_creates_matrix_rows_flagging_new_matter(monkeypatch):
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]
    db.arbitration_paragraph_responses.rows.extend(
        [
            {
                "_id": "para-1",
                "draft_id": "draft-1",
                "source_paragraph_number": "12",
                "source_paragraph_text": "The Respondent denies late access and says the contractor failed to mobilise.",
            },
            {
                "_id": "para-2",
                "draft_id": "draft-1",
                "source_paragraph_number": "20",
                "source_paragraph_text": "The Respondent counterclaims liquidated damages for delay.",
            },
        ]
    )

    class _Gen(_FakeLLMGenerator):
        response = json.dumps(
            [
                {
                    "source_id": "12",
                    "nature_of_defence": "denial",
                    "claimant_reply": "Access was handed over late per the contemporaneous notice; the mobilisation allegation is unsupported.",
                    "new_matter": False,
                },
                {
                    "source_id": "20",
                    "nature_of_defence": "counterclaim",
                    "claimant_reply": "The LD counterclaim fails because the delay is an employer-risk event.",
                    "reply_to_counterclaim": "LD is not leviable where EOT is due.",
                    "new_matter": True,
                },
            ]
        )

    monkeypatch.setattr(llm_agents_module, "LLMGenerator", _Gen)

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "rejoinder-reply",
            payload=ArbitrationAgentRunRequest(draft_id="draft-1", options={"agent_mode": "llm"}),
            current_user=_FakeUser(),
        )
    )

    rows = {row["source_sod_para"]: row for row in db.arbitration_rejoinder_matrix.rows}
    assert set(rows) == {"12", "20"}
    assert rows["12"]["new_matter"] is False
    assert rows["20"]["new_matter"] is True
    assert rows["20"]["permission_required"] is True
    assert rows["20"]["permission_obtained"] is False
    assert all(row["projection_read_only"] is True for row in rows.values())
    assert all(row["projection_source"] == "paragraph_response" for row in rows.values())
    paragraph_rows = {row["source_paragraph_number"]: row for row in db.arbitration_paragraph_responses.rows}
    assert paragraph_rows["12"]["response_text"].startswith("Access was handed over late")
    assert paragraph_rows["20"]["new_matter"] is True
    assert rows["12"]["approval_status"] == "needs_review"
    assert any("raises new matter" in w for w in result["warnings"])


def test_paragraph_response_is_authoritative_and_matrix_projection_is_read_only():
    db = _FakeDb()
    draft = db.arbitration_drafts.rows[0]
    draft["draft_type"] = "statement_of_defence"
    db.arbitration_paragraph_responses.rows.append(
        {
            "_id": "para-soc-1",
            "draft_id": "draft-1",
            "source_pleading_type": "statement_of_claim",
            "source_paragraph_number": "7",
            "source_paragraph_text": "The Respondent failed to provide access.",
            "response_type": "require_proof",
        }
    )
    service = ArbitrationDraftingService(db)

    updated = asyncio.run(
        service.update_paragraph_response(
            "draft-1",
            "para-soc-1",
            ArbitrationParagraphResponseUpdate(
                response_type="deny",
                response_text="Denied because access was provided on the contractual date.",
                response_reason="The contemporaneous handover record records timely access.",
            ),
            _FakeUser(),
        )
    )

    assert updated["response_type"] == "deny"
    assert len(db.arbitration_defence_matrix.rows) == 1
    projection = db.arbitration_defence_matrix.rows[0]
    assert projection["source_paragraph_response_id"] == "para-soc-1"
    assert projection["source_soc_para"] == "7"
    assert projection["defence"].startswith("Denied because")
    assert projection["projection_read_only"] is True
    assert projection["approval_status"] == "needs_review"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            ArbitrationCaseWorkspaceService(db).update_matrix_row(
                "case-1",
                "defence-matrix",
                projection["_id"],
                ArbitrationMatrixRowUpdate(defence="Client-side overwrite"),
                _FakeUser(),
            )
        )
    assert exc.value.status_code == 409


def test_historical_paragraph_review_resolves_only_unique_authoritative_match():
    responses = [
        {"_id": "response-7", "source_paragraph_number": "7"},
        {"_id": "response-8a", "source_paragraph_number": "8(a)"},
        {"_id": "response-8b", "source_paragraph_number": "8-a"},
    ]
    unique = classify_historical_row(
        {"source_soc_para": "07"},
        responses,
        matrix_slug="defence-matrix",
    )
    ambiguous = classify_historical_row(
        {"source_sod_para": "8(a)"},
        responses,
        matrix_slug="rejoinder-matrix",
    )
    missing = classify_historical_row(
        {"defence": "Legacy text without paragraph identity"},
        responses,
        matrix_slug="defence-matrix",
    )

    assert unique == {
        "classification": "unambiguous_paragraph_number",
        "candidate_response_ids": ["response-7"],
        "resolvable": True,
    }
    assert ambiguous["classification"] == "ambiguous_multiple_responses"
    assert ambiguous["resolvable"] is False
    assert missing["classification"] == "missing_paragraph_identity"
    assert missing["resolvable"] is False


def test_draft_bound_paragraph_position_matrix_cannot_be_created_directly():
    db = _FakeDb()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            ArbitrationCaseWorkspaceService(db).create_matrix_row(
                "case-1",
                "rejoinder-matrix",
                ArbitrationMatrixRowCreate(draft_id="draft-1", source_sod_para="4", claimant_reply="Direct duplicate"),
                _FakeUser(),
            )
        )
    assert exc.value.status_code == 409


def test_arbitration_engine_policy_is_server_authoritative_and_deterministic():
    config = SimpleNamespace(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="canary",
        ARBITRATION_ENGINE_CANARY_PERCENT=0,
        ARBITRATION_ENGINE_CANARY_TENANT_IDS="org-1",
        ARBITRATION_ENGINE_CANARY_PROJECT_IDS="",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=False,
    )
    payload = ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim", requested_engine="arbitration_v2")
    first_hash = canonical_workflow_request_hash(case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1")
    second_hash = canonical_workflow_request_hash(case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1")
    decision = ArbitrationEngineSelector(config).select(tenant_id="org-1", project_id="project-1", request_hash=first_hash)
    assert first_hash == second_hash
    assert decision.engine == "langgraph_v1"
    assert decision.reason == "canary_match"


def test_phase5_rollout_pause_and_force_v2_scope_override_canary():
    base = {
        "ARBITRATION_ENGINE_DEFAULT": "langgraph_v1",
        "ARBITRATION_ENGINE_ROLLOUT_MODE": "canary",
        "ARBITRATION_ENGINE_CANARY_PERCENT": 100,
        "ARBITRATION_ENGINE_CANARY_TENANT_IDS": "",
        "ARBITRATION_ENGINE_CANARY_PROJECT_IDS": "",
        "ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS": "org-blocked",
        "ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS": "project-blocked",
        "ARBITRATION_ENGINE_ROLLOUT_PAUSED": False,
        "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED": False,
    }
    tenant_decision = ArbitrationEngineSelector(SimpleNamespace(**base)).select(
        tenant_id="org-blocked", project_id="project-1", request_hash="request"
    )
    project_decision = ArbitrationEngineSelector(SimpleNamespace(**base)).select(
        tenant_id="org-1", project_id="project-blocked", request_hash="request"
    )
    paused_decision = ArbitrationEngineSelector(
        SimpleNamespace(**{**base, "ARBITRATION_ENGINE_ROLLOUT_PAUSED": True})
    ).select(tenant_id="org-1", project_id="project-1", request_hash="request")

    assert tenant_decision.engine == project_decision.engine == paused_decision.engine == "arbitration_v2"
    assert tenant_decision.reason == project_decision.reason == "tenant_or_project_forced_v2"
    assert paused_decision.reason == "rollout_paused"


def test_phase6_primary_requires_receipt_health_and_explicit_scope():
    base = {
        "ARBITRATION_ENGINE_DEFAULT": "langgraph_v1",
        "ARBITRATION_ENGINE_ROLLOUT_MODE": "primary",
        "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED": True,
        "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID": "phase6-acceptance",
        "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256": "b" * 64,
        "ARBITRATION_ENGINE_PRIMARY_TENANT_IDS": "org-accepted",
        "ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS": "",
        "ARBITRATION_ENGINE_PRIMARY_PERCENT": 0,
        "ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY": True,
        "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE": "active",
        "ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS": "",
        "ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS": "",
        "ARBITRATION_ENGINE_ROLLOUT_PAUSED": False,
    }
    health = {
        "primary_cutover": {"eligible": True},
        "acceptance_receipt": {"valid_for_scope": True},
    }
    selector = ArbitrationEngineSelector(SimpleNamespace(**base))

    accepted = selector.select(
        tenant_id="org-accepted",
        project_id="project-1",
        request_hash="request",
        rollout_health=health,
    )
    repeated = selector.select(
        tenant_id="org-accepted",
        project_id="project-1",
        request_hash="request",
        rollout_health=health,
    )
    outside_scope = selector.select(
        tenant_id="org-other",
        project_id="project-1",
        request_hash="request",
        rollout_health=health,
    )
    unhealthy = selector.select(
        tenant_id="org-accepted",
        project_id="project-1",
        request_hash="request",
        rollout_health={
            "primary_cutover": {"eligible": False},
            "acceptance_receipt": {"valid_for_scope": True},
        },
    )
    missing_receipt = ArbitrationEngineSelector(
        SimpleNamespace(**{**base, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID": ""})
    ).select(
        tenant_id="org-accepted",
        project_id="project-1",
        request_hash="request",
        rollout_health=health,
    )

    assert accepted.engine == "langgraph_v1"
    assert accepted.reason == "primary_scope_match"
    assert accepted.policy_version == "phase6-v1"
    assert accepted.decision_hash == repeated.decision_hash
    assert len(accepted.decision_hash) == 64
    assert outside_scope.engine == "arbitration_v2"
    assert outside_scope.reason == "primary_scope_miss"
    assert unhealthy.reason == "primary_health_not_ready"
    assert missing_receipt.reason == "primary_acceptance_receipt_missing"


def test_phase6_read_replay_only_mode_freezes_new_v2_scope_misses():
    config = SimpleNamespace(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="primary",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID="phase6-acceptance",
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256="c" * 64,
        ARBITRATION_ENGINE_PRIMARY_TENANT_IDS="org-accepted",
        ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS="",
        ARBITRATION_ENGINE_PRIMARY_PERCENT=0,
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=False,
        ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE="read_replay_only",
        ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS="",
        ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS="",
        ARBITRATION_ENGINE_ROLLOUT_PAUSED=False,
    )

    decision = ArbitrationEngineSelector(config).select(
        tenant_id="org-outside",
        project_id="project-1",
        request_hash="request",
    )

    assert decision.engine == "unavailable"
    assert decision.reason == "v2_new_runs_frozen"

    paused = ArbitrationEngineSelector(
        SimpleNamespace(**{**config.__dict__, "ARBITRATION_ENGINE_ROLLOUT_PAUSED": True})
    ).select(
        tenant_id="org-accepted",
        project_id="project-1",
        request_hash="request",
    )
    assert paused.engine == "unavailable"
    assert paused.reason == "v2_new_runs_frozen"


@pytest.mark.parametrize(
    "pleading_type",
    ["statement_of_claim", "statement_of_defence", "counterclaim", "rejoinder"],
)
def test_phase5_shadow_comparison_is_redacted_and_covers_required_dimensions(pleading_type):
    vector = {
        "evidence_set": "evidence-hash",
        "matrix_rows": "matrix-hash",
        "readiness": "readiness-hash",
        "section_coverage": "section-hash",
        "citation_validity": "citation-hash",
        "validation_blockers": "blocker-hash",
        "human_interventions": "intervention-hash",
    }
    comparison = build_shadow_comparison(
        pleading_type=pleading_type,
        state_version=4,
        authoritative=vector,
        candidate=dict(vector),
        authoritative_latency_ms=10.5,
        candidate_latency_ms=12.5,
    )

    assert comparison["overall_status"] == "match"
    assert comparison["authoritative_writes"] is False
    assert tuple(comparison["dimensions"]) == SHADOW_DIMENSIONS
    assert comparison["dimensions"]["output_latency"]["status"] == "measured"
    assert "text" not in json.dumps(comparison).lower()


def test_phase5_rollout_health_blocks_threshold_breaches_without_identifiers():
    now = datetime.now(timezone.utc)
    runs = [
        {
            "_id": f"run-{index}",
            "engine": "langgraph_v1" if index < 10 else "arbitration_v2",
            "status": "failed" if index == 0 else "awaiting_matrix_review" if index == 1 else "completed",
            "checkpoint_sync_status": "pending" if index == 2 else "synced",
            "updated_at": now - timedelta(hours=80) if index == 1 else now,
        }
        for index in range(20)
    ]
    events = [
        {
            "run_id": f"run-{index}",
            "event_type": "shadow_comparison",
            "data": {"overall_status": "match" if index < 18 else "mismatch"},
        }
        for index in range(20)
    ] + [{"run_id": "run-3", "event_type": "workflow_fallback_v2"}]
    config = SimpleNamespace(
        ARBITRATION_ENGINE_MAX_PAUSE_HOURS=72,
        ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE=20,
        ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT=2,
        ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT=4,
        ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT=99,
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=False,
        ARBITRATION_ENGINE_ROLLOUT_PAUSED=False,
    )

    health = build_rollout_health(runs, events, now=now, config=config)

    assert health["status"] == "blocked"
    assert health["rates"] == {
        "workflow_failure_percent": 5.0,
        "fallback_percent": 5.0,
        "shadow_parity_percent": 90.0,
        "unresolved_source_drift_percent": 0.0,
    }
    assert {alert["code"] for alert in health["alerts"]} == {
        "checkpoint_sync_pending",
        "stale_paused_workflow",
        "workflow_failure_rate",
        "fallback_rate",
        "shadow_parity_rate",
    }
    rendered = json.dumps(health)
    assert "run-" not in rendered
    assert "org-" not in rendered


def test_phase6_cutover_health_requires_all_pleading_types_receipt_and_v2_window():
    now = datetime.now(timezone.utc)
    pleading_types = [
        "statement_of_claim",
        "statement_of_defence",
        "counterclaim",
        "rejoinder",
    ]
    runs = [
        {
            "_id": f"run-{index}",
            "pleading_type": pleading_types[index % len(pleading_types)],
            "engine": "langgraph_v1",
            "status": "completed",
            "_acceptance_receipts_valid": True,
            "checkpoint_sync_status": "synced",
            "updated_at": now,
            "draft_version_hash": "a" * 64,
            "validation_artifact_set_hash": "b" * 64,
            "readiness_approval_receipt_id": f"readiness-{index}",
            "plan_approval_receipt_id": f"plan-{index}",
            "legal_review_approval_receipt_id": f"legal-{index}",
            "draft_approval_receipt_id": f"draft-{index}",
            "export_approval_receipt_id": f"export-{index}",
        }
        for index in range(20)
    ]
    events = [
        {
            "run_id": f"run-{index}",
            "event_type": "shadow_comparison",
            "data": {"overall_status": "match"},
        }
        for index in range(20)
    ]
    config = SimpleNamespace(
        ARBITRATION_ENGINE_MAX_PAUSE_HOURS=72,
        ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE=20,
        ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT=2,
        ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT=4,
        ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT=99,
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID="phase6-acceptance",
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256="d" * 64,
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=True,
        ARBITRATION_ENGINE_MIN_PLEADING_TYPE_SAMPLE=1,
        ARBITRATION_ENGINE_ROLLOUT_PAUSED=False,
        ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE="active",
        ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL=(now + timedelta(days=180)).date().isoformat(),
    )

    ready = build_rollout_health(runs, events, now=now, config=config)
    assert ready["status"] == "ready"
    assert ready["primary_cutover"]["eligible"] is True
    assert ready["primary_cutover"]["missing_pleading_types"] == []

    synthetic_ids_only = build_rollout_health(
        [{key: value for key, value in run.items() if key != "_acceptance_receipts_valid"} for run in runs],
        events,
        now=now,
        config=config,
    )
    assert synthetic_ids_only["primary_cutover"]["eligible"] is False
    assert synthetic_ids_only["sample"]["accepted_langgraph_workflows"] == 0

    incomplete = build_rollout_health(
        [run for run in runs if run["pleading_type"] != "rejoinder"],
        events,
        now=now,
        config=config,
    )
    assert incomplete["primary_cutover"]["eligible"] is False
    assert "pleading_type_coverage_incomplete" in incomplete["primary_cutover"]["blockers"]
    assert incomplete["primary_cutover"]["missing_pleading_types"] == ["rejoinder"]


def test_v2_workflow_create_is_idempotent_and_uses_immutable_manifests():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )
    first = asyncio.run(
        engine.create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key="idem-1", request_hash=request_hash
        )
    )
    second = asyncio.run(
        engine.create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key="idem-1", request_hash=request_hash
        )
    )
    assert first["_id"] == second["_id"]
    assert len(db.arbitration_workflow_runs.rows) == 1
    assert len(db.arbitration_workflow_snapshots.rows) == 16
    assert {row["kind"] for row in db.arbitration_workflow_snapshots.rows} == {
        "input", "document_manifest", "analysis_artifact", "analysis_artifact_set",
        "matrix_revision_set", "evidence_manifest", "material_questions"
    }
    analysis_artifacts = [row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "analysis_artifact"]
    assert len(analysis_artifacts) == 10
    assert all(row["payload"]["authoritative"] is False for row in analysis_artifacts)
    manifest = next(row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "document_manifest")
    assert manifest["payload"]["documents"][0]["document_id"] == "doc-1"
    assert "ocrText" not in json.dumps(manifest, default=str)


def test_phase5_shadow_milestone_is_idempotent_non_authoritative_and_auditable():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )
    run = asyncio.run(
        engine.create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key="shadow-1", request_hash=request_hash,
            rollout_mode="shadow",
        )
    )
    service = ArbitrationWorkflowService(db)

    asyncio.run(service._record_shadow_safely(run, authoritative_latency_ms=10.0))
    snapshot_count = len(db.arbitration_workflow_snapshots.rows)
    event_count = len(db.arbitration_workflow_events.rows)
    asyncio.run(service._record_shadow_safely(run, authoritative_latency_ms=10.0))

    comparisons = [row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "shadow_comparison"]
    assert len(comparisons) == 1
    assert comparisons[0]["payload"]["authoritative_writes"] is False
    assert comparisons[0]["payload"]["overall_status"] == "match"
    assert len(db.arbitration_workflow_snapshots.rows) == snapshot_count
    assert len(db.arbitration_workflow_events.rows) == event_count
    event = next(row for row in db.arbitration_workflow_events.rows if row["event_type"] == "shadow_comparison")
    assert event["data"]["comparison_hash"] == comparisons[0]["payload"]["comparison_hash"]
    assert all(
        row["payload"].get("authoritative") is False
        for row in db.arbitration_workflow_snapshots.rows
        if row["kind"] == "analysis_artifact"
    )
    assert "Notice records" not in json.dumps(comparisons[0], default=str)

    health = asyncio.run(service.operations_health("case-1"))
    assert health["sample"]["shadow_comparisons"] == 1
    assert "shadow-1" not in json.dumps(health)


def test_phase5_shadow_failure_is_isolated_redacted_and_idempotent(monkeypatch):
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )
    run = asyncio.run(
        engine.create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key="shadow-failure-1",
            request_hash=request_hash, rollout_mode="shadow",
        )
    )

    async def fail_projection(_self, _run):
        raise RuntimeError("raw provider detail must not be persisted")

    monkeypatch.setattr(LangGraphArbitrationEngine, "shadow_route_projection", fail_projection)
    service = ArbitrationWorkflowService(db)
    asyncio.run(service._record_shadow_safely(run, authoritative_latency_ms=10.0))
    asyncio.run(service._record_shadow_safely(run, authoritative_latency_ms=10.0))

    failures = [
        row for row in db.arbitration_workflow_snapshots.rows
        if row["kind"] == "shadow_comparison_failure"
    ]
    events = [
        row for row in db.arbitration_workflow_events.rows
        if row["event_type"] == "shadow_comparison_failed"
    ]
    assert len(failures) == len(events) == 1
    assert failures[0]["payload"]["error_code"] == "RuntimeError"
    assert failures[0]["payload"]["authoritative_writes"] is False
    assert "provider detail" not in json.dumps(failures[0], default=str)
    assert run["engine"] == "arbitration_v2"


def test_phase5_shadow_artifact_metrics_hash_actual_evidence_sections_and_citations():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1", pleading_type="statement_of_claim", selected_document_ids=["doc-1"]
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )
    run = asyncio.run(
        engine.create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key="shadow-artifacts-1",
            request_hash=request_hash, rollout_mode="shadow",
        )
    )
    db.arbitration_plans.rows.append(
        {
            "_id": "plan-shadow",
            "run_id": run["_id"],
            "section_structure": [{"order": 1, "key": "introduction"}, {"order": 2, "key": "claims"}],
            "section_source_mapping": [
                {"section_key": "introduction", "source_revision_ids": ["doc-1@v1"]},
                {"section_key": "claims", "source_revision_ids": ["claim-1@v1"]},
            ],
        }
    )
    db.arbitration_draft_versions.rows.append(
        {"_id": "version-shadow", "draft_id": "draft-1", "sections": {"introduction": {"markdown": "Redacted"}}}
    )
    report = asyncio.run(
        ArbitrationWorkflowRepository(db).create_snapshot(
            run_id=run["_id"],
            kind="validation_report",
            payload={"validation_input_hash": "validation-input-hash"},
            effect_key=f"{run['_id']}:phase5-validation-report",
        )
    )
    run.update(
        {
            "plan_id": "plan-shadow",
            "draft_version_id": "version-shadow",
            "validation_status": "passed",
            "validation_report_id": report["_id"],
            "validation_blockers": [],
        }
    )
    service = ArbitrationWorkflowService(db)

    first = asyncio.run(service._shadow_artifact_metrics(run))
    db.arbitration_draft_versions.rows[-1]["sections"]["claims"] = {"markdown": "Redacted"}
    second = asyncio.run(service._shadow_artifact_metrics(run))

    assert first["evidence_set_hash"]
    assert first["section_coverage_hash"] != second["section_coverage_hash"]
    assert first["citation_validity_hash"]
    assert "Redacted" not in json.dumps(first)


def test_concurrent_v2_workflow_create_reuses_run_and_snapshot_rows():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )

    async def create():
        return await engine.create_workflow(
            "case-1",
            payload,
            _FakeUser(),
            idempotency_key="concurrent-idem-1",
            request_hash=request_hash,
        )

    async def create_pair():
        return await asyncio.gather(create(), create())

    first, second = asyncio.run(create_pair())

    assert first["_id"] == second["_id"]
    assert len(db.arbitration_workflow_runs.rows) == 1
    assert len(db.arbitration_workflow_snapshots.rows) == 16
    assert len(db.arbitration_workflow_events.rows) == 1


def test_v2_adapter_is_semantically_stable_for_same_immutable_inputs():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )
    request_hash = canonical_workflow_request_hash(
        case_id="case-1", payload=payload, tenant_id="org-1", project_id="project-1"
    )
    first = asyncio.run(
        engine.create_workflow("case-1", payload, _FakeUser(), idempotency_key=None, request_hash=request_hash)
    )
    second = asyncio.run(
        engine.create_workflow("case-1", payload, _FakeUser(), idempotency_key=None, request_hash=request_hash)
    )

    stable_fields = (
        "engine",
        "engine_version",
        "status",
        "current_node",
        "next_action",
        "input_snapshot_hash",
        "document_manifest_hash",
        "evidence_snapshot_hash",
        "analysis_artifact_set_hash",
        "matrix_revision_hash",
        "readiness_artifact_hash",
        "question_snapshot_hash",
    )
    assert {key: first.get(key) for key in stable_fields} == {key: second.get(key) for key in stable_fields}


def test_workflow_effect_claim_and_completion_are_idempotent():
    db = _FakeDb()
    repository = ArbitrationWorkflowRepository(db)

    first = asyncio.run(
        repository.claim_effect(
            run_id="run-1",
            effect_key="run-1:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )
    second = asyncio.run(
        repository.claim_effect(
            run_id="run-1",
            effect_key="run-1:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )

    assert first["_claimed_now"] is True
    assert second["_claimed_now"] is False
    assert len(db.arbitration_workflow_effects.rows) == 1

    output = {"draft_version_id": "version-1", "draft_version_hash": "version-hash-1"}
    lease_token = str(first["lease_token"])
    asyncio.run(
        repository.complete_effect(
            "run-1:generate:plan-1",
            output,
            lease_token=lease_token,
        )
    )
    asyncio.run(
        repository.complete_effect(
            "run-1:generate:plan-1",
            output,
            lease_token=lease_token,
        )
    )
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            repository.complete_effect(
                "run-1:generate:plan-1",
                {"draft_version_id": "version-2", "draft_version_hash": "version-hash-2"},
                lease_token=lease_token,
            )
        )
    assert conflict.value.status_code == 409


def test_workflow_effect_lease_recovers_process_kill_and_fences_stale_lease():
    db = _FakeDb()
    repository = ArbitrationWorkflowRepository(db)
    first = asyncio.run(
        repository.claim_effect(
            run_id="run-effect-recovery",
            effect_key="run-effect-recovery:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )
    assert first["_claimed_now"] is True
    assert first["_recovered"] is False

    stored = db.arbitration_workflow_effects.rows[0]
    stored["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    recovered = asyncio.run(
        repository.claim_effect(
            run_id="run-effect-recovery",
            effect_key="run-effect-recovery:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )
    assert recovered["_claimed_now"] is True
    assert recovered["_recovered"] is True
    assert recovered["attempt_count"] == 2
    assert recovered["lease_token"] != first["lease_token"]

    output = {"draft_version_id": "version-1", "draft_version_hash": "hash-1"}
    with pytest.raises(HTTPException):
        asyncio.run(
            repository.complete_effect(
                "run-effect-recovery:generate:plan-1",
                output,
                lease_token=str(first["lease_token"]),
            )
        )
    asyncio.run(
        repository.complete_effect(
            "run-effect-recovery:generate:plan-1",
            output,
            lease_token=str(recovered["lease_token"]),
        )
    )
    replay = asyncio.run(
        repository.claim_effect(
            run_id="run-effect-recovery",
            effect_key="run-effect-recovery:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )
    assert replay["status"] == "completed"
    assert replay["_claimed_now"] is False


def test_workflow_effect_recovery_stops_at_configured_attempt_limit(monkeypatch):
    monkeypatch.setattr(settings, "ARBITRATION_ENGINE_EFFECT_MAX_ATTEMPTS", 1)
    db = _FakeDb()
    repository = ArbitrationWorkflowRepository(db)
    first = asyncio.run(
        repository.claim_effect(
            run_id="run-effect-budget",
            effect_key="run-effect-budget:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )
    db.arbitration_workflow_effects.rows[0]["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )

    blocked = asyncio.run(
        repository.claim_effect(
            run_id="run-effect-budget",
            effect_key="run-effect-budget:generate:plan-1",
            effect_type="draft_generation",
            input_hash="plan-1",
        )
    )

    assert blocked["_claimed_now"] is False
    assert blocked["_attempt_limit_reached"] is True
    assert blocked["lease_token"] == first["lease_token"]
    assert blocked["attempt_count"] == 1


def test_workflow_snapshot_effect_key_rejects_different_payload():
    db = _FakeDb()
    repository = ArbitrationWorkflowRepository(db)
    asyncio.run(
        repository.create_snapshot(
            run_id="run-1",
            kind="input",
            payload={"case_id": "case-1"},
            effect_key="run-1:snapshot:input",
        )
    )
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            repository.create_snapshot(
                run_id="run-1",
                kind="input",
                payload={"case_id": "case-2"},
                effect_key="run-1:snapshot:input",
            )
        )
    assert conflict.value.status_code == 409


def test_workflow_approval_and_plan_rows_are_idempotent():
    db = _FakeDb()
    repository = ArbitrationWorkflowRepository(db)
    first_receipt = {
        "_id": "receipt-1",
        "run_id": "run-1",
        "gate": "matrix_review",
        "artifact_hash": "matrix-hash-1",
        "decision": "approved",
    }
    duplicate_receipt = {**first_receipt, "_id": "receipt-2"}
    stored_first = asyncio.run(repository.record_approval(first_receipt))
    stored_duplicate = asyncio.run(repository.record_approval(duplicate_receipt))

    assert stored_first["_id"] == "receipt-1"
    assert stored_duplicate["_id"] == "receipt-1"
    assert len(db.arbitration_workflow_approvals.rows) == 1

    first_plan = {"_id": "plan-1", "run_id": "run-1", "plan_hash": "plan-hash-1"}
    duplicate_plan = {**first_plan, "_id": "plan-2"}
    stored_plan = asyncio.run(repository.create_plan(first_plan))
    stored_duplicate_plan = asyncio.run(repository.create_plan(duplicate_plan))

    assert stored_plan["_id"] == "plan-1"
    assert stored_duplicate_plan["_id"] == "plan-1"
    assert len(db.arbitration_plans.rows) == 1


def test_plan_gate_reuses_completed_generation_effect(monkeypatch):
    db = _FakeDb()
    version = db.arbitration_draft_versions.rows[0]
    version["version_hash"] = immutable_version_hash(version)
    plan_hash = "p" * 64
    db.arbitration_plans.rows.append(
        {"_id": "plan-completed-effect", "run_id": "run-completed-effect", "plan_hash": plan_hash}
    )
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-completed-effect",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_plan_approval",
            "current_node": "plan_approval_gate",
            "next_action": "approve_plan",
            "state_version": 1,
            "plan_hash": plan_hash,
            "plan_id": "plan-completed-effect",
            "created_by": "author-user",
            "last_material_editor_id": "editor-user",
            "authoritative_effects": [],
        }
    )
    db.arbitration_workflow_effects.rows.append(
        {
            "_id": "effect-1",
            "run_id": "run-completed-effect",
            "effect_key": f"run-completed-effect:generate:{plan_hash}",
            "effect_type": "draft_generation",
            "input_hash": plan_hash,
            "status": "completed",
            "output_refs": {
                "draft_version_id": version["_id"],
                "draft_version_hash": version["version_hash"],
            },
        }
    )
    service = ArbitrationWorkflowService(db)

    async def unexpected_generation(*args, **kwargs):
        raise AssertionError("completed effect must reuse its immutable output")

    monkeypatch.setattr(service.drafting, "generate", unexpected_generation)
    updated = asyncio.run(
        service.approve_gate(
            "case-1",
            "run-completed-effect",
            "plan",
            ArbitrationWorkflowApprovalRequest(
                state_version=1,
                artifact_hash=plan_hash,
                reviewer_role="senior_legal_approver",
            ),
            _FakeUser(),
        )
    )

    assert updated["status"] == "awaiting_legal_review"
    assert updated["draft_version_id"] == version["_id"]
    assert len(db.arbitration_draft_versions.rows) == 1


def test_workflow_transition_rejects_stale_state_version():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim")
    run = asyncio.run(
        engine.create_workflow("case-1", payload, _FakeUser(), idempotency_key=None, request_hash="hash-1")
    )
    updated = asyncio.run(
        engine.repository.transition(run["_id"], 1, {"status": "running"}, event="resumed")
    )
    assert updated["state_version"] == 2
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(engine.repository.transition(run["_id"], 1, {"status": "running"}, event="stale"))
    assert exc_info.value.status_code == 409


def test_checkpoint_state_rejects_raw_legal_content():
    validate_checkpoint_state(
        {
            "run_id": "run-1",
            "case_id": "case-1",
            "input_snapshot_id": "snapshot-1",
            "input_snapshot_hash": "abc",
            "execution_status": "running",
        }
    )
    with pytest.raises(ValueError, match="Unsafe arbitration checkpoint fields"):
        validate_checkpoint_state({"run_id": "run-1", "evidence_text": "raw legal evidence"})
    with pytest.raises(ValueError, match="Invalid arbitration checkpoint value types"):
        validate_checkpoint_state({"run_id": {"raw": "draft content"}})
    with pytest.raises(ValueError, match="Oversized arbitration checkpoint string fields"):
        validate_checkpoint_state({"run_id": "x" * 1025})


def test_official_arbitration_graph_resumes_through_every_human_gate_with_minimal_state():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    commands = pytest.importorskip("langgraph.types")
    graph = build_arbitration_graph(checkpointer=memory.InMemorySaver())
    config = {"configurable": {"thread_id": "arbitration-phase2-all-gates"}}
    graph.invoke(
        {
            "run_id": "run-phase2",
            "thread_id": "arbitration:run-phase2",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "graph_version": "v1",
            "state_schema_version": 1,
            "state_version": 1,
            "input_snapshot_id": "snapshot-input",
            "input_snapshot_hash": "input-hash",
            "documents_selected": False,
            "user_direction_complete": False,
        },
        config,
    )

    state = graph.get_state(config)
    assert state.next == ("document_selection_gate",)
    validate_checkpoint_state(dict(state.values))

    resume_steps = [
        ({
            "documents_selected": True,
            "analysis_artifact_set_id": "analysis-set-1",
            "analysis_artifact_set_hash": "analysis-set-hash",
        }, "material_question_gate"),
        ({"user_direction_complete": True}, "matrix_review_gate"),
        ({"matrices_approved": True}, "readiness_approval_gate"),
        ({"readiness_approved": True, "plan_id": "plan-1", "plan_hash": "plan-hash"}, "plan_approval_gate"),
        (
            {
                "plan_approved": True,
                "draft_version_id": "version-1",
                "draft_version_hash": "version-hash",
                "validation_artifact_set_id": "validation-set-1",
                "validation_artifact_set_hash": "validation-set-hash",
                "validation_report_id": "validation-report-1",
                "validation_report_hash": "validation-report-hash",
                "validation_status": "passed",
                "validation_route": "legal_review",
            },
            "legal_review_gate",
        ),
        ({"legal_review_approved": True}, "draft_approval_gate"),
        ({"draft_approved": True}, "export_authorization_gate"),
        ({"export_authorized": True}, None),
    ]
    for version, (state_update, expected_gate) in enumerate(resume_steps, start=2):
        graph.invoke(
            commands.Command(
                resume={"run_id": "run-phase2"},
                update={**state_update, "state_version": version},
            ),
            config,
        )
        state = graph.get_state(config)
        validate_checkpoint_state(dict(state.values))
        if expected_gate:
            assert state.next == (expected_gate,)
        else:
            assert state.next == ()
            assert state.values["execution_status"] == "completed"

    redacted = redact_checkpoint(dict(state.values))
    assert all(
        state.values[flag]
        for flag in (
            "citations_validated",
            "assertions_validated",
            "legal_structure_validated",
            "new_matter_validated",
            "quantum_validated",
            "duplication_validated",
            "exhibits_validated",
            "source_drift_validated",
        )
    )
    assert redacted["run_id"] == "run-phase2"
    assert redacted["input_snapshot_id"]["redacted"] is True
    assert "snapshot-input" not in json.dumps(redacted)


def test_analysis_fanout_resume_consumes_snapshot_before_merged_artifact_exists():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    commands = pytest.importorskip("langgraph.types")
    graph = build_arbitration_graph(checkpointer=memory.InMemorySaver())
    config = {"configurable": {"thread_id": "arbitration-analysis-input-order"}}
    graph.invoke(
        {
            "run_id": "run-analysis-input-order",
            "thread_id": "arbitration:run-analysis-input-order",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "graph_version": "phase6-node-owned-v1",
            "state_schema_version": 2,
            "state_version": 1,
            "input_snapshot_id": "snapshot-input",
            "input_snapshot_hash": "input-hash",
            "documents_selected": False,
            "user_direction_complete": False,
        },
        config,
    )

    graph.invoke(
        commands.Command(
            resume={"run_id": "run-analysis-input-order"},
            update={"documents_selected": True, "state_version": 2},
        ),
        config,
    )

    state = graph.get_state(config)
    assert state.next == ("material_question_gate",)
    assert state.values["documents_analyzed"] is True
    assert state.values["quantum_analysis_complete"] is True
    assert not state.values.get("analysis_artifact_set_id")


def test_langgraph_create_retry_reuses_checkpoint_and_state_version():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    engine = LangGraphArbitrationEngine(db, checkpointer=memory.InMemorySaver())
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1",
        pleading_type="statement_of_claim",
        selected_document_ids=["doc-1"],
    )

    async def create():
        return await engine.create_workflow(
            "case-1",
            payload,
            _FakeUser(),
            idempotency_key="phase2-idempotent-create",
            request_hash="phase2-request-hash",
        )

    first = asyncio.run(create())
    second = asyncio.run(create())

    assert first["_id"] == second["_id"]
    # The graph-owned merge command performs one authoritative CAS after the
    # five read-only analysis nodes; an idempotent retry must reuse that state.
    assert first["state_version"] == second["state_version"] == 2
    assert second.get("last_checkpoint_at") is not None
    assert [event["event_type"] for event in db.arbitration_workflow_events.rows] == [
        "workflow_created",
        "graph_analysis_merged",
    ]


def test_langgraph_create_does_not_execute_the_v2_workflow_adapter(monkeypatch):
    memory = pytest.importorskip("langgraph.checkpoint.memory")

    async def forbidden_v2_create(*_args, **_kwargs):
        raise AssertionError("LangGraph must not execute the complete v2 workflow adapter")

    monkeypatch.setattr(ArbitrationV2WorkflowEngine, "create_workflow", forbidden_v2_create)
    db = _FakeDb()
    saver = memory.InMemorySaver()
    engine = LangGraphArbitrationEngine(db, checkpointer=saver)
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(
                draft_id="draft-1",
                pleading_type="statement_of_claim",
                selected_document_ids=["doc-1"],
            ),
            _FakeUser(),
            idempotency_key="graph-owned-create",
            request_hash="graph-owned-create-hash",
        )
    )

    assert not issubclass(LangGraphArbitrationEngine, ArbitrationV2WorkflowEngine)
    assert run["engine"] == "langgraph_v1"
    node_results = [
        row
        for row in db.arbitration_workflow_snapshots.rows
        if row.get("kind") == "analysis_node_result"
    ]
    assert {row["payload"]["node"] for row in node_results} == set(ANALYSIS_NODE_BRANCHES)
    assert sum(len(row["payload"]["branches"]) for row in node_results) == len(ANALYSIS_BRANCHES)
    node_effects = {
        row.get("effect_type")
        for row in db.arbitration_workflow_effects.rows
        if str(row.get("effect_type") or "").startswith("graph_node:")
    }
    assert {
        "graph_node:validate_intake",
        "graph_node:capture_input_snapshot",
        "graph_node:merge_evidence_and_matrices",
        *(f"graph_node:{node}" for node in ANALYSIS_NODE_BRANCHES),
    }.issubset(node_effects)
    checkpoint = build_arbitration_graph(checkpointer=saver).get_state(
        {"configurable": {"thread_id": run["thread_id"]}}
    )
    before_version = int(run["state_version"])
    replay = asyncio.run(
        engine.command_executor.execute(
            "merge_evidence_and_matrices",
            dict(checkpoint.values),
        )
    )
    assert replay["matrix_revision_hash"] == run["matrix_revision_hash"]
    assert db.arbitration_workflow_runs.rows[0]["state_version"] == before_version
    assert sum(
        1
        for row in db.arbitration_workflow_effects.rows
        if row.get("effect_type") == "graph_node:merge_evidence_and_matrices"
    ) == 1


def test_graph_node_retry_classification_is_transient_only():
    assert classify_retry(TimeoutError("provider timeout")) == "transient"
    assert classify_retry(ConnectionError("dependency unavailable")) == "transient"
    assert classify_retry(HTTPException(status_code=503, detail="unavailable")) == "transient"
    assert classify_retry(HTTPException(status_code=409, detail="artifact drift")) == "permanent"
    assert classify_retry(ValueError("invalid immutable input")) == "permanent"


def test_langgraph_export_authorization_does_not_mark_run_complete_before_export_effect(monkeypatch):
    db = _FakeDb()
    version = db.arbitration_draft_versions.rows[0]
    version["version_hash"] = immutable_version_hash(version)
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-export-node-owned",
            "thread_id": "arbitration:run-export-node-owned",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "langgraph_v1",
            "status": "awaiting_export_authorization",
            "current_node": "export_authorization_gate",
            "next_action": "authorize_export",
            "state_version": 7,
            "draft_version_id": version["_id"],
            "draft_version_hash": version["version_hash"],
            "validation_status": "passed",
            "validation_blockers": [],
            "authoritative_effects": [],
            "created_by": "draft-author",
            "last_material_editor_id": "draft-author",
        }
    )
    observed = {}

    async def capture_checkpoint(_engine, run, update):
        observed.update({"status": run["status"], "node": run["current_node"], **update})

    monkeypatch.setattr(LangGraphArbitrationEngine, "checkpoint_transition", capture_checkpoint)
    state = asyncio.run(
        ArbitrationWorkflowService(db).approve_gate(
            "case-1",
            "run-export-node-owned",
            "export",
            ArbitrationWorkflowApprovalRequest(
                state_version=7,
                artifact_hash=version["version_hash"],
                reviewer_role="export_authorizer",
            ),
            _FakeUser("export-approver"),
        )
    )

    assert observed == {
        "status": "running",
        "node": "create_filing_export",
        "export_authorized": True,
    }
    assert state["status"] == "running"
    assert state["current_node"] == "create_filing_export"
    assert not state.get("filing_export_id")


def test_langgraph_matrix_refresh_rebinds_evidence_and_reexecutes_node_effects():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append(
        {
            "_id": "claim-refresh",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "claim_head": "Late access",
            "approval_status": "approved",
            "readiness_status": "ready",
            "source_id": "doc-1",
            "source_revision_id": "version-1",
        }
    )
    engine = LangGraphArbitrationEngine(db, checkpointer=memory.InMemorySaver())
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(
                draft_id="draft-1",
                pleading_type="statement_of_claim",
                selected_document_ids=["doc-1"],
            ),
            _FakeUser(),
            idempotency_key="graph-refresh",
            request_hash="graph-refresh-hash",
        )
    )
    prior_evidence_hash = run["evidence_snapshot_hash"]
    prior_matrix_hash = run["matrix_revision_hash"]
    prior_effect_count = len(db.arbitration_workflow_effects.rows)
    db.arbitration_claim_matrix.rows[0]["claim_head"] = "Late access and prolongation"
    db.arbitration_claim_matrix.rows[0]["revision"] = 2

    refreshed = asyncio.run(engine.refresh_analysis(run, actor_id="reviewer-1"))

    assert refreshed["evidence_snapshot_hash"] != prior_evidence_hash
    assert refreshed["matrix_revision_hash"] != prior_matrix_hash
    assert refreshed["current_node"] in {"material_question_gate", "matrix_review_gate"}
    assert len(db.arbitration_workflow_effects.rows) >= prior_effect_count + len(ANALYSIS_NODE_BRANCHES) + 1


def test_langgraph_cancellation_checkpoint_does_not_traverse_downstream_nodes():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    saver = memory.InMemorySaver()
    engine = LangGraphArbitrationEngine(db, checkpointer=saver)
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key="phase2-cancel-checkpoint",
            request_hash="phase2-cancel-request",
        )
    )
    run.update(
        {
            "state_version": 2,
            "status": "cancelled",
            "current_node": "cancelled",
            "next_action": "cancelled",
        }
    )

    asyncio.run(
        engine.checkpoint_state_update(
            run,
            {
                "cancellation_requested": True,
                "execution_status": "cancelled",
                "current_node": "cancelled",
                "next_action": "cancelled",
            },
        )
    )
    graph = build_arbitration_graph(checkpointer=saver)
    state = graph.get_state({"configurable": {"thread_id": run["thread_id"]}})

    assert state.values["execution_status"] == "cancelled"
    assert state.values["current_node"] == "cancelled"
    assert state.values["cancellation_requested"] is True
    assert state.next == ()


def test_lagging_checkpoint_replays_cumulative_gate_receipts_to_current_gate():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    saver = memory.InMemorySaver()
    engine = LangGraphArbitrationEngine(db, checkpointer=saver)
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key="phase2-checkpoint-catchup",
            request_hash="phase2-checkpoint-catchup-request",
        )
    )
    run.update(
        {
            "state_version": 5,
            "documents_selected": True,
            "material_questions_required": True,
            "user_direction_snapshot_id": "direction-snapshot",
            "matrix_review_approval_receipt_id": "matrix-receipt",
            "readiness_approval_receipt_id": "readiness-receipt",
            "analysis_artifact_set_id": "analysis-catchup",
            "analysis_artifact_set_hash": "analysis-catchup-hash",
            "plan_id": "plan-catchup",
            "plan_hash": "plan-catchup-hash",
        }
    )

    asyncio.run(engine.checkpoint_transition(run, {}))
    graph = build_arbitration_graph(checkpointer=saver)
    state = graph.get_state({"configurable": {"thread_id": run["thread_id"]}})

    assert state.next == ("plan_approval_gate",)
    assert state.values["documents_selected"] is True
    assert state.values["user_direction_complete"] is True
    assert state.values["matrices_approved"] is True
    assert state.values["readiness_approved"] is True
    assert state.values["state_version"] == 5


def test_checkpoint_failure_is_marked_pending_without_rolling_back_run(monkeypatch):
    db = _FakeDb()
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-checkpoint-pending",
            "case_id": "case-1",
            "engine": "langgraph_v1",
            "state_version": 4,
            "status": "awaiting_matrix_review",
        }
    )

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("checkpoint database unavailable")

    monkeypatch.setattr(LangGraphArbitrationEngine, "checkpoint_transition", unavailable)
    service = ArbitrationWorkflowService(db)
    asyncio.run(
        service._sync_langgraph_checkpoint(
            db.arbitration_workflow_runs.rows[0],
            {"matrices_approved": True},
        )
    )

    stored = db.arbitration_workflow_runs.rows[0]
    assert stored["status"] == "awaiting_matrix_review"
    assert stored["checkpoint_sync_status"] == "pending"
    assert stored["checkpoint_sync_state_version"] == 4
    assert stored["checkpoint_sync_error_code"] == "RuntimeError"
    assert db.arbitration_workflow_events.rows[-1]["event_type"] == "checkpoint_sync_pending"


def test_stale_resume_is_rejected_before_snapshot_or_event_side_effects():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key=None,
            request_hash="phase2-stale-resume",
        )
    )
    asyncio.run(engine.repository.transition(run["_id"], 1, {"status": "awaiting_document_selection"}, event="advanced"))
    snapshot_count = len(db.arbitration_workflow_snapshots.rows)
    event_count = len(db.arbitration_workflow_events.rows)

    with pytest.raises(HTTPException) as stale:
        asyncio.run(
            ArbitrationWorkflowService(db).resume(
                "case-1",
                run["_id"],
                ArbitrationWorkflowResumeRequest(
                    state_version=1,
                    gate="document_selection",
                    selected_document_ids=["doc-1"],
                ),
                _FakeUser(),
            )
        )

    assert stale.value.status_code == 409
    assert len(db.arbitration_workflow_snapshots.rows) == snapshot_count
    assert len(db.arbitration_workflow_events.rows) == event_count


def test_stale_plan_approval_is_rejected_before_receipt_or_generation_effect():
    db = _FakeDb()
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-stale-plan",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_plan_approval",
            "current_node": "plan_approval_gate",
            "state_version": 2,
            "plan_hash": "p" * 64,
            "authoritative_effects": [],
        }
    )

    with pytest.raises(HTTPException) as stale:
        asyncio.run(
            ArbitrationWorkflowService(db).approve_gate(
                "case-1",
                "run-stale-plan",
                "plan",
                ArbitrationWorkflowApprovalRequest(
                    state_version=1,
                    artifact_hash="p" * 64,
                    reviewer_role="senior_legal_approver",
                ),
                _FakeUser(),
            )
        )

    assert stale.value.status_code == 409
    assert db.arbitration_workflow_approvals.rows == []
    assert db.arbitration_workflow_effects.rows == []


def test_resume_requires_exact_gate_and_rejects_document_changes_after_selection():
    db = _FakeDb()
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-matrix-refresh",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_matrix_review",
            "current_node": "matrix_review_gate",
            "state_version": 1,
        }
    )
    service = ArbitrationWorkflowService(db)

    with pytest.raises(HTTPException) as partial_gate:
        asyncio.run(
            service.resume(
                "case-1",
                "run-matrix-refresh",
                ArbitrationWorkflowResumeRequest(state_version=1, gate="review", decision="refresh"),
                _FakeUser(),
            )
        )
    assert partial_gate.value.status_code == 409

    with pytest.raises(HTTPException) as late_documents:
        asyncio.run(
            service.resume(
                "case-1",
                "run-matrix-refresh",
                ArbitrationWorkflowResumeRequest(
                    state_version=1,
                    gate="matrix_review",
                    decision="refresh",
                    selected_document_ids=["doc-1"],
                ),
                _FakeUser(),
            )
        )
    assert late_documents.value.status_code == 422


def test_force_v2_fallback_preserves_gate_and_binds_original_input_snapshot():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    engine = LangGraphArbitrationEngine(db, checkpointer=memory.InMemorySaver())
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key="phase2-fallback",
            request_hash="phase2-fallback",
        )
    )
    stored = db.arbitration_workflow_runs.rows[0]
    original_node = stored["current_node"]
    original_status = stored["status"]

    updated = asyncio.run(
        ArbitrationWorkflowService(db).fallback(
            "case-1",
            run["_id"],
            ArbitrationWorkflowFallbackRequest(state_version=1, reason="Checkpoint service unavailable"),
            _FakeUser(),
        )
    )

    assert updated["engine"] == "arbitration_v2"
    assert updated["current_node"] == original_node
    assert updated["status"] == original_status
    assert updated["fallback_from_engine"] == "langgraph_v1"
    assert updated["fallback_input_snapshot_id"] == run["input_snapshot_id"]
    assert updated["fallback_input_snapshot_hash"] == run["input_snapshot_hash"]
    assert updated["fallback_snapshot_binding_id"]
    assert updated["fallback_snapshot_binding_hash"]
    binding = next(
        row
        for row in db.arbitration_workflow_snapshots.rows
        if row.get("_id") == updated["fallback_snapshot_binding_id"]
    )
    assert {item["kind"] for item in binding["payload"]["snapshots"]} == {
        "input",
        "document_manifest",
        "evidence_manifest",
    }


def test_force_v2_fallback_cannot_overwrite_a_langgraph_candidate():
    db = _FakeDb()
    run = asyncio.run(
        ArbitrationV2WorkflowEngine(db).create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key="candidate-fallback",
            request_hash="candidate-fallback-hash",
        )
    )
    stored = db.arbitration_workflow_runs.rows[0]
    stored.update(
        {
            "engine": "langgraph_v1",
            "draft_version_id": "candidate-1",
            "draft_version_hash": "candidate-hash",
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ArbitrationWorkflowService(db).fallback(
                "case-1",
                run["_id"],
                ArbitrationWorkflowFallbackRequest(
                    state_version=1,
                    reason="Operator requested fallback after candidate creation",
                ),
                _FakeUser(),
            )
        )
    assert exc_info.value.status_code == 409
    assert stored["engine"] == "langgraph_v1"
    assert stored["draft_version_id"] == "candidate-1"


def test_parallel_and_serial_matrix_analysis_have_identical_revision_hash():
    db = _FakeDb()
    domain = ArbitrationWorkflowDomain(db)
    run = {"_id": "run-parity", "case_id": "case-1", "draft_id": "draft-1", "pleading_type": "statement_of_claim"}
    parallel = asyncio.run(domain.analyze(run, parallel=True))
    serial = asyncio.run(domain.analyze(run, parallel=False))
    assert parallel["revision_hash"] == serial["revision_hash"]
    assert parallel["revision_set_id"] == serial["revision_set_id"]
    assert parallel["analysis_artifact_set_hash"] == serial["analysis_artifact_set_hash"]
    assert [item["branch"] for item in parallel["analysis_artifacts"]] == sorted(ANALYSIS_BRANCHES)

    matrix_snapshot = next(row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "matrix_revision_set")
    assert matrix_snapshot["payload"]["authoritative"] is False
    assert matrix_snapshot["payload"]["review_status"] == "needs_review"
    assert all("source_revision_ids" in row and "evidence_status" in row for row in matrix_snapshot["payload"]["rows"])

    branch_snapshots = [row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "analysis_artifact"]
    chronology = next(row for row in branch_snapshots if row["payload"]["branch"] == "chronology")
    documents = next(row for row in branch_snapshots if row["payload"]["branch"] == "document_understanding")
    assert chronology["payload"]["matrices"] == ["chronology-matrix"]
    assert {row["matrix"] for row in chronology["payload"]["rows"]} <= {"chronology-matrix"}
    assert documents["payload"]["matrices"] == ["document-index"]
    assert {row["matrix"] for row in documents["payload"]["rows"]} <= {"document-index"}


def test_sod_workflow_requires_immutable_opponent_version():
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-1", pleading_type="statement_of_defence", selected_document_ids=["doc-1"]
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(engine.create_workflow("case-1", payload, _FakeUser(), idempotency_key=None, request_hash="sod-hash"))
    assert exc_info.value.status_code == 422


def test_rejoinder_workflow_pins_both_soc_and_sod_versions():
    db = _FakeDb()
    db.arbitration_drafts.rows.extend(
        [
            {"_id": "draft-sod", "case_id": "case-1", "draft_type": "statement_of_defence"},
            {"_id": "draft-rejoinder", "case_id": "case-1", "draft_type": "rejoinder"},
        ]
    )
    db.arbitration_draft_versions.rows.append(
        {
            "_id": "version-sod",
            "draft_id": "draft-sod",
            "version": 1,
            "version_hash": "sod-version-hash",
            "full_markdown": "1. The Respondent denies late access.\n\n2. The Respondent counterclaims delay damages.",
        }
    )
    payload = ArbitrationWorkflowCreateRequest(
        draft_id="draft-rejoinder",
        pleading_type="rejoinder",
        selected_document_ids=["doc-1"],
        opponent_pleadings=[
            {"draft_id": "draft-1", "version_id": "version-1"},
            {"draft_id": "draft-sod", "version_id": "version-sod"},
        ],
    )
    run = asyncio.run(
        ArbitrationV2WorkflowEngine(db).create_workflow(
            "case-1", payload, _FakeUser(), idempotency_key=None, request_hash="rejoinder-hash"
        )
    )
    snapshot = next(row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "opponent_pleading")
    assert {item["draft_type"] for item in snapshot["payload"]["pleadings"]} == {
        "statement_of_claim", "statement_of_defence"
    }
    assert run["opponent_pleading_snapshot_hash"] == snapshot["snapshot_hash"]
    assert all(item["parse_status"] == "parsed" for item in snapshot["payload"]["pleadings"])
    assert all(item["paragraph_count"] >= 1 for item in snapshot["payload"]["pleadings"])
    sod = next(item for item in snapshot["payload"]["pleadings"] if item["draft_type"] == "statement_of_defence")
    assert [paragraph["number"] for paragraph in sod["paragraphs"]] == ["1", "2"]
    assert all(paragraph["paragraph_hash"] for paragraph in sod["paragraphs"])


def test_reviewer_policy_rejects_wrong_role_and_author_self_approval():
    user = _FakeUser()
    with pytest.raises(HTTPException) as role_error:
        enforce_gate_role("export", "legal_reviewer", user)
    assert role_error.value.status_code == 403
    with pytest.raises(HTTPException) as separation_error:
        enforce_author_approver_separation(user, ["user-1"], gate="plan")
    assert separation_error.value.status_code == 409


def test_atomic_version_allocator_advances_from_existing_maximum():
    db = _FakeDb()
    repository = ArbitrationDraftingService(db).repo
    async def allocate():
        return await asyncio.gather(repository.next_version("draft-1"), repository.next_version("draft-1"))

    allocated = asyncio.run(allocate())
    assert sorted(allocated) == [2, 3]


def test_validation_remediation_is_bounded_and_does_not_change_draft_content():
    version = {
        "full_markdown": "Unsupported assertion [SRC-99]",
        "source_ledger": [],
        "structured_output": {"approval_blockers": ["Evidence is required"]},
        "validation_status": "blocked",
    }
    orchestrator = ArbitrationValidationOrchestrator()
    report = asyncio.run(orchestrator.evaluate(version))
    remediated = orchestrator.bounded_remediation(report)
    assert remediated["human_review_required"] is True
    assert remediated["remediation_cycle"] <= remediated["max_remediation_cycles"]
    assert version["full_markdown"] == "Unsupported assertion [SRC-99]"


def test_phase4_validation_fans_out_deterministically_and_only_deduplicates_existing_citations():
    version = {
        "_id": "version-phase4",
        "draft_id": "draft-1",
        "version": 2,
        "version_hash": "phase4-version-hash",
        "full_markdown": "The amount is INR 100. [S1: Notice] [S1: Notice]",
        "sections": [
            {
                "key": "facts",
                "heading": "Facts",
                "body": "The amount is INR 100. [S1: Notice] [S1: Notice]",
            }
        ],
        "source_ledger": [{"source_key": "S1", "source_id": "doc-1", "source_revision_id": "rev-1"}],
        "structured_output": {"approval_blockers": []},
        "validation_status": "passed",
    }
    orchestrator = ArbitrationValidationOrchestrator()
    first = asyncio.run(
        orchestrator.evaluate(version, dependency_hashes={"plan_hash": "plan-1"})
    )

    assert [artifact["branch"] for artifact in first["artifacts"]] == list(VALIDATION_BRANCHES)
    assert all(artifact["artifact_hash"] for artifact in first["artifacts"])
    assert first["route"] == "remediate"
    remediation = orchestrator.remediate(version, first)
    assert remediation is not None
    assert remediation["full_markdown"].count("[S1: Notice]") == 1
    assert "INR 100" in remediation["full_markdown"]
    assert remediation["source_keys_preserved"] is True
    assert remediation["amounts_preserved"] is True
    assert remediation["dates_preserved"] is True

    second = asyncio.run(
        orchestrator.evaluate(
            {**version, "full_markdown": remediation["full_markdown"], "sections": remediation["sections"]},
            dependency_hashes={"plan_hash": "plan-1"},
            remediation_cycle=1,
        )
    )
    assert second["status"] == "passed"
    assert second["route"] == "legal_review"
    assert second["remediation_cycle"] == 1


def test_phase4_plan_gate_persists_validation_artifacts_and_blocks_unsupported_citation_approval():
    db = _FakeDb()
    version = db.arbitration_draft_versions.rows[0]
    version.update(
        {
            "full_markdown": "Unsupported assertion [S99: Missing source].",
            "validation_status": "passed",
            "structured_output": {"approval_blockers": []},
        }
    )
    version["version_hash"] = immutable_version_hash(version)
    plan_hash = "q" * 64
    db.arbitration_plans.rows.append(
        {"_id": "plan-phase4-blocked", "run_id": "run-phase4-blocked", "plan_hash": plan_hash}
    )
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-phase4-blocked",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_plan_approval",
            "current_node": "plan_approval_gate",
            "next_action": "approve_plan",
            "state_version": 1,
            "plan_hash": plan_hash,
            "plan_id": "plan-phase4-blocked",
            "created_by": "author-user",
            "last_material_editor_id": "editor-user",
            "authoritative_effects": [],
        }
    )
    db.arbitration_workflow_effects.rows.append(
        {
            "_id": "effect-phase4-blocked",
            "run_id": "run-phase4-blocked",
            "effect_key": f"run-phase4-blocked:generate:{plan_hash}",
            "effect_type": "draft_generation",
            "input_hash": plan_hash,
            "status": "completed",
            "output_refs": {"draft_version_id": version["_id"], "draft_version_hash": version["version_hash"]},
        }
    )
    service = ArbitrationWorkflowService(db)
    state = asyncio.run(
        service.approve_gate(
            "case-1",
            "run-phase4-blocked",
            "plan",
            ArbitrationWorkflowApprovalRequest(
                state_version=1,
                artifact_hash=plan_hash,
                reviewer_role="senior_legal_approver",
            ),
            _FakeUser(),
        )
    )

    assert state["status"] == "awaiting_legal_review"
    assert state["next_action"] == "revise_draft"
    assert state["validation_status"] == "blocked"
    assert state["validation_artifact_set_hash"]
    assert db.arbitration_plans.rows[0]["status"] == "approved"
    assert db.arbitration_plans.rows[0]["approval_receipt_id"]
    assert {row["kind"] for row in db.arbitration_workflow_snapshots.rows if row["kind"].startswith("validation_")} >= {
        *(f"validation_{branch}" for branch in VALIDATION_BRANCHES),
        "validation_report",
        "validation_artifact_set",
    }

    approval_count = len(db.arbitration_workflow_approvals.rows)
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(
            service.approve_gate(
                "case-1",
                "run-phase4-blocked",
                "legal_review",
                ArbitrationWorkflowApprovalRequest(
                    state_version=state["state_version"],
                    artifact_hash=state["draft_version_hash"],
                    reviewer_role="legal_reviewer",
                ),
                _FakeUser(),
            )
        )
    assert blocked.value.status_code == 409
    assert len(db.arbitration_workflow_approvals.rows) == approval_count


def test_phase4_bounded_remediation_creates_one_parent_linked_candidate_without_new_values():
    db = _FakeDb()
    parent = db.arbitration_draft_versions.rows[0]
    parent.update(
        {
            "full_markdown": "Claimed amount INR 100. [S1: Notice] [S1: Notice]",
            "sections": [
                {
                    "key": "facts",
                    "heading": "Facts",
                    "body": "Claimed amount INR 100. [S1: Notice] [S1: Notice]",
                }
            ],
            "validation_status": "passed",
            "structured_output": {"approval_blockers": []},
        }
    )
    parent["version_hash"] = immutable_version_hash(parent)
    plan_hash = "r" * 64
    db.arbitration_plans.rows.append(
        {"_id": "plan-phase4-remediation", "run_id": "run-phase4-remediation", "plan_hash": plan_hash}
    )
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-phase4-remediation",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_plan_approval",
            "current_node": "plan_approval_gate",
            "next_action": "approve_plan",
            "state_version": 1,
            "plan_hash": plan_hash,
            "plan_id": "plan-phase4-remediation",
            "created_by": "author-user",
            "last_material_editor_id": "editor-user",
            "authoritative_effects": [],
        }
    )
    db.arbitration_workflow_effects.rows.append(
        {
            "_id": "effect-phase4-remediation",
            "run_id": "run-phase4-remediation",
            "effect_key": f"run-phase4-remediation:generate:{plan_hash}",
            "effect_type": "draft_generation",
            "input_hash": plan_hash,
            "status": "completed",
            "output_refs": {"draft_version_id": parent["_id"], "draft_version_hash": parent["version_hash"]},
        }
    )

    state = asyncio.run(
        ArbitrationWorkflowService(db).approve_gate(
            "case-1",
            "run-phase4-remediation",
            "plan",
            ArbitrationWorkflowApprovalRequest(
                state_version=1,
                artifact_hash=plan_hash,
                reviewer_role="senior_legal_approver",
            ),
            _FakeUser(),
        )
    )
    candidate = max(db.arbitration_draft_versions.rows, key=lambda item: int(item.get("version") or 0))

    assert len(db.arbitration_draft_versions.rows) == 2
    assert candidate["parent_version_id"] == parent["_id"]
    assert candidate["parent_version"] == parent["version"]
    assert candidate["full_markdown"].count("[S1: Notice]") == 1
    assert "INR 100" in candidate["full_markdown"]
    assert candidate["source_ledger"] == parent["source_ledger"]
    assert state["draft_version_id"] == candidate["_id"]
    assert state["validation_status"] == "passed"
    assert state["remediation_cycle"] == 1
    assert state["remediation_artifact_hash"]


def test_phase4_human_revision_refresh_rebinds_validation_to_latest_immutable_candidate():
    db = _FakeDb()
    corrected = {
        **db.arbitration_draft_versions.rows[0],
        "_id": "version-corrected",
        "version": 2,
        "full_markdown": "Supported assertion [S1: Delay notice].",
        "validation_status": "passed",
        "structured_output": {"approval_blockers": []},
        "created_by": "draft-editor",
    }
    corrected["version_hash"] = immutable_version_hash(corrected)
    db.arbitration_draft_versions.rows.append(corrected)
    db.arbitration_workflow_runs.rows.append(
        {
            "_id": "run-phase4-refresh",
            "case_id": "case-1",
            "draft_id": "draft-1",
            "pleading_type": "statement_of_claim",
            "engine": "arbitration_v2",
            "status": "awaiting_legal_review",
            "current_node": "legal_review_gate",
            "next_action": "revise_draft",
            "state_version": 1,
            "draft_version_id": "version-1",
            "draft_version_hash": "blocked-version-hash",
            "validation_status": "blocked",
            "validation_blockers": [{"code": "unknown_source_key", "message": "Missing source"}],
            "authoritative_effects": ["draft_version"],
        }
    )

    state = asyncio.run(
        ArbitrationWorkflowService(db).resume(
            "case-1",
            "run-phase4-refresh",
            ArbitrationWorkflowResumeRequest(
                state_version=1,
                gate="legal_review",
                decision="refresh_candidate",
            ),
            _FakeUser(),
        )
    )

    assert state["draft_version_id"] == corrected["_id"]
    assert state["draft_version_hash"] == corrected["version_hash"]
    assert state["validation_status"] == "passed"
    assert state["next_action"] == "legal_review"
    assert state["validation_artifact_set_hash"]


def test_deterministic_rejoinder_reply_remains_review_only():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    result = asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "rejoinder-reply",
            payload=ArbitrationAgentRunRequest(draft_id="draft-1", options={}),
            current_user=_FakeUser(),
        )
    )

    assert result["created_records"] == []
    assert not db.arbitration_rejoinder_matrix.rows


def test_jurisdiction_agent_seeds_pleading_timetable_and_amendment_rule():
    db = _FakeDb()
    case = db.arbitration_cases.rows[0]

    asyncio.run(
        run_arbitration_agent(
            db,
            case,
            "jurisdiction",
            payload=ArbitrationAgentRunRequest(
                options={
                    "pleading_timetable": {"statement_of_defence": "2020-01-01"},
                    "amendment_leave_required": True,
                }
            ),
            current_user=_FakeUser(),
        )
    )

    rows = db.arbitration_jurisdiction_matrix.rows
    timetable = {row.get("pleading_stage"): row for row in rows if row.get("check_type") == "pleading_timetable"}
    assert {"statement_of_claim", "statement_of_defence", "rejoinder"}.issubset(set(timetable))
    assert timetable["statement_of_defence"]["timetable_status"] == "overdue"
    assert timetable["rejoinder"]["timetable_status"] == "not_scheduled"
    amendment = [row for row in rows if row.get("check_type") == "amendment_rule"]
    assert amendment and amendment[0]["leave_required"] is True


def test_readiness_flags_overdue_pleading_timetable():
    db = _FakeDb()
    db.arbitration_jurisdiction_matrix.rows.append(
        {
            "_id": "tt-1",
            "case_id": "case-1",
            "check_type": "pleading_timetable",
            "pleading_stage": "statement_of_defence",
            "timetable_status": "overdue",
            "approval_status": "approved",
        }
    )

    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness("case-1"))
    blockers = {check["check_key"]: check["status"] for check in readiness["blockers"]}
    assert blockers.get("pleading_timetable") == "needs_legal_review"


def test_exporter_renders_markdown_tables_and_contents():
    from backend.rbac_backend.services.arbitration_drafting.exporter import ArbitrationDraftExporter

    markdown = (
        "# EOT Statement of Claim\n\n"
        "## Claim Summary\n\n"
        "| Claim | Amount |\n|---|---|\n| EOT | INR 100 |\n| Prolongation | INR 200 |\n\n"
        "## Relief\n\nAward the sums claimed.\n"
    )

    docx_bytes = ArbitrationDraftExporter.build_docx({"full_markdown": markdown})
    pdf_bytes = ArbitrationDraftExporter.build_pdf({"full_markdown": markdown})

    assert docx_bytes[:2] == b"PK"
    assert pdf_bytes[:4] == b"%PDF"
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    # Table cell text and the generated Contents list are present.
    assert "Prolongation" in document_xml
    assert "Contents" in document_xml
    assert "Claim Summary" in document_xml


def test_arbitration_construction_fixture_contains_all_four_governed_routes():
    fixture_path = Path(__file__).resolve().parents[3] / "client" / "e2e" / "fixtures" / "arbitration-construction-dispute.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    draft_types = {draft["draft_type"] for draft in data["drafts"]}
    assert {"statement_of_claim", "statement_of_defence", "counterclaim", "rejoinder"} == draft_types
    assert data["case"]["dispute_type"] == "eot_delay"
    assert data["matrices"]["document-index"]
    assert data["matrices"]["claim-matrix"]
    assert data["matrices"]["defence-matrix"]
    assert data["matrices"]["rejoinder-matrix"]


def test_matrix_multispecialty_approval_rejects_same_actor_reuse():
    db = _FakeDb()
    db.arbitration_claim_matrix.rows.append({
        "_id": "claim-row-separation",
        "case_id": "case-1",
        "review_status": "under_review",
        "review_required_roles": ["legal", "quantum"],
        "review_completed_roles": [],
        "review_assignments": [
            {"reviewer_role": "legal", "reviewer_user_id": "user-1", "status": "assigned"},
            {"reviewer_role": "quantum", "reviewer_user_id": "user-1", "status": "assigned"},
        ],
    })
    service = ArbitrationCaseWorkspaceService(db)

    asyncio.run(service.review_matrix_row(
        "case-1", "claim-matrix", "claim-row-separation",
        ArbitrationMatrixReviewRequest(action="approve", reviewer_role="legal"),
        _FakeUser("user-1"),
    ))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.review_matrix_row(
            "case-1", "claim-matrix", "claim-row-separation",
            ArbitrationMatrixReviewRequest(action="approve", reviewer_role="quantum"),
            _FakeUser("user-1"),
        ))
    assert exc_info.value.status_code == 409


def test_case_linked_full_generation_requires_governed_approved_plan(monkeypatch):
    db = _FakeDb()
    service = ArbitrationDraftingService(db)

    async def ready(*args, **kwargs):
        return None

    monkeypatch.setattr(service.case_workspace, "assert_case_ready_for_draft", ready)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.generate("draft-1", ArbitrationGenerateRequest(), _FakeUser()))
    assert exc_info.value.status_code == 409
    assert "approved pleading-plan workflow gate" in str(exc_info.value.detail)


def test_readiness_fails_closed_when_approved_source_is_missing():
    db = _FakeDb()
    db.documents.rows = [row for row in db.documents.rows if row.get("_id") != "doc-1"]
    readiness = asyncio.run(ArbitrationCaseWorkspaceService(db).readiness("case-1"))
    assert readiness["status"] == "blocked"
    assert any(check["check_key"] == "authoritative_source_resolution" for check in readiness["blockers"])


def test_pending_approval_is_committed_only_after_run_cas_reference():
    db = _FakeDb()
    run = {
        "_id": "run-pending-recovery",
        "case_id": "case-1",
        "status": "awaiting_plan_approval",
        "plan_approval_receipt_id": "receipt-pending",
    }
    receipt = {
        "_id": "receipt-pending",
        "run_id": run["_id"],
        "gate": "plan",
        "receipt_status": "pending",
    }
    db.arbitration_workflow_runs.rows.append(run)
    db.arbitration_workflow_approvals.rows.append(receipt)

    asyncio.run(ArbitrationWorkflowRepository(db).reconcile_pending_approvals(run))
    assert receipt["receipt_status"] == "committed"
    assert receipt["committed_at"] is not None

    orphan = {"_id": "orphan", "run_id": run["_id"], "gate": "draft", "receipt_status": "pending"}
    db.arbitration_workflow_approvals.rows.append(orphan)
    asyncio.run(ArbitrationWorkflowRepository(db).reconcile_pending_approvals(run))
    assert orphan["receipt_status"] == "pending"


def test_missing_checkpoint_is_reconstructed_from_redacted_run_ledger():
    memory = pytest.importorskip("langgraph.checkpoint.memory")
    db = _FakeDb()
    saver = memory.InMemorySaver()
    engine = LangGraphArbitrationEngine(db, checkpointer=saver)
    run = {
        "_id": "run-post-ttl",
        "thread_id": "arbitration:run-post-ttl",
        "case_id": "case-1",
        "draft_id": "draft-1",
        "pleading_type": "statement_of_claim",
        "state_version": 5,
        "status": "awaiting_matrix_review",
        "documents_selected": True,
        "material_questions_required": False,
        "analysis_artifact_set_id": "analysis-set",
        "analysis_artifact_set_hash": "analysis-hash",
        "matrix_review_approval_receipt_id": "matrix-receipt",
    }

    asyncio.run(engine.checkpoint_transition(run, {"matrices_approved": True}))
    state = build_arbitration_graph(checkpointer=saver).get_state(
        {"configurable": {"thread_id": run["thread_id"]}}
    )
    assert state.next == ("readiness_approval_gate",)
    assert state.values["analysis_artifact_set_id"] == "analysis-set"
    assert "full_markdown" not in state.values


def test_formal_acceptance_receipt_rejects_forgery_expiry_and_wrong_scope():
    now = datetime.now(timezone.utc)
    receipt = {
        "_id": "acceptance-1",
        "status": "accepted",
        "criteria": {str(index): "passed" for index in range(1, 15)},
        "evidence_hashes": {str(index): f"{index:064x}" for index in range(1, 15)},
        "stakeholder_signoffs": ["security-signoff", "legal-signoff"],
        "acceptance_bundle_hash": "f" * 64,
        "evidence_record_ids": [f"evidence-{index}" for index in range(1, 15)],
        "signoff_receipt_ids": ["security-signoff", "legal-signoff"],
        "signoff_actor_ids": ["security-user", "legal-user"],
        "signoff_roles": ["security", "senior_legal_counsel"],
        "organization_ids": ["org-1"],
        "project_ids": ["project-1"],
        "accepted_at": now,
        "expires_at": now + timedelta(days=30),
    }
    receipt["receipt_hash"] = acceptance_hash(receipt)
    receipt["server_signature"] = sign_acceptance(receipt["receipt_hash"])

    assert verify_acceptance_receipt(
        receipt, receipt_id="acceptance-1", receipt_hash=receipt["receipt_hash"],
        organization_id="org-1", project_id="project-1", now=now,
    )
    assert not verify_acceptance_receipt(
        {**receipt, "server_signature": "0" * 64}, receipt_id="acceptance-1",
        receipt_hash=receipt["receipt_hash"], organization_id="org-1", project_id="project-1", now=now,
    )
    assert not verify_acceptance_receipt(
        receipt, receipt_id="acceptance-1", receipt_hash=receipt["receipt_hash"],
        organization_id="org-other", project_id="project-1", now=now,
    )
    assert not verify_acceptance_receipt(
        receipt, receipt_id="acceptance-1", receipt_hash=receipt["receipt_hash"],
        organization_id="org-1", project_id="project-1", now=now + timedelta(days=31),
    )


def test_production_acceptance_rejects_client_assertions_without_server_backed_evidence():
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    criteria = {str(index): "passed" for index in range(1, 15)}
    evidence_hashes = {str(index): f"{index:064x}" for index in range(1, 15)}
    db.arbitration_acceptance_evidence.rows.extend(
        {
            "_id": f"evidence-{criterion}",
            "criterion": criterion,
            "evidence_hash": evidence_hashes[criterion],
            "organization_id": "org-1",
            "project_id": "project-1",
            "status": "passed",
            "execution_mode": "real_execution",
            "executed_at": now,
            "recorded_by": "acceptance-runner",
        }
        for criterion in criteria
    )

    unresolved = asyncio.run(
        resolve_server_backed_acceptance(
            db,
            criteria=criteria,
            evidence_hashes=evidence_hashes,
            stakeholder_signoff_ids=["claimed-legal", "claimed-security"],
            organization_id="org-1",
            project_id="project-1",
        )
    )
    assert unresolved["valid"] is False
    assert unresolved["reason"] == "bound_distinct_legal_and_operational_signoffs_missing"

    db.arbitration_acceptance_signoffs.rows.extend(
        [
            {
                "_id": "legal-signoff",
                "organization_id": "org-1",
                "project_id": "project-1",
                "acceptance_bundle_hash": unresolved["bundle_hash"],
                "decision": "accepted",
                "actor_id": "legal-user",
                "actor_role": "senior_legal_counsel",
                "signed_at": now,
            },
            {
                "_id": "security-signoff",
                "organization_id": "org-1",
                "project_id": "project-1",
                "acceptance_bundle_hash": unresolved["bundle_hash"],
                "decision": "accepted",
                "actor_id": "security-user",
                "actor_role": "security",
                "signed_at": now,
            },
        ]
    )
    resolved = asyncio.run(
        resolve_server_backed_acceptance(
            db,
            criteria=criteria,
            evidence_hashes=evidence_hashes,
            stakeholder_signoff_ids=["legal-signoff", "security-signoff"],
            organization_id="org-1",
            project_id="project-1",
        )
    )
    assert resolved["valid"] is True
    assert len(resolved["evidence_record_ids"]) == 14
    assert resolved["signoff_actor_ids"] == ["legal-user", "security-user"]


def test_validator_blocks_fluent_unsupported_factual_assertion():
    report = ArbitrationDraftValidator().validation_report(
        {
            "draft": {"case_id": "case-1", "draft_type": "statement_of_claim"},
            "source_ledger": [{"source_key": "S1", "label": "Notice", "snippet": "Access was delayed."}],
        },
        "The contractor completed every required milestone before the employer terminated the contract without cause.",
    )
    assert any("factual assertions" in blocker for blocker in report["approval_blockers"])


def test_workflow_assertion_validation_persists_redacted_claim_source_spans():
    orchestrator = ArbitrationValidationOrchestrator()
    report = asyncio.run(orchestrator.evaluate(
        {
            "_id": "version-claim-spans",
            "draft_id": "draft-1",
            "version": 2,
            "version_hash": "a" * 64,
            "full_markdown": (
                "The Employer issued the notice under Clause 14.2 on 2026-07-01 [S1].\n"
                "The Contractor claimed USD 125,000 from Example Employer."
            ),
            "source_ledger": [{"source_key": "S1"}],
        },
        dependency_hashes={"matrix_revision_hash": "b" * 64},
        branches=("assertions",),
    ))

    assert [artifact["branch"] for artifact in report["artifacts"]] == ["assertions"]
    spans = report["artifacts"][0]["claim_source_spans"]
    assert spans[0]["source_keys"] == ["S1"]
    assert spans[0]["support_status"] == "supported"
    assert spans[1]["support_status"] == "needs_review"
    assert "text" not in spans[0]
    assert any(issue["code"] == "unsupported_claim_span" for issue in report["blockers"])


def test_filing_worker_refuses_matrix_drift_after_immutable_authorization():
    db = _FakeDb()
    _authorize_filing_fixture(db)
    service = ArbitrationCaseWorkspaceService(db)
    payload = asyncio.run(service.filing_bundle_payload("case-1"))
    authorization = asyncio.run(service._authorize_filing_bundle(payload, "zip", _FakeUser("export-user")))
    effect_key = f"arbitration_filing_bundle:{authorization['bundle_hash']}:zip"
    db.arbitration_bundle_exports.rows.append({
        "_id": "export-drift",
        "case_id": "case-1",
        "format": "zip",
        "status": "queued",
        "effect_key": effect_key,
        "bundle_hash": authorization["bundle_hash"],
        "export_authorization_id": authorization["_id"],
    })
    db.arbitration_document_index.rows[0]["title"] = "Mutated after authorization"

    with pytest.raises((RuntimeError, HTTPException)) as exc_info:
        asyncio.run(service.execute_filing_bundle_export_job(
            "case-1", "export-drift", "zip", effect_key=effect_key,
            lease_token="lease-drift", worker_name="test-worker",
        ))
    assert "drift" in str(exc_info.value).lower() or "current revision-bound" in str(exc_info.value).lower()


def test_sod_without_counterclaim_and_counterclaim_route_are_conditionally_distinct():
    db = _FakeDb()
    db.arbitration_drafts.rows.extend([
        {"_id": "draft-sod-route", "case_id": "case-1", "draft_type": "statement_of_defence"},
        {"_id": "draft-counterclaim-route", "case_id": "case-1", "draft_type": "counterclaim"},
    ])
    db.arbitration_counterclaim_matrix.rows.append({
        "_id": "counterclaim-route-row",
        "case_id": "case-1",
        "draft_id": "draft-counterclaim-route",
        "counterclaim_no": "CC-1",
        "approval_status": "approved",
        "source_id": "doc-1",
        "source_revision_id": "version-1",
    })
    engine = ArbitrationV2WorkflowEngine(db)

    sod = asyncio.run(engine.create_workflow(
        "case-1",
        ArbitrationWorkflowCreateRequest(
            draft_id="draft-sod-route",
            pleading_type="statement_of_defence",
            selected_document_ids=["doc-1"],
            opponent_pleadings=[{"draft_id": "draft-1", "version_id": "version-1"}],
        ),
        _FakeUser(), idempotency_key="route-sod", request_hash="route-sod-hash",
    ))
    counterclaim = asyncio.run(engine.create_workflow(
        "case-1",
        ArbitrationWorkflowCreateRequest(
            draft_id="draft-counterclaim-route",
            pleading_type="counterclaim",
            selected_document_ids=["doc-1"],
        ),
        _FakeUser(), idempotency_key="route-counterclaim", request_hash="route-counterclaim-hash",
    ))

    assert not any(
        blocker.get("matrix") == "counterclaim-matrix"
        for blocker in sod.get("blockers") or []
    )
    assert not any(
        blocker.get("matrix") == "counterclaim-matrix"
        for blocker in counterclaim.get("blockers") or []
    )
    assert sod["opponent_pleading_snapshot_hash"]
    assert not counterclaim.get("opponent_pleading_snapshot_hash")
