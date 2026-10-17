"""Generate, persist and serve the grounded summary of one document.

Generation (:meth:`GroundedSummaryService.generate`) is internal: the pipeline
and the review service call it after their own authorisation, and the on-demand
route calls it after the document gate. It

1. refuses a document that may not be consumed (human review, duplicate,
   deletion) - the summary is held, never generated over evidence nobody has
   released;
2. reads the canonical evidence (re-verified against its manifest) - a document
   without it (``legacy_v0``) gets no grounded summary;
3. builds the Fact Pack, renders deterministically, and optionally lets a model
   reword the narrative sections under :func:`validate_presentation`;
4. writes the result only if the head still names the revision it was built
   from, and appends it to ``document_grounded_summaries`` for audit.

A failure anywhere marks ``summary_state.status = failed`` and returns; it never
raises into the extraction pipeline. The summary is derivative.

Serving (:meth:`read_for_user`) applies the same gate order as
``GET /documents/{id}`` and decides from the live head whether the stored
summary is current. A stale or withheld summary is never returned as text.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from fastapi import status

from ...core.permissions import Permissions
from ...models.grounded_summary import (
    DISPLAYABLE_STATUSES,
    GROUNDED_SUMMARY_PROMPT_VERSION,
    SUMMARY_BLOCKED_FOR_REVIEW,
    SUMMARY_CURRENT,
    SUMMARY_DEGRADED,
    SUMMARY_FAILED,
    SUMMARY_GENERATING,
    SUMMARY_NOT_GENERATED,
    SUMMARY_STALE,
    SUMMARY_UNAVAILABLE,
    SUMMARY_WITHHELD,
    DetailedSummary,
    GeneratedFrom,
    GeneratorInfo,
)
from ...models.processing_state import ProcessingState
from ...utils.error_handler import DocumentError
from ..canonical_evidence_service import (
    EVIDENCE_PUBLISHED,
    CanonicalEvidence,
    CanonicalEvidenceInconsistentError,
    _read_canonical_evidence,
)
from ..extraction_adapters.document_page_store import DOCUMENT_EXTRACTION_HEADS
from ..publication_policy import document_id_candidates, is_consumable
from .fact_pack import FactPack, build_fact_pack
from .presentation import (
    PROMPT,
    PresentationModel,
    PresentationRejected,
    build_user_message,
    validate_presentation,
)
from .render import (
    deterministic_sections,
    executive_overview,
    item_from_facts,
    render_markdown,
    short_summary,
)

logger = logging.getLogger(__name__)

DOCUMENT_GROUNDED_SUMMARIES = "document_grounded_summaries"


def llm_enabled() -> bool:
    return os.getenv("GROUNDED_SUMMARY_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _held_status(document: Mapping[str, Any]) -> str:
    if str(document.get("processing_status") or "") == ProcessingState.HUMAN_REVIEW_REQUIRED.value:
        return SUMMARY_BLOCKED_FOR_REVIEW
    return SUMMARY_WITHHELD


def _is_degraded(pack: FactPack) -> bool:
    if any(state.cap == "uncertain" for state in pack.page_states.values()):
        return True
    if pack.metadata_quality.get("degraded"):
        return True
    return any(
        fact.verification_status == "uncertain"
        for fact in pack.facts
        if fact.type in {"letter_number", "letter_date", "subject", "from_party"}
    )


async def compose(
    pack: FactPack,
    evidence: CanonicalEvidence,
    *,
    model: Optional[PresentationModel] = None,
) -> tuple[Optional[str], DetailedSummary]:
    """Both summaries from one Fact Pack. Deterministic unless a model is given
    and its output passes validation."""
    sections = deterministic_sections(pack)
    overview = executive_overview(pack)
    short = short_summary(pack)
    mode = "deterministic"
    rejection: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    model_name: Optional[str] = None
    if model is not None:
        model_name = getattr(model, "model_name", None)
        started = time.perf_counter()
        try:
            raw, usage = await model.complete(PROMPT, build_user_message(pack))
            presentation = validate_presentation(raw, pack)
        except PresentationRejected as exc:
            rejection = f"rejected: {exc}"
        except Exception as exc:  # provider failure: style degrades, accuracy does not
            rejection = f"unavailable: {type(exc).__name__}"
        else:
            mode = "llm_assisted"
            if presentation.short_summary:
                short = presentation.short_summary
            if presentation.executive_overview:
                overview = presentation.executive_overview
            for key, items in presentation.sections.items():
                if items:
                    sections[key] = items
        usage = dict(usage or {})
        usage["latency_ms"] = round((time.perf_counter() - started) * 1000)
        if rejection:
            logger.warning("[grounded_summary] model presentation not used for %s: %s", evidence.document_id, rejection)

    review = pack.review_resolution
    detailed = DetailedSummary(
        executive_overview=overview,
        **sections,  # type: ignore[arg-type]
        facts=pack.facts,
        markdown=render_markdown(overview, sections),
        generated_from=GeneratedFrom(
            canonical_revision=int(evidence.canonical_revision or 0),
            canonical_sha256=str(evidence.manifest.get("canonical_sha256") or ""),
            extraction_run_id=evidence.extraction_run_id,
            review_resolution=(
                {
                    key: review.get(key)
                    for key in ("canonical_revision", "canonical_sha256", "approved_pages", "resolved_at", "status")
                }
                if review
                else None
            ),
        ),
        generator=GeneratorInfo(
            mode=mode,  # type: ignore[arg-type]
            model=model_name if mode == "llm_assisted" else None,
            prompt_version=GROUNDED_SUMMARY_PROMPT_VERSION,
            generated_at=_now(),
            degraded=_is_degraded(pack),
            llm_rejection=rejection,
            usage=usage,
        ),
    )
    return short, detailed


def default_model() -> Optional[PresentationModel]:
    if not llm_enabled():
        return None
    from ...config.document_processing_config import DocumentProcessingConfig
    from .presentation import OpenAIPresentationModel

    config = DocumentProcessingConfig()
    if not config.openai_api_key:
        return None
    try:
        return OpenAIPresentationModel(config.openai_api_key, config.openai_model, float(config.openai_timeout or 60))
    except Exception:
        logger.warning("[grounded_summary] presentation model unavailable", exc_info=True)
        return None


class GroundedSummaryService:
    def __init__(self, db: Any, *, model: Optional[PresentationModel] = None, use_default_model: bool = True):
        self.db = db
        self._model = model
        self._use_default_model = use_default_model and model is None

    def _presentation_model(self) -> Optional[PresentationModel]:
        if self._model is not None:
            return self._model
        if self._use_default_model:
            return default_model()
        return None

    async def _find_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        for key in document_id_candidates(document_id):
            found = await self.db.documents.find_one({"_id": key})
            if found:
                return found
        return None

    async def _set_state(self, document: Mapping[str, Any], status_value: str, **extra: Any) -> None:
        state = {"status": status_value, "updated_at": _now(), **extra}
        await self.db.documents.update_one({"_id": document["_id"]}, {"$set": {"summary_state": state}})

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------
    async def generate(self, document_id: str, *, trigger: str) -> Dict[str, Any]:
        """Generate for the current canonical revision. Never raises."""
        try:
            return await self._generate(str(document_id), trigger=trigger)
        except Exception as exc:
            logger.exception("[grounded_summary] generation failed for %s (%s)", document_id, trigger)
            try:
                document = await self._find_document(str(document_id))
                if document:
                    await self._set_state(document, SUMMARY_FAILED, reason=type(exc).__name__, trigger=trigger)
            except Exception:
                logger.warning("[grounded_summary] could not record failure for %s", document_id, exc_info=True)
            return {"status": SUMMARY_FAILED, "reason": type(exc).__name__}

    async def _generate(self, document_id: str, *, trigger: str) -> Dict[str, Any]:
        started = time.perf_counter()
        document = await self._find_document(document_id)
        if not document or document.get("lifecycle_state") == "deleted":
            return {"status": SUMMARY_WITHHELD, "reason": "document_not_found"}
        if not is_consumable(document):
            held = _held_status(document)
            await self._set_state(document, held, reason="document_not_consumable", trigger=trigger)
            return {"status": held, "reason": "document_not_consumable"}
        try:
            evidence = await _read_canonical_evidence(self.db, document)
        except CanonicalEvidenceInconsistentError as exc:
            await self._set_state(document, SUMMARY_FAILED, reason=f"evidence_inconsistent: {exc.reason}", trigger=trigger)
            return {"status": SUMMARY_FAILED, "reason": "evidence_inconsistent"}
        if evidence.status != EVIDENCE_PUBLISHED:
            await self._set_state(document, SUMMARY_UNAVAILABLE, reason="no_canonical_evidence", trigger=trigger)
            return {"status": SUMMARY_UNAVAILABLE, "reason": "no_canonical_evidence"}

        await self._set_state(
            document, SUMMARY_GENERATING, trigger=trigger,
            generated_from={"canonical_revision": evidence.canonical_revision,
                            "canonical_sha256": evidence.manifest.get("canonical_sha256")},
        )
        pack_started = time.perf_counter()
        pack = build_fact_pack(evidence, document, review_resolution=evidence.manifest.get("review_resolution"))
        pack_ms = round((time.perf_counter() - pack_started) * 1000)
        short, detailed = await compose(pack, evidence, model=self._presentation_model())

        # Bind to the revision it was built from: if the head moved, this
        # result describes evidence that is no longer current.
        head = await self.db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": str(document["_id"])}) or {}
        current = await self._find_document(document_id) or {}
        if (
            head.get("canonical_revision") != evidence.canonical_revision
            or head.get("canonical_sha256") != evidence.manifest.get("canonical_sha256")
        ):
            await self._set_state(document, SUMMARY_STALE, reason="evidence_changed_during_generation", trigger=trigger)
            return {"status": SUMMARY_STALE, "reason": "evidence_changed_during_generation"}
        if not is_consumable(current):
            held = _held_status(current)
            await self._set_state(document, held, reason="document_not_consumable", trigger=trigger)
            return {"status": held, "reason": "document_not_consumable"}

        result_status = SUMMARY_DEGRADED if detailed.generator.degraded else SUMMARY_CURRENT
        payload = detailed.model_dump(mode="python")
        generated_from = payload["generated_from"]
        timing = {"fact_pack_ms": pack_ms, "total_ms": round((time.perf_counter() - started) * 1000)}
        await self.db.documents.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "short_summary": short,
                    "detailed_summary": payload,
                    "summary_state": {
                        "status": result_status,
                        "updated_at": _now(),
                        "trigger": trigger,
                        "generated_from": generated_from,
                        "timing": timing,
                    },
                }
            },
        )
        await self.db[DOCUMENT_GROUNDED_SUMMARIES].insert_one(
            {
                "document_id": str(document["_id"]),
                "organization_id": evidence.organization_id,
                "project_id": evidence.project_id,
                "canonical_revision": generated_from["canonical_revision"],
                "canonical_sha256": generated_from["canonical_sha256"],
                "status": result_status,
                "trigger": trigger,
                "short_summary": short,
                "detailed_summary": payload,
                "created_at": _now(),
            }
        )
        logger.info(
            "[grounded_summary] %s: %s at revision %s (%s facts, mode=%s, %s ms)",
            document["_id"], result_status, generated_from["canonical_revision"], len(detailed.facts),
            detailed.generator.mode, timing["total_ms"],
        )
        return {"status": result_status, "canonical_revision": generated_from["canonical_revision"], "timing": timing}

    # ------------------------------------------------------------------
    # serving
    # ------------------------------------------------------------------
    async def authorised_document(self, document_id: str, current_user: Any, selection: Any, permission: str) -> Dict[str, Any]:
        """Selection first, then the policy - exactly GET /documents/{id}'s order."""
        from ..policy_service import PolicyService

        if selection is None:
            raise DocumentError("An active scope selection is required", status.HTTP_400_BAD_REQUEST)
        selection.require_selection()
        document = await self._find_document(str(document_id))
        if not document or document.get("lifecycle_state") == "deleted":
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
        await selection.require_record(document, allow_unscoped=True)
        await PolicyService(self.db).authorize_document(current_user, permission, document)
        return document

    async def read_for_user(self, document_id: str, current_user: Any, selection: Any) -> Dict[str, Any]:
        document = await self.authorised_document(document_id, current_user, selection, Permissions.DOCUMENT_VIEW)
        return await self.view(document)

    async def view(self, document: Mapping[str, Any]) -> Dict[str, Any]:
        """What may be shown for this document right now."""
        doc_id = str(document["_id"])
        state = document.get("summary_state") if isinstance(document.get("summary_state"), Mapping) else {}
        head = await self.db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": doc_id}) or {}
        current_evidence = (
            {"canonical_revision": head.get("canonical_revision"), "canonical_sha256": head.get("canonical_sha256")}
            if head.get("canonical_sha256")
            else None
        )
        out: Dict[str, Any] = {
            "document_id": doc_id,
            "summary_status": SUMMARY_NOT_GENERATED,
            "reason": None,
            "short_summary": None,
            "detailed_summary": None,
            "generated_from": None,
            "current_evidence": current_evidence,
        }
        if not is_consumable(document):
            out["summary_status"] = _held_status(document)
            out["reason"] = "document_not_consumable"
            return out
        if current_evidence is None:
            out["summary_status"] = SUMMARY_UNAVAILABLE
            out["reason"] = "no_canonical_evidence"
            return out
        detailed = document.get("detailed_summary")
        stored_status = str((state or {}).get("status") or "")
        if not isinstance(detailed, Mapping):
            out["summary_status"] = stored_status or SUMMARY_NOT_GENERATED
            out["reason"] = (state or {}).get("reason")
            return out
        generated_from = detailed.get("generated_from") or {}
        out["generated_from"] = generated_from
        if (
            generated_from.get("canonical_revision") != head.get("canonical_revision")
            or generated_from.get("canonical_sha256") != head.get("canonical_sha256")
        ):
            out["summary_status"] = SUMMARY_GENERATING if stored_status == SUMMARY_GENERATING else SUMMARY_STALE
            out["reason"] = "canonical_evidence_changed"
            return out
        if stored_status not in DISPLAYABLE_STATUSES:
            # e.g. a generation for this revision failed after an earlier
            # success; the stored text still matches the head, so it is shown
            # with the newer status beside it.
            stored_status = SUMMARY_DEGRADED if (detailed.get("generator") or {}).get("degraded") else SUMMARY_CURRENT
        out["summary_status"] = stored_status
        out["short_summary"] = document.get("short_summary")
        out["detailed_summary"] = dict(detailed)
        return out


__all__ = [
    "DOCUMENT_GROUNDED_SUMMARIES",
    "GroundedSummaryService",
    "compose",
    "default_model",
    "item_from_facts",
    "llm_enabled",
]
