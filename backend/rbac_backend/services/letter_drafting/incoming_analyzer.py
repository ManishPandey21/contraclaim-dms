from __future__ import annotations

import re
from typing import Any, Optional

from ...models.letter import Letter
from ...models.letter_drafting import (
    CitedClauseEvaluation,
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
)
from .context import condense_text


CLAUSE_PATTERN = re.compile(
    r"\b(?:clause|sub-clause|subclause|section|article)\s+([0-9A-Za-z.\-()]+)",
    re.IGNORECASE,
)
AMOUNT_PATTERN = re.compile(
    r"\b(?:INR|Rs\.?|AED|USD|EUR|GBP)\s*[\d,]+(?:\.\d+)?\b",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)
LETTER_NO_PATTERN = re.compile(
    r"\b(?:letter|ref(?:erence)?|no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/._-]{2,})",
    re.IGNORECASE,
)
SUBJECT_PATTERN = re.compile(
    r"\bsubject\s*[:\-]\s*(.+?)(?=\s+(?:contract|project|package|letter|ref(?:erence)?|we\s+request|we\s+require)\b|$)",
    re.IGNORECASE,
)
SENDER_PATTERN = re.compile(
    r"\b(?:from|sender)\s*[:\-]\s*(.+?)(?=\s+(?:letter|ref(?:erence)?|subject|contract|project|package)\b|$)",
    re.IGNORECASE,
)
RESPONSE_DUE_PATTERN = re.compile(
    r"\b(?:within\s+\d+\s+(?:day|days)|by\s+\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|on or before\s+\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b",
    re.IGNORECASE,
)
CONTRACT_REF_PATTERN = re.compile(
    r"\b(?:contract|project|package)\s*(?:no\.?|ref(?:erence)?|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9/._ -]{2,}?)(?=\s+(?:subject|letter|ref(?:erence)?|we\s+request|we\s+require|clause)\b|$)",
    re.IGNORECASE,
)

ISSUE_KEYWORDS = [
    ("claim", ("claim", "entitlement", "compensation")),
    ("delay", ("delay", "eot", "extension of time", "programme", "progress")),
    ("variation", ("variation", "change order", "extra item", "additional work")),
    ("payment", ("payment", "invoice", "ipc", "withhold", "deduction", "recovery")),
    ("approval", ("approval", "approved", "submission", "material approval")),
    ("dispute", ("dispute", "determination", "arbitration", "adjudication")),
    ("notice", ("notice", "notify", "notification")),
    ("contractual_compliance", ("non-compliance", "breach", "default", "compliance")),
    ("request_for_information", ("request for information", "rfi", "clarification")),
]


class IncomingLetterAnalyzer:
    """Extracts a lightweight, reviewable analysis from an incoming source."""

    async def analyze(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        document_service: Any,
    ) -> IncomingLetterAnalysis:
        text = await self._incoming_text(letter, request, document_service)
        subject = request.subject or getattr(letter, "subject", None)
        if not text:
            return IncomingLetterAnalysis(
                sender_role_or_party_type=self._party_type(
                    request.role or getattr(letter, "strategy_role", None)
                ),
                subject=subject,
                issue_type=request.issue_type or self._issue_type(
                    " ".join([subject or "", request.requirements or "", request.points or ""])
                ),
                issue_type_source="manual" if request.issue_type else "ai",
                main_request=request.purpose or request.requirements,
                action_requested=request.required_action,
                response_required=bool(request.required_action),
                response_deadline=request.response_deadline,
                deadline_risk=self._deadline_risk(request.response_deadline),
                priority_flag=bool(request.response_deadline),
                approval_urgency="urgent" if request.response_deadline else "normal",
                workflow_due_date=request.response_deadline,
                extraction_confidence=0.25,
            )

        clauses = list(
            dict.fromkeys(clause.strip(" .;,") for clause in CLAUSE_PATTERN.findall(text))
        )[:10]
        amount = self._first_match(AMOUNT_PATTERN, text)
        dates = DATE_PATTERN.findall(text)
        deadline = request.response_deadline or self._first_match(RESPONSE_DUE_PATTERN, text)
        issue_type = request.issue_type or self._issue_type(text)
        contract_ref = self._first_match(CONTRACT_REF_PATTERN, text)
        subject_from_text = self._subject_from_text(text)
        action_requested = request.required_action or self._action_requested(text)
        main_request = condense_text(request.purpose or request.requirements or text, 500)
        return IncomingLetterAnalysis(
            letter_no=self._letter_no(letter, text),
            letter_date=dates[0] if dates else None,
            sender=self._line_value(SENDER_PATTERN, text),
            sender_role_or_party_type=self._party_type(
                request.role or getattr(letter, "strategy_role", None)
            ),
            recipient=request.recipient or getattr(letter, "recipient", None),
            subject=subject_from_text or subject,
            contract_project_reference=contract_ref,
            subject_matches_requested_matter=self._subject_matches(subject, subject_from_text),
            issue_type=issue_type,
            issue_type_source="manual" if request.issue_type else "ai",
            main_request=main_request,
            clauses_cited=clauses,
            cited_clause_evaluations=[
                CitedClauseEvaluation(
                    clause_number=clause,
                    exists_in_contract=None,
                    quote_matches_contract=None,
                    applicable_to_issue=None,
                    effect_on_sender_position="unknown",
                    legal_or_commercial_review_required=issue_type
                    in {"claim", "delay", "variation", "payment", "dispute"},
                )
                for clause in clauses
            ],
            amount_claimed=amount,
            action_requested=action_requested,
            response_required=bool(action_requested or deadline),
            response_deadline=deadline,
            contractual_response_period=self._contractual_response_period(text),
            deadline_risk=self._deadline_risk(deadline),
            recommended_immediate_action=self._immediate_action(issue_type, deadline),
            priority_flag=self._deadline_risk(deadline) == "high",
            drafter_alert=self._deadline_alert(deadline),
            reviewer_alert=self._deadline_alert(deadline),
            approval_urgency="urgent" if self._deadline_risk(deadline) == "high" else "normal",
            workflow_due_date=deadline,
            contractual_risk=self._risk_label(request, clauses, amount),
            extraction_confidence=0.65 if text else 0.25,
        )

    async def _incoming_text(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        document_service: Any,
    ) -> Optional[str]:
        if request.incoming_document_id:
            try:
                docs = await document_service.get_documents_by_ids([request.incoming_document_id])
            except Exception:
                docs = []
            if docs:
                doc = docs[0]
                return condense_text(
                    getattr(doc, "summary", None)
                    or getattr(doc, "full_text", None)
                    or getattr(doc, "ocrText", None)
                    or getattr(doc, "content", None),
                    2500,
                )
        return condense_text(
            request.points
            or request.requirements
            or request.background_facts
            or getattr(letter, "content", None),
            2500,
        )

    @staticmethod
    def _first_match(pattern: re.Pattern[str], text: str) -> Optional[str]:
        match = pattern.search(text or "")
        return match.group(0) if match else None

    @staticmethod
    def _line_value(pattern: re.Pattern[str], text: str) -> Optional[str]:
        match = pattern.search(text or "")
        if not match:
            return None
        return condense_text(match.group(1).strip(), 160)

    @staticmethod
    def _letter_no(letter: Letter, text: str) -> Optional[str]:
        reference = getattr(letter, "reference", None)
        if reference:
            if isinstance(reference, dict):
                return reference.get("reference_number") or reference.get("id")
            return getattr(reference, "reference_number", None) or getattr(reference, "id", None)
        match = LETTER_NO_PATTERN.search(text or "")
        return match.group(1) if match else getattr(letter, "previous_letter_no", None)

    @staticmethod
    def _subject_from_text(text: str) -> Optional[str]:
        value = IncomingLetterAnalyzer._line_value(SUBJECT_PATTERN, text)
        return condense_text(value, 300) if value else None

    @staticmethod
    def _subject_matches(request_subject: Optional[str], text_subject: Optional[str]) -> Optional[bool]:
        if not request_subject or not text_subject:
            return None
        request_terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]{4,}", request_subject)
        }
        text_terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]{4,}", text_subject)
        }
        if not request_terms or not text_terms:
            return None
        return bool(request_terms & text_terms)

    @staticmethod
    def _issue_type(text: str) -> str:
        lowered = (text or "").lower()
        for issue_type, keywords in ISSUE_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                return issue_type
        return "other"

    @staticmethod
    def _party_type(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        normalized = str(value).strip().lower()
        if normalized.startswith("contractor"):
            return "contractor"
        if normalized.startswith("engineer"):
            return "engineer"
        if normalized.startswith("employer"):
            return "employer"
        return str(value)

    @staticmethod
    def _action_requested(text: str) -> Optional[str]:
        lowered = (text or "").lower()
        markers = ("request", "requested", "require", "required", "submit", "approve", "respond")
        for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
            if any(marker in sentence.lower() for marker in markers):
                return condense_text(sentence, 300)
        if "?" in lowered:
            return "Response requested by sender."
        return None

    @staticmethod
    def _contractual_response_period(text: str) -> Optional[str]:
        match = re.search(r"\bwithin\s+(\d+\s+(?:day|days))\b", text or "", re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _deadline_risk(deadline: Optional[str]) -> Optional[str]:
        if not deadline:
            return None
        lowered = deadline.lower()
        if "within" in lowered or "on or before" in lowered or "by " in lowered:
            return "high"
        return "medium"

    @staticmethod
    def _immediate_action(issue_type: str, deadline: Optional[str]) -> Optional[str]:
        if not deadline and issue_type == "other":
            return None
        if deadline:
            return "Confirm response owner and reviewer immediately due to stated response deadline."
        return "Confirm issue classification and supporting contract records before drafting."

    @staticmethod
    def _deadline_alert(deadline: Optional[str]) -> Optional[str]:
        if not deadline:
            return None
        return f"Response deadline detected: {deadline}"

    @staticmethod
    def _risk_label(
        request: DraftRunCreateRequest,
        clauses: list[str],
        amount: Optional[str],
    ) -> Optional[str]:
        if request.letter_category in {"claim_reply", "eot_reply", "dispute"} or amount:
            return "high"
        if clauses:
            return "medium"
        return "low"
