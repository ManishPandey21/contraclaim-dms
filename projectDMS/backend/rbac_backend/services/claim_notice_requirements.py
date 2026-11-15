"""Notice periods read from a project's own contract.

Two halves, kept apart on purpose:

- :func:`find_notice_periods` is deterministic text reading. It looks through
  the project's indexed clauses for sentences that state a notice period and
  returns them as candidates, each with the wording it was read from. It
  proposes; it decides nothing.
- :class:`NoticeRequirementService` records the period a person chose, and
  separately records a person verifying it against the quoted wording. Only a
  verified requirement is ever reported as the contract's period.
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
        """Record the period for a claim type. Starts unverified; supersedes the current one."""
        quote = " ".join(payload.quote.split())
        if not re.search(rf"(?<![\d.]){payload.period_days}(?![\d.])", quote):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The quoted contract wording does not state {payload.period_days} days. "
                    "Quote the sentence the period is read from."
                ),
            )
        record = NoticeRequirement(
            organization_id=str(organization_id),
            project_id=str(payload.project_id),
            claim_type=payload.claim_type,
            period_days=payload.period_days,
            trigger=payload.trigger,
            clause_no=normalize_cited_clause(payload.clause_no) or payload.clause_no,
            clause_title=payload.clause_title,
            quote=quote,
            document_id=payload.document_id,
            document_type=payload.document_type,
            clause_uid=payload.clause_uid,
            origin=payload.origin,
            created_by=user_id,
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
        await self._collection.update_one(
            {"_id": requirement.id, "status": "current"},
            {"$set": {"verification": "verified", "verified_by": user_id, "verified_at": now_utc()}},
        )
        refreshed = await self.get(requirement.id)
        return refreshed or requirement


__all__ = ["COLLECTION", "NoticeRequirementService", "find_notice_periods"]
