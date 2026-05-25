from rbac_backend.services.llm_config_service import DEFAULT_DRAFT_PROMPT_TEMPLATE


def test_default_draft_prompt_template_accepts_langgraph_payload() -> None:
    prompt = DEFAULT_DRAFT_PROMPT_TEMPLATE.format(
        sender_profile="Contractor",
        recipient="Engineer",
        active_contract_workspace="organization=org1; project=proj1; letter_id=letter-1",
        subject="Notice of delay",
        requirements="Current facts supplied by user.",
        plan="Use factual chronology before entitlement position.",
        sources="[S1] contract clause: Clause 8.4: extension of time wording",
        prior_correspondence="[P1] 2026-04-01 - Prior notice: related context",
        profile_patterns="Posture: protective and entitlement-focused.",
    )

    assert "Sender profile: Contractor" in prompt
    assert "Source Integrity Notes" in prompt
    assert "Learning Update" in prompt
