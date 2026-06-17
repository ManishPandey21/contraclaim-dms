"""Clause-grounded claim assessment (Phase 4 / Module 5).

Runs the existing citation-enforced contract-QA engine
(``RetrievalService.contract_iterative_qa``) over the project's contract, scoped
to a claim, to produce a *cited* draft assessment — relevant clauses, precedence
(SCC > GCC), entitlement and gaps. No new AI infrastructure: this layer only
builds the right question from the claim, invokes the engine, and persists the
answer + citations + iteration trace for traceability.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models.claim import ClaimAssessment
from .audit_event_service import AuditEventService

logger = logging.getLogger(__name__)

# Per-type framing for the assessment question. Each focuses the retrieval on the
# clauses that decide that claim sub-type.
_TYPE_FOCUS: Dict[str, str] = {
    "eot": (
        "Assess entitlement to an extension of time. Identify the relevant delay / "
        "extension-of-time clauses, the notice requirements and time-bars, and whether "
        "the described delay event is a qualifying (compensable/excusable) event."
    ),
    "variation": (
        "Assess this variation / change. Identify the variation (change) clauses, the "
        "valuation rules and the instruction/authorisation requirements, and whether the "
        "described work is a variation under the contract."
    ),
    "payment_ipc": (
        "Assess this payment / interim payment certificate claim. Identify the payment "
        "clauses, the certification and due-date rules, and any conditions precedent to "
        "payment for the described amounts."
    ),
    "loss_expense": (
        "Assess entitlement to loss and/or expense. Identify the relevant clauses, the "
        "notice requirements, and whether the described matter is a compensable head of claim."
    ),
    "acceleration": (
        "Assess this acceleration claim. Identify clauses dealing with acceleration / "
        "constructive acceleration and the instruction and recovery requirements."
    ),
    "defect": (
        "Assess this defect / defects-liability matter. Identify the defects-liability and "
        "rectification clauses and the responsibilities and time limits they impose."
    ),
}

_DEFAULT_FOCUS = (
    "Assess this claim against the contract. Identify the relevant clauses, the "
    "applicable requirements, and whether there is a contractual basis for it."
)


def build_assessment_query(claim: Dict[str, Any]) -> str:
    """Compose the clause-grounded assessment question for a claim (pure)."""
    claim_type = str(claim.get("type") or "other")
    focus = _TYPE_FOCUS.get(claim_type, _DEFAULT_FOCUS)
    parts = [focus]
    title = claim.get("title")
    if title:
        parts.append(f"Claim title: {title}.")
    description = claim.get("description")
    if description:
        parts.append(f"Details: {description}.")
    clauses = claim.get("contract_clauses") or []
    if clauses:
        parts.append("Pay particular attention to these clauses: " + ", ".join(map(str, clauses)) + ".")
    parts.append(
        "State the precedence between Special/Particular and General Conditions where relevant, "
        "and list any gaps or missing evidence. Cite the specific clauses you rely on."
    )
    return " ".join(parts)


class ClaimAssessmentService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        from ..core.database import get_database

        return await get_database()

    def _build_request(self, claim: Dict[str, Any]):
        # Imported lazily so the claims router doesn't pull the retrieval stack
        # at import time.
        from ..retrieval.models import ContractQARequest, SearchFilters

        org_id = str(claim.get("organization_id") or "")
        project_id = str(claim.get("project_id") or "")
        filters = SearchFilters(
            org_id=org_id,
            project_id=project_id,
            metadata={"uploadType": "contract", "document_type": "contract"},
        )
        return ContractQARequest(
            query=build_assessment_query(claim),
            filters=filters,
            require_citations=True,
            max_iterations=3,
        )

    async def assess(
        self, claim: Dict[str, Any], current_user: Any, retrieval_service: Any
    ) -> Dict[str, Any]:
        """Run the cited assessment and persist it."""
        request = self._build_request(claim)
        response = await retrieval_service.contract_iterative_qa(request, current_user)

        def _dump(items: Optional[List[Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for item in items or []:
                out.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
            return out

        record = ClaimAssessment(
            claim_id=str(claim.get("_id")),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_type=claim.get("type"),
            query=request.query,
            answer=getattr(response, "answer", "") or "",
            citations=_dump(getattr(response, "citations", None)),
            trace=_dump(getattr(response, "trace", None)),
            created_by=getattr(current_user, "id", None),
        ).model_dump(by_alias=True)

        db = await self._get_db()
        await db.claim_assessments.insert_one(record)
        await self.audit.emit(
            action="claim.assessed",
            actor_id=getattr(current_user, "id", None),
            resource_type="claim",
            resource_id=str(claim.get("_id")),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            after={"assessment_id": record["_id"], "citations": len(record["citations"])},
        )
        return record

    async def list(self, claim_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = db.claim_assessments.find({"claim_id": str(claim_id)}).sort("created_at", -1).limit(limit)
        return [a async for a in cursor]
