"""Pin the navbar selection in suites that test something other than the selection.

CL-4A binds every core register (and every core relationship target) to the
selected navbar project (``core/tenant_context.py``). Suites written before that -
relationship-framework guards, register business rules - exercise their subject
with the selection the browser always sends: the record's own project. They pin it
here rather than seed the projects/membership rows ``resolve_active_scope`` reads.

The selection semantics themselves (400 ``selection_required``, 403
``context_forbidden``, list narrowing, superadmin) are pinned against the REAL
``resolve_active_scope`` in the ``test_cl4a_*_scope.py`` suites and, over real Mongo
and RBAC, in ``integration/test_cl4a_core_active_scope_mongo.py``.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI

from rbac_backend.core.tenant_context import ActiveScope, RequestedScope, active_scope, requested_scope


class SelectedScope(RequestedScope):
    """A ``RequestedScope`` whose selection is already validated."""

    async def resolve(self) -> ActiveScope:
        return ActiveScope(self.db, self.user, self.organization_id or None, self.project_id or None)


def pin_selection(app: FastAPI, db: Any, organization_id: Optional[str], project_id: Optional[str]) -> None:
    """Every request of ``app`` carries this selection (``None`` project = nothing selected)."""
    app.dependency_overrides[active_scope] = lambda: ActiveScope(db, None, organization_id, project_id)
    app.dependency_overrides[requested_scope] = lambda: SelectedScope(
        db, None, organization_id or "", project_id or ""
    )


def selection(db: Any, organization_id: Optional[str], project_id: Optional[str]) -> ActiveScope:
    """For route functions a test calls directly (no FastAPI dependency resolution)."""
    return ActiveScope(db, None, organization_id, project_id)
