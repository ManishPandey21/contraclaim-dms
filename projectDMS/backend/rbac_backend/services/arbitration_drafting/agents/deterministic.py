from __future__ import annotations

import re
from datetime import datetime, timedelta
from textwrap import shorten
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ....models.arbitration_drafting import ArbitrationMatrixRow
from ..matrix_registry import MATRIX_COLLECTIONS
from ..repository import _collect, _jsonable


PROMPT_VERSION = "arbitration-workflow-deterministic-1"
MODEL_NAME = "deterministic-matrix-agent"
VERIFIED_STATUSES = {"approved", "edited_verified", "ready", "supported", "user_verified", "verified"}
REVIEW_STATUSES = {"ai_suggested", "draft", "needs_review", "pending", "under_review"}

# Standard pre-arbitration procedural steps from the SoC/SoD/Rejoinder guide (§2).
PRE_ARBITRATION_STEPS = [
    "dispute_notice",
    "engineer_decision",
    "amicable_settlement",
    "conciliation",
    "cooling_period",
]
DEFAULT_LIMITATION_PERIOD_YEARS = 3

# Guide §2: standard pleading-timetable stages.
PLEADING_STAGES = ["statement_of_claim", "statement_of_defence", "counterclaim", "rejoinder", "reply_to_counterclaim"]

# Guide §16 construction-specific issue templates, keyed by dispute category.
DISPUTE_ISSUE_TEMPLATES: Dict[str, Dict[str, str]] = {
    "eot_delay": {
        "respondent_position": (
            "No timely notice was given, the event did not affect the critical path, and any delay "
            "was concurrent with contractor-caused delay."
        ),
        "required_finding": (
            "Tribunal finding on notice compliance, critical path impact, concurrency, and entitlement "
            "to extension of time."
        ),
    },
    "prolongation": {
        "respondent_position": "No compensable delay is established and no actual cost records support the claim.",
        "required_finding": "Tribunal finding on compensability, causation, actual cost, and mitigation.",
    },
    "variation": {
        "respondent_position": (
            "The work was within the original scope, no written instruction was issued, and the claimed "
            "rate is not per the contract mechanism."
        ),
        "required_finding": "Tribunal finding on instruction, scope difference, measurement, and valuation basis.",
    },
    "payment": {
        "respondent_position": "The amount was not certified, the works were defective, or the sum was validly set off.",
        "required_finding": "Tribunal finding on certification, entitlement, and wrongful withholding.",
    },
    "ld": {
        "respondent_position": (
            "Milestones were missed due to contractor default and liquidated damages were contractually levied."
        ),
        "required_finding": "Tribunal finding on attribution of delay and validity of the deduction.",
    },
}


def _dispute_category(claim: Dict[str, Any]) -> str:
    text = " ".join(str(claim.get(key) or "") for key in ["claim_head", "facts", "causation", "relief"]).lower()
    if any(term in text for term in ["liquidated damages", " ld ", "ld refund", "deduction", "recovery"]):
        return "ld"
    if any(term in text for term in ["variation", "extra work", "extra item", "change order"]):
        return "variation"
    if any(term in text for term in ["prolongation", "overhead", "idle"]):
        return "prolongation"
    if any(term in text for term in ["delay", "eot", "extension of time", "critical path"]):
        return "eot_delay"
    if any(term in text for term in ["payment", "ipc", "bill", "certified", "certification"]):
        return "payment"
    return "general"


class DeterministicArbitrationAgent:
    def __init__(
        self,
        *,
        db: Any,
        case: Dict[str, Any],
        draft_id: Optional[str],
        options: Dict[str, Any],
        current_user: Any,
    ) -> None:
        self.db = db
        self.case = case
        self.case_id = str(case.get("_id"))
        self.draft_id = draft_id
        self.options = options
        self.current_user = current_user
        self.created_records: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.source_ids: set[str] = set()

    async def run(self, agent_type: str) -> Dict[str, Any]:
        normalized = _normalize_agent_type(agent_type)
        try:
            if normalized == "orchestrator":
                await self._run_orchestrator()
            else:
                await self._run_one(normalized)
        except Exception as exc:  # pragma: no cover - defensive persistence guard
            self.errors.append(f"{agent_type} failed: {exc}")
        return self._result(agent_type)

    async def _run_orchestrator(self) -> None:
        sequence = self.options.get("sequence") or [
            "document-indexing",
            "chronology-builder-adapter",
            "clause-interpretation",
            "jurisdiction",
            "claim-identification",
            "quantum",
            "delay-expert",
            "notice-compliance",
            "issue-framing",
        ]
        for agent_type in sequence:
            await self._run_one(_normalize_agent_type(str(agent_type)))

    async def _run_one(self, agent_type: str) -> None:
        handlers = {
            "document-indexing": self._document_indexing,
            "document-understanding": self._document_understanding,
            "jurisdiction": self._jurisdiction,
            "chronology-builder-adapter": self._chronology_builder_adapter,
            "clause-interpretation": self._clause_interpretation,
            "issue-framing": self._issue_framing,
            "claim-identification": self._claim_identification,
            "defence-analysis": self._defence_analysis,
            "counterclaim-setoff": self._review_only_agent,
            "rejoinder-reply": self._rejoinder_reply,
            "quantum": self._quantum,
            "notice-compliance": self._notice_compliance,
            "delay-expert": self._delay_expert_alignment,
            "review-consistency": self._review_consistency,
            "legal-guardrail": self._review_only_agent,
        }
        handler = handlers.get(agent_type)
        if not handler:
            self.warnings.append(f"Unknown arbitration agent '{agent_type}'. No matrix rows were created.")
            return
        await handler(agent_type)

    async def _document_indexing(self, agent_type: str) -> None:
        rows = await self._find_source_rows("documents", limit=int(self.options.get("document_limit") or 50))
        if not rows:
            self.warnings.append("Document indexing found no scoped documents for this arbitration case.")
            return
        for document in rows:
            document_id = _id(document)
            if not document_id:
                continue
            row = {
                "source_type": document.get("source_type") or "document",
                "source_id": document_id,
                "title": _document_title(document),
                "document_date": _first(document, "document_date", "letter_date", "date", "created_at"),
                "letter_no": _first(document, "letter_no", "letterNo", "reference_no", "document_number"),
                "sender": _first(document, "from_company", "sender", "from", "issuing_party"),
                "recipient": _first(document, "to_company", "recipient", "to", "receiving_party"),
                "document_type": _document_type(document),
                "relevance_note": _condense(_first(document, "subject", "summary", "description", "full_content", "extracted_text")),
                "source_file_link": _first(document, "file_url", "source_file_link", "storage_path", "file_path") or f"/documents/{document_id}",
                "allowed_use": "fact",
                "risk_flags": _document_risk_flags(document),
                "exhibit_prefix": _exhibit_prefix(self.case, self.options),
            }
            await self._insert_matrix_row(
                "document-index",
                row,
                unique={"source_id": document_id},
                agent_type=agent_type,
                source_id=document_id,
                label=row["title"],
            )

    async def _chronology_builder_adapter(self, agent_type: str) -> None:
        events = await self._find_source_rows("matter_chronology_events", limit=int(self.options.get("chronology_limit") or 100))
        include_review = bool(self.options.get("include_review_sources"))
        events = [event for event in events if _verified(event) or include_review and _reviewable(event)]
        if not events:
            self.warnings.append("Chronology adapter found no verified chronology events for this arbitration case.")
            return
        for event in events:
            event_id = _id(event)
            if not event_id:
                continue
            title = _first(event, "title", "event", "description") or "Chronology event"
            evidence = [item for item in [_first(event, "source_document_id", "document_id", "source_id")] if item]
            row = {
                "chronology_id": _first(event, "chronology_id"),
                "chronology_event_id": event_id,
                "date": _first(event, "event_date", "date", "start_date", "created_at"),
                "event": _condense(f"{title}. {_first(event, 'description', 'details') or ''}", 500),
                "document_ref": _first(event, "document_ref", "source_document_id", "source_id"),
                "party_responsible": _first(event, "responsible_party", "party_responsible", "delay_owner"),
                "clause": _first(event, "clause", "clause_number") or ", ".join(_string_list(event.get("contract_clauses"))),
                "impact": _first(event, "impact", "delay_impact", "effect"),
                "evidence": evidence,
                "issue_link": _first(event, "issue_link", "issue_id"),
                "claim_link": _first(event, "claim_link", "claim_id"),
                "pleading_use": _first(event, "pleading_use") or "chronology",
            }
            await self._insert_matrix_row(
                "chronology-matrix",
                row,
                unique={"chronology_event_id": event_id},
                agent_type=agent_type,
                source_id=event_id,
                label=str(title),
            )

    async def _clause_interpretation(self, agent_type: str) -> None:
        clauses = await self._clause_source_rows()
        if not clauses and self.case.get("arbitration_clause"):
            clauses = [
                {
                    "_id": f"case:{self.case_id}:arbitration_clause",
                    "topic": "Arbitration agreement",
                    "clause_number": "Arbitration clause",
                    "clause_text_excerpt": self.case.get("arbitration_clause"),
                    "obligation_or_right": "Arbitration agreement relied on for jurisdiction and procedure.",
                }
            ]
        if not clauses:
            self.warnings.append("Clause agent found no scoped contract clauses or arbitration clause text.")
            return
        for clause in clauses:
            source_id = str(_id(clause) or _first(clause, "clause_source_id", "clause_number") or "")
            clause_number = _first(clause, "clause_number", "clause", "section_number", "reference") or "Unnumbered clause"
            excerpt = _first(clause, "clause_text_excerpt", "clause_text", "text", "content", "chunk_text", "excerpt")
            if not excerpt:
                continue
            row = {
                "topic": _first(clause, "topic", "heading", "title", "section_title") or _topic_from_clause(str(excerpt)),
                "clause_source_id": source_id or clause_number,
                "clause_number": clause_number,
                "clause_text_excerpt": _condense(excerpt, 900),
                "obligation_or_right": _first(clause, "obligation_or_right", "interpretation", "summary"),
                "claimant_use": _first(clause, "claimant_use"),
                "respondent_use": _first(clause, "respondent_use"),
                "related_evidence_ids": _string_list(_first(clause, "related_evidence_ids", "source_document_ids")),
                "risk": _first(clause, "risk", "risk_note"),
            }
            await self._insert_matrix_row(
                "clause-matrix",
                row,
                unique={"clause_source_id": row["clause_source_id"]},
                agent_type=agent_type,
                source_id=row["clause_source_id"],
                label=f"{row['clause_number']}: {row['topic']}",
            )

    async def _claim_identification(self, agent_type: str) -> None:
        claims = await self._find_source_rows("claims", limit=int(self.options.get("claim_limit") or 50))
        if not claims:
            self.warnings.append("Claim agent found no scoped claim register records.")
            return
        for claim in claims:
            claim_id = _id(claim)
            if not claim_id:
                continue
            claim_no = _first(claim, "claim_no", "claim_ref", "reference_no", "number") or claim_id
            amount = _first(claim, "amount_or_days", "amount_claimed", "claim_amount", "amount")
            row = {
                "source_claim_id": claim_id,
                "claim_no": str(claim_no),
                "claim_head": _first(claim, "claim_head", "title", "description") or "Claim",
                "amount_or_days": amount,
                "amount": _numeric(amount),
                "currency": _first(claim, "currency") or "INR",
                "clause_ids": _string_list(_first(claim, "clause_ids", "contract_clauses")),
                "facts": _condense(_first(claim, "facts", "summary", "description", "basis"), 800),
                "notice_ids": _string_list(_first(claim, "notice_ids", "notice_references")),
                "evidence_ids": _string_list(_first(claim, "evidence_ids", "supporting_document_ids", "document_ids")),
                "causation": _first(claim, "causation", "cause", "delay_cause"),
                "calculation_id": f"Q-CLAIM-{_safe_key(str(claim_no))}",
                "weakness": _first(claim, "weakness", "risk", "remarks"),
                "relief": _first(claim, "relief", "relief_sought") or _relief_from_amount(amount),
            }
            await self._insert_matrix_row(
                "claim-matrix",
                row,
                unique={"source_claim_id": claim_id},
                agent_type=agent_type,
                source_id=claim_id,
                label=str(row["claim_head"]),
            )

    async def _quantum(self, agent_type: str) -> None:
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for collection_name in ["claims", "variations", "ipc_bills"]:
            for row in await self._find_source_rows(collection_name, limit=int(self.options.get("quantum_limit") or 50)):
                candidates.append((collection_name, row))
        chronology_rows = await self._matrix_rows("chronology-matrix")
        created_any = False
        for collection_name, source in candidates:
            source_id = _id(source)
            amount = _first(
                source,
                "amount_claimed",
                "claim_amount",
                "approved_amount",
                "certified_amount",
                "bill_amount",
                "variation_amount",
                "amount",
            )
            numeric_amount = _numeric(amount)
            if not source_id or numeric_amount is None:
                continue
            calculation_type = {
                "claims": "claim_summary",
                "variations": "variation",
                "ipc_bills": "payment",
            }.get(collection_name, "claim_summary")
            reference = _first(source, "claim_ref", "variation_ref", "ipc_no", "bill_no", "reference_no") or source_id
            # Guide §11.3: link the amount to its cost head and any delay events.
            delay_event_ids = [
                str(event.get("chronology_event_id") or event.get("_id"))
                for event in chronology_rows
                if str(event.get("claim_link") or "") and str(event.get("claim_link")) == str(reference)
            ]
            row = {
                "calculation_id": f"Q-{collection_name.upper()}-{_safe_key(str(reference))}",
                "calculation_type": calculation_type,
                "source_records": [{"source_collection": collection_name, "source_id": source_id, "reference": reference}],
                "formula": _first(source, "formula", "calculation_basis") or "Amount taken from source register pending reviewer validation.",
                "assumptions": _string_list(_first(source, "assumptions")),
                "amount": numeric_amount,
                "currency": _first(source, "currency") or "INR",
                "tax_treatment": _first(source, "tax_treatment"),
                "checked_by": _first(source, "checked_by"),
                "cost_head": _cost_head(source, collection_name),
                "evidence_ids": _string_list(
                    _first(source, "evidence_ids", "supporting_document_ids", "document_ids", "linked_document_ids")
                ),
                "delay_event_ids": delay_event_ids,
                "delay_period_start": _iso_date(_parse_date(_first(source, "delay_start_date", "period_start"))),
                "delay_period_end": _iso_date(_parse_date(_first(source, "delay_end_date", "period_end"))),
                "critical_path_days": _numeric(_first(source, "eot_days_claimed", "critical_path_days", "delay_days")),
            }
            inserted = await self._insert_matrix_row(
                "quantum-annexures",
                row,
                unique={"calculation_id": row["calculation_id"]},
                agent_type=agent_type,
                source_id=source_id,
                label=row["calculation_id"],
            )
            created_any = created_any or inserted
        if not created_any:
            self.warnings.append("Quantum agent found no scoped amount records with traceable source ids.")
        await self._interest_annexures(agent_type)
        await self._claim_summary_rollup(agent_type)

    async def _interest_annexures(self, agent_type: str) -> None:
        """Guide §11: interest is pleaded from a traceable computation, never a bare rate."""
        rate = _numeric(self.options.get("interest_rate"))
        if rate is None:
            self.warnings.append(
                "Interest annexures skipped: provide interest_rate (percent per annum) in the quantum agent run options."
            )
            return
        period_days = self._interest_period_days()
        if period_days is None:
            self.warnings.append(
                "Interest annexures skipped: provide interest_period_days or interest_from/interest_to dates in run options."
            )
            return
        for row in await self._matrix_rows("quantum-annexures"):
            calculation_type = str(row.get("calculation_type") or "")
            if calculation_type in {"interest", "claim_summary_rollup"}:
                continue
            principal = _numeric(row.get("amount"))
            if principal is None:
                continue
            principal_calc_id = str(row.get("calculation_id") or _id(row))
            reference = principal_calc_id[2:] if principal_calc_id.startswith("Q-") else principal_calc_id
            interest_row = {
                "calculation_id": f"Q-INTEREST-{_safe_key(reference)}",
                "calculation_type": "interest",
                "principal_calculation_id": principal_calc_id,
                "principal_amount": principal,
                "interest_rate_percent": rate,
                "interest_period_days": period_days,
                "interest_from": self.options.get("interest_from"),
                "interest_to": self.options.get("interest_to"),
                "formula": f"Simple interest: {principal} x {rate}% p.a. x {period_days}/365 days",
                "amount": compute_simple_interest(principal, rate, period_days),
                "currency": row.get("currency") or "INR",
                "source_records": [
                    {
                        "source_collection": "arbitration_quantum_annexures",
                        "source_id": _id(row),
                        "reference": principal_calc_id,
                    }
                ],
            }
            await self._insert_matrix_row(
                "quantum-annexures",
                interest_row,
                unique={"calculation_id": interest_row["calculation_id"]},
                agent_type=agent_type,
                source_id=_id(row),
                label=interest_row["calculation_id"],
            )

    def _interest_period_days(self) -> Optional[int]:
        period = _numeric(self.options.get("interest_period_days"))
        if period is not None and period > 0:
            return int(period)
        start = _parse_date(self.options.get("interest_from"))
        end = _parse_date(self.options.get("interest_to"))
        if start and end and end > start:
            return (end - start).days
        return None

    async def _claim_summary_rollup(self, agent_type: str) -> None:
        """Guide §11.2 claim summary: principal + interest + total per claim, with grand totals."""
        rows = await self._matrix_rows("quantum-annexures")
        principals = [
            row
            for row in rows
            if str(row.get("calculation_type") or "") not in {"interest", "claim_summary_rollup"}
            and _numeric(row.get("amount")) is not None
        ]
        if not principals:
            return
        interest_by_principal = {
            str(row.get("principal_calculation_id") or ""): _numeric(row.get("amount")) or 0.0
            for row in rows
            if str(row.get("calculation_type") or "") == "interest"
        }
        line_items: List[Dict[str, Any]] = []
        principal_total = 0.0
        interest_total = 0.0
        for row in principals:
            calc_id = str(row.get("calculation_id") or _id(row))
            principal = _numeric(row.get("amount")) or 0.0
            interest = interest_by_principal.get(calc_id, 0.0)
            line_items.append(
                {
                    "calculation_id": calc_id,
                    "description": row.get("cost_head") or row.get("calculation_type") or calc_id,
                    "principal": round(principal, 2),
                    "interest": round(interest, 2),
                    "total": round(principal + interest, 2),
                }
            )
            principal_total += principal
            interest_total += interest
        summary_fields = {
            "calculation_type": "claim_summary_rollup",
            "line_items": line_items,
            "principal_total": round(principal_total, 2),
            "interest_total": round(interest_total, 2),
            "amount": round(principal_total + interest_total, 2),
            "currency": principals[0].get("currency") or "INR",
            "formula": "Sum of principal annexures plus computed interest annexures (guide §11.2 claim summary).",
            "source_records": [
                {
                    "source_collection": "arbitration_quantum_annexures",
                    "source_id": _id(row),
                    "reference": row.get("calculation_id"),
                }
                for row in principals
            ],
        }
        collection = _collection(self.db, MATRIX_COLLECTIONS["quantum-annexures"])
        if collection is None:
            return
        existing = await collection.find_one(
            {"case_id": self.case_id, "calculation_id": "Q-CLAIM-SUMMARY", "deleted_at": {"$exists": False}}
        )
        if existing:
            # Agent-owned rollup: recompute totals on re-run without logging a new record.
            await collection.update_one(
                {"_id": existing["_id"], "case_id": self.case_id},
                {"$set": _jsonable({**summary_fields, "updated_at": datetime.utcnow()})},
            )
            return
        await self._insert_matrix_row(
            "quantum-annexures",
            {"calculation_id": "Q-CLAIM-SUMMARY", **summary_fields},
            unique={"calculation_id": "Q-CLAIM-SUMMARY"},
            agent_type=agent_type,
            source_id="Q-CLAIM-SUMMARY",
            label="Claim summary rollup",
        )

    async def _notice_compliance(self, agent_type: str) -> None:
        document_rows = await self._matrix_rows("document-index")
        notice_rows = [row for row in document_rows if _looks_like_notice(row)]
        if not notice_rows:
            self.warnings.append("Notice compliance agent found no notice-like document index rows.")
            return
        for document in notice_rows:
            notice_ref = str(document.get("source_id") or document.get("letter_no") or document.get("_id"))
            row = {
                "notice_ref": notice_ref,
                "notice_type": _notice_type(document),
                "notice_date": document.get("document_date") or document.get("date"),
                "notice_source_id": document.get("source_id"),
                "exhibit_id": document.get("exhibit_id"),
                "contractual_requirement": "Confirm notice clause, timing, service method, and condition precedent impact.",
                "compliance_status": "needs_review" if not self._auto_approve else "ready",
                "risk": "Review whether this notice satisfies the applicable contractual precondition.",
            }
            await self._insert_matrix_row(
                "notice-compliance",
                row,
                unique={"notice_ref": notice_ref},
                agent_type=agent_type,
                source_id=notice_ref,
                label=row["notice_type"],
            )

    async def _issue_framing(self, agent_type: str) -> None:
        claim_rows = await self._matrix_rows("claim-matrix")
        existing = await self._matrix_rows("issue-matrix")
        next_no = len(existing) + 1
        if claim_rows:
            for claim in claim_rows:
                claim_no = str(claim.get("claim_no") or claim.get("source_claim_id") or "")
                if not claim_no:
                    continue
                issue_key = f"claim:{claim_no}"
                issue = f"Whether the claimant is entitled to {claim.get('claim_head') or 'the claimed relief'}."
                category = _dispute_category(claim)
                template = DISPUTE_ISSUE_TEMPLATES.get(category, {})
                row = {
                    "issue_key": issue_key,
                    "issue_no": next_no,
                    "issue": issue,
                    "issue_type": _issue_type(claim),
                    "dispute_category": category,
                    "claimant_position": _condense(claim.get("facts") or claim.get("relief") or issue, 500),
                    "respondent_position": template.get("respondent_position")
                    or "To be developed from Statement of Defence or respondent records.",
                    "evidence_ids": _string_list(claim.get("evidence_ids")) + _string_list(claim.get("notice_ids")),
                    "clause_ids": _string_list(claim.get("clause_ids")),
                    "required_finding": template.get("required_finding") or _required_finding(claim),
                    "status": "ready" if self._auto_approve else "needs_review",
                }
                inserted = await self._insert_matrix_row(
                    "issue-matrix",
                    row,
                    unique={"issue_key": issue_key},
                    agent_type=agent_type,
                    source_id=claim.get("_id") or claim_no,
                    label=issue,
                )
                if inserted:
                    next_no += 1
            return

        issue_key = "case-core"
        summary = self.case.get("case_summary") or self.case.get("title") or "the dispute"
        row = {
            "issue_key": issue_key,
            "issue_no": next_no,
            "issue": f"What relief, if any, follows from {summary}?",
            "issue_type": "relief",
            "claimant_position": _condense(summary, 500),
            "respondent_position": "To be developed from respondent records.",
            "evidence_ids": [],
            "clause_ids": [],
            "required_finding": "Tribunal finding on entitlement, causation, quantum, and relief.",
            "status": "needs_review",
        }
        await self._insert_matrix_row(
            "issue-matrix",
            row,
            unique={"issue_key": issue_key},
            agent_type=agent_type,
            source_id=self.case_id,
            label=row["issue"],
        )

    async def _document_understanding(self, agent_type: str) -> None:
        await self._document_indexing(agent_type)
        self.warnings.append("Document understanding MVP indexed source documents; fact extraction remains a human-review step.")

    async def _jurisdiction(self, agent_type: str) -> None:
        if self.case.get("arbitration_clause"):
            await self._clause_interpretation("clause-interpretation")
            scope_row = {
                "check_type": "arbitration_clause_scope",
                "clause_source_id": self.case.get("arbitration_clause_source_id")
                or f"case:{self.case_id}:arbitration_clause",
                "claim_description": _condense(self.case.get("case_summary") or self.case.get("title"), 300),
                "scope_status": "needs_review",
                "notes": "Confirm every claim head falls within the arbitration agreement before drafting.",
            }
            await self._insert_matrix_row(
                "jurisdiction-matrix",
                scope_row,
                unique={"check_type": "arbitration_clause_scope"},
                agent_type=agent_type,
                source_id=str(scope_row["clause_source_id"]),
                label="Arbitration clause scope check",
            )
        else:
            self.warnings.append(
                "Jurisdiction agent found no arbitration clause text; record the clause and scope check manually."
            )
        await self._limitation_analysis(agent_type)
        await self._pre_arbitration_steps(agent_type)
        await self._pleading_timetable(agent_type)
        await self._amendment_rule(agent_type)

    async def _pleading_timetable(self, agent_type: str) -> None:
        """Guide §2: pleading timetable (SoC/SoD/counterclaim/rejoinder deadlines)."""
        timetable = self.options.get("pleading_timetable") or {}
        for stage in PLEADING_STAGES:
            due = _iso_date(_parse_date(timetable.get(stage)))
            row = {
                "check_type": "pleading_timetable",
                "pleading_stage": stage,
                "due_date": due,
                "filed_date": _iso_date(_parse_date((timetable.get(f"{stage}_filed") if isinstance(timetable, dict) else None))),
                "timetable_status": "filed"
                if timetable.get(f"{stage}_filed")
                else (_timetable_status(_parse_date(timetable.get(stage))) if due else "not_scheduled"),
                "notes": "Confirm the tribunal's procedural-order deadline for this pleading stage.",
            }
            await self._insert_matrix_row(
                "jurisdiction-matrix",
                row,
                unique={"check_type": "pleading_timetable", "pleading_stage": stage},
                agent_type=agent_type,
                source_id=f"timetable:{stage}",
                label=f"Pleading timetable: {stage}",
            )

    async def _amendment_rule(self, agent_type: str) -> None:
        """Guide §2: whether new claims / additional evidence require tribunal leave."""
        leave_required = self.options.get("amendment_leave_required")
        row = {
            "check_type": "amendment_rule",
            "leave_required": leave_required,
            "amendment_status": "needs_review" if leave_required is None else "recorded",
            "notes": (
                "Confirm from the arbitration rules / procedural order whether new claims or "
                "additional evidence require tribunal leave after the pleading is filed."
            ),
        }
        await self._insert_matrix_row(
            "jurisdiction-matrix",
            row,
            unique={"check_type": "amendment_rule"},
            agent_type=agent_type,
            source_id="amendment_rule",
            label="Amendment rule",
        )

    async def _limitation_analysis(self, agent_type: str) -> None:
        period_years = int(self.options.get("limitation_period_years") or DEFAULT_LIMITATION_PERIOD_YEARS)
        claims = await self._find_source_rows("claims", limit=int(self.options.get("claim_limit") or 50))
        subjects: List[Tuple[str, str, Dict[str, Any]]] = []
        for claim in claims:
            claim_id = _id(claim)
            if claim_id:
                subjects.append((claim_id, _first(claim, "title", "claim_ref", "description") or f"Claim {claim_id}", claim))
        if not subjects:
            subjects.append((self.case_id, "Case-level limitation review", {}))
        dated_rows = 0
        for subject_id, subject_label, record in subjects:
            cause_date = _parse_date(_first(record, "cause_of_action_date", "event_date") or self.options.get("cause_of_action_date"))
            rejection_date = _parse_date(_first(record, "rejection_date", "determination_date") or self.options.get("rejection_date"))
            final_bill_date = _parse_date(_first(record, "final_bill_date") or self.options.get("final_bill_date"))
            acknowledgements = [
                parsed
                for parsed in (
                    _parse_date(item)
                    for item in _string_list(_first(record, "acknowledgement_dates") or self.options.get("acknowledgement_dates"))
                )
                if parsed
            ]
            base = max((d for d in [cause_date, rejection_date, final_bill_date, *acknowledgements] if d), default=None)
            expiry = _add_years(base, period_years) if base else None
            if base:
                dated_rows += 1
            row = {
                "check_type": "limitation",
                "limitation_subject_id": subject_id,
                "subject": _condense(subject_label, 200),
                "cause_of_action_date": _iso_date(cause_date),
                "rejection_date": _iso_date(rejection_date),
                "final_bill_date": _iso_date(final_bill_date),
                "acknowledgement_dates": [_iso_date(item) for item in acknowledgements],
                "limitation_period_years": period_years,
                "limitation_base_date": _iso_date(base),
                "limitation_expiry_date": _iso_date(expiry),
                "limitation_status": _limitation_status(expiry),
                "basis": (
                    "Deterministic check from the latest of cause-of-action, rejection, final-bill, and "
                    "acknowledgement dates; confirm under the applicable limitation statute."
                ),
            }
            await self._insert_matrix_row(
                "jurisdiction-matrix",
                row,
                unique={"check_type": "limitation", "limitation_subject_id": subject_id},
                agent_type=agent_type,
                source_id=subject_id,
                label=f"Limitation: {row['subject']}",
            )
        if not dated_rows:
            self.warnings.append(
                "Limitation dates were not found in claim records or run options; complete the limitation rows manually "
                "(cause_of_action_date, rejection_date, final_bill_date, acknowledgement_dates)."
            )

    async def _pre_arbitration_steps(self, agent_type: str) -> None:
        steps = self.options.get("pre_arbitration_steps") or PRE_ARBITRATION_STEPS
        for entry in steps:
            if isinstance(entry, dict):
                step = str(entry.get("step") or "").strip()
                required = entry.get("required")
                compliance_status = str(entry.get("compliance_status") or "needs_review")
                completed_date = entry.get("completed_date")
            else:
                step = str(entry).strip()
                required = None
                compliance_status = "needs_review"
                completed_date = None
            if not step:
                continue
            row = {
                "check_type": "pre_arbitration_step",
                "step": step,
                "required": required,
                "compliance_status": compliance_status,
                "completed_date": completed_date,
                "contractual_requirement": (
                    "Confirm whether this step is a contractual precondition, its deadline, and its completion evidence."
                ),
            }
            await self._insert_matrix_row(
                "jurisdiction-matrix",
                row,
                unique={"check_type": "pre_arbitration_step", "step": step},
                agent_type=agent_type,
                source_id=f"prearb:{step}",
                label=f"Pre-arbitration step: {step}",
            )

    async def _delay_expert_alignment(self, agent_type: str) -> None:
        """Expert alignment checklist (guide §12): delay and quantum rows per claim.

        Deterministic seeding only — the expert/reviewer must resolve each row.
        Delay claims start with ``concurrency_addressed = False`` (flagged risk);
        quantum rows compare the pleaded amount against approved quantum
        annexures and record contradictions when they do not match.
        """
        claim_rows = await self._matrix_rows("claim-matrix")
        if not claim_rows:
            self.warnings.append("Delay/expert alignment requires claim matrix rows; run claim identification first.")
            return
        quantum_rows = await self._matrix_rows("quantum-annexures")
        expert_documents = await self._expert_report_documents()
        for claim in claim_rows:
            claim_no = str(claim.get("claim_no") or claim.get("source_claim_id") or _id(claim))
            if _is_delay_claim(claim):
                report_id = expert_documents.get("delay")
                risk_flags = ["concurrency_not_addressed"]
                if not report_id:
                    risk_flags.append("expert_report_missing")
                row = {
                    "expert_type": "delay",
                    "claim_no": claim_no,
                    "claim_row_id": _id(claim),
                    "methodology": None,
                    "concurrency_addressed": False,
                    "critical_path_confirmed": False,
                    "expert_report_source_id": report_id,
                    "contradictions": [],
                    "alignment_status": "needs_review",
                    "risk_flags": risk_flags,
                    "notes": "Confirm delay methodology, critical path impact, and concurrency treatment with the delay expert.",
                }
                inserted = await self._insert_matrix_row(
                    "expert-alignment",
                    row,
                    unique={"expert_type": "delay", "claim_no": claim_no},
                    agent_type=agent_type,
                    source_id=_id(claim) or claim_no,
                    label=f"Delay expert alignment: claim {claim_no}",
                )
                if inserted:
                    self.warnings.append(
                        f"Concurrency has not been addressed for delay/EOT claim {claim_no}; delay expert alignment is required."
                    )
            amount = _numeric(claim.get("amount") if claim.get("amount") is not None else claim.get("amount_or_days"))
            if amount is None:
                continue
            annexure = self._matching_quantum_annexure(claim, amount, quantum_rows)
            verified_amount = _numeric((annexure or {}).get("amount"))
            calculation_match = verified_amount is not None and abs(verified_amount - amount) < 0.01
            contradictions: List[str] = []
            risk_flags = []
            if annexure is None:
                contradictions.append(f"No quantum annexure found supporting pleaded amount {amount} for claim {claim_no}.")
                risk_flags.append("quantum_annexure_missing")
            elif not calculation_match:
                contradictions.append(
                    f"Pleaded amount {amount} does not match quantum annexure {annexure.get('calculation_id')} amount {verified_amount}."
                )
                risk_flags.append("amount_mismatch")
            row = {
                "expert_type": "quantum",
                "claim_no": claim_no,
                "claim_row_id": _id(claim),
                "pleaded_amount": amount,
                "verified_amount": verified_amount,
                "calculation_id": (annexure or {}).get("calculation_id"),
                "calculation_match": calculation_match,
                "expert_report_source_id": expert_documents.get("quantum"),
                "contradictions": contradictions,
                "alignment_status": "needs_review",
                "risk_flags": risk_flags,
                "notes": "Confirm the pleaded amount, calculation basis, and source records with the quantum expert.",
            }
            inserted = await self._insert_matrix_row(
                "expert-alignment",
                row,
                unique={"expert_type": "quantum", "claim_no": claim_no},
                agent_type=agent_type,
                source_id=_id(claim) or claim_no,
                label=f"Quantum expert alignment: claim {claim_no}",
            )
            if inserted and contradictions:
                self.warnings.extend(contradictions)

    async def _expert_report_documents(self) -> Dict[str, str]:
        """Map expert type -> document-index source id for expert-looking documents."""
        mapping: Dict[str, str] = {}
        for row in await self._matrix_rows("document-index"):
            text = " ".join(str(row.get(key) or "") for key in ["title", "document_type", "relevance_note"]).lower()
            source_id = str(row.get("source_id") or "")
            if not source_id:
                continue
            if "delay" in text and ("expert" in text or "analysis" in text or "report" in text):
                mapping.setdefault("delay", source_id)
            if "quantum" in text and ("expert" in text or "report" in text):
                mapping.setdefault("quantum", source_id)
        return mapping

    @staticmethod
    def _matching_quantum_annexure(
        claim: Dict[str, Any],
        amount: float,
        quantum_rows: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        calculation_id = str(claim.get("calculation_id") or "")
        if calculation_id:
            for row in quantum_rows:
                if str(row.get("calculation_id") or "") == calculation_id:
                    return row
        for row in quantum_rows:
            row_amount = _numeric(row.get("amount"))
            if row_amount is not None and abs(row_amount - amount) < 0.01:
                return row
        return None

    async def _review_consistency(self, agent_type: str) -> None:
        """Guide §5.2 red-flag review, computed deterministically from the matrices.

        Annotates claim-matrix rows with ``red_flags`` and reports them as agent
        warnings. It never changes approval/readiness statuses — red flags are
        review guidance, not automatic blockers. "Wrong party named" is not
        deterministically checkable and stays a human review item.
        """
        claim_rows = await self._matrix_rows("claim-matrix")
        quantum_rows = await self._matrix_rows("quantum-annexures")
        chronology_rows = await self._matrix_rows("chronology-matrix")
        document_rows = await self._matrix_rows("document-index")
        jurisdiction_rows = await self._matrix_rows("jurisdiction-matrix")

        case_flags: List[str] = []
        for row in document_rows:
            text = " ".join(str(row.get(key) or "") for key in ["title", "document_type", "relevance_note"]).lower()
            if any(term in text for term in ["final bill", "no dues", "no-dues", "no claim certificate", "full and final"]):
                case_flags.append("final_bill_or_no_dues_waiver_risk")
                break
        if any(
            str(row.get("scope_status") or "").lower() == "out_of_scope"
            for row in jurisdiction_rows
            if str(row.get("check_type") or "") == "arbitration_clause_scope"
        ):
            case_flags.append("claim_outside_arbitration_clause")
        if claim_rows and not chronology_rows:
            case_flags.append("no_contemporaneous_chronology")

        if not claim_rows:
            self.warnings.append("Red-flag review found no claim matrix rows; run claim identification first.")
            if case_flags:
                self.warnings.append(f"Case-level red flags: {', '.join(sorted(set(case_flags)))}.")
            return

        collection = _collection(self.db, MATRIX_COLLECTIONS["claim-matrix"])
        for claim in claim_rows:
            claim_no = str(claim.get("claim_no") or _id(claim))
            claim_text = " ".join(
                str(claim.get(key) or "") for key in ["claim_head", "facts", "causation", "relief"]
            ).lower()
            flags: List[str] = []
            if not _string_list(claim.get("notice_ids")):
                flags.append("no_claim_notice")
            amount = _numeric(claim.get("amount") if claim.get("amount") is not None else claim.get("amount_or_days"))
            annexure = self._matching_quantum_annexure(claim, amount, quantum_rows) if amount is not None else None
            if amount is not None and annexure is None:
                flags.append("no_cost_records")
            if _is_delay_claim(claim):
                has_critical_path = bool(
                    (annexure or {}).get("critical_path_days") or (annexure or {}).get("delay_event_ids")
                )
                if not has_critical_path:
                    flags.append("no_critical_path_impact")
            if any(term in claim_text for term in ["variation", "extra work", "extra item"]) and not _string_list(
                claim.get("evidence_ids")
            ):
                flags.append("no_written_instruction_for_variation")
            all_flags = sorted(set(flags + case_flags))
            if not all_flags:
                continue
            if collection is not None:
                await collection.update_one(
                    {"_id": claim.get("_id"), "case_id": self.case_id},
                    {"$set": {"red_flags": all_flags, "updated_at": datetime.utcnow()}},
                )
            self.warnings.append(f"Red flags for claim {claim_no}: {', '.join(all_flags)}.")

    async def _defence_analysis(self, agent_type: str) -> None:
        # Deterministic mode has no safe heuristic for admissions/denials; named
        # separately so the LLM agent subclass can supply a real implementation.
        await self._review_only_agent(agent_type)

    async def _rejoinder_reply(self, agent_type: str) -> None:
        # Deterministic mode has no safe heuristic for reply drafting; named
        # separately so the LLM agent subclass can supply a real implementation.
        await self._review_only_agent(agent_type)

    async def _review_only_agent(self, agent_type: str) -> None:
        self.warnings.append(f"{agent_type} is registered for the workflow but needs pleading-specific source import before row generation.")

    async def _clause_source_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        rows.extend(await self._find_source_rows("contract_clauses", limit=int(self.options.get("clause_limit") or 50)))
        vector_rows = await self._find_source_rows("document_vectors", limit=int(self.options.get("clause_vector_limit") or 100))
        for row in vector_rows:
            text = str(_first(row, "text", "content", "chunk_text", "excerpt") or "")
            if _first(row, "clause_number", "clause", "section_number") or re.search(r"\bclause\b|\bgcc\b|\bscc\b", text, flags=re.I):
                rows.append(row)
        return _dedupe_by_id(rows)

    async def _find_source_rows(self, collection_name: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        collection = _collection(self.db, collection_name)
        if collection is None:
            return []
        query = _scope_query(self.case, include_contract=False)
        try:
            cursor = collection.find(query)
            if hasattr(cursor, "sort"):
                cursor = cursor.sort("updated_at", -1)
            if hasattr(cursor, "limit"):
                cursor = cursor.limit(limit)
            rows = await _collect(cursor)
        except Exception as exc:
            self.warnings.append(f"Unable to read {collection_name}: {exc}")
            return []
        return [row for row in rows if _in_case_scope(row, self.case)]

    async def _matrix_rows(self, matrix_slug: str) -> List[Dict[str, Any]]:
        collection_name = MATRIX_COLLECTIONS[matrix_slug]
        collection = _collection(self.db, collection_name)
        if collection is None:
            return []
        cursor = collection.find({"case_id": self.case_id, "deleted_at": {"$exists": False}})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("created_at", 1)
        return await _collect(cursor)

    async def _paragraph_responses(self) -> List[Dict[str, Any]]:
        if not self.draft_id:
            return []
        collection = _collection(self.db, "arbitration_paragraph_responses")
        if collection is None:
            return []
        cursor = collection.find({"draft_id": self.draft_id})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("source_paragraph_number", 1)
        return await _collect(cursor)

    async def _insert_matrix_row(
        self,
        matrix_slug: str,
        body: Dict[str, Any],
        *,
        unique: Dict[str, Any],
        agent_type: str,
        source_id: Optional[str],
        label: str,
    ) -> bool:
        collection_name = MATRIX_COLLECTIONS[matrix_slug]
        collection = _collection(self.db, collection_name)
        if collection is None:
            self.errors.append(f"Matrix collection {collection_name} is not available.")
            return False
        query = {"case_id": self.case_id, "deleted_at": {"$exists": False}, **unique}
        existing = await collection.find_one(query)
        if existing:
            if source_id:
                self.source_ids.add(str(source_id))
            return False

        now = datetime.utcnow()
        row_body = {
            **body,
            **self._status_defaults(matrix_slug),
            "case_id": self.case_id,
            "draft_id": self.draft_id,
            "organization_id": self.case.get("organization_id"),
            "project_id": self.case.get("project_id"),
            "contract_id": self.case.get("contract_id"),
            "generated_by_agent": agent_type,
            "created_by": _actor_id(self.current_user),
            "created_at": now,
            "updated_by": _actor_id(self.current_user),
            "updated_at": now,
        }
        row = ArbitrationMatrixRow(**row_body).model_dump(by_alias=True)
        if matrix_slug == "document-index":
            row = await self._assign_exhibit(row)
        await collection.insert_one(_jsonable(row))
        if source_id:
            self.source_ids.add(str(source_id))
        self.created_records.append(
            {
                "agent_type": agent_type,
                "matrix": matrix_slug,
                "collection": collection_name,
                "row_id": row.get("_id"),
                "label": label,
                "source_id": source_id,
            }
        )
        return True

    def _status_defaults(self, matrix_slug: str) -> Dict[str, Any]:
        status = "approved" if self._auto_approve else "needs_review"
        readiness = "ready" if self._auto_approve else "needs_review"
        defaults = {
            "verification_status": "verified" if self._auto_approve else "needs_review",
            "approval_status": status,
            "readiness_status": readiness,
            "human_approval_status": status,
        }
        if matrix_slug == "issue-matrix":
            defaults["status"] = readiness
        return defaults

    @property
    def _auto_approve(self) -> bool:
        return str(self.options.get("auto_approve") or "").lower() in {"1", "true", "yes", "approved"}

    async def _assign_exhibit(self, row: Dict[str, Any]) -> Dict[str, Any]:
        prefix = str(row.get("exhibit_prefix") or _exhibit_prefix(self.case, self.options)).strip().upper()
        if row.get("exhibit_id") and row.get("exhibit_number"):
            return row
        existing = await _collect(
            _collection(self.db, "arbitration_document_index").find({"case_id": self.case_id, "exhibit_prefix": prefix})
        )
        used = [int(item.get("exhibit_number") or 0) for item in existing if str(item.get("exhibit_number") or "").isdigit()]
        row["exhibit_prefix"] = prefix
        row["exhibit_number"] = (max(used) if used else 0) + 1
        row["exhibit_id"] = f"{prefix}-{row['exhibit_number']}"
        return row

    def _result(self, agent_type: str) -> Dict[str, Any]:
        created = len(self.created_records)
        matrix_names = sorted({record["matrix"] for record in self.created_records})
        if created:
            summary = f"{agent_type} created {created} matrix row(s): {', '.join(matrix_names)}."
        else:
            summary = f"{agent_type} completed without creating new matrix rows."
        return {
            "agent_type": agent_type,
            "created_records": self.created_records,
            "warnings": self.warnings,
            "errors": self.errors,
            "source_ids": sorted(self.source_ids),
            "output_summary": summary,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL_NAME,
        }


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except Exception:
        return getattr(db, name, None)


def _scope_query(case: Dict[str, Any], *, include_contract: bool = False) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if case.get("organization_id"):
        query["organization_id"] = case.get("organization_id")
    if case.get("project_id"):
        query["project_id"] = case.get("project_id")
    if include_contract and case.get("contract_id"):
        query["contract_id"] = case.get("contract_id")
    return query


def _in_case_scope(row: Dict[str, Any], case: Dict[str, Any]) -> bool:
    for key in ["organization_id", "project_id"]:
        if row.get(key) and case.get(key) and str(row.get(key)) != str(case.get(key)):
            return False
    contract_id = case.get("contract_id")
    row_contract = row.get("contract_id") or row.get("contract_upload_id")
    if contract_id and row_contract and str(row_contract) != str(contract_id):
        return False
    return True


def _normalize_agent_type(agent_type: str) -> str:
    raw = str(agent_type or "").strip().lower().replace("_", "-")
    aliases = {
        "case-manager": "orchestrator",
        "document-collection": "document-indexing",
        "document-index": "document-indexing",
        "chronology": "chronology-builder-adapter",
        "clause": "clause-interpretation",
        "issue": "issue-framing",
        "claims": "claim-identification",
        "claim": "claim-identification",
        "quantum-calculation": "quantum",
        "notice": "notice-compliance",
        "legal": "legal-guardrail",
        "review": "review-consistency",
    }
    return aliases.get(raw, raw or "orchestrator")


def _actor_id(user: Any) -> Optional[str]:
    if isinstance(user, dict):
        return user.get("id") or user.get("email") or user.get("_id")
    return getattr(user, "id", None) or getattr(user, "email", None)


def _id(row: Dict[str, Any]) -> Optional[str]:
    value = row.get("_id") or row.get("id")
    return str(value) if value not in (None, "") else None


def _first(row: Dict[str, Any], *keys: str) -> Any:
    containers = [row]
    for nested_key in ["metadata", "document_metadata", "letter_metadata", "extraction", "ai_metadata"]:
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value not in (None, "", []):
                return value
    return None


def _string_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _condense(value: Any, width: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= width:
        return text
    return shorten(text, width=width, placeholder="...")


def compute_simple_interest(principal: float, rate_percent: float, days: int) -> float:
    """Simple interest per guide §11: principal x rate% p.a. x days/365."""
    return round(principal * (rate_percent / 100.0) * (days / 365.0), 2)


def _cost_head(source: Dict[str, Any], collection_name: str) -> str:
    if collection_name == "variations":
        return "variation_works"
    if collection_name == "ipc_bills":
        return "certified_payment"
    text = " ".join(
        str(_first(source, key) or "") for key in ["title", "claim_head", "description", "type", "summary"]
    ).lower()
    if any(term in text for term in ["idle", "plant standby", "machinery standby"]):
        return "idle_resources"
    if any(term in text for term in ["disruption", "productivity"]):
        return "disruption_productivity"
    if any(term in text for term in ["ld", "liquidated damages", "deduction", "recovery"]):
        return "ld_refund"
    if any(term in text for term in ["prolongation", "overhead", "eot", "delay", "extension of time"]):
        return "prolongation_overheads"
    if any(term in text for term in ["variation", "extra work", "extra item"]):
        return "variation_works"
    if any(term in text for term in ["payment", "ipc", "bill", "certified"]):
        return "certified_payment"
    return "claim_amount"


def _is_delay_claim(claim: Dict[str, Any]) -> bool:
    text = " ".join(
        str(claim.get(key) or "") for key in ["claim_head", "facts", "causation", "relief"]
    ).lower()
    return any(term in text for term in ["delay", "eot", "extension of time", "prolongation", "critical path"])


def _parse_date(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value in (None, "", []):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue
    return None


def _iso_date(value: Optional[datetime]) -> Optional[str]:
    return value.date().isoformat() if value else None


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29 February
        return value.replace(year=value.year + years, day=28)


def _limitation_status(expiry: Optional[datetime], *, at_risk_days: int = 90) -> str:
    if not expiry:
        return "needs_review"
    now = datetime.utcnow()
    if expiry < now:
        return "time_barred"
    if expiry <= now + timedelta(days=at_risk_days):
        return "at_risk"
    return "within_limitation"


def _timetable_status(due: Optional[datetime], *, due_soon_days: int = 7) -> str:
    if not due:
        return "not_scheduled"
    now = datetime.utcnow()
    if due < now:
        return "overdue"
    if due <= now + timedelta(days=due_soon_days):
        return "due_soon"
    return "scheduled"


def _numeric(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _document_title(document: Dict[str, Any]) -> str:
    return (
        _first(document, "subject", "title", "filename", "file_name", "document_name")
        or _first(document, "letter_no", "reference_no")
        or f"Document {_id(document) or ''}".strip()
    )


def _document_type(document: Dict[str, Any]) -> str:
    explicit = _first(document, "document_type", "type", "category")
    if explicit:
        return str(explicit)
    text = " ".join(
        str(value or "")
        for value in [
            _document_title(document),
            _first(document, "subject", "summary", "description", "extracted_text"),
        ]
    ).lower()
    if "notice" in text:
        return "notice"
    if "claim" in text or "eot" in text:
        return "claim"
    if "variation" in text:
        return "variation"
    if "payment" in text or "ipc" in text or "ra bill" in text:
        return "payment"
    if "drawing" in text:
        return "drawing"
    return "letter"


def _document_risk_flags(document: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    if not _first(document, "source_file_link", "file_url", "storage_path", "file_path"):
        flags.append("source_file_link_needs_confirmation")
    if not _first(document, "document_date", "letter_date", "date"):
        flags.append("date_missing")
    if not _first(document, "letter_no", "letterNo", "reference_no", "document_number"):
        flags.append("letter_number_missing")
    return flags


def _exhibit_prefix(case: Dict[str, Any], options: Dict[str, Any]) -> str:
    if options.get("exhibit_prefix"):
        return str(options["exhibit_prefix"]).strip().upper()
    perspective = str(case.get("party_perspective") or "").lower()
    return "R" if perspective == "respondent" else "C"


def _verified(row: Dict[str, Any]) -> bool:
    statuses = _status_set(row)
    return bool(statuses & VERIFIED_STATUSES)


def _reviewable(row: Dict[str, Any]) -> bool:
    statuses = _status_set(row)
    return not statuses or bool(statuses & REVIEW_STATUSES)


def _status_set(row: Dict[str, Any]) -> set[str]:
    return {
        str(row.get(key) or "").strip().lower()
        for key in ["verification_status", "approval_status", "human_approval_status", "readiness_status", "status"]
        if row.get(key) is not None
    }


def _dedupe_by_id(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for row in rows:
        key = str(_id(row) or _first(row, "clause_number", "text", "content") or len(output))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _topic_from_clause(excerpt: str) -> str:
    lowered = excerpt.lower()
    if "site" in lowered or "access" in lowered:
        return "Site access"
    if "payment" in lowered or "certif" in lowered:
        return "Payment"
    if "variation" in lowered:
        return "Variation"
    if "extension of time" in lowered or "eot" in lowered or "delay" in lowered:
        return "Extension of time"
    if "arbitration" in lowered or "dispute" in lowered:
        return "Dispute resolution"
    return "Contract entitlement"


def _relief_from_amount(amount: Any) -> Optional[str]:
    numeric = _numeric(amount)
    if numeric is None:
        return None
    return f"Award payment of the supported amount ({amount})."


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return cleaned or "SOURCE"


def _looks_like_notice(row: Dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ["title", "document_type", "relevance_note", "letter_no", "subject"]
    ).lower()
    return any(term in text for term in ["notice", "claim", "eot", "extension of time", "hindrance", "delay"])


def _notice_type(row: Dict[str, Any]) -> str:
    text = " ".join(str(row.get(key) or "") for key in ["title", "document_type", "relevance_note"]).lower()
    if "eot" in text or "extension of time" in text:
        return "EOT notice"
    if "claim" in text:
        return "Claim notice"
    if "delay" in text or "hindrance" in text:
        return "Delay notice"
    return "Contractual notice"


def _issue_type(claim: Dict[str, Any]) -> str:
    text = " ".join(str(claim.get(key) or "") for key in ["claim_head", "facts", "relief"]).lower()
    if "payment" in text or "ipc" in text or "bill" in text:
        return "quantum"
    if "delay" in text or "eot" in text or "extension of time" in text:
        return "causation"
    if "variation" in text:
        return "entitlement"
    return "relief"


def _required_finding(claim: Dict[str, Any]) -> str:
    parts = ["entitlement"]
    if claim.get("causation"):
        parts.append("causation")
    if claim.get("amount") is not None or claim.get("amount_or_days") is not None:
        parts.append("quantum")
    return "Tribunal finding on " + ", ".join(parts) + "."
