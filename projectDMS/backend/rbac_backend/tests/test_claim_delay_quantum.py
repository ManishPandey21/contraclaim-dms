"""Delay Lab and Quantum Builder.

Held to: registers stay authoritative and untouched; an overlap is never
deducted until a person decides its treatment with a basis; days already
granted are read, not typed; a cost line is not supported until its proof is
linked, and prolongation only counts inside periods decided as compensable.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.models.claim_delay import FormulaInput, QuantumLine, QuantumRecord
from rbac_backend.services.claim_bench import BoardVersionRequest, ClaimBenchService, ElementDecisionRequest, HeadPatchRequest
from rbac_backend.services.claim_delay_lab import (
    ClaimDelayLab,
    DelaySettingsRequest,
    EventDecisionRequest,
    LinkCreateRequest,
    CostBasisRequest,
    CostReviewRequest,
    LinkRemoveRequest,
    OverlapDecisionRequest,
    hindrance_snapshot,
    register_proposal,
)
from rbac_backend.services.claim_delay_engine import EMPLOYER_CONTRACTOR, overlap_identity
from rbac_backend.services.claim_quantum import ClaimQuantum, QuantumLineRequest, line_lock_reasons
from rbac_backend.tests.bench_fakes import FakeDB

ORG, PROJECT, CLAIM_ID, USER = "org-A", "proj-A", "claim-1", "user-1"
ON = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="primary", CLAIM_BENCH_CANARY_TENANT_IDS="")
# 4B-22 (D-16): an overlap is identified by its kind, dates and events.
OVERLAP = overlap_identity(EMPLOYER_CONTRACTOR, date(2026, 3, 20), date(2026, 3, 31), ["E1", "C1"])


def _hindrance(_id: str, start: str, end: Optional[str], responsibility: str = "employer", critical: bool = True, **over: Any) -> dict:
    return {
        "_id": _id, "organization_id": ORG, "project_id": PROJECT, "hindrance_ref": f"HR-{_id}",
        "title": f"Event {_id}", "start_date": datetime.fromisoformat(start),
        "end_date": datetime.fromisoformat(end) if end else None,
        "responsibility": responsibility, "critical_path_impact": critical, "archived_at": None, **over,
    }


def _setup():
    db = FakeDB()
    db.projects.docs.append({"_id": PROJECT, "organization_id": ORG, "contractual_capacity": "contractor"})
    db.claims.docs.append(
        {"_id": CLAIM_ID, "title": "Delayed handover", "type": "eot", "status": "submitted",
         "event_date": datetime(2026, 3, 1), "organization_id": ORG, "project_id": PROJECT, "currency": "INR", "bench": None,
         # Under the contract its determinations belong to (4B-23: no cross-contract evidence).
         "contract_id": "primary",
         # The documents quantum lines cite are linked to the claim (4B-24 re-checks this).
         "linked_document_ids": ["doc-1", "doc-2", "x", "y"]}
    )
    db.delay_events.docs.extend(
        [
            _hindrance("E1", "2026-03-01", "2026-03-31"),
            _hindrance("C1", "2026-03-20", "2026-04-10", "contractor"),
            _hindrance("OPEN", "2026-05-01", None),
            _hindrance("FAR", "2026-01-01", "2026-01-05", project_id="proj-B"),
        ]
    )
    bench = ClaimBenchService(db, config=ON)
    return db, bench, ClaimDelayLab(db, bench), ClaimQuantum(db, bench)


async def _claim(db: FakeDB) -> dict:
    return await db.claims.find_one({"_id": CLAIM_ID})


async def _version(db: FakeDB) -> int:
    return (await _claim(db))["bench"]["board_version"]


async def _link(db: FakeDB, lab: ClaimDelayLab, target_id: str, target_type: str = "hindrance", **kwargs: Any):
    record = await lab.load_target(target_type, target_id)
    return await lab.add_link(
        await _claim(db), "H1",
        LinkCreateRequest(board_version=await _version(db), target_type=target_type, target_id=target_id, **kwargs),
        record, USER,
    )


async def _decide(db: FakeDB, lab: ClaimDelayLab, event_id: str, owner: str, criticality: str = "critical"):
    return await lab.decide_event(
        await _claim(db), "H1", event_id,
        EventDecisionRequest(board_version=await _version(db), risk_owner=owner, criticality=criticality), USER,
    )


async def _two_events(db: FakeDB, bench: ClaimBenchService, lab: ClaimDelayLab):
    await bench.open(await _claim(db), USER)
    await _link(db, lab, "E1")
    await _link(db, lab, "C1")
    await _decide(db, lab, "E1", "employer")
    return await _decide(db, lab, "C1", "contractor")


def _refused(exc: pytest.ExceptionInfo, status: int, *fragments: str) -> None:
    assert exc.value.status_code == status
    for fragment in fragments:
        assert fragment in str(exc.value.detail)


# --------------------------------------------------------------------------- #
# Linking
# --------------------------------------------------------------------------- #
async def test_linking_pins_the_record_proposes_what_the_register_says_and_writes_nothing_to_it() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    before = await db.delay_events.find_one({"_id": "E1"})
    view = await _link(db, lab, "E1")
    assert await db.delay_events.find_one({"_id": "E1"}) == before
    (event,) = view.events
    assert event.label == "Hindrance HR-E1" and event.days == 31 and event.revision_current
    assert event.decision.proposed_risk_owner == "employer" and event.decision.proposed_criticality == "critical"
    assert event.decision.proposed_by == "register"
    # A proposal is not a decision: nothing is counted yet.
    assert event.decision.risk_owner is None and not event.counted and view.employer_critical_days == 0
    stored = await db.claim_head_links.find_one({"target_id": "E1"})
    assert stored["status"] == "active" and stored["snapshot"]["start_date"] == datetime(2026, 3, 1)
    assert len(stored["target_revision"]) == 64


def test_the_register_proposal_never_decides_a_concurrent_or_unreviewed_event() -> None:
    for responsibility in ("concurrent", "under_review"):
        proposal = register_proposal(hindrance_snapshot(_hindrance("X", "2026-03-01", "2026-03-02", responsibility, False)))
        assert proposal.proposed_risk_owner is None and proposal.proposed_criticality == "unproven"
        assert proposal.risk_owner is None and proposal.criticality is None


async def test_only_a_record_of_the_claims_own_project_can_be_linked_and_only_once() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as other_project:
        await _link(db, lab, "FAR")
    _refused(other_project, 422, "claim's own project")
    await _link(db, lab, "E1")
    with pytest.raises(HTTPException) as twice:
        await _link(db, lab, "E1")
    _refused(twice, 409, "already linked")
    assert await db.claim_head_links.count_documents({}) == 1
    with pytest.raises(HTTPException) as unknown_type:
        await lab.load_target("variation", "v1")
    _refused(unknown_type, 422)


async def test_an_archived_hindrance_cannot_be_linked() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    db.delay_events.docs.append(_hindrance("OLD", "2026-02-01", "2026-02-05", archived_at=datetime(2026, 2, 10)))
    with pytest.raises(HTTPException) as exc:
        await _link(db, lab, "OLD")
    _refused(exc, 422, "archived")


async def test_a_stale_board_version_leaves_no_link_behind() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    record = await lab.load_target("hindrance", "E1")
    with pytest.raises(HTTPException) as exc:
        await lab.add_link(
            await _claim(db), "H1", LinkCreateRequest(board_version=99, target_type="hindrance", target_id="E1"), record, USER
        )
    _refused(exc, 409)
    assert await db.claim_head_links.count_documents({}) == 0


# --------------------------------------------------------------------------- #
# Events, overlaps, net extension
# --------------------------------------------------------------------------- #
async def test_an_overlap_gives_a_range_until_its_treatment_is_decided_with_a_basis() -> None:
    db, bench, lab, _ = _setup()
    view = await _two_events(db, bench, lab)
    assert view.employer_critical_days == 31 and view.contractor_critical_days == 22 and view.contractor_only_days == 10
    (overlap,) = view.overlaps
    assert overlap.overlap_id == OVERLAP and overlap.days == 12 and overlap.finding is None
    assert overlap.employer_event_ids == ["E1"] and overlap.contractor_event_ids == ["C1"]
    assert (view.net.low, view.net.high, view.net.days, view.net.settled) == (19, 31, None, False)
    assert any("1 overlap" in blocker for blocker in view.blockers)

    with pytest.raises(HTTPException) as no_basis:
        await lab.decide_overlap(
            await _claim(db), "H1", OVERLAP,
            OverlapDecisionRequest(board_version=await _version(db), treatment="eot_granted_no_cost", basis="ok"), USER,
        )
    _refused(no_basis, 422, "State the basis")

    decided = await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(
            board_version=await _version(db), treatment="eot_granted_no_cost",
            basis="Sub-Clause 8.4; concurrent delay: time without cost.",
        ),
        USER,
    )
    assert decided.net.days == 31 and decided.net.settled and decided.overlaps[0].days_to_eot == 12
    assert decided.overlaps[0].finding.decided_by == USER
    # Time is given; cost is not: only the 19 non-concurrent days are compensable.
    assert [(p.start_date.date(), p.end_date.date(), p.days) for p in decided.compensable_periods] == [
        (date(2026, 3, 1), date(2026, 3, 19), 19)
    ]


async def test_apportionment_needs_its_days_and_cannot_exceed_the_overlap() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    for days in (None, 13):
        with pytest.raises(HTTPException) as exc:
            await lab.decide_overlap(
                await _claim(db), "H1", OVERLAP,
                OverlapDecisionRequest(board_version=await _version(db), treatment="apportioned", days_to_eot=days,
                                       basis="Apportioned on the dominant cause."),
                USER,
            )
        _refused(exc, 422, "0 to 12")
    view = await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment="apportioned", days_to_eot=5,
                               basis="Apportioned on the dominant cause."),
        USER,
    )
    assert view.net.days == 24 and view.net.overlap_days_to_eot == 5


async def test_a_finding_does_not_survive_the_overlap_it_was_made_for() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment="no_eot", basis="Contractor delay dominant throughout."),
        USER,
    )
    # The Contractor's event is found not to have been critical after all: there is no overlap now.
    view = await _decide(db, lab, "C1", "contractor", "not_critical")
    assert view.overlaps == [] and view.net.days == 31
    assert (await _claim(db))["bench"]["heads"][0]["delay"]["findings"] == {}
    with pytest.raises(HTTPException) as exc:
        await lab.decide_overlap(
            await _claim(db), "H1", OVERLAP,
            OverlapDecisionRequest(board_version=await _version(db), treatment="no_eot", basis="Contractor delay dominant."),
            USER,
        )
    _refused(exc, 404)


async def test_an_open_ended_event_needs_the_date_the_analysis_runs_to() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    await _link(db, lab, "OPEN")
    view = await _decide(db, lab, "OPEN", "employer")
    assert view.events[0].open_ended and view.events[0].days is None and view.employer_critical_days == 0
    assert any("set the date the analysis runs to" in blocker for blocker in view.blockers)
    dated = await lab.set_settings(
        await _claim(db), "H1",
        DelaySettingsRequest(board_version=await _version(db), as_of_date=datetime(2026, 5, 10), method="windows"), USER,
    )
    assert dated.employer_critical_days == 10 and dated.net.days == 10 and dated.blockers == []


async def test_methods_the_product_cannot_support_are_refused_with_the_reason() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as exc:
        await lab.set_settings(
            await _claim(db), "H1", DelaySettingsRequest(board_version=await _version(db), method="time_impact_analysis"), USER
        )
    _refused(exc, 422, "programme network")
    assert "time_impact_analysis" in (await lab.view(await _claim(db), "H1")).unavailable_methods


async def test_neutral_time_needs_a_stated_basis() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    db.delay_events.docs.append(_hindrance("N1", "2026-06-01", "2026-06-10", "neutral"))
    await _link(db, lab, "N1")
    await _decide(db, lab, "N1", "neutral")
    view = await lab.set_settings(
        await _claim(db), "H1", DelaySettingsRequest(board_version=await _version(db), method="windows", include_neutral=True), USER
    )
    assert view.net.days == 10 and any("neutral events" in blocker for blocker in view.blockers)
    with_basis = await lab.set_settings(
        await _claim(db), "H1",
        DelaySettingsRequest(board_version=await _version(db), neutral_basis="Sub-Clause 19.4: force majeure gives time."), USER,
    )
    assert with_basis.blockers == []


# --------------------------------------------------------------------------- #
# Days already granted
# --------------------------------------------------------------------------- #
def _determination(
    db: FakeDB, _id: str, items: list, status: str = "granted", *, frozen: bool = True,
    supersedes: list | None = None, on: datetime = datetime(2026, 6, 1),
) -> None:
    """A determination as the key-date register stores it: frozen once issued, items keyed by milestone."""
    db.key_date_eot_determinations.docs.append(
        {"_id": _id, "organization_id": ORG, "project_id": PROJECT, "contract_id": "primary",
         "determination_reference": f"DET-{_id}", "determination_date": on, "status": status,
         "frozen_at": on if frozen else None, "supersedes_determination_ids": list(supersedes or [])}
    )
    for item in items:
        granting = item.get("determination_result") in ("granted", "partially_granted")
        db.key_date_eot_determination_items.docs.append(
            {"determination_id": _id, "key_date_id": f"kd-{item['milestone_ref']}",
             "eot_granted_date": datetime(2027, 1, 1) if granting else None, **item}
        )


async def test_days_already_granted_are_read_from_the_linked_determination() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "partially_granted"}])
    view = await _link(db, lab, "D1", "eot_determination")
    (record,) = view.records
    assert record.label == "EOT determination DET-D1" and record.granted_days == 10 and record.counted
    assert view.net.previously_granted == 10 and (view.net.low, view.net.high) == (9, 21)


async def test_a_determination_over_several_milestones_is_not_counted_until_one_is_named() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D2", [
        {"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"},
        {"milestone_ref": "KD-2", "granted_extension_days": 30, "determination_result": "granted"},
    ])
    view = await _link(db, lab, "D2", "eot_determination")
    assert view.net.previously_granted == 0 and not view.records[0].counted
    assert "several milestones" in view.records[0].not_counted_reason
    await lab.remove_link(await _claim(db), "H1", view.records[0].link_id, LinkRemoveRequest(board_version=await _version(db)), USER)
    named = await _link(db, lab, "D2", "eot_determination", milestone_ref="KD-2")
    assert named.net.previously_granted == 30 and named.records[0].milestone_ref == "KD-2"
    with pytest.raises(HTTPException) as exc:
        _determination(db, "D3", [{"milestone_ref": "KD-1", "granted_extension_days": 1, "determination_result": "granted"}])
        await _link(db, lab, "D3", "eot_determination", milestone_ref="KD-9")
    _refused(exc, 422, "no item for milestone KD-9")


@pytest.mark.parametrize("result, status", [("rejected", "rejected"), ("pending", "pending"), ("granted", "under_review")])
async def test_a_determination_that_granted_nothing_or_is_not_final_is_not_counted(result, status) -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": result}], status)
    view = await _link(db, lab, "D1", "eot_determination")
    assert view.net.previously_granted == 0 and not view.records[0].counted and view.records[0].not_counted_reason


# --------------------------------------------------------------------------- #
# 4B-21 (D-15, C-01): the key-date register's own rule decides what was granted
# --------------------------------------------------------------------------- #
def _register_governing(db: FakeDB) -> dict:
    """What the register itself says governs each milestone, by the register's code path."""
    from rbac_backend.services.key_date_revision_service import KeyDateRevisionService

    determinations = []
    for row in db.key_date_eot_determinations.docs:
        determinations.append(
            {**row, "items": [item for item in db.key_date_eot_determination_items.docs if item["determination_id"] == row["_id"]]}
        )
    effective = {"kd-KD-1": None, "kd-KD-2": None}
    governing = {"kd-KD-1": None, "kd-KD-2": None}
    KeyDateRevisionService._replay_determinations(determinations, effective, governing)
    return {key: (row or {}).get("_id") for key, row in governing.items()}


async def test_a_superseding_determination_is_the_one_that_counts() -> None:
    """C-01: D1 grants 10; D2 (status "superseded" in the register, meaning it is the operative
    one) supersedes D1 and grants 15. The register says 15; the Lab said 10."""
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"}])
    _determination(
        db, "D2", [{"milestone_ref": "KD-1", "granted_extension_days": 15, "determination_result": "granted"}],
        "superseded", supersedes=["D1"], on=datetime(2026, 8, 1),
    )
    await _link(db, lab, "D1", "eot_determination")
    view = await _link(db, lab, "D2", "eot_determination")
    counted = {record.label: (record.counted, record.not_counted_reason) for record in view.records}
    assert counted["EOT determination DET-D2"] == (True, None)
    assert counted["EOT determination DET-D1"][0] is False
    assert "supersedes" in counted["EOT determination DET-D1"][1]
    assert view.net.previously_granted == 15
    # The same function the register replays with names the same governing determination.
    assert _register_governing(db)["kd-KD-1"] == "D2"


async def test_a_draft_determination_is_not_counted() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"}], frozen=False)
    view = await _link(db, lab, "D1", "eot_determination")
    assert view.net.previously_granted == 0 and "not frozen" in view.records[0].not_counted_reason
    assert _register_governing(db)["kd-KD-1"] is None


async def test_an_under_review_determination_granting_five_days_is_not_counted() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 5, "determination_result": "granted"}], "under_review")
    view = await _link(db, lab, "D1", "eot_determination")
    assert view.net.previously_granted == 0 and "not final" in view.records[0].not_counted_reason


async def test_a_later_determination_on_the_same_milestone_governs_and_the_earlier_is_not_summed() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"}])
    _determination(db, "D2", [{"milestone_ref": "KD-1", "granted_extension_days": 25, "determination_result": "granted"}], on=datetime(2026, 9, 1))
    await _link(db, lab, "D1", "eot_determination")
    view = await _link(db, lab, "D2", "eot_determination")
    assert view.net.previously_granted == 25
    assert _register_governing(db)["kd-KD-1"] == "D2"


async def test_freezing_or_superseding_a_linked_determination_marks_the_link_stale() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    _determination(db, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"}], frozen=False)
    view = await _link(db, lab, "D1", "eot_determination")
    assert view.records[0].revision_current and view.stale_links == 0
    db.key_date_eot_determinations.docs[0]["frozen_at"] = datetime(2026, 6, 2)
    after_freeze = await lab.view(await _claim(db), "H1")
    assert not after_freeze.records[0].revision_current and after_freeze.stale_links == 1

    db2, bench2, lab2, _ = _setup()
    await _two_events(db2, bench2, lab2)
    _determination(db2, "D1", [{"milestone_ref": "KD-1", "granted_extension_days": 10, "determination_result": "granted"}])
    linked = await _link(db2, lab2, "D1", "eot_determination")
    assert linked.records[0].revision_current and linked.net.previously_granted == 10
    _determination(
        db2, "D2", [{"milestone_ref": "KD-1", "granted_extension_days": 15, "determination_result": "granted"}],
        supersedes=["D1"], on=datetime(2026, 8, 1),
    )
    superseded = await lab2.view(await _claim(db2), "H1")
    assert not superseded.records[0].revision_current and superseded.stale_links == 1


# --------------------------------------------------------------------------- #
# Pinned revisions
# --------------------------------------------------------------------------- #
async def test_a_changed_hindrance_is_flagged_and_the_assessment_stays_on_what_was_linked() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    await db.delay_events.update_one({"_id": "E1"}, {"$set": {"end_date": datetime(2026, 4, 30)}})
    view = await lab.view(await _claim(db), "H1")
    changed = next(event for event in view.events if event.event_id == "E1")
    assert not changed.revision_current and view.stale_links == 1
    assert view.employer_critical_days == 31  # still the linked version; nothing re-pointed on its own

    refreshed = await lab.remove_link(
        await _claim(db), "H1", changed.link_id, LinkRemoveRequest(board_version=await _version(db), refresh=True), USER
    )
    event = next(item for item in refreshed.events if item.event_id == "E1")
    assert event.revision_current and refreshed.stale_links == 0 and refreshed.employer_critical_days == 61
    assert event.decision.risk_owner == "employer"  # the person's decision is kept
    history = await db.claim_head_links.find({"target_id": "E1"}).to_list(length=10)
    assert sorted(doc["status"] for doc in history) == ["active", "removed"]
    old = next(doc for doc in history if doc["status"] == "removed")
    assert old["superseded_by_id"] and old["snapshot"]["end_date"] == datetime(2026, 3, 31)


async def test_removing_a_link_removes_its_decision_and_keeps_the_link_as_history() -> None:
    db, bench, lab, _ = _setup()
    view = await _two_events(db, bench, lab)
    link_id = next(event.link_id for event in view.events if event.event_id == "C1")
    after = await lab.remove_link(await _claim(db), "H1", link_id, LinkRemoveRequest(board_version=await _version(db)), USER)
    assert [event.event_id for event in after.events] == ["E1"] and after.overlaps == []
    assert "C1" not in (await _claim(db))["bench"]["heads"][0]["delay"]["event_decisions"]
    assert (await db.claim_head_links.find_one({"_id": link_id}))["status"] == "removed"


async def test_removing_a_head_removes_its_links() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    await bench.remove_head(await _claim(db), "H1", BoardVersionRequest(board_version=await _version(db)), USER)
    assert await db.claim_head_links.count_documents({"status": "active"}) == 0
    assert await db.claim_head_links.count_documents({"status": "removed"}) == 2


# --------------------------------------------------------------------------- #
# The board's lock gate
# --------------------------------------------------------------------------- #
async def test_a_started_delay_assessment_must_be_finished_before_the_board_locks() -> None:
    db, bench, lab, _ = _setup()
    await _two_events(db, bench, lab)
    response = await bench.get(await _claim(db))
    for key in ("event", "notice", "contractual_basis", "causation", "quantum"):
        response = await bench.decide_element(
            await _claim(db), "H1", key, ElementDecisionRequest(board_version=response.bench.board_version, state="weak"), USER
        )
    response = await bench.patch_head(
        await _claim(db), "H1", HeadPatchRequest(board_version=response.bench.board_version, classification="excusable"), USER
    )
    assert not response.gate.can_lock
    assert any("choose the delay analysis method" in blocker for blocker in response.gate.blockers)
    assert any("1 overlap" in blocker for blocker in response.gate.blockers)
    with pytest.raises(HTTPException) as exc:
        await bench.lock(await _claim(db), BoardVersionRequest(board_version=response.bench.board_version), USER)
    _refused(exc, 409, "overlap")

    await lab.set_settings(await _claim(db), "H1", DelaySettingsRequest(board_version=await _version(db), method="windows"), USER)
    await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment="eot_granted_no_cost", basis="Sub-Clause 8.4, time without cost."),
        USER,
    )
    ready = await bench.get(await _claim(db))
    assert ready.gate.blockers == [] and ready.gate.can_lock
    locked = await bench.lock(await _claim(db), BoardVersionRequest(board_version=ready.bench.board_version), USER)
    assert locked.bench.stage == "locked"
    # A delay decision reopens a locked board.
    await _decide(db, lab, "C1", "contractor", "unproven")
    assert (await bench.get(await _claim(db))).bench.stage == "heads"


async def test_a_head_with_no_linked_events_owes_no_delay_assessment() -> None:
    db, bench, lab, _ = _setup()
    await bench.open(await _claim(db), USER)
    view = await lab.view(await _claim(db), "H1")
    assert view.blockers == [] and view.events == [] and view.net.days == 0


# --------------------------------------------------------------------------- #
# Quantum
# --------------------------------------------------------------------------- #
DOCS = {"doc-1", "doc-2"}


def _line(**over: Any) -> QuantumLineRequest:
    return QuantumLineRequest(
        **{"board_version": 0, "category": "direct", "description": "Idle plant at Km 14", "amount": 1000.0, **over}
    )


async def _add(db: FakeDB, quantum: ClaimQuantum, **over: Any):
    return await quantum.add_line(await _claim(db), "H1", _line(board_version=await _version(db), **over), DOCS, USER)


async def test_a_line_without_its_proof_is_locked_and_left_out_of_the_supported_total() -> None:
    db, bench, _, quantum = _setup()
    await bench.open(await _claim(db), USER)
    view = await _add(db, quantum)
    (line,) = view.lines
    assert line.locked and "Link at least one record" in line.lock_reasons[0]
    assert (view.claimed_total, view.supported_total, view.locked_total, view.locked_count) == (1000.0, 0.0, 1000.0, 1)
    supported = await quantum.update_line(
        await _claim(db), "H1", "Q1",
        _line(board_version=await _version(db), records=[QuantumRecord(document_id="doc-1")]), DOCS, USER,
    )
    assert not supported.lines[0].locked and supported.supported_total == 1000.0
    # The head's amount is now its build-up, and the register follows the head.
    stored = await _claim(db)
    assert stored["bench"]["heads"][0]["amount_claimed"] == 1000.0 and stored["amount_claimed"] == 1000.0


async def test_a_record_must_be_a_document_already_linked_to_the_claim() -> None:
    db, bench, _, quantum = _setup()
    await bench.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as exc:
        await _add(db, quantum, records=[QuantumRecord(document_id="someone-elses-doc")])
    _refused(exc, 422, "already linked to this claim")
    assert (await quantum.view(await _claim(db), "H1")).lines == []


async def test_prolongation_counts_only_inside_periods_decided_as_compensable() -> None:
    db, bench, lab, quantum = _setup()
    await _two_events(db, bench, lab)
    record = [QuantumRecord(document_id="doc-1")]
    inside = await _add(db, quantum, category="prolongation", period_start=datetime(2026, 3, 1),
                        period_end=datetime(2026, 3, 19), records=record)
    assert not inside.lines[0].locked
    spilling = await _add(db, quantum, category="prolongation", period_start=datetime(2026, 3, 15),
                          period_end=datetime(2026, 3, 25), records=record)
    reasons = spilling.lines[1].lock_reasons
    assert spilling.lines[1].locked and reasons == [
        "6 of the 11 days in this period are not in a period decided as compensable "
        "in the delay assessment (Employer critical delay, not concurrent or decided as carrying cost)."
    ]
    # Deciding that the overlap carries cost is not enough (D-16, AQ-4: no automatic money).
    await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment="eot_granted_with_cost",
                               basis="Costs separated: Employer delay alone caused them."),
        USER,
    )
    assert (await quantum.view(await _claim(db), "H1")).lines[1].locked
    # The cost basis, recorded by one person ...
    recorded = await lab.record_cost_basis(
        await _claim(db), "H1", OVERLAP,
        CostBasisRequest(board_version=await _version(db), contractual_basis="Sub-Clause 8.4 with 8.5 cost.",
                         causation="Idle plant stood only for want of access.", evidence_document_ids=["doc-1"]),
        USER, DOCS,
    )
    assert (await quantum.view(await _claim(db), "H1")).lines[1].locked
    assert "reviewer" in recorded.overlaps[0].cost_status
    # ... and reviewed by another, unlocks it, with no change to the line.
    reviewed = await lab.review_cost_basis(
        await _claim(db), "H1", OVERLAP, CostReviewRequest(board_version=await _version(db)), "determiner-1"
    )
    assert reviewed.overlaps[0].cost_established and reviewed.overlaps[0].cost_status is None
    assert not (await quantum.view(await _claim(db), "H1")).lines[1].locked


@pytest.mark.parametrize(
    "line, fragments",
    [
        (QuantumLine(line_id="Q", category="prolongation", description="d", amount=1), ["Link at least one record", "State the period"]),
        (QuantumLine(line_id="Q", category="prolongation", description="d", amount=1, period_start=datetime(2026, 3, 9),
                     period_end=datetime(2026, 3, 1), records=[QuantumRecord(document_id="x")]), ["ends before it starts"]),
        (QuantumLine(line_id="Q", category="overhead_formula", description="d", amount=1, period_start=datetime(2026, 3, 1),
                     period_end=datetime(2026, 3, 5), records=[QuantumRecord(document_id="x")]),
         ["Choose the formula", "record of actual loss"]),
        (QuantumLine(line_id="Q", category="loss_of_profit", description="d", amount=1, records=[QuantumRecord(document_id="x")]),
         ["available and was declined"]),
        (QuantumLine(line_id="Q", category="interest", description="d", amount=1), ["basis for interest"]),
        (QuantumLine(line_id="Q", category="disruption", description="d", amount=1), ["Link at least one record"]),
    ],
)
def test_each_category_names_exactly_what_would_unlock_it(line: QuantumLine, fragments: list) -> None:
    allowed = {date(2026, 3, day) for day in range(1, 6)}
    reasons = line_lock_reasons(line, allowed)
    assert len(reasons) == len(fragments)
    for fragment in fragments:
        assert any(fragment in reason for reason in reasons)


def test_a_formula_line_unlocks_only_with_actual_loss_shown() -> None:
    allowed = {date(2026, 3, day) for day in range(1, 6)}
    line = QuantumLine(
        line_id="Q", category="overhead_formula", description="Head office overheads", amount=5.0, formula="hudson",
        period_start=datetime(2026, 3, 1), period_end=datetime(2026, 3, 5),
        formula_inputs={
            "tender_percentage": FormulaInput(value="5", document_id="x"),
            "contract_sum": FormulaInput(value="1000000", document_id="x"),
            "contract_period_days": FormulaInput(value="350", document_id="x"),
        },
        records=[QuantumRecord(document_id="x", role="support")],
    )
    assert any("actual loss" in reason for reason in line_lock_reasons(line, allowed))
    line.records.append(QuantumRecord(document_id="y", role="actual_loss"))
    assert line_lock_reasons(line, allowed) == []
    assert line_lock_reasons(line, set()) != []  # and still only within compensable periods


async def test_invalid_lines_are_refused() -> None:
    db, bench, _, quantum = _setup()
    await bench.open(await _claim(db), USER)
    for over, fragment in [
        ({"category": "contingency"}, "Unknown cost category"),
        ({"formula": "hudson"}, "applies only to an overhead-by-formula line"),
        ({"category": "overhead_formula", "formula": "made_up"}, "Unknown formula"),
    ]:
        with pytest.raises(HTTPException) as exc:
            await _add(db, quantum, **over)
        _refused(exc, 422, fragment)
    with pytest.raises(HTTPException) as missing:
        await quantum.remove_line(await _claim(db), "H1", "Q9", await _version(db), USER)
    _refused(missing, 404)


async def test_removing_the_last_line_leaves_the_heads_amount_as_it_was() -> None:
    db, bench, _, quantum = _setup()
    await bench.open(await _claim(db), USER)
    await _add(db, quantum, amount=250.0)
    await _add(db, quantum, amount=750.0)
    assert (await _claim(db))["bench"]["heads"][0]["amount_claimed"] == 1000.0
    view = await quantum.remove_line(await _claim(db), "H1", "Q1", await _version(db), USER)
    assert view.claimed_total == 750.0 and [item.line.line_id for item in view.lines] == ["Q2"]


async def test_link_index_migration_keeps_one_active_link_and_free_history() -> None:
    from rbac_backend.migrations.v20261117_0001_claim_head_link_indexes import INDEXES, upgrade

    active = next(index for index in INDEXES if index["name"] == "claim_head_link_active_unique")
    assert active["unique"] and active["partialFilterExpression"] == {"status": "active"}
    dry = await upgrade(FakeDB(), True)
    assert dry.status == "dry_run" and len(dry.operations) == len(INDEXES) and len(dry.notices) == 1


# --------------------------------------------------------------------------- #
# 4B-22 (D-16): neutral concurrency, cost basis, reversed dates
# --------------------------------------------------------------------------- #
async def _cost_ready(db: FakeDB, bench: ClaimBenchService, lab: ClaimDelayLab, treatment: str = "not_true_concurrency"):
    await _two_events(db, bench, lab)
    return await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment=treatment,
                               basis="Not true concurrency: the Contractor delay was not critical in fact."),
        USER,
    )


def _cost_request(version: int, evidence=("doc-1",)) -> CostBasisRequest:
    return CostBasisRequest(board_version=version, contractual_basis="Sub-Clause 8.4 with Sub-Clause 8.5 cost.",
                            causation="The plant stood idle only for want of access.", evidence_document_ids=list(evidence))


async def test_not_true_concurrency_is_time_only_until_its_cost_basis_is_recorded_and_reviewed() -> None:
    db, bench, lab, _ = _setup()
    view = await _cost_ready(db, bench, lab)
    (overlap,) = view.overlaps
    assert overlap.finding.event_ids == ["C1", "E1"]  # the finding records the events it was made on
    assert view.net.days == 31  # time
    assert sum(period.days for period in view.compensable_periods) == 19  # no money for the 12 overlap days
    assert not overlap.cost_established and "contractual basis" in overlap.cost_status


async def test_a_cost_basis_needs_evidence_linked_to_the_claim_and_an_independent_reviewer() -> None:
    db, bench, lab, _ = _setup()
    await _cost_ready(db, bench, lab)
    with pytest.raises(HTTPException) as foreign:
        await lab.record_cost_basis(await _claim(db), "H1", OVERLAP, _cost_request(await _version(db), ("not-linked",)), USER, DOCS)
    _refused(foreign, 422, "linked to this claim")
    with pytest.raises(HTTPException) as early:
        await lab.review_cost_basis(await _claim(db), "H1", OVERLAP, CostReviewRequest(board_version=await _version(db)), "determiner-1")
    _refused(early, 422, "Record the cost basis")
    await lab.record_cost_basis(await _claim(db), "H1", OVERLAP, _cost_request(await _version(db)), USER, DOCS)
    with pytest.raises(HTTPException) as self_review:
        await lab.review_cost_basis(await _claim(db), "H1", OVERLAP, CostReviewRequest(board_version=await _version(db)), USER)
    _refused(self_review, 403, "someone other than the person who recorded it")
    reviewed = await lab.review_cost_basis(
        await _claim(db), "H1", OVERLAP, CostReviewRequest(board_version=await _version(db)), "determiner-1"
    )
    assert reviewed.overlaps[0].cost_established
    assert sum(period.days for period in reviewed.compensable_periods) == 31


async def test_re_deciding_the_treatment_withdraws_the_cost_basis() -> None:
    db, bench, lab, _ = _setup()
    await _cost_ready(db, bench, lab)
    await lab.record_cost_basis(await _claim(db), "H1", OVERLAP, _cost_request(await _version(db)), USER, DOCS)
    await lab.review_cost_basis(await _claim(db), "H1", OVERLAP, CostReviewRequest(board_version=await _version(db)), "determiner-1")
    again = await lab.decide_overlap(
        await _claim(db), "H1", OVERLAP,
        OverlapDecisionRequest(board_version=await _version(db), treatment="eot_granted_with_cost",
                               basis="Re-decided after the programme analysis was revised."),
        USER,
    )
    assert again.overlaps[0].finding.cost_basis is None and not again.overlaps[0].cost_established


async def test_a_time_only_treatment_takes_no_cost_basis() -> None:
    db, bench, lab, _ = _setup()
    await _cost_ready(db, bench, lab, treatment="eot_granted_no_cost")
    with pytest.raises(HTTPException) as exc:
        await lab.record_cost_basis(await _claim(db), "H1", OVERLAP, _cost_request(await _version(db)), USER, DOCS)
    _refused(exc, 422, "carrying cost")


async def test_neutral_and_contractor_concurrency_is_shown_and_must_be_decided_once_neutral_time_is_given() -> None:
    db, bench, lab, _ = _setup()
    db.delay_events.docs.extend([_hindrance("N1", "2026-03-25", "2026-04-05", "neutral")])
    await _two_events(db, bench, lab)
    await _link(db, lab, "N1")
    await _decide(db, lab, "N1", "neutral")
    before = await lab.view(await _claim(db), "H1")
    assert [o.kind for o in before.overlaps] == ["employer_contractor"]  # no neutral entitlement recorded yet
    view = await lab.set_settings(
        await _claim(db), "H1",
        DelaySettingsRequest(board_version=await _version(db), include_neutral=True,
                             neutral_basis="Sub-Clause 8.4(c): exceptionally adverse climatic conditions."),
        USER,
    )
    kinds = sorted(o.kind for o in view.overlaps)
    assert kinds == ["employer_contractor", "neutral_contractor"]
    neutral = next(o for o in view.overlaps if o.kind == "neutral_contractor")
    assert neutral.neutral_event_ids == ["N1"] and neutral.contractor_event_ids == ["C1"]
    assert neutral.cost_status == "Neutral delay carries time only, never cost."
    blockers = await lab.blockers(await _claim(db), (await bench.get(await _claim(db))).bench)
    assert any("between neutral and Contractor delay need a decided treatment" in item for item in blockers)


async def test_an_event_ending_before_it_starts_says_so_and_not_to_set_the_as_of_date() -> None:
    """C-06: it said "set the date the analysis runs to", which cannot fix a wrong date."""
    db, bench, lab, _ = _setup()
    db.delay_events.docs.append(_hindrance("R1", "2026-03-10", "2026-03-01"))
    await bench.open(await _claim(db), USER)
    await _link(db, lab, "R1")
    await _decide(db, lab, "R1", "employer")
    blockers = await lab.blockers(await _claim(db), (await bench.get(await _claim(db))).bench)
    assert any("ends before it starts; correct the dates on the register" in item for item in blockers), blockers
    assert not any("set the date the analysis runs to" in item for item in blockers)
