#!/usr/bin/env python
"""Generate the canonical Gate 3 production route inventory.

Gate 3 bullet 8 used to read "Empty, loading, and API-error states for **every
production route**" and named no route set. A requirement with an undefined
denominator cannot be measured: it can never go green, and nobody can say what
would make it so. R-A8S narrows the bullet to the states a real staging
deployment can demonstrate, and this script is what gives the narrowed bullet a
denominator with a deterministic count.

**The route list is derived, never typed.** It is parsed out of
`client/src/routes.tsx`, which is the registration React Router actually uses,
so a route added there appears here on the next run and a route deleted there
disappears. `backend/rbac_backend/tests/test_gate3_route_inventory.py` fails
when the tracked artefact and a fresh run disagree, which is what stops a new
route silently escaping the denominator.

**The state applicability is declared, not derived**, and the difference is
stated rather than blurred. Whether a page has a meaningful empty state is a
judgement about the product - there is no single `EmptyState` component to grep
for, and a heuristic over TSX source would be the same substring-matching
mistake this repository has already been bitten by once (`route_control_
manifest.json`'s `enforcement` field). So `PAGE_STATES` below records the
judgement per page component, and the guard enforces *completeness*: every page
the parser finds must carry a declaration, and every declaration must name a
page the parser finds. A new page therefore fails the build until somebody
decides what it owes, which is the property the gate needs. It does not, and
does not claim to, verify that a declaration is correct.

Usage:

    python scripts/gate3_route_inventory.py --format markdown > docs/GATE_3_ROUTE_INVENTORY.md
    python scripts/gate3_route_inventory.py --format json
    python scripts/gate3_route_inventory.py --format summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_FILE = REPO_ROOT / "client" / "src" / "routes.tsx"

#: Components that wrap a page without being one. `element={<ProtectedRoute>
#: <RoleGuard><DocumentsPage/></RoleGuard></ProtectedRoute>}` renders
#: `DocumentsPage`; the other two are authorisation chrome.
WRAPPERS = {
    "ProtectedRoute",
    "RoleGuard",
    "MainLayout",
    "Suspense",
    "Navigate",
    "Route",
    "Routes",
    "RouteSkeleton",
}

#: The session boundary. A route is authenticated when it, or any ancestor
#: layout route, renders this.
AUTH_WRAPPER = "ProtectedRoute"


# --------------------------------------------------------------------------- #
# Per-page state declarations - see the module docstring on why these are
# declared rather than derived.
# --------------------------------------------------------------------------- #
#
#   empty   - the page renders a collection whose zero-length case a viewer can
#             reach, and owes a deliberate empty state rather than blank space.
#   loading - the page fetches before it can render, and owes a deliberate
#             loading state rather than a flash of empty layout.
#   error   - where the deliberate-failure behaviour is tested. Gate 3 no longer
#             certifies this (an induced API failure needs fault injection,
#             which disqualifies the run as staging evidence); the requirement
#             moved to the mocked component/E2E layer named here.
#
# `error` may be `None` only for a page with neither an empty nor a loading
# obligation - a page that fetches nothing has no API failure to render.

STATE_UNTESTED = "MISSING - no fault-injection coverage yet"
STATE_MOCKED_E2E = "client/e2e/contract-workflows.spec.ts"
STATE_NOT_APPLICABLE = None


@dataclass(frozen=True)
class PageStates:
    empty: bool
    loading: bool
    error_test_location: Optional[str]


def _fetching(empty: bool = True, error: Optional[str] = STATE_UNTESTED) -> PageStates:
    return PageStates(empty=empty, loading=True, error_test_location=error)


def _static() -> PageStates:
    return PageStates(empty=False, loading=False, error_test_location=STATE_NOT_APPLICABLE)


#: Seeded from measured source signals on 2026-09-09 and then reviewed:
#: `loading` is declared for every page that reaches the API - directly or
#: through a hook or child panel - because such a page cannot render its subject
#: synchronously. `empty` is declared where the route's *primary subject* is a
#: collection a viewer can legitimately find empty; a detail route keyed by an
#: id, a form and a wizard step are not empty-state surfaces, they are
#: not-found, invalid and in-progress surfaces respectively.
PAGE_STATES: Dict[str, PageStates] = {
    # --- public surfaces, no API call ------------------------------------- #
    "LandingPage": _static(),
    "Overview": _static(),
    "BlogPage": _static(),
    "BlogArticlePage": PageStates(
        empty=False,
        loading=False,
        error_test_location="client/src/pages/__tests__/BlogArticlePage.test.tsx",
    ),
    "BlogVideoPage": PageStates(
        empty=False,
        loading=False,
        error_test_location="client/src/pages/__tests__/BlogVideoPage.test.tsx",
    ),
    "BlogNotFound": _static(),
    "NotFound": _static(),
    # --- session boundary -------------------------------------------------- #
    "LoginPage": PageStates(
        empty=False,
        loading=True,
        error_test_location="client/src/pages/__tests__/LoginPage.login.test.tsx",
    ),
    "SecurityTermsPage": _fetching(empty=False),
    # --- core workspace ---------------------------------------------------- #
    "Dashboard": _fetching(
        empty=False, error="client/src/pages/__tests__/Dashboard.test.tsx"
    ),
    "OrganizationsPage": _fetching(),
    "ProjectsPage": _fetching(
        error="client/src/pages/__tests__/ProjectsPage.organization-filter.test.tsx"
    ),
    "DocumentsPage": _fetching(
        error="client/src/pages/__tests__/DocumentsPage.under-process-guard.test.tsx"
    ),
    "EnhancedDocumentsPage": _fetching(),
    "TagsPage": _fetching(),
    "ProfilePage": _fetching(empty=False),
    "UsersPage": _fetching(),
    "PermissionsPage": _fetching(
        error="client/src/pages/__tests__/PermissionsPage.permission-catalog.test.ts"
    ),
    "SettingsPage": _fetching(empty=False),
    "PlanSettingsPage": _fetching(empty=False),
    "SubscriptionManagementPage": _fetching(),
    "BillingReturnPage": _fetching(empty=False),
    "NotificationCenterPage": _fetching(),
    "LegalWordsPage": _fetching(),
    "AdminLegalWordsPage": _fetching(),
    "UploadPage": _fetching(empty=False),
    "DocumentViewerPage": _fetching(empty=False),
    "ShareDocumentPage": _fetching(empty=False),
    "EmailGroupsPage": _fetching(),
    "RegisterPage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/RegisterPage.subscription.test.tsx",
    ),
    "FolderStructurePage": _fetching(),
    "TasksPage": _fetching(),
    "PartiesInvolvedPage": _fetching(),
    "ReferencePage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/ReferencePage.parsed-references.test.tsx",
    ),
    "HealthPage": _fetching(empty=False),
    "ObservabilityPage": _fetching(),
    "RetrievalConsolePage": _fetching(),
    "BillingCatalogPage": _fetching(),
    # --- letters ----------------------------------------------------------- #
    "LetterWorkflowPage": _fetching(
        error="client/src/pages/__tests__/LetterWorkflowPage.integration.test.tsx"
    ),
    "LetterSummaryPage": _fetching(
        empty=False, error="client/src/pages/__tests__/LetterSummaryPage.test.tsx"
    ),
    "LetterInputPage": _fetching(empty=False),
    "LetterStrategicPlanPage": _fetching(empty=False),
    "LetterDraftPage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/LetterDraftPage.basic-render.test.tsx",
    ),
    "LetterReviewPage": _fetching(empty=False),
    "LetterApprovalPage": _fetching(empty=False),
    "LetterCompletedPage": _fetching(empty=False),
    "LetterQualityDashboardPage": _fetching(),
    "LetterTemplatePage": _fetching(),
    "LetterTemplateEditorPage": _fetching(empty=False),
    "RepresentativesPage": _fetching(),
    # --- registers ---------------------------------------------------------- #
    "ReportsAnalyticsPage": _fetching(),
    "ClaimsRegisterPage": _fetching(),
    "ClaimDetailPage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/ClaimDetailPage.document-links.test.tsx",
    ),
    "SLATrackerPage": _fetching(),
    "KeyDateRegisterPage": _fetching(),
    "KeyDateDetailPage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/KeyDateDetailPage.achievement-evidence.test.tsx",
    ),
    "VariationRegisterPage": _fetching(),
    "BankGuaranteeRegisterPage": _fetching(
        error="client/src/pages/__tests__/BankGuaranteeRegisterPage.event-evidence.test.tsx"
    ),
    "InsuranceRegisterPage": _fetching(
        error="client/src/pages/__tests__/InsuranceRegisterPage.document-links.test.tsx"
    ),
    "IPCBillRegisterPage": _fetching(
        error="client/src/pages/__tests__/IPCBillRegisterPage.document-links.test.tsx"
    ),
    "ConcernsPage": _fetching(),
    # --- contracts ---------------------------------------------------------- #
    "ContractsPage": _fetching(error=STATE_MOCKED_E2E),
    "ContractsUploadPage": PageStates(
        empty=False, loading=True, error_test_location=STATE_MOCKED_E2E
    ),
    "ContractsSearchPage": _fetching(error=STATE_MOCKED_E2E),
    "ContractQAPage": _fetching(error=STATE_MOCKED_E2E),
    "ContractViewerPage": _fetching(),
    "ContractTimelinePage": _fetching(),
    "ContractAppraisalPage": _fetching(error=STATE_MOCKED_E2E),
    "ContractMasterPage": _fetching(error="client/e2e/contract-master.spec.ts"),
    "ContractMasterWorkspacePage": _fetching(
        error="client/e2e/contract-master.spec.ts"
    ),
    "ContractMasterPrototypePage": _fetching(
        empty=False,
        error="client/src/pages/__tests__/ContractMasterPrototypePage.test.tsx",
    ),
    # --- chronology ---------------------------------------------------------- #
    # One component serves the list, the create form, the detail, the review and
    # the presentation route, so its declaration has to cover the widest of them.
    "ChronologyBuilderPage": _fetching(),
    # --- arbitration --------------------------------------------------------- #
    "ArbitrationCaseWorkspacePage": _fetching(
        error="client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx"
    ),
    "ArbitrationDraftingPage": _fetching(
        error="client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx"
    ),
}


# --------------------------------------------------------------------------- #
# Parsing `routes.tsx`
# --------------------------------------------------------------------------- #


def _strip_comments(source: str) -> str:
    """Remove `{/* ... */}` and `/* ... */` blocks, and whole-line `//` comments.

    All three appear in `routes.tsx` and all three can contain angle brackets
    and the word `path`, which the tag scanner would otherwise read as route
    declarations.

    The bare `/* ... */` form was added in R-A8T. A commented-out route in the
    JSX spelling was correctly ignored; the same route commented out in the
    plain-TypeScript spelling was parsed as a live one and inflated the
    denominator. A false *inclusion* is the gentler direction - it demands
    coverage for a route that does not exist rather than excusing one that does
    - but the inventory is supposed to be derived from the router, and a comment
    is not the router.
    """
    source = re.sub(r"\{/\*.*?\*/\}", "", source, flags=re.S)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    kept = []
    for line in source.splitlines():
        if line.lstrip().startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


QUOTES = "\"'`"


def _end_of_open_tag(source: str, start: int) -> Tuple[int, bool]:
    """Index just past the `>` closing the tag opened at `start`, and self-closing.

    Two things put a `>` where it does not close a tag: an attribute value that
    nests JSX (`element={<Page />}`), and a quoted value that contains one
    (`path="a>b"`). The scan therefore tracks brace depth *and* quote state, and
    accepts a `>` only outside both. Without the quote half it desyncs on the
    first such attribute and every route after it is lost - silently, which is
    the one failure mode this whole inventory exists to prevent.
    """
    depth = 0
    quote: Optional[str] = None
    index = start
    while index < len(source):
        char = source[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in QUOTES:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ">" and depth == 0:
            self_closing = source[:index].rstrip().endswith("/")
            return index + 1, self_closing
        index += 1
    raise ValueError(f"unterminated tag opened at offset {start}")


def _top_level_attributes(tag: str) -> str:
    """The tag's own attributes, with every `{...}` expression blanked out.

    `<Route path="tasks" element={<RoleGuard path="/tasks" ...>}>` has two
    `path=` attributes and only the first belongs to the Route. Truncating at
    `element=` was the previous answer and it was wrong in one direction:
    `<Route element={<Page />} path="eps" />` is legal JSX and its path was
    dropped. Blanking the braced expressions instead is order-independent.
    """
    out = []
    depth = 0
    quote: Optional[str] = None
    for char in tag:
        if quote is not None:
            out.append(" " if depth else char)
            if char == quote:
                quote = None
            continue
        if char in QUOTES and depth == 0:
            quote = char
            out.append(char)
            continue
        if char == "{":
            depth += 1
            out.append(" ")
            continue
        if char == "}":
            depth = max(0, depth - 1)
            out.append(" ")
            continue
        out.append(" " if depth else char)
    return "".join(out)


#: `path="a"`, `path='a'` and a template literal are all legal, and all three
#: were parser blind spots until the R-A8S self-review probed for them.
_PATH_RE = re.compile(
    r"""\bpath\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\})"""
)

#: `<Route index element={...} />` renders at its parent's path and carries no
#: `path` of its own, so a parser keyed on `path=` drops it entirely.
_INDEX_RE = re.compile(r"\bindex\b\s*(?:=\s*\{?\s*true\s*\}?)?(?=[\s/>])")


def _attribute_path(tag: str) -> Optional[str]:
    """The `path=` of a `<Route>` opening tag, in any of its legal spellings."""
    match = _PATH_RE.search(_top_level_attributes(tag))
    if match:
        return next(group for group in match.groups() if group is not None)
    # A template literal lives inside braces, which `_top_level_attributes`
    # blanks, so it is matched against the raw tag - and only when there is no
    # top-level `path=` to prefer.
    match = _PATH_RE.search(tag)
    if match and match.group(3) is not None:
        return match.group(3)
    return None


def _is_index_route(tag: str) -> bool:
    return _INDEX_RE.search(_top_level_attributes(tag)) is not None


def _element_expression(tag: str) -> str:
    """The `element={...}` attribute's own value, and nothing after it.

    This used to return everything from `element=` to the end of the tag, so a
    sibling attribute that also holds JSX was read as part of the page. R-A8T's
    review demonstrated it with `errorElement`:

        <Route path="ee" element={<RealPage/>} errorElement={<ErrPage/>}/>

    resolved to page `ErrPage`, because `_page_component` takes the last
    component it sees. The row then carried the wrong page name, and the
    `PAGE_STATES` declaration it was matched against was the wrong one - a
    silent mis-attribution rather than a failure.

    The match is anchored on `element=` at an attribute boundary so that
    `errorElement=` and `lazyElement=` do not satisfy it, and the value is read
    to its matching brace.
    """
    match = re.search(r"(?:^|[\s{])element=", tag)
    if match is None:
        return ""
    start = tag.find("{", match.end() - 1)
    if start == -1:
        # `element=<Page/>` without braces, or a string value. Take the rest of
        # the tag, which is what the old behaviour did for every case.
        return tag[match.end():]
    depth = 0
    for index in range(start, len(tag)):
        char = tag[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return tag[start : index + 1]
    return tag[start:]


def _page_component(element: str) -> Optional[str]:
    """The rendered page: the last non-wrapper component in the element chain."""
    for name in reversed(re.findall(r"<([A-Z][A-Za-z0-9_]*)", element)):
        if name not in WRAPPERS:
            return name
    return None


def _join(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child
    if not parent or parent == "/":
        return "/" + child
    return parent.rstrip("/") + "/" + child


@dataclass(frozen=True)
class RouteRow:
    route: str
    method: str
    auth_required: bool
    page: str
    empty_state_applicable: bool
    loading_state_applicable: bool
    error_test_location: Optional[str]


class UnresolvableRoute(ValueError):
    """A `<Route>` the parser cannot place, raised rather than skipped.

    Skipping is the dangerous option: a route the parser does not understand
    would simply be absent from the denominator, and absent is exactly what
    "silently escapes" means. So a leaf `<Route>` that declares neither a `path`
    nor `index` fails the run, and the guard that regenerates the inventory
    fails with it.
    """


def parse_routes(source: str, *, strict: bool = True) -> List[dict]:
    """Every routable `<Route>`, with its resolved path and auth context.

    A `<Route>` is routable when it declares a `path` or is an `index` route. A
    `<Route>` with children and no `path` is a layout - it contributes an auth
    context and nothing else, which is correct and is not an error.
    """
    source = _strip_comments(source)
    rows: List[dict] = []
    #: (accumulated path, authenticated) for each open `<Route>` ancestor.
    stack: List[Tuple[str, bool]] = []

    index = 0
    while index < len(source):
        if source.startswith("</Route>", index):
            if stack:
                stack.pop()
            index += len("</Route>")
            continue
        if not source.startswith("<Route", index) or (
            index + 6 < len(source) and source[index + 6].isalnum()
        ):
            index += 1
            continue

        end, self_closing = _end_of_open_tag(source, index)
        tag = source[index:end]
        index = end

        parent_path, parent_auth = stack[-1] if stack else ("", False)
        element = _element_expression(tag)
        authenticated = parent_auth or (AUTH_WRAPPER in element)
        declared = _attribute_path(tag)
        is_index = declared is None and _is_index_route(tag)
        full = _join(parent_path, declared) if declared is not None else parent_path

        if declared is not None or is_index:
            page = _page_component(element)
            if page is None:
                if strict:
                    raise UnresolvableRoute(
                        "a Route declares a path but no page component could be "
                        f"identified from its element: {tag.strip()[:160]}"
                    )
            else:
                rows.append(
                    {
                        "route": full or "/",
                        "method": "GET",
                        "auth_required": authenticated,
                        "page": page,
                    }
                )
        elif "path=" in _top_level_attributes(tag) and strict:
            # A Route that DECLARES a path the parser could not read - a
            # constant, a call, anything but a literal. R-A8T's review found
            # this reached the layout branch below when the Route had children:
            # the parent vanished from the inventory and every child was
            # re-parented onto the root, so `<Route path={ROUTES.ADMIN}>` with a
            # child `kid` produced `/kid`. The self-closing spelling of the same
            # construct already raised; this makes the two agree.
            raise UnresolvableRoute(
                "a Route declares a path the inventory cannot resolve to a "
                f"literal: {tag.strip()[:160]}"
            )
        elif self_closing and strict:
            # A leaf Route with no path and no index cannot be placed. Silently
            # dropping it is how a route escapes the denominator.
            raise UnresolvableRoute(
                "a self-closing Route declares neither a path nor index, so it "
                f"cannot be placed in the inventory: {tag.strip()[:160]}"
            )

        if not self_closing:
            stack.append((full, authenticated))

    return rows


def build_inventory(routes_file: Path = ROUTES_FILE) -> dict:
    parsed = parse_routes(routes_file.read_text(encoding="utf-8"))

    rows: List[RouteRow] = []
    undeclared: List[str] = []
    for entry in parsed:
        states = PAGE_STATES.get(entry["page"])
        if states is None:
            undeclared.append(entry["page"])
            continue
        rows.append(
            RouteRow(
                route=entry["route"],
                method=entry["method"],
                auth_required=entry["auth_required"],
                page=entry["page"],
                empty_state_applicable=states.empty,
                loading_state_applicable=states.loading,
                error_test_location=states.error_test_location,
            )
        )

    rows.sort(key=lambda row: (row.route, row.page))
    orphan_declarations = sorted(
        set(PAGE_STATES) - {entry["page"] for entry in parsed}
    )

    try:
        source_label = str(routes_file.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        # A synthetic router supplied by a mutation control lives outside the
        # repository. Reporting its absolute path is correct; refusing to run
        # would make the controls untestable.
        source_label = str(routes_file).replace("\\", "/")

    return {
        "source": source_label,
        "route_count": len(rows),
        "authenticated_route_count": sum(1 for row in rows if row.auth_required),
        "empty_state_route_count": sum(1 for row in rows if row.empty_state_applicable),
        "loading_state_route_count": sum(
            1 for row in rows if row.loading_state_applicable
        ),
        "undeclared_pages": sorted(set(undeclared)),
        "orphan_declarations": orphan_declarations,
        "routes": [asdict(row) for row in rows],
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

MARKDOWN_HEADER = """# Gate 3 production route inventory

**Generated. Do not edit by hand.** Regenerate with:

```bash
python scripts/gate3_route_inventory.py --format markdown > docs/GATE_3_ROUTE_INVENTORY.md
```

This file is the denominator for Gate 3 bullet 8. The bullet used to say "every
production route" and name no route set, which made it unmeasurable in both
directions - it could never go green, and nobody could say which route had been
missed. The route list below is parsed out of `client/src/routes.tsx`, the
registration React Router actually uses, so it cannot drift from the application
without `backend/rbac_backend/tests/test_gate3_route_inventory.py` failing.

**What is derived and what is declared.** `ROUTE`, `METHOD`, `AUTH REQUIRED` and
`UI PAGE` come from the router source. `EMPTY`, `LOADING` and `ERROR TEST
LOCATION` are declared per page component in
`scripts/gate3_route_inventory.py::PAGE_STATES`, because whether a page owes a
deliberate empty state is a product judgement and this repository has been
burned before by substring heuristics that claimed a property they could not
see. The guard enforces completeness, not correctness: a page with no
declaration fails the build, and a declaration naming no page fails it too.

`METHOD` is `GET` throughout: these are browser navigations, and the states this
bullet is about are the ones a viewer sees on arrival.

**ERROR TEST LOCATION** is where deliberate-failure behaviour is proven. Gate 3
no longer certifies it - an API failure induced on demand requires fault
injection, and a run that injects faults is not the unmocked staging run Gate 3
requires - so the requirement lives at the component and mocked-E2E layer, where
controlled failure is legitimate. `MISSING` is an open test debt, tracked by
Gate 3's execution plan; it is not a licence to ship a page with no error state.
"""


def render_markdown(inventory: dict) -> str:
    lines = [MARKDOWN_HEADER, ""]
    lines.append("## Counts")
    lines.append("")
    lines.append(f"- Source: `{inventory['source']}`")
    lines.append(f"- Routes: **{inventory['route_count']}**")
    lines.append(
        f"- Authenticated routes: **{inventory['authenticated_route_count']}**"
    )
    lines.append(
        f"- Routes owing an empty state: **{inventory['empty_state_route_count']}**"
    )
    lines.append(
        f"- Routes owing a loading state: **{inventory['loading_state_route_count']}**"
    )
    lines.append("")
    lines.append("## The inventory")
    lines.append("")
    lines.append(
        "| ROUTE | METHOD | AUTH REQUIRED | UI PAGE | EMPTY | LOADING | ERROR TEST LOCATION |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in inventory["routes"]:
        lines.append(
            "| `{route}` | {method} | {auth} | `{page}` | {empty} | {loading} | {error} |".format(
                route=row["route"],
                method=row["method"],
                auth="yes" if row["auth_required"] else "no",
                page=row["page"],
                empty="yes" if row["empty_state_applicable"] else "no",
                loading="yes" if row["loading_state_applicable"] else "no",
                error=(
                    f"`{row['error_test_location']}`"
                    if row["error_test_location"]
                    and "/" in row["error_test_location"]
                    else (row["error_test_location"] or "n/a")
                ),
            )
        )
    # No trailing blank line. `print()` supplies the single final newline the
    # redirect writes, and `end-of-file-fixer` strips anything past it - which
    # made `pre-commit run --all-files` modify the tracked artefact and exit 1,
    # failing the CI leg Gate 1 bullet 6 depends on. The hook had never been
    # run locally, so the generator and the hook disagreed unnoticed.
    return "\n".join(lines)


def render_summary(inventory: dict) -> str:
    lines = [
        f"routes: {inventory['route_count']}",
        f"authenticated: {inventory['authenticated_route_count']}",
        f"empty-state applicable: {inventory['empty_state_route_count']}",
        f"loading-state applicable: {inventory['loading_state_route_count']}",
    ]
    if inventory["undeclared_pages"]:
        lines.append(f"UNDECLARED PAGES: {inventory['undeclared_pages']}")
    if inventory["orphan_declarations"]:
        lines.append(f"ORPHAN DECLARATIONS: {inventory['orphan_declarations']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format", choices=("summary", "json", "markdown"), default="summary"
    )
    parser.add_argument("--routes-file", type=Path, default=ROUTES_FILE)
    args = parser.parse_args()

    inventory = build_inventory(args.routes_file)

    if args.format == "json":
        print(json.dumps(inventory, indent=2))
    elif args.format == "markdown":
        print(render_markdown(inventory))
    else:
        print(render_summary(inventory))

    return 1 if inventory["undeclared_pages"] or inventory["orphan_declarations"] else 0


if __name__ == "__main__":
    sys.exit(main())
