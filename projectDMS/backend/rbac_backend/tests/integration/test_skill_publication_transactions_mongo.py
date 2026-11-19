"""4B-05 (B1b): skill publication decisions are serialised, and atomic, on a real replica set.

Recording an approval, revoking one and publishing each run in one MongoDB
transaction that writes the version document. These tests force the races the
audit found instead of hoping to hit them: one operation is held open inside
its transaction while the competing one commits.

Invariant checked after every scenario: no version is published while a review
it relied on is revoked, missing or stale.

Fault injection: a failure between the two writes of a revoke (approval, then
version) or of a record (insert, then version) leaves neither write behind.

Opt-in: ``SKILL_PUBLICATION_MONGODB_URI`` (a disposable replica set). Run in
CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.skill import SkillApprovalRequest, SkillEvalReport, SkillVersion
from rbac_backend.services.skills.approvals import SKILL_APPROVALS, SkillApprovalService, relied_on
from rbac_backend.services.skills.loader import LoadedSkill, bundle_hash
from rbac_backend.services.skills.registry import SKILL_VERSIONS, SkillRegistry
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY, system_skill_version
from rbac_backend.tests.reviewer_assignment_helpers import hold_platform

pytestmark = pytest.mark.integration

MONGODB_URI_ENV = "SKILL_PUBLICATION_MONGODB_URI"

LEGAL = "senior_legal_approver"
CONTRACT = "chief_contract_expert"
DELAY = "delay_planning_expert"
COUNSEL = SimpleNamespace(id="counsel", roles=[LEGAL])
CONTRACT_EXPERT = SimpleNamespace(id="contract-expert", roles=[CONTRACT])
BOTH_TECHNICAL = SimpleNamespace(id="dual-expert", roles=[CONTRACT, DELAY])
PUBLISHER = "publisher"
REPORT = SkillEvalReport(suite="claims-golden-v1", score=0.92, threshold=0.85, case_count=15, recorded_by="evaluator")


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


@asynccontextmanager
async def _db() -> AsyncIterator[Any]:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"skill_publication_{uuid.uuid4().hex[:12]}"
    db = client[name]
    # Collections must exist before a transaction writes to them on older servers.
    await db.create_collection(SKILL_VERSIONS)
    await db.create_collection(SKILL_APPROVALS)
    await db.create_collection("skills")
    await db.create_collection("audit_events")
    # As migration 20261114_0001 creates it in production.
    await db[SKILL_VERSIONS].create_index([("skill_key", 1), ("version", 1)], unique=True, name="skill_version_unique")
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _loaded(key: str, body: str) -> LoadedSkill:
    skill_md = f"---\nname: {key}\ndescription: Pack under review.\n---\n\n# Method\n\n{body}\n"
    return LoadedSkill(
        skill_key=key, name="Pack", description="Pack under review.", skill_md=skill_md,
        references=[], content_hash=bundle_hash(skill_md, []),
    )


async def _draft(db: Any, version: str, key: str = CLAIMS_SKILL_KEY) -> SkillVersion:
    base = system_skill_version(key)
    return await SkillRegistry(db).create_draft(
        _loaded(key, f"method {version}"), version=version, kind=base.kind, manifest=base.manifest,
        created_by="importer",
    )


async def _approve(db: Any, record: SkillVersion, kind: str, role: str, actor: Any):
    # A reviewer role is held through a platform assignment (4B-07).
    await hold_platform(db, actor)
    return await SkillApprovalService(db).record(
        record, SkillApprovalRequest(kind=kind, reviewer_role=role, eval_run_id="run-test"), actor, REPORT
    )


async def _fully_approved(db: Any, version: str):
    record = await _draft(db, version)
    await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
    legal = await _approve(db, record, "legal", LEGAL, COUNSEL)
    return record, legal


async def _assert_invariant(db: Any) -> None:
    """No published version stands on a review it relied on that is not live."""
    service = SkillApprovalService(db)
    async for doc in db[SKILL_VERSIONS].find({"status": "published"}):
        version = SkillVersion(**doc)
        approvals = await service.list(version.skill_key, version.version)
        from rbac_backend.services.skills.approvals import approval_statuses

        statuses = approval_statuses(version, version.eval_report, approvals)
        assert all(item.satisfied for item in statuses), (version.version, [s.state for s in statuses])
        for approval in approvals:
            if approval.revoked_at is not None:
                assert not relied_on(version, approval), (version.version, approval.kind)


async def _outcome(coro: Any) -> Any:
    try:
        return await coro
    except HTTPException as exc:
        return exc


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Plain path
# --------------------------------------------------------------------------- #


def test_publish_with_both_reviews_and_the_revision_moves_with_every_decision() -> None:
    async def scenario() -> None:
        async with _db() as db:
            record, _legal = await _fully_approved(db, "1.1.0")
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.1.0"})
            assert stored["approval_revision"] == 2 and stored["approvals_digest"]
            published = await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.1.0", REPORT, PUBLISHER)
            assert published.status == "published" and published.approval_revision == 3
            await _assert_invariant(db)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Forced races
# --------------------------------------------------------------------------- #


def _hold(monkeypatch: pytest.MonkeyPatch, method: str, seconds: float, only_first: bool = True) -> None:
    """Pause inside the transaction, after it has read, so the other side commits first."""
    original = getattr(SkillApprovalService, method)
    state = {"done": False}

    async def held(self, *args: Any, **kwargs: Any):
        result = await original(self, *args, **kwargs)
        if not (only_first and state["done"]):
            state["done"] = True
            await asyncio.sleep(seconds)
        return result

    monkeypatch.setattr(SkillApprovalService, method, held)


def test_a_revoke_that_commits_while_publish_is_deciding_stops_the_publish(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "1.2.0")
            # Publish reads the approvals (still live), then waits; the revoke commits meanwhile.
            _hold(monkeypatch, "_list_in", 0.5)
            publish = asyncio.create_task(
                _outcome(SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.2.0", REPORT, PUBLISHER))
            )
            await asyncio.sleep(0.1)
            await SkillApprovalService(db).revoke(legal.id, "authority overruled", COUNSEL)
            result = await publish
            assert isinstance(result, HTTPException) and result.status_code in {409, 422}, result
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.2.0"})
            assert stored["status"] == "draft"
            await _assert_invariant(db)

    _run(scenario())


def test_a_publish_that_commits_while_revoke_is_deciding_is_deprecated_by_that_revoke(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "1.3.0")
            # Revoke reads the version (still a draft), then waits; publish commits meanwhile.
            _hold(monkeypatch, "_version_in", 0.5)
            revoke = asyncio.create_task(_outcome(SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)))
            await asyncio.sleep(0.1)
            published = await _outcome(SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.3.0", REPORT, PUBLISHER))
            revoked = await revoke
            assert not isinstance(revoked, HTTPException), revoked
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.3.0"})
            if not isinstance(published, HTTPException):
                # Both committed: the revoke was retried on the published version and deprecated it.
                assert stored["status"] == "deprecated", stored["status"]
            else:
                assert stored["status"] == "draft"
            await _assert_invariant(db)

    _run(scenario())


_ROLE: "contextvars.ContextVar[str]" = contextvars.ContextVar("race_role", default="")


def test_publish_and_revoke_interleaved_step_by_step_never_leave_a_published_version_on_a_revoked_review(
    monkeypatch,
) -> None:
    """Owner request (4B-05 follow-up): a deterministic interleaving for publish versus revoke.

    The publish-during-revoke test above cannot fail without transactions, because a revoke
    marks the approval before it reads the version, so a publish that starts later already
    sees the revocation. This test forces the one order that can break the invariant, with
    events rather than sleeps, each side identified by its own task context:

      1. publish reads the approvals: both live;
      2. revoke marks the legal approval revoked and reads the version: still a draft;
      3. publish writes the version as published and commits;
      4. revoke writes the version, filtered on the draft it read.

    Without transactions, step 4 matches nothing and the revoke answers 409 after its
    approval write has already landed: a published version stands on a revoked review.
    With transactions, step 4 conflicts with step 3, the revoke is retried from the start,
    sees the published version, and deprecates it in the same transaction.
    """

    publish_has_read = asyncio.Event()
    revoke_has_read = asyncio.Event()
    publish_done = asyncio.Event()
    seen = {"publish_hold": 0, "revoke_hold": 0}
    original_list_in = SkillApprovalService._list_in
    original_version_in = SkillApprovalService._version_in

    async def list_in(self, *args: Any, **kwargs: Any):
        result = await original_list_in(self, *args, **kwargs)
        if _ROLE.get() == "publish" and seen["publish_hold"] == 0:
            seen["publish_hold"] += 1
            publish_has_read.set()
            await asyncio.wait_for(revoke_has_read.wait(), 10)  # step 1 -> 2
        return result

    async def version_in(self, *args: Any, **kwargs: Any):
        result = await original_version_in(self, *args, **kwargs)
        if _ROLE.get() == "revoke" and seen["revoke_hold"] == 0:
            seen["revoke_hold"] += 1
            revoke_has_read.set()
            await asyncio.wait_for(publish_done.wait(), 10)  # step 2 -> 3 -> 4
        return result

    monkeypatch.setattr(SkillApprovalService, "_list_in", list_in)
    monkeypatch.setattr(SkillApprovalService, "_version_in", version_in)

    async def as_role(role: str, coro: Any) -> Any:
        _ROLE.set(role)
        return await _outcome(coro)

    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "1.9.0")
            publish = asyncio.create_task(
                as_role("publish", SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.9.0", REPORT, PUBLISHER))
            )
            await asyncio.wait_for(publish_has_read.wait(), 10)
            revoke = asyncio.create_task(as_role("revoke", SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)))
            published = await asyncio.wait_for(publish, 10)
            publish_done.set()
            revoked = await asyncio.wait_for(revoke, 10)

            # The interleaving really happened: both sides were held once.
            assert seen == {"publish_hold": 1, "revoke_hold": 1}, seen
            # The invariant first: a failure here is the defect itself, not a symptom.
            await _assert_invariant(db)
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.9.0"})
            assert stored["status"] == "deprecated", stored["status"]
            assert (await db[SKILL_APPROVALS].find_one({"_id": legal.id}))["revoked_at"] is not None
            assert not isinstance(published, HTTPException), published
            assert not isinstance(revoked, HTTPException), revoked

    _run(scenario())


@pytest.mark.parametrize("round_", range(10))
def test_unforced_publish_and_revoke_races_never_break_the_invariant(round_: int) -> None:
    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, f"2.{round_}.0")
            results = await asyncio.gather(
                _outcome(SkillRegistry(db).publish(CLAIMS_SKILL_KEY, f"2.{round_}.0", REPORT, PUBLISHER)),
                _outcome(SkillApprovalService(db).revoke(legal.id, "race", COUNSEL)),
            )
            assert not isinstance(results[1], HTTPException), results[1]
            stored = await db[SKILL_VERSIONS].find_one({"version": f"2.{round_}.0"})
            assert stored["status"] in {"draft", "deprecated"}, stored["status"]
            await _assert_invariant(db)

    _run(scenario())


def test_one_person_cannot_give_two_reviews_by_racing_them(monkeypatch) -> None:
    """The EOT pack needs two technical reviewers; one person holding both roles is one review."""

    async def scenario() -> None:
        async with _db() as db:
            record = await _draft(db, "1.4.0", key=EOT_SKILL_KEY)
            _hold(monkeypatch, "_list_in", 0.4)
            results = await asyncio.gather(
                _outcome(_approve(db, record, "technical", CONTRACT, BOTH_TECHNICAL)),
                _outcome(_approve(db, record, "technical", DELAY, BOTH_TECHNICAL)),
            )
            refused = [r for r in results if isinstance(r, HTTPException)]
            assert len(refused) == 1 and refused[0].status_code == 409, results
            live = await db[SKILL_APPROVALS].count_documents(
                {"version": "1.4.0", "reviewer_user_id": "dual-expert", "revoked_at": None}
            )
            assert live == 1

    _run(scenario())


def test_publish_refuses_when_one_person_holds_two_required_reviews() -> None:
    """Even if such approvals exist in the store (written before 4B-05), publish refuses."""

    async def scenario() -> None:
        async with _db() as db:
            record = await _draft(db, "1.5.0", key=EOT_SKILL_KEY)
            await _approve(db, record, "technical", CONTRACT, BOTH_TECHNICAL)
            first = await db[SKILL_APPROVALS].find_one({"version": "1.5.0"})
            second = dict(first, _id=str(uuid.uuid4()), reviewer_role=DELAY)
            await db[SKILL_APPROVALS].insert_one(second)
            await _approve(db, record, "legal", LEGAL, COUNSEL)
            result = await _outcome(SkillRegistry(db).publish(EOT_SKILL_KEY, "1.5.0", REPORT, PUBLISHER))
            assert isinstance(result, HTTPException) and result.status_code == 409
            assert "different person" in result.detail
            assert (await db[SKILL_VERSIONS].find_one({"version": "1.5.0"}))["status"] == "draft"

    _run(scenario())


# --------------------------------------------------------------------------- #
# Fault injection: a failure between two writes leaves neither
# --------------------------------------------------------------------------- #


class _Injected(RuntimeError):
    pass


def _fail_bump(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(self, current, kw, fields):  # after the first write, before the version write
        raise _Injected("injected failure before the version write")

    monkeypatch.setattr(SkillApprovalService, "_bump", failing)


def test_a_revoke_that_fails_before_deprecating_leaves_nothing_revoked(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "1.6.0")
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.6.0", REPORT, PUBLISHER)
            _fail_bump(monkeypatch)
            with pytest.raises(_Injected):
                await SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)
            approval = await db[SKILL_APPROVALS].find_one({"_id": legal.id})
            assert approval["revoked_at"] is None, "the revocation must roll back with the deprecation"
            assert (await db[SKILL_VERSIONS].find_one({"version": "1.6.0"}))["status"] == "published"
            await _assert_invariant(db)

    _run(scenario())


def test_a_record_that_fails_before_the_version_write_leaves_no_approval(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            record = await _draft(db, "1.7.0")
            _fail_bump(monkeypatch)
            with pytest.raises(_Injected):
                await _approve(db, record, "legal", LEGAL, COUNSEL)
            assert await db[SKILL_APPROVALS].count_documents({"version": "1.7.0"}) == 0
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.7.0"})
            assert stored.get("approval_revision", 0) == 0

    _run(scenario())


def test_the_revoke_route_deprecates_through_the_service_transaction() -> None:
    """The router no longer deprecates on its own; the service did it atomically."""

    async def scenario() -> None:
        async with _db() as db:
            from rbac_backend.models.skill import SkillApprovalRevokeRequest
            from rbac_backend.routers import skills as skills_router

            _record, legal = await _fully_approved(db, "1.8.0")
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.8.0", REPORT, PUBLISHER)

            class Policy:
                async def authorize(self, *args: Any, **kwargs: Any) -> None:
                    return None

            await skills_router.revoke_skill_approval(
                legal.id, SkillApprovalRevokeRequest(reason="overruled"),
                registry=SkillRegistry(db), approvals=SkillApprovalService(db), policy=Policy(),
                current_user=SimpleNamespace(id="counsel", roles=[LEGAL]),
            )
            stored = await db[SKILL_VERSIONS].find_one({"version": "1.8.0"})
            assert stored["status"] == "deprecated" and stored["deprecated_by"] == "counsel"
            resolved, _, _ = await SkillRegistry(db).resolve(CLAIMS_SKILL_KEY, "org1", None)
            assert resolved is None or resolved.version != "1.8.0"

    _run(scenario())


def test_revoking_an_approval_the_version_did_not_rely_on_does_not_deprecate_it() -> None:
    async def scenario() -> None:
        async with _db() as db:
            record, _legal = await _fully_approved(db, "1.9.0")
            # A stale legal approval against a different evaluation result.
            other = SkillEvalReport(suite="claims-golden-v0", score=0.9, threshold=0.85, case_count=15,
                                    recorded_by="evaluator")
            second_counsel = SimpleNamespace(id="counsel-2", roles=[LEGAL])
            await hold_platform(db, second_counsel)
            stale = await SkillApprovalService(db).record(
                record, SkillApprovalRequest(kind="legal", reviewer_role=LEGAL, eval_run_id="run-test"),
                second_counsel, other,
            )
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.9.0", REPORT, PUBLISHER)
            await SkillApprovalService(db).revoke(stale.id, "superseded evaluation", COUNSEL)
            assert (await db[SKILL_VERSIONS].find_one({"version": "1.9.0"}))["status"] == "published"
            await _assert_invariant(db)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Audit events (4B-05): written in the decision's transaction
# --------------------------------------------------------------------------- #


async def _events(db: Any, version: str) -> list:
    return [
        (doc["action"], doc["actor_id"])
        async for doc in db["audit_events"].find(
            {"resource_type": "skill_version", "metadata.version": version}
        ).sort("created_at", 1)
    ]


def test_every_publication_decision_writes_one_audit_event() -> None:
    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "2.1.0")
            # Recording the same review again changes nothing and records nothing.
            await _approve(db, _record, "legal", LEGAL, COUNSEL)
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "2.1.0", REPORT, PUBLISHER)
            await SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)
            # A second revoke of the same approval is a no-op and records nothing.
            await SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)
            assert await _events(db, "2.1.0") == [
                ("skill.approval.record", "contract-expert"),
                ("skill.approval.record", "counsel"),
                ("skill.version.publish", PUBLISHER),
                ("skill.approval.revoke", "counsel"),
                ("skill.version.deprecate", "counsel"),
            ]
            revoke = await db["audit_events"].find_one({"action": "skill.approval.revoke"})
            assert revoke["metadata"]["approval_id"] == legal.id
            assert revoke["metadata"]["reason"] == "overruled"
            assert revoke["metadata"]["deprecated_version"] is True
            assert revoke["resource_id"] == f"{CLAIMS_SKILL_KEY}@2.1.0"
            publish = await db["audit_events"].find_one({"action": "skill.version.publish"})
            assert publish["before"] == {"status": "draft"} and publish["after"] == {"status": "published"}
            assert len(publish["metadata"]["approval_ids"]) == 2

    _run(scenario())


def test_a_direct_deprecation_is_audited_once() -> None:
    async def scenario() -> None:
        async with _db() as db:
            await _fully_approved(db, "2.2.0")
            registry = SkillRegistry(db)
            await registry.publish(CLAIMS_SKILL_KEY, "2.2.0", REPORT, PUBLISHER)
            await registry.deprecate(CLAIMS_SKILL_KEY, "2.2.0", "platform-admin")
            with pytest.raises(HTTPException):
                await registry.deprecate(CLAIMS_SKILL_KEY, "2.2.0", "platform-admin")
            events = await _events(db, "2.2.0")
            assert events.count(("skill.version.deprecate", "platform-admin")) == 1
            event = await db["audit_events"].find_one({"action": "skill.version.deprecate"})
            assert event["metadata"]["cause"] == "direct"

    _run(scenario())


def test_a_refused_decision_writes_no_audit_event() -> None:
    async def scenario() -> None:
        async with _db() as db:
            record = await _draft(db, "2.3.0")
            await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
            # Publishing without the legal review is refused inside the transaction.
            with pytest.raises(HTTPException):
                await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "2.3.0", REPORT, PUBLISHER)
            assert await _events(db, "2.3.0") == [("skill.approval.record", "contract-expert")]

    _run(scenario())


def test_an_aborted_decision_leaves_no_audit_event(monkeypatch) -> None:
    """The event is in the transaction: a failure after it rolls it back with the decision."""

    async def scenario() -> None:
        async with _db() as db:
            _record, legal = await _fully_approved(db, "2.4.0")
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "2.4.0", REPORT, PUBLISHER)
            before = await _events(db, "2.4.0")
            from rbac_backend.services.skills import approvals as approvals_module

            real = approvals_module.audit_skill_decision
            calls = {"n": 0}

            async def fail_after_first(*args: Any, **kwargs: Any) -> None:
                await real(*args, **kwargs)  # the revoke event is written...
                calls["n"] += 1
                if calls["n"] == 2:
                    raise _Injected("injected failure after the deprecation event")  # ...then this aborts

            monkeypatch.setattr(approvals_module, "audit_skill_decision", fail_after_first)
            with pytest.raises(_Injected):
                await SkillApprovalService(db).revoke(legal.id, "overruled", COUNSEL)
            assert await _events(db, "2.4.0") == before
            assert (await db[SKILL_APPROVALS].find_one({"_id": legal.id}))["revoked_at"] is None
            assert (await db[SKILL_VERSIONS].find_one({"version": "2.4.0"}))["status"] == "published"

    _run(scenario())


def test_a_lost_publish_race_writes_one_publish_event(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            await _fully_approved(db, "2.5.0")
            outcomes = await asyncio.gather(
                _outcome(SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "2.5.0", REPORT, PUBLISHER)),
                _outcome(SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "2.5.0", REPORT, "publisher-2")),
            )
            assert sum(not isinstance(item, HTTPException) for item in outcomes) == 1
            events = [action for action, _ in await _events(db, "2.5.0")]
            assert events.count("skill.version.publish") == 1

    _run(scenario())


# --------------------------------------------------------------------------- #
# Deprecation is one transaction: read, materialise, status, audit (4B-05)
# --------------------------------------------------------------------------- #

BUNDLED_KEY = "indian-infra-letter-analysis"  # published in the bundle, absent from a new database
BUNDLED_VERSION = "1.0.0"


async def _deprecations(db: Any, key: str, version: str) -> list:
    return [
        doc
        async for doc in db["audit_events"].find(
            {"action": "skill.version.deprecate", "metadata.skill_key": key, "metadata.version": version}
        )
    ]


def test_deprecating_a_bundled_only_version_materialises_it_in_the_same_transaction() -> None:
    async def scenario() -> None:
        async with _db() as db:
            assert await db[SKILL_VERSIONS].count_documents({"skill_key": BUNDLED_KEY}) == 0
            result = await SkillRegistry(db).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
            assert result.status == "deprecated" and result.deprecated_by == "platform-admin"
            docs = await db[SKILL_VERSIONS].find({"skill_key": BUNDLED_KEY}).to_list(length=None)
            assert len(docs) == 1 and docs[0]["status"] == "deprecated"
            events = await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION)
            assert len(events) == 1 and events[0]["metadata"]["materialised"] is True
            # The published pointer no longer names it.
            skill = await db["skills"].find_one({"skill_key": BUNDLED_KEY})
            assert skill is not None and skill["latest_published_version"] is None

    _run(scenario())


def test_a_deprecation_that_fails_after_materialising_leaves_nothing_behind(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            from rbac_backend.services.skills import registry as registry_module

            async def failing(*args: Any, **kwargs: Any) -> None:
                raise _Injected("injected failure at the audit write")

            monkeypatch.setattr(registry_module, "audit_skill_decision", failing)
            with pytest.raises(_Injected):
                await SkillRegistry(db).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
            assert await db[SKILL_VERSIONS].count_documents({"skill_key": BUNDLED_KEY}) == 0, (
                "the materialised copy must roll back with the deprecation"
            )
            assert await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION) == []
            # Without the fault the same call succeeds: the failure left no half-state.
            monkeypatch.undo()
            await SkillRegistry(db).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
            assert len(await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION)) == 1

    _run(scenario())


def test_a_stored_version_that_fails_at_the_audit_write_stays_published(monkeypatch) -> None:
    async def scenario() -> None:
        async with _db() as db:
            await _fully_approved(db, "3.1.0")
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "3.1.0", REPORT, PUBLISHER)
            from rbac_backend.services.skills import registry as registry_module

            async def failing(*args: Any, **kwargs: Any) -> None:
                raise _Injected("injected failure at the audit write")

            monkeypatch.setattr(registry_module, "audit_skill_decision", failing)
            with pytest.raises(_Injected):
                await SkillRegistry(db).deprecate(CLAIMS_SKILL_KEY, "3.1.0", "platform-admin")
            stored = await db[SKILL_VERSIONS].find_one({"version": "3.1.0"})
            assert stored["status"] == "published" and stored.get("deprecated_by") is None

    _run(scenario())


@pytest.mark.parametrize("round_", range(5))
@pytest.mark.parametrize("bundled", [True, False])
def test_concurrent_deprecations_succeed_once_and_audit_once(round_: int, bundled: bool) -> None:
    async def scenario() -> None:
        async with _db() as db:
            if bundled:
                key, version = BUNDLED_KEY, BUNDLED_VERSION
            else:
                key, version = CLAIMS_SKILL_KEY, "3.2.0"
                await _fully_approved(db, version)
                await SkillRegistry(db).publish(key, version, REPORT, PUBLISHER)
            outcomes = await asyncio.gather(
                *(_outcome(SkillRegistry(db).deprecate(key, version, f"admin-{n}")) for n in range(4))
            )
            succeeded = [item for item in outcomes if not isinstance(item, HTTPException)]
            refused = [item for item in outcomes if isinstance(item, HTTPException)]
            assert len(succeeded) == 1, outcomes
            assert all(item.status_code == 409 for item in refused), [i.detail for i in refused]
            assert await db[SKILL_VERSIONS].count_documents({"skill_key": key, "version": version}) == 1
            stored = await db[SKILL_VERSIONS].find_one({"skill_key": key, "version": version})
            events = await _deprecations(db, key, version)
            assert len(events) == 1 and events[0]["actor_id"] == stored["deprecated_by"]

    _run(scenario())


def test_a_deprecation_held_open_loses_to_one_that_commits_first() -> None:
    """Forced interleaving: A reads the bundled version (absent from the database) and waits;
    B materialises and commits; A's retry must see B's result and refuse."""

    async def scenario() -> None:
        async with _db() as db:
            state = {"held": False}
            real_insert_owner = db[SKILL_VERSIONS]

            class SlowFirstInsert:
                def __init__(self, inner: Any) -> None:
                    self.inner = inner

                def __getattr__(self, name: str) -> Any:
                    return getattr(self.inner, name)

                async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
                    if not state["held"]:
                        state["held"] = True
                        await asyncio.sleep(0.5)  # after A's read, before A's write
                    return await self.inner.insert_one(*args, **kwargs)

            class Db:
                client = db.client

                def __getitem__(self, name: str) -> Any:
                    if name == SKILL_VERSIONS:
                        return SlowFirstInsert(real_insert_owner)
                    return db[name]

                def __getattr__(self, name: str) -> Any:
                    return getattr(db, name)

            first = asyncio.create_task(_outcome(SkillRegistry(Db()).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "admin-a")))
            await asyncio.sleep(0.1)
            second = await _outcome(SkillRegistry(db).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "admin-b"))
            first_result = await first
            assert not isinstance(second, HTTPException), second
            assert isinstance(first_result, HTTPException) and first_result.status_code == 409, first_result
            stored = await db[SKILL_VERSIONS].find({"skill_key": BUNDLED_KEY}).to_list(length=None)
            assert len(stored) == 1 and stored[0]["deprecated_by"] == "admin-b"
            events = await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION)
            assert [e["actor_id"] for e in events] == ["admin-b"]

    _run(scenario())


def test_repeating_a_deprecation_is_refused_and_records_nothing_more() -> None:
    async def scenario() -> None:
        async with _db() as db:
            registry = SkillRegistry(db)
            await registry.deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
            for _ in range(3):
                with pytest.raises(HTTPException) as exc:
                    await registry.deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
                assert exc.value.status_code == 409
            with pytest.raises(HTTPException) as missing:
                await registry.deprecate(BUNDLED_KEY, "9.9.9", "platform-admin")
            assert missing.value.status_code == 404
            with pytest.raises(HTTPException) as draft:
                await registry.deprecate("indian-infra-claims", "1.0.0", "platform-admin")
            assert draft.value.status_code == 409
            assert await db[SKILL_VERSIONS].count_documents({"skill_key": "indian-infra-claims"}) == 0
            assert len(await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION)) == 1

    _run(scenario())


def test_without_transactions_deprecation_is_refused_before_any_write() -> None:
    """A server that cannot run transactions (standalone) answers 503 and writes nothing:
    previously a bundled version was materialised before the transaction was attempted."""

    async def scenario() -> None:
        async with _db() as db:
            from pymongo.errors import OperationFailure

            class Session:
                async def __aenter__(self) -> "Session":
                    return self

                async def __aexit__(self, *exc: Any) -> None:
                    return None

                async def with_transaction(self, callback: Any) -> Any:
                    raise OperationFailure(
                        "Transaction numbers are only allowed on a replica set member or mongos", code=20
                    )

            class Client:
                async def start_session(self) -> Session:
                    return Session()

            class Standalone:
                client = Client()

                def __getitem__(self, name: str) -> Any:
                    return db[name]

            with pytest.raises(HTTPException) as exc:
                await SkillRegistry(Standalone()).deprecate(BUNDLED_KEY, BUNDLED_VERSION, "platform-admin")
            assert exc.value.status_code == 503
            assert await db[SKILL_VERSIONS].count_documents({}) == 0
            assert await db["audit_events"].count_documents({}) == 0

    _run(scenario())


@pytest.mark.parametrize("round_", range(5))
def test_concurrent_materialisations_collide_even_without_the_unique_index(round_: int) -> None:
    """Review finding 5: the migration's skill_version_unique index is not created at
    startup; without it two transactions could each insert their own copy."""

    async def scenario() -> None:
        async with _db() as db:
            await db[SKILL_VERSIONS].drop_index("skill_version_unique")
            outcomes = await asyncio.gather(
                *(_outcome(SkillRegistry(db).deprecate(BUNDLED_KEY, BUNDLED_VERSION, f"admin-{n}")) for n in range(4))
            )
            assert sum(not isinstance(item, HTTPException) for item in outcomes) == 1, outcomes
            assert await db[SKILL_VERSIONS].count_documents({"skill_key": BUNDLED_KEY}) == 1
            assert len(await _deprecations(db, BUNDLED_KEY, BUNDLED_VERSION)) == 1

    _run(scenario())
