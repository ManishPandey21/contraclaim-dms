"""LLM-backed arbitration matrix agent (ARB-101).

Runs behind the same ``run_arbitration_agent`` contract as the deterministic
agent and reuses all of its persistence machinery (tenant scoping, matrix
idempotency, exhibit assignment, agent-run result shape). The LLM only supplies
*analysis*; grounding guardrails are enforced in code:

- Prompts contain ONLY scoped source rows, each keyed by a stable source id.
- Parsed rows are rejected when they cite a source id that was not in the prompt.
- Generated rows are ALWAYS persisted as ``needs_review`` — ``auto_approve`` is
  ignored in LLM mode so model output can never bypass human approval.
- Amounts, currencies, dates, clause excerpts, and document metadata are copied
  from the source records, never from model output.

LLM-driven handlers: clause-interpretation, claim-identification, issue-framing,
defence-analysis, document-understanding.

Deliberately still deterministic (inherited): document-indexing (mechanical
metadata mapping), chronology-builder-adapter (verified events are copied, not
interpreted), quantum (no amount without a traceable register source), and
notice-compliance.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from ....config.document_processing_config import DocumentProcessingConfig
from ....retrieval.generator import LLMGenerator
from .deterministic import (
    DeterministicArbitrationAgent,
    _collection,
    _condense,
    _document_title,
    _first,
    _id,
    _numeric,
    _string_list,
    _verified,
)

logger = logging.getLogger(__name__)

LLM_PROMPT_VERSION = "arbitration-agents-llm-1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

_ISSUE_TYPES = {
    "jurisdiction",
    "limitation",
    "entitlement",
    "breach",
    "causation",
    "quantum",
    "interest",
    "costs",
    "counterclaim",
    "setoff",
    "relief",
}

_ADMISSION_VALUES = {"admitted", "partly_admitted", "denied", "not_admitted"}

CLAUSE_INSTRUCTION = """Task: build clause matrix rows for arbitration preparation.
For each source clause relevant to a construction dispute (site access, programme, EOT,
variation, payment, LD, notices, dispute resolution), return one object with:
- "source_id": the id of the clause source, copied exactly
- "topic": short topic label
- "obligation_or_right": one sentence stating the obligation or right the clause creates
- "claimant_use": how a claimant could rely on this clause, or null
- "respondent_use": how a respondent could rely on this clause, or null
- "risk": the main interpretation or evidentiary risk, or null"""

CLAIM_INSTRUCTION = """Task: build claim matrix rows from the claim register sources.
For each source claim, return one object with:
- "source_id": the id of the claim source, copied exactly
- "claim_head": short claim head title
- "facts": the factual basis, using only facts stated in the source
- "causation": the causal link between event and time/cost impact, or null if the source does not state one
- "weakness": the main evidentiary weakness a tribunal may see, or null
- "relief": the relief that should be requested, or null
Never state amounts, days, or dates that are not in the source text."""

ISSUE_INSTRUCTION = """Task: frame tribunal-facing issues from the claim matrix sources.
For each source claim row, return one object with:
- "source_id": the id of the claim matrix source, copied exactly
- "issue": a neutral "Whether ..." question for the tribunal
- "issue_type": one of jurisdiction, limitation, entitlement, breach, causation, quantum, interest, costs, counterclaim, setoff, relief
- "claimant_position": the claimant position grounded in the source, or null
- "respondent_position": the likely respondent position, or "To be developed from respondent records." if not evidenced
- "required_finding": what the tribunal must decide"""

DEFENCE_INSTRUCTION = """Task: build defence matrix rows responding to the claim matrix sources.
For each source claim row, return one object with:
- "source_id": the id of the claim matrix source, copied exactly
- "admission_denial": one of admitted, partly_admitted, denied, not_admitted
- "defence": the reasoned defence; a denial without a stated reason is not acceptable
- "quantum_objection": the objection to the amount/days, or null
- "positive_case": the respondent's own version of events if supported by the source, or null
Base every point only on the source text. Do not invent facts."""

REJOINDER_INSTRUCTION = """Task: build rejoinder matrix rows replying to imported Statement of Defence paragraphs.
For each SoD paragraph source, return one object with:
- "source_id": the SoD paragraph number, copied exactly
- "nature_of_defence": short classification (e.g. denial, no-notice, concurrency, within-scope, quantum objection)
- "claimant_reply": the claimant's reply to that defence, grounded only in the paragraph text; a reply must give a reason, never a bare denial
- "new_matter": true only if the reply raises a claim or ground not already in the Statement of Claim, otherwise false
- "reply_to_counterclaim": the reply if the paragraph is a counterclaim, otherwise null
A rejoinder must not become a second Statement of Claim: prefer new_matter=false and only set true when the reply genuinely introduces fresh matter."""

DOCUMENT_INSTRUCTION = """Task: classify project documents for the arbitration document index.
For each source document, return one object with:
- "source_id": the id of the document source, copied exactly
- "document_type": one of letter, notice, claim, variation, payment, drawing, mom, report, programme, other
- "relevance_note": one or two sentences on why the document matters to the dispute
- "issue_tags": array of short issue labels (e.g. "EOT", "site access", "payment"), or []
- "claim_tags": array of claim heads the document supports, or []"""


def resolve_llm_model(options: Optional[Dict[str, Any]] = None) -> str:
    return str(
        (options or {}).get("agent_model")
        or os.getenv("ARBITRATION_AGENT_MODEL")
        or DEFAULT_LLM_MODEL
    )


class LLMOutputError(RuntimeError):
    """Raised when the model response cannot be parsed into grounded JSON rows."""


class LLMArbitrationAgent(DeterministicArbitrationAgent):
    def __init__(
        self,
        *,
        db: Any,
        case: Dict[str, Any],
        draft_id: Optional[str],
        options: Dict[str, Any],
        current_user: Any,
        generator: Optional[LLMGenerator] = None,
    ) -> None:
        super().__init__(db=db, case=case, draft_id=draft_id, options=options, current_user=current_user)
        self.model_name = resolve_llm_model(options)
        self.generator = generator or LLMGenerator(DocumentProcessingConfig(openai_model=self.model_name))
        if str(options.get("auto_approve") or "").lower() in {"1", "true", "yes", "approved"}:
            self.warnings.append("auto_approve is ignored in LLM agent mode; generated rows require human review.")

    @property
    def available(self) -> bool:
        return bool(getattr(self.generator, "available", False))

    # --- guardrail overrides -------------------------------------------------

    def _status_defaults(self, matrix_slug: str) -> Dict[str, Any]:
        # LLM output can never be auto-approved, regardless of run options.
        defaults = {
            "verification_status": "needs_review",
            "approval_status": "needs_review",
            "readiness_status": "needs_review",
            "human_approval_status": "needs_review",
        }
        if matrix_slug == "issue-matrix":
            defaults["status"] = "needs_review"
        return defaults

    def _result(self, agent_type: str) -> Dict[str, Any]:
        result = super()._result(agent_type)
        result["prompt_version"] = LLM_PROMPT_VERSION
        result["model"] = self.model_name
        return result

    # --- prompt / parse core -------------------------------------------------

    def _build_prompt(self, instruction: str, sources: List[Dict[str, str]]) -> str:
        case_summary = _condense(
            self.case.get("case_summary") or self.case.get("title") or "Construction arbitration case.", 300
        )
        lines = [
            "You are preparing arbitration matrices for a construction dispute.",
            "Rules:",
            "- Use ONLY the numbered sources below. Never invent facts, dates, amounts, parties, clause numbers, or documents.",
            '- Every output object MUST include "source_id" copied exactly from one source id below.',
            "- If a source does not support a row, omit that source.",
            "- Return ONLY a JSON array (no prose, no markdown fences). Return [] if nothing is supported.",
            "",
            instruction.strip(),
            "",
            f"Case: {case_summary}",
            "",
            "Sources:",
        ]
        lines.extend(f"[id={source['id']}] {source['text']}" for source in sources)
        return "\n".join(lines)

    def _parse_json_rows(self, raw: str) -> List[Dict[str, Any]]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        match = re.search(r"\[.*\]", text, flags=re.S)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                parsed = parsed.get("rows") if isinstance(parsed.get("rows"), list) else [parsed]
            if isinstance(parsed, list):
                return [row for row in parsed if isinstance(row, dict)]
        raise LLMOutputError("Model response is not a parseable JSON array of rows.")

    async def _generate_rows(
        self,
        agent_type: str,
        instruction: str,
        sources: List[Dict[str, str]],
        *,
        max_sources: int = 40,
    ) -> List[Dict[str, Any]]:
        scoped = sources[:max_sources]
        if not scoped:
            return []
        prompt = self._build_prompt(instruction, scoped)
        raw = await self.generator.generate(prompt, max_tokens=2500, model=self.model_name)
        try:
            rows = self._parse_json_rows(raw)
        except LLMOutputError as exc:
            self.warnings.append(
                f"{agent_type}: {exc} No rows were created; re-run the agent or use deterministic mode."
            )
            return []
        allowed_ids = {str(source["id"]) for source in scoped}
        valid: List[Dict[str, Any]] = []
        for row in rows:
            source_id = str(row.get("source_id") or "")
            if source_id not in allowed_ids:
                self.warnings.append(
                    f"{agent_type}: LLM row rejected because it cites unknown source id '{source_id or 'missing'}'."
                )
                continue
            valid.append(row)
        return valid

    @staticmethod
    def _clean(value: Any, width: int = 700) -> Optional[str]:
        text = _condense(value, width)
        return text or None

    # --- LLM-driven handlers ---------------------------------------------------

    async def _clause_interpretation(self, agent_type: str) -> None:
        clauses = await self._clause_source_rows()
        if not clauses and self.case.get("arbitration_clause"):
            clauses = [
                {
                    "_id": f"case:{self.case_id}:arbitration_clause",
                    "clause_number": "Arbitration clause",
                    "clause_text_excerpt": self.case.get("arbitration_clause"),
                }
            ]
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for clause in clauses:
            source_id = str(_id(clause) or _first(clause, "clause_source_id", "clause_number") or "")
            excerpt = _first(clause, "clause_text_excerpt", "clause_text", "text", "content", "chunk_text", "excerpt")
            if not source_id or not excerpt:
                continue
            by_id[source_id] = clause
            clause_number = _first(clause, "clause_number", "clause", "section_number", "reference") or "Unnumbered clause"
            sources.append({"id": source_id, "text": f"{clause_number}: {_condense(excerpt, 500)}"})
        if not sources:
            self.warnings.append("Clause agent found no scoped contract clauses or arbitration clause text.")
            return
        for row in await self._generate_rows(agent_type, CLAUSE_INSTRUCTION, sources):
            source_id = str(row["source_id"])
            clause = by_id[source_id]
            clause_number = _first(clause, "clause_number", "clause", "section_number", "reference") or "Unnumbered clause"
            excerpt = _first(clause, "clause_text_excerpt", "clause_text", "text", "content", "chunk_text", "excerpt")
            body = {
                "topic": self._clean(row.get("topic"), 120) or "Contract entitlement",
                "clause_source_id": source_id,
                "clause_number": clause_number,
                # Grounding: excerpt always comes from the source row, never the model.
                "clause_text_excerpt": _condense(excerpt, 900),
                "obligation_or_right": self._clean(row.get("obligation_or_right")),
                "claimant_use": self._clean(row.get("claimant_use")),
                "respondent_use": self._clean(row.get("respondent_use")),
                "risk": self._clean(row.get("risk")),
            }
            await self._insert_matrix_row(
                "clause-matrix",
                body,
                unique={"clause_source_id": source_id},
                agent_type=agent_type,
                source_id=source_id,
                label=f"{clause_number}: {body['topic']}",
            )

    async def _claim_identification(self, agent_type: str) -> None:
        claims = await self._find_source_rows("claims", limit=int(self.options.get("claim_limit") or 50))
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for claim in claims:
            claim_id = _id(claim)
            if not claim_id:
                continue
            by_id[claim_id] = claim
            amount = _first(claim, "amount_or_days", "amount_claimed", "claim_amount", "amount")
            text_parts = [
                _first(claim, "claim_ref", "claim_no", "reference_no") or "",
                _first(claim, "title", "claim_head") or "",
                _first(claim, "description", "summary", "facts", "basis") or "",
                f"Amount: {amount}" if amount is not None else "",
                f"Clauses: {', '.join(_string_list(_first(claim, 'contract_clauses', 'clause_ids')))}",
            ]
            sources.append({"id": claim_id, "text": _condense(" | ".join(part for part in text_parts if part), 500)})
        if not sources:
            self.warnings.append("Claim agent found no scoped claim register records.")
            return
        for row in await self._generate_rows(agent_type, CLAIM_INSTRUCTION, sources):
            claim_id = str(row["source_id"])
            claim = by_id[claim_id]
            claim_no = _first(claim, "claim_no", "claim_ref", "reference_no", "number") or claim_id
            # Grounding: amount/currency/clauses/evidence come from the register row.
            amount = _first(claim, "amount_or_days", "amount_claimed", "claim_amount", "amount")
            body = {
                "source_claim_id": claim_id,
                "claim_no": str(claim_no),
                "claim_head": self._clean(row.get("claim_head"), 200) or _first(claim, "title", "description") or "Claim",
                "amount_or_days": amount,
                "amount": _numeric(amount),
                "currency": _first(claim, "currency") or "INR",
                "clause_ids": _string_list(_first(claim, "clause_ids", "contract_clauses")),
                "facts": self._clean(row.get("facts"), 800),
                "notice_ids": _string_list(_first(claim, "notice_ids", "notice_references")),
                "evidence_ids": _string_list(_first(claim, "evidence_ids", "supporting_document_ids", "document_ids")),
                "causation": self._clean(row.get("causation")),
                "calculation_id": f"Q-CLAIM-{re.sub(r'[^A-Za-z0-9]+', '-', str(claim_no)).strip('-') or 'SOURCE'}",
                "weakness": self._clean(row.get("weakness")),
                "relief": self._clean(row.get("relief")),
            }
            await self._insert_matrix_row(
                "claim-matrix",
                body,
                unique={"source_claim_id": claim_id},
                agent_type=agent_type,
                source_id=claim_id,
                label=str(body["claim_head"]),
            )

    async def _issue_framing(self, agent_type: str) -> None:
        claim_rows = await self._matrix_rows("claim-matrix")
        if not claim_rows:
            self.warnings.append("Issue framing requires claim matrix rows; run claim identification first.")
            return
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for claim in claim_rows:
            row_id = _id(claim)
            if not row_id:
                continue
            by_id[row_id] = claim
            text_parts = [
                str(claim.get("claim_head") or ""),
                str(claim.get("facts") or ""),
                str(claim.get("causation") or ""),
                str(claim.get("relief") or ""),
            ]
            sources.append({"id": row_id, "text": _condense(" | ".join(part for part in text_parts if part), 500)})
        existing = await self._matrix_rows("issue-matrix")
        next_no = len(existing) + 1
        for row in await self._generate_rows(agent_type, ISSUE_INSTRUCTION, sources):
            claim = by_id[str(row["source_id"])]
            claim_no = str(claim.get("claim_no") or claim.get("source_claim_id") or _id(claim))
            issue_key = f"claim:{claim_no}"
            issue_type = str(row.get("issue_type") or "").strip().lower()
            body = {
                "issue_key": issue_key,
                "issue_no": next_no,
                "issue": self._clean(row.get("issue"), 400)
                or f"Whether the claimant is entitled to {claim.get('claim_head') or 'the claimed relief'}.",
                "issue_type": issue_type if issue_type in _ISSUE_TYPES else "entitlement",
                "claimant_position": self._clean(row.get("claimant_position"), 500),
                "respondent_position": self._clean(row.get("respondent_position"), 500)
                or "To be developed from Statement of Defence or respondent records.",
                # Grounding: evidence/clause links come from the claim matrix row.
                "evidence_ids": _string_list(claim.get("evidence_ids")) + _string_list(claim.get("notice_ids")),
                "clause_ids": _string_list(claim.get("clause_ids")),
                "required_finding": self._clean(row.get("required_finding"), 300)
                or "Tribunal finding on entitlement, causation, quantum, and relief.",
            }
            inserted = await self._insert_matrix_row(
                "issue-matrix",
                body,
                unique={"issue_key": issue_key},
                agent_type=agent_type,
                source_id=str(row["source_id"]),
                label=str(body["issue"]),
            )
            if inserted:
                next_no += 1

    async def _defence_analysis(self, agent_type: str) -> None:
        claim_rows = await self._matrix_rows("claim-matrix")
        if not claim_rows:
            self.warnings.append("Defence analysis requires claim matrix rows; import or generate the claim matrix first.")
            return
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for claim in claim_rows:
            row_id = _id(claim)
            if not row_id:
                continue
            by_id[row_id] = claim
            text_parts = [
                str(claim.get("claim_head") or ""),
                str(claim.get("facts") or ""),
                str(claim.get("amount_or_days") or ""),
                str(claim.get("causation") or ""),
            ]
            sources.append({"id": row_id, "text": _condense(" | ".join(part for part in text_parts if part), 500)})
        for row in await self._generate_rows(agent_type, DEFENCE_INSTRUCTION, sources):
            claim = by_id[str(row["source_id"])]
            claim_no = str(claim.get("claim_no") or claim.get("source_claim_id") or _id(claim))
            admission = str(row.get("admission_denial") or "").strip().lower()
            if admission not in _ADMISSION_VALUES:
                admission = "not_admitted"
            defence_text = self._clean(row.get("defence"), 800)
            if admission in {"denied", "not_admitted"} and not defence_text:
                # Guide rule: no blanket denial without a reason.
                self.warnings.append(
                    f"{agent_type}: LLM defence row for claim {claim_no} rejected — denial without a stated reason."
                )
                continue
            body = {
                "source_claim_no": claim_no,
                "source_claim_row_id": str(row["source_id"]),
                "admission_denial": admission,
                "defence": defence_text,
                "clause_ids": _string_list(claim.get("clause_ids")),
                "evidence_ids": _string_list(claim.get("evidence_ids")),
                "quantum_objection": self._clean(row.get("quantum_objection"), 500),
                "positive_case": self._clean(row.get("positive_case"), 800),
            }
            await self._insert_matrix_row(
                "defence-matrix",
                body,
                unique={"source_claim_no": claim_no},
                agent_type=agent_type,
                source_id=str(row["source_id"]),
                label=f"Defence to claim {claim_no}",
            )

    async def _rejoinder_reply(self, agent_type: str) -> None:
        if not self.draft_id:
            self.warnings.append(
                "Rejoinder reply agent needs a draft_id with imported Statement of Defence paragraphs."
            )
            return
        paragraphs = await self._paragraph_responses()
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for para in paragraphs:
            number = str(para.get("source_paragraph_number") or "")
            text = _condense(para.get("source_paragraph_text"), 600)
            if not number or not text:
                continue
            by_id[number] = para
            sources.append({"id": number, "text": text})
        if not sources:
            self.warnings.append(
                "Rejoinder reply agent found no imported Statement of Defence paragraphs on this draft."
            )
            return
        next_no = len(await self._matrix_rows("rejoinder-matrix")) + 1
        for row in await self._generate_rows(agent_type, REJOINDER_INSTRUCTION, sources):
            number = str(row["source_id"])
            reply = self._clean(row.get("claimant_reply"), 800)
            if not reply:
                self.warnings.append(
                    f"{agent_type}: reply for SoD paragraph {number} rejected — no grounded reply text."
                )
                continue
            new_matter = bool(row.get("new_matter"))
            body = {
                "source_sod_para": number,
                "rejoinder_no": next_no,
                "nature_of_defence": self._clean(row.get("nature_of_defence"), 200),
                "claimant_reply": reply,
                "reply_to_counterclaim": self._clean(row.get("reply_to_counterclaim"), 600),
                "new_matter": new_matter,
                # A rejoinder that raises new matter needs tribunal leave; flag it so
                # the readiness gate and validator block it until permission is recorded.
                "tribunal_permission_required": new_matter,
            }
            inserted = await self._insert_matrix_row(
                "rejoinder-matrix",
                body,
                unique={"source_sod_para": number},
                agent_type=agent_type,
                source_id=number,
                label=f"Reply to SoD para {number}",
            )
            if inserted:
                next_no += 1
            if new_matter:
                self.warnings.append(
                    f"Rejoinder reply to SoD paragraph {number} raises new matter; tribunal permission is required."
                )

    async def _document_understanding(self, agent_type: str) -> None:
        documents = await self._find_source_rows("documents", limit=int(self.options.get("document_limit") or 50))
        by_id: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, str]] = []
        for document in documents:
            document_id = _id(document)
            if not document_id:
                continue
            by_id[document_id] = document
            text_parts = [
                _document_title(document),
                _first(document, "letter_no", "reference_no") or "",
                _first(document, "subject", "summary", "description", "full_content", "extracted_text") or "",
            ]
            sources.append({"id": document_id, "text": _condense(" | ".join(part for part in text_parts if part), 500)})
        if not sources:
            self.warnings.append("Document understanding found no scoped documents for this arbitration case.")
            return
        index_collection = _collection(self.db, "arbitration_document_index")
        for row in await self._generate_rows(agent_type, DOCUMENT_INSTRUCTION, sources):
            document_id = str(row["source_id"])
            document = by_id[document_id]
            analysis = {
                "document_type": self._clean(row.get("document_type"), 60) or "letter",
                "relevance_note": self._clean(row.get("relevance_note"), 700),
                "issue_tags": _string_list(row.get("issue_tags"))[:10],
                "claim_tags": _string_list(row.get("claim_tags"))[:10],
            }
            existing = None
            if index_collection is not None:
                existing = await index_collection.find_one(
                    {"case_id": self.case_id, "source_id": document_id, "deleted_at": {"$exists": False}}
                )
            if existing:
                if _verified(existing):
                    # Never mutate rows a human has already approved/verified.
                    continue
                await index_collection.update_one(
                    {"_id": existing["_id"], "case_id": self.case_id},
                    {"$set": {**analysis, **self._status_defaults("document-index"), "generated_by_agent": agent_type}},
                )
                self.source_ids.add(document_id)
                self.created_records.append(
                    {
                        "agent_type": agent_type,
                        "matrix": "document-index",
                        "collection": "arbitration_document_index",
                        "row_id": existing.get("_id"),
                        "label": _document_title(document),
                        "source_id": document_id,
                        "action": "updated",
                    }
                )
                continue
            # Grounding: identity/metadata fields come from the source document.
            body = {
                "source_type": document.get("source_type") or "document",
                "source_id": document_id,
                "title": _document_title(document),
                "document_date": _first(document, "document_date", "letter_date", "date", "created_at"),
                "letter_no": _first(document, "letter_no", "letterNo", "reference_no", "document_number"),
                "sender": _first(document, "from_company", "sender", "from", "issuing_party"),
                "recipient": _first(document, "to_company", "recipient", "to", "receiving_party"),
                "source_file_link": _first(document, "file_url", "source_file_link", "storage_path", "file_path")
                or f"/documents/{document_id}",
                "allowed_use": "fact",
                **analysis,
            }
            await self._insert_matrix_row(
                "document-index",
                body,
                unique={"source_id": document_id},
                agent_type=agent_type,
                source_id=document_id,
                label=str(body["title"]),
            )


__all__ = ["LLMArbitrationAgent", "LLMOutputError", "LLM_PROMPT_VERSION", "resolve_llm_model"]
