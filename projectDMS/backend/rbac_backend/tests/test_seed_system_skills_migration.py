"""Migration 20261114_0002 seeds the bundled system skills.

It writes two collections per skill: the write-once ``skill_versions`` row and
the ``skills`` summary row. A crash between the two used to be permanent: the
rerun found the version, skipped it, and never wrote the summary row.
"""

from __future__ import annotations

import copy

import pytest

from rbac_backend.migrations.v20261114_0002_seed_system_skills import upgrade
from rbac_backend.services.skills.system_skills import system_skill_versions
from rbac_backend.tests.bench_fakes import FakeDB


def _snapshot(db: FakeDB) -> dict:
    strip = lambda docs: sorted(  # noqa: E731
        ({k: v for k, v in d.items() if k != "_id"} for d in docs), key=lambda d: (d.get("skill_key"), d.get("version"))
    )
    return {"versions": strip(db.skill_versions.docs), "skills": strip(db.skills.docs)}


async def test_fresh_database_gets_every_version_and_summary_row() -> None:
    db = FakeDB()
    result = await upgrade(db, dry_run=False)
    keys = {v.skill_key for v in system_skill_versions()}
    assert {d["skill_key"] for d in db.skill_versions.docs} == keys
    assert {d["skill_key"] for d in db.skills.docs} == keys
    assert all(d["latest_published_version"] == "1.0.0" for d in db.skills.docs)
    assert result.warnings == []


async def test_dry_run_writes_nothing() -> None:
    db = FakeDB()
    await upgrade(db, dry_run=True)
    assert db.skill_versions.docs == [] and db.skills.docs == []


async def test_a_crash_after_the_version_insert_is_repaired_by_the_rerun(monkeypatch) -> None:
    db = FakeDB()
    real_update = db.skills.update_one

    async def crash(*args, **kwargs):
        raise RuntimeError("process killed between the two writes")

    monkeypatch.setattr(db.skills, "update_one", crash)
    with pytest.raises(RuntimeError):
        await upgrade(db, dry_run=False)
    assert len(db.skill_versions.docs) == 1 and db.skills.docs == []

    monkeypatch.setattr(db.skills, "update_one", real_update)
    result = await upgrade(db, dry_run=False)
    keys = {v.skill_key for v in system_skill_versions()}
    assert {d["skill_key"] for d in db.skills.docs} == keys
    assert len(db.skill_versions.docs) == len(keys)
    assert result.warnings == []


async def test_rerun_changes_nothing_and_never_rewinds_a_newer_summary() -> None:
    db = FakeDB()
    await upgrade(db, dry_run=False)
    # The registry later publishes 1.1.0 of one skill and refreshes its summary.
    db.skills.docs[0]["latest_published_version"] = "1.1.0"
    before = copy.deepcopy(_snapshot(db))
    await upgrade(db, dry_run=False)
    assert _snapshot(db) == before
