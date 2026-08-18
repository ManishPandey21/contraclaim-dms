"""Relevant Clause Checking Agent (multi-agent workflow Phase 2).

Verifies the clauses cited in an incoming letter against the project's
structured clause records (``contract_clauses``) and enriches the analysis'
``CitedClauseEvaluation`` entries: existence, title, contract wording snippet,
a conservative applicability signal, and SCC/addendum-modification alerts.

Grounding rules honoured here:
- Never asserts a negative legal conclusion: ``applicable_to_issue`` is only
  ever True (clear keyword overlap) or None (left for the drafter).
- ``exists_in_contract=False`` is only set when the project actually has
  clause records — if the contract was never clause-indexed everything stays
  None with an explanatory drafter comment.
- Position effect stays "unknown": the human decides the contractual position.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ...models.letter_drafting import CitedClauseEvaluation, IncomingLetterAnalysis

logger = logging.getLogger(__name__)

# "GCC 8.4", "SCC Clause 9.2", "Sub-Clause 8.4(a)" -> "8.4" / "9.2" / "8.4(a)"
_PREFIX_RE = re.compile(
    r"^(?:gcc|scc|pcc|er)?\s*(?:sub[-\s]?clause|clause|article|section)?\s*",
    re.IGNORECASE,
)
_STOPWORDS = {
    "the", "and", "for", "with", "shall", "this", "that", "from", "have",
    "been", "will", "under", "upon", "any", "all", "such", "may", "not",
}

_MODIFIER_TYPES = ("SCC", "PCC", "ADDEND", "CORRIG", "AMEND")


def normalize_cited_clause(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = _PREFIX_RE.sub("", str(value).strip()).strip(" .;:,")
    return cleaned or None


def _terms(*texts: Optional[str]) -> set:
    combined = " ".join(t for t in texts if t)
    return {
        token
        for token in re.findall(r"[a-z]{4,}", combined.lower())
        if token not in _STOPWORDS
    }


class ClauseCheckingAgent:
    """Enrich cited-clause evaluations from structured clause records."""

    COLLECTION = "contract_clauses"
    APPLICABILITY_MIN_HITS = 2

    def __init__(self, db: Any) -> None:
        self.db = db

    async def enrich(
        self,
        analysis: IncomingLetterAnalysis,
        org_id: str,
        project_id: str,
    ) -> IncomingLetterAnalysis:
        if self.db is None or not org_id or not project_id:
            return analysis
        if not analysis.cited_clause_evaluations:
            return analysis

        records = await self._load_records(org_id, project_id)
        has_index = bool(records)
        by_no: Dict[str, List[dict]] = {}
        for record in records:
            key = str(record.get("clause_no") or "").strip()
            if key:
                by_no.setdefault(key, []).append(record)

        issue_terms = _terms(
            analysis.subject,
            analysis.main_request,
            " ".join(analysis.key_reply_points or []),
        )

        enriched: List[CitedClauseEvaluation] = []
        for evaluation in analysis.cited_clause_evaluations:
            enriched.append(
                self._evaluate(evaluation, by_no, has_index, issue_terms)
            )
        return analysis.model_copy(update={"cited_clause_evaluations": enriched})

    async def _load_records(self, org_id: str, project_id: str) -> List[dict]:
        try:
            cursor = self.db[self.COLLECTION].find(
                {
                    "org_id": org_id,
                    "project_id": project_id,
                    "is_current": True,
                    "is_authorised_for_ai": True,
                }
            ).limit(400)
            records = [doc async for doc in cursor]
        except Exception as exc:
            logger.warning("clause check: record load failed: %s", exc)
            return []

        # `is_authorised_for_ai` is a clause-level flag fixed at index time from
        # clause confidence; it is never revisited when the PARENT document's
        # authority later becomes adverse, and contract_clauses carries no
        # processing_status of its own. Resolve the canonical document through
        # the one shared retrieval filter - the sibling _clause_record_sources
        # path was already gated, and this checker must match it.
        from ..publication_policy import blocked_document_ids

        blocked = await blocked_document_ids(
            self.db, [record.get("document_id") for record in records]
        )
        if blocked:
            records = [r for r in records if str(r.get("document_id") or "") not in blocked]
        return records

    def _evaluate(
        self,
        evaluation: CitedClauseEvaluation,
        by_no: Dict[str, List[dict]],
        has_index: bool,
        issue_terms: set,
    ) -> CitedClauseEvaluation:
        normalized = normalize_cited_clause(evaluation.clause_number)
        if not has_index:
            return evaluation.model_copy(
                update={
                    "drafter_comment": (
                        "Contract is not clause-indexed for this project; "
                        "verify the clause manually or run clause indexing."
                    )
                }
            )
        matches = by_no.get(normalized or "", [])
        if not matches:
            return evaluation.model_copy(
                update={
                    "exists_in_contract": False,
                    "drafter_comment": (
                        f"Cited clause '{evaluation.clause_number}' was not found in the "
                        "project's clause index — confirm the reference before relying on it."
                    ),
                    "legal_or_commercial_review_required": True,
                }
            )

        # Base wording preference: GCC (or first record) provides the quote.
        base = next(
            (
                m for m in matches
                if not str(m.get("document_type") or "").upper().startswith(_MODIFIER_TYPES)
            ),
            matches[0],
        )
        clause_text = str(base.get("cleaned_text") or "")
        clause_terms = _terms(clause_text, base.get("clause_title"))
        overlap = len(issue_terms & clause_terms)
        applicable: Optional[bool] = True if overlap >= self.APPLICABILITY_MIN_HITS else None

        modifiers = [
            m for m in matches
            if str(m.get("document_type") or "").upper().startswith(_MODIFIER_TYPES)
        ]
        comment: Optional[str] = evaluation.drafter_comment
        review_required = evaluation.legal_or_commercial_review_required
        if modifiers:
            modifier_types = sorted(
                {str(m.get("document_type") or "modifier") for m in modifiers}
            )
            comment = (
                f"{'/'.join(modifier_types)} modifies clause "
                f"{base.get('clause_no')} — check the modified wording before citing the base clause."
            )
            review_required = True

        return evaluation.model_copy(
            update={
                "exists_in_contract": True,
                "clause_title": evaluation.clause_title or base.get("clause_title"),
                "quoted_text": evaluation.quoted_text or (clause_text[:240] or None),
                "applicable_to_issue": applicable,
                "drafter_comment": comment,
                "legal_or_commercial_review_required": review_required,
            }
        )


__all__ = ["ClauseCheckingAgent", "normalize_cited_clause"]
