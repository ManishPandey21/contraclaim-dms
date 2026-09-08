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
