"""Release 1.2 grounded letter summary: authority, grounding, provenance.

The evidence is real: pages are recorded and published through
``DocumentPageStore`` exactly as the unified pipeline does, and read back
through the verified canonical reader. Only the presentation model is a fake,
because the point of most of these tests is what happens when it misbehaves.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.core.tenant_context import ActiveScope
from rbac_backend.models.grounded_summary import (
    GROUNDED_SUMMARY_PROMPT_VERSION,
    SUMMARY_BLOCKED_FOR_REVIEW,
    SUMMARY_CURRENT,
    SUMMARY_DEGRADED,
    SUMMARY_STALE,
    SUMMARY_UNAVAILABLE,
    SUMMARY_WITHHELD,
)
from rbac_backend.services.canonical_evidence_service import _read_canonical_evidence
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.extraction.canonical import assemble_canonical_document
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    DOCUMENT_EXTRACTION_HEADS,
    DocumentPageStore,
)
from rbac_backend.services.grounded_summary.fact_pack import build_fact_pack, find_dates
from rbac_backend.services.grounded_summary.presentation import (
    PresentationRejected,
    validate_presentation,
)
from rbac_backend.services.grounded_summary.service import (
    DOCUMENT_GROUNDED_SUMMARIES,
    GroundedSummaryService,
    compose,
)
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.retry_harness import FakeDb

ORG_A, ORG_B = "org-A", "org-B"
PROJECT_A = "proj-A1"

PAGE_ONE = (
    "GLM/KNP/UPMRC/2024/4299\n"
    "Date: 22.06.2024\n"
    "To: General Consultant, UPMRC\n"
    "Sub: Revised claim for reworks due to soil collapse at Nayaganj Station\n"
    "Ref: (1) GLM/KNP/UPMRC/2022/2327 dated 24-12-2022\n"
    "(2) GC/KNP/GLM/2023/118 dated 03.02.2023\n\n"
    "With reference to the above letters, we submit our revised claim.\n\n"
    "On 26 February 2023 the guide wall collapsed due to soil conditions not disclosed in the tender data. "
    "The Contractor states that the soil collapse caused additional cost of Rs. 3,40,11,265/- including "
    "idling of the rig for 52 days. "
    "We request the Engineer to determine the claim under Clause 17.1 of GCC. "
    "We reserve our right to claim further costs and extension of time under GCC 8.4.1.\n\n"
    "For Gulermak-Sam India Kanpur Metro JV\nAuthorised Signatory"
)
PAGE_TWO = "Claim summary\nTotal basic amount 22140168\nOverheads @ 20%\nAmount carried to the claim: Rs. 2,21,40,168/-"


def _classification(text: str) -> PageClassification:
    return PageClassification(
        page_class=PageClass.TEXT_NATIVE, char_count=len(text), image_count=0, image_coverage=0.0,
        table_count=0, width=595.0, height=842.0, rotation=0,
    )


def _page(number: int, text: str, **kwargs: Any) -> ExtractedPage:
    return ExtractedPage(
        number=number, text=text, source=PageSource.TEXT_LAYER, status=PageStatus.TEXT_LAYER,
        classification=_classification(text), quality_verdict=kwargs.pop("quality_verdict", "pass"), **kwargs,
    )


def _pages(page_two_checks: Optional[list] = None, page_two_review: bool = False) -> List[ExtractedPage]:
    return [
        _page(1, PAGE_ONE),
        _page(
            2, PAGE_TWO,
            quality_checks=page_two_checks or [
                {"name": "subtotal", "verdict": "pass", "candidate_value": "22140168", "detail": "sum of heads"},
            ],
            needs_review=page_two_review,
            quality_verdict="fail" if page_two_review else "pass",
        ),
    ]


async def _seed(
    db: FakeDb,
    *,
    org: str = ORG_A,
    pages: Optional[List[ExtractedPage]] = None,
    run: str = "run-1",
    **fields: Any,
) -> str:
    document_id = ObjectId()
    row: Dict[str, Any] = {
        "_id": document_id,
        "organization_id": org,
        "project_id": PROJECT_A,
        "filename": "4299.pdf",
        "processing_status": "metadata_extracted",
        "letterNo": "GLM/KNP/UPMRC/2024/4299",
        "date": "2024-06-22T00:00:00",
        "subject": "Revised claim for reworks due to soil collapse at Nayaganj Station",
        "from": "Gulermak-Sam India Kanpur Metro JV",
        "to": "General Consultant, UPMRC",
        "summary": "LLM SUMMARY: the Engineer approved INR 99,99,999 under Clause 99.9",
        "key_reply_points": ["REPLY POINT: dispute the soil data"],
        "reference": [
            {"letterNo": "GLM/KNP/UPMRC/2022/2327", "date": "24-12-2022"},
            {"letterNo": "GC/KNP/GLM/2023/118", "date": "03-02-2023"},
        ],
        "references": [{"documentId": "linked-2327", "letterNo": "GLM/KNP/UPMRC/2022/2327"}],
        "contractual_clauses": ["Clause 17.1", "Clause 20.1"],
        "metadata_quality": {"status": "complete", "degraded": False, "references_authoritative": True},
    }
    row.update(fields)
    await db.documents.insert_one(row)
    await _publish(db, str(document_id), pages or _pages(), run=run, org=org)
    return str(document_id)


async def _publish(db: FakeDb, document_id: str, pages: List[ExtractedPage], *, run: str, org: str = ORG_A) -> dict:
    store = DocumentPageStore(
        db=db, document_id=document_id, organization_id=org, project_id=PROJECT_A, extraction_run_id=run
    )
    await store.record_pages(pages)
    return await store.publish_canonical(
        expected=assemble_canonical_document(pages), pipeline_version="unified_v1", source_sha256="abc"
    )


async def _pack(db: FakeDb, document_id: str):
    document = await db.documents.find_one({"_id": ObjectId(document_id)})
    evidence = await _read_canonical_evidence(db, document)
    return build_fact_pack(evidence, document, review_resolution=evidence.manifest.get("review_resolution")), evidence


class FakeModel:
    model_name = "fake-model"

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: List[Tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply(user) if callable(self.reply) else self.reply
        return text, {"prompt_tokens": len(user) // 4, "completion_tokens": len(text) // 4}


def _facts_of(user_message: str) -> List[Dict[str, Any]]:
    return json.loads(user_message.split("FACTS:\n", 1)[1])


def _first(facts: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    return next(fact for fact in facts if fact["type"] == kind)


# --- 1. short summary grounding ------------------------------------------------


async def test_short_summary_cannot_introduce_a_clause_absent_from_the_fact_pack() -> None:
    db = FakeDb()
    document_id = await _seed(db)
    pack, evidence = await _pack(db, document_id)

    def reply(user: str) -> str:
        subject = _first(_facts_of(user), "subject")
        return json.dumps(
            {"short_summary": {"text": "Contractor claims under Clause 99.9.", "fact_ids": [subject["id"]]}}
        )

    short, detailed = await compose(pack, evidence, model=FakeModel(reply))

    assert "99.9" not in (short or "")
    assert detailed.generator.mode == "deterministic"
    assert "number '99.9'" in (detailed.generator.llm_rejection or "")


async def test_deterministic_short_summary_is_bounded_and_built_from_header_facts() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    short, _ = await compose(pack, evidence)

    assert short and len(short) <= 240
    assert "Gulermak-Sam India Kanpur Metro JV" in short
    assert "Nayaganj" in short


# --- 2/3. detailed grounding and provenance -------------------------------------------


async def test_every_detailed_item_cites_existing_facts_with_page_provenance() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    _, detailed = await compose(pack, evidence)

    fact_ids = {fact.id for fact in detailed.facts}
    facts = {fact.id: fact for fact in detailed.facts}
    items = [
        item
        for key in ("background", "material_events", "issues_positions", "contractual_basis", "requests_relief",
                    "claims_reservations", "dates_periods", "amounts_quantities", "references")
        for item in getattr(detailed, key)
    ]
    assert items
    for item in items:
        assert item.fact_ids and set(item.fact_ids) <= fact_ids
        # Every fact read from the canonical text carries its page and span,
        # and the span really is that text.
        for fact_id in item.fact_ids:
            fact = facts[fact_id]
            if fact.origin == "canonical_text":
                assert fact.page_numbers and fact.source_spans
                for span in fact.source_spans:
                    assert evidence.page_at(span.start_char) == span.page_number
                    assert evidence.text[span.start_char:span.end_char].strip()
    assert detailed.generated_from.canonical_revision == evidence.canonical_revision
    assert detailed.generated_from.canonical_sha256 == evidence.manifest["canonical_sha256"]
    assert detailed.generated_from.extraction_run_id == "run-1"


async def test_header_facts_located_in_the_text_are_verified_with_their_span() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    letter = pack.by_type("letter_number")[0]
    assert letter.verification_status == "verified"
    span = letter.source_spans[0]
    assert evidence.text[span.start_char:span.end_char] == "GLM/KNP/UPMRC/2024/4299"
    letter_date = pack.by_type("letter_date")[0]
    # 22.06.2024 has one valid reading: no day/month ambiguity.
    assert letter_date.verification_status == "verified"
    assert letter_date.normalized_value == "2024-06-22"


async def test_metadata_the_text_does_not_state_is_uncertain_not_verified() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db, subject="Completely different subject never written"))

    subject = pack.by_type("subject")[0]
    assert subject.verification_status == "uncertain"
    assert any(f.type == "metadata_qualification" and "Subject" in f.label for f in pack.facts)


# --- 4. party position --------------------------------------------------------------


async def test_party_statements_stay_attributed_in_the_deterministic_render() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    _, detailed = await compose(pack, evidence)

    statements = detailed.issues_positions + detailed.material_events + detailed.requests_relief + detailed.claims_reservations
    soil = [item for item in statements if "soil collapse caused additional cost" in item.text]
    assert soil, [item.text for item in statements]
    for item in soil:
        assert item.text.startswith("Gulermak-Sam India Kanpur Metro JV ")
        assert item.party == "Gulermak-Sam India Kanpur Metro JV"
    requests = [item for item in detailed.requests_relief if "Clause 17.1" in item.text]
    assert requests and requests[0].role == "request"
    reservations = [item for item in detailed.claims_reservations if "reserve our right" in item.text]
    assert reservations and reservations[0].role == "reservation"


async def test_model_turning_an_allegation_into_established_fact_is_rejected() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    def reply(user: str) -> str:
        position = next(f for f in _facts_of(user) if f.get("role") == "party_position")
        return json.dumps(
            {"sections": {"issues_positions": [
                {"text": "The soil collapse caused additional cost.", "fact_ids": [position["id"]]}
            ]}}
        )

    _, detailed = await compose(pack, evidence, model=FakeModel(reply))

    assert detailed.generator.mode == "deterministic"
    assert "without attribution" in (detailed.generator.llm_rejection or "")
    assert all("Gulermak" in item.text or "author" in item.text for item in detailed.issues_positions)


async def test_model_asserting_entitlement_is_rejected() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))
    claim = pack.by_type("claim_statement", "reservation_statement")[0]

    with pytest.raises(PresentationRejected, match="conclusion"):
        validate_presentation(
            json.dumps({"sections": {"claims_reservations": [
                {"text": f"{claim.party} states it is entitled to the costs, which are established.",
                 "fact_ids": [claim.id]}
            ]}}),
            pack,
        )


async def test_valid_model_output_is_used_and_its_items_inherit_fact_provenance() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    def reply(user: str) -> str:
        facts = _facts_of(user)
        request = next(f for f in facts if f.get("role") == "request")
        subject = _first(facts, "subject")
        return json.dumps({
            "short_summary": {"text": "Contractor's revised claim for reworks at Nayaganj Station.",
                              "fact_ids": [subject["id"]]},
            "sections": {"requests_relief": [
                {"text": "The Contractor requests determination of the claim under Clause 17.1 of GCC.",
                 "fact_ids": [request["id"]]}
            ]},
        })

    short, detailed = await compose(pack, evidence, model=FakeModel(reply))

    assert detailed.generator.mode == "llm_assisted"
    assert detailed.generator.prompt_version == GROUNDED_SUMMARY_PROMPT_VERSION
    assert short == "Contractor's revised claim for reworks at Nayaganj Station."
    item = detailed.requests_relief[0]
    assert item.page_numbers == [1] and item.source_spans and item.role == "request"
    # Structured sections never come from the model.
    assert detailed.amounts_quantities and all(i.fact_ids for i in detailed.amounts_quantities)


def test_prompt_version_is_pinned() -> None:
    assert GROUNDED_SUMMARY_PROMPT_VERSION == "grounded_letter_summary.v4"


def test_prompt_asks_for_less_than_the_validator_accepts() -> None:
    from rbac_backend.services.grounded_summary.presentation import PROMPT, SHORT_SUMMARY_MAX

    assert "fewer than 240 characters" in PROMPT and SHORT_SUMMARY_MAX >= 240
    assert "{{" not in PROMPT and "FACTS_JSON" not in PROMPT


async def test_the_model_sees_the_status_under_the_name_the_prompt_uses() -> None:
    from rbac_backend.services.grounded_summary.presentation import fact_pack_payload

    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))

    rows = fact_pack_payload(pack)
    assert rows and all("verification_status" in row and "status" not in row for row in rows)


async def test_a_declined_summary_field_keeps_its_deterministic_text_and_the_sections_are_used() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))
    deterministic_short, _ = await compose(pack, evidence)

    def reply(user: str) -> str:
        request = next(f for f in _facts_of(user) if f.get("role") == "request")
        return json.dumps({
            "short_summary": {"text": "", "fact_ids": []},
            "executive_overview": {"text": "", "fact_ids": []},
            "sections": {"requests_relief": [
                {"text": "The Contractor requests determination of the claim under Clause 17.1 of GCC.",
                 "fact_ids": [request["id"]]}
            ]},
        })

    short, detailed = await compose(pack, evidence, model=FakeModel(reply))

    assert detailed.generator.mode == "llm_assisted", detailed.generator.llm_rejection
    assert short == deterministic_short
    assert detailed.requests_relief[0].text.startswith("The Contractor requests determination")


async def test_empty_text_that_still_cites_a_fact_is_rejected() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))
    subject = pack.by_type("subject")[0]

    with pytest.raises(PresentationRejected, match="empty text"):
        validate_presentation(json.dumps({"short_summary": {"text": "", "fact_ids": [subject.id]}}), pack)


# --- 5/6. human review ----------------------------------------------------------------


async def test_a_document_held_for_review_gets_no_new_summary() -> None:
    db = FakeDb()
    document_id = await _seed(db, pages=_pages(page_two_review=True), processing_status="human_review_required")

    outcome = await GroundedSummaryService(db, use_default_model=False).generate(document_id, trigger="pipeline")

    stored = await db.documents.find_one({"_id": ObjectId(document_id)})
    assert outcome["status"] == SUMMARY_BLOCKED_FOR_REVIEW
    assert "detailed_summary" not in stored and "short_summary" not in stored
    assert stored["summary_state"]["status"] == SUMMARY_BLOCKED_FOR_REVIEW


async def test_after_human_approval_the_summary_regenerates_and_keeps_the_machine_failure() -> None:
    db = FakeDb()
    checks = [{"name": "subtotal", "verdict": "fail", "candidate_value": "22140168", "detail": "mismatch"}]
    document_id = await _seed(db, pages=_pages(page_two_checks=checks, page_two_review=True),
                              processing_status="partially_processed")
    head = await db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": document_id})
    await db[DOCUMENT_EXTRACTION_HEADS].update_one(
        {"document_id": document_id},
        {"$set": {"review_resolution": {
            "status": "resolved", "canonical_revision": head["canonical_revision"],
            "canonical_sha256": head["canonical_sha256"], "approved_pages": [2],
        }}},
    )

    outcome = await GroundedSummaryService(db, use_default_model=False).generate(document_id, trigger="review_resolved")
    stored = await db.documents.find_one({"_id": ObjectId(document_id)})
    detailed = stored["detailed_summary"]

    assert outcome["status"] in {SUMMARY_CURRENT, SUMMARY_DEGRADED}
    assert detailed["generated_from"]["review_resolution"]["approved_pages"] == [2]
    page_two = [f for f in detailed["facts"] if 2 in f["page_numbers"] and f["origin"] == "canonical_text"]
    assert page_two and all(f["verification_status"] == "human_approved" for f in page_two)
    qualification = [u for u in detailed["uncertainties"] if "Page 2 failed automated quality checks" in u["text"]]
    assert qualification and "subtotal" in qualification[0]["text"]


async def test_a_review_resolution_for_another_revision_does_not_apply() -> None:
    db = FakeDb()
    document_id = await _seed(db, pages=_pages(page_two_review=True), processing_status="partially_processed")
    await db[DOCUMENT_EXTRACTION_HEADS].update_one(
        {"document_id": document_id},
        {"$set": {"review_resolution": {"canonical_revision": 99, "canonical_sha256": "old", "approved_pages": [2]}}},
    )
    pack, _ = await _pack(db, document_id)

    assert pack.review_resolution is None
    assert all(f.verification_status == "uncertain" for f in pack.facts if 2 in f.page_numbers and f.origin == "canonical_text")


# --- 7. stale canonical revision -------------------------------------------------------


async def test_a_new_canonical_revision_makes_the_stored_summary_stale_and_hidden() -> None:
    db = FakeDb()
    document_id = await _seed(db)
    service = GroundedSummaryService(db, use_default_model=False)
    assert (await service.generate(document_id, trigger="pipeline"))["status"] in {SUMMARY_CURRENT, SUMMARY_DEGRADED}

    await _publish(db, document_id, [_page(1, PAGE_ONE + " Additional sentence."), _page(2, PAGE_TWO)], run="run-2")
    view = await service.view(await db.documents.find_one({"_id": ObjectId(document_id)}))

    assert view["summary_status"] == SUMMARY_STALE
    assert view["detailed_summary"] is None and view["short_summary"] is None
    assert view["current_evidence"]["canonical_revision"] == 2


# --- 8. amounts -------------------------------------------------------------------------


async def test_amounts_come_from_the_canonical_text_with_the_gate_verdict() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))

    amounts = {fact.normalized_value: fact for fact in pack.by_type("amount")}
    claim = amounts[34011265]
    assert claim.unit == "INR" and claim.page_numbers == [1]
    assert claim.verification_status == "source_stated"
    table = amounts[22140168]
    assert table.verification_status == "verified" and table.page_numbers == [2]
    assert table.evidence_checks[0]["name"] == "subtotal"
    # The LLM summary's amount is never a fact.
    assert 9999999 not in amounts


# --- 9. references ------------------------------------------------------------------------


async def test_stated_and_resolved_references_stay_distinct() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    references = {fact.normalized_value: fact for fact in pack.by_type("reference")}
    linked = references["glm-knp-upmrc-2022-2327"]
    unlinked = references["gc-knp-glm-2023-118"]
    assert linked.resolved_document_id == "linked-2327"
    assert unlinked.resolved_document_id is None
    assert linked.verification_status == unlinked.verification_status == "source_stated"
    _, detailed = await compose(pack, evidence)
    texts = [item.text for item in detailed.references]
    assert any("2327" in t and "linked DMS record" in t and "not linked" not in t for t in texts)
    assert any("118" in t and "not linked to a DMS record" in t for t in texts)


# --- 10/11. fallback and hallucination ---------------------------------------------------------


async def test_model_unavailable_still_produces_the_deterministic_summary() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))

    short, detailed = await compose(pack, evidence, model=FakeModel(RuntimeError("provider down")))

    assert short and detailed.generator.mode == "deterministic"
    assert detailed.generator.llm_rejection == "unavailable: RuntimeError"
    assert detailed.requests_relief and detailed.markdown


async def test_model_citing_an_unknown_fact_is_rejected() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))
    with pytest.raises(PresentationRejected, match="unknown fact"):
        validate_presentation(json.dumps({"executive_overview": {"text": "x", "fact_ids": ["invented-1"]}}), pack)
    with pytest.raises(PresentationRejected, match="cites no fact"):
        validate_presentation(json.dumps({"executive_overview": {"text": "x", "fact_ids": []}}), pack)
    with pytest.raises(PresentationRejected, match="may not be written"):
        validate_presentation(json.dumps({"sections": {"amounts_quantities": []}}), pack)


async def test_the_model_sees_the_fact_pack_not_the_document() -> None:
    db = FakeDb()
    pack, evidence = await _pack(db, await _seed(db))
    model = FakeModel(json.dumps({}))

    await compose(pack, evidence, model=model)

    _, user = model.calls[0]
    assert "LLM SUMMARY" not in user and "REPLY POINT" not in user
    assert "Total basic amount 22140168\nOverheads" not in user
    # Facts and the sentences that state them - never a page's whole text.
    assert evidence.page_text(1) not in user


# --- 12/13. legacy summary and fact authority ---------------------------------------------------


async def test_generation_never_touches_the_legacy_summary_or_reads_it() -> None:
    db = FakeDb()
    document_id = await _seed(db)

    await GroundedSummaryService(db, use_default_model=False).generate(document_id, trigger="pipeline")
    stored = await db.documents.find_one({"_id": ObjectId(document_id)})

    assert stored["summary"] == "LLM SUMMARY: the Engineer approved INR 99,99,999 under Clause 99.9"
    assert stored["key_reply_points"] == ["REPLY POINT: dispute the soil data"]
    blob = json.dumps(stored["detailed_summary"], default=str) + (stored["short_summary"] or "")
    assert "99.9" not in blob and "LLM SUMMARY" not in blob and "REPLY POINT" not in blob
    history = await db[DOCUMENT_GROUNDED_SUMMARIES].find({"document_id": document_id}).to_list()
    assert len(history) == 1


BACKEND = Path(__file__).resolve().parents[1]
#: The only production modules that may read the new fields. A drafting,
#: chronology, claims or arbitration consumer reading them would start using a
#: derived view as evidence.
_ALLOWED_READERS = {
    "services/grounded_summary",
    "routers/document_grounded_summary.py",
    "models/grounded_summary.py",
}


def test_no_production_consumer_reads_the_grounded_summary_as_a_source() -> None:
    offenders = []
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND).as_posix()
        if "tests" in path.parts or any(relative.startswith(allowed) for allowed in _ALLOWED_READERS):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in {"detailed_summary", "short_summary"}:
                offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in {"detailed_summary", "short_summary"}:
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, f"grounded summary read outside its package: {offenders}"


# --- 14. tenant / RBAC ----------------------------------------------------------------------------


def _user(user_id: str, roles: List[str], org: Optional[str], projects: List[str] = ()) -> CurrentUser:
    return CurrentUser(
        id=user_id, username=user_id, email=f"{user_id}@example.com", roles=roles,
        organization_id=org, organizations=[org] if org else [], projects=list(projects),
    )


MEMBER_A = _user("u-a", ["projectuser"], ORG_A, [PROJECT_A])
ORG_ADMIN_B = _user("u-b", ["orgadmin"], ORG_B)
NO_VIEW_A = _user("u-noview", ["projectuser"], ORG_A, [PROJECT_A])
GRANTS = {MEMBER_A.id: {"dms.document.view"}, ORG_ADMIN_B.id: {"dms.document.view"}, NO_VIEW_A.id: set()}


@pytest.fixture
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


async def test_summary_inherits_the_document_gate(_authorization_seams) -> None:
    db = FakeDb()
    await db.projects.insert_one({"_id": PROJECT_A, "organization_id": ORG_A})
    document_id = await _seed(db)
    service = GroundedSummaryService(db, use_default_model=False)
    await service.generate(document_id, trigger="pipeline")

    view = await service.read_for_user(document_id, MEMBER_A, ActiveScope(db, MEMBER_A, ORG_A, PROJECT_A))
    assert view["detailed_summary"] and view["summary_status"] in {SUMMARY_CURRENT, SUMMARY_DEGRADED}

    for user in (ORG_ADMIN_B, NO_VIEW_A):
        with pytest.raises(Exception) as refused:
            await service.read_for_user(document_id, user, ActiveScope(db, user, ORG_A, PROJECT_A))
        assert getattr(refused.value, "status_code", None) == 403, refused.value


# --- 15. deleted / duplicate ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"duplicate_status": "duplicate"},
        {"lifecycle_state": "duplicate"},
    ],
)
async def test_quarantined_documents_never_serve_a_summary(fields: Dict[str, Any]) -> None:
    db = FakeDb()
    document_id = await _seed(db)
    service = GroundedSummaryService(db, use_default_model=False)
    await service.generate(document_id, trigger="pipeline")
    await db.documents.update_one({"_id": ObjectId(document_id)}, {"$set": fields})

    view = await service.view(await db.documents.find_one({"_id": ObjectId(document_id)}))
    regenerated = await service.generate(document_id, trigger="on_demand")

    assert view["summary_status"] == SUMMARY_WITHHELD
    assert view["detailed_summary"] is None and view["short_summary"] is None
    assert regenerated["status"] == SUMMARY_WITHHELD


async def test_a_deleted_document_is_not_summarised() -> None:
    db = FakeDb()
    document_id = await _seed(db, lifecycle_state="deleted")
    outcome = await GroundedSummaryService(db, use_default_model=False).generate(document_id, trigger="on_demand")
    assert outcome == {"status": SUMMARY_WITHHELD, "reason": "document_not_found"}


async def test_a_legacy_document_without_canonical_evidence_gets_no_grounded_summary() -> None:
    db = FakeDb()
    document_id = ObjectId()
    await db.documents.insert_one({"_id": document_id, "organization_id": ORG_A, "project_id": PROJECT_A,
                                   "processing_status": "metadata_extracted", "ocrText": "legacy text",
                                   "summary": "legacy summary"})
    service = GroundedSummaryService(db, use_default_model=False)

    outcome = await service.generate(str(document_id), trigger="on_demand")
    view = await service.view(await db.documents.find_one({"_id": document_id}))

    assert outcome["status"] == SUMMARY_UNAVAILABLE
    assert view["summary_status"] == SUMMARY_UNAVAILABLE and view["detailed_summary"] is None


# --- dates -----------------------------------------------------------------------------------------


def test_ambiguous_numeric_dates_are_not_normalised() -> None:
    mentions = {m.stated: m for m in find_dates("on 05.06.2024 and 22.06.2024 and 26 February 2023")}
    assert mentions["05.06.2024"].ambiguous
    assert not mentions["22.06.2024"].ambiguous
    assert mentions["26 February 2023"].readings[0].isoformat() == "2023-02-26"


async def test_an_ambiguous_date_is_uncertain_and_qualified() -> None:
    db = FakeDb()
    pages = [_page(1, PAGE_ONE + " The work front was handed over on 05.06.2023 after delay."), _page(2, PAGE_TWO)]
    pack, _ = await _pack(db, await _seed(db, pages=pages))

    ambiguous = [f for f in pack.by_type("date") if f.value == "05.06.2023"]
    assert ambiguous and ambiguous[0].verification_status == "uncertain"
    assert ambiguous[0].normalized_value is None
    assert any(f.type == "date_qualification" for f in pack.facts)


async def test_failure_is_recorded_and_never_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    document_id = await _seed(db)
    from rbac_backend.services.grounded_summary import service as service_module

    def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("renderer bug")

    monkeypatch.setattr(service_module, "build_fact_pack", _boom)
    outcome = await GroundedSummaryService(db, use_default_model=False).generate(document_id, trigger="pipeline")
    stored = await db.documents.find_one({"_id": ObjectId(document_id)})

    assert outcome["status"] == "failed"
    assert stored["summary_state"]["status"] == "failed"
    assert stored["processing_status"] == "metadata_extracted"


async def test_table_rows_and_headings_are_not_read_as_party_statements() -> None:
    db = FakeDb()
    pack, _ = await _pack(db, await _seed(db))

    statements = [f for f in pack.facts if f.type.endswith("_statement")]
    assert not [f for f in statements if 2 in f.page_numbers], [f.quote for f in statements]
    # The header block is split into its lines, not one run-on "sentence".
    assert not [f for f in pack.by_type("date") if f.value in {"24-12-2022", "03.02.2023"}]


# --- regressions from the 4299 local acceptance run (2026-10-16) ------------------------

TABLE_PAGE = (
    "Date Count Status\n8/10/2022 1 Idle\n9/10/2022 1 Idle\n10/13/2022 1 Idle\n"
    "B Monthly Rental 2,000,000 INR 10/30/2022 1 Idle\n"
    "S/N Description Unit Qty Rate in INR Amount in INR 1 Mobilisation Charges Nos 1 950,000.00 950,000\n"
)


async def test_table_rows_do_not_become_material_dates_or_false_amounts() -> None:
    db = FakeDb()
    pages = [_page(1, PAGE_ONE), _page(2, TABLE_PAGE)]
    pack, _ = await _pack(db, await _seed(db, pages=pages))

    assert not [f for f in pack.by_type("date") if 2 in f.page_numbers]
    amounts = {f.normalized_value for f in pack.by_type("amount")}
    assert 10 not in amounts and 1 not in amounts  # "INR 10/30/2022", "INR 1 Mobilisation"
    assert 2000000 in amounts  # "2,000,000 INR": figure before the marker


async def test_the_model_input_is_smaller_than_the_document_and_cites_only_citable_facts() -> None:
    db = FakeDb()
    pages = [_page(1, PAGE_ONE), _page(2, TABLE_PAGE * 40)]
    pack, evidence = await _pack(db, await _seed(db, pages=pages))
    model = FakeModel(json.dumps({}))

    await compose(pack, evidence, model=model)

    _, user = model.calls[0]
    assert len(user) < len(evidence.text)
    date_fact = pack.by_type("letter_date")[0]
    reference = pack.by_type("reference")[0]
    with pytest.raises(PresentationRejected, match="unknown fact"):
        validate_presentation(
            json.dumps({"executive_overview": {"text": "x", "fact_ids": [reference.id]}}), pack
        )
    assert date_fact.id in user and reference.id not in user


async def test_a_page_ocr_never_read_is_reported_as_unread_not_as_failed_checks() -> None:
    db = FakeDb()
    unread = ExtractedPage(
        number=2, text="", source=PageSource.EMPTY, status=PageStatus.OCR_DISABLED,
        classification=_classification(""), quality_verdict="fail", needs_review=True,
    )
    pack, _ = await _pack(db, await _seed(db, pages=[_page(1, PAGE_ONE), unread]))

    notes = [f.value for f in pack.by_type("page_qualification")]
    assert notes == ["Page 2 could not be read by extraction; its content is missing."]
