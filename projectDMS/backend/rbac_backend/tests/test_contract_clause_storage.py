"""Phase 1 tests for the Contract Clause Chunking Agent storage layer:
hierarchy, scope enforcement, duplicate prevention, long-clause splitting and
checksum-based change detection.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.contract_clause import ClauseScopeError, ClauseStorageService


# --------------------------------------------------------------------------- #
# In-memory async Mongo doubles
# --------------------------------------------------------------------------- #
class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.created_indexes: list = []

    async def create_index(self, keys, **kwargs):
        self.created_indexes.append((keys, kwargs))

    async def find_one(self, filt):
        uid = filt.get("clause_uid")
        doc = self.docs.get(uid)
        return dict(doc) if doc is not None else None

    async def update_one(self, filt, update, upsert=False):
        uid = filt["clause_uid"]
        set_fields = update.get("$set", {})
        set_on_insert = update.get("$setOnInsert", {})
        if uid in self.docs:
            self.docs[uid].update(set_fields)
        elif upsert:
            doc = {"clause_uid": uid}
            doc.update(set_on_insert)
            doc.update(set_fields)
            self.docs[uid] = doc

        class _Result:
            upserted_id = None if uid in ({} if upsert else {}) else None

        return _Result()


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class DenyPolicy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append({"permission": permission, **kwargs})
        raise HTTPException(status_code=403, detail="denied")


class AllowPolicy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append({"permission": permission, **kwargs})


SCOPE = dict(org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-1")


# --------------------------------------------------------------------------- #
# Parent-child hierarchy
# --------------------------------------------------------------------------- #
def test_clause_path_and_level_for_dotted_numbering():
    assert ClauseStorageService.clause_path("8.4.1") == ["8", "8.4", "8.4.1"]
    assert ClauseStorageService.level("8.4.1") == 3
    assert ClauseStorageService.parent_clause_no("8.4.1") == "8.4"

    assert ClauseStorageService.clause_path("8") == ["8"]
    assert ClauseStorageService.level("8") == 1
    assert ClauseStorageService.parent_clause_no("8") is None


def test_clause_number_prefixes_and_non_dotted_are_normalized():
    assert ClauseStorageService.normalize_clause_no("Sub-Clause 8.4") == "8.4"
    assert ClauseStorageService.clause_path("Clause 8.4") == ["8", "8.4"]
    # Non-dotted identifiers stay at level 1 with no parent.
    assert ClauseStorageService.clause_path("Appendix 1") == ["Appendix 1"]
    assert ClauseStorageService.parent_clause_no("(a)") is None
    assert ClauseStorageService.level("(a)") == 1


def test_build_record_populates_hierarchy():
    svc = ClauseStorageService(FakeDB())
    rec = svc.build_record(
        **SCOPE, clause_no="8.4", clause_title="Extension of Time",
        text="The Contractor shall...", document_type="GCC",
    )
    assert rec.clause_no == "8.4"
    assert rec.parent_clause_no == "8"
    assert rec.clause_path == ["8", "8.4"]
    assert rec.level == 2
    assert rec.checksum  # computed
    assert rec.quality_status == "validated"
    assert rec.is_authorised_for_ai is True


# --------------------------------------------------------------------------- #
# Scope + authorization (req 2, 3, 4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("missing", ["org_id", "project_id", "contract_id", "document_id"])
def test_validate_scope_blocks_missing_key(missing):
    args = dict(SCOPE)
    args[missing] = ""
    with pytest.raises(ClauseScopeError) as exc:
        ClauseStorageService.validate_scope(**args)
    assert missing in str(exc.value)


def test_build_record_requires_scope():
    svc = ClauseStorageService(FakeDB())
    with pytest.raises(ClauseScopeError):
        svc.build_record(org_id="org-A", project_id="", contract_id="ct-1",
                         document_id="doc-1", clause_no="1")


@pytest.mark.asyncio
async def test_processing_run_denied_without_permission():
    policy = DenyPolicy()
    svc = ClauseStorageService(FakeDB(), policy_service=policy)
    with pytest.raises(HTTPException) as exc:
        await svc.authorize_processing_run(
            object(), org_id="org-A", project_id="proj-A",
            contract_id="ct-1", document_id="doc-1",
        )
    assert exc.value.status_code == 403
    # First permission checked is contract read.
    assert policy.calls[0]["permission"] == Permissions.CONTRACT_READ
    assert policy.calls[0]["organization_id"] == "org-A"


@pytest.mark.asyncio
async def test_processing_run_requests_all_required_permissions():
    policy = AllowPolicy()
    svc = ClauseStorageService(FakeDB(), policy_service=policy)
    await svc.authorize_processing_run(
        object(), org_id="org-A", project_id="proj-A",
        contract_id="ct-1", document_id="doc-1",
    )
    requested = {c["permission"] for c in policy.calls}
    assert {
        Permissions.CONTRACT_READ,
        Permissions.CONTRACT_CLAUSE_CREATE,
        Permissions.AI_CONTRACT_PROCESSING_RUN,
    } <= requested


@pytest.mark.asyncio
async def test_processing_run_blocks_bad_scope_before_authorizing():
    policy = AllowPolicy()
    svc = ClauseStorageService(FakeDB(), policy_service=policy)
    with pytest.raises(ClauseScopeError):
        await svc.authorize_processing_run(
            object(), org_id="org-A", project_id="proj-A",
            contract_id="", document_id="doc-1",
        )
    assert policy.calls == []  # scope guard runs before any authorization


# --------------------------------------------------------------------------- #
# Duplicate prevention + checksum change detection (req 21, 22)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reprocessing_updates_not_duplicates():
    db = FakeDB()
    svc = ClauseStorageService(db)
    rec = svc.build_record(**SCOPE, clause_no="8.4", text="original text", document_type="GCC")

    assert await svc.save_clause(rec) == "inserted"
    # Same clause, unchanged text -> no duplicate, no content change.
    again = svc.build_record(**SCOPE, clause_no="8.4", text="original text", document_type="GCC")
    assert await svc.save_clause(again) == "unchanged"

    collection = db[ClauseStorageService.COLLECTION]
    assert len(collection.docs) == 1  # exactly one record


@pytest.mark.asyncio
async def test_checksum_change_marks_reembed():
    db = FakeDB()
    svc = ClauseStorageService(db)
    rec = svc.build_record(**SCOPE, clause_no="8.4", text="v1", document_type="GCC")
    await svc.save_clause(rec)

    changed = svc.build_record(**SCOPE, clause_no="8.4", text="v2 amended", document_type="GCC")
    assert changed.checksum != rec.checksum
    assert await svc.save_clause(changed) == "updated"

    stored = db[ClauseStorageService.COLLECTION].docs[rec.clause_uid]
    assert stored["embedding_status"] == "pending"  # reset for re-embedding
    assert len(db[ClauseStorageService.COLLECTION].docs) == 1


# --------------------------------------------------------------------------- #
# Long clause splitting (req 14)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_long_clause_parts_share_clause_no_distinct_records():
    db = FakeDB()
    svc = ClauseStorageService(db)
    parts = [
        svc.build_record(
            **SCOPE, clause_no="8.4", text=f"part {i}", document_type="GCC",
            chunk_type="clause_part", chunk_part=i, chunk_total=3, chunk_index=i - 1,
        )
        for i in (1, 2, 3)
    ]
    # Same clause_no, distinct idempotency keys per part.
    assert {p.clause_no for p in parts} == {"8.4"}
    assert len({p.clause_uid for p in parts}) == 3

    counts = await svc.save_clauses(parts)
    assert counts["inserted"] == 3
    assert len(db[ClauseStorageService.COLLECTION].docs) == 3
    # Reprocessing the same parts does not duplicate.
    counts2 = await svc.save_clauses(parts)
    assert counts2["unchanged"] == 3
    assert len(db[ClauseStorageService.COLLECTION].docs) == 3


# --------------------------------------------------------------------------- #
# Validation rules (req 11 table)
# --------------------------------------------------------------------------- #
def test_missing_document_type_marked_incomplete():
    svc = ClauseStorageService(FakeDB())
    rec = svc.build_record(**SCOPE, clause_no="8.4", text="x", document_type=None)
    assert rec.quality_status == "incomplete_metadata"
    assert rec.is_authorised_for_ai is False


def test_low_confidence_requires_human_review():
    svc = ClauseStorageService(FakeDB())
    rec = svc.build_record(**SCOPE, clause_no="8.4", text="x", document_type="GCC", confidence="low")
    assert rec.human_review_required is True
    assert rec.quality_status == "needs_review"
    assert rec.is_authorised_for_ai is False
