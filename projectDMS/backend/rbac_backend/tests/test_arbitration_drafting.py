from backend.rbac_backend.models.arbitration_drafting import (
    ArbitrationDraftCreate,
    ArbitrationDisputeType,
    ArbitrationPartyRole,
    ArbitrationDraftType,
)
from backend.rbac_backend.services.arbitration_drafting.generator import ArbitrationDraftGenerator
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
