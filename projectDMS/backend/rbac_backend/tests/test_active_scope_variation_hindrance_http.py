"""CL-3A: the selected navbar project bounds Variation as it bounds Hindrance.

``EffectiveScope = membership/role scope ∩ explicit selected project``. PR #22
built the selection boundary (`core/tenant_context.py`) for the Hindrance &
Constraint Register; CL-3A makes it shared relationship infrastructure and
closes the CL-2 debt for Variation:

* member of A1 + A2, selected A1, Variation A1        -> allowed
* member of A1 + A2, selected A2, Variation A1        -> 403 ``context_forbidden``
* not a member of A1, Variation A1                    -> refused
* no project selected: record-level / write           -> 400 ``selection_required``
* no project selected: list                           -> bounded by membership
* superadmin: an explicit selection still constrains; record-level needs one.

It also pins the shared relationship routes (``/api/entities/{type}/{id}/...``,
``/api/document-links/{id}``, ``/api/documents/{id}/entity-links`` and
``/api/documents/{id}/link-targets``) for the scoped target types - since CL-4A
Claim is one of them - and that a target type whose adapter does not set
``active_scope_enforced`` (a synthetic one: every registered type now opts in)
keeps its selection-blind behaviour.

The harness is the Hindrance HTTP suite's in-memory Mongo boundary with the real
``PolicyService`` / ``ScopeService`` / ``DocumentRelationshipService``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_db
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.document_relationships import (
    get_document_relationship_service,
)
from rbac_backend.routers.document_relationships import router as relationship_router
from rbac_backend.routers.evidence_registers import router as registers_router
from rbac_backend.routers.hindrances import router as hindrances_router
from rbac_backend.routers.variations import router as variations_router
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.entity_adapter_registry import (
    ClaimEntityAdapter,
    EntityAdapterRegistry,
)
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.test_hindrance_register_http import (
    _Database,
    _document,
    _principal,
)

VARIATION_ALL = {
    "dms.variation.view",
    "dms.variation.create",
    "dms.variation.edit",
    "dms.variation.delete",
    "dms.variation.approve",
}
HINDRANCE_ALL = {"dms.hindrance.view", "dms.hindrance.create", "dms.hindrance.edit", "dms.hindrance.archive"}
BASE = {"dms.document.view", "dms.claim.view", "dms.claim.edit", "dms.keydate.view"}

MEMBER_AB = _principal("u-member-ab", ["projectadmin"], "org-A", ["proj-A1", "proj-A2"])
MEMBER_A2 = _principal("u-member-a2", ["projectadmin"], "org-A", ["proj-A2"])
ORG_B_ADMIN = _principal("u-orgadmin-b", ["orgadmin"], "org-B")
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {
    MEMBER_AB.id: VARIATION_ALL | HINDRANCE_ALL | BASE,
    MEMBER_A2.id: VARIATION_ALL | HINDRANCE_ALL | BASE,
    ORG_B_ADMIN.id: VARIATION_ALL | HINDRANCE_ALL | BASE,
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


class _UnboundClaimAdapter(ClaimEntityAdapter):
    """A target type that does NOT opt into the selection (CL-4A control).

    Every registered type is selection-bound since CL-4A; this one reads the
    same Claim rows and differs only in ``active_scope_enforced``.
    """

    target_type = "unbound_claim"
    active_scope_enforced = False


REGISTRY = EntityAdapterRegistry([*EntityAdapterRegistry().adapters(), _UnboundClaimAdapter()])


def _variation(variation_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": variation_id,
        "variation_number": variation_id.upper(),
        "variation_type": "positive",
        "status": "submitted",
        "organization_id": org,
        "project_id": project,
        "contract_id": "primary",
        "currency_amounts": [],
        "linked_document_ids": [],
        "created_at": datetime(2026, 9, 2),
    }


def _hindrance(item_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": item_id,
        "organization_id": org,
        "project_id": project,
        "hindrance_ref": f"HIN-{item_id[-2:].upper()}",
        "event_type": "hindrance",
        "title": f"Access blocked {item_id}",
        "start_date": datetime(2026, 2, 10),
        "responsibility": "employer",
        "status": "open",
        "archived_at": None,
        "linked_document_ids": [],
        "evidence_link_ids": [],
        "metadata": {},
        "created_at": datetime(2026, 2, 10),
    }


def _seeded() -> _Database:
    db = _Database()
    db.projects.docs.extend(
        [
            {"_id": "proj-A1", "organization_id": "org-A", "name": "A1"},
            {"_id": "proj-A2", "organization_id": "org-A", "name": "A2"},
            {"_id": "proj-B1", "organization_id": "org-B", "name": "B1"},
        ]
    )
    db.documents.docs.extend(
        [
            _document("letter-A1", "org-A", "proj-A1", uploadType="incoming"),
            _document("letter-A2", "org-A", "proj-A2", uploadType="incoming"),
        ]
    )
    db.variations.docs.extend(
        [
            _variation("var-A1", "org-A", "proj-A1"),
            _variation("var-A2", "org-A", "proj-A2"),
            _variation("var-B1", "org-B", "proj-B1"),
        ]
    )
    db.delay_events.docs.extend(
        [
            _hindrance("hin-A1", "org-A", "proj-A1"),
            _hindrance("hin-A2", "org-A", "proj-A2"),
        ]
    )
    db.claims.docs.append(
        {
            "_id": "claim-A1",
            "claim_ref": "CLM-A1",
            "title": "Claim A1",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "status": "draft",
            "evidence_frozen_at": None,
        }
    )
    return db


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    for router in (variations_router, hindrances_router, registers_router, relationship_router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
        db, registry=REGISTRY
    )
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any, project: str | None):
    """A client whose requests carry ``project`` as the navbar selection (None = nothing selected)."""
    headers = {"X-Proj-Id": project} if project else {}
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client


def _code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def _forbidden(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


def _variation_row(db: _Database, variation_id: str) -> dict[str, Any] | None:
    return next((row for row in db.variations.docs if row["_id"] == variation_id), None)


# ---------------------------------------------------------------------------
# Variation register routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variation_detail_follows_the_selected_project() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        allowed = await client.get("/api/variations/var-A1")
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.get("/api/variations/var-A1")
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/variations/var-A1")
    assert allowed.status_code == 200, allowed.text
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    # The refusal discloses nothing about the record's project.
    assert "proj-A1" not in mismatched.text


@pytest.mark.asyncio
async def test_variation_non_member_is_refused_whatever_it_selects() -> None:
    db = _seeded()
    async with _as(db, MEMBER_A2, "proj-A1") as client:
        declared_a1 = await client.get("/api/variations/var-A1")
    async with _as(db, MEMBER_A2, "proj-A2") as client:
        own_selection = await client.get("/api/variations/var-A1")
    assert _forbidden(declared_a1), declared_a1.text
    assert own_selection.status_code == 403, own_selection.text


@pytest.mark.asyncio
async def test_variation_update_and_delete_are_bound_by_the_selection() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        put = await client.put("/api/variations/var-A1", json={"description": "moved"})
        delete = await client.delete("/api/variations/var-A1")
    async with _as(db, MEMBER_AB, None) as client:
        put_unselected = await client.put("/api/variations/var-A1", json={"description": "moved"})
        delete_unselected = await client.delete("/api/variations/var-A1")
    assert _forbidden(put) and _forbidden(delete), (put.text, delete.text)
    assert _selection_required(put_unselected) and _selection_required(delete_unselected)
    row = _variation_row(db, "var-A1")
    assert row is not None and "description" not in row or row.get("description") is None

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        ok_put = await client.put("/api/variations/var-A1", json={"description": "agreed"})
        ok_delete = await client.delete("/api/variations/var-A1")
    assert ok_put.status_code == 200, ok_put.text
    assert ok_delete.status_code == 204, ok_delete.text
    assert _variation_row(db, "var-A1") is None


@pytest.mark.asyncio
async def test_variation_create_never_rewrites_the_payload_project() -> None:
    db = _seeded()
    body = {"project_id": "proj-A1", "organization_id": "org-A", "variation_number": "VO-NEW"}
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.post("/api/variations", json=body)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.post("/api/variations", json=body)
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert not any(row.get("variation_number") == "VO-NEW" for row in db.variations.docs)

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        created = await client.post("/api/variations", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == "proj-A1"


@pytest.mark.asyncio
async def test_variation_list_narrows_to_the_selection_and_never_goes_global() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        selected = await client.get("/api/variations")
        leave = await client.get("/api/variations", params={"project_id": "proj-A2"})
        summary = await client.get("/api/variations/summary")
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/variations")
    assert selected.status_code == 200, selected.text
    assert {row["_id"] for row in selected.json()} == {"var-A1"}
    assert _forbidden(leave), leave.text
    assert summary.status_code == 200, summary.text
    # No selection: bounded to the caller's own projects, never the foreign org.
    assert unselected.status_code == 200, unselected.text
    assert {row["_id"] for row in unselected.json()} == {"var-A1", "var-A2"}


@pytest.mark.asyncio
async def test_superadmin_is_bound_by_an_explicit_selection() -> None:
    db = _seeded()
    async with _as(db, SUPERADMIN, "proj-A2") as client:
        mismatched = await client.get("/api/variations/var-A1")
        link = await client.post(
            "/api/entities/variation/var-A1/document-links:batch",
            json={"links": [{"document_id": "letter-A1", "relationship_role": "correspondence"}]},
        )
    async with _as(db, SUPERADMIN, None) as client:
        unselected = await client.get("/api/variations/var-A1")
        broad = await client.get("/api/variations")
    async with _as(db, SUPERADMIN, "proj-A1") as client:
        allowed = await client.get("/api/variations/var-A1")
    assert _forbidden(mismatched) and _forbidden(link), (mismatched.text, link.text)
    assert _selection_required(unselected), unselected.text
    assert broad.status_code == 200 and {"var-A1", "var-A2", "var-B1"} <= {row["_id"] for row in broad.json()}
    assert allowed.status_code == 200, allowed.text


# ---------------------------------------------------------------------------
# Shared relationship routes: Variation and Hindrance targets
# ---------------------------------------------------------------------------


async def _link(client: httpx.AsyncClient, target_type: str, target_id: str, document_id: str = "letter-A1"):
    return await client.post(
        f"/api/entities/{target_type}/{target_id}/document-links:batch",
        json={"links": [{"document_id": document_id, "relationship_role": "correspondence"}]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_type,target_id", [("variation", "var-A1"), ("delay_event", "hin-A1"), ("claim", "claim-A1")]
)
async def test_document_link_routes_are_bound_by_the_selection(target_type: str, target_id: str) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        link_mismatch = await _link(client, target_type, target_id)
        list_mismatch = await client.get(f"/api/entities/{target_type}/{target_id}/document-links")
    async with _as(db, MEMBER_AB, None) as client:
        link_unselected = await _link(client, target_type, target_id)
        list_unselected = await client.get(f"/api/entities/{target_type}/{target_id}/document-links")
    assert _forbidden(link_mismatch) and _forbidden(list_mismatch), (link_mismatch.text, list_mismatch.text)
    assert _selection_required(link_unselected) and _selection_required(list_unselected)
    assert not db.entity_document_links.docs

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        linked = await _link(client, target_type, target_id)
    assert linked.status_code == 201, linked.text
    link_id = linked.json()["links"][0]["_id"]

    async with _as(db, MEMBER_AB, "proj-A2") as client:
        get_mismatch = await client.get(f"/api/document-links/{link_id}")
        history_mismatch = await client.get(f"/api/document-links/{link_id}/history")
        remove_mismatch = await client.post(
            f"/api/document-links/{link_id}:remove", json={"reason": "wrong project", "expected_revision": 1}
        )
    async with _as(db, MEMBER_AB, None) as client:
        remove_unselected = await client.post(
            f"/api/document-links/{link_id}:remove", json={"reason": "no project", "expected_revision": 1}
        )
    assert _forbidden(get_mismatch) and _forbidden(history_mismatch), (get_mismatch.text, history_mismatch.text)
    assert _forbidden(remove_mismatch), remove_mismatch.text
    assert _selection_required(remove_unselected), remove_unselected.text
    assert db.entity_document_links.docs[0].get("removed_at") is None

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        removed = await client.post(
            f"/api/document-links/{link_id}:remove", json={"reason": "done", "expected_revision": 1}
        )
    assert removed.status_code == 200, removed.text


@pytest.mark.asyncio
async def test_reverse_lookup_hides_scoped_rows_outside_the_selection_only() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        for target_type, target_id in (
            ("variation", "var-A1"),
            ("delay_event", "hin-A1"),
            ("claim", "claim-A1"),
            ("unbound_claim", "claim-A1"),
        ):
            response = await _link(client, target_type, target_id)
            assert response.status_code == 201, response.text
        selected_a1 = await client.get("/api/documents/letter-A1/entity-links")
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        selected_a2 = await client.get("/api/documents/letter-A1/entity-links")
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/documents/letter-A1/entity-links")

    def _types(response: httpx.Response) -> set[str]:
        assert response.status_code == 200, response.text
        return {row["target_type"] for row in response.json()["links"]}

    assert _types(selected_a1) == {"variation", "delay_event", "claim", "unbound_claim"}
    # Selected A2: Variation, Hindrance and (CL-4A) Claim A1 are not revealed; the
    # row of a type that does not opt in keeps its selection-blind behaviour.
    assert _types(selected_a2) == {"unbound_claim"}
    assert "var-A1" not in selected_a2.text and "hin-A1" not in selected_a2.text
    # Nothing selected: the list rule - bounded by membership, not by a selection.
    assert _types(unselected) == {"variation", "delay_event", "claim", "unbound_claim"}

    # A selection the caller may not hold hides the scoped rows (fail closed).
    async with _as(db, MEMBER_AB, "proj-B1") as client:
        foreign_selection = await client.get("/api/documents/letter-A1/entity-links")
    assert _types(foreign_selection) == {"unbound_claim"}


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type,target_id", [("variation", "var-A1"), ("delay_event", "hin-A1")])
async def test_link_to_record_offers_scoped_targets_only_in_the_selected_project(
    target_type: str, target_id: str
) -> None:
    db = _seeded()
    path = "/api/documents/letter-A1/link-targets"
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        selected_a1 = await client.get(path, params={"target_type": target_type})
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        selected_a2 = await client.get(path, params={"target_type": target_type})
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(path, params={"target_type": target_type})
    assert selected_a1.status_code == 200, selected_a1.text
    assert [row["target_id"] for row in selected_a1.json()["targets"]] == [target_id]
    assert selected_a2.status_code == 200 and selected_a2.json()["targets"] == [], selected_a2.text
    assert _selection_required(unselected), unselected.text


@pytest.mark.asyncio
async def test_link_to_record_never_offers_an_archived_hindrance() -> None:
    db = _seeded()
    db.delay_events.docs[0]["archived_at"] = datetime(2026, 9, 1)
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        response = await client.get(
            "/api/documents/letter-A1/link-targets", params={"target_type": "delay_event"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["targets"] == []


@pytest.mark.asyncio
async def test_unrelated_target_types_ignore_the_selection() -> None:
    """CL-4A: a target type without ``active_scope_enforced`` is unchanged."""
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        linked = await _link(client, "unbound_claim", "claim-A1")
        listed = await client.get("/api/entities/unbound_claim/claim-A1/document-links")
        bound = await client.get("/api/entities/claim/claim-A1/document-links")
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/entities/unbound_claim/claim-A1/document-links")
    assert linked.status_code == 201, linked.text
    assert listed.status_code == 200 and len(listed.json()["links"]) == 1, listed.text
    assert unselected.status_code == 200 and len(unselected.json()["links"]) == 1, unselected.text
    # The same Claim row through its registered (bound) type is refused.
    assert _forbidden(bound), bound.text


# ---------------------------------------------------------------------------
# /api/delay-events compatibility routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compatibility_delay_events_are_bound_by_the_selection() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        get_mismatch = await client.get("/api/delay-events/hin-A1")
        patch_mismatch = await client.patch("/api/delay-events/hin-A1", json={"title": "moved"})
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        allowed = await client.get("/api/delay-events/hin-A1")
    assert _forbidden(get_mismatch) and _forbidden(patch_mismatch), (get_mismatch.text, patch_mismatch.text)
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_the_canonical_hindrance_api_accepts_no_raw_document_ids() -> None:
    """Document links on a Hindrance are written through entity_document_links.

    `/api/hindrances` has never accepted `linked_document_ids` (extra fields are
    forbidden); the G31-authorized raw path on `/api/delay-events` is kept for
    compatibility and is recorded CL-3A debt.
    """
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        created = await client.post(
            "/api/hindrances",
            json={
                "project_id": "proj-A1",
                "title": "Raw link attempt",
                "start_date": "2026-02-10T00:00:00",
                "linked_document_ids": ["letter-A1"],
            },
        )
        patched = await client.patch("/api/hindrances/hin-A1", json={"linked_document_ids": ["letter-A1"]})
    assert created.status_code == 422, created.text
    assert patched.status_code == 422, patched.text
    assert all(not row.get("linked_document_ids") for row in db.delay_events.docs)


@pytest.mark.asyncio
async def test_scope_less_legacy_variation_is_held_to_the_selected_organisation_only() -> None:
    """CL-2 keeps a project-less legacy Variation readable and deletable.

    It is in no other project, so only its organisation is held to the selection;
    a selection is still required, and the permission gate still decides.
    """
    db = _seeded()
    legacy = _variation("var-legacy", "org-A", "proj-A1")
    legacy.pop("project_id")
    foreign = _variation("var-legacy-b", "org-B", "proj-B1")
    foreign.pop("project_id")
    db.variations.docs.extend([legacy, foreign])
    async with _as(db, SUPERADMIN, "proj-A1") as client:
        own_org = await client.get("/api/variations/var-legacy")
        other_org = await client.get("/api/variations/var-legacy-b")
    async with _as(db, SUPERADMIN, None) as client:
        unselected = await client.get("/api/variations/var-legacy")
    assert own_org.status_code == 200, own_org.text
    assert _forbidden(other_org), other_org.text
    assert _selection_required(unselected), unselected.text


@pytest.mark.asyncio
async def test_hindrances_affecting_a_target_needs_the_selection_too() -> None:
    """A record-level read: the target must be in the selected project."""
    db = _seeded()
    db.key_date_milestones.docs.extend(
        [
            {"_id": "kd-A1", "organization_id": "org-A", "project_id": "proj-A1", "milestone_ref": "KD-03", "title": "Access"},
            {"_id": "kd-A2", "organization_id": "org-A", "project_id": "proj-A2", "milestone_ref": "KD-07", "title": "Depot"},
        ]
    )
    path = "/api/hindrances/affecting/key_date/kd-A1"
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        allowed = await client.get(path)
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.get(path)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(path)
    assert allowed.status_code == 200, allowed.text
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text


@pytest.mark.asyncio
async def test_an_archived_hindrance_refuses_a_canonical_unlink() -> None:
    """An archived entry is read-only: its evidence cannot be added OR removed."""
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        linked = await _link(client, "delay_event", "hin-A1")
        assert linked.status_code == 201, linked.text
        link_id = linked.json()["links"][0]["_id"]
        db.delay_events.docs[0]["archived_at"] = datetime(2026, 9, 1)
        removed = await client.post(
            f"/api/document-links/{link_id}:remove", json={"reason": "after archive", "expected_revision": 1}
        )
        # A Variation has no archive concept, so its unlink is unaffected.
        variation_link = await _link(client, "variation", "var-A1")
        variation_removed = await client.post(
            f"/api/document-links/{variation_link.json()['links'][0]['_id']}:remove",
            json={"reason": "normal", "expected_revision": 1},
        )
    assert removed.status_code == 409, removed.text
    assert db.entity_document_links.docs[0].get("removed_at") is None
    assert variation_removed.status_code == 200, variation_removed.text


@pytest.mark.asyncio
async def test_an_organisation_only_selection_still_narrows_the_lists() -> None:
    """X-Org-Id with no project: a list may not leave the selected organisation."""
    db = _seeded()
    transport_headers = {"X-Org-Id": "org-A"}
    app = _app(db, SUPERADMIN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=transport_headers
    ) as client:
        variations = await client.get("/api/variations")
        hindrances = await client.get("/api/hindrances")
        foreign_filter = await client.get("/api/variations", params={"organization_id": "org-B"})
    assert variations.status_code == 200, variations.text
    assert {row["_id"] for row in variations.json()} == {"var-A1", "var-A2"}
    assert hindrances.status_code == 200, hindrances.text
    assert {row["organization_id"] for row in hindrances.json()["items"]} == {"org-A"}
    assert _forbidden(foreign_filter), foreign_filter.text


@pytest.mark.asyncio
async def test_an_unusable_selection_says_one_thing_whatever_the_reason() -> None:
    """The refusal must not tell a caller whether a project exists or who owns it."""
    db = _seeded()
    db.projects.docs.append({"_id": "proj-dead", "organization_id": "org-A", "name": "Closed", "is_active": False})
    messages = set()
    for selection in ("proj-B1", "proj-dead", "proj-nonexistent"):
        async with _as(db, MEMBER_AB, selection) as client:
            response = await client.get("/api/variations/var-A1")
        assert _forbidden(response), (selection, response.text)
        messages.add(response.json()["detail"]["message"])
    assert messages == {"This project is not available to your account."}
    # The precise cause is kept for the operator, in the audit trail.
    reasons = {row.get("reason") for row in db.audit_events.docs if row.get("action") == "tenant.context.rejected"}
    assert len(reasons) >= 2
