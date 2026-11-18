"""The legal gate on skill publication.

A version that names required approvals is published only when each one
exists, was given by a holder of the named reviewer role, and is bound to the
exact text and evaluation being published. Evaluator, legal signatory and
publisher are different people, and a platform role is not a reviewer role.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.skill import SkillApprovalRequest, SkillEvalReport
from rbac_backend.services.skills.approvals import (
    SkillApprovalService,
    approval_statuses,
    eval_report_hash,
)
from rbac_backend.services.skills.loader import LoadedSkill, bundle_hash
from rbac_backend.services.skills.registry import SkillRegistry
from rbac_backend.services.skills.system_skills import (
    CLAIMS_SKILL_KEY,
    EOT_SKILL_KEY,
    VARIATIONS_SKILL_KEY,
    system_skill_version,
)
from rbac_backend.tests.bench_fakes import FakeDB
from rbac_backend.tests.reviewer_assignment_helpers import hold_platform

LEGAL = "senior_legal_approver"
CONTRACT = "chief_contract_expert"
DELAY = "delay_planning_expert"


def _user(user_id: str, *roles: str) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, roles=list(roles))


EVALUATOR = _user("evaluator")
CONTRACT_EXPERT = _user("contract-expert", CONTRACT)
COUNSEL = _user("counsel", LEGAL)
SUPERADMIN = _user("root", "superadmin")
PUBLISHER = "publisher"


def _report(score: float = 0.92, recorded_by: str | None = "evaluator") -> SkillEvalReport:
    return SkillEvalReport(
        suite="claims-golden-v1", score=score, threshold=0.85, case_count=12, recorded_by=recorded_by
    )


def _loaded(key: str, body: str) -> LoadedSkill:
    skill_md = f"---\nname: {key}\ndescription: Pack under review.\n---\n\n# Method\n\n{body}\n"
    return LoadedSkill(
        skill_key=key, name="Pack", description="Pack under review.", skill_md=skill_md,
        references=[], content_hash=bundle_hash(skill_md, []),
    )


async def _draft(db: FakeDB, key: str = CLAIMS_SKILL_KEY, body: str = "method v1", version: str = "1.1.0"):
    base = system_skill_version(key)
    return await SkillRegistry(db).create_draft(
        _loaded(key, body), version=version, kind=base.kind, manifest=base.manifest, created_by="importer"
    )


async def _approve(db: FakeDB, record, kind: str, role: str, actor, report: SkillEvalReport | None = None):
    # A reviewer role is held through a platform assignment (4B-07). An actor with
    # no reviewer role on its list gets none, and is refused as before.
    await hold_platform(db, actor)
    return await SkillApprovalService(db).record(
        record, SkillApprovalRequest(kind=kind, reviewer_role=role, eval_run_id="run-test"), actor, report or _report()
    )


async def _fully_approved(db: FakeDB):
    record = await _draft(db)
    await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
    await _approve(db, record, "legal", LEGAL, COUNSEL)
    return record


async def _publish(db: FakeDB, report: SkillEvalReport | None = None, by: str = PUBLISHER, key: str = CLAIMS_SKILL_KEY):
    return await SkillRegistry(db).publish(key, "1.1.0", report or _report(), by)


def _refused(exc: pytest.ExceptionInfo, status: int, *fragments: str) -> None:
    assert exc.value.status_code == status
    for fragment in fragments:
        assert fragment in str(exc.value.detail)


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #
async def test_publish_is_refused_without_approvals_and_names_each_missing_one() -> None:
    db = FakeDB()
    await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db)
    _refused(
        exc, 422,
        "technical approval by chief_contract_expert: not given",
        "legal approval by senior_legal_approver: not given",
    )


async def test_publish_succeeds_with_technical_and_legal_approval_and_separate_people() -> None:
    db = FakeDB()
    await _fully_approved(db)
    published = await _publish(db)
    assert published.status == "published" and published.published_by == PUBLISHER
    version, _, reason = await SkillRegistry(db).resolve(CLAIMS_SKILL_KEY, "org1", None)
    assert version is not None and version.version == "1.1.0" and reason is None


async def test_technical_approval_alone_does_not_unlock_publication() -> None:
    db = FakeDB()
    record = await _draft(db)
    await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
    with pytest.raises(HTTPException) as exc:
        await _publish(db)
    _refused(exc, 422, "legal approval by senior_legal_approver: not given")
    assert "chief_contract_expert" not in str(exc.value.detail)


async def test_every_technical_reviewer_a_pack_names_is_required() -> None:
    db = FakeDB()
    record = await _draft(db, key=EOT_SKILL_KEY)
    await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
    await _approve(db, record, "legal", LEGAL, COUNSEL)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, key=EOT_SKILL_KEY)
    _refused(exc, 422, "technical approval by delay_planning_expert: not given")


async def test_an_approved_draft_cannot_be_reimported_under_the_same_version() -> None:
    """4B-04: reviewers' names cannot be left on text or policy they did not see."""
    db = FakeDB()
    record = await _fully_approved(db)
    with pytest.raises(HTTPException) as exc:
        await _draft(db, body="method v1, quietly rewritten")  # same version number, new text
    _refused(exc, 409, "has approvals")
    stored = await SkillRegistry(db).get_version(CLAIMS_SKILL_KEY, "1.1.0")
    assert stored.content_hash == record.content_hash


async def test_approval_for_other_text_does_not_unlock_a_reimported_draft() -> None:
    """Once its approvals are revoked a draft may be replaced; the old approvals then count for nothing."""
    db = FakeDB()
    record = await _fully_approved(db)
    service = SkillApprovalService(db)
    for item in await service.list(record.skill_key, record.version):
        actor = COUNSEL if item.kind == "legal" else CONTRACT_EXPERT
        await service.revoke(item.id, "text is being rewritten", actor)
    await _draft(db, body="method v1, quietly rewritten")
    with pytest.raises(HTTPException) as exc:
        await _publish(db)
    _refused(exc, 422, "given for different skill text")


async def test_approval_for_another_evaluation_result_does_not_count() -> None:
    db = FakeDB()
    await _fully_approved(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, report=_report(score=0.99))
    _refused(exc, 422, "given for a different evaluation result")


async def test_revoked_approval_blocks_publication() -> None:
    db = FakeDB()
    record = await _fully_approved(db)
    service = SkillApprovalService(db)
    legal = next(item for item in await service.list(record.skill_key, record.version) if item.kind == "legal")
    revoked = await service.revoke(legal.id, "authority in the pack was overruled", COUNSEL)
    assert revoked.revoked_at is not None and revoked.revoked_by == "counsel"
    with pytest.raises(HTTPException) as exc:
        await _publish(db)
    _refused(exc, 422, "legal approval by senior_legal_approver: revoked")


async def test_the_legal_signatory_cannot_publish_what_they_approved() -> None:
    db = FakeDB()
    await _fully_approved(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, by="counsel")
    _refused(exc, 409, "legal signatory cannot publish")


async def test_the_evaluator_cannot_publish() -> None:
    db = FakeDB()
    await _fully_approved(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, by="evaluator")
    _refused(exc, 409, "ran the evaluation cannot publish")


async def test_publish_needs_a_named_evaluator_when_approvals_are_required() -> None:
    db = FakeDB()
    await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, report=_report(recorded_by=None))
    _refused(exc, 422, "recorded_by")


# --------------------------------------------------------------------------- #
# B1: the server floor. A client manifest can add reviews, never remove one.
# --------------------------------------------------------------------------- #
async def _import_with_manifest(db: FakeDB, key: str, version: str, manifest, body: str = "method"):
    return await SkillRegistry(db).create_draft(
        _loaded(key, body), version=version, kind="evaluation", manifest=manifest, created_by="importer"
    )


@pytest.mark.parametrize("version", ["1.0.1", "1.1.0", "9.0.0"])
async def test_reimporting_a_system_pack_with_no_required_reviews_keeps_the_bundled_ones(version: str) -> None:
    """The exploit from the audit: import the claims pack as a new version with
    required_approvals [] and publish it on a self-typed evaluation."""
    db = FakeDB()
    stripped = system_skill_version(CLAIMS_SKILL_KEY).manifest.model_copy(update={"required_approvals": []})
    record = await _import_with_manifest(db, CLAIMS_SKILL_KEY, version, stripped)
    kept = {(item.kind, item.reviewer_role) for item in record.manifest.required_approvals}
    assert kept == {("technical", CONTRACT), ("legal", LEGAL)}
    with pytest.raises(HTTPException) as exc:
        await SkillRegistry(db).publish(CLAIMS_SKILL_KEY, version, _report(), PUBLISHER)
    _refused(
        exc, 422,
        "technical approval by chief_contract_expert: not given",
        "legal approval by senior_legal_approver: not given",
    )
    resolved, _, _ = await SkillRegistry(db).resolve(CLAIMS_SKILL_KEY, "org1", None)
    assert resolved is None, "nothing may go live without the reviews"


async def test_a_published_letter_pack_needs_the_legal_sign_off_at_its_next_version() -> None:
    """Decision D-03: the gate applies to the three letter packs from their next version."""
    db = FakeDB()
    await _draft(db, key=VARIATIONS_SKILL_KEY)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, key=VARIATIONS_SKILL_KEY)
    _refused(exc, 422, "legal approval by senior_legal_approver: not given")
    resolved, _, _ = await SkillRegistry(db).resolve(VARIATIONS_SKILL_KEY, "org1", None)
    assert resolved is not None and resolved.version == "1.0.0"


async def test_an_unknown_key_imported_into_the_system_scope_needs_the_legal_sign_off() -> None:
    db = FakeDB()
    manifest = system_skill_version(VARIATIONS_SKILL_KEY).manifest.model_copy(update={"required_approvals": []})
    record = await _import_with_manifest(db, "metro-defence-trial", "0.1.0", manifest)
    assert [(item.kind, item.reviewer_role) for item in record.manifest.required_approvals] == [("legal", LEGAL)]
    with pytest.raises(HTTPException) as exc:
        await SkillRegistry(db).publish("metro-defence-trial", "0.1.0", _report(), PUBLISHER)
    _refused(exc, 422, "legal approval by senior_legal_approver: not given")


async def test_a_manifest_may_add_reviews_to_the_floor() -> None:
    from rbac_backend.models.skill import SkillApprovalRequirement

    db = FakeDB()
    base = system_skill_version(CLAIMS_SKILL_KEY).manifest
    extra = SkillApprovalRequirement(kind="technical", reviewer_role=DELAY)
    record = await _import_with_manifest(
        db, CLAIMS_SKILL_KEY, "1.2.0", base.model_copy(update={"required_approvals": [extra]})
    )
    assert {(i.kind, i.reviewer_role) for i in record.manifest.required_approvals} == {
        ("technical", CONTRACT), ("legal", LEGAL), ("technical", DELAY),
    }


async def test_a_manifest_changed_after_approval_makes_the_approvals_stale() -> None:
    """Text is bound by content_hash; the manifest by policy_hash. Neither can change under an approval."""
    db = FakeDB()
    record = await _fully_approved(db)
    # A change the import path now refuses, made directly in the store.
    db.skill_versions.docs[-1]["manifest"]["validators"] = ["reservation_of_rights"]
    with pytest.raises(HTTPException) as exc:
        await _publish(db)
    _refused(exc, 422, "given before the manifest changed")
    assert record.status == "draft"


async def test_naming_a_different_evaluator_at_publish_invalidates_the_approvals() -> None:
    """recorded_by is part of the evaluation's identity: the publisher cannot swap it."""
    db = FakeDB()
    await _fully_approved(db)
    with pytest.raises(HTTPException) as exc:
        await _publish(db, report=_report(recorded_by="someone-else"))
    _refused(exc, 422, "given for a different evaluation result")


@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.84])
async def test_an_evaluation_cannot_set_its_own_bar_below_the_server_minimum(threshold: float) -> None:
    db = FakeDB()
    record = await _draft(db)
    low = SkillEvalReport(suite="s", score=1.0, threshold=threshold, case_count=1, recorded_by="evaluator")
    with pytest.raises(HTTPException) as at_review:
        await _approve(db, record, "legal", LEGAL, COUNSEL, report=low)
    _refused(at_review, 422, "below the required minimum 0.85")
    with pytest.raises(HTTPException) as at_publish:
        await _publish(db, report=low)
    _refused(at_publish, 422, "below the required minimum 0.85")


async def test_configuration_can_raise_the_evaluation_minimum_but_not_lower_it(monkeypatch) -> None:
    from rbac_backend.core.config import settings
    from rbac_backend.services.skills.approvals import minimum_eval_threshold

    monkeypatch.setattr(settings, "SKILL_EVAL_MIN_THRESHOLD", 0.9)
    assert minimum_eval_threshold() == 0.9
    monkeypatch.setattr(settings, "SKILL_EVAL_MIN_THRESHOLD", 0.1)
    assert minimum_eval_threshold() == 0.85


async def test_the_approvals_listing_reports_the_floor_and_the_policy_hash() -> None:
    from rbac_backend.routers import skills as skills_router
    from rbac_backend.services.skills.approvals import manifest_policy_hash

    db = FakeDB()
    stripped = system_skill_version(CLAIMS_SKILL_KEY).manifest.model_copy(update={"required_approvals": []})
    record = await _import_with_manifest(db, CLAIMS_SKILL_KEY, "1.3.0", stripped)

    class Policy:
        async def authorize(self, *args, **kwargs):
            return None

    response = await skills_router.list_skill_approvals(
        CLAIMS_SKILL_KEY, "1.3.0",
        registry=SkillRegistry(db), approvals=SkillApprovalService(db), policy=Policy(),
        current_user=SimpleNamespace(id="publisher", roles=["superadmin"]),
    )
    assert response.policy_hash == manifest_policy_hash(record.manifest)
    assert {(i.kind, i.reviewer_role) for i in response.required} == {("technical", CONTRACT), ("legal", LEGAL)}


# --------------------------------------------------------------------------- #
# Giving an approval
# --------------------------------------------------------------------------- #
async def test_a_super_admin_without_the_reviewer_role_cannot_approve() -> None:
    db = FakeDB()
    record = await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, record, "legal", LEGAL, SUPERADMIN)
    _refused(exc, 403, "senior_legal_approver")
    assert await SkillApprovalService(db).list(record.skill_key, record.version) == []


async def test_a_reviewer_cannot_claim_a_role_they_do_not_hold() -> None:
    db = FakeDB()
    record = await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, record, "legal", LEGAL, CONTRACT_EXPERT)
    _refused(exc, 403)


async def test_an_approval_the_pack_does_not_call_for_is_refused() -> None:
    db = FakeDB()
    record = await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, record, "technical", DELAY, _user("planner", DELAY))
    _refused(exc, 422, "does not call for")


async def test_the_evaluator_cannot_approve_their_own_evaluation() -> None:
    db = FakeDB()
    record = await _draft(db)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, record, "legal", LEGAL, _user("evaluator", LEGAL))
    _refused(exc, 409, "cannot approve")


async def test_one_person_cannot_give_both_the_technical_and_the_legal_approval() -> None:
    db = FakeDB()
    record = await _draft(db)
    both = _user("both", CONTRACT, LEGAL)
    await _approve(db, record, "technical", CONTRACT, both)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, record, "legal", LEGAL, both)
    _refused(exc, 409, "independent")


async def test_approval_needs_a_passing_evaluation_with_a_named_evaluator() -> None:
    db = FakeDB()
    record = await _draft(db)
    with pytest.raises(HTTPException) as failing:
        await _approve(db, record, "legal", LEGAL, COUNSEL, _report(score=0.5))
    _refused(failing, 422, "passing evaluation")
    with pytest.raises(HTTPException) as anonymous:
        await _approve(db, record, "legal", LEGAL, COUNSEL, _report(recorded_by=None))
    _refused(anonymous, 422, "recorded_by")


async def test_only_a_draft_is_reviewed() -> None:
    db = FakeDB()
    await _fully_approved(db)
    published = await _publish(db)
    with pytest.raises(HTTPException) as exc:
        await _approve(db, published, "legal", LEGAL, _user("counsel-2", LEGAL))
    _refused(exc, 409, "only a draft is reviewed")


async def test_recording_the_same_approval_twice_changes_nothing() -> None:
    db = FakeDB()
    record = await _draft(db)
    first = await _approve(db, record, "legal", LEGAL, COUNSEL)
    second = await _approve(db, record, "legal", LEGAL, COUNSEL)
    assert first.id == second.id
    assert len(await SkillApprovalService(db).list(record.skill_key, record.version)) == 1


async def test_only_the_reviewer_or_a_peer_in_the_same_role_can_revoke() -> None:
    db = FakeDB()
    record = await _draft(db)
    approval = await _approve(db, record, "legal", LEGAL, COUNSEL)
    service = SkillApprovalService(db)
    with pytest.raises(HTTPException) as exc:
        await service.revoke(approval.id, "not mine to withdraw", SUPERADMIN)
    _refused(exc, 403)
    peer_user = _user("counsel-2", LEGAL)
    await hold_platform(db, peer_user)
    peer = await service.revoke(approval.id, "peer review found an error", peer_user)
    assert peer.revoked_by == "counsel-2"


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #
async def test_an_approval_records_exactly_what_was_reviewed() -> None:
    db = FakeDB()
    record = await _draft(db)
    report = _report()
    approval = await _approve(db, record, "legal", LEGAL, COUNSEL, report)
    assert approval.content_hash == record.content_hash
    assert approval.eval_suite == "claims-golden-v1"
    assert approval.eval_report_hash == eval_report_hash(report)
    assert (approval.eval_score, approval.eval_threshold, approval.eval_case_count) == (0.92, 0.85, 12)
    assert approval.eval_recorded_by == "evaluator"
    assert approval.reviewer_user_id == "counsel" and approval.decided_at is not None
    statuses = {item.reviewer_role: item for item in approval_statuses(record, report, [approval])}
    assert statuses[LEGAL].satisfied and statuses[LEGAL].state == "ok"
    assert not statuses[CONTRACT].satisfied and statuses[CONTRACT].state == "missing"


def test_evaluation_hash_ignores_notes_but_not_results() -> None:
    base = _report()
    assert eval_report_hash(base) == eval_report_hash(base.model_copy(update={"notes": "re-typed"}))
    assert eval_report_hash(base) != eval_report_hash(base.model_copy(update={"case_count": 13}))
    assert eval_report_hash(base) != eval_report_hash(base.model_copy(update={"suite": "other"}))


# --------------------------------------------------------------------------- #
# Seed migration
# --------------------------------------------------------------------------- #
async def test_seed_migration_stores_the_four_packs_as_drafts_and_publishes_nothing() -> None:
    from rbac_backend.migrations.v20261115_0001_seed_claim_arbitration_skill_drafts import upgrade
    from rbac_backend.services.skills.system_skills import DRAFT_SKILL_KEYS

    db = FakeDB()
    dry = await upgrade(db, True)
    assert dry.status == "dry_run" and await db.skill_versions.count_documents({}) == 0

    applied = await upgrade(db, False)
    assert applied.status == "applied" and applied.warnings == [] and len(applied.notices) == 2
    stored = await db.skill_versions.find({}).to_list(length=100)
    assert {doc["skill_key"] for doc in stored} == set(DRAFT_SKILL_KEYS)
    assert all(doc["status"] == "draft" and doc.get("published_at") is None for doc in stored)
    assert await db.skills.count_documents({}) == 0  # no "latest published" pointer is created

    registry = SkillRegistry(db)
    for key in DRAFT_SKILL_KEYS:
        version, _, reason = await registry.resolve(key, "org1", None)
        assert version is None and reason == "No published version"

    again = await upgrade(db, False)
    assert await db.skill_versions.count_documents({}) == 4 and again.warnings == []
    assert all(
        op["operation"] != "insert_one" for op in again.operations if op["collection"] == "skill_versions"
    )


async def test_seed_migration_reports_a_stored_version_with_different_text_and_leaves_it() -> None:
    from rbac_backend.migrations.v20261115_0001_seed_claim_arbitration_skill_drafts import upgrade

    db = FakeDB()
    await db.skill_versions.insert_one(
        {"skill_key": CLAIMS_SKILL_KEY, "version": "1.0.0", "status": "draft", "content_hash": "0" * 64}
    )
    result = await upgrade(db, False)
    assert len(result.warnings) == 1 and CLAIMS_SKILL_KEY in result.warnings[0]
    kept = await db.skill_versions.find_one({"skill_key": CLAIMS_SKILL_KEY, "version": "1.0.0"})
    assert kept["content_hash"] == "0" * 64


async def test_a_seeded_draft_publishes_only_through_the_gate() -> None:
    from rbac_backend.migrations.v20261115_0001_seed_claim_arbitration_skill_drafts import upgrade

    db = FakeDB()
    await upgrade(db, False)
    registry = SkillRegistry(db)
    with pytest.raises(HTTPException) as exc:
        await registry.publish(CLAIMS_SKILL_KEY, "1.0.0", _report(), PUBLISHER)
    _refused(exc, 422, "Approval gate not met")
    record = await registry.get_version(CLAIMS_SKILL_KEY, "1.0.0")
    await _approve(db, record, "technical", CONTRACT, CONTRACT_EXPERT)
    await _approve(db, record, "legal", LEGAL, COUNSEL)
    published = await registry.publish(CLAIMS_SKILL_KEY, "1.0.0", _report(), PUBLISHER)
    assert published.status == "published"
    effective = {item.skill_key: item for item in await registry.list_effective("org1", None)}
    assert effective[CLAIMS_SKILL_KEY].available and effective[CLAIMS_SKILL_KEY].version == "1.0.0"


async def test_a_fresh_install_publishes_only_the_letter_packs() -> None:
    """Both seed migrations in order, as a new database receives them.

    The older seed iterates the whole bundle. It must not hand the draft packs
    a publication date or a "latest published" pointer before the later
    migration stores them as drafts.
    """
    from rbac_backend.migrations.v20261114_0002_seed_system_skills import upgrade as seed_published
    from rbac_backend.migrations.v20261115_0001_seed_claim_arbitration_skill_drafts import (
        upgrade as seed_drafts,
    )
    from rbac_backend.services.skills.system_skills import DRAFT_SKILL_KEYS

    db = FakeDB()
    first = await seed_published(db, False)
    assert first.warnings == []
    after_first = await db.skill_versions.find({}).to_list(length=100)
    assert len(after_first) == 3 and not {doc["skill_key"] for doc in after_first} & set(DRAFT_SKILL_KEYS)

    await seed_drafts(db, False)
    versions = {doc["skill_key"]: doc for doc in await db.skill_versions.find({}).to_list(length=100)}
    assert len(versions) == 7
    for key in DRAFT_SKILL_KEYS:
        assert versions[key]["status"] == "draft"
        assert versions[key].get("published_at") is None and versions[key].get("published_by") is None
    pointers = await db.skills.find({}).to_list(length=100)
    assert len(pointers) == 3 and not {doc["skill_key"] for doc in pointers} & set(DRAFT_SKILL_KEYS)
    assert all(doc["latest_published_version"] == "1.0.0" for doc in pointers)


# --------------------------------------------------------------------------- #
# 4B-05: a server that cannot run transactions refuses publication decisions
# --------------------------------------------------------------------------- #
async def test_a_standalone_server_refuses_instead_of_writing_unserialised() -> None:
    from pymongo.errors import OperationFailure

    from rbac_backend.services.skills.transactions import run_in_transaction

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def with_transaction(self, callback):
            raise OperationFailure(
                "Transaction numbers are only allowed on a replica set member or mongos", code=20
            )

    class Client:
        async def start_session(self):
            return Session()

    db = SimpleNamespace(client=Client())
    called = []

    async def callback(session):
        called.append(session)

    with pytest.raises(HTTPException) as exc:
        await run_in_transaction(db, callback)
    _refused(exc, 503, "needs a MongoDB replica set")
    assert called == [], "nothing may be written outside a transaction"
