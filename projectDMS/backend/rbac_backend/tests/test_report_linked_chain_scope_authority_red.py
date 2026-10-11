"""G30: the linked-letter-chain report's tenant boundary must come from the
CALLER'S IDENTITY, never from a caller-supplied request field.

`_generate_linked_chain_report` is the one report of the six that never derives
scope from `current_user`. Its enrichment query was bounded solely by
`request.organization_id` / `request.project_id`, and both are
`Optional[str] = None` on `ReportRequest`. The router authorizes against
`request.organization_id or current_user.organization_id`, so an omitted
`organizationId` passes the gate on the user's own organisation while the
service applies no fallback at all: the Mongo enrichment then runs unfiltered
across every tenant, and the `if not meta: continue` guard that the code calls
"the tenant boundary for this report" stops dropping anything.

The shipped UI produces exactly that request - `ReportsAnalyticsPage` sends
`organizationId: selected || project?.organization_id || undefined`, which is
`undefined` for a global-role user before a navbar selection.

Two leak shapes are pinned here, both reachable with the `dms.report.view`
permission and no privilege escalation:

* a foreign TENANT's letter reaching the preview rows / CSV when
  `organizationId` is omitted;
* a foreign PROJECT's letter reaching a project-tier user when `projectId` is
  omitted - a request-derived boundary cannot consult `current_user.projects`,
  so supplying `organizationId` alone does not contain it.

A denial-based defence does not cover either: `graph_codes_denied` answers
"does a consumable supporter exist?" and carries no tenant or project predicate
whatsoever, so a foreign tenant's clean document satisfies it. Both foreign
documents below are deliberately clean and consumable, so the ONLY thing that
can contain them is scope.

Beside every "now rejects" case is a "still works" case: legitimate in-scope
enrichment must survive, and superadmin's documented unrestricted read must
keep working.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.report import ReportRequest
from rbac_backend.services import report_service as report_service_module
from rbac_backend.services.report_service import ReportService

FOREIGN_TENANT_MARKER = "ORG_B_CONFIDENTIAL_MARKER_20260830"
FOREIGN_PROJECT_MARKER = "PROJ_B_CONFIDENTIAL_MARKER_20260830"
IN_SCOPE_MARKER = "ORG_A_PROJ_A_LEGITIMATE_MARKER_20260830"

ROOT_CODE = "LTR-ROOT-001"
LINKED_CODE = "LTR-LINKED-001"
LINKED_NORM = "ltr-linked-001"


# ---------------------------------------------------------------------------
# Mongo boundary: the query IS the thing under test, so it must be applied.
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if not any(_same(actual, candidate) for candidate in expected["$in"]):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, *_a: Any, **_k: Any) -> "_Cursor":
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield deepcopy(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    def aggregate(self, _pipeline: Any) -> _Cursor:  # pragma: no cover - unused here
        return _Cursor([])


class _Database:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._collections: Dict[str, _Collection] = {"documents": _Collection(documents)}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


class _Graph:
    """Falkor boundary. Post-G32 the shared node carries identity only."""

    enabled = True

    def __init__(self, norm_codes: List[str]) -> None:
        self.norm_codes = norm_codes

    def _execute(self, _query: str, _params: Dict[str, Any]) -> Any:
        return self.norm_codes

    def _parse_rows(self, result: Any) -> List[Dict[str, Any]]:
        return [{"normCode": code} for code in result]


def _document(
    *,
    organization_id: str,
    project_id: str,
    marker: str,
    letter_no: str = LINKED_CODE,
) -> Dict[str, Any]:
    """A CLEAN, consumable supporter. Only scope can contain it."""
    return {
        "_id": f"doc-{organization_id}-{project_id}",
        "letter_no": letter_no,
        "letterNo": letter_no,
        "letterNoNormalized": LINKED_NORM,
        "organization_id": organization_id,
        "project_id": project_id,
        "subject": f"{marker} SUBJECT",
        "summary": f"{marker} SUMMARY BODY",
        "date": "2026-08-30",
        "processing_status": "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }


def _user(roles: List[str], *, organization_id: Optional[str], projects: List[str]) -> CurrentUser:
    return CurrentUser(
        id="user-1",
        username="reporter",
        email="reporter@example.com",
        roles=roles,
        organization_id=organization_id,
        organizations=[organization_id] if organization_id else [],
        projects=projects,
    )


def _request(**overrides: Any) -> ReportRequest:
    payload: Dict[str, Any] = {
        "report_id": "linked-letter-chain",
        "letter_no": ROOT_CODE,
        "chain_direction": "up",
        "include_self": False,
    }
    payload.update(overrides)
    return ReportRequest(**payload)


async def _preview(
    monkeypatch: pytest.MonkeyPatch,
    documents: List[Dict[str, Any]],
    request: ReportRequest,
    user: CurrentUser,
):
    db = _Database(documents)
    monkeypatch.setattr(
        report_service_module,
        "get_database",
        lambda: _async_value(db),
    )
    import rbac_backend.services.falkor_graph_service as falkor_module

    monkeypatch.setattr(falkor_module, "FalkorGraphService", lambda: _Graph([LINKED_NORM]))
    return await ReportService(db).generate_preview(request, user)


def _async_value(value: Any):
    async def _coro():
        return value

    return _coro()


def _serialized(preview: Any) -> str:
    return repr(preview.rows) + repr(preview.metrics)


# ---------------------------------------------------------------------------
# Now rejects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omitted_organization_id_does_not_publish_a_foreign_tenants_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped UI sends `organizationId: undefined` before a selection."""
    preview = await _preview(
        monkeypatch,
        [_document(organization_id="org-B", project_id="proj-B", marker=FOREIGN_TENANT_MARKER)],
        _request(organization_id=None),
        _user(["orguser"], organization_id="org-A", projects=[]),
    )

    assert FOREIGN_TENANT_MARKER not in _serialized(preview), (
        "the linked-chain report published another tenant's letter because the "
        "request omitted organizationId; scope must come from current_user"
    )
    assert preview.rows == []
    assert preview.metrics["total_linked"] == 0


@pytest.mark.asyncio
async def test_omitted_project_id_does_not_publish_an_unassigned_projects_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supplying organizationId is not containment for a project-tier user."""
    preview = await _preview(
        monkeypatch,
        [_document(organization_id="org-A", project_id="proj-B", marker=FOREIGN_PROJECT_MARKER)],
        _request(organization_id="org-A", project_id=None),
        _user(["projectuser"], organization_id="org-A", projects=["proj-A"]),
    )

    assert FOREIGN_PROJECT_MARKER not in _serialized(preview), (
        "a project-tier user received a letter from a project they are not "
        "assigned to; the report never consults current_user.projects"
    )
    assert preview.rows == []
    assert preview.metrics["total_linked"] == 0


@pytest.mark.asyncio
async def test_a_foreign_organization_id_in_the_request_cannot_widen_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supplied organizationId outside the caller's entitlement denies all."""
    preview = await _preview(
        monkeypatch,
        [_document(organization_id="org-B", project_id="proj-B", marker=FOREIGN_TENANT_MARKER)],
        _request(organization_id="org-B"),
        _user(["orguser"], organization_id="org-A", projects=[]),
    )

    assert FOREIGN_TENANT_MARKER not in _serialized(preview)
    assert preview.rows == []


# ---------------------------------------------------------------------------
# Still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_scope_letter_is_still_enriched_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = await _preview(
        monkeypatch,
        [_document(organization_id="org-A", project_id="proj-A", marker=IN_SCOPE_MARKER)],
        _request(organization_id="org-A"),
        _user(["projectuser"], organization_id="org-A", projects=["proj-A"]),
    )

    assert preview.metrics["total_linked"] == 1
    assert len(preview.rows) == 1
    row = preview.rows[0]
    assert row["letter_no"] == LINKED_NORM
    assert IN_SCOPE_MARKER in row["subject"]
    assert IN_SCOPE_MARKER in row["summary"]
    assert row["organization_id"] == "org-A"
    assert row["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_superadmin_consolidated_read_is_unrestricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superadmin's documented cross-tenant read must not regress to deny-all."""
    preview = await _preview(
        monkeypatch,
        [_document(organization_id="org-B", project_id="proj-B", marker=FOREIGN_TENANT_MARKER)],
        _request(organization_id=None),
        _user(["superadmin"], organization_id=None, projects=[]),
    )

    assert preview.metrics["total_linked"] == 1
    assert FOREIGN_TENANT_MARKER in preview.rows[0]["subject"]


@pytest.mark.asyncio
async def test_a_blocked_in_scope_document_is_still_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope containment is added to authority containment, not instead of it."""
    blocked = _document(organization_id="org-A", project_id="proj-A", marker=IN_SCOPE_MARKER)
    blocked["duplicate_status"] = "duplicate"

    preview = await _preview(
        monkeypatch,
        [blocked],
        _request(organization_id="org-A"),
        _user(["orguser"], organization_id="org-A", projects=[]),
    )

    assert IN_SCOPE_MARKER not in _serialized(preview)
    assert preview.rows == []
    assert preview.metrics["total_linked"] == 0
