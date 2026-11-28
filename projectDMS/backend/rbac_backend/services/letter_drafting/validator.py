from __future__ import annotations

import re
from typing import Any, List

from ...models.letter_drafting import (
    DraftArtifact,
    DraftContextBundle,
    SourceEvidence,
    ValidationFinding,
    ValidationReport,
)


class DraftValidator:
    """Deterministic v2 draft validator."""

    CLAUSE_PATTERN = re.compile(
        r"\b(?:clause|section|sub-clause|subclause|article)\s+([0-9A-Za-z.\-()]+)",
        re.IGNORECASE,
    )
    ROMAN_NUMERAL = re.compile(r"(?=[mdclxvi])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
    PLACEHOLDER_PATTERN = re.compile(
        r"\[(?:CONFIRM:|TO BE INSERTED BY USER:|POSITION CONFLICT:)[^\]]+\]",
        re.IGNORECASE,
    )
    RED_FLAG_PATTERNS = {
        "possible_waiver": re.compile(r"\b(?:waive|waiver|forego|forgo)\b", re.IGNORECASE),
        "possible_admission": re.compile(
            r"\b(?:we admit|we acknowledge responsibility|our delay|our default)\b",
            re.IGNORECASE,
        ),
        "unsupported_allegation": re.compile(
            r"\b(?:misusing|fraudulent|willful breach|bad faith)\b",
            re.IGNORECASE,
        ),
        "payment_implication": re.compile(
            r"\b(?:payment|deduction|recovery|withhold|withholding|invoice|ipc)\b",
            re.IGNORECASE,
        ),
        "time_implication": re.compile(
            r"\b(?:time[- ]bar|extension of time|eot|delay damages|liquidated damages)\b",
            re.IGNORECASE,
        ),
    }

    def threshold_findings(self, context: DraftContextBundle) -> ValidationReport:
        findings = [
            ValidationFinding(
                level="error",
                code=f"threshold_missing_{key}",
                message=f"Drafting threshold missing: {key.replace('_', ' ')}.",
            )
            for key, present in context.threshold_inputs.items()
            if not present
        ]
        return ValidationReport(blocking=bool(findings), findings=findings)

    def validate(
        self,
        artifact: DraftArtifact,
        role: str,
        sources: List[SourceEvidence],
        finalized: bool = False,
    ) -> ValidationReport:
        findings: List[ValidationFinding] = []
        if not artifact.draft_letter.strip():
            findings.append(
                ValidationFinding(
                    level="error",
                    code="missing_draft_letter",
                    message="Draft Letter section is empty.",
                )
            )
        if not artifact.source_integrity_notes.strip():
            findings.append(
                ValidationFinding(
                    level="error",
                    code="missing_source_integrity_notes",
                    message="Source Integrity Notes section is empty.",
                )
            )
        if not finalized and artifact.learning_update:
            findings.append(
                ValidationFinding(
                    level="error",
                    code="unexpected_learning_update",
                    message="Learning Update must be omitted unless the user explicitly finalizes or approves the letter.",
                )
            )
        if not sources:
            findings.append(
                ValidationFinding(
                    level="error",
                    code="no_sources",
                    message="No source ledger was available for this run.",
                )
            )

        # The pattern's character class includes "." (for "20.2"), so a clause
        # cited at the end of a sentence arrives as "20.2."; trailing sentence
        # punctuation is not part of a clause number. Same normalisation as
        # incoming_analyzer.
        allowed_clauses = {
            (source.clause_number or "").lower().strip(" .;,")
            for source in sources
            if source.source_type == "contract_clause" and source.clause_number
        }
        # A clause number carries a digit ("20.2", "4A", "8.4(a)") or is a roman
        # numeral ("XIII"); anything else after "clause" is prose ("the clause
        # that ...", "no clause exists ...") and is not a citation.
        mentioned = {
            token
            for token in (
                match.group(1).lower().strip(" .;,")
                for match in self.CLAUSE_PATTERN.finditer(artifact.draft_letter)
            )
            if token and (any(ch.isdigit() for ch in token) or self.ROMAN_NUMERAL.fullmatch(token))
        }
        missing_clauses = sorted([clause for clause in mentioned if clause not in allowed_clauses])
        if missing_clauses:
            findings.append(
                ValidationFinding(
                    level="error",
                    code="unsupported_clause_citation",
                    message="Draft cites clauses that are not present as contract clause sources.",
                    evidence=", ".join(missing_clauses[:8]),
                )
            )

        placeholders = self.PLACEHOLDER_PATTERN.findall(artifact.draft_letter)
        if placeholders:
            findings.append(
                ValidationFinding(
                    level="warning",
                    code="placeholders_present",
                    message="Draft contains confirmation placeholders requiring user review.",
                    evidence=", ".join(placeholders[:5]),
                )
            )

        role_text = role.lower()
        role_markers = {
            "contractor": "contractor",
            "employer": "employer",
            "engineer": "engineer",
        }
        if role_text in role_markers:
            draft_lower = artifact.draft_letter.lower()
            marker = role_markers[role_text]
            if marker not in draft_lower:
                findings.append(
                    ValidationFinding(
                        level="warning",
                        code="role_marker_absent",
                        message=f"Draft does not clearly reflect the selected {role_text} profile.",
                    )
                )

        return ValidationReport(
            blocking=any(finding.level == "error" for finding in findings),
            findings=findings,
        )

    def critique(
        self,
        artifact: DraftArtifact,
        role: str,
        sources: List[SourceEvidence],
        finalized: bool = False,
    ) -> ValidationReport:
        """Run validation plus contractual red-flag screening."""

        validation = self.validate(artifact, role, sources, finalized=finalized)
        findings = list(validation.findings)
        draft = artifact.draft_letter or ""

        for code, pattern in self.RED_FLAG_PATTERNS.items():
            match = pattern.search(draft)
            if not match:
                continue
            findings.append(
                ValidationFinding(
                    level="warning",
                    code=code,
                    message=(
                        "Draft contains language with contractual risk; reviewer "
                        "acknowledgement or source-backed revision is recommended."
                    ),
                    evidence=match.group(0),
                )
            )

        if "without prejudice" not in draft.lower() and role.lower() == "contractor":
            findings.append(
                ValidationFinding(
                    level="warning",
                    code="rights_reservation_absent",
                    message="Contractor-profile draft may need an express reservation of rights.",
                )
            )

        return ValidationReport(
            blocking=any(finding.level == "error" for finding in findings),
            findings=findings,
        )

    def skill_findings(self, board: Any, steps: Any, enabled: Any = None) -> ValidationReport:
        """Checks derived from the skills' own rules, reported by assertion id.

        Each finding's ``evidence`` is ``assertion:<id>`` so the Bench can show
        it on the card it belongs to.
        """
        from ..skills.skill_validators import run_skill_validators

        findings = [
            finding.as_validation_finding()
            for finding in run_skill_validators(board, steps, enabled)
        ]
        return ValidationReport(
            blocking=any(finding.level == "error" for finding in findings),
            findings=findings,
        )

    def strategy_alignment(self, artifact: DraftArtifact, plan: str) -> ValidationReport:
        """Check that a draft has an explicit strategic plan and broadly follows it."""

        findings: List[ValidationFinding] = []
        if not (plan or "").strip():
            findings.append(
                ValidationFinding(
                    level="error",
                    code="missing_strategy_plan",
                    message="Draft review requires the latest saved strategic plan.",
                )
            )
            return ValidationReport(blocking=True, findings=findings)

        draft_lower = (artifact.draft_letter or "").lower()
        plan_terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", plan)
            if term.lower()
            not in {
                "drafting",
                "posture",
                "response",
                "matrix",
                "required",
                "outcome",
                "contractual",
                "factual",
                "basis",
                "confirm",
                "inserted",
                "provided",
            }
        ]
        unique_terms = list(dict.fromkeys(plan_terms))[:20]
        if unique_terms:
            matches = sum(1 for term in unique_terms if term in draft_lower)
            if matches < max(1, min(3, len(unique_terms) // 5)):
                findings.append(
                    ValidationFinding(
                        level="warning",
                        code="strategy_alignment_weak",
                        message="Draft has weak lexical alignment with the saved strategic plan; reviewer should compare position, issue framing, and required outcome.",
                    )
                )

        return ValidationReport(
            blocking=any(finding.level == "error" for finding in findings),
            findings=findings,
        )
