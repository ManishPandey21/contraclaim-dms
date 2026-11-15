"""Claim `linked_letter_ids` — the second half of the Claim legacy migration.

The trace says this field is NOT the same thing as `linked_document_ids`:

  * it is rendered in the UI as a link to `/letters/{id}/input`, the Letter
    drafting workspace — not the Document viewer;
  * `Letter` is a correspondence-drafting record with no canonical Document id;
  * there is no letter->document resolver in this repository. The only possible
    join is the normalised letter number, which CLAUDE.md documents as NOT
    unique: a letter code is shared across documents and organisations;
  * `entity_document_links` requires a canonical `document_id`, and a Letter id
    is not one.

So the field carries mixed semantics and is classified per candidate, never
migrated wholesale. Only an id that genuinely resolves to a canonical Document
in the claim's own scope may become a relationship; a Letter register row is
reported and left alone. This is the §12.5 lesson restated: an auxiliary
register row must never self-authorise canonical Document content.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from rbac_backend.core.permissions import Permissions
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _PermissionPolicy,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/legacy-relationship-backfill/apply",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


class _OperatorPolicy(_PermissionPolicy):
    async def authorize(self, actor, permission: str, **kwargs) -> None:
        if permission not in getattr(actor, "permissions", set()):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _operator(*permissions: str) -> SimpleNamespace:
    granted = set(permissions) or {
        Permissions.DMS_ADMIN,
        Permissions.CLAIM_VIEW,
        Permissions.CLAIM_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="operator-1", organization_id="org-1", permissions=granted
    )


def _seed(letter_ids, *, letters=None, **document_overrides) -> _Database:
    db = _Database()
    db.claims.documents[0]["linked_letter_ids"] = list(letter_ids)
    db.letters = _Collection("letters", list(letters or []))
    if document_overrides:
        db.documents.documents[0].update(document_overrides)
    return db


def _active_links(db) -> list:
    return [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]


async def _inventory(db, monkeypatch, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "claim_letter",
        "org_id": "org-1",
        "project_id": "project-1",
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.inventory_legacy_relationship_backfill(**kwargs)


async def _apply(db, monkeypatch, **overrides):
    from rbac_backend.routers import legacy_relationship_backfill as backfill_router

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "claim_letter",
        "selections": [{"target_id": "claim-1", "document_id": "doc-1"}],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": db,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.apply_legacy_relationship_backfill(**kwargs)


@pytest.mark.asyncio
async def test_a_letter_id_resolving_to_a_canonical_document_can_be_migrated(
    monkeypatch,
) -> None:
    """Mixed legacy data: some ids really are canonical Document ids."""
    db = _seed(["doc-1"])

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "backfilled"
    assert outcome["relationship_role"] == "correspondence"

    links = _active_links(db)
    assert len(links) == 1
    assert links[0]["target_type"] == "claim"
    assert links[0]["document_id"] == "doc-1"
    assert links[0]["relationship_role"] == "correspondence"
    assert links[0]["source"] == "migration"
    assert (links[0].get("metadata") or {}).get("legacy_field") == "linked_letter_ids"

    # The legacy array is preserved.
    assert db.claims.documents[0]["linked_letter_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_a_letter_register_row_is_never_migrated(monkeypatch) -> None:
    """A Letter is a correspondence record, not canonical Document content, and
    this repository has no letter->document resolver."""
    db = _seed(
        ["letter-1"],
        letters=[
            {
                "_id": "letter-1",
                "letter_no": "LTR-001",
                "organization_id": "org-1",
                "project_id": "project-1",
            }
        ],
    )

    result = await _apply(
        db,
        monkeypatch,
        selections=[{"target_id": "claim-1", "document_id": "letter-1"}],
    )

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert "letter_register_record" in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_identifier_in_both_namespaces_is_ambiguous(monkeypatch) -> None:
    """The same token existing as a Document AND a Letter must never silently
    pick one namespace."""
    db = _seed(
        ["doc-1"],
        letters=[
            {
                "_id": "doc-1",
                "letter_no": "LTR-002",
                "organization_id": "org-1",
                "project_id": "project-1",
            }
        ],
    )

    result = await _apply(db, monkeypatch)

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert "ambiguous_identity" in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_an_unresolvable_identifier_is_reported_not_migrated(
    monkeypatch,
) -> None:
    db = _seed(["ghost-1"])

    result = await _apply(
        db,
        monkeypatch,
        selections=[{"target_id": "claim-1", "document_id": "ghost-1"}],
    )

    outcome = result["results"][0]
    assert outcome["status"] == "requires_manual_review"
    assert "missing_identity" in outcome["findings"]
    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_a_failing_letters_probe_never_looks_like_a_clean_document(
    monkeypatch,
) -> None:
    """Resolution uncertainty must fail closed. Swallowing a letters-probe error
    would make an id that exists in BOTH namespaces classify as a clean
    Document and auto-migrate — defeating the ambiguity guard under exactly the
    condition it exists for."""
    db = _seed(["doc-1"])

    class _ExplodingLetters:
        name = "letters"

        async def find_one(self, *_args, **_kwargs):
            raise RuntimeError("letters unavailable")

    db.letters = _ExplodingLetters()

    with pytest.raises(RuntimeError):
        await _apply(db, monkeypatch)

    assert _active_links(db) == []


@pytest.mark.asyncio
async def test_the_inventory_reports_every_candidate(monkeypatch) -> None:
    """§11.1: do not silently discard invalid rows; produce a reconciliation
    report."""
    db = _seed(
        ["doc-1", "letter-1", "ghost-1"],
        letters=[
            {
                "_id": "letter-1",
                "letter_no": "LTR-001",
                "organization_id": "org-1",
                "project_id": "project-1",
            }
        ],
    )

    report = await _inventory(db, monkeypatch)

    assert report["candidate_count"] == 3
    assert report["counts"]["valid"] == 1
    assert report["counts"]["letter_register_record"] == 1
    assert report["counts"]["missing_identity"] == 1
