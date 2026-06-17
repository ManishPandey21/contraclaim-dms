"""Evidence bundle export (Phase 4 / Module 4): zip contents + in-scope filter."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from rbac_backend.services.evidence_bundle_service import (
    EvidenceBundleService,
    build_evidence_zip,
)


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.namelist()


def _read(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(name)


# --- pure builder ---------------------------------------------------------


def test_build_zip_has_core_members_and_manifest():
    claim = {"_id": "c1", "claim_ref": "EOT/001", "title": "Monsoon delay", "organization_id": "org-A"}
    content = build_evidence_zip(
        claim=claim,
        audit_csv="created_at,action\n2026-01-01,claim.created\n",
        documents=[{"document_id": "d1", "name": "letter.pdf", "status": "included", "data": b"PDFDATA"}],
        generated_by="u1",
        audit_event_count=1,
    )
    names = _names(content)
    assert "claim.json" in names
    assert "audit-trail.csv" in names
    assert "manifest.json" in names
    assert "documents/letter.pdf" in names

    manifest = json.loads(_read(content, "manifest.json"))
    assert manifest["claim_id"] == "c1"
    assert manifest["audit_event_count"] == 1
    assert manifest["document_count"] == 1
    assert manifest["documents"][0]["archived_as"] == "documents/letter.pdf"


def test_build_zip_dedupes_document_names():
    content = build_evidence_zip(
        claim={"_id": "c1"},
        audit_csv="",
        documents=[
            {"document_id": "d1", "name": "doc.pdf", "data": b"a"},
            {"document_id": "d2", "name": "doc.pdf", "data": b"b"},
        ],
    )
    docs = [n for n in _names(content) if n.startswith("documents/")]
    assert len(docs) == 2 and len(set(docs)) == 2


# --- service: in-scope document filter ------------------------------------


class _Documents:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query):
        return self.docs.get(query.get("_id"))


class _DB:
    def __init__(self, docs):
        self.documents = _Documents(docs)


class _FileService:
    """Returns a temp-file-like object via a tiny shim path."""

    def __init__(self, blobs):
        self.blobs = blobs

    async def materialize_to_temp(self, file_object_id, suffix=None):
        import tempfile
        from pathlib import Path

        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(self.blobs[file_object_id])
        handle.close()
        return Path(handle.name)


@pytest.mark.asyncio
async def test_gather_excludes_out_of_scope_and_missing_documents():
    docs = {
        "in": {"_id": "in", "organization_id": "org-A", "filename": "ours.pdf", "file_object_id": "f1"},
        "foreign": {"_id": "foreign", "organization_id": "org-B", "filename": "theirs.pdf", "file_object_id": "f2"},
    }
    claim = {
        "_id": "c1",
        "organization_id": "org-A",
        "linked_document_ids": ["in", "foreign", "ghost"],
    }
    svc = EvidenceBundleService(_DB(docs), file_object_service=_FileService({"f1": b"OURS", "f2": b"THEIRS"}))
    content = await svc.build(claim, [{"action": "claim.created", "resource_id": "c1"}], generated_by="u1")

    archived = [n for n in _names(content) if n.startswith("documents/")]
    assert archived == ["documents/ours.pdf"]  # foreign + ghost excluded

    manifest = json.loads(_read(content, "manifest.json"))
    statuses = {d["document_id"]: d["status"] for d in manifest["documents"]}
    assert statuses == {"in": "included", "foreign": "out_of_scope", "ghost": "not_found"}
