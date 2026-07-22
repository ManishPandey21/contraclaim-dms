from __future__ import annotations

from typing import Any, Dict, List, Optional


PROMPT_VERSION = "arbitration_pleadings.v2"
SOURCE_POLICY = "source-ledger-only-with-evidence-required-fallback"

_CLAIM_SECTION_KEYS = {
    "caption",
    "index",
    "introduction",
    "parties",
    "jurisdiction",
    "factual_background",
    "legal_claims",
    "quantum",
    "interest",
    "costs",
    "relief",
    "verification",
    "annexures",
}

SECTION_KEYS_BY_DRAFT_TYPE: Dict[str, set[str]] = {
    "statement_of_claim": set(_CLAIM_SECTION_KEYS),
    "statement_of_defence": {
        "caption",
        "overview",
        "preliminary_objections",
        "paragraph_response",
        "respondent_facts",
        "legal_defences",
        "quantum_challenge",
        "interest_costs_reply",
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
        "interest_costs_reply",
        "counterclaim_reply",
        "reaffirmed_relief",
        "annexures",
    },
    "counterclaim": set(_CLAIM_SECTION_KEYS),
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
    matrix = context.get("matrix_context") or {}
    rows = [
        *(matrix.get("chronology") or []),
        *(matrix.get("documents") or []),
        *(matrix.get("issues") or []),
        *(context.get("source_ledger") or []),
    ]
    facts = []
    for row in rows[:8]:
        snippet = row.get("snippet")
        if snippet:
            facts.append(f"{snippet} {_source_label(row)}")
    if context.get("draft", {}).get("manual_facts"):
        facts.insert(0, f"{context['draft']['manual_facts']} [User-provided fact; verify before filing]")
    return facts or ["[Evidence required]"]


def _matrix_rows(context: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    return list((context.get("matrix_context") or {}).get(key) or [])


def _matrix_counts(context: Dict[str, Any]) -> Dict[str, int]:
    return {key: len(value or []) for key, value in (context.get("matrix_context") or {}).items()}


def _row_excerpt(row: Dict[str, Any]) -> str:
    parts = [
        str(row.get("label") or "").strip(),
        str(row.get("snippet") or "").strip(),
    ]
    text = " - ".join(part for part in parts if part)
    return text or str(row.get("citation") or row.get("source_id") or "Matrix source")


def _numbered_sources(rows: List[Dict[str, Any]], fallback: str = "[Evidence required]") -> str:
    if not rows:
        return fallback
    return "\n".join(f"{idx}. {_row_excerpt(row)} {_source_label(row)}" for idx, row in enumerate(rows, start=1))


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
        pleading_plan = context.get("pleading_plan") or {}
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
                "matrix_context_counts": _matrix_counts(context),
                "missing_evidence_count": len(context.get("missing_evidence") or []),
                "section_key": section_key,
                "generation_instruction_included": bool(additional_instruction),
                "pleading_plan_id": pleading_plan.get("_id"),
                "pleading_plan_hash": pleading_plan.get("plan_hash"),
                "planned_section_keys": [
                    item.get("key")
                    for item in pleading_plan.get("section_structure") or []
                    if item.get("key")
                ],
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
        sections: List[Dict[str, str]] = [
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
                "body": self._jurisdiction_text(context),
            },
            {"key": "factual_background", "heading": "Factual Background", "body": self._numbered(facts)},
            {
                "key": "legal_claims",
                "heading": "Legal Claims / Causes of Action",
                "body": self._claim_matrix_text(context, claim_heads, evidence),
            },
            {
                "key": "quantum",
                "heading": "Damages, Causation, Mitigation and Quantum",
                "body": self._quantum(context, draft, claim_heads, evidence),
            },
            {"key": "interest", "heading": "Interest", "body": self._interest_text(context, draft)},
            {"key": "costs", "heading": "Costs", "body": self._costs_text()},
            {"key": "relief", "heading": "Prayer for Relief", "body": draft.get("relief_sought") or "[Evidence required]"},
            {
                "key": "verification",
                "heading": "Verification / Statement of Truth",
                "body": self._verification_text(pleading),
            },
            {"key": "annexures", "heading": "List of Relied-Upon Documents / Annexures", "body": self._annexure_text(evidence)},
        ]
        headings = [section["heading"] for section in sections]
        headings.insert(1, "Index")
        sections.insert(1, {"key": "index", "heading": "Index", "body": self._index_text(context, headings)})
        return sections

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
            {
                "key": "preliminary_objections",
                "heading": "Preliminary Objections",
                "body": self._preliminary_objections_text(
                    context,
                    "The Respondent raises the following preliminary objections based on the jurisdiction, "
                    "limitation, and pre-arbitration record:",
                ),
            },
            {
                "key": "paragraph_response",
                "heading": "Paragraph-by-Paragraph Response to SoC",
                "body": self._paragraph_responses(responses, evidence, default="Statement of Claim paragraphs must be imported.")
                if responses
                else self._defence_matrix_text(context, evidence),
            },
            {"key": "respondent_facts", "heading": "Respondent's Factual Background", "body": self._numbered(_facts(context))},
            {"key": "legal_defences", "heading": "Legal Defences on Merits", "body": self._defence_matrix_text(context, evidence)},
            {"key": "quantum_challenge", "heading": "Quantum Challenge", "body": self._quantum_challenge(context, draft, evidence)},
            {
                "key": "interest_costs_reply",
                "heading": "Reply to Interest and Costs",
                "body": self._interest_costs_reply(context),
            },
            {"key": "counterclaim", "heading": "Counterclaim, if applicable", "body": self._counterclaim_matrix_text(context)},
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
            {
                "key": "preliminary_objections",
                "heading": "Response to Preliminary Objections",
                "body": self._preliminary_objections_text(
                    context,
                    "The Claimant replies to the Respondent's preliminary objections by reference to the "
                    "jurisdiction, limitation, and pre-arbitration record:",
                ),
            },
            {
                "key": "paragraph_replies",
                "heading": "Paragraph-by-Paragraph Reply to the Statement of Defence",
                "body": self._paragraph_responses(responses, evidence, default="Statement of Defence paragraphs must be imported.")
                if responses
                else self._rejoinder_matrix_text(context),
            },
            {"key": "clarified_facts", "heading": "Claimant's Clarified Factual Position", "body": self._numbered(_facts(context))},
            {"key": "legal_defences_reply", "heading": "Reply to Legal Defences", "body": self._rejoinder_matrix_text(context)},
            {"key": "quantum_reply", "heading": "Reply to Quantum Objections", "body": self._quantum_challenge(context, draft, evidence)},
            {
                "key": "interest_costs_reply",
                "heading": "Reply to Interest and Costs",
                "body": self._interest_costs_reply(context),
            },
            {"key": "counterclaim_reply", "heading": "Reply to Counterclaim, if any", "body": self._counterclaim_matrix_text(context)},
            {"key": "reaffirmed_relief", "heading": "Reaffirmation of Reliefs", "body": draft.get("relief_sought") or "[Evidence required]"},
            {"key": "annexures", "heading": "Updated List of Documents / Annexures", "body": self._annexure_text(evidence)},
        ]

    def _index_text(self, context: Dict[str, Any], headings: List[str]) -> str:
        lines = ["Pleading index:"]
        lines.extend(f"{idx}. {heading}" for idx, heading in enumerate(headings, start=1))
        lines.append("")
        lines.append("Document index (exhibits):")
        document_rows = _matrix_rows(context, "documents")
        if document_rows:
            for row in document_rows:
                exhibit = (row.get("metadata") or {}).get("exhibit_id") or row.get("citation")
                lines.append(f"- {exhibit}: {row.get('label')} {_source_label(row)}")
        else:
            lines.append("- [Evidence required]")
        return "\n".join(lines)

    def _interest_rows(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for row in _matrix_rows(context, "quantum"):
            metadata = row.get("metadata") or {}
            text = f"{row.get('label') or ''} {row.get('citation') or ''} {metadata.get('calculation_type') or ''}".lower()
            if "interest" in text:
                rows.append(row)
        return rows

    def _interest_text(self, context: Dict[str, Any], draft: Dict[str, Any]) -> str:
        interest_rows = self._interest_rows(context)
        rate = draft.get("interest_rate")
        lines: List[str] = []
        if rate is not None:
            lines.append(
                f"The Claimant claims pre-reference, pendente lite, and future interest at {rate} percent per annum."
            )
        if interest_rows:
            lines.append("Interest is computed in the following calculation annexures:")
            lines.append(_numbered_sources(interest_rows))
        elif rate is not None:
            lines.append("Interest calculation annexure: [Evidence required]")
        if not lines:
            return "[Evidence required]"
        return "\n".join(lines)

    def _costs_text(self) -> str:
        return (
            "The Claimant claims the costs of the arbitration, including tribunal and institutional fees, "
            "counsel fees, expert fees, and documentation and hearing costs. "
            "Quantification of costs: [Evidence required]"
        )

    def _verification_text(self, pleading: str) -> str:
        return (
            f"Verified by the authorised representative that the contents of this {pleading} are true and "
            "correct to their knowledge and the party's records, and that no material fact has been concealed. "
            "Place: [Evidence required]. Date: [Evidence required]. "
            "Signatory authority (board resolution / power of attorney): [Evidence required]"
        )

    def _preliminary_objections_text(self, context: Dict[str, Any], lead_in: str) -> str:
        rows = _matrix_rows(context, "jurisdiction")
        if not rows:
            return "[Evidence required]"
        return f"{lead_in}\n{_numbered_sources(rows)}"

    def _interest_costs_reply(self, context: Dict[str, Any]) -> str:
        lines = [
            "The claim to interest is denied; entitlement, the applicable rate, the period, and the computation "
            "must each be established. The claim to costs is denied; costs are in the discretion of the Tribunal "
            "and any claimed cost heads must be proved.",
        ]
        interest_rows = self._interest_rows(context)
        if interest_rows:
            lines.append("Interest computation records under challenge:")
            lines.append(_numbered_sources(interest_rows))
        else:
            lines.append("Interest and cost computation records: [Evidence required]")
        return "\n".join(lines)

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

    def _jurisdiction_text(self, context: Dict[str, Any]) -> str:
        draft = context["draft"]
        clause_rows = _matrix_rows(context, "clauses")
        if draft.get("arbitration_clause"):
            cited_clause = _evidence_note([row for row in clause_rows if "arbitration" in _row_excerpt(row).lower()], "")
            suffix = f" {cited_clause}" if cited_clause else ""
            return f"{draft.get('arbitration_clause')}{suffix}"
        arbitration_rows = [row for row in clause_rows if "arbitration" in _row_excerpt(row).lower() or "dispute" in _row_excerpt(row).lower()]
        if arbitration_rows:
            return _numbered_sources(arbitration_rows)
        return "[Evidence required]"

    def _claim_matrix_text(
        self,
        context: Dict[str, Any],
        claim_heads: List[Dict[str, Any]],
        evidence: List[Dict[str, Any]],
    ) -> str:
        claim_rows = _matrix_rows(context, "claims")
        if not claim_rows:
            return self._claim_heads(claim_heads, evidence)
        lines: List[str] = []
        for idx, row in enumerate(claim_rows, start=1):
            metadata = row.get("metadata") or {}
            support = _source_label(row)
            parts = [f"{idx}. {row.get('label') or 'Claim'}"]
            if row.get("snippet"):
                parts.append(str(row.get("snippet")))
            if row.get("clause_number"):
                parts.append(f"Clause support: {row.get('clause_number')}")
            if metadata.get("amount_or_days"):
                parts.append(f"Amount/days: {metadata.get('amount_or_days')}")
            parts.append(support)
            lines.append(" - ".join(parts))
        return "\n".join(lines)

    def _defence_matrix_text(self, context: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
        defence_rows = _matrix_rows(context, "defences")
        if defence_rows:
            return _numbered_sources(defence_rows)
        return self._defence_text(evidence)

    def _counterclaim_matrix_text(self, context: Dict[str, Any]) -> str:
        counterclaim_rows = _matrix_rows(context, "counterclaims")
        return _numbered_sources(counterclaim_rows)

    def _rejoinder_matrix_text(self, context: Dict[str, Any]) -> str:
        rejoinder_rows = _matrix_rows(context, "rejoinder_replies")
        if not rejoinder_rows:
            return "[Evidence required] Statement of Defence paragraphs must be imported or rejoinder matrix rows must be approved."
        lines: List[str] = []
        for idx, row in enumerate(rejoinder_rows, start=1):
            metadata = row.get("metadata") or {}
            flags = []
            if metadata.get("new_matter"):
                flags.append("new matter/legal review")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"{idx}. {_row_excerpt(row)} {_source_label(row)}{suffix}")
        return "\n".join(lines)

    def _claim_heads(self, claim_heads: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> str:
        if not claim_heads:
            return "[Evidence required]"
        lines = []
        for idx, head in enumerate(claim_heads, start=1):
            support = _evidence_note([row for row in evidence if row.get("source_id") in set(head.get("supporting_source_ids") or [])])
            lines.append(f"{idx}. {head.get('description')} - {support}")
        return "\n".join(lines)

    def _quantum(self, context: Dict[str, Any], draft: Dict[str, Any], claim_heads: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> str:
        quantum_rows = _matrix_rows(context, "quantum")
        if quantum_rows:
            lines = ["Approved quantum/calculation annexures:"]
            lines.extend(f"- {_row_excerpt(row)} {_source_label(row)}" for row in quantum_rows)
            claim_rows = _matrix_rows(context, "claims")
            if claim_rows:
                lines.append("Linked claim matrix rows:")
                lines.extend(f"- {_row_excerpt(row)} {_source_label(row)}" for row in claim_rows)
            return "\n".join(lines)
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

    def _quantum_challenge(self, context: Dict[str, Any], draft: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
        quantum_rows = _matrix_rows(context, "quantum")
        if quantum_rows:
            return "\n".join(
                [
                    "Address causation, remoteness, mitigation, duplication, rates, and source support against these approved quantum records:",
                    _numbered_sources(quantum_rows),
                ]
            )
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
