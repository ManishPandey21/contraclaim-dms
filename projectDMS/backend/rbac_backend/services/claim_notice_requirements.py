"""Notice periods read from a project's own contract.

Two halves, kept apart on purpose:

- :func:`find_notice_periods` is deterministic text reading. It looks through
  the project's indexed clauses for sentences that state a notice period and
  returns them as candidates, each with the wording it was read from. It
  proposes; it decides nothing.
- :class:`NoticeRequirementService` records the period a person chose, and
  separately records a person verifying it against the quoted wording. Only a
  verified requirement is ever reported as the contract's period.

D-08 (plan 4B-14, C-08). Before it, a typed quote that merely contained the day
number became a "contract, verified" period: the client supplied the clause,
the quote, the document and ``origin=clause_index``, and the recorder could
verify their own entry. Now:

- the server resolves the clause in this project's clause index, by
  ``clause_uid`` and then by normalised clause number. When it resolves, the
  normalised quote must be in that clause's text (422 otherwise) and the clause
  number, title, document and uid are the index's; ``origin`` is
  ``clause_index``. When it does not, the record is ``entered_from_contract``
  and carries none of the client's document or uid claims;
- ``claim_type`` is one of the claim types (model validation, 422);
- the verifier cannot be the recorder (403 ``notice_self_verification``), and
  verification is a compare-and-set on the unverified current record;
- both actions write an audit event. Clause-read rights are checked by the
  routes on both actions.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from ..models.claim_notice import (
    NoticePeriodCandidate,
    NoticeRequirement,
    NoticeRequirementCreate,
)
from ..utils.datetime import now_utc
from .audit_event_service import AuditEventService
from .letter_drafting.clause_checker import _MODIFIER_TYPES, ClauseCheckingAgent, normalize_cited_clause

logger = logging.getLogger(__name__)

COLLECTION = "claim_notice_requirements"
MAX_CANDIDATES = 12

_SENTENCE = re.compile(r"[^.;\n]+[.;\n]?")
_DAYS = re.compile(
    r"(?<![\d.])(\d{1,3})\s*\)?\s*(?:calendar\s+|working\s+|business\s+)?days?\b", re.IGNORECASE
)
_NOTICE = re.compile(r"\bnotice\b|\bnotif(?:y|ies|ied|ication)\b", re.IGNORECASE)
_CLAIM_CONTEXT = re.compile(
    r"\bclaim|\bextension of time|\badditional payment|\bevent\b|\bcircumstance|\bdelay|\bvariation|\bentitle",
    re.IGNORECASE,
)
_AWARENESS = re.compile(r"\baware\b|\bbecame aware\b|\bshould have become aware\b|\bknowledge\b", re.IGNORECASE)
_RELEVANT_TITLE = re.compile(
    r"\bclaim|\bnotice|\bextension of time|\bdelay|\bvariation|\bdispute", re.IGNORECASE
)


_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def normalise_wording(text: Any) -> str:
    """Wording compared for containment: case, whitespace, curly quotes and dashes folded."""
    return " ".join(str(text or "").translate(_QUOTE_FOLD).lower().split())


def _clause_text(record: Dict[str, Any]) -> str:
    return str(record.get("text") or record.get("cleaned_text") or "")


def resolve_clause(
    records: Iterable[Dict[str, Any]],
    *,
    clause_uid: Optional[str],
    clause_no: str,
    document_id: Optional[str],
    quote: str,
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(the clause the quote is in, every clause the reference resolved to)``.

    By ``clause_uid`` first; else every clause with the same normalised number,
    the named document's first. The first resolved clause whose text contains
    the quote is returned; if the reference resolved but no text contains the
    quote, the first value is None and the second is not empty.
    """
    pool = list(records)
    resolved: List[Dict[str, Any]] = []
    if clause_uid:
        resolved = [record for record in pool if str(record.get("clause_uid") or "") == str(clause_uid)]
    if not resolved:
        wanted = normalize_cited_clause(clause_no)
        resolved = [
            record
            for record in pool
            if wanted and normalize_cited_clause(str(record.get("clause_no") or "")) == wanted
        ]
        if document_id:
            resolved.sort(key=lambda record: str(record.get("document_id") or "") != str(document_id))
    needle = normalise_wording(quote)
    for record in resolved:
        if needle and needle in normalise_wording(_clause_text(record)):
            return record, resolved
    return None, resolved


def _audit_view(record: "NoticeRequirement") -> Dict[str, Any]:
    return record.model_dump(
        include={
            "id", "claim_type", "period_days", "trigger", "clause_no", "clause_uid", "document_id",
            "origin", "verification", "status", "created_by", "verified_by",
        }
    )


def _is_modifier(document_type: Optional[str]) -> bool:
    return str(document_type or "").upper().startswith(_MODIFIER_TYPES)


def find_notice_periods(
    clauses: Iterable[Dict[str, Any]], cited_clause_numbers: Iterable[str] = ()
) -> List[NoticePeriodCandidate]:
    """Notice periods stated in ``clauses``, most relevant first.

    A sentence is a candidate when it speaks of giving notice, in the context
    of a claim or delay, and states a number of days. Order: clauses the claim
    cites, then documents that modify the general conditions, then by clause
    number. Duplicates of the same clause, period and wording are dropped.
    """
    cited = {normalize_cited_clause(value) for value in cited_clause_numbers}
    cited.discard(None)
    found: List[NoticePeriodCandidate] = []
    seen: set = set()
    for clause in clauses:
        clause_no = str(clause.get("clause_no") or "").strip()
        if not clause_no:
            continue
        title = str(clause.get("clause_title") or "")
        is_cited = clause_no in cited
        text = str(clause.get("text") or clause.get("cleaned_text") or "")
        if not text or not (is_cited or _RELEVANT_TITLE.search(title) or _NOTICE.search(text)):
            continue
        for match in _SENTENCE.finditer(text):
            sentence = " ".join(match.group(0).split())
            if len(sentence) < 20 or not _NOTICE.search(sentence) or not _CLAIM_CONTEXT.search(sentence):
                continue
            for days_match in _DAYS.finditer(sentence):
                days = int(days_match.group(1))
                if not 1 <= days <= 365:
                    continue
                key = (clause_no, str(clause.get("document_id") or ""), days, sentence)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    NoticePeriodCandidate(
                        period_days=days,
                        trigger="awareness" if _AWARENESS.search(sentence) else "event",
                        clause_no=clause_no,
                        clause_title=title or None,
                        quote=sentence[:2000],
                        document_id=str(clause.get("document_id") or "") or None,
                        document_type=clause.get("document_type"),
                        clause_uid=clause.get("clause_uid"),
                        modifies_general_conditions=_is_modifier(clause.get("document_type")),
                        cited_by_claim=is_cited,
                    )
                )
    found.sort(
        key=lambda item: (
            not item.cited_by_claim,
            not item.modifies_general_conditions,
            [int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", item.clause_no)],
            item.period_days,
        )
    )
    return found[:MAX_CANDIDATES]


class NoticeRequirementService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    @property
    def _collection(self) -> Any:
        return self.db[COLLECTION]

    async def clause_records(self, organization_id: str, project_id: str) -> List[Dict[str, Any]]:
        """Current, AI-authorised clauses whose document is still publishable."""
        return await ClauseCheckingAgent(self.db)._load_records(organization_id, project_id)

    async def candidates(
        self, organization_id: str, project_id: str, cited_clause_numbers: Iterable[str] = ()
    ) -> tuple[bool, List[NoticePeriodCandidate]]:
        records = await self.clause_records(organization_id, project_id)
        return bool(records), find_notice_periods(records, cited_clause_numbers)

    async def current(
        self, organization_id: Optional[str], project_id: Optional[str], claim_type: str
    ) -> Optional[NoticeRequirement]:
        if not organization_id or not project_id:
            return None
        doc = await self._collection.find_one(
            {
                "organization_id": str(organization_id),
                "project_id": str(project_id),
                "claim_type": claim_type,
                "status": "current",
            }
        )
        return NoticeRequirement(**doc) if doc else None

    async def current_for_project(
        self, organization_id: Optional[str], project_id: Optional[str]
    ) -> Dict[str, NoticeRequirement]:
        if not organization_id or not project_id:
            return {}
        docs = await self._collection.find(
            {"organization_id": str(organization_id), "project_id": str(project_id), "status": "current"}
        ).to_list(length=100)
        return {doc["claim_type"]: NoticeRequirement(**doc) for doc in docs}

    async def history(self, organization_id: str, project_id: str, claim_type: str) -> List[NoticeRequirement]:
        docs = await self._collection.find(
            {"organization_id": str(organization_id), "project_id": str(project_id), "claim_type": claim_type}
        ).to_list(length=200)
        return sorted((NoticeRequirement(**doc) for doc in docs), key=lambda item: item.created_at, reverse=True)

    async def set(
        self, organization_id: str, payload: NoticeRequirementCreate, user_id: Optional[str]
    ) -> NoticeRequirement:
        """Record the period for a claim type. Starts unverified; supersedes the current one.

        The clause is resolved here, never taken from the client (D-08).
        """
        quote = " ".join(payload.quote.split())
        if not re.search(rf"(?<![\d.]){payload.period_days}(?![\d.])", quote):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "notice_quote_period",
                    "user_message": (
                        f"The quoted contract wording does not state {payload.period_days} days. "
                        "Quote the sentence the period is read from."
                    ),
                    "detail": None,
                },
            )
        records = await self.clause_records(str(organization_id), str(payload.project_id))
        clause, resolved = resolve_clause(
            records,
            clause_uid=payload.clause_uid,
            clause_no=payload.clause_no,
            document_id=payload.document_id,
            quote=quote,
        )
        if resolved and clause is None:
            numbers = sorted({str(item.get("clause_no") or "") for item in resolved})
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "notice_quote_not_in_clause",
                    "user_message": (
                        f"The quoted wording is not in clause {', '.join(numbers)} of this project's contract. "
                        "Copy the sentence from the clause as it is indexed."
                    ),
                    "detail": {"clause_no": numbers},
                },
            )
        if clause is not None:
            provenance: Dict[str, Any] = {
                "clause_no": str(clause.get("clause_no") or payload.clause_no).strip()[:40],
                "clause_title": (str(clause.get("clause_title") or "")[:300] or None),
                "document_id": str(clause.get("document_id") or "") or None,
                "document_type": clause.get("document_type"),
                "clause_uid": clause.get("clause_uid"),
                "origin": "clause_index",
            }
        else:
            # Not in the index: the person's own reading of the contract, shown as such.
            provenance = {
                "clause_no": normalize_cited_clause(payload.clause_no) or payload.clause_no,
                "clause_title": payload.clause_title,
                "document_id": None,
                "document_type": None,
                "clause_uid": None,
                "origin": "entered_from_contract",
            }
        record = NoticeRequirement(
            organization_id=str(organization_id),
            project_id=str(payload.project_id),
            claim_type=payload.claim_type,
            period_days=payload.period_days,
            trigger=payload.trigger,
            quote=quote,
            created_by=user_id,
            **provenance,
        )
        previous = await self.current(organization_id, payload.project_id, payload.claim_type)
        if previous is not None:
            await self._collection.update_one(
                {"_id": previous.id, "status": "current"},
                {"$set": {"status": "superseded", "superseded_at": now_utc(), "superseded_by_id": record.id}},
            )
        try:
            await self._collection.insert_one(record.model_dump(by_alias=True))
        except DuplicateKeyError as exc:
            # Someone else recorded a period for this claim type at the same moment.
            raise HTTPException(
                status_code=409,
                detail="The notice period for this claim type was just changed by someone else. Reload and try again.",
            ) from exc
        await self.audit.emit(
            action="claim.notice_requirement.set",
            actor_id=user_id,
            resource_type="claim_notice_requirement",
            resource_id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            before=_audit_view(previous) if previous is not None else None,
            after=_audit_view(record),
            metadata={"superseded_id": previous.id if previous is not None else None},
        )
        return record

    async def get(self, requirement_id: str) -> Optional[NoticeRequirement]:
        doc = await self._collection.find_one({"_id": requirement_id})
        return NoticeRequirement(**doc) if doc else None

    async def verify(self, requirement: NoticeRequirement, user_id: Optional[str]) -> NoticeRequirement:
        """A person confirms the period against the quoted clause wording."""
        if requirement.status != "current":
            raise HTTPException(
                status_code=409, detail="This notice period has been replaced; verify the current one"
            )
        if requirement.verification == "verified":
            return requirement
        if not user_id:
            raise HTTPException(status_code=403, detail="Verification needs an identified user")
        if requirement.created_by and str(requirement.created_by) == str(user_id):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "notice_self_verification",
                    "user_message": "You recorded this notice period, so someone else must verify it against the contract.",
                    "detail": None,
                },
            )
        result = await self._collection.update_one(
            {"_id": requirement.id, "status": "current", "verification": "extracted_unverified"},
            {"$set": {"verification": "verified", "verified_by": user_id, "verified_at": now_utc()}},
        )
        refreshed = await self.get(requirement.id) or requirement
        if not getattr(result, "modified_count", 0):
            if refreshed.status != "current":
                raise HTTPException(
                    status_code=409, detail="This notice period has been replaced; verify the current one"
                )
            return refreshed  # verified by someone else a moment ago
        await self.audit.emit(
            action="claim.notice_requirement.verified",
            actor_id=user_id,
            resource_type="claim_notice_requirement",
            resource_id=requirement.id,
            organization_id=requirement.organization_id,
            project_id=requirement.project_id,
            before=_audit_view(requirement),
            after=_audit_view(refreshed),
        )
        return refreshed


__all__ = ["COLLECTION", "NoticeRequirementService", "find_notice_periods", "normalise_wording", "resolve_clause"]
