from backend.rbac_backend.models.arbitration_drafting import (
    ArbitrationDraftCreate,
    ArbitrationDisputeType,
    ArbitrationPartyRole,
    ArbitrationDraftType,
)
from backend.rbac_backend.services.arbitration_drafting.generator import ArbitrationDraftGenerator
from backend.rbac_backend.services.arbitration_drafting.service import stable_generation_input_hash
from backend.rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator


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
