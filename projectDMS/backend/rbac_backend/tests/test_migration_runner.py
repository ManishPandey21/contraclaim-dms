from __future__ import annotations

from typing import Any, Dict, List

import pytest

from rbac_backend.initial_data.seed_catalog import (
    SEED_CATALOG_ID,
    seed_catalog_digest,
    seed_catalog_payload,
    seed_catalog_record,
    seed_catalog_validation_issues,
)
from rbac_backend.migrations import MIGRATIONS, Migration, MigrationResult, MigrationRunner
from rbac_backend.migrations.v20260705_0001_arbitration_hardening_indexes import upgrade as upgrade_arbitration_hardening
from rbac_backend.migrations.v20260721_0002_arbitration_workflow_foundation import ensure_compatible_index
from rbac_backend.migrations.v20260722_0001_langgraph_checkpoint_ttl_compatibility import (
    DEFAULT_TTL_INDEX,
    LEGACY_CHECKPOINT_TTL_INDEX,
    TTL_SECONDS,
    upgrade as upgrade_langgraph_ttl,
)
from rbac_backend.migrations.v20260722_0002_arbitration_phase6_scope_index import (
    INDEX_KEYS as ARB_PHASE6_SCOPE_INDEX_KEYS,
    INDEX_NAME as ARB_PHASE6_SCOPE_INDEX_NAME,
    upgrade as upgrade_arbitration_phase6_scope,
)
from rbac_backend.migrations.v20260722_0003_arbitration_filing_export_effects import (
    upgrade as upgrade_arbitration_export_effects,
)
from rbac_backend.migrations.v20260723_0001_arbitration_effect_recovery import (
    upgrade as upgrade_arbitration_effect_recovery,
)
from rbac_backend.migrations.runner import LEDGER_COLLECTION


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self.docs]


class _Collection:
    def __init__(self, name: str):
        self.name = name
        self.docs: Dict[Any, Dict[str, Any]] = {}
        self.indexes: List[tuple[Any, Dict[str, Any]]] = []
        self.update_calls = 0

    def find(self, query: Dict[str, Any]):
        if not query:
            return _Cursor(list(self.docs.values()))
        matched = []
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                matched.append(doc)
        return _Cursor(matched)

    async def find_one(self, query: Dict[str, Any]):
        rows = await self.find(query).to_list()
        return rows[0] if rows else None

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name") or "idx"

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        self.update_calls += 1
        doc_id = query.get("_id")
        doc = self.docs.get(doc_id)
        if doc is None and upsert:
            doc = {"_id": doc_id}
            doc.update(update.get("$setOnInsert") or {})
            self.docs[doc_id] = doc
        if doc is not None:
            doc.update(update.get("$set") or {})


class _DB:
    def __init__(self):
        self.collections: Dict[str, _Collection] = {}

    def __getitem__(self, name: str):
        return self.collections.setdefault(name, _Collection(name))

    def __getattr__(self, name: str):
        return self[name]


async def _noop_upgrade(db, dry_run: bool):
    if not dry_run:
        await db.example.update_one({"_id": "applied"}, {"$set": {"applied": True}}, upsert=True)
    return MigrationResult(
        version="20260629_9999",
        name="noop",
        status="dry_run" if dry_run else "applied",
        operations=[{"operation": "noop"}],
    )


@pytest.mark.asyncio
async def test_migration_dry_run_does_not_write_ledger_or_data():
    db = _DB()
    runner = MigrationRunner(
        db,
        [Migration("20260629_9999", "noop", "No-op test migration", _noop_upgrade)],
    )

    results = await runner.run(apply=False)

    assert [result.status for result in results] == ["dry_run"]
    assert db[LEDGER_COLLECTION].docs == {}
    assert db["example"].docs == {}
    assert db[LEDGER_COLLECTION].indexes == []


@pytest.mark.asyncio
async def test_migration_apply_records_once_and_rerun_skips():
    db = _DB()
    runner = MigrationRunner(
        db,
        [Migration("20260629_9999", "noop", "No-op test migration", _noop_upgrade)],
    )

    first = await runner.run(apply=True)
    second = await runner.run(apply=True)

    assert [result.status for result in first] == ["applied"]
    assert [result.status for result in second] == ["skipped"]
    assert db["example"].docs["applied"]["applied"] is True
    assert db[LEDGER_COLLECTION].docs["20260629_9999"]["version"] == "20260629_9999"
    assert db[LEDGER_COLLECTION].update_calls == 1
    assert db[LEDGER_COLLECTION].indexes[0] == ("version", {"unique": True, "background": True})


def test_migration_catalog_versions_are_unique_and_sorted():
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


@pytest.mark.asyncio
async def test_phase6_scope_migration_indexes_bounded_acceptance_query():
    db = _DB()

    dry_run = await upgrade_arbitration_phase6_scope(db, dry_run=True)
    applied = await upgrade_arbitration_phase6_scope(db, dry_run=False)

    assert dry_run.operations == [
        {
            "operation": "create_index",
            "collection": "arbitration_workflow_runs",
            "keys": ARB_PHASE6_SCOPE_INDEX_KEYS,
            "name": ARB_PHASE6_SCOPE_INDEX_NAME,
            "unique": False,
        }
    ]
    assert applied.status == "applied"
    assert (
        ARB_PHASE6_SCOPE_INDEX_KEYS,
        {"name": ARB_PHASE6_SCOPE_INDEX_NAME, "background": True},
    ) in db.arbitration_workflow_runs.indexes


@pytest.mark.asyncio
async def test_filing_export_effect_migration_adds_unique_effect_and_lease_indexes():
    db = _DB()

    result = await upgrade_arbitration_export_effects(db, dry_run=False)

    assert result.status == "applied"
    assert (
        [("effect_key", 1)],
        {
            "name": "effect_key_1",
            "unique": True,
            "partialFilterExpression": {"effect_key": {"$type": "string"}},
            "background": True,
        },
    ) in db.arbitration_bundle_exports.indexes
    assert (
        [("status", 1), ("execution_lease_expires_at", 1)],
        {"name": "status_1_execution_lease_expires_at_1", "background": True},
    ) in db.arbitration_bundle_exports.indexes


@pytest.mark.asyncio
async def test_effect_recovery_migration_uses_supported_active_record_partial_indexes():
    db = _DB()

    result = await upgrade_arbitration_effect_recovery(db, dry_run=False)

    assert result.status == "applied"
    assert (
        [
            ("organization_id", 1),
            ("project_id", 1),
            ("criterion", 1),
            ("evidence_hash", 1),
        ],
        {
            "name": "arb_acceptance_evidence_scope_criterion_hash",
            "unique": True,
            "background": True,
            "partialFilterExpression": {"invalidated_at": None},
        },
    ) in db.arbitration_acceptance_evidence.indexes
    assert (
        [
            ("organization_id", 1),
            ("project_id", 1),
            ("acceptance_bundle_hash", 1),
            ("actor_id", 1),
        ],
        {
            "name": "arb_acceptance_signoff_scope_bundle_actor",
            "unique": True,
            "background": True,
            "partialFilterExpression": {"invalidated_at": None},
        },
    ) in db.arbitration_acceptance_signoffs.indexes


def test_seed_catalog_digest_is_stable_and_validates_current_seeds():
    payload = seed_catalog_payload()
    assert seed_catalog_digest(payload) == seed_catalog_digest(payload)
    assert seed_catalog_validation_issues(payload) == []

    record = seed_catalog_record()
    assert record["_id"] == SEED_CATALOG_ID
    assert record["digest"] == seed_catalog_digest(payload)
    assert record["permission_count"] >= record["client_dms_permission_count"]


@pytest.mark.asyncio
async def test_arbitration_hardening_migration_creates_background_job_indexes():
    db = _DB()

    dry_run = await upgrade_arbitration_hardening(db, dry_run=True)
    applied = await upgrade_arbitration_hardening(db, dry_run=False)

    assert dry_run.status == "dry_run"
    assert any(operation["collection"] == "arbitration_bundle_exports" for operation in dry_run.operations)
    assert applied.status == "applied"
    assert (
        [("case_id", 1), ("status", 1), ("created_at", -1)],
        {"background": True},
    ) in db.arbitration_bundle_exports.indexes
    assert ("background_job_id", {"background": True}) in db.arbitration_agent_runs.indexes


@pytest.mark.asyncio
async def test_workflow_migration_reuses_exact_index_with_a_different_name():
    class _IndexConflict(Exception):
        code = 85

    class _ExistingIndexCollection:
        async def create_index(self, keys, **kwargs):
            raise _IndexConflict("same key specification already has another name")

        async def index_information(self):
            return {
                "draft_id_1_version_1": {
                    "key": [("draft_id", 1), ("version", 1)],
                    "unique": True,
                }
            }

    name = await ensure_compatible_index(
        _ExistingIndexCollection(),
        [("draft_id", 1), ("version", 1)],
        name="arb_draft_version_unique",
        unique=True,
        background=True,
    )

    assert name == "draft_id_1_version_1"


@pytest.mark.asyncio
async def test_workflow_migration_rejects_incompatible_existing_index():
    class _IndexConflict(Exception):
        code = 85

    class _ExistingIndexCollection:
        async def create_index(self, keys, **kwargs):
            raise _IndexConflict("same key specification has incompatible options")

        async def index_information(self):
            return {
                "draft_id_1_version_1": {
                    "key": [("draft_id", 1), ("version", 1)],
                    "unique": False,
                }
            }

    with pytest.raises(_IndexConflict):
        await ensure_compatible_index(
            _ExistingIndexCollection(),
            [("draft_id", 1), ("version", 1)],
            name="arb_draft_version_unique",
            unique=True,
            background=True,
        )


@pytest.mark.asyncio
async def test_langgraph_ttl_migration_replaces_compatible_named_index():
    class _TTLCollection:
        def __init__(self, indexes=None):
            self.indexes = dict(indexes or {})
            self.dropped = []

        async def index_information(self):
            return self.indexes

        async def drop_index(self, name):
            self.dropped.append(name)
            self.indexes.pop(name, None)

        async def create_index(self, keys, **kwargs):
            self.indexes[DEFAULT_TTL_INDEX] = {
                "key": [(keys, 1)],
                "expireAfterSeconds": kwargs["expireAfterSeconds"],
            }
            return DEFAULT_TTL_INDEX

    class _TTLDb:
        def __init__(self):
            self.collections = {
                "arbitration_langgraph_checkpoints": _TTLCollection(
                    {
                        LEGACY_CHECKPOINT_TTL_INDEX: {
                            "key": [("created_at", 1)],
                            "expireAfterSeconds": TTL_SECONDS,
                        }
                    }
                ),
                "arbitration_langgraph_checkpoint_writes": _TTLCollection(),
            }

        def __getitem__(self, name):
            return self.collections[name]

    db = _TTLDb()
    result = await upgrade_langgraph_ttl(db, dry_run=False)

    assert result.status == "applied"
    assert db.collections["arbitration_langgraph_checkpoints"].dropped == [LEGACY_CHECKPOINT_TTL_INDEX]
    assert DEFAULT_TTL_INDEX in db.collections["arbitration_langgraph_checkpoints"].indexes
    assert DEFAULT_TTL_INDEX in db.collections["arbitration_langgraph_checkpoint_writes"].indexes


@pytest.mark.asyncio
async def test_langgraph_ttl_migration_rejects_incompatible_ttl():
    class _TTLCollection:
        async def index_information(self):
            return {
                LEGACY_CHECKPOINT_TTL_INDEX: {
                    "key": [("created_at", 1)],
                    "expireAfterSeconds": 60,
                }
            }

    class _TTLDb:
        def __getitem__(self, name):
            return _TTLCollection()

    with pytest.raises(RuntimeError, match="Incompatible checkpoint TTL index"):
        await upgrade_langgraph_ttl(_TTLDb(), dry_run=False)


# --------------------------------------------------------------------------- #
# R-A8V / F-A8V-2 - a ledger row whose migration no longer exists was invisible
# --------------------------------------------------------------------------- #
#
# Measured on the live production database on 2026-09-13, read-only:
# `schema_migrations` carries **18** rows, two of which - `20260730_0001`
# (`tenant_context_active_flags`) and `20260806_0001`
# (`subscription_current_scope_unique`) - name migrations that exist in neither
# the release branch nor `contraclaim/main`. They were applied by an earlier
# deployment and their code has since left the deployed line.
#
# `plan()` iterates the CATALOGUE and asks the ledger only whether each entry is
# present, so those two rows appear nowhere in `--list`. On a production copy the
# command reports 16 applied and 3 pending against a ledger holding 18 rows, and
# the two extra are not mentioned, not counted and not explained.
#
# That is precisely the signal Gate 6 bullet 3 exists to surface: this database
# carries schema effects from code this deployment does not have, and nothing
# forward-only will ever re-derive them. The runner's behaviour is unchanged -
# an unknown version is still ignored for ordering and execution, which is
# correct for a forward-only runner. What changes is that it is now *reported*.


@pytest.mark.asyncio
async def test_a_ledger_row_with_no_migration_in_the_catalogue_is_reported():
    db = _DB()
    db[LEDGER_COLLECTION].docs["20260730_0001"] = {
        "_id": "20260730_0001",
        "version": "20260730_0001",
        "name": "tenant_context_active_flags",
        "description": "applied by a deployment whose code this tree does not carry",
    }
    runner = MigrationRunner(
        db,
        [Migration("20260906_0001", "permission_name_unique", "Collapse duplicates", _noop_upgrade)],
    )

    rows = await runner.plan()

    by_version = {row["version"]: row for row in rows}
    assert set(by_version) == {"20260730_0001", "20260906_0001"}
    assert by_version["20260906_0001"]["status"] == "pending"

    orphan = by_version["20260730_0001"]
    assert orphan["status"] == "applied_not_in_catalogue"
    assert orphan["name"] == "tenant_context_active_flags"
    assert [row["version"] for row in rows] == sorted(row["version"] for row in rows), (
        "the plan is read as an ordered history; an orphan appended at the end "
        "would read as the most recent migration"
    )


@pytest.mark.asyncio
async def test_an_orphan_ledger_row_changes_nothing_about_what_runs():
    """Reporting it is the whole change. A forward-only runner has no code for
    an absent migration and must not invent one - the risk of this diagnostic is
    that it grows into a re-run or a failure, so that is pinned here."""
    db = _DB()
    db[LEDGER_COLLECTION].docs["20260730_0001"] = {
        "_id": "20260730_0001",
        "version": "20260730_0001",
        "name": "tenant_context_active_flags",
    }
    runner = MigrationRunner(
        db,
        [Migration("20260629_9999", "noop", "No-op test migration", _noop_upgrade)],
    )

    results = await runner.run(apply=True)

    assert [result.version for result in results] == ["20260629_9999"]
    assert [result.status for result in results] == ["applied"]
    assert db["example"].docs["applied"]["applied"] is True


@pytest.mark.asyncio
async def test_a_clean_ledger_reports_no_orphan():
    """The negative control. Without it this diagnostic could be firing on every
    database and the test above would still pass."""
    db = _DB()
    runner = MigrationRunner(
        db,
        [Migration("20260629_9999", "noop", "No-op test migration", _noop_upgrade)],
    )

    await runner.run(apply=True)
    rows = await runner.plan()

    assert [row["status"] for row in rows] == ["applied"]
    assert all(row["status"] != "applied_not_in_catalogue" for row in rows)


@pytest.mark.asyncio
async def test_a_target_does_not_hide_an_orphan_below_it():
    """`--target` bounds what may RUN. It must not bound what is reported about
    the database, or the narrowest target would be the quietest report."""
    db = _DB()
    db[LEDGER_COLLECTION].docs["20260806_0001"] = {
        "_id": "20260806_0001",
        "version": "20260806_0001",
        "name": "subscription_current_scope_unique",
    }
    runner = MigrationRunner(
        db,
        [
            Migration("20260629_9999", "noop", "No-op test migration", _noop_upgrade),
            Migration("20260906_0001", "later", "A later migration", _noop_upgrade),
        ],
    )

    rows = await runner.plan(target="20260629_9999")

    versions = [row["version"] for row in rows]
    assert "20260806_0001" in versions
    assert "20260906_0001" not in versions
