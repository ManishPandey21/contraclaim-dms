import inspect
from pathlib import Path

from fastapi.routing import APIRoute

from rbac_backend.main import app


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _api_routes():
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and str(getattr(route, "path", "")).startswith("/api")
    ]


def test_frontend_referenced_route_families_are_mounted():
    paths = {getattr(route, "path", "") for route in app.routes}

    expected_paths = {
        "/api/search/documents",
        "/api/search/suggestions",
        "/api/search/popular",
        "/api/search/track",
        "/api/search/analytics",
        "/api/search/semantic",
        "/api/tasks",
        "/api/tasks/{task_id}",
        "/api/folder-structure",
        "/api/folder-structure/{path:path}",
        "/api/upload-file",
        "/api/input-requests/letter/{letter_id}",
        "/api/input-requests/{request_id}/respond",
        "/api/input-requests/{request_id}/close",
        "/api/performance/health",
        "/api/project-events",
        "/api/project-events/{event_id}",
        "/api/event-links",
        "/api/event-links/suggest",
        "/api/event-links/{link_group_id}/history",
        "/api/event-links/{link_group_id}/verify",
        "/api/event-links/{link_group_id}/reject",
        "/api/event-links/{link_group_id}/approve",
        "/api/evidence-graph/downstream-links",
        "/api/evidence-graph/backfill/dry-run",
        "/api/evidence-graph/backfill",
        "/api/evidence-graph/reconcile",
        "/api/contracts/timeline",
        "/api/chronologies",
        "/api/chronologies/{chronology_id}",
        "/api/chronologies/{chronology_id}/extract",
        "/api/chronologies/{chronology_id}/events",
        "/api/chronologies/{chronology_id}/events/{event_id}",
        "/api/chronologies/{chronology_id}/events/{event_id}/verify",
        "/api/chronologies/{chronology_id}/events/{event_id}/reject",
        "/api/chronologies/{chronology_id}/events/{event_id}/mark-duplicate",
        "/api/chronologies/{chronology_id}/events/{event_id}/link",
        "/api/chronologies/{chronology_id}/events/{event_id}/revisions",
        "/api/chronologies/{chronology_id}/pleading-context",
        "/api/chronologies/{chronology_id}/export/docx",
        "/api/chronologies/{chronology_id}/export/xlsx",
        "/api/chronologies/{chronology_id}/export/pdf",
        "/api/chronologies/{chronology_id}/export/evidence-index",
        "/api/arbitration/drafts/{draft_id}/attach-chronology",
        "/api/arbitration/drafts/{draft_id}/chronology-context",
        "/api/drawing-references",
        "/api/drawing-references/{item_id}",
        "/api/delay-events",
        "/api/delay-events/{item_id}",
        "/api/programme-milestones",
        "/api/programme-milestones/{item_id}",
        "/api/key-dates/import/template",
        "/api/key-dates/import/preview",
        "/api/key-dates/import",
        "/api/bank-guarantees/import/template",
        "/api/bank-guarantees/import/preview",
        "/api/bank-guarantees/import",
        "/api/security-terms/status",
        "/api/security-terms/accept",
        "/api/security-terms/acceptances",
        "/api/security-terms/versions",
        "/api/security-terms/versions/{version_id}/activate",
    }

    missing = sorted(expected_paths - paths)
    assert missing == []


def test_legacy_auth_routes_are_marked_deprecated():
    legacy = {
        (route.path, route.name): route
        for route in _api_routes()
        if route.name in {"login_for_access_token", "logout_user"}
    }

    assert legacy[("/api/token", "login_for_access_token")].deprecated is True
    if ("/api/logout", "logout_user") in legacy:
        assert legacy[("/api/logout", "logout_user")].deprecated is True


def test_unsafe_routes_have_explicit_auth_or_public_classification():
    public_or_legacy = {
        ("/api/login", "login"),
        ("/api/contact", "submit_contact_request"),
        # Public, rate-limited browser telemetry sink for app-shell/runtime errors.
        ("/api/client-errors", "report_client_error"),
        ("/api/token", "login_for_access_token"),
        ("/api/logout", "logout_user"),
        # Provider-called webhook: authenticated by HMAC signature verification
        # inside BillingWebhookService, not by a logged-in user.
        ("/api/billing/webhooks/{provider}", "handle_billing_webhook"),
    }
    guard_markers = (
        "get_current_user",
        "require_permission",
        "PolicyService",
        "policy.authorize",
        "authorize_scope",
        "_require_superadmin",
        "require_step_up",
        "ensure_manage_",
        "auth_service.require_permission",
        "check_organization_access",
        "check_project_access",
        "verify_langgraph_token",
    )
    missing = []

    for route in _api_routes():
        methods = set(route.methods or set()) & UNSAFE_METHODS
        if not methods:
            continue
        if (route.path, route.name) in public_or_legacy:
            continue
        source = inspect.getsource(route.endpoint)
        if not any(marker in source for marker in guard_markers):
            missing.append(f"{','.join(sorted(methods))} {route.path} {route.name}")

    assert missing == []


def test_legacy_document_permission_flow_is_removed_from_active_source():
    repo_root = Path(__file__).resolve().parents[2]
    active_roots = [
        repo_root / "rbac_backend",
        repo_root.parent / "client" / "src",
    ]
    forbidden = (
        "Legacy Document Permissions",
        "Legacy Documents Permission",
        "documents:read",
        "documents:create",
        "documents:update",
        "documents:delete",
        "documents:approve",
        "documents:share",
        "documents:upload",
        "documents:download_all",
        "documents:comment",
        "check_document_access",
    )
    allowed_files = {
        Path(__file__).resolve(),
    }

    hits: list[str] = []
    for root in active_roots:
        for path in root.rglob("*"):
            if path in allowed_files or not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append(f"{path.relative_to(repo_root.parent)}: {token}")

    assert hits == []


def test_critical_dangerous_routes_require_step_up():
    critical_routes = {
        ("/api/users/{user_id}", "delete_user"),
        ("/api/users/{user_id}/lock", "lock_user_account"),
        ("/api/users/{user_id}/unlock", "unlock_user_account"),
        ("/api/documents/{id}", "delete_document"),
        ("/api/organizations/{organization_id}", "delete_organization"),
        ("/api/projects/{project_id}", "delete_project"),
        ("/api/projects/{project_id}/deactivate", "deactivate_project"),
        ("/api/storage-sync/resync-doc", "resync_document_vectors"),
        ("/api/storage-sync/resync-bulk", "resync_bulk_vectors"),
        ("/api/storage-sync/reconcile", "reconcile_vectors"),
        ("/api/storage-sync/reconcile-files", "reconcile_files"),
        ("/api/settings/storage/org/{org_id}", "update_org_storage_settings"),
        ("/api/storage-settings/org/{org_id}", "update_org_storage_settings_alias"),
        ("/api/settings/storage/project/{project_id}", "update_project_storage_settings"),
        ("/api/storage-settings/project/{project_id}", "update_project_storage_settings_alias"),
        ("/api/smtp-settings/organization/{organization_id}", "create_organization_smtp_settings"),
        ("/api/smtp-settings/organization/{organization_id}", "update_organization_smtp_settings"),
        ("/api/smtp-settings/organization/{organization_id}/test", "test_organization_smtp_settings"),
        ("/api/smtp-settings/project/{project_id}", "create_project_smtp_settings"),
        ("/api/smtp-settings/project/{project_id}", "update_project_smtp_settings"),
        ("/api/smtp-settings/project/{project_id}/test", "test_project_smtp_settings"),
        ("/api/rbac-monetization/plans", "upsert_plan"),
        ("/api/rbac-monetization/plans/{plan_id}", "update_plan"),
        ("/api/rbac-monetization/plan-settings/scope", "update_plan_settings_scope"),
        ("/api/rbac-monetization/subscriptions", "create_subscription"),
        ("/api/rbac-monetization/subscriptions/{subscription_id}", "update_subscription"),
        ("/api/rbac-monetization/billing-records", "create_billing_record"),
        ("/api/letters/{letter_id}/drafting/runs/{run_id}/force-v2", "force_v2_fallback"),
    }

    indexed = {(route.path, route.name): route for route in _api_routes()}
    missing_routes = sorted(critical_routes - set(indexed))
    assert missing_routes == []

    missing_step_up = []
    for key in critical_routes:
        source = inspect.getsource(indexed[key].endpoint)
        if "require_step_up(" not in source and "step_up_dependency(" not in source:
            missing_step_up.append(f"{key[0]} {key[1]}")

    assert missing_step_up == []
