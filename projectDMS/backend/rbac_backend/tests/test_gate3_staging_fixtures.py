"""The Gate 3 staging fixtures must be run-owned, cleanable and tenant-safe.

Bullets 2, 3 and 5 need seeded state, and seeded state on a **same-host** staging
stack is the most dangerous artefact this programme produces: the thing next door
is production's data. The evidence matrix says as much for bullet 3 - "plus a
teardown that removes it - the drill must not accumulate staging documents".

Four properties are claimed in `client/e2e/staging/fixtures.ts`, and a claim in a
docstring is worth what the last person to edit the file remembered:

* **run-owned** - every created object carries `RUN_TAG`, and nothing without it
  is ever deleted;
* **idempotent** - creating twice with the same tag returns the existing object;
* **deterministically cleanable** - teardown enumerates by tag, deletes by id and
  reports what it could not remove;
* **tenant-safe** - every write names `E2E_STAGING_ORG_ID`, and the module
  refuses a target listed in `E2E_PRODUCTION_HOSTS`.

These are structural assertions over the source, not executions. That is the
honest limit of an offline phase: they establish that the rules are written into
the harness, not that the harness has run. Nothing here ticks a bullet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING_E2E = REPO_ROOT / "client" / "e2e" / "staging"
FIXTURES = STAGING_E2E / "fixtures.ts"

#: Specs that consume the fixtures. Named rather than globbed: a new staging spec
#: should be added here deliberately, not slip past because the glob missed it.
FIXTURE_SPECS = (
    STAGING_E2E / "org-admin-permissions.spec.ts",
    STAGING_E2E / "document-lifecycle.spec.ts",
)

#: Every function in the fixture module that creates persistent state.
CREATORS = ("ensureDisposableRole", "ensureDisposableDocument", "setRolePermissions")


@pytest.fixture(scope="module")
def source() -> str:
    assert FIXTURES.is_file(), f"the Gate 3 fixture harness is missing at {FIXTURES}"
    return FIXTURES.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The text of one exported function, from its signature to the next one.

    Crude on purpose: a TypeScript parser would be a dependency, and the property
    being asserted is "this call appears inside this function", which does not
    need one.
    """
    match = re.search(rf"export (?:async )?function {re.escape(name)}\b", source)
    assert match, f"{name} is gone from the fixture harness"
    rest = source[match.start() + 1 :]
    following = re.search(r"^export (?:async )?function ", rest, re.M)
    return rest[: following.start()] if following else rest


# --------------------------------------------------------------------------- #
# Run-owned
# --------------------------------------------------------------------------- #


def test_the_run_tag_is_derived_from_the_run_id(source: str) -> None:
    """The tag must be an input, because the run that needs cleaning is the one
    that died before its teardown ran."""
    assert 'process.env.E2E_RUN_ID' in source, (
        "the run id cannot be supplied, so a crashed run's objects cannot be "
        "identified afterwards"
    )
    assert re.search(r"RUN_TAG\s*=\s*`g3-\$\{RUN_ID\}`", source), (
        "the run tag is no longer derived from the run id"
    )


@pytest.mark.parametrize("creator", CREATORS)
def test_every_creator_refuses_an_untagged_or_foreign_write(source: str, creator: str) -> None:
    body = _function_body(source, creator)
    assert "assertWriteIsTenantSafe" in body, (
        f"{creator} creates state without passing through the tenant-safety check, so "
        "it can write into another organisation or create an object cleanup cannot find"
    )


def test_the_tenant_check_enforces_both_halves(source: str) -> None:
    body = _function_body(source, "assertWriteIsTenantSafe")
    assert "fixtureOrganizationId()" in body, "the write is no longer bound to one organisation"
    assert "isRunOwned" in body, "an object may now be created without the run tag"
    assert "assertTargetIsNotProduction" in body, (
        "the tenant check no longer re-checks the target, so a suite whose "
        "E2E_BASE_URL changed after import could write to production"
    )


# --------------------------------------------------------------------------- #
# Tenant-safe
# --------------------------------------------------------------------------- #


def test_the_harness_refuses_a_production_target(source: str) -> None:
    body = _function_body(source, "assertTargetIsNotProduction")
    assert "E2E_PRODUCTION_HOSTS" in source
    assert "throw new Error" in body, (
        "a production target no longer raises; a refusal that returns quietly is "
        "a refusal that does not happen"
    )


def test_the_organisation_is_never_inherited_from_the_session(source: str) -> None:
    """A fixture that inherits the caller's active scope writes wherever the
    caller happened to be - and for a global role that is the navbar selection."""
    body = _function_body(source, "fixtureOrganizationId")
    assert "E2E_STAGING_ORG_ID" in body
    assert "throw new Error" in body, "an absent organisation id no longer stops the run"


# --------------------------------------------------------------------------- #
# Idempotent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "creator, finder",
    [
        ("ensureDisposableRole", "findRoleByName"),
        ("ensureDisposableDocument", "findDocumentByLetterNo"),
    ],
)
def test_creation_is_idempotent(source: str, creator: str, finder: str) -> None:
    body = _function_body(source, creator)
    assert finder in body, (
        f"{creator} no longer looks for an existing object first, so a re-run after "
        "a crash either duplicates state or fails on a uniqueness constraint"
    )
    assert "return existing" in body, f"{creator} finds an existing object and creates anyway"


# --------------------------------------------------------------------------- #
# Deterministically cleanable
# --------------------------------------------------------------------------- #


def test_cleanup_only_deletes_run_owned_objects(source: str) -> None:
    body = _function_body(source, "cleanupRunOwned")
    assert body.count("isRunOwned") >= 2, (
        "cleanup deletes without checking ownership on every collection it walks. A "
        "teardown that decides membership by shape eventually deletes something that "
        "only looked like a fixture"
    )
    assert "RUN_TAG" in body, "cleanup no longer enumerates by run tag"


def test_cleanup_reports_what_it_could_not_remove(source: str) -> None:
    body = _function_body(source, "cleanupRunOwned")
    assert "report.failed" in body, (
        "cleanup no longer records failures, so a teardown that leaves state behind "
        "reports the same result as one that did not"
    )
    assert "404" in body, (
        "cleanup no longer treats an already-absent object as removed, so a second "
        "invocation fails on the objects the first one deleted"
    )


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=lambda path: path.name)
def test_every_fixture_spec_tears_itself_down(spec: Path) -> None:
    assert spec.is_file(), f"missing Gate 3 spec: {spec}"
    text = spec.read_text(encoding="utf-8")
    assert "cleanupRunOwned" in text, f"{spec.name} seeds state and never removes it"
    assert "afterAll" in text, f"{spec.name} does not tear down"
    assert "report.failed" in text, (
        f"{spec.name} calls cleanup and ignores whether it worked"
    )


# --------------------------------------------------------------------------- #
# The specs are deployment evidence, not component evidence
# --------------------------------------------------------------------------- #


def test_a_standalone_request_context_carries_the_target(source: str) -> None:
    """`playwright.request.newContext()` inherits NOTHING from playwright.config.ts.

    Not `baseURL`, not `ignoreHTTPSErrors`. A teardown context built without them
    turns every relative path in the harness into an invalid URL, and a staging
    TLS terminator with a self-signed certificate into a connection error - so
    the teardown fails for a reason that has nothing to do with whether the state
    was removed, and the run reports state left behind that was never there.
    """
    body = _function_body(source, "newFixtureContext")
    assert "baseURL" in body, "the standalone context has no baseURL"
    assert "ignoreHTTPSErrors" in body, (
        "the standalone context does not accept the staging terminator's certificate"
    )
    assert "assertTargetIsNotProduction" in body, (
        "a context can now be opened against production without the target check"
    )


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=lambda path: path.name)
def test_no_spec_builds_its_own_bare_request_context(spec: Path) -> None:
    """One place decides how a standalone context is built, or two will disagree."""
    text = spec.read_text(encoding="utf-8")
    assert "playwright.request.newContext(" not in text, (
        f"{spec.name} builds its own request context instead of calling "
        "newFixtureContext(); a bare context has no baseURL and no TLS exemption"
    )
    if "newContext" in text or "playwright" in text:
        assert "newFixtureContext" in text, (
            f"{spec.name} takes the playwright worker fixture without using the shared "
            "context helper"
        )


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=lambda path: path.name)
def test_no_fixture_spec_mocks_the_api(spec: Path) -> None:
    """The whole reason R-A8I's 33 green tests earned no checkbox."""
    text = spec.read_text(encoding="utf-8")
    for forbidden in ("page.route(", "context.route(", "**/api/**"):
        assert forbidden not in text, (
            f"{spec.name} intercepts the network; a mocked suite proves the component "
            "renders its own states and nothing about a deployment"
        )


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=lambda path: path.name)
def test_every_fixture_spec_is_gated_on_its_staging_environment(spec: Path) -> None:
    """Under `CONTRACLAIM_STAGING_E2E` a missing variable must fail, not skip."""
    text = spec.read_text(encoding="utf-8")
    assert "requireStagingEnvironment" in text, (
        f"{spec.name} runs without checking it has a staging target, so it can report "
        "green having measured a dev server"
    )


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=lambda path: path.name)
def test_no_fixture_spec_is_excluded_when_targeting_a_deployment(spec: Path) -> None:
    """`playwright.config.ts` ignores the mocked suites by name. A staging spec
    that matched one of those patterns would silently never run."""
    config = (REPO_ROOT / "client" / "playwright.config.ts").read_text(encoding="utf-8")
    ignored = re.findall(r'"\*\*/([^"]+)"', config)
    assert spec.name not in ignored, f"{spec.name} is excluded from deployment runs"


# --------------------------------------------------------------------------- #
# Bullet 2 measures the page the product renders, reached the way a user does
# --------------------------------------------------------------------------- #

ORG_ADMIN_SPEC = STAGING_E2E / "org-admin-permissions.spec.ts"


def _bullet2_browser_test(text: str) -> str:
    start = text.index('test("the Client DMS group is offered')
    end = text.index("\n});", start)
    return text[start:end]


def test_bullet2_selects_the_permissions_tab_before_asserting_the_group() -> None:
    """F-A8W-B4, measured in R-A8W Stage B run 2.

    `PermissionsPage` opens on `<Tabs defaultValue="roles">` and renders the
    permission groups only under the `permissions` tab. An assertion on
    "Client DMS" before the tab is selected measures the default tab.
    """
    text = ORG_ADMIN_SPEC.read_text(encoding="utf-8")
    page_source = (REPO_ROOT / "client" / "src" / "pages" / "PermissionsPage.tsx").read_text(encoding="utf-8")
    assert '<Tabs defaultValue="roles"' in page_source and 'TabsContent value="permissions"' in page_source, (
        "the page no longer hides the groups behind a tab; re-derive this guard from the page"
    )

    body = _bullet2_browser_test(text)
    helper = text[text.index("async function openRoleInPermissionMatrix") :]
    assert 'getByRole("tab", { name: "Permissions", exact: true }).click()' in helper
    select_at = body.index("openRoleInPermissionMatrix(")
    group_at = body.index('name: "Client DMS"')
    assert select_at < group_at, "the spec asserts the Client DMS group before selecting the Permissions tab"


SECURITY_TERMS = STAGING_E2E / "security-terms.ts"
TERMS_MOCKED_SPEC = REPO_ROOT / "client" / "e2e" / "security-terms-acceptance.spec.ts"


def test_bullet2_accepts_the_terms_through_the_page_not_the_api() -> None:
    fixtures = FIXTURES.read_text(encoding="utf-8")
    body = _function_body(fixtures, "openProtectedPage")
    assert "assertTargetIsNotProduction" in body, "the protected page opens without the target check"
    assert "acceptSecurityTermsIfPresented(page, path, ready)" in body, (
        "openProtectedPage no longer goes through the shared terms acceptance"
    )
    helper = _function_body(SECURITY_TERMS.read_text(encoding="utf-8"), "acceptSecurityTermsIfPresented")
    assert "Accept and Continue" in helper and ".check()" in helper, (
        "the terms are no longer accepted through the terms page"
    )
    assert "request.post" not in helper and "security-terms/accept" in helper, (
        "acceptance must be a browser action observed on the wire, not a harness POST"
    )
    text = ORG_ADMIN_SPEC.read_text(encoding="utf-8")
    assert "security-terms/accept" not in text, "the spec posts the acceptance itself"
    assert text.count("openProtectedPage(") >= 3, "a browser test reaches a protected page without the terms step"


def test_the_terms_heading_locator_is_unambiguous_by_semantics_not_order() -> None:
    """F-A8Z-B1, measured on the R-A8Z staging deployment.

    The terms page renders its title as the `h1` AND the active version's title as
    an `h2` inside the scrollable terms; the deployment titles that version with
    the same text. A heading locator by name alone matched both and strict mode
    refused it. The fix is the page's semantic contract (one level-1 title), not
    element order.
    """
    page = (REPO_ROOT / "client" / "src" / "pages" / "SecurityTermsPage.tsx").read_text(encoding="utf-8")
    assert re.search(r"<h1[^>]*>\s*Security, Privacy &amp; Anti-Piracy Terms\s*</h1>", page), (
        "the terms page title is no longer the h1; re-derive the locator from the page"
    )
    assert "<h2" in page and "{active.title}</h2>" in page, (
        "the active version's title is no longer an h2 in the terms region; re-derive this guard"
    )

    helper = _function_body(SECURITY_TERMS.read_text(encoding="utf-8"), "securityTermsHeading")
    assert re.search(r'getByRole\("heading",\s*\{\s*level:\s*1,', helper), (
        "the terms heading locator is not pinned to the level-1 page title"
    )
    for order_dependent in (".first()", ".nth(", ".last()"):
        assert order_dependent not in helper, f"the terms heading locator depends on element order ({order_dependent})"
    assert "getByRole(\"heading\"" not in FIXTURES.read_text(encoding="utf-8").split("export async function openProtectedPage")[1].split("\nexport ")[0], (
        "openProtectedPage builds its own heading locator again instead of using the shared one"
    )


def test_the_terms_locator_has_a_mocked_page_test_excluded_from_deployments() -> None:
    """The locator reached a deployment unexercised once; the mocked suite runs it in CI."""
    spec = TERMS_MOCKED_SPEC.read_text(encoding="utf-8")
    assert 'from "./staging/security-terms"' in spec, "the mocked suite does not exercise the staging helper"
    assert "acceptSecurityTermsIfPresented(" in spec and "securityTermsHeading(page)" in spec
    assert "toHaveCount(2)" in spec, "the duplicate-heading premise is no longer asserted"
    config = (REPO_ROOT / "client" / "playwright.config.ts").read_text(encoding="utf-8")
    assert '"**/security-terms-acceptance.spec.ts"' in config, (
        "the mocked terms suite is not excluded from deployed-stack runs, where it would "
        "assert its own fixtures while reporting a staging URL"
    )


def test_serial_mode_cannot_skip_the_gate4_tests() -> None:
    """R-A8Z: file-level serial mode turned one bullet-2 failure into 8 skipped Gate 4 tests."""
    text = ORG_ADMIN_SPEC.read_text(encoding="utf-8")
    gate3_start = text.index('test.describe("Gate 3 bullet 2')
    gate4_start = text.index('test.describe("Gate 4 bullet 8')
    configures = [m.start() for m in re.finditer(r"test\.describe\.configure\(", text)]
    assert configures, "the shared-role bullet-2 tests are no longer serial"
    for position in configures:
        assert gate3_start < position < gate4_start, (
            "describe.configure is applied outside the Gate 3 bullet 2 block, so a bullet-2 "
            "failure can skip Gate 4 tests"
        )


def test_a_deployed_stack_run_is_one_worker_with_no_retries() -> None:
    """R-A9A review: both describe blocks clean up by run tag, and Playwright splits a
    parallel describe with an afterAll across workers - one block's teardown could
    delete the other's run-owned role mid-test. A retried pass is not evidence."""
    config = (REPO_ROOT / "client" / "playwright.config.ts").read_text(encoding="utf-8")
    assert re.search(r"workers:\s*targetsDeployedStack\s*\?\s*1\s*:", config), (
        "a deployed-stack run is not forced to one worker"
    )
    assert re.search(r"retries:\s*targetsDeployedStack\s*\?\s*0\s*:", config), (
        "a deployed-stack run may retry, so a second-attempt pass could be filed as evidence"
    )


def _gate4_test(text: str, title_start: str) -> str:
    start = text.index(f'test("{title_start}')
    return text[start : text.index("\n  });", start)]


@pytest.mark.parametrize(
    "title",
    [
        "a foreign organisation addressed directly is refused",
        "a foreign user and a foreign role addressed directly are refused",
        "the org admin cannot move itself into a foreign organisation",
    ],
)
def test_direct_id_refusals_require_exactly_403(title: str) -> None:
    """R-A9A review: users, roles and projects answer 404 for an id that does not exist,
    so accepting 404 passes on a wrong or stale fixture id while measuring nothing."""
    body = _gate4_test(ORG_ADMIN_SPEC.read_text(encoding="utf-8"), title)
    assert "REFUSALS" not in body, f"{title!r} still accepts 404 as a refusal"
    assert ".toBe(403)" in body, f"{title!r} does not require exactly 403"


def test_the_foreign_project_by_id_requires_exactly_403() -> None:
    body = _gate4_test(ORG_ADMIN_SPEC.read_text(encoding="utf-8"), "a foreign project, or a foreign-organisation filter")
    direct = body[body.index("const direct") : body.index("const filtered")]
    assert "REFUSALS" not in direct and ".toBe(403)" in direct


def test_the_foreign_fixtures_cannot_be_the_fixture_tenant() -> None:
    text = ORG_ADMIN_SPEC.read_text(encoding="utf-8")
    helper = text[text.index("function requireForeignTenant") :]
    helper = helper[: helper.index("\n}\n")]
    assert "not.toBe(env.E2E_STAGING_ORG_ID)" in helper
    gate4 = text[text.index('test.describe("Gate 4 bullet 8') :]
    assert "requireStagingEnvironment(" not in gate4, "a Gate 4 test skips the foreign-tenant check"


def test_the_admin_page_walk_measures_each_page_on_its_own() -> None:
    """R-A9A review: the navbar lists organisations and projects on every mount, and
    ProtectedRoute renders the access-denied card at the same URL."""
    body = _gate4_test(ORG_ADMIN_SPEC.read_text(encoding="utf-8"), "navigating the admin pages")
    loop = body[body.index("for (const [path, api, title] of pages)") :]
    assert loop.index("observed = new Set<string>()") < loop.index("page.goto(path)"), (
        "API calls are not reset per page, so the navbar's calls satisfy every page"
    )
    assert 'getByRole("heading", { level: 1, name: title })' in loop
    assert '"Access unavailable"' in loop
    assert body.index("await Promise.allSettled(parsing)") < body.index('expect(leaks'), (
        "response bodies may still be parsing when the leak assertion runs"
    )


def test_gate4_row12_measures_the_refusal_behind_a_passing_control() -> None:
    """F-A8Z-B2: a re-parent 403 means nothing if the same account is refused its own project."""
    text = ORG_ADMIN_SPEC.read_text(encoding="utf-8")
    start = text.index('test("the org admin can update its own project')
    body = text[start : text.index("\n  });", start)]
    read_control = body.index("CONTROL: the org admin was refused a read")
    write_control = body.index("CONTROL: an unchanged update")
    move = body.index("organization_id: env.E2E_FOREIGN_ORG_ID")
    read_back = body.index("now belongs to another organisation")
    assert read_control < write_control < move < read_back, (
        "row 12 must read and update its own project successfully BEFORE the foreign re-parent, "
        "and read the project back after it"
    )
    assert ".toBe(200)" in body[write_control - 200 : move] and ").toBe(\n      403\n    )" in body, (
        "row 12's control must require 200 and its re-parent exactly 403"
    )


def test_bullet2_saves_in_the_browser_and_reads_the_server_back() -> None:
    body = _bullet2_browser_test(ORG_ADMIN_SPEC.read_text(encoding="utf-8"))
    for affordance in ('"Save Changes"', '"Verify"', "getByRole(\"dialog\")", "toBeChecked()", "page.reload()"):
        assert affordance in body, f"the browser leg no longer uses {affordance}"
    assert "readRolePermissions(session, role)" in body, "the browser save is not read back from the server"
    save_at = body.index('"Save Changes"')
    assert "setRolePermissions(session, role, [CLIENT_DMS_PERMISSION])" not in body[save_at:], (
        "the grant is written through the API after the browser step, so the browser proves nothing"
    )


# --------------------------------------------------------------------------- #
# The refusal semantics the authorisation bullet turns on
# --------------------------------------------------------------------------- #


def test_the_authorization_denial_never_accepts_a_401() -> None:
    """A scope refusal that arrives as 401 logs the user out of their own session.

    The client turns 401 into a forced logout and 403 into a toast, so accepting
    401 here would certify the masked-status defect as correct behaviour.
    """
    text = (STAGING_E2E / "document-lifecycle.spec.ts").read_text(encoding="utf-8")
    match = re.search(r"const REFUSALS = \[([^\]]*)\]", text)
    assert match, "the refusal set is gone from the document lifecycle spec"
    statuses = {code.strip() for code in match.group(1).split(",") if code.strip()}
    assert statuses == {"403", "404"}, (
        f"the accepted refusal statuses are {sorted(statuses)}; 401 from a scoped "
        "endpoint is a masked 403 and must fail this bullet, and 200 is a leak"
    )


def test_the_denial_covers_the_listing_as_well_as_the_detail_route() -> None:
    """A 403 on the detail route means nothing if the row is in the outsider's list.

    `build_scope_query` answers row visibility; the gate answers membership. Both
    halves have to be measured or the bullet only covers one of them.
    """
    text = (STAGING_E2E / "document-lifecycle.spec.ts").read_text(encoding="utf-8")
    assert "not.toContain(document.letterNo)" in text, (
        "the out-of-scope listing is not asserted, so a document refused by id could "
        "still be enumerated"
    )
