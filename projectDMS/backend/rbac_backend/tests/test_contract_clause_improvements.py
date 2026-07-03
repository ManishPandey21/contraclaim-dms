"""Tests for the clause-agent hardening improvements:
#2 duplicate clause numbers, #3 raw/cleaned + char spans, #5 CONTRACT_UPDATE
in processing auth, #6 reprocessing preserves human edits, and #7 an
integration proof that upload -> contract_clauses -> Qdrant payload -> readable
by downstream (drafting/appraisal) works end to end.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.contract_clause import (
    ClauseChunkingAgent,
    ClauseIndexService,
    ClauseStorageService,
    DetectedClause,
    DocumentScope,
)
from rbac_backend.services.contract_clause.embedding_service import ClauseEmbeddingService


# --------------------------------------------------------------------------- #
# Async Mongo double (supports storage upsert + index find + embedding update)
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self):
        self.docs = {}
        self.inserted = []

    async def create_index(self, *a, **k):
        return None

    def find(self, filt):
        did = filt.get("document_id")
        return _Cursor([d for d in self.docs.values() if did is None or d.get("document_id") == did])

    async def find_one(self, filt):
        d = self.docs.get(filt.get("clause_uid"))
        return dict(d) if d else None

    async def update_one(self, filt, update, upsert=False):
        uid = filt["clause_uid"]
        if uid in self.docs:
            self.docs[uid].update(update.get("$set", {}))
        elif upsert:
            doc = {"clause_uid": uid, **update.get("$setOnInsert", {}), **update.get("$set", {})}
            self.docs[uid] = doc

    async def delete_one(self, filt):
        self.docs.pop(filt.get("clause_uid"), None)

    async def insert_one(self, doc):
        self.inserted.append(doc)

        class _R:
            inserted_id = "run"

        return _R()


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


class AllowPolicy:
    def __init__(self):
        self.calls = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append(permission)


class _User:
    id = "user-1"


SCOPE = DocumentScope(
    org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-1",
    document_title="Vol-2 GCC", document_type="GCC", volume="Vol-2",
)


# --------------------------------------------------------------------------- #
# #2 duplicate clause numbers
# --------------------------------------------------------------------------- #
def test_duplicate_clause_numbers_get_distinct_uids_and_ordinals():
    agent = ClauseChunkingAgent(FakeDB())
    clauses = [
        DetectedClause(clause_no="8.4", clause_title="EOT (Vol 1)", text="first 8.4"),
        DetectedClause(clause_no="8.4", clause_title="EOT (Vol 2)", text="second 8.4"),
        DetectedClause(clause_no="9.1", text="unique"),
    ]
    records, summary = agent.build_records(SCOPE, clauses)

    dup = [r for r in records if r.clause_no == "8.4"]
    assert len(dup) == 2
    assert dup[0].clause_uid != dup[1].clause_uid          # no collision
    assert [r.duplicate_ordinal for r in dup] == [1, 2]
    assert all(r.duplicate_status == "duplicate" for r in dup)
    assert all(r.duplicate_group_key == "8.4" for r in dup)
    unique = [r for r in records if r.clause_no == "9.1"][0]
    assert unique.duplicate_status == "unique" and unique.duplicate_ordinal == 1
    assert summary.duplicates_detected == 1


def test_clause_uid_first_occurrence_is_backward_compatible():
    # ordinal 1 keeps the legacy uid (no #dup suffix) so existing records match.
    legacy = ClauseStorageService.clause_uid("o", "p", "c", "d", "8.4", 1, 0)
    with_ord1 = ClauseStorageService.clause_uid("o", "p", "c", "d", "8.4", 1, 0, duplicate_ordinal=1)
    with_ord2 = ClauseStorageService.clause_uid("o", "p", "c", "d", "8.4", 1, 0, duplicate_ordinal=2)
    assert legacy == with_ord1
    assert with_ord2 != legacy


# --------------------------------------------------------------------------- #
# #3 raw text preserved in `text`, cleaned in `cleaned_text`, char spans
# --------------------------------------------------------------------------- #
def test_raw_and_cleaned_text_are_distinct_with_char_spans():
    agent = ClauseChunkingAgent(FakeDB())
    clause = DetectedClause(
        clause_no="8.4", clause_title="EOT",
        text="8.4  Extension  of Time\n\nHDR", cleaned_text="Extension of Time",
        char_start=120, char_end=180,
    )
    records, _ = agent.build_records(SCOPE, [clause])
    rec = records[0]
    assert rec.text == "8.4  Extension  of Time\n\nHDR"   # exact raw preserved
    assert rec.cleaned_text == "Extension of Time"        # cleaned used for embedding
    assert rec.char_start == 120 and rec.char_end == 180
    assert rec.checksum == ClauseStorageService.checksum("Extension of Time")  # checksum on cleaned


def test_split_clause_preserves_raw_slice_per_part():
    # Three aligned paragraphs; a small max forces one part per paragraph.
    agent = ClauseChunkingAgent(FakeDB(), max_clause_chars=45)
    cleaned = "\n\n".join(f"clean paragraph number {i} aaaaaaaaaa" for i in range(3))
    raw = "\n\n".join(f"RAW paragraph number {i} bbbbbbbbbbbb" for i in range(3))
    clause = DetectedClause(clause_no="8.4", text=raw, cleaned_text=cleaned)

    records, _ = agent.build_records(SCOPE, [clause])
    parts = [r for r in records if r.clause_no == "8.4"]
    assert len(parts) == 3 and all(r.chunk_type == "clause_part" for r in parts)
    # Each part now carries its exact RAW slice in `text` and cleaned in cleaned_text.
    for i, rec in enumerate(parts):
        assert rec.text == f"RAW paragraph number {i} bbbbbbbbbbbb"
        assert rec.cleaned_text == f"clean paragraph number {i} aaaaaaaaaa"


# --------------------------------------------------------------------------- #
# #5 processing authorization includes CONTRACT_UPDATE
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_processing_authorization_requires_contract_update():
    policy = AllowPolicy()
    storage = ClauseStorageService(FakeDB(), policy_service=policy)
    await storage.authorize_processing_run(
        _User(), org_id="o", project_id="p", contract_id="c", document_id="d"
    )
    assert Permissions.CONTRACT_UPDATE in policy.calls
    assert Permissions.CONTRACT_READ in policy.calls
    assert Permissions.CONTRACT_CLAUSE_CREATE in policy.calls
    assert Permissions.AI_CONTRACT_PROCESSING_RUN in policy.calls


# --------------------------------------------------------------------------- #
# #6 reprocessing preserves human edits unless force_reset
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reprocess_preserves_human_edits():
    db = FakeDB()
    storage = ClauseStorageService(db)
    rec = storage.build_record(**{
        "org_id": "o", "project_id": "p", "contract_id": "c", "document_id": "d",
        "clause_no": "8.4", "clause_title": "AI title", "text": "body", "document_type": "GCC",
    })
    await storage.save_clause(rec)

    # A reviewer edits the title + verifies (as the index service would).
    doc = db[storage.COLLECTION].docs[rec.clause_uid]
    doc.update(manually_edited=True, clause_title="Human title", quality_status="validated", verified_by="user-9")

    # Reprocess with the same (AI) record: human edits are kept.
    outcome = await storage.save_clause(rec)
    stored = db[storage.COLLECTION].docs[rec.clause_uid]
    assert outcome == "preserved"
    assert stored["clause_title"] == "Human title"
    assert stored["verified_by"] == "user-9"

    # force_reset overwrites with the AI values.
    outcome2 = await storage.save_clause(rec, force_reset=True)
    stored2 = db[storage.COLLECTION].docs[rec.clause_uid]
    assert stored2["clause_title"] == "AI title"


# --------------------------------------------------------------------------- #
# #7 integration proof: process -> contract_clauses -> Qdrant payload -> readable
# --------------------------------------------------------------------------- #
class FakeEmbedClient:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVectorClient:
    def __init__(self):
        self.upserts = []

    async def upsert(self, vectors, chunks, namespace=None):
        self.upserts.append({"vectors": vectors, "chunks": chunks, "namespace": namespace})


@pytest.mark.asyncio
async def test_end_to_end_clauses_embedded_and_readable():
    db = FakeDB()
    vector = FakeVectorClient()
    embedding = ClauseEmbeddingService(db=db, embedding_client=FakeEmbedClient(), vector_client=vector)
    agent = ClauseChunkingAgent(db, policy_service=AllowPolicy(), embedding_service=embedding)

    clauses = [
        DetectedClause(clause_no="8.4", clause_title="EOT", text="raw 8.4", cleaned_text="Extension of Time", page_start=52),
        DetectedClause(clause_no="9.1", clause_title="Taking Over", text="raw 9.1", cleaned_text="Taking Over Certificate", page_start=53),
    ]

    summary = await agent.process(_User(), SCOPE, clauses, [], total_pages=60)

    # contract_clauses populated.
    assert summary.records_written["inserted"] == 2
    # Embedded to Qdrant namespace with cleaned text + req-17 payload.
    assert len(vector.upserts) == 1
    up = vector.upserts[0]
    assert up["namespace"] == "contract_clauses"
    assert [c["text"] for c in up["chunks"]] == ["Extension of Time", "Taking Over Certificate"]
    payload = up["chunks"][0]["metadata"]
    for field in ("org_id", "project_id", "contract_id", "document_id", "clause_no",
                  "clause_title", "page_start", "page_end", "document_type", "is_current"):
        assert field in payload
    assert payload["org_id"] == "org-A" and payload["clause_no"] == "8.4"

    # Downstream (drafting/appraisal) can read the records via the index service.
    index = ClauseIndexService(db, policy_service=AllowPolicy())
    rows = await index.list_clauses(_User(), document_id="doc-1", org_id="org-A", project_id="proj-A")
    assert {r["clause_no"] for r in rows} == {"8.4", "9.1"}
    assert all(r["is_authorised_for_ai"] for r in rows)
