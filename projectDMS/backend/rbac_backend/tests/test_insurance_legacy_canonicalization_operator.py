"""Operator seam for legacy Insurance canonicalization (HIGH 1).

The canonicalization service already refuses ineligible classifications; what
these tests pin is that an *authorised operator* has a runnable, dry-run-capable,
tenant-scoped way to reach it, and that the seam cannot be turned into a bulk
migration engine.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _PermissionPolicy,
)
from rbac_backend.tests.test_insurance_document_link_migration import (
    _MigrationDocumentController,
    _UniqueCollection,
)


LEGACY_BYTES = b"legacy-policy-bytes"


class _OperatorPolicy(_PermissionPolicy):
    """Enforces the operator gate the same way PolicyService.authorize does."""

    async def authorize(self, actor, permission: str, **kwargs) -> None:
        if permission not in getattr(actor, "permissions", set()):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/insurance/legacy-evidence/canonicalize",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _operator(*permissions: str, user_id: str = "operator-1") -> SimpleNamespace:
    """Default operator carries the explicit admin permission plus what the
    canonicalization service itself needs."""
    granted = set(permissions) or {
        Permissions.DMS_ADMIN,
        Permissions.INSURANCE_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id=user_id,
        organization_id="org-1",
        permissions=granted,
    )


def _seed(
    tmp_path: Path,
    *,
    token: str = "policy-a.pdf",
    organization_id: str = "org-1",
    project_id: str = "project-1",
) -> tuple[_Database, Path]:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir(exist_ok=True)
    (legacy_root / token).write_bytes(LEGACY_BYTES)
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": organization_id,
                "project_id": project_id,
                "policy_number": "POL-1",
                "date_of_issue": "2026-08-01T00:00:00Z",
                "document_id": token,
            }
        ],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )
    return db, legacy_root


async def _canonicalize(db, legacy_root, monkeypatch, **overrides):
    """Drive the real operator route function."""
    from rbac_backend.routers import insurance as insurance_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(insurance_router, "require_step_up", _step_up)
    monkeypatch.setattr(
        insurance_router, "_legacy_insurance_root", lambda: legacy_root
    )

    kwargs = {
        "request": _request(),
        "insurance_ids": ["insurance-1"],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
        "controller": _MigrationDocumentController(db),
    }
    kwargs.update(overrides)
    return await insurance_router.canonicalize_legacy_insurance_evidence(**kwargs)


@pytest.mark.asyncio
async def test_operator_can_canonicalize_an_eligible_legacy_insurance_file(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    result = await _canonicalize(db, legacy_root, monkeypatch)

    assert result["dry_run"] is False
    assert len(result["results"]) == 1
    outcome = result["results"][0]
    assert outcome["insurance_id"] == "insurance-1"
    assert outcome["status"] == "canonicalized"
    assert outcome["document_id"] == "canonical-insurance-doc-1"

    # Final physical state, not helper calls.
    link = db.entity_document_links.documents[0]
    assert link["target_type"] == "insurance"
    assert link["target_id"] == "insurance-1"
    assert link["source"] == "migration"
    assert link["removed_at"] is None
    # Legacy source bytes survive canonicalization.
    assert (legacy_root / "policy-a.pdf").read_bytes() == LEGACY_BYTES


@pytest.mark.asyncio
async def test_actor_without_the_admin_permission_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        await _canonicalize(
            db,
            legacy_root,
            monkeypatch,
            current_user=_operator(
                Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW
            ),
        )

    assert excinfo.value.status_code == 403
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_step_up_is_required_even_for_an_admin(tmp_path: Path, monkeypatch) -> None:
    from rbac_backend.routers import insurance as insurance_router

    db, legacy_root = _seed(tmp_path)
    monkeypatch.setattr(
        insurance_router, "_legacy_insurance_root", lambda: legacy_root
    )

    # require_step_up is NOT stubbed here: the real one runs and finds no token.
    with pytest.raises(HTTPException) as excinfo:
        await insurance_router.canonicalize_legacy_insurance_evidence(
            request=_request(),
            insurance_ids=["insurance-1"],
            org_id="org-1",
            project_id="project-1",
            dry_run=False,
            db=db,
            current_user=_operator(),
            policy=_OperatorPolicy(),
            controller=_MigrationDocumentController(db),
        )

    assert excinfo.value.status_code == 403
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_an_unscoped_run_is_refused(tmp_path: Path, monkeypatch) -> None:
    db, legacy_root = _seed(tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        await _canonicalize(db, legacy_root, monkeypatch, org_id=None, project_id=None)

    assert excinfo.value.status_code == 400
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_explicit_candidate_selection_is_required(
    tmp_path: Path, monkeypatch
) -> None:
    """There is no 'migrate everything' mode."""
    db, legacy_root = _seed(tmp_path)

    for empty in ([], [""], ["   "]):
        with pytest.raises(HTTPException) as excinfo:
            await _canonicalize(db, legacy_root, monkeypatch, insurance_ids=empty)
        assert excinfo.value.status_code == 400

    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_a_batch_larger_than_the_bound_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        await _canonicalize(
            db,
            legacy_root,
            monkeypatch,
            insurance_ids=[f"insurance-{n}" for n in range(51)],
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_dry_run_performs_zero_mutation(tmp_path: Path, monkeypatch) -> None:
    db, legacy_root = _seed(tmp_path)
    documents_before = deepcopy(db.documents.documents)
    versions_before = deepcopy(db.document_versions.documents)
    file_objects_before = deepcopy(db.file_objects.documents)

    result = await _canonicalize(db, legacy_root, monkeypatch, dry_run=True)

    assert result["dry_run"] is True
    assert result["results"][0]["status"] == "eligible"
    # Nothing new anywhere, and nothing existing altered.
    assert db.entity_document_links.documents == []
    assert db.documents.documents == documents_before
    assert db.document_versions.documents == versions_before
    assert db.file_objects.documents == file_objects_before
    assert db.insurance_document_migration_claims.documents == []
    assert (legacy_root / "policy-a.pdf").read_bytes() == LEGACY_BYTES


@pytest.mark.asyncio
async def test_a_missing_legacy_source_is_refused(tmp_path: Path, monkeypatch) -> None:
    db, legacy_root = _seed(tmp_path)
    (legacy_root / "policy-a.pdf").unlink()

    result = await _canonicalize(db, legacy_root, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert outcome["classification"] == "missing_file"
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_a_hash_mismatch_is_refused(tmp_path: Path, monkeypatch) -> None:
    db, legacy_root = _seed(tmp_path)
    db.insurance_policies.documents[0]["document_sha256"] = "b" * 64

    result = await _canonicalize(db, legacy_root, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert outcome["classification"] == "hash_mismatch"
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_a_policy_outside_the_requested_organisation_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path, organization_id="org-2")

    result = await _canonicalize(db, legacy_root, monkeypatch)

    assert result["results"][0]["status"] == "out_of_scope"
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_a_policy_outside_the_requested_project_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path, project_id="project-2")

    result = await _canonicalize(db, legacy_root, monkeypatch)

    assert result["results"][0]["status"] == "out_of_scope"
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_an_unknown_policy_is_reported_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    result = await _canonicalize(
        db, legacy_root, monkeypatch, insurance_ids=["insurance-missing"]
    )

    assert result["results"][0]["status"] == "not_found"
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_the_run_is_audited_with_operator_candidate_and_result(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    result = await _canonicalize(db, legacy_root, monkeypatch)

    events = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "insurance.legacy_canonicalization"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["actor_id"] == "operator-1"
    assert event["resource_id"] == "insurance-1"
    assert event["organization_id"] == "org-1"
    assert event["after"]["status"] == "canonicalized"
    assert event["after"]["dry_run"] is False
    assert event["after"]["migration_run_id"] == result["migration_run_id"]


@pytest.mark.asyncio
async def test_rerunning_the_operator_seam_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    db, legacy_root = _seed(tmp_path)

    first = await _canonicalize(db, legacy_root, monkeypatch)
    second = await _canonicalize(db, legacy_root, monkeypatch)

    assert first["results"][0]["status"] == "canonicalized"
    # The second run reuses canonical identity rather than minting a duplicate.
    assert second["results"][0].get("document_id") in {
        first["results"][0]["document_id"],
        None,
    }
    active_links = [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]
    assert len(active_links) == 1
    assert (legacy_root / "policy-a.pdf").read_bytes() == LEGACY_BYTES


def test_the_canonicalization_module_never_deletes_legacy_bytes() -> None:
    """Structural gate: no filesystem removal primitive may enter this module."""
    source = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "insurance_document_link_migration.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "os.remove",
        "os.unlink",
        ".unlink(",
        "shutil.rmtree",
        "shutil.move",
        ".rename(",
        "os.rmdir",
    ):
        assert forbidden not in source, f"{forbidden} must never appear here"
