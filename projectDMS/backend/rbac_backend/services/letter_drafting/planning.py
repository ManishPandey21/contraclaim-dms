from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ...models.letter import Letter
from ...models.letter_drafting import (
    DraftContextBundle,
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
    PlanningSheet,
    ReplyMatrixRow,
    ReplyPointOrigin,
    SourceEvidence,
    SourceIntegritySummary,
)
from .context import condense_text

#: How each reply-matrix row is framed in the plan handed to the drafter. An
#: AI reply consideration must never read as something the letter states.
_POINT_LABELS: Dict[str, str] = {
    "incoming_letter": "Incoming point",
    "ai_reply_consideration": (
        "AI-derived reply consideration (advisory; not stated in the incoming letter)"
    ),
    "user_direction": "User-directed point",
}


class PlanningSheetBuilder:
    """Builds deterministic planning artifacts before the LLM draft stage."""

    def build(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        role: str,
        context: DraftContextBundle,
        sources: List[SourceEvidence],
        analysis: Optional[IncomingLetterAnalysis],
    ) -> Tuple[PlanningSheet, List[ReplyMatrixRow], SourceIntegritySummary, str]:
        missing_inputs = [
            key.replace("_", " ")
            for key, present in context.threshold_inputs.items()
            if not present
        ]
        clause_sources = [source for source in sources if source.source_type == "contract_clause"]
        prior_sources = [source for source in sources if source.source_type == "prior_correspondence"]
        fact_sources = [
            source
            for source in sources
            if source.allowed_use in {"fact", "comment"} and (source.text or source.snippet)
        ]
        contractual_basis = [
            self._source_label(source)
            for source in clause_sources[:8]
        ] or list(request.clauses_to_consider)
        factual_basis = [
            condense_text(source.text or source.snippet, 260) or source.label
            for source in fact_sources[:8]
        ]
        previous_correspondence = [
            self._source_label(source)
            for source in prior_sources[:5]
        ]
        planning_sheet = PlanningSheet(
            draft_type=request.draft_type,
            letter_category=request.letter_category,
            letter_purpose=request.purpose or request.requirements or getattr(letter, "subject", None),
            subject=request.subject or getattr(letter, "subject", None),
            recipient=request.recipient or getattr(letter, "recipient", None),
            sender_role=role,  # type: ignore[arg-type]
            trigger_event=request.trigger_event or (analysis.main_request if analysis else None),
            contractual_basis=contractual_basis,
            factual_basis=factual_basis,
            previous_correspondence=previous_correspondence,
            issue_type=analysis.issue_type if analysis else (request.issue_type or "other"),
            response_deadline=(
                analysis.response_deadline if analysis else request.response_deadline
            ),
            deadline_risk=analysis.deadline_risk if analysis else None,
            cited_clause_evaluations=analysis.cited_clause_evaluations if analysis else [],
            recommended_position=request.desired_position,
            required_action=request.required_action,
            timeline=f"{request.timeline_days} days" if request.timeline_days is not None else None,
            tone=request.tone,
            risk_level=self._risk_level(request, analysis),
            rights_reservation_required=role == "contractor",
            missing_inputs=missing_inputs,
        )
        reply_matrix = self._reply_matrix(request, sources, analysis)
        source_summary = SourceIntegritySummary(
            documents_relied_upon=[
                source.label
                for source in sources
                if source.source_type == "context_document"
            ][:12],
            clauses_relied_upon=contractual_basis[:12],
            user_provided_facts=context.current_materials[:8],
            prior_correspondence_used=previous_correspondence,
            placeholders_requiring_confirmation=self._placeholders(planning_sheet),
            unsupported_points_excluded=[
                row.incoming_point
                for row in reply_matrix
                if row.status == "unsupported"
            ],
            warnings=(
                [
                    "Incoming letter metadata was only partially extracted; "
                    "verify its letter number, date, parties and references before relying on them."
                ]
                if analysis is not None
                and analysis.source_metadata_quality == "partial_extraction"
                else []
            ),
        )
        return planning_sheet, reply_matrix, source_summary, self._plan_text(planning_sheet, reply_matrix)

    def _reply_matrix(
        self,
        request: DraftRunCreateRequest,
        sources: List[SourceEvidence],
        analysis: Optional[IncomingLetterAnalysis],
    ) -> List[ReplyMatrixRow]:
        points: List[Tuple[str, ReplyPointOrigin]] = []
        # Key Reply Points are AI advice generated at extraction time, not
        # words of the incoming letter. They still lead the agenda, but as
        # advisory rows: no borrowed evidence, always needing confirmation.
        if analysis:
            for point in analysis.key_reply_points or []:
                cleaned = str(point).strip(" -\t")
                if cleaned:
                    points.append((cleaned, "ai_reply_consideration"))
        if analysis and analysis.main_request:
            points.append((analysis.main_request, "incoming_letter"))
        for line in (request.points or "").splitlines():
            line = line.strip(" -\t")
            if line:
                points.append((line, "user_direction"))
        if request.required_action:
            points.append((request.required_action, "user_direction"))

        source_ids = [source.source_id for source in sources if source.allowed_use in {"fact", "clause"}][:5]
        clause_refs = [
            source.clause_number
            for source in sources
            if source.source_type == "contract_clause" and source.clause_number
        ][:5]
        origins: Dict[str, ReplyPointOrigin] = {}
        for point, origin in points:
            # A point the letter or the user states is never demoted to
            # advisory because the AI suggested the same words.
            if origins.get(point, "ai_reply_consideration") == "ai_reply_consideration":
                origins[point] = origin
        rows: List[ReplyMatrixRow] = []
        for point, origin in list(origins.items())[:10]:
            advisory = origin == "ai_reply_consideration"
            # The retrieved sources were never matched to an AI suggestion;
            # stamping them on it would dress advice up as evidenced fact.
            row_source_ids = [] if advisory else source_ids
            rows.append(
                ReplyMatrixRow(
                    incoming_point=condense_text(point, 500) or point,
                    proposed_reply=request.desired_position or "[CONFIRM: response position for this incoming point]",
                    source_ids=row_source_ids,
                    clause_refs=[] if advisory else [ref for ref in clause_refs if ref],
                    risk_note=(
                        "AI-derived reply consideration; not stated in the incoming letter"
                        if advisory
                        else None
                    ),
                    status="supported" if row_source_ids else "needs_confirmation",
                    point_origin=origin,
                )
            )
        return rows

    @staticmethod
    def _risk_level(
        request: DraftRunCreateRequest,
        analysis: Optional[IncomingLetterAnalysis],
    ) -> str:
        if request.letter_category in {"claim_reply", "eot_reply", "dispute"}:
            return "high"
        if analysis and analysis.contractual_risk in {"high", "medium", "low"}:
            return analysis.contractual_risk
        return "medium"

    @staticmethod
    def _source_label(source: SourceEvidence) -> str:
        if source.clause_number:
            return f"Clause {source.clause_number}: {source.label}"
        if source.metadata.get("letter_no"):
            return f"{source.metadata.get('letter_no')}: {source.label}"
        return source.label

    @staticmethod
    def _placeholders(sheet: PlanningSheet) -> List[str]:
        placeholders: List[str] = []
        if not sheet.contractual_basis:
            placeholders.append("[CONFIRM: relevant clause number and key wording]")
        if not sheet.trigger_event:
            placeholders.append("[CONFIRM: exact date of the notified event]")
        if not sheet.factual_basis:
            placeholders.append("[TO BE INSERTED BY USER: supporting evidence or impact quantification]")
        return placeholders

    @staticmethod
    def _plan_text(sheet: PlanningSheet, rows: List[ReplyMatrixRow]) -> str:
        matrix_text = "\n".join(
            f"- {_POINT_LABELS.get(row.point_origin or '', 'Incoming point')}: {row.incoming_point}"
            f"\n  Proposed reply: {row.proposed_reply}"
            for row in rows[:8]
        ) or "- No reply matrix points available."
        return "\n".join(
            [
                "1. Drafting posture",
                f"- Profile: {sheet.sender_role}",
                f"- Tone: {sheet.tone}",
                f"- Risk level: {sheet.risk_level}",
                f"- Issue type: {sheet.issue_type}",
                f"- Response deadline: {sheet.response_deadline or 'Not identified'}",
                "2. Factual basis",
                "\n".join(f"- {item}" for item in sheet.factual_basis) or "- [TO BE INSERTED BY USER: supporting evidence or impact quantification]",
                "3. Contractual basis",
                "\n".join(f"- {item}" for item in sheet.contractual_basis) or "- [CONFIRM: relevant clause number and key wording]",
                "4. Cited clause checks",
                "\n".join(
                    f"- Clause {item.clause_number}: exists={item.exists_in_contract}, applicable={item.applicable_to_issue}, effect={item.effect_on_sender_position}"
                    for item in sheet.cited_clause_evaluations[:8]
                )
                or "- No cited clauses identified for verification.",
                "5. Response matrix",
                matrix_text,
                "6. Required outcome",
                f"- {sheet.required_action or sheet.recommended_position or '[CONFIRM: requested action or position]'}",
            ]
        )
