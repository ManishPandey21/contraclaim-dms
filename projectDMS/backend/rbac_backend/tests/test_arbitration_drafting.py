import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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
from backend.rbac_backend.services.arbitration_drafting.workflow_domain import ArbitrationWorkflowDomain
from backend.rbac_backend.services.arbitration_drafting.workflow_repository import ArbitrationWorkflowRepository
from backend.rbac_backend.services.arbitration_drafting.workflow_service import ArbitrationWorkflowService
from backend.rbac_backend.services.arbitration_drafting.approval_policy import (
    enforce_author_approver_separation,
    enforce_gate_role,
)
from backend.rbac_backend.services.arbitration_drafting.workflow_validation import ArbitrationValidationOrchestrator
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
            if key == "deleted_at" and isinstance(expected, dict) and expected.get("$exists") is False:
                if "deleted_at" in row:
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
    id = "user-1"
    email = "user@example.test"


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
            "approved_at": "2026-07-21T00:00:00",
        }
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
    }

    generated = ArbitrationDraftGenerator().generate(context)

    assert "[S1: CPL/2025/0142]" in generated["full_markdown"]
    assert "Award extension of time." in generated["full_markdown"]


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

    assert stable_generation_input_hash(base_context) == stable_generation_input_hash(same_inputs)
    assert stable_generation_input_hash(base_context) != stable_generation_input_hash(changed_inputs)


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
                required_roles=["legal"],
                comment="Please review entitlement.",
            ),
            _FakeUser(),
        )
    )

    assert reviewed["review_status"] == "under_review"
    assert reviewed["approval_status"] == "needs_review"
    assert reviewed["review_required_roles"] == ["legal"]
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
            "review_assignments": [{"reviewer_role": "legal", "status": "assigned"}],
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
            _FakeUser(),
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

    async def fake_submit(name, func, *args, **kwargs):
        assert name == "arbitration-filing-bundle:zip"
        assert args[0] == "case-1"
        return "job-export-1"

    monkeypatch.setattr(case_workspace_module, "submit_background_job", fake_submit)

    queued = asyncio.run(service.queue_filing_bundle_export("case-1", "zip", _FakeUser()))
    completed = asyncio.run(service.execute_filing_bundle_export_job("case-1", queued["_id"], "zip"))
    artifact = asyncio.run(service.get_filing_bundle_export_content("case-1", queued["_id"]))

    assert queued["status"] == "queued"
    assert queued["background_job_id"] == "job-export-1"
    assert completed["status"] == "completed"
    assert completed["content_length"] > 0
    assert artifact["content"][:2] == b"PK"
    assert artifact["filename"] == "arbitration-case-bundle.zip"


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
    # C-2's source (doc-review) has no stored file: recorded, not silently dropped.
    assert by_exhibit["C-2"]["error"]
    assert by_exhibit["C-2"]["path"] is None


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
    assert len(db.arbitration_workflow_snapshots.rows) == 5
    assert {row["kind"] for row in db.arbitration_workflow_snapshots.rows} == {
        "input", "document_manifest", "matrix_revision_set", "evidence_manifest", "material_questions"
    }
    manifest = next(row for row in db.arbitration_workflow_snapshots.rows if row["kind"] == "document_manifest")
    assert manifest["payload"]["documents"][0]["document_id"] == "doc-1"
    assert "ocrText" not in json.dumps(manifest, default=str)


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
    assert len(db.arbitration_workflow_snapshots.rows) == 5
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
    asyncio.run(repository.complete_effect("run-1:generate:plan-1", output))
    asyncio.run(repository.complete_effect("run-1:generate:plan-1", output))
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            repository.complete_effect(
                "run-1:generate:plan-1",
                {"draft_version_id": "version-2", "draft_version_hash": "version-hash-2"},
            )
        )
    assert conflict.value.status_code == 409


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
        ("documents_selected", "material_question_gate"),
        ("user_direction_complete", "matrix_review_gate"),
        ("matrices_approved", "readiness_approval_gate"),
        ("readiness_approved", "plan_approval_gate"),
        ("plan_approved", "legal_review_gate"),
        ("legal_review_approved", "draft_approval_gate"),
        ("draft_approved", "export_authorization_gate"),
        ("export_authorized", None),
    ]
    for version, (flag, expected_gate) in enumerate(resume_steps, start=2):
        graph.invoke(
            commands.Command(
                resume={"run_id": "run-phase2"},
                update={flag: True, "state_version": version},
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
    assert redacted["run_id"] == "run-phase2"
    assert redacted["input_snapshot_id"]["redacted"] is True
    assert "snapshot-input" not in json.dumps(redacted)


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
    assert first["state_version"] == second["state_version"] == 1
    assert second.get("last_checkpoint_at") is not None
    assert [event["event_type"] for event in db.arbitration_workflow_events.rows] == [
        "workflow_created",
    ]


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
    assert state.next == ("document_selection_gate",)


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
    db = _FakeDb()
    engine = ArbitrationV2WorkflowEngine(db)
    run = asyncio.run(
        engine.create_workflow(
            "case-1",
            ArbitrationWorkflowCreateRequest(pleading_type="statement_of_claim"),
            _FakeUser(),
            idempotency_key=None,
            request_hash="phase2-fallback",
        )
    )
    stored = db.arbitration_workflow_runs.rows[0]
    stored["engine"] = "langgraph_v1"
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


def test_parallel_and_serial_matrix_analysis_have_identical_revision_hash():
    db = _FakeDb()
    domain = ArbitrationWorkflowDomain(db)
    run = {"_id": "run-parity", "case_id": "case-1", "draft_id": "draft-1", "pleading_type": "statement_of_claim"}
    parallel = asyncio.run(domain.analyze(run, parallel=True))
    serial = asyncio.run(domain.analyze(run, parallel=False))
    assert parallel["revision_hash"] == serial["revision_hash"]
    assert parallel["revision_set_id"] == serial["revision_set_id"]


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
        {"_id": "version-sod", "draft_id": "draft-sod", "version": 1, "version_hash": "sod-version-hash"}
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


def test_arbitration_construction_e2e_fixture_covers_soc_sod_and_rejoinder():
    fixture_path = Path(__file__).resolve().parents[3] / "client" / "e2e" / "fixtures" / "arbitration-construction-dispute.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    draft_types = {draft["draft_type"] for draft in data["drafts"]}
    assert {"statement_of_claim", "statement_of_defence", "rejoinder"}.issubset(draft_types)
    assert data["case"]["dispute_type"] == "eot_delay"
    assert data["matrices"]["document-index"]
    assert data["matrices"]["claim-matrix"]
    assert data["matrices"]["defence-matrix"]
    assert data["matrices"]["rejoinder-matrix"]
