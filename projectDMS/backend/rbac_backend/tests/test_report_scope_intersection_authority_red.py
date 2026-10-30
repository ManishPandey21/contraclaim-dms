"""G30: report row visibility must INTERSECT organisation and project
entitlement, never union them.

`ReportService._scope_clause` built its filter as::

    {"$or": [{"organization_id": "org-A"}, {"project_id": "proj-A"}]}

Either branch alone admits a row. So a project-tier actor assigned only to
Project A matched every record whose `organization_id` is Organisation A —
including Project B's, which they are not assigned to. The canonical
row-visibility helper `build_scope_query` produces the intersection instead::

    {"organization_id": "org-A", "project_id": {"$in": ["proj-A"]}}

Five report families consumed the OR clause and are pinned here, each through
its FINAL OUTPUT (preview rows, metrics and `total_rows`) rather than through
the query dict, because the defect is material disclosure:

* `letter-status`      — `documents`, aggregate rows + status breakdown + count
* `letter-received`    — `documents`, aggregate rows + count
* `letter-by-tags`     — `documents`, aggregate rows + count
* `document-activity`  — `documents`, find rows + status/upload breakdowns
* `task-completion`    — `tasks`,     find rows + status breakdown

The sixth report, `linked-letter-chain`, never called `_scope_clause`; its own
boundary is pinned by `test_report_linked_chain_scope_authority_red.py` (G-A1).

Two further widenings of the same helper are pinned:

* `_scope_clause` ORed together every entry of `current_user.organizations`,
  which for a project-tier caller is entitlement history, not the active
  selection. `EffectiveScope = Entitlement ∩ Navbar Selection`, so a second
  organisation in that list must not become visible rows.
* an UNRECOGNISED role fell through to an organisation-wide clause built from
  whatever `organization_id` the caller carried. The canonical helper denies
  all instead. That is a deliberate, visible fail-closed correction — see
  `test_an_unrecognised_role_is_denied_rather_than_given_the_whole_organisation`.

Global behaviour is asserted in the same file so a fail-closed fix cannot
quietly make superadmin or superuser project-bound.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.report import ReportRequest
from rbac_backend.services import report_service as report_service_module
from rbac_backend.services.report_service import ReportService

ORG_A = "org-A"
ORG_B = "org-B"
PROJ_A = "proj-A"
PROJ_B = "proj-B"
PROJ_C = "proj-C"

IN_SCOPE_MARKER = "ORG_A_PROJ_A_LEGITIMATE_MARKER_20260831"
SIBLING_PROJECT_MARKER = "ORG_A_PROJ_B_CONFIDENTIAL_MARKER_20260831"
FOREIGN_ORG_MARKER = "ORG_B_PROJ_C_CONFIDENTIAL_MARKER_20260831"

#: The five report families that consumed `_scope_clause`.
DOCUMENT_REPORTS = ("letter-status", "letter-received", "letter-by-tags", "document-activity")
SCOPE_CLAUSE_REPORTS = DOCUMENT_REPORTS + ("task-completion",)


# ---------------------------------------------------------------------------
# Mongo boundary. The scope filter IS the thing under test, so it must be
# applied for real - by the fake collection, exactly as the driver would.
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
                candidates = expected["$in"]
                if isinstance(actual, list):
                    if not any(_same(item, c) for item in actual for c in candidates):
                        return False
                    continue
                if not any(_same(actual, candidate) for candidate in candidates):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


def _sort_key(row: Dict[str, Any], spec: Dict[str, Any]):
    key: List[Any] = []
    for field in spec:
        value = row.get(field)
        key.append((value is None, "" if value is None else str(value)))
    return key


def _resolve(row: Dict[str, Any], token: Any) -> Any:
    if isinstance(token, str) and token.startswith("$"):
        return row.get(token[1:])
    return token


def _project(row: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, rule in spec.items():
        if rule == 1:
            if field in row:
                out[field] = row[field]
            continue
        if isinstance(rule, dict) and "$ifNull" in rule:
            for token in rule["$ifNull"]:
                value = _resolve(row, token)
                if value is not None:
                    out[field] = value
                    break
            else:
                out[field] = None
            continue
        out[field] = _resolve(row, rule)  # pragma: no cover - defensive
    return out


def _group(rows: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    buckets: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    for row in rows:
        key = _resolve(row, spec["_id"])
        if key not in buckets:
            buckets[key] = {"_id": key}
            order.append(key)
            for name, accumulator in spec.items():
                if name == "_id":
                    continue
                if isinstance(accumulator, dict) and "$sum" in accumulator:
                    buckets[key][name] = 0
        for name, accumulator in spec.items():
            if name == "_id":
                continue
            if isinstance(accumulator, dict) and "$sum" in accumulator:
                buckets[key][name] += accumulator["$sum"]
    return [buckets[key] for key in order]


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, spec: Any = None, direction: Any = None) -> "_Cursor":
        if isinstance(spec, str):
            spec = {spec: direction or 1}
        if isinstance(spec, dict):
            self.rows.sort(key=lambda row: _sort_key(row, spec))
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])


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

    async def count_documents(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> int:
        return sum(1 for row in self.rows if _matches(row, query))

    def aggregate(self, pipeline: List[Dict[str, Any]], *_a: Any, **_k: Any) -> _Cursor:
        rows = [deepcopy(row) for row in self.rows]
        for stage in pipeline:
            (name, spec), = stage.items()
            if name == "$match":
                rows = [row for row in rows if _matches(row, spec)]
            elif name == "$project":
                rows = [_project(row, spec) for row in rows]
            elif name == "$group":
                rows = _group(rows, spec)
            elif name == "$sort":
                rows.sort(key=lambda row: _sort_key(row, spec))
            elif name == "$limit":
                rows = rows[:spec]
            else:  # pragma: no cover - the reports use nothing else
                raise AssertionError(f"unsupported aggregation stage: {name}")
        return _Cursor(rows)


class _Database:
    def __init__(
        self, documents: List[Dict[str, Any]], tasks: List[Dict[str, Any]]
    ) -> None:
        self._collections: Dict[str, _Collection] = {
            "documents": _Collection(documents),
            "tasks": _Collection(tasks),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


# ---------------------------------------------------------------------------
# Fixtures: one organisation with two projects, plus a foreign organisation.
# ---------------------------------------------------------------------------


def _document(*, organization_id: str, project_id: str, marker: str) -> Dict[str, Any]:
    return {
        "_id": f"doc-{organization_id}-{project_id}",
        "letter_no": f"LTR-{marker[:12]}",
        "organization_id": organization_id,
        "project_id": project_id,
        "subject": f"{marker} SUBJECT",
        "summary": f"{marker} SUMMARY BODY",
        "status": f"{marker}_STATUS",
        "uploadType": f"{marker}_UPLOAD",
        "direction": "incoming",
        "tags": [f"{marker}_TAG"],
        "subTags": [f"{marker}_SUBTAG"],
        "date": "2026-08-31",
        "created_at": "2026-08-31",
        "createdAt": "2026-08-31",
        "updated_at": "2026-08-31",
        "updatedAt": "2026-08-31",
    }


def _task(*, organization_id: str, project_id: str, marker: str) -> Dict[str, Any]:
    return {
        "_id": f"task-{organization_id}-{project_id}",
        "title": f"{marker} TITLE",
        "status": f"{marker}_STATUS",
        "priority": "high",
        "assigned_to": "user-1",
        "organization_id": organization_id,
        "project_id": project_id,
        "created_at": "2026-08-31",
        "updated_at": "2026-08-31",
    }


ALL_DOCUMENTS = [
    _document(organization_id=ORG_A, project_id=PROJ_A, marker=IN_SCOPE_MARKER),
    _document(organization_id=ORG_A, project_id=PROJ_B, marker=SIBLING_PROJECT_MARKER),
    _document(organization_id=ORG_B, project_id=PROJ_C, marker=FOREIGN_ORG_MARKER),
]

ALL_TASKS = [
    _task(organization_id=ORG_A, project_id=PROJ_A, marker=IN_SCOPE_MARKER),
    _task(organization_id=ORG_A, project_id=PROJ_B, marker=SIBLING_PROJECT_MARKER),
    _task(organization_id=ORG_B, project_id=PROJ_C, marker=FOREIGN_ORG_MARKER),
]


def _user(
    roles: List[str],
    *,
    organization_id: Optional[str],
    projects: List[str],
    organizations: Optional[List[str]] = None,
) -> CurrentUser:
    if organizations is None:
        organizations = [organization_id] if organization_id else []
    return CurrentUser(
        id="user-1",
        username="reporter",
        email="reporter@example.com",
        roles=roles,
        organization_id=organization_id,
        organizations=organizations,
        projects=projects,
    )


def _request(report_id: str, **overrides: Any) -> ReportRequest:
    payload: Dict[str, Any] = {"report_id": report_id}
    payload.update(overrides)
    return ReportRequest(**payload)


def _async_value(value: Any):
    async def _coro():
        return value

    return _coro()


async def _preview(
    monkeypatch: pytest.MonkeyPatch,
    request: ReportRequest,
    user: CurrentUser,
):
    db = _Database(ALL_DOCUMENTS, ALL_TASKS)
    monkeypatch.setattr(report_service_module, "get_database", lambda: _async_value(db))
    return await ReportService(db).generate_preview(request, user)


def _serialized(preview: Any) -> str:
    """Rows AND metrics: a count that discloses a foreign record is disclosure."""
    return repr(preview.rows) + repr(preview.metrics)


# ---------------------------------------------------------------------------
# A. Project sibling leak - the defect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_a_sibling_project_in_the_same_organisation_is_not_disclosed(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """Project A's user must not read Project B merely because the org matches."""
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_A),
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
    )

    assert SIBLING_PROJECT_MARKER not in _serialized(preview), (
        f"report '{report_id}' disclosed a project the caller is not assigned to; "
        "organisation and project entitlement must INTERSECT, not union"
    )
    assert preview.total_rows == 1, (
        f"report '{report_id}' counted {preview.total_rows} records for a caller "
        "entitled to exactly one"
    )
    assert len(preview.rows) == 1


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_an_omitted_organization_id_does_not_widen_to_the_sibling_project(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """The shipped Reports page sends no organizationId before a selection."""
    preview = await _preview(
        monkeypatch,
        _request(report_id),
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
    )

    assert SIBLING_PROJECT_MARKER not in _serialized(preview)
    assert FOREIGN_ORG_MARKER not in _serialized(preview)
    assert preview.total_rows == 1


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_a_caller_supplied_project_cannot_override_assignment(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """`projectId` is a narrowing selection, never an authority substitute."""
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_A, project_id=PROJ_B),
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
    )

    assert SIBLING_PROJECT_MARKER not in _serialized(preview)
    assert preview.total_rows == 0
    assert preview.rows == []


# ---------------------------------------------------------------------------
# B. Foreign organisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_a_foreign_organization_id_in_the_request_cannot_widen_scope(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_B),
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
    )

    assert FOREIGN_ORG_MARKER not in _serialized(preview)
    assert preview.total_rows == 0


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_a_second_entitlement_organisation_is_not_the_active_selection(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """`EffectiveScope = Entitlement ∩ Navbar Selection`.

    `_scope_clause` ORed every entry of `current_user.organizations`, so a
    project-tier caller carrying a second organisation in that list read that
    organisation's rows without ever selecting it.
    """
    preview = await _preview(
        monkeypatch,
        _request(report_id),
        _user(
            ["projectuser"],
            organization_id=ORG_A,
            projects=[PROJ_A],
            organizations=[ORG_A, ORG_B],
        ),
    )

    assert FOREIGN_ORG_MARKER not in _serialized(preview), (
        f"report '{report_id}' published a non-selected organisation's records "
        "because the scope clause unioned the caller's entitlement history"
    )
    assert preview.total_rows == 1


# ---------------------------------------------------------------------------
# C. In-scope positive - containment must not become denial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_the_assigned_project_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_A, project_id=PROJ_A),
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
    )

    assert IN_SCOPE_MARKER in _serialized(preview), (
        f"report '{report_id}' lost the caller's own project"
    )
    assert preview.total_rows == 1
    assert len(preview.rows) == 1


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_an_organisation_tier_caller_without_assignments_reads_the_organisation(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """An org-tier caller with no project assignments keeps the org-wide read."""
    preview = await _preview(
        monkeypatch,
        _request(report_id),
        _user(["orguser"], organization_id=ORG_A, projects=[]),
    )

    serialized = _serialized(preview)
    assert IN_SCOPE_MARKER in serialized
    assert SIBLING_PROJECT_MARKER in serialized
    assert FOREIGN_ORG_MARKER not in serialized
    assert preview.total_rows == 2


# ---------------------------------------------------------------------------
# D. Global / superadmin behaviour must not regress to project-bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_superadmin_consolidated_read_stays_unrestricted(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    preview = await _preview(
        monkeypatch,
        _request(report_id),
        _user(["superadmin"], organization_id=None, projects=[]),
    )

    serialized = _serialized(preview)
    assert IN_SCOPE_MARKER in serialized
    assert SIBLING_PROJECT_MARKER in serialized
    assert FOREIGN_ORG_MARKER in serialized
    assert preview.total_rows == 3


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_superadmin_may_narrow_to_one_organisation(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_B),
        _user(["superadmin"], organization_id=None, projects=[]),
    )

    serialized = _serialized(preview)
    assert FOREIGN_ORG_MARKER in serialized
    assert IN_SCOPE_MARKER not in serialized
    assert preview.total_rows == 1


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_a_superuser_is_bounded_by_entitlement_not_by_the_home_organisation(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """The global `superuser` role keeps its multi-organisation read."""
    preview = await _preview(
        monkeypatch,
        _request(report_id),
        _user(
            ["superuser"],
            organization_id=ORG_A,
            projects=[],
            organizations=[ORG_A, ORG_B],
        ),
    )

    serialized = _serialized(preview)
    assert IN_SCOPE_MARKER in serialized
    assert SIBLING_PROJECT_MARKER in serialized
    assert FOREIGN_ORG_MARKER in serialized
    assert preview.total_rows == 3


# ---------------------------------------------------------------------------
# E. Unrecognised roles - a visible fail-closed correction, pinned on purpose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_id", SCOPE_CLAUSE_REPORTS)
@pytest.mark.asyncio
async def test_an_unrecognised_role_is_denied_rather_than_given_the_whole_organisation(
    monkeypatch: pytest.MonkeyPatch, report_id: str
) -> None:
    """`reporter` holds `reports:view` (aliased to `dms.report.view`) but is not
    a tier `build_scope_query` recognises.

    `_scope_clause` gave it an organisation-wide clause built from whatever
    `organization_id` the caller happened to carry - an org-wide read granted by
    a helper's fallback rather than by the authorization model. The canonical
    helper denies all instead.

    This is a BEHAVIOUR CHANGE, deliberately pinned so it stays visible: a
    caller holding ONLY `reporter` (or only `contractmgr_org`) now gets an empty
    report. If org-wide access for those seeded roles is intended, the fix
    belongs in `build_scope_query`, which is the single canonical row-visibility
    authority - never in a report-local fallback that would restore the union.
    """
    preview = await _preview(
        monkeypatch,
        _request(report_id, organization_id=ORG_A),
        _user(["reporter"], organization_id=ORG_A, projects=[]),
    )

    assert _serialized(preview).count(IN_SCOPE_MARKER) == 0
    assert SIBLING_PROJECT_MARKER not in _serialized(preview)
    assert FOREIGN_ORG_MARKER not in _serialized(preview)
    assert preview.total_rows == 0


# ---------------------------------------------------------------------------
# F. The legacy union helper must not survive anywhere in the report layer
# ---------------------------------------------------------------------------


def test_no_report_scope_filter_unions_organisation_with_project() -> None:
    """A structural pin: the leak was one `$or` between two scope dimensions.

    Every report family is asserted through its output above; this catches a
    sixth caller added later that reintroduces the union shape directly.
    """
    scope = ReportService._scope_clause(
        _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A]),
        "organization_id",
        "project_id",
    )

    assert "$or" not in (scope or {}), (
        "the report scope filter unions organisation with project again: "
        f"{scope}. Either branch alone admits a row, which is the leak."
    )
    assert scope and scope.get("organization_id") == ORG_A
    assert PROJ_A in (scope.get("project_id") or {}).get("$in", [])
