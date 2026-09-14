"""F-A9A-2: a shared legacy alias makes unrelated permissions equivalent.

Found in R-A9A while building the Gate 4 row 12 mutation control: removing
`dms.project.manage` from the org-admin role did NOT make the project update gate
refuse.

`LEGACY_PERMISSION_ALIASES` maps twenty canonical permissions onto the one legacy
name `projects:update` (`dms.project.manage`, but also `dms.claim.manage`,
`dms.task.manage`, `dms.keydate.manage`, `dms.ipc.approve`, ...). The intent of an
alias is one-way: a route that still checks the legacy `projects:update` accepts a
holder of the canonical permission that replaced it. `PermissionService` builds a
bidirectional table and expands a requested name through it, so equivalence
becomes transitive through the shared alias: a role holding only
`dms.task.manage` satisfies `projects:update` AND `dms.project.manage` - and
`PolicyService.has_permission("dms.project.manage")` asks exactly that. Such a
role passes both the dependency and the policy gate of
`PUT /api/projects/{id}` inside its own tenant. Tenant scope still applies; this
is an intra-tenant over-grant, not a cross-tenant leak.

A production authorization change, so it is recorded and NOT fixed in R-A9A. The
tests are strict xfails: they turn red when the defect is fixed, and the marker
must then be removed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.utils.audit_logger import AuditLogger


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items()):
                return dict(doc)
        return None

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


def _holds(monkeypatch, held: str, requested: str) -> bool:
    database = SimpleNamespace(
        users=_Collection([{"_id": "u1", "roles": ["r1"]}]),
        roles=_Collection([{"_id": "r1", "permissions": [held]}]),
        permissions=_Collection([]),
    )

    async def _get_db(self):
        return database

    async def _no_redis():
        return None

    async def _log_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(PermissionService, "_get_db", _get_db)
    monkeypatch.setattr(permission_module, "get_runtime_state", lambda: SimpleNamespace(get_redis=_no_redis))
    monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)
    return asyncio.run(PermissionService().user_has_permission("u1", requested, log=False))


def test_the_canonical_permission_still_satisfies_its_own_legacy_alias(monkeypatch) -> None:
    """The intended direction, which any fix must keep."""
    assert _holds(monkeypatch, "dms.project.manage", "projects:update")
    assert _holds(monkeypatch, "dms.task.manage", "projects:update")


def test_an_unrelated_permission_grants_nothing(monkeypatch) -> None:
    assert not _holds(monkeypatch, "dms.document.view", "dms.project.manage")


@pytest.mark.xfail(strict=True, reason="F-A9A-2: shared legacy alias makes dms.task.manage satisfy dms.project.manage")
@pytest.mark.parametrize("held", ["dms.task.manage", "dms.claim.manage", "dms.keydate.manage"])
def test_a_sibling_of_a_shared_alias_does_not_grant_project_management(monkeypatch, held: str) -> None:
    assert not _holds(monkeypatch, held, "dms.project.manage")
