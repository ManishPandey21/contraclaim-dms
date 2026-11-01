"""A0b / D1: a system timestamp or an unrelated date is never an event date.

Owner rules (OD4 and the A0b brief):

* a missing event date stays missing (``None``); ``created_at``, upload
  timestamps and ``utcnow()`` never fill it;
* a document date is ``document_date``, not an event date, except for the one
  deterministic "letter / document issued" event, which may carry its source
  document's date and says so in ``metadata.date_role``;
* a referenced letter's date, or the first date in a body, is never the event
  date;
* day-first is the default, but ``03/04/2026`` is ambiguous: it is flagged
  ``ambiguous_dm`` and is never an unqualified ``exact`` date.

Allowed date sources per event, as implemented:

* Chronology Builder extraction (one entry per source document, the letter
  itself): the document's own ``date`` / ``document_date`` / ``letter_date``;
  never ``created_at``, never the body.
* Evidence-graph ingest document event: ``letter`` type only, from
  ``documents.date``; every other document class is undated.
* Register / hindrance / backfill projections: the register's own business
  date fields only; never ``created_at`` / ``upload_date`` / ``utcnow()``.

Every test here is clock-independent: none reads or patches the clock, and each
asserts ``None`` (or a fixed past date) rather than "not close to now".
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.models.chronology import (
    ChronologyDateType,
    ChronologyDecisionRequest,
    ChronologyExtractRequest,
    ChronologyVerificationStatus,
    MatterChronologyCreate,
    MatterChronologyEventUpdate,
)
from rbac_backend.models.evidence_registers import DrawingReferenceCreate
from rbac_backend.models.evidence_graph import ProjectEvent, ProjectEventCreate, ProjectEventType
from rbac_backend.services.evidence_graph_backfill_service import EvidenceGraphBackfillService
from rbac_backend.services.evidence_graph_service import EvidenceGraphService
from rbac_backend.services.evidence_register_service import EvidenceRegisterService
from rbac_backend.services.hindrance_register_service import timeline_fields
from rbac_backend.tests import test_chronology_event_authority as adapter_harness
from rbac_backend.tests.test_chronology_service import _VerifierPolicy, _svc, _user
from rbac_backend.tests.test_evidence_registers_backfill import _DB as _BackfillDB

UPLOADED_AT = datetime(2026, 9, 30, 11, 45)
DOCUMENT_DATE = datetime(2026, 3, 25)
REFERENCE_BODY = (
    "With reference to your letter dated 15.03.2026, access to the north abutment "
    "was not provided on 10.03.2026 and the works remain hindered."
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _extract(document: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    svc = _svc()
    await svc.db.documents.insert_one(
        {"_id": "doc-1", "organization_id": "org-A", "project_id": "proj-A", "filename": "letter.pdf", **document}
    )
    chronology = await svc.create_chronology(
        MatterChronologyCreate(
            organization_id="org-A",
            project_id="proj-A",
            title="Access chronology",
            chronology_type="eot_delay",
            party_perspective="claimant",
            selected_source_ids=["doc-1"],
        ),
        _user(),
    )
    extracted = await svc.extract_events(chronology["_id"], ChronologyExtractRequest(), _user())
    return svc, chronology, extracted["events"][0]


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("metadata") or {})


# --- 1. explicit event date ----------------------------------------------------


def test_explicit_event_date_survives_verification_unchanged() -> None:
    async def scenario() -> None:
        svc, chronology, event = await _extract({"date": DOCUMENT_DATE, "subject": "Access letter"})
        await svc.db.matter_chronology_events.update_one(
            {"_id": event["_id"]},
            {"$set": {"event_date": datetime(2026, 3, 10), "date_type": "exact", "metadata": {"date_role": "event_date"}}},
        )
        await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(), policy=_VerifierPolicy())
        project_event = next(iter(svc.db.project_events.docs.values()))
        assert project_event["event_date"] == datetime(2026, 3, 10)
        assert _metadata(project_event)["date_role"] == "event_date"

    _run(scenario())


# --- 2 / 9. document date and the letter-issued rule ---------------------------


def test_builder_letter_entry_uses_document_date_and_says_so() -> None:
    async def scenario() -> None:
        _, _, event = await _extract({"date": DOCUMENT_DATE, "subject": "Access letter", "summary": REFERENCE_BODY})
        assert event["event_date"] == DOCUMENT_DATE
        assert _metadata(event)["date_role"] == "document_date"
        assert "ambiguous_dm" not in (_metadata(event).get("date_flags") or [])

    _run(scenario())


def test_verified_letter_entry_projects_its_date_role_to_the_timeline() -> None:
    async def scenario() -> None:
        svc, chronology, event = await _extract({"date": DOCUMENT_DATE, "subject": "Access letter"})
        await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(), policy=_VerifierPolicy())
        project_event = next(iter(svc.db.project_events.docs.values()))
        assert project_event["event_date"] == DOCUMENT_DATE
        assert _metadata(project_event)["date_role"] == "document_date"

    _run(scenario())


def test_ingest_letter_event_uses_verified_document_date_with_role() -> None:
    async def scenario() -> None:
        svc = EvidenceGraphService(_svc().db)
        svc.audit = type("A", (), {"emit": staticmethod(lambda **_: asyncio.sleep(0))})()
        event = await svc._get_or_create_document_event(
            document_id="doc-1",
            document_data={"organization_id": "org-A", "project_id": "proj-A", "date": DOCUMENT_DATE, "created_at": UPLOADED_AT},
            parsed={"subject": "Access letter"},
            ai_extraction_id="ext-1",
            current_user=_user(),
        )
        assert event["event_type"] == ProjectEventType.LETTER.value
        assert event["event_date"] == DOCUMENT_DATE
        assert _metadata(event)["date_role"] == "document_date"

    _run(scenario())


def test_ingest_non_letter_event_does_not_take_the_document_date() -> None:
    """A minutes-of-meeting letter's date is not the meeting date."""

    async def scenario() -> None:
        svc = EvidenceGraphService(_svc().db)
        svc.audit = type("A", (), {"emit": staticmethod(lambda **_: asyncio.sleep(0))})()
        event = await svc._get_or_create_document_event(
            document_id="doc-1",
            document_data={"organization_id": "org-A", "project_id": "proj-A", "date": DOCUMENT_DATE},
            parsed={"subject": "Minutes of progress meeting", "document_class": "meeting_record"},
            ai_extraction_id="ext-1",
            current_user=_user(),
        )
        assert event["event_type"] == ProjectEventType.MEETING.value
        assert event.get("event_date") is None
        assert "source_missing" in _metadata(event)["date_flags"]

    _run(scenario())


def test_a_reviewer_entered_date_is_the_event_date_not_the_document_date() -> None:
    """Correcting the proposed date resolves it: role and ambiguity follow the edit."""

    async def scenario() -> None:
        svc, chronology, event = await _extract({"date": "03/04/2026", "subject": "Access letter"})
        updated = await svc.update_event(
            chronology["_id"], event["_id"], MatterChronologyEventUpdate(event_date=datetime(2026, 3, 10)), _user()
        )
        assert updated["event_date"] == datetime(2026, 3, 10)
        assert _metadata(updated)["date_role"] == "event_date"
        assert "ambiguous_dm" not in _metadata(updated)["date_flags"]
        # The idempotency hash the extractor relies on is not lost by the edit.
        assert _metadata(updated)["content_hash"] == _metadata(event)["content_hash"]

    _run(scenario())


# --- 3 / 10. referenced dates and the first body date --------------------------


def test_referenced_letter_date_is_not_the_event_date() -> None:
    async def scenario() -> None:
        _, _, event = await _extract({"date": DOCUMENT_DATE, "subject": "Access letter", "summary": REFERENCE_BODY})
        assert event["event_date"] != datetime(2026, 3, 15)

    _run(scenario())


def test_first_body_date_never_becomes_the_event_date() -> None:
    """No document date at all: the body's 15 March (a reference) must not be taken."""

    async def scenario() -> None:
        _, _, event = await _extract({"subject": "Access letter", "summary": REFERENCE_BODY, "content": REFERENCE_BODY})
        assert event.get("event_date") is None
        assert event["date_type"] == ChronologyDateType.UNDATED.value
        assert event.get("date_text") in (None, "")

    _run(scenario())


# --- 4 / 5 / 6 / 7. system timestamps and the missing date ---------------------


def test_upload_timestamp_and_created_at_are_never_builder_event_dates() -> None:
    async def scenario() -> None:
        _, _, event = await _extract(
            {"subject": "Undated letter", "created_at": UPLOADED_AT, "upload_date": UPLOADED_AT, "uploadedAt": UPLOADED_AT}
        )
        assert event.get("event_date") is None
        assert event["date_type"] == ChronologyDateType.UNDATED.value
        assert event["date_type"] != ChronologyDateType.EXACT.value

    _run(scenario())


def test_verifying_an_undated_event_publishes_it_undated() -> None:
    """Verified means the event was reviewed; it does not mean the date is known."""

    async def scenario() -> None:
        svc, chronology, event = await _extract({"subject": "Undated letter", "created_at": UPLOADED_AT})
        verified = await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(), policy=_VerifierPolicy())
        assert verified["verification_status"] == ChronologyVerificationStatus.VERIFIED
        project_event = next(iter(svc.db.project_events.docs.values()))
        assert project_event["event_date"] is None
        assert "source_missing" in _metadata(project_event)["date_flags"]

    _run(scenario())


def test_ingest_letter_without_a_document_date_stays_undated() -> None:
    async def scenario() -> None:
        svc = EvidenceGraphService(_svc().db)
        svc.audit = type("A", (), {"emit": staticmethod(lambda **_: asyncio.sleep(0))})()
        event = await svc._get_or_create_document_event(
            document_id="doc-1",
            document_data={"organization_id": "org-A", "project_id": "proj-A", "created_at": UPLOADED_AT},
            parsed={"subject": "Access letter"},
            ai_extraction_id="ext-1",
            current_user=_user(),
        )
        assert event.get("event_date") is None

    _run(scenario())


def test_backfill_never_dates_an_event_from_created_at_or_upload_date() -> None:
    async def scenario() -> None:
        db = _BackfillDB()
        await db.documents.insert_one(
            {"_id": "doc-1", "organization_id": "org-A", "project_id": "proj-A", "subject": "Letter",
             "created_at": UPLOADED_AT, "upload_date": UPLOADED_AT}
        )
        await db.claims.insert_one(
            {"_id": "claim-1", "organization_id": "org-A", "project_id": "proj-A", "claim_ref": "C-1", "created_at": UPLOADED_AT}
        )
        await EvidenceGraphBackfillService(db).run({"organization_id": "org-A"}, current_user=_user(), dry_run=False)
        events = {row["source_entity_id"]: row for row in db.project_events.docs.values()}
        assert events["doc-1"]["event_date"] is None
        assert events["claim-1"]["event_date"] is None

    _run(scenario())


def test_backfill_letter_keeps_its_document_date_with_role() -> None:
    async def scenario() -> None:
        db = _BackfillDB()
        await db.documents.insert_one(
            {"_id": "doc-1", "organization_id": "org-A", "project_id": "proj-A", "subject": "Letter",
             "date": DOCUMENT_DATE, "created_at": UPLOADED_AT}
        )
        await EvidenceGraphBackfillService(db).run({"organization_id": "org-A"}, current_user=_user(), dry_run=False)
        event = next(iter(db.project_events.docs.values()))
        assert event["event_date"] == DOCUMENT_DATE
        assert _metadata(event)["date_role"] == "document_date"

    _run(scenario())


def test_register_drawing_without_dates_is_undated_not_recorded_at() -> None:
    async def scenario() -> None:
        db = _BackfillDB()
        await EvidenceRegisterService(db).create_drawing_reference(
            DrawingReferenceCreate(organization_id="org-A", project_id="proj-A", drawing_number="DWG-1", title="GA"),
            _user(),
        )
        event = next(iter(db.project_events.docs.values()))
        assert event["event_date"] is None
        assert "source_missing" in _metadata(event)["date_flags"]

    _run(scenario())


def test_project_event_model_accepts_a_missing_event_date() -> None:
    created = ProjectEventCreate(title="Access not provided")
    assert created.event_date is None
    stored = ProjectEvent(**created.model_dump()).model_dump(by_alias=True)
    assert stored["event_date"] is None
    # The router serialises rows through the model; an undated row must not 500.
    assert ProjectEvent(**{**stored, "event_date": None}).event_date is None


# --- 8. ambiguous dd/mm ----------------------------------------------------------


@pytest.mark.parametrize("raw", ["03/04/2026", "03.04.2026", "03-04-2026"])
def test_ambiguous_day_month_is_flagged_and_not_exact(raw: str) -> None:
    async def scenario() -> None:
        _, _, event = await _extract({"date": raw, "subject": "Access letter"})
        assert "ambiguous_dm" in _metadata(event)["date_flags"]
        assert event["date_type"] != ChronologyDateType.EXACT.value
        # Day-first default is still how it reads, so it is displayable.
        assert event["event_date"] == datetime(2026, 4, 3)
        assert event["verification_status"] == ChronologyVerificationStatus.NEEDS_REVIEW.value

    _run(scenario())


@pytest.mark.parametrize("raw", ["25/03/2026", "25.03.2026", "2026-03-25", "05/05/2026"])
def test_unambiguous_day_month_is_exact_and_unflagged(raw: str) -> None:
    async def scenario() -> None:
        _, _, event = await _extract({"date": raw, "subject": "Access letter"})
        assert "ambiguous_dm" not in (_metadata(event).get("date_flags") or [])
        assert event["date_type"] == ChronologyDateType.EXACT.value

    _run(scenario())


# --- 15. arbitration consumer ----------------------------------------------------


def test_arbitration_adapter_keeps_an_undated_event_without_inventing_a_date() -> None:
    rows = adapter_harness._adapter_rows(
        adapter_harness._event(event_date=None, created_at=UPLOADED_AT),
        [],
    )
    assert rows, "an undated verified event still enters the chronology matrix"
    assert rows[0].get("date") is None


# --- 16. hindrance sync ------------------------------------------------------------


def test_hindrance_projection_keeps_a_real_start_date() -> None:
    fields = timeline_fields({"_id": "h-1", "start_date": datetime(2026, 2, 1), "created_at": UPLOADED_AT})
    assert fields["event_date"] == datetime(2026, 2, 1)


def test_hindrance_projection_without_start_date_is_undated() -> None:
    fields = timeline_fields({"_id": "h-1", "created_at": UPLOADED_AT})
    assert fields["event_date"] is None
    assert "source_missing" in fields["metadata"]["date_flags"]


# --- stale date_text after a date edit ---------------------------------------------
#
# The ledger, exports and the timeline projection prefer `date_text` over the
# structured date, so a `date_text` left behind by an `event_date` edit would
# keep presenting the old date as the event's date.

DATE_A_TEXT = "25/03/2026"
DATE_A = datetime(2026, 3, 25)
DATE_B = datetime(2026, 3, 10)


async def _verified_at_a() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    svc, chronology, event = await _extract({"date": DATE_A_TEXT, "subject": "Access letter"})
    assert event["event_date"] == DATE_A and event["date_text"] == DATE_A_TEXT
    await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(), policy=_VerifierPolicy())
    return svc, chronology, event


def test_changing_only_event_date_retires_the_old_date_text() -> None:
    async def scenario() -> dict[str, Any]:
        svc, chronology, event = await _verified_at_a()
        updated = await svc.update_event(
            chronology["_id"], event["_id"], MatterChronologyEventUpdate(event_date=DATE_B), _user()
        )
        assert updated["event_date"] == DATE_B
        assert updated.get("date_text") != DATE_A_TEXT

        # Re-verified, the arbitration ledger cites B, never the stale A text.
        await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(), policy=_VerifierPolicy())
        context = await svc.pleading_context(chronology["_id"])
        citation = context.source_ledger[0]["citation"]
        assert citation.startswith("2026-03-10")
        assert DATE_A_TEXT not in citation

        project_event = next(iter(svc.db.project_events.docs.values()))
        assert project_event["event_date"] == DATE_B
        assert _metadata(project_event).get("date_text") != DATE_A_TEXT

        return await svc.get_event(chronology["_id"], event["_id"])

    current = _run(scenario())
    # The arbitration chronology adapter (its own event loop) reads the edited row.
    scoped = {**current, "organization_id": "org-1", "project_id": "project-1", "verification_status": "approved"}
    rows = adapter_harness._adapter_rows(adapter_harness._event(**scoped), [adapter_harness._doc("metadata_extracted")])
    assert rows[0]["date"] == DATE_B


def test_changing_event_date_and_date_text_together_keeps_both() -> None:
    async def scenario() -> None:
        svc, chronology, event = await _verified_at_a()
        updated = await svc.update_event(
            chronology["_id"], event["_id"], MatterChronologyEventUpdate(event_date=DATE_B, date_text="10.03.2026"), _user()
        )
        assert updated["event_date"] == DATE_B
        assert updated["date_text"] == "10.03.2026"

    _run(scenario())


def test_clearing_event_date_leaves_no_stale_date_text() -> None:
    async def scenario() -> None:
        svc, chronology, event = await _verified_at_a()
        updated = await svc.update_event(
            chronology["_id"], event["_id"], MatterChronologyEventUpdate(event_date=None), _user()
        )
        assert updated.get("event_date") is None
        assert updated.get("date_text") is None
        assert updated.get("date_type") == ChronologyDateType.UNDATED.value
        assert "source_missing" in _metadata(updated)["date_flags"]

    _run(scenario())


def test_an_unrelated_edit_keeps_date_text() -> None:
    async def scenario() -> None:
        svc, chronology, event = await _verified_at_a()
        updated = await svc.update_event(
            chronology["_id"], event["_id"], MatterChronologyEventUpdate(title="Access letter (renamed)"), _user()
        )
        assert updated["event_date"] == DATE_A
        assert updated["date_text"] == DATE_A_TEXT
        assert _metadata(updated)["date_role"] == "document_date"

    _run(scenario())
