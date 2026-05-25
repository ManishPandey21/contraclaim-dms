from __future__ import annotations

from typing import List, Optional

from ...models.letter import Letter
from ...models.letter_drafting import (
    DraftContextBundle,
    DraftRunCreateRequest,
    SourceEvidence,
    ValidationFinding,
    ValidationReport,
)


CLAUSE_SENSITIVE_CATEGORIES = {
    "claim_reply",
    "eot_reply",
    "variation",
    "payment_ipc",
    "advance_recovery",
    "completion",
    "dispute",
}


class DraftInputValidator:
    """Validates the workflow-specific inputs before drafting."""

    def validate_request(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        context: Optional[DraftContextBundle] = None,
        sources: Optional[List[SourceEvidence]] = None,
    ) -> ValidationReport:
        findings: List[ValidationFinding] = []
        sources = sources or []

        if not (request.subject or getattr(letter, "subject", None)):
            findings.append(self._error("missing_subject", "A subject or key issue is required."))

        if not (request.recipient or getattr(letter, "recipient", None)):
            findings.append(self._error("missing_recipient", "An intended recipient is required."))

        has_current_material = bool(
            request.requirements
            or request.points
            or request.purpose
            or request.background_facts
            or request.trigger_event
            or getattr(letter, "content", None)
        )
        if not has_current_material:
            findings.append(
                self._error(
                    "missing_factual_basis",
                    "At least one current instruction, fact, event, or supporting basis is required.",
                )
            )

        if request.draft_type == "reply":
            has_incoming = bool(
                request.incoming_document_id
                or request.incoming_letter_id
                or request.document_ids
                or getattr(letter, "context_document_ids", None)
                or getattr(letter, "previous_letter_id", None)
                or getattr(letter, "reference", None)
                or getattr(letter, "reply_to", None)
            )
            if not has_incoming:
                findings.append(
                    self._error(
                        "missing_incoming_reference",
                        "Reply drafts require an incoming document, incoming letter, or prior reference.",
                    )
                )
        else:
            if not (request.trigger_event or request.background_facts or request.requirements):
                findings.append(
                    self._error(
                        "missing_trigger_event",
                        "Fresh drafts require a trigger event or background facts.",
                    )
                )
            if not request.required_action:
                findings.append(
                    ValidationFinding(
                        level="warning",
                        code="missing_required_action",
                        message="Fresh draft does not state the action expected from the recipient.",
                    )
                )

        has_clause_evidence = any(source.source_type == "contract_clause" for source in sources)
        if request.letter_category in CLAUSE_SENSITIVE_CATEGORIES and not (
            request.clauses_to_consider or has_clause_evidence
        ):
            findings.append(
                ValidationFinding(
                    level="warning",
                    code="missing_clause_basis",
                    message="This letter category normally requires clause references or contract clause extracts.",
                )
            )

        if context:
            missing = [
                key.replace("_", " ")
                for key, present in context.threshold_inputs.items()
                if not present
            ]
            if missing:
                findings.append(
                    self._error(
                        "threshold_inputs_incomplete",
                        "Drafting threshold missing: " + ", ".join(missing) + ".",
                    )
                )

        return ValidationReport(
            blocking=any(finding.level == "error" for finding in findings),
            findings=findings,
        )

    @staticmethod
    def _error(code: str, message: str) -> ValidationFinding:
        return ValidationFinding(level="error", code=code, message=message)
