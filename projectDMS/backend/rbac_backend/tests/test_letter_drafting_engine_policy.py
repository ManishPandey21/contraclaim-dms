from types import SimpleNamespace

from rbac_backend.models.letter_drafting import DraftRun, DraftRunCreateRequest
from rbac_backend.services.letter_drafting.engines import (
    DraftEngineSelector,
    canonical_request_hash,
)


def _settings(**overrides):
    values = {
        "DRAFT_ENGINE_DEFAULT": "v2",
        "DRAFT_ENGINE_ROLLOUT_MODE": "off",
        "DRAFT_ENGINE_CANARY_PERCENT": 0,
        "DRAFT_ENGINE_CANARY_TENANT_IDS": "",
        "DRAFT_ENGINE_SHADOW_ENABLED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_server_policy_defaults_to_v2_even_when_a_client_flag_exists():
    selector = DraftEngineSelector(_settings())
    assert selector.select(tenant_id="org-1", request_hash="a").engine == "v2"


def test_canary_decision_is_deterministic_and_allowlist_scoped():
    selector = DraftEngineSelector(
        _settings(
            DRAFT_ENGINE_DEFAULT="langgraph_v3",
            DRAFT_ENGINE_ROLLOUT_MODE="canary",
            DRAFT_ENGINE_CANARY_PERCENT=0,
            DRAFT_ENGINE_CANARY_TENANT_IDS="pilot-org",
        )
    )
    assert selector.select(tenant_id="pilot-org", request_hash="x").engine == "langgraph_v3"
    assert selector.select(tenant_id="other-org", request_hash="x").engine == "v2"
    assert selector.select(tenant_id="pilot-org", request_hash="x") == selector.select(
        tenant_id="pilot-org", request_hash="x"
    )


def test_shadow_never_replaces_the_v2_response_and_primary_is_explicit():
    shadow = DraftEngineSelector(
        _settings(
            DRAFT_ENGINE_DEFAULT="langgraph_v3",
            DRAFT_ENGINE_ROLLOUT_MODE="shadow",
            DRAFT_ENGINE_SHADOW_ENABLED=True,
        )
    ).select(tenant_id="org-1", request_hash="x")
    assert shadow.engine == "v2" and shadow.shadow is True

    primary = DraftEngineSelector(
        _settings(DRAFT_ENGINE_DEFAULT="langgraph_v3", DRAFT_ENGINE_ROLLOUT_MODE="primary")
    ).select(tenant_id="org-1", request_hash="x")
    assert primary.engine == "langgraph_v3"


def test_canonical_request_hash_is_order_independent_and_tenant_bound():
    first = DraftRunCreateRequest(points="Preserve rights", attachments=["b", "a"])
    second = DraftRunCreateRequest(attachments=["b", "a"], points="Preserve rights")
    assert canonical_request_hash(letter_id="letter-1", payload=first, tenant_id="org-1") == canonical_request_hash(
        letter_id="letter-1", payload=second, tenant_id="org-1"
    )
    assert canonical_request_hash(letter_id="letter-1", payload=first, tenant_id="org-1") != canonical_request_hash(
        letter_id="letter-1", payload=first, tenant_id="org-2"
    )


def test_pre_migration_records_stay_readable_with_v2_execution_defaults():
    run = DraftRun(
        run_id="legacy-run",
        letter_id="letter-1",
        mode="draft",
        status="completed",
        role="contractor",
    )
    assert run.engine == "v2"
    assert run.execution_status == "completed"
    assert run.state_schema_version == 1
