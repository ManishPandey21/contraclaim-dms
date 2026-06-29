from __future__ import annotations

from typing import Any, Dict, List, Optional


PROMPT_VERSION = "arbitration_pleadings.v2"
SOURCE_POLICY = "source-ledger-only-with-evidence-required-fallback"

SECTION_KEYS_BY_DRAFT_TYPE: Dict[str, set[str]] = {
    "statement_of_claim": {
        "caption",
        "introduction",
        "parties",
        "jurisdiction",
        "factual_background",
        "legal_claims",
        "quantum",
        "relief",
        "annexures",
    },
    "statement_of_defence": {
        "caption",
        "overview",
        "preliminary_objections",
        "paragraph_response",
        "respondent_facts",
        "legal_defences",
        "quantum_challenge",
        "counterclaim",
        "relief",
        "annexures",
    },
    "rejoinder": {
        "caption",
        "scope",
        "preliminary_objections",
        "paragraph_replies",
        "clarified_facts",
        "legal_defences_reply",
        "quantum_reply",
        "counterclaim_reply",
        "reaffirmed_relief",
        "annexures",
    },
    "counterclaim": {
        "caption",
        "introduction",
        "parties",
        "jurisdiction",
        "factual_background",
        "legal_claims",
        "quantum",
        "relief",
        "annexures",
    },
}


def _source_label(row: Dict[str, Any]) -> str:
    key = row.get("source_key") or "S?"
    citation = row.get("citation") or row.get("label") or row.get("source_id")
    return f"[{key}: {citation}]"


def _evidence_note(rows: List[Dict[str, Any]], fallback: str = "[Evidence required]") -> str:
    if not rows:
        return fallback
    labels = ", ".join(_source_label(row) for row in rows[:4])
    return labels


def _facts(context: Dict[str, Any]) -> List[str]:
    rows = context.get("source_ledger") or []
    facts = []
    for row in rows[:8]:
        snippet = row.get("snippet")
        if snippet:
            facts.append(f"{snippet} {_source_label(row)}")
    if context.get("draft", {}).get("manual_facts"):
        facts.insert(0, f"{context['draft']['manual_facts']} [User-provided fact; verify before filing]")
    return facts or ["[Evidence required]"]


class ArbitrationDraftGenerator:
    """Deterministic source-grounded pleading generator.

    This gives the workflow a safe production baseline. Live LLM generation can
    replace section bodies later, but should keep the same source-ledger contract
    and validator rules.
    """

    def generate(
        self,
        context: Dict[str, Any],
        *,
        section_key: Optional[str] = None,
        additional_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        draft = context["draft"]
        draft_type = draft.get("draft_type")
        if draft_type == "statement_of_defence":
            sections = self._statement_of_defence(context)
        elif draft_type == "rejoinder":
            sections = self._rejoinder(context)
        elif draft_type == "counterclaim":
            sections = self._statement_of_claim(context, counterclaim=True)
        else:
            sections = self._statement_of_claim(context)
        if section_key:
            sections = [section for section in sections if section["key"] == section_key]
            if not sections:
                sections = [{"key": section_key, "heading": section_key.replace("_", " ").title(), "body": "[Evidence required]"}]
        markdown = self._markdown(draft, sections, context, additional_instruction=additional_instruction)
        source_ledger = context.get("source_ledger") or []
        return {
            "sections": sections,
            "full_markdown": markdown,
            "structured_output": {
                "draft_type": draft_type,
                "section_keys": [section["key"] for section in sections],
                "prompt_version": PROMPT_VERSION,
                "source_policy": SOURCE_POLICY,
                "source_count": len(source_ledger),
                "source_hashes": [row.get("source_hash") for row in source_ledger if row.get("source_hash")],
                "missing_evidence_count": len(context.get("missing_evidence") or []),
                "section_key": section_key,
                "generation_instruction_included": bool(additional_instruction),
            },
            "missing_evidence": context.get("missing_evidence") or [],
            "annexures": self._annexures(context.get("source_ledger") or []),
            "ai_prompt_version": PROMPT_VERSION,
            "model": "deterministic-source-grounded",
        }

    def _statement_of_claim(self, context: Dict[str, Any], *, counterclaim: bool = False) -> List[Dict[str, str]]:
        draft = context["draft"]
        evidence = context.get("source_ledger") or []
        facts = _facts(context)
        claim_heads = context.get("claim_heads") or []
        pleading = "Counterclaim" if counterclaim else "Statement of Claim"
        return [
            {"key": "caption", "heading": "Caption / Cover Page", "body": self._caption(draft, pleading)},
            {
                "key": "introduction",
                "heading": "Introduction and Executive Summary",
                "body": f"This {pleading} is prepared for {draft.get('title')}. The pleading relies on the selected project record and contract materials. {_evidence_note(evidence)}",
            },
            {"key": "parties", "heading": "Parties", "body": self._parties(draft)},
            {
                "key": "jurisdiction",
                "heading": "Jurisdiction and Arbitration Agreement",
                "body": draft.get("arbitration_clause") or "[Evidence required]",
            },
            {"key": "factual_background", "heading": "Factual Background", "body": self._numbered(facts)},
            {
                "key": "legal_claims",
                "heading": "Legal Claims / Causes of Action",
                "body": self._claim_heads(claim_heads, evidence),
            },
            {
                "key": "quantum",
                "heading": "Damages, Causation, Mitigation and Quantum",
                "body": self._quantum(draft, claim_heads, evidence),
            },
            {"key": "relief", "heading": "Prayer for Relief", "body": draft.get("relief_sought") or "[Evidence required]"},
            {"key": "annexures", "heading": "List of Relied-Upon Documents / Annexures", "body": self._annexure_text(evidence)},
        ]

    def _statement_of_defence(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        draft = context["draft"]
        evidence = context.get("source_ledger") or []
        responses = context.get("paragraph_responses") or []
        return [
            {"key": "caption", "heading": "Caption / Cover Page", "body": self._caption(draft, "Statement of Defence")},
            {
                "key": "overview",
                "heading": "Introduction and Overview",
                "body": f"The Respondent responds to the Statement of Claim on the basis of the available record. {_evidence_note(evidence)}",
            },
            {"key": "preliminary_objections", "heading": "Preliminary Objections", "body": "[Evidence required]"},
            {
                "key": "paragraph_response",
                "heading": "Paragraph-by-Paragraph Response to SoC",
                "body": self._paragraph_responses(responses, evidence, default="Statement of Claim paragraphs must be imported."),
            },
            {"key": "respondent_facts", "heading": "Respondent's Factual Background", "body": self._numbered(_facts(context))},
            {"key": "legal_defences", "heading": "Legal Defences on Merits", "body": self._defence_text(evidence)},
            {"key": "quantum_challenge", "heading": "Quantum Challenge", "body": self._quantum_challenge(draft, evidence)},
            {"key": "counterclaim", "heading": "Counterclaim, if applicable", "body": "[Evidence required]"},
            {"key": "relief", "heading": "Prayer for Relief", "body": draft.get("relief_sought") or "[Evidence required]"},
            {"key": "annexures", "heading": "List of Relied-Upon Documents / Annexures", "body": self._annexure_text(evidence)},
        ]

    def _rejoinder(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        draft = context["draft"]
        evidence = context.get("source_ledger") or []
        responses = context.get("paragraph_responses") or []
        return [
            {"key": "caption", "heading": "Caption / Cover Page", "body": self._caption(draft, "Rejoinder / Reply to Statement of Defence")},
            {
                "key": "scope",
                "heading": "Introduction and Scope of Rejoinder",
                "body": (
                    "This Rejoinder is filed in response to the Statement of Defence. The Claimant denies the Respondent's "
                    "defences except where expressly admitted, and maintains the claims, reliefs, and legal position stated "
                    f"in the Statement of Claim. {_evidence_note(evidence)}"
                ),
            },
            {"key": "preliminary_objections", "heading": "Response to Preliminary Objections", "body": "[Evidence required]"},
            {
                "key": "paragraph_replies",
                "heading": "Paragraph-by-Paragraph Reply to the Statement of Defence",
                "body": self._paragraph_responses(responses, evidence, default="Statement of Defence paragraphs must be imported."),
            },
            {"key": "clarified_facts", "heading": "Claimant's Clarified Factual Position", "body": self._numbered(_facts(context))},
            {"key": "legal_defences_reply", "heading": "Reply to Legal Defences", "body": self._defence_text(evidence)},
            {"key": "quantum_reply", "heading": "Reply to Quantum Objections", "body": self._quantum_challenge(draft, evidence)},
            {"key": "counterclaim_reply", "heading": "Reply to Counterclaim, if any", "body": "[Evidence required]"},
            {"key": "reaffirmed_relief", "heading": "Reaffirmation of Reliefs", "body": draft.get("relief_sought") or "[Evidence required]"},
            {"key": "annexures", "heading": "Updated List of Documents / Annexures", "body": self._annexure_text(evidence)},
        ]

    def _caption(self, draft: Dict[str, Any], pleading: str) -> str:
        case = draft.get("case_details") or {}
        lines = [
            pleading,
            f"Case reference: {case.get('case_reference') or '[Evidence required]'}",
            f"Tribunal / Institution: {draft.get('tribunal_details') or case.get('tribunal') or '[Evidence required]'}",
            f"Parties: {case.get('parties') or '[Evidence required]'}",
            f"Project: {draft.get('project_id')}",
        ]
        return "\n".join(lines)

    def _parties(self, draft: Dict[str, Any]) -> str:
        parties = (draft.get("case_details") or {}).get("parties")
        return str(parties) if parties else "[Evidence required]"

    def _claim_heads(self, claim_heads: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> str:
        if not claim_heads:
            return "[Evidence required]"
        lines = []
        for idx, head in enumerate(claim_heads, start=1):
            support = _evidence_note([row for row in evidence if row.get("source_id") in set(head.get("supporting_source_ids") or [])])
            lines.append(f"{idx}. {head.get('description')} - {support}")
        return "\n".join(lines)

    def _quantum(self, draft: Dict[str, Any], claim_heads: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> str:
        amount = draft.get("claim_amount")
        if amount is None and not claim_heads:
            return "[Evidence required]"
        rows = [f"Claim amount: {draft.get('currency') or ''} {amount}" if amount is not None else "Claim amount: [Evidence required]"]
        for head in claim_heads:
            rows.append(f"- {head.get('description')}: {head.get('currency') or draft.get('currency') or ''} {head.get('amount') or '[Evidence required]'}")
        rows.append(f"Supporting quantum evidence: {_evidence_note([row for row in evidence if row.get('allowed_use') == 'quantum'])}")
        return "\n".join(rows)

    def _defence_text(self, evidence: List[Dict[str, Any]]) -> str:
        return (
            "The response must address no breach, force majeure, hardship, prior breach, waiver, estoppel, "
            f"set-off, failure to mitigate, contractual bar, and notice bar where raised. {_evidence_note(evidence)}"
        )

    def _quantum_challenge(self, draft: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
        return (
            "Address causation, remoteness, mitigation, duplication, speculative elements, rates, and cost support. "
            f"Payment/quantum support: {_evidence_note([row for row in evidence if row.get('allowed_use') == 'quantum'])}"
        )

    def _paragraph_responses(self, responses: List[Dict[str, Any]], evidence: List[Dict[str, Any]], *, default: str) -> str:
        if not responses:
            return f"[Evidence required] {default}"
        lines = []
        for response in responses:
            status = str(response.get("response_type") or "require_proof").replace("_", " ").title()
            text = response.get("response_text") or response.get("response_reason") or "[Evidence required]"
            support_ids = {str(item) for item in response.get("supporting_source_ids") or []}
            support = _evidence_note([row for row in evidence if str(row.get("source_id")) in support_ids], "")
            if not support and response.get("response_type") in {
                "deny",
                "part_admit_part_deny",
                "not_admitted",
                "misconceived",
                "incorrect",
                "misleading",
            }:
                support = "[Evidence required]"
            suffix = f" {support}" if support else ""
            lines.append(f"{response.get('source_paragraph_number')}. {status}: {text}{suffix}")
        return "\n".join(lines)

    def _numbered(self, rows: List[str]) -> str:
        return "\n".join(f"{idx}. {row}" for idx, row in enumerate(rows, start=1))

    def _annexures(self, evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {"annexure_no": f"A-{idx}", "source_id": row.get("source_id"), "label": row.get("label"), "citation": row.get("citation")}
            for idx, row in enumerate(evidence, start=1)
        ]

    def _annexure_text(self, evidence: List[Dict[str, Any]]) -> str:
        if not evidence:
            return "[Evidence required]"
        return "\n".join(
            f"A-{idx}. {row.get('label')} ({row.get('citation') or row.get('source_id')})"
            for idx, row in enumerate(evidence, start=1)
        )

    def _markdown(
        self,
        draft: Dict[str, Any],
        sections: List[Dict[str, str]],
        context: Dict[str, Any],
        *,
        additional_instruction: Optional[str],
    ) -> str:
        lines = [f"# {draft.get('title')}", ""]
        for section in sections:
            lines.extend([f"## {section['heading']}", "", section["body"], ""])
        missing = context.get("missing_evidence") or []
        if missing:
            lines.extend(["## Missing Evidence Alerts", ""])
            lines.extend(f"- {item}" for item in missing)
            lines.append("")
        if additional_instruction:
            lines.extend(["## User Generation Instruction", "", additional_instruction, ""])
        return "\n".join(lines).strip() + "\n"
