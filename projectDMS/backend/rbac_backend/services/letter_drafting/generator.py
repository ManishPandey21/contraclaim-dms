from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...config.document_processing_config import DocumentProcessingConfig
from ...models.letter import Letter
from ...models.letter_drafting import DraftArtifact, DraftContextBundle, SourceEvidence
from ...retrieval.generator import LLMGenerator
from .context import condense_text
from .prompts import (
    DRAFT_PROMPT_KEY,
    STRATEGY_PROMPT_KEY,
    PromptRegistry,
    render_prompt,
)


PROFILE_PATTERNS = {
    "contractor": (
        "Protective, commercially aware, entitlement-focused. Sequence factual chronology, "
        "contractual basis, impact, entitlement position, and reservation of rights."
    ),
    "employer": (
        "Authoritative, governance-oriented, compliance-focused. Sequence matter reference, "
        "decision/instruction, contractual basis, expectations, and consequences."
    ),
    "engineer": (
        "Neutral, procedurally precise, analytically structured. Sequence background, "
        "submissions received, contractual analysis, reasoning, and determination."
    ),
}


class StrategyPlanner:
    def __init__(self, prompt_registry: PromptRegistry, model_name: str = "gpt-4o-mini") -> None:
        self.prompt_registry = prompt_registry
        self.model_name = model_name
        self.generator = LLMGenerator(DocumentProcessingConfig(openai_model=model_name))

    async def generate(
        self,
        letter: Letter,
        role: str,
        recipient_focus: Optional[str],
        context: DraftContextBundle,
        sources: List[SourceEvidence],
        request_payload: Dict[str, Any],
    ) -> tuple[str, int, List[str]]:
        warnings: List[str] = []
        prompt_record = await self.prompt_registry.get_enabled(STRATEGY_PROMPT_KEY)
        payload = build_prompt_payload(
            letter, role, recipient_focus, context, sources, request_payload, plan=""
        )
        try:
            prompt = render_prompt(prompt_record, payload)
            plan = await self.generator.generate(prompt, max_tokens=1800, model=self.model_name)
        except Exception as exc:
            warnings.append(f"strategy_llm: {exc}")
            plan = fallback_strategy_plan(role, context, sources)
        return plan or fallback_strategy_plan(role, context, sources), prompt_record.version, warnings


class DraftGenerator:
    def __init__(self, prompt_registry: PromptRegistry, model_name: str = "gpt-4o") -> None:
        self.prompt_registry = prompt_registry
        self.model_name = model_name
        self.generator = LLMGenerator(DocumentProcessingConfig(openai_model=model_name))

    async def generate(
        self,
        letter: Letter,
        role: str,
        recipient_focus: Optional[str],
        context: DraftContextBundle,
        sources: List[SourceEvidence],
        request_payload: Dict[str, Any],
        plan: str,
        finalized: bool = False,
    ) -> tuple[DraftArtifact, List[str]]:
        warnings: List[str] = []
        prompt_record = await self.prompt_registry.get_enabled(DRAFT_PROMPT_KEY)
        payload = build_prompt_payload(
            letter, role, recipient_focus, context, sources, request_payload, plan=plan
        )
        payload["finalized"] = "true" if finalized else "false"
        try:
            prompt = render_prompt(prompt_record, payload)
            raw = await self.generator.generate(prompt, max_tokens=1400, model=self.model_name)
        except Exception as exc:
            warnings.append(f"draft_llm: {exc}")
            raw = fallback_draft(role, letter, context, sources, finalized)
        artifact = parse_draft_artifact(
            raw or fallback_draft(role, letter, context, sources, finalized),
            model_name=self.model_name,
            prompt_version=prompt_record.version,
            finalized=finalized,
        )
        return artifact, warnings


def build_prompt_payload(
    letter: Letter,
    role: str,
    recipient_focus: Optional[str],
    context: DraftContextBundle,
    sources: List[SourceEvidence],
    request_payload: Dict[str, Any],
    plan: str,
) -> Dict[str, str]:
    return {
        "role": role,
        "recipient": str(request_payload.get("recipient") or letter.recipient or "the counterparty"),
        "subject": str(request_payload.get("subject") or letter.subject or "Contract correspondence"),
        "recipient_focus": recipient_focus or "N/A",
        "active_workspace": "; ".join(
            f"{key}={value or 'not provided'}"
            for key, value in context.active_workspace.items()
        ),
        "current_materials": "\n".join(f"- {item}" for item in context.current_materials) or "None provided.",
        "sources": format_sources_for_prompt(sources),
        "profile_pattern": PROFILE_PATTERNS.get(role, PROFILE_PATTERNS["contractor"]),
        "plan": plan or "No approved strategy plan supplied.",
    }


def format_sources_for_prompt(sources: List[SourceEvidence]) -> str:
    if not sources:
        return "No sources available."
    lines: List[str] = []
    for idx, source in enumerate(sources, start=1):
        label = f"[S{idx}] {source.source_type}/{source.allowed_use}: {source.label}"
        if source.clause_number:
            label += f" (Clause {source.clause_number})"
        text = source.text or source.snippet
        if text:
            label += f": {condense_text(text, 360)}"
        lines.append(label)
    return "\n".join(lines[:30])


def parse_draft_artifact(
    raw: str,
    model_name: str,
    prompt_version: int,
    finalized: bool,
) -> DraftArtifact:
    sections = split_sections(raw or "")
    draft_letter = sections.get("draft letter") or raw or ""
    notes = sections.get("source integrity notes") or "No Source Integrity Notes section was returned."
    learning = sections.get("learning update")
    if not finalized:
        learning = None
    elif learning and learning.strip().upper() == "N/A":
        learning = None
    return DraftArtifact(
        draft_letter=draft_letter.strip(),
        source_integrity_notes=notes.strip(),
        learning_update=learning.strip() if learning else None,
        raw_model_output=raw or "",
        model_name=model_name,
        prompt_version=prompt_version,
    )


def split_sections(raw: str) -> Dict[str, str]:
    headings = {"draft letter", "source integrity notes", "learning update"}
    current: Optional[str] = None
    output: Dict[str, List[str]] = {}
    for line in (raw or "").splitlines():
        normalized = line.strip().strip("#:").lower()
        if normalized in headings:
            current = normalized
            output.setdefault(current, [])
            continue
        if current:
            output[current].append(line)
    return {key: "\n".join(value).strip() for key, value in output.items()}


def fallback_strategy_plan(
    role: str,
    context: DraftContextBundle,
    sources: List[SourceEvidence],
) -> str:
    clause_lines = [
        f"- Clause {source.clause_number}: {source.label}"
        for source in sources
        if source.source_type == "contract_clause" and source.clause_number
    ]
    return "\n".join(
        [
            "1. Incoming letter summary",
            "\n".join(f"- {item}" for item in context.current_materials[:3]) or "- Not verified from available sources.",
            "2. Sender and subject verification",
            "- Verify sender entity, sender role, exact subject, and subject match before drafting.",
            "3. Letter reference number and date",
            "- Not verified from available sources.",
            "4. Main issue classification",
            "- Classify as claim, delay, variation, payment, approval, dispute, notice, contractual compliance issue, request for information, or other.",
            "5. Requested action",
            "- Confirm the sender's requested action from the incoming letter.",
            "6. Stated deadline",
            "- Not verified from available sources.",
            "7. Contractual response deadline",
            "- Verify applicable contract response period before issue.",
            "8. Cited clauses",
            "\n".join(clause_lines) or "- No cited clauses verified from available sources.",
            "9. Clause correctness check",
            "- Not verified from available sources.",
            "10. Clause applicability analysis",
            "- Not verified from available sources.",
            "11. Counter-position or counter-clauses",
            "- Identify counter-clauses or related provisions before final drafting.",
            "12. Missing information",
            "- Confirm any missing dates, references, evidence, amounts, and clause wording.",
            "13. Recommended response strategy",
            PROFILE_PATTERNS.get(role, PROFILE_PATTERNS["contractor"]),
            "14. Points the drafter must verify manually",
            "- Sender/reference/date, response deadline, cited clause wording, factual support, and commercial/legal review need.",
            "15. Suggested structure for the reply letter",
            "- Reference and subject; incoming matter summary; factual position; contractual analysis; response to each point; requested outcome; reservations.",
        ]
    )


def fallback_draft(
    role: str,
    letter: Letter,
    context: DraftContextBundle,
    sources: List[SourceEvidence],
    finalized: bool,
) -> str:
    subject = letter.subject or "Contract correspondence"
    fact_lines = "\n".join(f"{idx}. {item}" for idx, item in enumerate(context.current_materials[:5], start=1))
    if not fact_lines:
        fact_lines = "[TO BE INSERTED BY USER: supporting evidence or impact quantification]"
    source_notes = "\n".join(
        f"- {source.label}: {source.allowed_use}" for source in sources[:10]
    ) or "- No prior correspondence was available in the workspace."
    learning = "N/A"
    if finalized:
        learning = (
            f"Profile Type: {role.title()}\n"
            "Structural Pattern Learned: Generalized role-specific contractual correspondence sequence.\n"
            "Tone / Framing Pattern Learned: Formal, source-grounded, commercially measured framing.\n"
            "Reasoning Pattern Learned: Facts before contractual position and requested action.\n"
            "Reusable Language Pattern: Fully anonymized reservation and confirmation phrasing.\n"
            "Confirmation: No contract-specific data (names, dates, amounts, references, facts) has been retained or included."
        )
    return f"""Draft Letter
Subject: {subject}

Dear Sir/Madam,

{fact_lines}

This draft is prepared from the available source material and remains subject to user confirmation of any missing particulars.

Yours faithfully,
[TO BE INSERTED BY USER: sender name and designation]

Source Integrity Notes
{source_notes}

Learning Update
{learning}
"""
