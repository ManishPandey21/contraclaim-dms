from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rbac_backend.models.letter_drafting import DraftRun, SourceEvidence
from rbac_backend.services.letter_drafting.repository import DraftRunRepository


class _Collection:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def find_one(self, query, *args, **kwargs):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None

    async def update_one(self, query, update, *args, **kwargs):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class _DB:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, key):
        return self.collections.setdefault(key, _Collection())


def _run(**overrides):
    data = {
        "run_id": "run-1",
        "letter_id": "letter-1",
        "mode": "draft",
        "status": "completed",
        "role": "contractor",
        "inputs": {"subject": "Claim response", "points": "Reserve all rights"},
        "sources": [
            SourceEvidence(
                source_id="doc-1",
                source_type="context_document",
                allowed_use="fact",
                label="Incoming letter",
                text="Sensitive source text must not be copied into the evidence snapshot.",
                source_hash="sha256:source",
                document_id="doc-1",
            )
        ],
    }
    data.update(overrides)
    return DraftRun(**data)


@pytest.mark.asyncio
async def test_immutable_snapshot_keeps_evidence_ledger_text_free():
    db = _DB()
    repo = DraftRunRepository(db)

    fields = await repo.create_immutable_snapshots(_run())

    assert fields["input_snapshot_hash"]
    evidence = db["letter_draft_evidence_snapshots"].docs[0]
    assert evidence["sources"][0]["source_id"] == "doc-1"
    assert "text" not in evidence["sources"][0]
    assert "Sensitive source text" not in str(evidence)


@pytest.mark.asyncio
async def test_non_terminal_update_does_not_overwrite_completed_at():
    db = _DB()
    repo = DraftRunRepository(db)
    completed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stored = _run(completed_at=completed_at).model_dump(mode="python")
    db["letter_draft_runs"].docs.append(stored)

    updated = await repo.update_fields("letter-1", "run-1", {"warnings": ["late audit annotation"]})

    assert updated is not None
    assert updated.completed_at == completed_at
    assert updated.updated_at is not None
