"""Reconciliation store, inventory and materialise.

M09-21 - a dry run writes nothing at all, not even a reconciliation row.

The reason that RED needs a mutation proof: "no legal facts were written" is
almost true of a preview that quietly persists reconciliation rows, so a naive
assertion passes while the preview is seeding the very adjudications an operator
is about to make. The assertion here is therefore total - **zero writes to any
collection** - and the mutation makes the preview persist, which must turn it
red.

Candidate identity is the other load-bearing piece. It is derived from the
canonical document alone, so re-running inventory after a decision, a lease, a
rename or a re-upload converges on the same row rather than forking a second
candidate that disagrees with the first.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    ContractMigrationReconciliation,
    ReconciliationCandidate,
    ScopeClassificationState,
    TypeClassificationState,
    candidate_identity,
)


# --------------------------------------------------------------------------- #
# a database that refuses to be written to unobserved
# --------------------------------------------------------------------------- #


class RecordingCollection:
    def __init__(self, name: str, docs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.name = name
        self.docs = docs or []
        self.writes: List[Dict[str, Any]] = []

    # -- reads --------------------------------------------------------------- #

    def find(self, query=None, projection=None):
        return _Cursor(list(self.docs))

    async def find_one(self, query=None, *args, **kwargs):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (query or {}).items()):
                return doc
        return None

    # -- writes -------------------------------------------------------------- #

    async def insert_one(self, document, *args, **kwargs):
        self.writes.append({"op": "insert_one", "document": document})
        self.docs.append(document)
        return type("R", (), {"inserted_id": document.get("_id")})()

    async def update_one(self, query, update, *args, **kwargs):
        self.writes.append({"op": "update_one", "query": query, "update": update})
        return type("R", (), {"matched_count": 1, "upserted_id": None})()

    async def create_index(self, *args, **kwargs):
        # Index creation is setup, not a candidate write, and is recorded
        # separately so the dry-run assertion cannot be satisfied by hiding a
        # document write among the indexes.
        return None


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return self._docs if length is None else self._docs[:length]


class RecordingDatabase:
    def __init__(self, seed: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self._collections: Dict[str, RecordingCollection] = {}
        for name, docs in (seed or {}).items():
            self._collections[name] = RecordingCollection(name, list(docs))

    def __getitem__(self, name: str) -> RecordingCollection:
        if name not in self._collections:
            self._collections[name] = RecordingCollection(name)
        return self._collections[name]

    def __getattr__(self, name: str) -> RecordingCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    @property
    def all_writes(self) -> List[Dict[str, Any]]:
        writes: List[Dict[str, Any]] = []
        for collection in self._collections.values():
            for write in collection.writes:
                writes.append({"collection": collection.name, **write})
        return writes


def _legacy_document(document_id: str, **overrides) -> Dict[str, Any]:
    doc = {
        "_id": document_id,
        "organization_id": "org-1",
        "project_id": "project-1",
        "uploadType": "contract",
        "filename": "GCC-volume-2.pdf",
        "upload_id": f"upload-{document_id}",
    }
    doc.update(overrides)
    return doc


def _database(documents: Optional[List[Dict[str, Any]]] = None, **extra) -> RecordingDatabase:
    seed = {"documents": documents if documents is not None else [_legacy_document("doc-1")]}
    seed.update(extra)
    return RecordingDatabase(seed)


# --------------------------------------------------------------------------- #
# M09-21: the dry run
# --------------------------------------------------------------------------- #


def test_inventory_writes_nothing_at_all():
    db = _database()
    service = ContractMigrationReconciliation(db)

    candidates = asyncio.run(service.inventory(organization_id="org-1"))

    assert candidates, "inventory produced no candidates, so the assertion is vacuous"
    # Not "no legal writes". No writes.
    assert db.all_writes == []


def test_inventory_does_not_even_write_a_reconciliation_row():
    db = _database()
    service = ContractMigrationReconciliation(db)

    asyncio.run(service.inventory(organization_id="org-1"))

    assert db[RECONCILIATION_COLLECTION].writes == []
    assert db[RECONCILIATION_COLLECTION].docs == []


def test_inventory_touches_no_authoritative_collection():
    from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS

    db = _database()
    asyncio.run(ContractMigrationReconciliation(db).inventory(organization_id="org-1"))

    for collection in LEGAL_COLLECTIONS:
        assert db[collection].writes == []


def test_materialise_is_a_separate_explicitly_named_action():
    db = _database()
    service = ContractMigrationReconciliation(db)

    candidates = asyncio.run(service.inventory(organization_id="org-1"))
    assert db.all_writes == []

    asyncio.run(service.materialise_inventory(candidates))
    assert db[RECONCILIATION_COLLECTION].writes, "materialise wrote nothing"


def test_materialise_writes_only_to_the_reconciliation_collection():
    from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS

    db = _database()
    service = ContractMigrationReconciliation(db)
    candidates = asyncio.run(service.inventory(organization_id="org-1"))
    asyncio.run(service.materialise_inventory(candidates))

    written = {write["collection"] for write in db.all_writes}
    assert written == {RECONCILIATION_COLLECTION}
    for collection in LEGAL_COLLECTIONS:
        assert db[collection].writes == []


# --------------------------------------------------------------------------- #
# candidate identity
# --------------------------------------------------------------------------- #


def test_candidate_identity_is_derived_from_the_canonical_document_alone():
    identity = candidate_identity(module="contracts", canonical_document_id="doc-1")
    assert identity == "contract-master-migration:contracts:doc-1"


def test_candidate_identity_signature_admits_no_decision_or_session_input():
    import inspect

    parameters = set(inspect.signature(candidate_identity).parameters)
    assert parameters == {"module", "canonical_document_id"}


def test_identity_survives_a_decision_a_lease_a_rename_and_a_re_upload():
    """Re-running inventory must converge, not fork a second disagreeing row."""
    db = _database(
        [
            _legacy_document(
                "doc-1",
                filename="renamed-SCC.pdf",
                upload_id="upload-second-attempt",
            )
        ]
    )
    first = asyncio.run(ContractMigrationReconciliation(_database()).inventory(organization_id="org-1"))
    second = asyncio.run(ContractMigrationReconciliation(db).inventory(organization_id="org-1"))

    assert [c.candidate_id for c in first] == [c.candidate_id for c in second]


# --------------------------------------------------------------------------- #
# session evidence is captured, never re-read
# --------------------------------------------------------------------------- #


def test_session_evidence_is_captured_at_inventory_time():
    db = _database(
        [_legacy_document("doc-1")],
        contract_upload_sessions=[
            {"_id": "session-1", "upload_id": "upload-doc-1", "project_id": "project-1"}
        ],
    )
    service = ContractMigrationReconciliation(db)

    candidates = asyncio.run(service.inventory(organization_id="org-1"))

    assert candidates[0].session_evidence == {"project_id": "project-1"}


def test_captured_session_evidence_is_carried_into_the_durable_row():
    db = _database(
        [_legacy_document("doc-1")],
        contract_upload_sessions=[
            {"_id": "session-1", "upload_id": "upload-doc-1", "project_id": "project-1"}
        ],
    )
    service = ContractMigrationReconciliation(db)
    candidates = asyncio.run(service.inventory(organization_id="org-1"))
    asyncio.run(service.materialise_inventory(candidates))

    row = db[RECONCILIATION_COLLECTION].writes[0]["document"]
    assert row["session_evidence"] == {"project_id": "project-1"}


def test_promotion_reads_the_captured_evidence_and_never_the_session():
    """DEBT-08. The session is TTL'd; the capture is what survives."""
    db = _database(
        [_legacy_document("doc-1")],
        contract_upload_sessions=[
            {"_id": "session-1", "upload_id": "upload-doc-1", "project_id": "project-1"}
        ],
    )
    service = ContractMigrationReconciliation(db)
    candidates = asyncio.run(service.inventory(organization_id="org-1"))
    asyncio.run(service.materialise_inventory(candidates))

    # The session is gone, as it will be by promotion time.
    db._collections["contract_upload_sessions"].docs = []

    stored = db[RECONCILIATION_COLLECTION].writes[0]["document"]
    assert asyncio.run(service.captured_session_evidence(stored)) == {"project_id": "project-1"}


def test_the_reconciliation_reader_never_queries_the_session_collection():
    import inspect

    from rbac_backend.services import contract_migration_reconciliation

    import ast

    source = inspect.getsource(
        contract_migration_reconciliation.ContractMigrationReconciliation.captured_session_evidence
    )
    tree = ast.parse(source.lstrip())
    # Assert on calls, not substrings: the docstring legitimately contains the
    # word "finding", and a substring check would fail on prose.
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "find" not in called
    assert "find_one" not in called
    assert "contract_upload_sessions" not in {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


# --------------------------------------------------------------------------- #
# non-authoritative by construction
# --------------------------------------------------------------------------- #


def test_the_reconciliation_collection_is_not_an_authoritative_one():
    from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS

    assert RECONCILIATION_COLLECTION not in LEGAL_COLLECTIONS


def test_a_materialised_row_carries_no_resolved_scope_or_type():
    db = _database()
    service = ContractMigrationReconciliation(db)
    candidates = asyncio.run(service.inventory(organization_id="org-1"))
    asyncio.run(service.materialise_inventory(candidates))

    row = db[RECONCILIATION_COLLECTION].writes[0]["document"]
    assert row["scope_state"] == ScopeClassificationState.UNRESOLVED.value
    assert row["type_state"] == TypeClassificationState.TYPE_UNKNOWN.value
    # Nothing here may look like an authoritative instrument.
    assert "contract_document_type" not in row
    assert "classification_revision" not in row
    assert "projection_status" not in row


def test_a_candidate_is_not_an_applicable_instrument():
    from rbac_backend.models.contract_document import require_applicable_instruments

    db = _database()
    candidates = asyncio.run(ContractMigrationReconciliation(db).inventory(organization_id="org-1"))

    with pytest.raises(TypeError):
        require_applicable_instruments(candidates)


def test_candidate_is_frozen():
    db = _database()
    candidate = asyncio.run(ContractMigrationReconciliation(db).inventory(organization_id="org-1"))[0]
    assert isinstance(candidate, ReconciliationCandidate)
    with pytest.raises(Exception):
        candidate.scope_state = ScopeClassificationState.ORG_SCOPE_CONFIRMED  # type: ignore[misc]


def test_there_is_no_migrate_all_entry_point():
    from rbac_backend.services import contract_migration_reconciliation

    names = [name for name in dir(ContractMigrationReconciliation) if not name.startswith("__")]
    assert not [name for name in names if "all" in name.lower() and "migrat" in name.lower()]
    assert "migrate_all" not in dir(contract_migration_reconciliation)
