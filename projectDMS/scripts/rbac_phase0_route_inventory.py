"""Generate the Phase 0 backend authorization route inventory.

The inventory is intentionally static/source-based. It does not prove an
endpoint is correctly authorized; it tells reviewers which authorization pattern
is visible on every mounted FastAPI route so the Phase 0 baseline can be kept
current while later phases migrate routes to the canonical PolicyService gate.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import sys
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from fastapi.routing import APIRoute

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rbac_backend.core.permissions import CANONICAL_PERMISSIONS, Permissions
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS
from rbac_backend.main import app


PUBLIC_OR_EXTERNAL_ROUTES: dict[tuple[str, str], str] = {
    ("/api/login", "login"): "public login endpoint",
    ("/api/contact", "submit_contact_request"): "public contact form",
    ("/api/client-errors", "report_client_error"): "public rate-limited client telemetry sink",
    ("/api/token", "login_for_access_token"): "deprecated legacy token endpoint",
    ("/api/logout", "logout_user"): "deprecated legacy logout endpoint",
    # Verified behaviourally by test_unauthenticated_requests_never_succeed:
    # these three answer 200 with no credentials, so the contract records them
    # as public rather than claiming a session requirement they do not enforce.
    (
        "/api/csrf-token",
        "get_csrf_token",
    ): "public CSRF bootstrap; must be reachable before login to issue the token the login POST requires",
    (
        "/api/profiles/health",
        "profiles_health",
    ): "public liveness probe returning a static {'status': 'ok'} with no tenant or user data",
    (
        "/api/ai-assistant/health",
        "health_check",
    ): "public liveness probe returning a static service name/timestamp with no tenant or user data",
    (
        "/api/billing/webhooks/{provider}",
        "handle_billing_webhook",
    ): "provider webhook authenticated by signature verification",
}

# Explicitly reviewed authenticated operations that are user-owned or global
# and therefore have no tenant target. They still require authentication and
# may require target-user ownership, rate limiting, CSRF, or step-up controls.
NON_TENANT_ROUTE_RATIONALES: dict[tuple[str, str], str] = {
    ("/api/me", "get_current_user_info"): "current authenticated user session projection",
    ("/api/email/legacy/send-history", "get_email_history"): "current-user email audit history",
    ("/api/email/legacy/templates", "get_email_templates"): "shared system email-template catalog",
    ("/api/email/public-share/{token}/download", "download_public_shared_document"): "capability URL guarded by signed expiring share token",
    ("/api/admin/legal-words", "list_admin_legal_words"): "platform legal-word catalog guarded by admin permission",
    ("/api/legal-words/published", "list_published_legal_words"): "authenticated platform-wide published legal words",
    ("/api/legal-words/search", "search_legal_word"): "authenticated shared legal-term lookup",
    ("/api/legal-words/today", "get_today_legal_words"): "authenticated platform-wide daily legal words",
    ("/api/logout", "logout"): "current-session revocation",
    ("/api/notifications", "list_notifications"): "current-user notification inbox",
    ("/api/notifications/delivery-logs", "list_my_delivery_logs"): "current-user notification delivery history",
    ("/api/notifications/preferences", "get_preferences"): "current-user notification preferences",
    ("/api/notifications/preferences", "update_preferences"): "current-user notification preferences",
    ("/api/notifications/unread-count", "unread_count"): "current-user notification count",
    ("/api/notifications/read-all", "mark_all_notifications_read"): "current-user notification state",
    ("/api/notifications/{notification_id}/action", "execute_notification_action"): "current-user-owned notification action",
    ("/api/notifications/{notification_id}/read", "mark_notification_read"): "current-user-owned notification state",
    ("/api/profiles/change-password", "change_my_password"): "current-user credential change with password verification",
    ("/api/profiles/me", "read_my_profile"): "current-user profile",
    ("/api/profiles/me", "update_my_profile"): "current-user profile update",
    ("/api/profiles/photo", "upload_profile_photo"): "current-user profile photo",
    ("/api/refresh", "refresh_token"): "current-session token refresh",
    ("/api/search/track", "track_search"): "current-user search telemetry",
    ("/api/search/popular", "get_popular_searches"): "static shared search suggestions",
    ("/api/security-terms/acceptances", "list_my_security_terms_acceptances"): "current-user legal acceptance history",
    ("/api/security-terms/status", "security_terms_status"): "current-user legal acceptance status",
    ("/api/security-terms/accept", "accept_security_terms"): "current-user legal acceptance",
    ("/api/auth/sso/callback", "sso_callback"): "SSO protocol callback guarded by signed state and provider validation",
    ("/api/auth/sso/login", "sso_login"): "SSO protocol redirect initialization",
    ("/api/step-up", "issue_step_up_token"): "current-user step-up challenge",
    ("/api/users/me", "get_current_user_profile"): "current authenticated user permission projection",
    ("/api/rbac-monetization/add-ons", "list_addons"): "global commercial add-on catalog",
    ("/api/rbac-monetization/entitlements/me", "get_my_effective_entitlements"): "current-user effective entitlement projection with validated tenant context",
    ("/api/rbac-monetization/plan-catalog", "get_plan_catalog"): "global commercial plan catalog",
    ("/api/rbac-monetization/plans/{plan_code}", "get_plan_detail"): "global commercial plan detail",
    ("/api/performance/cache", "get_cache_stats"): "global operational metrics guarded by platform permission",
    ("/api/performance/cache/clear", "clear_cache"): "global system cache operation guarded by platform permission and step-up",
    ("/api/performance/endpoints", "get_endpoint_performance"): "global operational metrics guarded by platform permission",
    ("/api/performance/health", "get_health_status"): "global service health response",
    ("/api/performance/job/{job_id}/cancel", "cancel_job"): "global background job operation guarded by platform permission and step-up",
    ("/api/performance/jobs", "get_job_stats"): "global operational metrics guarded by platform permission",
    ("/api/performance/metrics", "get_performance_metrics"): "global operational metrics guarded by platform permission",
    ("/api/performance/slow-queries", "get_slow_queries"): "global operational metrics guarded by platform permission",
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


@dataclass(frozen=True)
class RouteControlItem:
    """Machine-verifiable semantic authorization contract for one live route."""

    path: str
    name: str
    methods: tuple[str, ...]
    unsafe: bool
    owner: str
    authentication_mode: str
    active_user_required: bool
    permissions: tuple[str, ...]
    entitlement_requirement: str
    scope_sources: tuple[str, ...]
    target_load_required: bool
    target_load_evidence: bool
    step_up_required: bool
    audit_required: bool
    quota_required: bool
    enforcement: str
    wrong_tenant_test: str
    public_or_non_tenant_reason: str
    status: str
    #: Call-graph proof that this route reaches an authorization sink. Unlike
    #: ``enforcement`` (derived from source substring matching) this is derived
    #: from resolved call expressions, so defining or importing a gate never
    #: counts as using one. See ``scripts/authz_call_graph.py``.
    gate_evidence: str = "none"
    gate_sink: str = ""
    gate_via: str = ""
    #: Justification for a route that reaches no reusable gate. Every such route
    #: must appear in ``INLINE_GUARD_ROUTES`` or be public/non-tenant.
    gate_exception_reason: str = ""


# Routes that enforce authorization with an inline check inside the endpoint
# body rather than by calling a reusable gate. Each entry has been read and the
# enforcing expression is quoted, so the exception is auditable and narrow. A
# route may only appear here while it genuinely has no reusable-gate call path;
# ``test_route_authz_gate_evidence`` fails if an entry becomes stale.
INLINE_GUARD_ROUTES: dict[tuple[str, str], str] = {
    (
        "/api/notification-test/email",
        "send_notification_test_email",
    ): (
        "self-service unless targeting another user: raises 403 when "
        "`target_user_id != current_user.id and not _is_notification_admin(current_user)`"
    ),
    (
        "/api/rbac-monetization/plan-settings",
        "get_plan_settings",
    ): (
        "superadmin returns global settings; otherwise results are filtered by "
        "`_subscription_scope_filters(current_user)` and a caller with no "
        "subscription scope is denied 403 (fail-closed)"
    ),
    (
        "/api/search/analytics",
        "get_search_analytics",
    ): (
        "platform-wide analytics are not tenant-taggable, so the endpoint raises "
        "403 unless 'superadmin' is in the caller's roles"
    ),
    (
        "/api/search/suggestions",
        "get_search_suggestions",
    ): (
        "tenant-scoped, not admin-only: non-superadmin callers are constrained to "
        "`ScopeService().client_organization_ids/client_project_ids`, and an "
        "unrecognised role or empty scope returns an empty suggestion list"
    ),
    (
        "/api/v1/admin/vector/reconcile",
        "reconcile_vectors",
    ): (
        "raises 403 unless 'superadmin' is in the caller's roles; org_id and "
        "project_id are required query parameters"
    ),
}


KNOWN_PERMISSION_NAMES = {
    *CANONICAL_PERMISSIONS,
    *(str(item.get("_id")) for item in DEFAULT_PERMISSIONS if item.get("_id")),
    Permissions.PLATFORM_ADMIN,
    Permissions.PLATFORM_ROLE_MANAGE,
    Permissions.PLATFORM_PERMISSION_MANAGE,
}

PERMISSION_CONSTANTS = {
    name: value
    for name, value in vars(Permissions).items()
    if name.isupper() and isinstance(value, str)
}


@lru_cache(maxsize=None)
def _safe_source(target: Any) -> str:
    try:
        return inspect.getsource(inspect.unwrap(target))
    except (OSError, TypeError):
        return ""


def _is_application_callable(target: Any) -> bool:
    module = str(getattr(target, "__module__", "") or "")
    return module == "rbac_backend" or module.startswith("rbac_backend.")


def _callable_key(target: Any) -> tuple[str, str]:
    unwrapped = inspect.unwrap(target)
    return (
        str(getattr(unwrapped, "__module__", "")),
        str(getattr(unwrapped, "__qualname__", repr(unwrapped))),
    )


def _resolve_annotation(annotation: ast.expr | None, namespace: dict[str, Any]) -> Any:
    if isinstance(annotation, ast.Name):
        return namespace.get(annotation.id)
    if isinstance(annotation, ast.Subscript):
        return _resolve_annotation(annotation.value, namespace)
    if isinstance(annotation, ast.Attribute):
        owner = _resolve_expression(annotation.value, namespace, {})
        return getattr(owner, annotation.attr, None) if owner is not None else None
    return None


def _resolve_expression(
    expression: ast.expr,
    namespace: dict[str, Any],
    local_types: dict[str, Any],
) -> Any:
    if isinstance(expression, ast.Name):
        return local_types.get(expression.id) or namespace.get(expression.id)
    if isinstance(expression, ast.Call):
        return _resolve_expression(expression.func, namespace, local_types)
    if isinstance(expression, ast.Attribute):
        owner = _resolve_expression(expression.value, namespace, local_types)
        return getattr(owner, expression.attr, None) if owner is not None else None
    return None


@lru_cache(maxsize=None)
def _called_application_targets(target: Any, source: str) -> tuple[Any, ...]:
    """Resolve local helper/service calls without executing application code.

    This intentionally follows only callables that belong to ``rbac_backend``.
    It makes helper-enforced policy visible while keeping the inventory bounded
    and deterministic for CI.
    """

    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return ()

    namespace = dict(getattr(inspect.unwrap(target), "__globals__", {}) or {})
    local_types: dict[str, Any] = {}

    qualname = str(getattr(inspect.unwrap(target), "__qualname__", ""))
    owner_name = qualname.split(".")[-2] if "." in qualname else ""
    owner = namespace.get(owner_name)
    if inspect.isclass(owner):
        local_types["self"] = owner

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                resolved = _resolve_annotation(argument.annotation, namespace)
                if inspect.isclass(resolved):
                    local_types[argument.arg] = resolved
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        resolved = _resolve_expression(value.func, namespace, local_types)
        if not inspect.isclass(resolved):
            continue
        names: list[str] = []
        if isinstance(node, ast.Assign):
            names = [item.id for item in node.targets if isinstance(item, ast.Name)]
        elif isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            local_types[name] = resolved

    called: list[Any] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_expression(node.func, namespace, local_types)
        if resolved is None or not callable(resolved) or not _is_application_callable(resolved):
            continue
        called.append(resolved)
    return tuple(called)


def _route_source(route: APIRoute, *, max_depth: int = 5, max_callables: int = 120) -> str:
    """Return endpoint source plus bounded application helper/service sources."""

    queue: list[tuple[Any, int]] = [(route.endpoint, 0)]
    visited: set[tuple[str, str]] = set()
    sources: list[str] = []

    while queue and len(visited) < max_callables:
        target, depth = queue.pop(0)
        key = _callable_key(target)
        if key in visited:
            continue
        visited.add(key)
        source = _safe_source(target)
        if not source:
            continue
        sources.append(f"# semantic-source: {key[0]}:{key[1]}\n{source}")
        if depth >= max_depth:
            continue
        for called in _called_application_targets(target, source):
            if _callable_key(called) not in visited:
                queue.append((called, depth + 1))

    return "\n\n".join(sources)


def _contains_any(source: str, needles: Iterable[str]) -> list[str]:
    return [needle for needle in needles if needle in source]


def _classify_source(source: str, route: APIRoute) -> tuple[str, list[str], str]:
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
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
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


def _extract_permission_names(source: str) -> tuple[str, ...]:
    permissions: set[str] = set()
    for constant_name in re.findall(r"\bPermissions\.([A-Z][A-Z0-9_]*)\b", source):
        value = PERMISSION_CONSTANTS.get(constant_name)
        if value:
            permissions.add(value)
    for literal in re.findall(r"['\"]([a-z][a-z0-9_.-]*(?::|\.)[a-z0-9_.*-]+)['\"]", source):
        if literal in KNOWN_PERMISSION_NAMES:
            permissions.add(literal)
    return tuple(sorted(permissions))


def _route_owner(route: APIRoute) -> str:
    module = str(getattr(route.endpoint, "__module__", "") or "")
    return module.removeprefix("rbac_backend.routers.") or module or "unknown"


def _route_scope_sources(source: str) -> tuple[str, ...]:
    candidates = {
        "organization_id": ("organization_id", "organizationId", "org_id"),
        "project_id": ("project_id", "projectId", "proj_id"),
        "package_id": ("package_id", "packageId"),
        "letter_id": ("letter_id", "letterId"),
        "drafting_request_id": ("drafting_request_id", "request_id"),
    }
    return tuple(
        key for key, markers in candidates.items() if any(marker in source for marker in markers)
    )


def _target_load_evidence(source: str) -> bool:
    markers = (
        "authorize_document",
        "find_one(",
        ".get_document(",
        ".get_letter(",
        ".get_concern_by_id(",
        ".get_organization_by_id(",
        ".get_subscription_by_id(",
        ".get_user_by_id(",
        ".get_allocation(",
        "organization_id=organization_id",
        "_load_authorized(",
        "_load(",
    )
    return any(marker in source for marker in markers)


def build_route_control_manifest() -> list[RouteControlItem]:
    # Imported here: authz_call_graph imports helpers from this module, so a
    # module-level import would be circular. Support both import styles, since
    # tests import this module as ``scripts.rbac_phase0_route_inventory`` while
    # the CLI runs it as a top-level script.
    try:
        from authz_call_graph import analyze_route
    except ImportError:  # pragma: no cover - depends on sys.path shape
        from scripts.authz_call_graph import analyze_route

    inventory_by_key = {
        (item.path, item.name, item.methods): item for item in build_inventory()
    }
    controls: list[RouteControlItem] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = str(getattr(route, "path", ""))
        if not path.startswith("/api"):
            continue
        methods = tuple(sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"}))
        name = str(route.name)
        item = inventory_by_key[(path, name, methods)]
        evidence = analyze_route(route)
        source = _route_source(route)
        permissions = _extract_permission_names(source)
        scopes = _route_scope_sources(source)
        key = (path, name)
        public_reason = PUBLIC_OR_EXTERNAL_ROUTES.get(key, "")
        non_tenant_reason = NON_TENANT_ROUTE_RATIONALES.get(key, "")

        if item.classification == "public_or_external":
            authentication_mode = (
                "provider_signature"
                if "webhook" in public_reason
                else "public"
            )
        elif item.classification == "service_token":
            authentication_mode = "service_token"
        else:
            authentication_mode = "session"

        if item.classification.startswith("policy_service"):
            enforcement = "central_policy"
        elif item.classification == "system_admin_guard":
            enforcement = "platform_admin_guard"
            permissions = tuple(sorted({*permissions, Permissions.PLATFORM_ADMIN}))
        elif item.classification in {"legacy_permission_only", "permission_only"}:
            enforcement = "legacy_permission"
        elif item.classification == "public_or_external":
            enforcement = authentication_mode
        elif item.classification == "service_token":
            enforcement = "service_token"
        elif non_tenant_reason:
            enforcement = "authenticated_non_tenant"
        else:
            enforcement = "authentication_only"

        commercial = any(permission.startswith(("dms.", "drafting.")) for permission in permissions)
        entitlement_requirement = (
            "required" if commercial else "not_commercial" if permissions or public_reason or non_tenant_reason else "undeclared"
        )
        target_required = item.unsafe and bool(re.search(r"\{[^}]+(?:_id|Id|id)[^}]*\}", path)) and not non_tenant_reason
        target_evidence = _target_load_evidence(source)

        # A public / provider-signature route has no tenant target, so these two
        # blob-derived fields carry no meaning for it and are not consumed
        # downstream (wrong_tenant_test is already "not_applicable:..." and
        # target_load_required is already False). Pinning them also makes the
        # manifest deterministic: the blob for a public route such as
        # /api/contact traverses into EmailService, and suites that patch that
        # service shrink the traversal, which made the drift test order-dependent.
        if public_reason:
            scopes = ()
            target_evidence = False
        step_up = "require_step_up(" in source or "step_up_dependency(" in source
        audit_required = item.unsafe and authentication_mode not in {"public", "provider_signature"}
        quota_required = "meter_event_type" in source or "check_and_record(" in source

        if public_reason:
            wrong_tenant_test = "not_applicable:public_or_external"
        elif item.classification in {"service_token", "system_admin_guard"}:
            wrong_tenant_test = "not_applicable:global_or_service_operation"
        elif non_tenant_reason:
            wrong_tenant_test = "not_applicable:user_or_global_resource"
        elif item.unsafe and scopes:
            wrong_tenant_test = f"policy_contract:{name}"
        elif item.unsafe and target_required:
            wrong_tenant_test = f"policy_contract:{name}"
        else:
            wrong_tenant_test = "not_applicable:no_tenant_target"

        if item.classification.startswith("policy_service"):
            status_value = "complete"
            if not permissions:
                status_value = "missing_permission_declaration"
            elif target_required and not target_evidence:
                status_value = "missing_target_load_evidence"
        elif item.classification in {"public_or_external", "service_token", "system_admin_guard"}:
            status_value = "complete"
        elif non_tenant_reason:
            status_value = "complete"
        else:
            status_value = "migration_required"

        controls.append(
            RouteControlItem(
                path=path,
                name=name,
                methods=methods,
                unsafe=item.unsafe,
                owner=_route_owner(route),
                authentication_mode=authentication_mode,
                active_user_required=authentication_mode == "session",
                permissions=permissions,
                entitlement_requirement=entitlement_requirement,
                scope_sources=scopes,
                target_load_required=target_required,
                target_load_evidence=target_evidence,
                step_up_required=step_up,
                audit_required=audit_required,
                quota_required=quota_required,
                enforcement=enforcement,
                wrong_tenant_test=wrong_tenant_test,
                public_or_non_tenant_reason=public_reason or non_tenant_reason,
                status=status_value,
                gate_evidence=evidence.strength if evidence.gated else "none",
                gate_sink=evidence.sink,
                gate_via=evidence.via,
                gate_exception_reason=(
                    "" if evidence.gated else INLINE_GUARD_ROUTES.get((path, name), "")
                ),
            )
        )

    return sorted(controls, key=lambda item: (item.path, item.name, item.methods))


def summarize_route_controls(items: list[RouteControlItem]) -> dict[str, object]:
    by_status = Counter(item.status for item in items)
    unsafe_by_status = Counter(item.status for item in items if item.unsafe)
    return {
        "total_api_routes": len(items),
        "unsafe_api_routes": sum(1 for item in items if item.unsafe),
        "by_status": dict(sorted(by_status.items())),
        "unsafe_by_status": dict(sorted(unsafe_by_status.items())),
        "wrong_tenant_tests_required": sum(
            1 for item in items if item.wrong_tenant_test.startswith("policy_contract:")
        ),
    }


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
        choices=("summary", "json", "markdown", "contract-summary", "contract-json"),
        default="summary",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file. Intended for the checked-in route-control manifest.",
    )
    args = parser.parse_args()

    if args.format.startswith("contract-"):
        controls = build_route_control_manifest()
        payload = (
            json.dumps(summarize_route_controls(controls), indent=2, sort_keys=True)
            if args.format == "contract-summary"
            else json.dumps(
                {
                    "schema_version": 1,
                    "summary": summarize_route_controls(controls),
                    "routes": [asdict(item) for item in controls],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        items = build_inventory()
        if args.format == "json":
            payload = json.dumps([asdict(item) for item in items], indent=2, sort_keys=True)
        elif args.format == "markdown":
            payload = render_markdown(items)
        else:
            payload = json.dumps(summarize(items), indent=2, sort_keys=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
