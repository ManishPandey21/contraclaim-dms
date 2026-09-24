"""Contract Master v1 evidence search: a real scope token, never a fabricated one.

``POST /contract-master/evidence/search`` minted its ``AuthorizedContractScope``
with ``AuthorizedContractScope.for_tests(...)`` - the constructor whose docstring
says "Test-only, and named so its use in production would be obvious in review" -
after a separate ``policy.authorize`` call, and built the organisation with
``str(current_user.organization_id)``. For a global role with nothing selected
that is the literal organisation ``"None"``.

The route now mints its scope through ``authorize_contract_scope``, which runs
``PolicyService.authorize`` and then proves the (organisation, project) pair, and
refuses 400 ``selection_required`` when the principal carries no organisation.
Contract Master is outside active-scope enforcement on this branch
(``core/tenant_context.py`` binds only the registers that opt in), so the
organisation is the validated ``CurrentUser.organization_id`` - the home
organisation for tenant-bound roles, and the selection (never a home) for global
roles.

Real-Mongo, real-RBAC coverage of the same contract is in
``integration/test_contract_master_policy_mongo.py``.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException, status

from rbac_backend.routers import contract_master_api
from rbac_backend.services import contract_scope_resolver
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.tests.test_contract_master_policy_wiring import (
    CONTRACT,
    ORG,
    PACKAGE,
    PROJECT,
    ROUTE_MATRIX,
    _app,
    _FakeDb,
    _production_files,
    _user,
)


# --------------------------------------------------------------------------- #
# evidence search: no fabricated scope, no "None" organisation
# --------------------------------------------------------------------------- #

EVIDENCE_BODY = ROUTE_MATRIX[("POST", "/api/contract-master/evidence/search")][1]


@pytest.fixture
def refusing_policy(monkeypatch):
    """Every authorize call refuses, and records what was asked."""
    asked: List[Dict[str, Any]] = []

    async def authorize(self, current_user, permission, **kwargs):
        asked.append({"policy": self, "permission": str(permission), **kwargs})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="refused by test"
        )

    monkeypatch.setattr(PolicyService, "authorize", authorize)
    return asked


def test_evidence_search_never_calls_the_test_only_constructor(monkeypatch):
    """Authorisation succeeds here, so the old code would have reached for_tests.

    The two evidence methods are stubbed to capture the scope they are handed;
    it must be a real token for the caller's organisation, minted without the
    test-only constructor.
    """
    from rbac_backend.services.contract_service import ContractService

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("runtime code called AuthorizedContractScope.for_tests")

    asked: List[Dict[str, Any]] = []
    handed: List[Any] = []

    async def authorize(self, current_user, permission, **kwargs):
        asked.append({"permission": str(permission), **kwargs})

    async def search(self, request, *, scope, mode, resolver):
        handed.append(scope)
        return SimpleNamespace(results=[], total_count=0)

    async def report(self, request, *, scope, mode, resolver):
        handed.append(scope)
        return {}

    monkeypatch.setattr(
        contract_scope_resolver.AuthorizedContractScope,
        "for_tests",
        classmethod(_forbidden),
    )
    monkeypatch.setattr(PolicyService, "authorize", authorize)
    monkeypatch.setattr(ContractService, "search_contract_evidence", search)
    monkeypatch.setattr(ContractService, "evidence_source_report", report)

    response = _app(_FakeDb(), _user()).post(
        "/api/contract-master/evidence/search", json=EVIDENCE_BODY
    )

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "valid_empty"
    (call,) = asked
    assert (call["organization_id"], call["project_id"]) == (ORG, PROJECT)
    assert len(handed) == 2
    for scope in handed:
        assert isinstance(scope, contract_scope_resolver.AuthorizedContractScope)
        assert (scope.organization_id, scope.project_id, scope.contract_id) == (
            ORG,
            PROJECT,
            CONTRACT,
        )
        assert scope.actor_id == "actor-wiring"


def test_evidence_search_authorizes_through_authorize_contract_scope(monkeypatch):
    seen: List[Dict[str, Any]] = []
    real = contract_scope_resolver.authorize_contract_scope

    async def spy(policy, current_user, **kwargs):
        seen.append(kwargs)
        return await real(policy, current_user, **kwargs)

    async def authorize(self, current_user, permission, **kwargs):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="refused by test"
        )

    monkeypatch.setattr(PolicyService, "authorize", authorize)
    monkeypatch.setattr(contract_master_api, "authorize_contract_scope", spy)
    response = _app(_FakeDb(), _user()).post(
        "/api/contract-master/evidence/search", json=EVIDENCE_BODY
    )
    assert response.status_code == 403
    assert seen == [
        {
            "permission": "dms.contract.master.view",
            "organization_id": ORG,
            "project_id": PROJECT,
            "contract_id": CONTRACT,
            "audit": False,
        }
    ]


def test_evidence_search_with_no_selected_project_is_400(refusing_policy):
    """Nothing selected: refused before any scope is minted or policy question asked.

    The body names a project and the account has a home organisation; neither is
    read as a selection.
    """
    response = _app(_FakeDb(), _user(), selected=(ORG, None)).post(
        "/api/contract-master/evidence/search", json=EVIDENCE_BODY
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "selection_required"
    assert refusing_policy == []


def test_evidence_search_outside_the_selected_project_is_403(refusing_policy):
    response = _app(_FakeDb(), _user(), selected=(ORG, "project-elsewhere")).post(
        "/api/contract-master/evidence/search", json=EVIDENCE_BODY
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "context_forbidden"
    assert refusing_policy == []


def test_evidence_search_uses_the_selection_not_the_home_organisation(refusing_policy):
    response = _app(_FakeDb(), _user("org-home-elsewhere")).post(
        "/api/contract-master/evidence/search", json=EVIDENCE_BODY
    )
    assert response.status_code == 403, response.text
    (call,) = refusing_policy
    assert (call["organization_id"], call["project_id"]) == (ORG, PROJECT)


def test_production_code_never_uses_the_test_only_scope_constructor():
    """``AuthorizedContractScope.for_tests`` skips PolicyService by design.

    Scoped to that class: other classes may legitimately own a ``for_tests``.
    """
    offenders = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "for_tests"
                and (
                    (
                        isinstance(node.value, ast.Name)
                        and node.value.id == "AuthorizedContractScope"
                    )
                    or (
                        isinstance(node.value, ast.Attribute)
                        and node.value.attr == "AuthorizedContractScope"
                    )
                )
            ):
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert offenders == [], offenders
