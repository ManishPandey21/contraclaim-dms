"""Static contracts of the Hindrance & Constraint Register.

Mounting, permission catalogue, default-role mapping, entitlement and indexes.
These pin decisions, so a change here is a deliberate authorization change.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from rbac_backend.core.database import ensure_indexes
from rbac_backend.core.permissions import CLIENT_DMS_PERMISSIONS, Permissions, permission_domain
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.main import app
from rbac_backend.services.entitlement_service import PERMISSION_FEATURE_REQUIREMENTS
from rbac_backend.services.entity_adapter_registry import EntityAdapterRegistry

HINDRANCE_PERMISSIONS = {
    "dms.hindrance.view",
    "dms.hindrance.create",
    "dms.hindrance.edit",
    "dms.hindrance.archive",
}


def _mounted() -> set[tuple[str, str]]:
    """Flatten both FastAPI representations: eager `APIRoute`s and the lazy
    `include_router` wrappers (FastAPI 0.139+, as CI installs) whose
    `effective_candidates` are the fully prefixed routes."""
    routes = []
    for mounted in app.routes:
        if isinstance(mounted, APIRoute):
            routes.append(mounted)
            continue
        candidates = getattr(mounted, "effective_candidates", None)
        if callable(candidates):
            routes.extend(candidates())
    pairs = set()
    for route in routes:
        for method in getattr(route, "methods", None) or ():
            pairs.add((method, str(getattr(route, "path", ""))))
    return pairs


def test_canonical_and_compatibility_routes_are_mounted_on_the_real_app() -> None:
    mounted = _mounted()
    expected = {
        ("GET", "/api/hindrances"),
        ("POST", "/api/hindrances"),
        ("GET", "/api/hindrances/{item_id}"),
        ("PATCH", "/api/hindrances/{item_id}"),
        ("POST", "/api/hindrances/{item_id}/archive"),
        ("POST", "/api/hindrances/{item_id}/restore"),
        ("POST", "/api/hindrances/{item_id}/timeline-sync"),
        ("GET", "/api/hindrances/{item_id}/history"),
        ("GET", "/api/hindrances/{item_id}/links"),
        ("POST", "/api/hindrances/{item_id}/links"),
        ("POST", "/api/hindrances/{item_id}/links/{link_id}/remove"),
        ("GET", "/api/hindrances/affecting/{target_type}/{target_id}"),
        # The compatibility API keeps its exact surface.
        ("GET", "/api/delay-events"),
        ("POST", "/api/delay-events"),
        ("GET", "/api/delay-events/{item_id}"),
        ("PATCH", "/api/delay-events/{item_id}"),
    }
    assert sorted(expected - mounted) == []
    assert not any(method == "DELETE" and path.startswith("/api/hindrances") for method, path in mounted)


def test_hindrance_permissions_are_catalogued_client_dms_permissions() -> None:
    assert {
        Permissions.HINDRANCE_VIEW,
        Permissions.HINDRANCE_CREATE,
        Permissions.HINDRANCE_EDIT,
        Permissions.HINDRANCE_ARCHIVE,
    } == HINDRANCE_PERMISSIONS
    assert HINDRANCE_PERMISSIONS <= set(CLIENT_DMS_PERMISSIONS)
    assert HINDRANCE_PERMISSIONS <= {row["_id"] for row in DEFAULT_PERMISSIONS}
    assert {permission_domain(permission) for permission in HINDRANCE_PERMISSIONS} == {"client_dms"}


def test_hindrance_permissions_ride_the_existing_evidence_graph_entitlement() -> None:
    # Owner-visible decision: no new plan feature, so no plan's scope widens.
    for permission in HINDRANCE_PERMISSIONS:
        assert PERMISSION_FEATURE_REQUIREMENTS[permission] == ("feature.dms.evidence_graph",)


def test_default_role_mapping_is_deliberate() -> None:
    held = {role["_id"]: set(role.get("permissions") or []) for role in DEFAULT_ROLES}
    for role_id in ("superadmin", "orgadmin", "contractmgr_org", "projectadmin"):
        assert HINDRANCE_PERMISSIONS <= held[role_id], role_id
    for role_id in (
        "orguser",
        "projectuser",
        "doccontroller",
        "reporter",
        "limited_user",
        "settings_manager",
    ):
        assert not (HINDRANCE_PERMISSIONS & held[role_id]), role_id


def test_the_relationship_registry_knows_the_register() -> None:
    adapter = EntityAdapterRegistry().get("delay_event")
    context = adapter.context_from_entity(
        {"_id": "h-1", "organization_id": "org-A", "project_id": "proj-A", "hindrance_ref": "HIN-0001"}
    )
    assert context.view_permission == Permissions.HINDRANCE_VIEW
    assert context.manage_permission == Permissions.HINDRANCE_EDIT
    assert context.route == "/hindrances/h-1"
    assert context.label == "HIN-0001"
    assert "supporting_document" in context.allowed_roles
    assert adapter.supports_freeze is False


class _IndexCollection:
    def __init__(self, name: str, sink: list[tuple[str, Any, dict[str, Any]]]) -> None:
        self.name = name
        self.sink = sink

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.sink.append((self.name, keys, kwargs))
        return kwargs.get("name") or "idx"


class _IndexDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> _IndexCollection:
        return _IndexCollection(name, self.calls)

    def __getitem__(self, name: str) -> _IndexCollection:
        return _IndexCollection(name, self.calls)


async def test_startup_indexes_enforce_reference_and_link_uniqueness() -> None:
    db = _IndexDatabase()
    await ensure_indexes(db)
    by_name = {kwargs.get("name"): (collection, keys, kwargs) for collection, keys, kwargs in db.calls}

    collection, keys, kwargs = by_name["uq_delay_events_project_reference"]
    assert collection == "delay_events"
    assert [key for key, _ in keys] == ["organization_id", "project_id", "hindrance_ref"]
    assert kwargs["unique"] is True
    assert kwargs["partialFilterExpression"] == {"hindrance_ref": {"$type": "string"}}

    collection, keys, kwargs = by_name["uq_delay_event_links_active"]
    assert collection == "delay_event_links"
    assert kwargs["unique"] is True
    assert kwargs["partialFilterExpression"] == {"removed_at": None}
    assert "ix_delay_event_links_reverse" in by_name
    assert "ix_delay_events_document_reverse" in by_name
