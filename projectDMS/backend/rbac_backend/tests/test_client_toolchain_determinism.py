"""F-A8M-4 - one supported client toolchain, enforced rather than described.

R-A8M could not run `npm ci` on the staging host. npm 11.7.0 warned
`EBADENGINE` and then failed with

    npm error code EUSAGE
    npm error Missing: @rollup/rollup-android-arm-eabi@4.60.4 from lock file

while the client image built cleanly, because `node:20-bookworm-slim` carries
npm 10. Two npm majors were resolving this project's dependencies two different
ways, and which one produced the shipped bundle was decided by a base image
rather than by anything in this repository.

R-A8N reproduced both halves in the release images themselves:

    node:20-bookworm-slim   npm 10.8.2    npm ci  exit 0
    node:24-bookworm-slim   npm 11.19.0   npm ci  EUSAGE, 20 missing @rollup/*

The cause is not a defective lockfile. npm 11's `npm ci` requires every optional
platform package a dependency declares to be present in the lockfile; rollup
4.60.4 declares twenty-three, and this project pins the two platforms it builds
on. Adding the other twenty-one would satisfy npm 11 today and be wrong again at
the next tool that changes its lockfile expectations -
`test_client_lockfile_platform_completeness.py` already records why the lockfile
carries what it carries, and that reasoning is still right.

So the fix is a supported range that is *enforced*. `engines` is advisory by
default - npm prints EBADENGINE and installs anyway - and `client/.npmrc` sets
`engine-strict=true`, which turns the warning into a refusal. An unsupported npm
now stops at the engine check, naming the engine, instead of at a lockfile error
that invites someone to regenerate the lockfile under the wrong npm.

These are static checks over the four places the toolchain is stated. Running
`npm ci` here would need network and minutes, and would only ever measure the
one npm the test host happens to have - which is precisely how the unmeasured
claim this replaces got into the tree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "client"
PACKAGE_JSON = CLIENT / "package.json"
NPMRC = CLIENT / ".npmrc"
DOCKERFILE = CLIENT / "Dockerfile"
DOCKERIGNORE = CLIENT / ".dockerignore"
WORKFLOW = REPO_ROOT.parent / ".github" / "workflows" / "ci.yml"

#: The npm major the release image carries and the lockfile is valid under.
#: Measured, not chosen: `docker run --rm node:20-bookworm-slim npm -v` -> 10.8.2.
SUPPORTED_NPM_MAJOR = 10
SUPPORTED_NODE_MAJOR = 20


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


# --- 1. The range is stated -------------------------------------------------


def test_the_supported_npm_range_excludes_the_major_that_cannot_install(manifest: dict):
    """`>=10.0.0` admitted npm 11, which cannot install this lockfile. A range
    that includes a major the project does not support is not a statement about
    anything."""
    npm_range = (manifest.get("engines") or {}).get("npm")

    assert npm_range, "client/package.json declares no supported npm range"
    assert f"<{SUPPORTED_NPM_MAJOR + 1}" in npm_range, (
        f"engines.npm is {npm_range!r}, which admits npm "
        f"{SUPPORTED_NPM_MAJOR + 1}; that major refuses this lockfile"
    )
    assert f">={SUPPORTED_NPM_MAJOR}" in npm_range


def test_the_supported_node_range_excludes_the_next_major(manifest: dict):
    node_range = (manifest.get("engines") or {}).get("node")

    assert node_range, "client/package.json declares no supported node range"
    assert f"<{SUPPORTED_NODE_MAJOR + 1}" in node_range, (
        f"engines.node is {node_range!r}; a newer node carries a newer npm, "
        "which is how the majors diverged in the first place"
    )


def test_the_exact_package_manager_is_named(manifest: dict):
    """`packageManager` is what a Corepack-enabled environment reads. It pins an
    exact version rather than a range, so an environment that honours it gets
    the same npm the release image carries and not merely a compatible one."""
    declared = manifest.get("packageManager")

    assert declared, "client/package.json names no packageManager"
    assert declared.startswith(f"npm@{SUPPORTED_NPM_MAJOR}."), (
        f"packageManager is {declared!r}; it must name the npm major the "
        "release image carries"
    )


# --- 2. The range is enforced ----------------------------------------------


def test_engine_strict_is_set_so_the_range_is_a_refusal_not_a_warning():
    """The whole difference between this and the state R-A8M measured.

    Without `engine-strict`, npm prints EBADENGINE and installs anyway, and the
    operator sees a lockfile error instead of an engine error - so the obvious
    next move is to regenerate the lockfile under the wrong npm.
    """
    assert NPMRC.is_file(), "client/.npmrc is missing; engines are advisory without it"
    settings = {}
    for line in NPMRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        settings[key.strip()] = value.strip()

    assert settings.get("engine-strict") == "true", (
        f"client/.npmrc does not set engine-strict=true (got {settings!r})"
    )


def test_the_image_build_sees_the_npmrc_before_it_installs():
    """A `.npmrc` the build context never copies enforces nothing.

    `COPY package*.json ./` does not match `.npmrc`, so without this the image
    would keep installing under whatever npm the base tag happens to carry - the
    exact coupling this finding is about.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in dockerfile.splitlines() if line.strip()]

    copy_index = next(
        (i for i, line in enumerate(lines) if line.startswith("COPY") and ".npmrc" in line),
        None,
    )
    assert copy_index is not None, "the client image never copies client/.npmrc"

    ci_index = next(
        (i for i, line in enumerate(lines) if re.match(r"RUN\s+npm\s+ci\b", line)), None
    )
    assert ci_index is not None, "the client image no longer runs npm ci"
    assert copy_index < ci_index, ".npmrc arrives after npm ci, so it enforces nothing"


def test_the_npmrc_is_not_excluded_from_the_build_context():
    """`.dockerignore` here carries `.env*` and `**/.env*`. A future rule broad
    enough to catch `.npmrc` would silently disable the enforcement above while
    every other check still passed."""
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    import fnmatch

    excluded = [p for p in patterns if fnmatch.fnmatch(".npmrc", p.lstrip("/").replace("**/", ""))]
    assert not excluded, f"client/.dockerignore excludes .npmrc via {excluded}"


def test_the_declared_node_major_is_the_one_the_image_builds_on(manifest: dict):
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    tags = re.findall(r"FROM\s+node:(\d+)", dockerfile)

    assert tags, "the client image no longer starts from a pinned node major"
    assert set(tags) == {str(SUPPORTED_NODE_MAJOR)}, (
        f"the client image builds on node {sorted(set(tags))} but the supported "
        f"major is {SUPPORTED_NODE_MAJOR}; a bump has to come with a measured "
        "lockfile and a new supported range, not on its own"
    )
    assert str(SUPPORTED_NODE_MAJOR) in (manifest["engines"] or {})["node"]


# --- 3. CI produces its evidence under the same toolchain -------------------


@pytest.mark.skipif(not (REPO_ROOT.parent / ".github" / "workflows" / "ci.yml").is_file(),
                    reason="CI workflow lives at the repository root, which is not in this tree")
def test_ci_pins_the_same_node_major_and_records_what_it_ran_under():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    pinned = re.search(r'NODE_VERSION:\s*"?(\d+)"?', workflow)
    assert pinned, "the workflow does not pin a node major"
    assert pinned.group(1) == str(SUPPORTED_NODE_MAJOR), (
        f"CI runs node {pinned.group(1)} while the release image and "
        f"package.json say {SUPPORTED_NODE_MAJOR}"
    )

    # Every job that installs the client must state the toolchain it installed
    # under, so the evidence a release phase reads names its own npm.
    assert workflow.count("npm --version") >= 2, (
        "a client job installs without recording the npm it used"
    )

    # And every job that installs must assert the lockfile survived it. A
    # rewritten lockfile is the visible symptom of an npm that resolved a
    # different tree, and leaving it to a release phase to notice by hand is how
    # F-A8M-4 stayed open for two phases.
    assert workflow.count("git diff --exit-code -- package-lock.json") >= 2, (
        "a client job runs npm ci without checking that the lockfile is unchanged"
    )
