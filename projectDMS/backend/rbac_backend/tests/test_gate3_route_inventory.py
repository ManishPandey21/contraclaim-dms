"""Gate 3 bullet 8 must have a denominator, and the denominator must be real.

The bullet used to read "Empty, loading, and API-error states for **every
production route**". Two things were wrong with it and only one was about
testing.

The first is that "every production route" named no route set. A requirement
with no denominator cannot go green and cannot be shown to be missed - it is
unfalsifiable in the same way Gate 2's FalkorDB vector criterion was, and the
same remedy applies: say what it means, mechanically.

The second is that the error state cannot be certified the way Gate 3 certifies
everything else. Gate 3's preamble says a bullet is satisfied only by a run
against a real staging environment, and an API failure produced on demand
requires the API to be mocked or faulted. A run that mocks is not a staging run;
a staging run cannot fail on command. The bullet was therefore asking for two
mutually exclusive things.

R-A8S narrows Gate 3 to `EMPTY` and `LOADING` over the enumerated route set, and
relocates the error-state requirement to the layer where controlled failure is
legitimate. **The UI quality bar does not move.** What moves is where the
evidence is produced, and this module is what stops either half being quietly
dropped:

* the inventory must exist, be generated, and match a fresh run of the generator;
* every page must carry a state declaration, and every declaration must name a
  page that exists, so a new route cannot land undeclared;
* Gate 3 must not re-acquire a mocked-error requirement;
* the relocated error-state requirement must still be present somewhere.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import List

import pytest

RBAC_BACKEND = Path(__file__).resolve().parents[1]
PROJECT = RBAC_BACKEND.parents[1]
GENERATOR = PROJECT / "scripts" / "gate3_route_inventory.py"
INVENTORY = PROJECT / "docs" / "GATE_3_ROUTE_INVENTORY.md"
GATE_FILE = PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"
MATRIX = PROJECT / "docs" / "GATE_3_EVIDENCE_MATRIX.md"
ROUTES_TSX = PROJECT / "client" / "src" / "routes.tsx"


def _generator():
    name = "_gate3_route_inventory"
    spec = importlib.util.spec_from_file_location(name, GENERATOR)
    assert spec and spec.loader, f"cannot load {GENERATOR}"
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because `@dataclass` resolves string
    # annotations through `sys.modules[cls.__module__]`, which raises for a
    # module loaded straight off a path.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    assert GENERATOR.is_file(), f"the route inventory generator is missing at {GENERATOR}"
    return _generator()


@pytest.fixture(scope="module")
def inventory(generator):
    return generator.build_inventory()


# --------------------------------------------------------------------------- #
# The denominator exists and is derived
# --------------------------------------------------------------------------- #


def test_the_inventory_is_generated_from_the_router_and_not_typed(generator) -> None:
    """The route list has to come from the registration React Router uses.

    A hand-maintained list is a second source of truth that drifts silently, and
    silently is the only way a route escapes a denominator.
    """
    source = GENERATOR.read_text(encoding="utf-8")
    assert "client" in source and "routes.tsx" in source
    assert generator.ROUTES_FILE == ROUTES_TSX
    assert ROUTES_TSX.is_file(), "the router source the generator parses is gone"


def test_the_inventory_has_a_deterministic_count(generator, inventory) -> None:
    """Two runs over the same source agree, and the count is plausible."""
    again = generator.build_inventory()
    assert again == inventory, "the generator is not deterministic"
    assert inventory["route_count"] > 50, (
        f"only {inventory['route_count']} routes derived; the parser has probably "
        "stopped seeing most of the router"
    )


def test_the_parser_sees_every_route_the_router_declares(inventory) -> None:
    """Cross-checked against the shape `client/src/config/__tests__/routeInventory.test.ts` uses.

    That guard counts `<Route ... path="...">` with a regex. If the Python parser
    resolves fewer paths than the regex finds, it is dropping routes - which is
    precisely the failure this whole module exists to prevent.

    The regex is **not** an independent check for every route form: it shares the
    parser's old blind spots (single quotes, template literals, `index`). It is a
    floor, not a ceiling, and `test_the_parser_has_no_blind_spot_a_route_could_hide_in`
    is what covers the rest.
    """
    source = ROUTES_TSX.read_text(encoding="utf-8")
    declared = re.findall(r'<Route\b[^>]*?\bpath="([^"]+)"', source)
    assert declared, "the regex cross-check found no routes at all"
    assert inventory["route_count"] >= len(declared), (
        f"the generator resolved {inventory['route_count']} routes but the "
        f"double-quoted regex alone finds {len(declared)} - the parser is dropping routes"
    )


@pytest.mark.parametrize(
    "label, snippet, expected",
    [
        pytest.param(
            "index-route",
            '<Routes><Route path="parent" element={<AlphaPage />}>'
            "<Route index element={<BetaPage />} /></Route></Routes>",
            [("/parent", "AlphaPage"), ("/parent", "BetaPage")],
            id="index-route-renders-at-its-parents-path",
        ),
        pytest.param(
            "single-quoted",
            "<Routes><Route path='beta' element={<BetaPage />} /></Routes>",
            [("/beta", "BetaPage")],
            id="single-quoted-path",
        ),
        pytest.param(
            "template-literal",
            "<Routes><Route path={`gamma`} element={<GammaPage />} /></Routes>",
            [("/gamma", "GammaPage")],
            id="template-literal-path",
        ),
        pytest.param(
            "gt-in-value",
            '<Routes><Route path="a>b" element={<DeltaPage />} />'
            '<Route path="after" element={<AfterPage />} /></Routes>',
            [("/a>b", "DeltaPage"), ("/after", "AfterPage")],
            id="a-gt-inside-a-quoted-value-does-not-desync-the-scan",
        ),
        pytest.param(
            "element-first",
            '<Routes><Route element={<EpsilonPage />} path="eps" /></Routes>',
            [("/eps", "EpsilonPage")],
            id="element-declared-before-path",
        ),
        pytest.param(
            "commented-out",
            '<Routes>{/* <Route path="ghost" element={<GhostPage />} /> */}</Routes>',
            [],
            id="a-commented-out-route-is-not-a-route",
        ),
    ],
)
def test_the_parser_has_no_blind_spot_a_route_could_hide_in(
    generator, label: str, snippet: str, expected: list
) -> None:
    """Every one of these dropped a route silently until R-A8S probed for it.

    A route form the parser does not understand is a route absent from the
    denominator, which is the exact failure the inventory exists to prevent - and
    it is invisible, because nothing counts what was never seen.
    """
    rows = generator.parse_routes(snippet)
    assert [(row["route"], row["page"]) for row in rows] == expected, rows


def test_a_route_the_parser_cannot_place_fails_rather_than_disappears(generator) -> None:
    """The rule that makes the list above a floor rather than a whitelist.

    New route forms will keep arriving. The parser cannot be taught all of them
    in advance, so the fallback has to be a failure, not a skip.
    """
    with pytest.raises(generator.UnresolvableRoute):
        generator.parse_routes(
            '<Routes><Route element={<MysteryPage />} /></Routes>'
        )

    # A layout route - children, no path - is legitimate and must not trip it.
    rows = generator.parse_routes(
        "<Routes><Route element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>"
        '<Route path="inside" element={<InsidePage />} /></Route></Routes>'
    )
    assert rows == [
        {"route": "/inside", "method": "GET", "auth_required": True, "page": "InsidePage"}
    ], rows


def test_an_unresolvable_path_fails_even_when_the_route_has_children(generator) -> None:
    """R-A8T. The self-closing and with-children spellings must agree.

    `<Route path={ROUTES.ADMIN} .../>` already raised, because a leaf the parser
    cannot place is a route escaping the denominator. The *same* declaration
    with children fell through to the layout branch instead: the parent vanished
    from the inventory and its children were re-parented onto the root, so a
    child `kid` was recorded as `/kid` rather than under the constant's path.
    Silently - which is worse than either a raise or a wrong count alone,
    because the total still looked plausible.
    """
    with pytest.raises(generator.UnresolvableRoute):
        generator.parse_routes(
            "<Routes><Route path={ROUTES.ADMIN} element={<AdminPage />}>"
            '<Route path="kid" element={<KidPage />} /></Route></Routes>'
        )

    # A genuine layout - no `path=` attribute at all - is still legitimate.
    rows = generator.parse_routes(
        "<Routes><Route element={<MainLayout />}>"
        '<Route path="inside" element={<InsidePage />} /></Route></Routes>'
    )
    assert [row["route"] for row in rows] == ["/inside"], rows


def test_a_sibling_jsx_attribute_does_not_become_the_page(generator) -> None:
    """R-A8T. `_element_expression` read to the end of the tag, not to its brace.

    So a route with an `errorElement` was attributed to the error page, and the
    row was then matched against the wrong `PAGE_STATES` declaration. Wrong
    coverage claimed for the wrong screen, with the count unchanged.
    """
    rows = generator.parse_routes(
        '<Routes><Route path="ee" element={<RealPage />} '
        "errorElement={<ErrPage />} /></Routes>"
    )
    assert [(row["route"], row["page"]) for row in rows] == [("/ee", "RealPage")], rows

    # Attribute order must not matter either.
    rows = generator.parse_routes(
        '<Routes><Route errorElement={<ErrPage />} path="ee2" '
        "element={<RealPage />} /></Routes>"
    )
    assert [(row["route"], row["page"]) for row in rows] == [("/ee2", "RealPage")], rows


def test_a_plain_block_comment_is_not_a_route(generator) -> None:
    """R-A8T. The JSX comment spelling was stripped; the plain one was not.

    A commented-out route was counted as live, which demands coverage for a
    screen that does not exist. The gentler direction of the same defect, and
    still a denominator that is not the router.
    """
    rows = generator.parse_routes(
        "<Routes>/* <Route path=\"ghost\" element={<GhostPage />} /> */"
        '<Route path="real" element={<RealPage />} /></Routes>'
    )
    assert [row["route"] for row in rows] == ["/real"], rows


#: Routes owing an empty or loading state that name an existing error test,
#: measured on 2026-09-09 (R-A8T). A ratchet, not a target.
ERROR_TESTS_LOCATED_ON_2026_09_09 = 36


def test_the_relocated_error_coverage_does_not_shrink(inventory) -> None:
    """Bullet 8's amendment relocated the error state; this is what holds it there.

    The amendment moved the error-state requirement to the component and
    mocked-E2E layer. Relocation is only honest while the destination exists, and
    the gate document's prose claimed error behaviour was "still tested" when 49
    of the 85 applicable routes name no error test at all. The number is now
    stated there, and this ratchet stops the other 36 being deleted afterwards
    - which would satisfy the amendment's letter by emptying it.

    The floor is deliberately the measurement and not a round number: raising it
    is a real improvement someone should bank here.
    """
    applicable = [
        row
        for row in inventory["routes"]
        if row["empty_state_applicable"] or row["loading_state_applicable"]
    ]
    located = [
        row
        for row in applicable
        if row["error_test_location"] and "MISSING" not in row["error_test_location"]
    ]
    assert applicable, "no route owes an empty or loading state; the ratchet is vacuous"
    assert len(located) >= ERROR_TESTS_LOCATED_ON_2026_09_09, (
        f"{len(located)} of {len(applicable)} applicable routes name an error "
        f"test; {ERROR_TESTS_LOCATED_ON_2026_09_09} did on 2026-09-09. Bullet 8's "
        "amendment relocated the error-state requirement rather than deleting it, "
        "so the destination may not shrink"
    )


def test_the_tracked_inventory_survives_the_end_of_file_hook(inventory) -> None:
    """The artefact must be a fixed point of `pre-commit run --all-files`.

    It was not. `render_markdown` appended a blank line and `print` added
    another newline, so the checked-in file ended in a blank line,
    `end-of-file-fixer` rewrote it, and the hook set exited 1 - failing
    `backend-checks`, which is the CI leg Gate 1 bullet 6 is waiting on. Nobody
    saw it because `pre-commit` is not installed on the development host, so the
    hook that would have caught it is the same hook the gate is blocked on.
    """
    raw = INVENTORY.read_bytes()
    assert raw.endswith(b"\n"), "the inventory does not end with a newline"
    assert not raw.endswith(b"\n\n"), (
        "the inventory ends with a blank line, so `end-of-file-fixer` will "
        "rewrite it and `pre-commit run --all-files` will exit 1 in CI"
    )
    assert not raw.endswith(b"\r\n\r\n"), "same, with CRLF endings"


def test_the_tracked_inventory_matches_a_fresh_run(generator, inventory) -> None:
    """The artefact is checked in so reviewers can read it; it must not go stale."""
    assert INVENTORY.is_file(), (
        f"the Gate 3 route inventory is missing at {INVENTORY}; Gate 3 bullet 8 has "
        "no denominator"
    )
    assert INVENTORY.read_text(encoding="utf-8").strip() == generator.render_markdown(
        inventory
    ).strip(), (
        "docs/GATE_3_ROUTE_INVENTORY.md is out of date. Regenerate it:\n"
        "  python scripts/gate3_route_inventory.py --format markdown "
        "> docs/GATE_3_ROUTE_INVENTORY.md"
    )


def test_the_inventory_states_its_own_count(inventory) -> None:
    text = INVENTORY.read_text(encoding="utf-8")
    assert f"Routes: **{inventory['route_count']}**" in text
    assert f"Routes owing an empty state: **{inventory['empty_state_route_count']}**" in text
    assert (
        f"Routes owing a loading state: **{inventory['loading_state_route_count']}**"
        in text
    )


# --------------------------------------------------------------------------- #
# No route escapes the denominator
# --------------------------------------------------------------------------- #


def test_every_derived_page_carries_a_state_declaration(inventory) -> None:
    assert not inventory["undeclared_pages"], (
        "these pages are reachable from the router and declare nothing about their "
        f"empty/loading obligations: {inventory['undeclared_pages']}. Add them to "
        "scripts/gate3_route_inventory.py::PAGE_STATES."
    )


def test_no_declaration_names_a_page_the_router_does_not_reach(inventory) -> None:
    """The other direction. A declaration for a deleted page inflates nothing but
    it is a claim about a surface that no longer exists, and it hides the fact
    that its route went away."""
    assert not inventory["orphan_declarations"], (
        "these declarations name pages the router does not reach: "
        f"{inventory['orphan_declarations']}"
    )


def test_the_applicable_set_is_not_empty(inventory) -> None:
    """Otherwise the narrowed bullet is satisfied by declaring nothing applicable."""
    assert inventory["empty_state_route_count"] > 0
    assert inventory["loading_state_route_count"] > 0
    assert inventory["loading_state_route_count"] >= inventory["empty_state_route_count"], (
        "more routes owe an empty state than owe a loading state, which cannot be "
        "true: a page that renders a collection has to fetch it first"
    )


def test_every_named_error_test_location_exists(inventory) -> None:
    missing = []
    for row in inventory["routes"]:
        location = row["error_test_location"]
        if not location or "/" not in location:
            continue
        if not (PROJECT / location).exists():
            missing.append((row["route"], location))
    assert not missing, f"error-state tests are claimed from files that do not exist: {missing}"


def test_a_page_that_owes_a_state_names_an_error_location(inventory) -> None:
    """`MISSING` is allowed and is the honest answer; `None` is not.

    A page that fetches has an API failure to render. Leaving its error column
    blank would let the relocated requirement evaporate one row at a time.
    """
    silent = [
        row["route"]
        for row in inventory["routes"]
        if (row["empty_state_applicable"] or row["loading_state_applicable"])
        and not row["error_test_location"]
    ]
    assert not silent, (
        f"these routes owe an empty or loading state and say nothing about where "
        f"their error state is tested: {silent}"
    )


# --------------------------------------------------------------------------- #
# The mutations. Each breaks exactly one rule.
# --------------------------------------------------------------------------- #


SYNTHETIC_ROUTER = """
const AlphaPage = lazyWithRetry(() => import("./pages/AlphaPage"));
const BetaPage = lazyWithRetry(() => import("./pages/BetaPage"));

const AppRoutes = () => (
  <Routes>
    <Route path="/public" element={<AlphaPage />} />
    <Route element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>
      <Route path="beta" element={<RoleGuard path="/beta"><BetaPage /></RoleGuard>} />
    </Route>
  </Routes>
);
"""


def test_the_parser_resolves_nesting_and_authentication(generator) -> None:
    """Anchor for the mutations: the parser must actually work first."""
    rows = generator.parse_routes(SYNTHETIC_ROUTER)
    assert rows == [
        {"route": "/public", "method": "GET", "auth_required": False, "page": "AlphaPage"},
        {"route": "/beta", "method": "GET", "auth_required": True, "page": "BetaPage"},
    ], rows


def test_a_route_added_without_a_declaration_is_red(generator, monkeypatch, tmp_path) -> None:
    """Mutation: a new production route lands with no empty/loading decision."""
    router = tmp_path / "routes.tsx"
    router.write_text(
        SYNTHETIC_ROUTER.replace(
            '    </Route>',
            '      <Route path="gamma" element={<GammaPage />} />\n    </Route>',
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        generator.PAGE_STATES, "AlphaPage", generator.PageStates(False, False, None)
    )
    monkeypatch.setitem(
        generator.PAGE_STATES, "BetaPage", generator.PageStates(True, True, "x.tsx")
    )

    result = generator.build_inventory(router)
    assert "GammaPage" in result["undeclared_pages"], (
        "a route added to the router without a state declaration was absorbed "
        "silently; the denominator would have grown with nothing behind it"
    )


def test_an_empty_route_inventory_is_red(generator, tmp_path) -> None:
    """Mutation: the denominator becomes undefined by the inventory going empty."""
    router = tmp_path / "routes.tsx"
    router.write_text("const AppRoutes = () => (<Routes></Routes>);\n", encoding="utf-8")

    result = generator.build_inventory(router)
    assert result["route_count"] == 0
    with pytest.raises(AssertionError):
        assert result["route_count"] > 50


def test_a_stale_tracked_inventory_is_red(generator, inventory) -> None:
    """Mutation: the router gains a route and the artefact is not regenerated."""
    stale = generator.render_markdown(inventory).replace(
        f"Routes: **{inventory['route_count']}**",
        f"Routes: **{inventory['route_count'] - 1}**",
    )
    assert stale.strip() != generator.render_markdown(inventory).strip()


# --------------------------------------------------------------------------- #
# Gate 3 must not re-acquire the mocked-error requirement
# --------------------------------------------------------------------------- #


def _gate3_bullets() -> List[tuple[str, str]]:
    text = GATE_FILE.read_text(encoding="utf-8")
    section = re.search(r"^### Gate 3:.*?$(.*?)^### Gate 4:", text, re.S | re.M)
    assert section, "the Gate 3 section is gone from the release gate document"
    return re.findall(r"^- \[([ xX])\] (.+)$", section.group(1), re.M)


def validate_gate3_state_bullet(bullets: List[str]) -> List[str]:
    """One message per violated rule for the narrowed state-coverage bullet."""
    errors: List[str] = []
    lowered = [text.lower() for text in bullets]

    state_bullets = [text for text in lowered if "empty" in text and "loading" in text]
    if not state_bullets:
        errors.append(
            "state coverage lost: no Gate 3 bullet requires empty and loading states"
        )
    for text in state_bullets:
        if "error" in text:
            errors.append(
                "mocked-error requirement returned: a Gate 3 bullet asks for an "
                f"API-error state, which no unmocked staging run can produce: {text!r}"
            )
        if "every production route" in text and "inventory" not in text:
            errors.append(
                "undefined denominator returned: a Gate 3 bullet says 'every "
                f"production route' without naming the inventory: {text!r}"
            )
        if "gate_3_route_inventory" not in text.replace(" ", "_").lower() and (
            "route inventory" not in text
        ):
            errors.append(
                f"the state bullet does not name its route denominator: {text!r}"
            )
    return errors


def test_the_gate3_state_bullet_is_sound() -> None:
    assert validate_gate3_state_bullet([text for _, text in _gate3_bullets()]) == []


SOUND_STATE_BULLET = (
    "Empty and loading states across the applicable routes in "
    "`docs/GATE_3_ROUTE_INVENTORY.md`."
)


def test_the_sound_synthetic_bullet_passes() -> None:
    assert validate_gate3_state_bullet([SOUND_STATE_BULLET]) == []


@pytest.mark.parametrize(
    "bullet, expected",
    [
        pytest.param(
            "Empty, loading, and API-error states for every production route.",
            "mocked-error requirement returned",
            id="original-wording-restored",
        ),
        pytest.param(
            "Empty, loading and error states across the applicable routes in "
            "`docs/GATE_3_ROUTE_INVENTORY.md`.",
            "mocked-error requirement returned",
            id="error-state-added-back",
        ),
        pytest.param(
            "Empty and loading states for every production route.",
            "undefined denominator returned",
            id="denominator-removed",
        ),
    ],
)
def test_each_state_bullet_breakage_is_caught(bullet: str, expected: str) -> None:
    errors = validate_gate3_state_bullet([bullet])
    assert any(expected in error for error in errors), (
        f"expected an error containing {expected!r}; got {errors}"
    )


def test_deleting_the_state_bullet_entirely_is_caught() -> None:
    errors = validate_gate3_state_bullet(["Login, logout, session refresh, and CSRF."])
    assert any("state coverage lost" in error for error in errors)


# --------------------------------------------------------------------------- #
# The relocated error-state requirement must still exist
# --------------------------------------------------------------------------- #


def test_the_error_state_requirement_survives_outside_gate_3(inventory) -> None:
    """Narrowing Gate 3 is not permission to stop testing error states.

    The requirement moved to the layer where controlled failure is legitimate.
    If every relocated test were deleted, Gate 3 would be green and the product
    would have no error-state coverage at all - which is the outcome the
    narrowing must not buy.
    """
    located = {
        row["error_test_location"]
        for row in inventory["routes"]
        if row["error_test_location"] and "/" in row["error_test_location"]
    }
    assert located, (
        "no route names a real error-state test any more; the requirement Gate 3 "
        "gave up has not landed anywhere else"
    )
    existing = [location for location in located if (PROJECT / location).exists()]
    assert len(existing) >= 10, (
        f"only {len(existing)} error-state test locations exist; the relocated "
        "requirement is being deleted rather than moved"
    )


def test_the_mocked_suites_the_relocation_depends_on_still_exist() -> None:
    """`contract-workflows.spec.ts` is named by the relocation and mocks the API.

    Gate 3 excludes it from staging runs on purpose. That exclusion is only
    acceptable while the suite still runs somewhere, which is what this asserts.
    """
    for spec in ("client/e2e/contract-workflows.spec.ts", "client/e2e/contract-master.spec.ts"):
        assert (PROJECT / spec).is_file(), f"{spec} is gone; it carries relocated error-state coverage"

    config = (PROJECT / "client" / "playwright.config.ts").read_text(encoding="utf-8")
    assert "testIgnore" in config, (
        "the mocked suites are no longer excluded from deployment runs, so they "
        "could be offered as Gate 3 staging evidence"
    )


def test_the_matrix_records_the_narrowing() -> None:
    """The evidence matrix and the gate document must tell the same story."""
    text = MATRIX.read_text(encoding="utf-8")
    assert "GATE_3_ROUTE_INVENTORY.md" in text, (
        "the Gate 3 evidence matrix does not reference the route inventory that "
        "defines bullet 8's denominator"
    )
    assert "AMENDED in R-A8S" in text, (
        "the matrix no longer records that bullet 8 was amended, so the row and "
        "the gate document's supersession block can drift apart"
    )
    assert "corrected in R-A8S" in text, (
        "the matrix no longer records that bullet 7's MISSING row was wrong; the "
        "correction is the reason the bullet stayed in the scored gate"
    )
