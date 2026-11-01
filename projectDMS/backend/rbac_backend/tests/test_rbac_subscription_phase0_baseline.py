"""Phase 0 baseline guardrails for RBAC + subscription hardening.

This module contains active safety controls for the RBAC + subscription
hardening baseline. Earlier remediation phases used strict xfails to pin known
audit gaps; those markers are removed as each phase closes the gap.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rbac_backend.core.config import Settings
from rbac_backend.routers import ai_assistant, rbac_monetization, roles
from rbac_backend.services import entitlement_service
from rbac_backend.services.scope_service import ScopeService


REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_DOC = REPO_ROOT / "docs" / "RBAC_SUBSCRIPTION_PHASE0_BASELINE.md"
ROUTE_INVENTORY_SCRIPT = REPO_ROOT / "scripts" / "rbac_phase0_route_inventory.py"


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs=()):
        self._docs = list(docs)

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._docs)

    async def find_one(self, query):
        def matches(doc):
            for key, expected in (query or {}).items():
                actual = doc.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if actual not in expected["$in"] and str(actual) not in {str(item) for item in expected["$in"]}:
                        return False
                elif actual != expected and str(actual) != str(expected):
                    return False
            return True

        for doc in self._docs:
            if matches(doc):
                return dict(doc)
        return None


class _FakeScopeDB:
    def __init__(self):
        self.organization_memberships = _FakeCollection()
        self.project_memberships = _FakeCollection()
        self.projects = _FakeCollection(
            (
                {"_id": "project-from-org-A", "organization_id": "org-A"},
                {"_id": "project-from-org-B", "organization_id": "org-B"},
            )
        )


def _load_route_inventory_module():
    spec = importlib.util.spec_from_file_location(
        "rbac_phase0_route_inventory",
        ROUTE_INVENTORY_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client_org_user_without_project_assignments():
    return SimpleNamespace(
        id="user-1",
        roles=["orguser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=[],
        account_type="client_user",
    )


def test_phase0_baseline_document_exists_and_declares_security_contract():
    text = BASELINE_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "Canonical authorization contract",
        "Permission and entitlement matrix",
        "Subscription state baseline",
        "Organisation/project inheritance baseline",
        "Known Phase 0 gaps pinned by tests",
        "RBAC_ENTITLEMENT_FAIL_OPEN",
        "scripts/rbac_phase0_route_inventory.py",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in text]
    assert missing == []


def test_phase0_backend_route_inventory_classifies_every_api_route():
    module = _load_route_inventory_module()
    items = module.build_inventory()

    assert items, "inventory must include mounted /api routes"
    assert all(item.classification for item in items)
    assert all(item.path.startswith("/api") for item in items)

    summary = module.summarize(items)
    assert summary["total_api_routes"] == len(items)
    assert "policy_service" in summary["by_classification"] or "policy_service_with_step_up" in summary["by_classification"]


def test_phase0_route_inventory_keeps_public_unsafe_routes_explicit():
    module = _load_route_inventory_module()
    items = module.build_inventory()

    public_unsafe = [
        item
        for item in items
        if item.unsafe and item.classification == "public_or_external"
    ]
    assert public_unsafe, "expected login/webhook public or external route classifications"
    assert all(item.public_reason for item in public_unsafe)


def test_phase0_production_safety_controls_fail_closed():
    assert Settings.model_fields["RBAC_ENTITLEMENT_FAIL_OPEN"].default is False
    assert Settings.model_fields["ALLOW_DEV_HEADERS"].default is False

    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0",
        SECRET_KEY="x" * 32,
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["https://app.contraclaim.com"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        AUTH_COOKIE_SECURE=True,
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=True,
        CLAMAV_FAIL_OPEN=False,
        RBAC_ENTITLEMENT_FAIL_OPEN=True,
        ALLOW_DEV_HEADERS=True,
    )

    with pytest.raises(ValueError) as exc:
        settings.validate_runtime_configuration()

    message = str(exc.value)
    assert "RBAC_ENTITLEMENT_FAIL_OPEN" in message
    assert "ALLOW_DEV_HEADERS" in message


@pytest.mark.asyncio
async def test_phase1_scope_service_rejects_mismatched_org_project_pair():
    service = ScopeService(db=_FakeScopeDB())

    allowed = await service.is_client_scope_allowed(
        _client_org_user_without_project_assignments(),
        organization_id="org-A",
        project_id="project-from-org-B",
    )

    assert allowed is False


def test_phase2_ai_assistant_generation_uses_policy_service():
    helper_source = inspect.getsource(ai_assistant.AIAssistantController._authorize_ai_action)
    assert "policy_service.authorize" in helper_source
    # One-letter actions are authorised against the TARGET letter's own
    # organisation/project, not the requested or defaulted scope.
    letter_helper_source = inspect.getsource(
        ai_assistant.AIAssistantController._authorize_letter_action
    )
    assert "policy_service.authorize" in letter_helper_source
    assert "resolve_letter_scope" in letter_helper_source

    protected_methods = {
        ai_assistant.AIAssistantController.generate_letter_draft: "self._authorize_ai_action",
        ai_assistant.AIAssistantController.generate_langgraph_draft: "self._authorize_letter_action",
        ai_assistant.AIAssistantController.generate_strategy_plan: "self._authorize_letter_action",
        ai_assistant.AIAssistantController.get_langgraph_run: "self._authorize_letter_action",
    }
    for method, helper in protected_methods.items():
        source = inspect.getsource(method)
        assert "authorize_scope(" not in source
        assert helper in source


def test_phase2_route_inventory_tracks_ai_assistant_policy_service_delegation():
    module = _load_route_inventory_module()
    items = module.build_inventory()
    by_route = {(item.path, item.name): item for item in items}

    protected_ai_routes = [
        ("/api/ai-assistant/search-letters", "search_similar_letters"),
        ("/api/ai-assistant/generate-draft", "generate_letter_draft"),
        ("/api/ai-assistant/langgraph/background", "generate_langgraph_background"),
        ("/api/ai-assistant/langgraph/draft", "generate_langgraph_draft"),
        ("/api/ai-assistant/langgraph/runs/{letter_id}", "get_langgraph_run"),
        ("/api/ai-assistant/langgraph/strategy-plan", "generate_langgraph_strategy_plan"),
    ]

    assert {
        route: by_route[route].classification
        for route in protected_ai_routes
    } == {route: "policy_service" for route in protected_ai_routes}


def test_phase4_metered_operations_are_wired_to_usage_metering_service():
    policy_source = (REPO_ROOT / "backend" / "rbac_backend" / "services" / "policy_service.py").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    ai_source = (REPO_ROOT / "backend" / "rbac_backend" / "routers" / "ai_assistant.py").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    documents_source = (REPO_ROOT / "backend" / "rbac_backend" / "routers" / "documents.py").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    contracts_source = (REPO_ROOT / "backend" / "rbac_backend" / "routers" / "contracts.py").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    contract_ingest_source = (
        REPO_ROOT / "backend" / "rbac_backend" / "services" / "contracts_ingest.py"
    ).read_text(encoding="utf-8", errors="ignore")

    assert "usage_metering_service.check_and_record" in policy_source
    assert "meter_event_type" in policy_source
    assert "UsageEventType.DRAFTED_LETTER" in ai_source
    assert "UsageEventType.AI_REVIEW" in ai_source
    assert "UsageEventType.ADVANCED_SEARCH" in ai_source
    assert "UsageEventType.DOCUMENT_UPLOAD" in documents_source
    assert "UsageEventType.ADVANCED_SEARCH" in documents_source
    assert "UsageEventType.DOCUMENT_UPLOAD" in contracts_source
    assert "UsageEventType.OCR_PAGE" in contract_ingest_source


def test_phase3_entitlement_check_includes_addon_and_granular_features():
    source = inspect.getsource(entitlement_service.EntitlementService.check_permission_entitlement)
    helper_source = inspect.getsource(entitlement_service.EntitlementService._subscription_features)

    assert "_subscription_features" in source
    assert "_addon_features" in helper_source
    assert "entitlement_overrides" in helper_source
    assert entitlement_service.required_feature_keys("dms.claim.create") == (
        "feature.dms.enabled",
        "feature.dms.claims",
    )


def test_phase5_subscription_lifecycle_routes_authorize_resolved_scope():
    lifecycle_functions = [
        rbac_monetization.update_subscription,
        rbac_monetization.upgrade_subscription,
        rbac_monetization.downgrade_subscription,
        rbac_monetization.cancel_subscription,
        rbac_monetization.reactivate_subscription,
        rbac_monetization.change_billing_period,
        rbac_monetization.add_subscription_addon,
        rbac_monetization.remove_subscription_addon,
        rbac_monetization.convert_trial,
    ]

    missing_scope: list[str] = []
    for fn in lifecycle_functions:
        source = inspect.getsource(fn)
        if "_load_subscription_or_404" not in source or "_authorize_subscription_scope" not in source:
            missing_scope.append(fn.__name__)

    assert missing_scope == []


def test_phase5_subscription_helper_authorizes_stored_subscription_scope():
    source = inspect.getsource(rbac_monetization._authorize_subscription_scope)

    assert "subscription.get(\"organization_id\")" in source
    assert "subscription.get(\"project_id\")" in source
    assert "policy.authorize" in source
    assert "resource_id=subscription_id" in source


def test_phase6_role_mutation_routes_authorize_resolved_role_scope():
    scoped_role_functions = [
        roles.update_role,
        roles.delete_role,
        roles.add_role_permission,
        roles.remove_role_permission,
    ]

    for fn in scoped_role_functions:
        source = inspect.getsource(fn)
        assert "_role_policy_scope(before)" in source
        assert "policy.authorize" in source
        assert '"roles:update"' in source or '"roles:delete"' in source or '"roles:assign"' in source

    create_source = inspect.getsource(roles.create_role)
    assert "_create_role_policy_scope" in create_source
    assert '"roles:create"' in create_source


def test_phase6_role_service_blocks_direct_privilege_escalation_paths():
    from rbac_backend.services import role_service

    service_source = inspect.getsource(role_service.RoleService)
    assert "_ensure_permissions_assignable" in service_source
    assert "Not authorized to change system role status" in service_source
    assert "Not authorized to move roles across organizations" in service_source
    assert "Not authorized to modify reserved or system roles" in service_source
    assert "except RoleServiceError" in inspect.getsource(role_service.RoleService.delete_role)
    assert "_ensure_role_manageable(updated_by, existing)" in inspect.getsource(
        role_service.RoleService.update_role_permissions
    )
