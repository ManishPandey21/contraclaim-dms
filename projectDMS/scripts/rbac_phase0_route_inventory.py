"""Generate the Phase 0 backend authorization route inventory.

The inventory is intentionally static/source-based. It does not prove an
endpoint is correctly authorized; it tells reviewers which authorization pattern
is visible on every mounted FastAPI route so the Phase 0 baseline can be kept
current while later phases migrate routes to the canonical PolicyService gate.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from fastapi.routing import APIRoute

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rbac_backend.main import app


PUBLIC_OR_EXTERNAL_ROUTES: dict[tuple[str, str], str] = {
    ("/api/login", "login"): "public login endpoint",
    ("/api/contact", "submit_contact_request"): "public contact form",
    ("/api/client-errors", "report_client_error"): "public rate-limited client telemetry sink",
    ("/api/token", "login_for_access_token"): "deprecated legacy token endpoint",
    ("/api/logout", "logout_user"): "deprecated legacy logout endpoint",
    (
        "/api/billing/webhooks/{provider}",
        "handle_billing_webhook",
    ): "provider webhook authenticated by signature verification",
}

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class RouteInventoryItem:
    path: str
    name: str
    methods: tuple[str, ...]
    unsafe: bool
    classification: str
    markers: tuple[str, ...]
    public_reason: str = ""


def _effective_api_routes() -> Iterator[Any]:
    """Yield mounted API routes across FastAPI's eager and lazy representations.

    FastAPI 0.139 stores ``include_router`` entries as private lazy wrappers.
    Their ``effective_candidates`` are the fully prefixed route contexts with
    the effective dependency graph, tags, and metadata used by dispatch.
    Older FastAPI versions expose flattened ``APIRoute`` objects directly.
    """
    for mounted in app.routes:
        if isinstance(mounted, APIRoute):
            yield mounted
            continue
        candidates = getattr(mounted, "effective_candidates", None)
        if not callable(candidates):
            continue
        for route in candidates():
            if getattr(route, "endpoint", None) is not None:
                yield route


def _route_source(route: Any) -> str:
    try:
        source = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        return ""
    if route.endpoint.__module__ == "rbac_backend.routers.ai_assistant":
        from rbac_backend.routers.ai_assistant import AIAssistantController

        delegated_methods = {
            "search_similar_letters": "search_similar_letters",
            "generate_letter_draft": "generate_letter_draft",
            "generate_langgraph_draft": "generate_langgraph_draft",
            "generate_langgraph_background": "generate_langgraph_draft",
            "generate_langgraph_strategy_plan": "generate_strategy_plan",
            "get_langgraph_run": "get_langgraph_run",
        }
        method_name = delegated_methods.get(str(route.name))
        method = getattr(AIAssistantController, method_name, None) if method_name else None
        if method is not None:
            source = "\n".join(
                [
                    source,
                    inspect.getsource(method),
                    inspect.getsource(AIAssistantController._authorize_ai_action),
                ]
            )
    return source


def _contains_any(source: str, needles: Iterable[str]) -> list[str]:
    return [needle for needle in needles if needle in source]


def _classify_source(source: str, route: Any) -> tuple[str, list[str], str]:
    key = (str(route.path), str(route.name))
    if key in PUBLIC_OR_EXTERNAL_ROUTES:
        return "public_or_external", [], PUBLIC_OR_EXTERNAL_ROUTES[key]

    markers: list[str] = []

    policy_markers = _contains_any(
        source,
        [
            "policy.authorize",
            "PolicyService().authorize",
            "PolicyService(db).authorize",
            "policy_service.authorize",
            "authorize_document",
        ],
    )
    if policy_markers:
        markers.extend(policy_markers)
        if "require_step_up(" in source or "step_up_dependency(" in source:
            markers.append("step_up")
            return "policy_service_with_step_up", markers, ""
        return "policy_service", markers, ""

    legacy_permission_markers = _contains_any(
        source,
        [
            "auth_service.require_permission",
            "AuthorizationService(",
            "controller.auth_service.require_permission",
        ],
    )
    if legacy_permission_markers:
        return "legacy_permission_only", legacy_permission_markers, ""

    permission_markers = _contains_any(
        source,
        [
            "require_permission(",
            "Depends(require_permission",
        ],
    )
    if permission_markers:
        return "permission_only", permission_markers, ""

    scope_markers = _contains_any(
        source,
        [
            "authorize_scope(",
            "build_scope_query(",
            "check_organization_access",
            "check_project_access",
        ],
    )
    if scope_markers:
        return "scope_only", scope_markers, ""

    system_markers = _contains_any(
        source,
        [
            "_require_superadmin",
            "_require_platform_admin",
            "roles.includes(\"superadmin\")",
            "superadmin",
        ],
    )
    if system_markers:
        return "system_admin_guard", system_markers, ""

    step_up_markers = _contains_any(source, ["require_step_up(", "step_up_dependency("])
    if step_up_markers:
        return "step_up_only", step_up_markers, ""

    auth_markers = _contains_any(
        source,
        [
            "get_current_user",
            "get_current_active_user",
            "CurrentUser = Depends",
            "current_user: CurrentUser",
        ],
    )
    if auth_markers:
        return "auth_only", auth_markers, ""

    service_token_markers = _contains_any(
        source,
        [
            "verify_langgraph_token",
            "verify_service_token",
            "X-Service-Token",
        ],
    )
    if service_token_markers:
        return "service_token", service_token_markers, ""

    return "no_visible_guard", [], ""


def build_inventory() -> list[RouteInventoryItem]:
    items: list[RouteInventoryItem] = []
    for route in _effective_api_routes():
        path = str(getattr(route, "path", ""))
        if not path.startswith("/api"):
            continue
        methods = tuple(sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"}))
        source = _route_source(route)
        classification, markers, public_reason = _classify_source(source, route)
        items.append(
            RouteInventoryItem(
                path=path,
                name=str(route.name),
                methods=methods,
                unsafe=bool(set(methods) & UNSAFE_METHODS),
                classification=classification,
                markers=tuple(markers),
                public_reason=public_reason,
            )
        )
    return sorted(items, key=lambda item: (item.path, item.name, item.methods))


def summarize(items: list[RouteInventoryItem]) -> dict[str, object]:
    by_classification = Counter(item.classification for item in items)
    unsafe_by_classification = Counter(
        item.classification for item in items if item.unsafe
    )
    return {
        "total_api_routes": len(items),
        "unsafe_api_routes": sum(1 for item in items if item.unsafe),
        "by_classification": dict(sorted(by_classification.items())),
        "unsafe_by_classification": dict(sorted(unsafe_by_classification.items())),
    }


def render_markdown(items: list[RouteInventoryItem]) -> str:
    summary = summarize(items)
    lines = [
        "# Backend Route Authorization Inventory",
        "",
        "Generated by `scripts/rbac_phase0_route_inventory.py`.",
        "",
        "This is a static source inventory. It supports review; it does not replace",
        "route-level tests or `PolicyService` enforcement.",
        "",
        "## Summary",
        "",
        f"- Total `/api` routes: {summary['total_api_routes']}",
        f"- Unsafe routes: {summary['unsafe_api_routes']}",
        "",
        "### Classification counts",
        "",
        "| Classification | Total | Unsafe |",
        "| --- | ---: | ---: |",
    ]
    by_classification = summary["by_classification"]
    unsafe_by_classification = summary["unsafe_by_classification"]
    assert isinstance(by_classification, dict)
    assert isinstance(unsafe_by_classification, dict)
    for classification, total in by_classification.items():
        unsafe_total = unsafe_by_classification.get(classification, 0)
        lines.append(f"| `{classification}` | {total} | {unsafe_total} |")

    lines.extend(
        [
            "",
            "## Routes",
            "",
            "| Methods | Path | Endpoint | Unsafe | Classification | Markers / reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in items:
        markers = ", ".join(item.markers) or item.public_reason or "-"
        lines.append(
            f"| `{','.join(item.methods)}` | `{item.path}` | `{item.name}` | "
            f"{'yes' if item.unsafe else 'no'} | `{item.classification}` | {markers} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("summary", "json", "markdown"),
        default="summary",
        help="Output format.",
    )
    args = parser.parse_args()

    items = build_inventory()
    if args.format == "json":
        print(json.dumps([asdict(item) for item in items], indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(render_markdown(items))
    else:
        print(json.dumps(summarize(items), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
