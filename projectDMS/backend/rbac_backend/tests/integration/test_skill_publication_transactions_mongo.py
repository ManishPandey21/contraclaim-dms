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
        record, SkillApprovalRequest(kind=kind, reviewer_role=role, eval_report=REPORT), actor
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
                record, SkillApprovalRequest(kind="legal", reviewer_role=LEGAL, eval_report=other),
                second_counsel,
            )
            await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, "1.9.0", REPORT, PUBLISHER)
            await SkillApprovalService(db).revoke(stale.id, "superseded evaluation", COUNSEL)
            assert (await db[SKILL_VERSIONS].find_one({"version": "1.9.0"}))["status"] == "published"
            await _assert_invariant(db)

    _run(scenario())
