"""`npm ci` must install the client on every platform the release builds on.

R-A8I could not run `npm ci` on the staging host: npm 11.7.0 rejected the lockfile
for missing `@rollup/rollup-*` platform packages, and the phase recorded it as an
environment artefact of the host's npm major. It is not. The lockfile was
generated on Windows and carried **only** the two `win32` rollup binaries out of
the twenty-three platform variants rollup declares as optional dependencies, so
on Linux the binary that links the bundle was simply not in it.

The client image looked healthy for one reason: its Dockerfile ran
`npm ci && npm install --no-save @rollup/rollup-linux-x64-gnu`. Every production
client image therefore fetched its native rollup binary **outside the lockfile** -
unpinned, un-integrity-checked, resolved at build time. That is a reproducibility
and supply-chain hole wearing the costume of a convenience flag, and it is the
reason the missing entries went unnoticed for as long as they did.

So the fix is the lockfile, not the npm major: the platform binaries the release
actually builds on are pinned at rollup's exact version, and the Dockerfile
installs the lockfile and nothing else.

**Corrected by R-A8N.** That paragraph used to end "`npm ci` then behaves
identically under npm 10 and npm 11, on Linux and on Windows". R-A8M measured
otherwise, and R-A8N reproduced it in the release images: npm 11 requires *every*
optional platform package a dependency declares to be present in the lockfile,
and rollup 4.60.4 declares twenty-three.

    node:20-bookworm-slim   npm 10.8.2    npm ci  exit 0
    node:24-bookworm-slim   npm 11.19.0   npm ci  EUSAGE, 20 missing @rollup/*

Pinning the two platforms the release builds on satisfies npm 10 and cannot
satisfy npm 11 by construction. The claim was never measured, and this file could
not have caught it: it reads the manifest, by design (see below). Chasing the
lockfile across majors would mean regenerating it once per npm release forever,
so the answer is one supported toolchain, declared in `package.json` and
*enforced* by `client/.npmrc`'s `engine-strict`.
`test_client_toolchain_determinism.py` owns that half.

The check here is on the manifest rather than on a run of `npm ci`, deliberately:
the failure only reproduces on a platform whose binary is missing, so a test that
ran the install would pass on the very machine that generated the incomplete
lockfile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "client"
LOCKFILE = CLIENT / "package-lock.json"
PACKAGE_JSON = CLIENT / "package.json"
DOCKERFILE = CLIENT / "Dockerfile"

#: The platforms the release is actually built on, and where each one comes from.
#: Adding a build platform means adding it here, which is the point: the list is
#: the claim, and the test is whether the lockfile supports the claim.
BUILD_PLATFORMS = {
    "@rollup/rollup-linux-x64-gnu": "the client image base, node:20-bookworm-slim",
    "@rollup/rollup-win32-x64-msvc": "the development host",
}


@pytest.fixture(scope="module")
def lock() -> dict:
    assert LOCKFILE.is_file(), f"client lockfile not found at {LOCKFILE}"
    return json.loads(LOCKFILE.read_text(encoding="utf-8"))


def test_the_lockfile_carries_a_rollup_binary_for_every_build_platform(lock: dict) -> None:
    packages = lock["packages"]
    rollup_version = packages["node_modules/rollup"]["version"]

    missing = {}
    wrong_version = {}
    for name, why in BUILD_PLATFORMS.items():
        entry = packages.get(f"node_modules/{name}")
        if entry is None:
            missing[name] = why
        elif entry.get("version") != rollup_version:
            wrong_version[name] = (entry.get("version"), rollup_version)

    assert not missing, (
        "the lockfile has no rollup binary for platforms the release builds on: "
        f"{missing}. `npm ci` fails there, which is what R-A8I hit and recorded as "
        "an npm-version artefact."
    )
    assert not wrong_version, (
        "a platform binary is pinned at a different version from rollup itself, "
        f"which npm refuses to install: {wrong_version}"
    )


def test_every_build_platform_binary_is_verifiable(lock: dict) -> None:
    """A lockfile entry with no integrity hash pins a name, not a package.

    Most of this lockfile predates that standard and is carried as debt; what is
    added for a build platform must not extend it, because these entries are the
    native code that links the shipped bundle.
    """
    packages = lock["packages"]

    unverifiable = [
        name
        for name in BUILD_PLATFORMS
        if not packages.get(f"node_modules/{name}", {}).get("integrity")
    ]

    assert not unverifiable, (
        f"build-platform binaries with no integrity hash: {unverifiable}"
    )


def test_the_client_image_installs_the_lockfile_and_nothing_else() -> None:
    """`npm install --no-save <pkg>` inside the image is the hole this closes.

    It resolved the native binary at build time, outside the lockfile, so two
    builds of the same commit could ship different rollup binaries and no artefact
    in the repository would record which.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    offenders = [
        line.strip()
        for line in dockerfile.splitlines()
        if not line.lstrip().startswith("#")
        and re.search(r"npm\s+(install|i|add)\b", line)
    ]

    assert not offenders, (
        "the client image installs packages outside the lockfile, so the shipped "
        f"bundle is not reproducible from the repository alone: {offenders}"
    )
    assert "npm ci" in dockerfile, "the client image no longer installs from the lockfile"


def test_the_node_and_npm_versions_are_pinned_where_ci_can_read_them() -> None:
    """R-A8I ran the gate under npm 11 while the image ships npm 10.

    The fix above does NOT make both work - R-A8N measured that, and the module
    docstring records it. The pin is therefore load-bearing rather than
    decorative: it is the only thing in the repository that says which runtime
    the evidence should be produced under. Whether it is *enforced* is
    `test_client_toolchain_determinism.py`'s question.
    """
    manifest = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    engines = manifest.get("engines") or {}

    assert "node" in engines, "client/package.json declares no supported node range"
    assert "npm" in engines, "client/package.json declares no supported npm range"

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    image = re.search(r"FROM\s+node:(\d+)", dockerfile)
    assert image, "the client image no longer starts from a pinned node major"
    assert image.group(1) in engines["node"], (
        f"the image builds on node {image.group(1)} but package.json declares "
        f"{engines['node']!r}; the declared range must include the one that ships"
    )
