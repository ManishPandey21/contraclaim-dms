"""Filing-bundle exhibit bytes come only from an authorised, server-resolved source.

`ArbitrationCaseWorkspaceService._load_exhibit_file` used to open
`row["source_file_link"]` - a client-writable matrix field - as a raw filesystem
path, and looked the source record up by id with no tenant or case scope. These
tests drive the real service over an in-memory Mongo boundary. Every file is a
canary under pytest's `tmp_path`; the storage roots are redirected there, so no
real server file is ever read.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.core.config import settings
from rbac_backend.models.arbitration_drafting import ArbitrationMatrixRowCreate
from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from rbac_backend.services.s3_service import S3Service
from rbac_backend.tests.test_hindrance_register_http import _Collection, _Database, _matches, _principal


class _WorkspaceCollection(_Collection):
    """`_Collection` plus the `update_many` that the case-touch path invalidates approvals with."""

    async def update_many(self, query: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any):
        matched = 0
        for row in self.docs:
            if _matches(row, query):
                self._apply(row, update, inserting=False)
                matched += 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class _WorkspaceDatabase(_Database):
    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_") or name == "client":
            raise AttributeError(name)
        return self._collections.setdefault(name, _WorkspaceCollection(name))

ORG_A, ORG_B = "org-A", "org-B"
A1, B1 = "proj-A1", "proj-B1"
CASE_ID = "case-a1"

CANARY = b"CANARY-NOT-A-SECRET"
A1_BYTES = b"A1-EXHIBIT-BYTES"
B1_BYTES = b"B1-FOREIGN-BYTES"
S3_BYTES = b"A1-S3-EXHIBIT-BYTES"

DOC_A1 = ObjectId()
DOC_A1_S3 = ObjectId()
DOC_A1_OUTSIDE = ObjectId()
DOC_A1_TRAVERSAL = ObjectId()
DOC_A1_SYMLINK = ObjectId()
DOC_B1 = ObjectId()
DOC_B1_STRING_ID = "doc-b1-string-id"

EDITOR = _principal("u-editor-a1", ["projectadmin"], ORG_A, [A1])


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    uploads = tmp_path / "uploads"
    secure = tmp_path / "secure"
    temp_root = tmp_path / "tmp"
    outside = tmp_path / "outside"
    for directory in (uploads, secure, temp_root, outside):
        directory.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", str(uploads), raising=False)
    monkeypatch.setattr(settings, "SECURE_UPLOADS_DIR", str(secure), raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))

    (uploads / "a1.pdf").write_bytes(A1_BYTES)
    (uploads / "b1.pdf").write_bytes(B1_BYTES)
    (uploads / "other-tenant.pdf").write_bytes(CANARY)
    (outside / "canary.txt").write_bytes(CANARY)
    return {"uploads": uploads, "outside": outside, "tmp": tmp_path}


def _document(document_id: Any, org: str, project: str, **fields: Any) -> dict[str, Any]:
    return {
        "_id": document_id,
        "organization_id": org,
        "project_id": project,
        "filename": f"{document_id}.pdf",
        **fields,
    }


@pytest.fixture
def db(storage: dict[str, Path]) -> _Database:
    database = _WorkspaceDatabase()
    database.arbitration_cases.docs.append(
        {"_id": CASE_ID, "organization_id": ORG_A, "project_id": A1, "contract_id": "primary", "title": "Case A1"}
    )
    uploads, outside = storage["uploads"], storage["outside"]
    database.documents.docs.extend(
        [
            _document(DOC_A1, ORG_A, A1, filepath_local=str(uploads / "a1.pdf")),
            _document(DOC_A1_S3, ORG_A, A1, filepath_s3="tenants/org-A/a1-s3.pdf"),
            _document(DOC_A1_OUTSIDE, ORG_A, A1, filepath_local=str(outside / "canary.txt")),
            _document(DOC_A1_TRAVERSAL, ORG_A, A1, filepath_local=str(uploads / ".." / "outside" / "canary.txt")),
            _document(DOC_B1, ORG_B, B1, filepath_local=str(uploads / "b1.pdf")),
            _document(DOC_B1_STRING_ID, ORG_B, B1, filepath_local=str(uploads / "b1.pdf")),
        ]
    )
    return database


@pytest.fixture(autouse=True)
def _s3(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    async def _download_bytes(self, key: str) -> bytes:
        fetched.append(key)
        return S3_BYTES

    monkeypatch.setattr(S3Service, "download_bytes", _download_bytes)
    return fetched


async def _exhibit(db: _Database, **fields: Any) -> dict[str, Any]:
    service = ArbitrationCaseWorkspaceService(db)
    payload = ArbitrationMatrixRowCreate(title="Exhibit", document_type="letter", **fields)
    return await service.create_matrix_row(CASE_ID, "document-index", payload, EDITOR)


async def _collect(db: _Database, row: dict[str, Any]) -> dict[str, Any]:
    entries = await ArbitrationCaseWorkspaceService(db)._collect_exhibit_files([row])
    assert len(entries) == 1
    return entries[0]


# ---------------------------------------------------------------------------
# A client-supplied path is never opened
# ---------------------------------------------------------------------------


async def test_a_client_supplied_absolute_path_is_never_read(db: _Database, storage: dict[str, Path]) -> None:
    row = await _exhibit(db, source_file_link=str(storage["outside"] / "canary.txt"))
    entry = await _collect(db, row)
    assert entry["content"] is None
    assert entry["error"]


async def test_a_client_supplied_path_inside_a_storage_root_is_never_read(db: _Database, storage: dict[str, Path]) -> None:
    row = await _exhibit(db, source_file_link=str(storage["uploads"] / "other-tenant.pdf"))
    entry = await _collect(db, row)
    assert entry["content"] is None


async def test_a_client_supplied_traversal_path_is_never_read(db: _Database, storage: dict[str, Path]) -> None:
    row = await _exhibit(db, source_file_link=str(storage["uploads"] / ".." / "outside" / "canary.txt"))
    entry = await _collect(db, row)
    assert entry["content"] is None


async def test_the_citation_audit_is_not_a_file_existence_oracle(db: _Database, storage: dict[str, Path]) -> None:
    await _exhibit(db, source_file_link=str(storage["outside"] / "canary.txt"))
    await _exhibit(db, source_file_link=str(storage["outside"] / "absent.txt"))
    audit = await ArbitrationCaseWorkspaceService(db).citation_audit(CASE_ID)
    unresolved = [issue for issue in audit["issues"] if issue.get("issue_type") == "exhibit_file_missing"]
    # Both rows are unresolvable, for the same stated reason: whether the
    # client-named path exists on the server is not observable.
    assert len(unresolved) == 2, audit["issues"]
    reasons = {issue["message"].split("(", 1)[1].split(")", 1)[0] for issue in unresolved}
    assert len(reasons) == 1, reasons


# ---------------------------------------------------------------------------
# The source record must belong to the case's tenant
# ---------------------------------------------------------------------------


async def test_a_foreign_tenant_document_is_not_exported(db: _Database) -> None:
    row = await _exhibit(db, source_id=str(DOC_B1))
    entry = await _collect(db, row)
    assert entry["content"] is None


async def test_a_foreign_tenant_document_with_a_string_id_is_not_exported(db: _Database) -> None:
    row = await _exhibit(db, source_id=DOC_B1_STRING_ID)
    entry = await _collect(db, row)
    assert entry["content"] is None


# ---------------------------------------------------------------------------
# A server-recorded path is confined to the storage roots
# ---------------------------------------------------------------------------


async def test_a_recorded_path_outside_the_storage_roots_is_refused(db: _Database) -> None:
    row = await _exhibit(db, source_id=str(DOC_A1_OUTSIDE))
    entry = await _collect(db, row)
    assert entry["content"] is None
    assert entry["error"]


async def test_a_recorded_traversal_path_is_refused(db: _Database) -> None:
    row = await _exhibit(db, source_id=str(DOC_A1_TRAVERSAL))
    entry = await _collect(db, row)
    assert entry["content"] is None


async def test_a_recorded_symlink_escaping_the_storage_roots_is_refused(db: _Database, storage: dict[str, Path]) -> None:
    link = storage["uploads"] / "escape.pdf"
    try:
        os.symlink(storage["outside"] / "canary.txt", link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are not available here: {exc}")
    db.documents.docs.append(_document(DOC_A1_SYMLINK, ORG_A, A1, filepath_local=str(link)))
    row = await _exhibit(db, source_id=str(DOC_A1_SYMLINK))
    entry = await _collect(db, row)
    assert entry["content"] is None


# ---------------------------------------------------------------------------
# Legitimate exhibits still export
# ---------------------------------------------------------------------------


async def test_a_case_scoped_document_exports_from_its_recorded_local_file(db: _Database) -> None:
    row = await _exhibit(db, source_id=str(DOC_A1))
    entry = await _collect(db, row)
    assert entry["content"] == A1_BYTES
    assert entry["error"] is None


async def test_a_case_scoped_document_exports_from_its_storage_key(db: _Database, _s3: list[str]) -> None:
    row = await _exhibit(db, source_id=str(DOC_A1_S3))
    entry = await _collect(db, row)
    assert entry["content"] == S3_BYTES
    assert _s3 == ["tenants/org-A/a1-s3.pdf"]


async def test_a_case_scoped_document_exports_even_if_its_display_link_is_a_path(
    db: _Database, storage: dict[str, Path]
) -> None:
    # Agents copy a display link onto the row; bytes still come from the record.
    row = await _exhibit(db, source_id=str(DOC_A1), source_file_link=str(storage["outside"] / "canary.txt"))
    entry = await _collect(db, row)
    assert entry["content"] == A1_BYTES


# ---------------------------------------------------------------------------
# The exact record whose bytes are exported must still be authoritative
# ---------------------------------------------------------------------------


def _blocked_document(db: _Database, storage: dict[str, Path], **verdict: Any) -> ObjectId:
    document_id = ObjectId()
    db.documents.docs.append(
        _document(document_id, ORG_A, A1, filepath_local=str(storage["uploads"] / "a1.pdf"), **verdict)
    )
    return document_id


@pytest.mark.parametrize(
    "verdict",
    [
        pytest.param({"processing_status": "human_review_required"}, id="quality-gate-review"),
        pytest.param({"duplicate_status": "duplicate"}, id="confirmed-duplicate"),
        pytest.param({"lifecycle_state": "deleted"}, id="deleted"),
    ],
)
async def test_a_document_the_quality_gate_blocked_is_not_exported(db: _Database, storage: dict[str, Path], verdict: dict) -> None:
    row = await _exhibit(db, source_id=str(_blocked_document(db, storage, **verdict)))
    entry = await _collect(db, row)
    assert entry["content"] is None
    assert entry["error"]


async def test_a_row_cannot_dodge_the_authority_check_by_its_source_type(db: _Database, storage: dict[str, Path]) -> None:
    # `source_type` is client-writable; authority is decided on the record actually read.
    blocked = _blocked_document(db, storage, processing_status="human_review_required")
    row = await _exhibit(db, source_id=str(blocked), source_type="manual_fact")
    entry = await _collect(db, row)
    assert entry["content"] is None


async def test_a_deleted_letter_is_not_exported(db: _Database, storage: dict[str, Path]) -> None:
    letter_id = ObjectId()
    db.letters.docs.append(
        {"_id": letter_id, "organization_id": ORG_A, "project_id": A1, "lifecycle_state": "deleted", "filepath_local": str(storage["uploads"] / "a1.pdf")}
    )
    row = await _exhibit(db, source_id=str(letter_id))
    entry = await _collect(db, row)
    assert entry["content"] is None


# ---------------------------------------------------------------------------
# Authorised legacy id representations still resolve; scope is not broadened
# ---------------------------------------------------------------------------

LEGACY_ORG, LEGACY_PROJECT = ObjectId(), ObjectId()
LEGACY_CASE = "case-legacy"


async def test_a_document_whose_scope_is_stored_as_object_ids_is_exported(db: _Database, storage: dict[str, Path]) -> None:
    db.arbitration_cases.docs.append(
        {"_id": LEGACY_CASE, "organization_id": str(LEGACY_ORG), "project_id": str(LEGACY_PROJECT), "title": "Legacy"}
    )
    document_id = ObjectId()
    db.documents.docs.append(
        {"_id": document_id, "organization_id": LEGACY_ORG, "project_id": LEGACY_PROJECT, "filename": "l.pdf", "filepath_local": str(storage["uploads"] / "a1.pdf")}
    )
    row = {"_id": "row-legacy", "case_id": LEGACY_CASE, "source_id": str(document_id), "exhibit_id": "C-9"}
    entry = await _collect(db, row)
    assert entry["content"] == A1_BYTES


async def test_the_readiness_evidence_manifest_resolves_the_same_legacy_record(db: _Database, storage: dict[str, Path]) -> None:
    case = {"_id": LEGACY_CASE, "organization_id": str(LEGACY_ORG), "project_id": str(LEGACY_PROJECT)}
    document_id = ObjectId()
    db.documents.docs.append(
        {"_id": document_id, "organization_id": LEGACY_ORG, "project_id": LEGACY_PROJECT, "filename": "l.pdf"}
    )
    row = {"_id": "row-legacy", "case_id": LEGACY_CASE, "source_id": str(document_id), "approval_status": "approved", "verification_status": "verified"}
    manifest = await ArbitrationCaseWorkspaceService(db)._authoritative_evidence_manifest(case, {"document-index": [row]})
    assert [item["resolution_status"] for item in manifest] == ["resolved"]


async def test_an_organisation_level_document_is_not_an_exhibit_of_a_project_case(db: _Database, storage: dict[str, Path]) -> None:
    # Arbitration evidence is bound to the case's project (the readiness
    # manifest applies the same rule); widening it is out of scope.
    document_id = ObjectId()
    db.documents.docs.append(_document(document_id, ORG_A, None, filepath_local=str(storage["uploads"] / "a1.pdf")))
    row = await _exhibit(db, source_id=str(document_id))
    entry = await _collect(db, row)
    assert entry["content"] is None
