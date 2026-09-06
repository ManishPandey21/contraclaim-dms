"""One definition of "is this the required test file?", for every gate.

Five certification modes — G29, G30, G31, G32-STATE and staging Gate 2 — each
convert a skipped *required* test into a failure, because a required suite that
skips proves nothing and a run that skipped it must not report green. All five
decided membership the same way, and all five were wrong the same way.

Each required set is written repo-relative:

    backend/rbac_backend/tests/integration/test_x.py

and membership was a suffix match against that whole string. The backend image
copies the **contents** of ``backend/`` to ``/app``, so inside the container the
path pytest reports is::

    /app/rbac_backend/tests/integration/test_x.py

which has no ``backend/`` segment anywhere and therefore matched nothing. R-A8I
proved the consequence by execution: 21 required FalkorDB tests skipped, the
conversion never fired, and pytest exited 0 with Gate 2 bullet 4 unmeasured. The
one mechanism built to stop a gate reporting green while measuring nothing was
inert in the only layout that can produce that gate's evidence.

The requirement is a test **identity**, not a checkout layout. So membership is
decided on the path from the ``rbac_backend`` package root inward — the part
that is the same in the repository, in the container, in a worktree and in CI —
and on nothing outside it.

Two rules, each with a tempting weaker version:

* **Anchor on the package, not on a root.** Swapping ``backend/`` for ``/app/``
  fixes one machine and breaks the other. There is no root that is always right;
  there is a package-relative path that always is.
* **Anchor, do not merely contain.** Matching on the basename would let a
  same-named module in another package satisfy a requirement it carries no
  evidence for, and would convert skips in runs the gate does not score.
"""

from __future__ import annotations

from typing import Iterable

#: The package every required test lives inside. The first path segment that is
#: identical in every layout the suite is launched from.
PACKAGE_ANCHOR = "rbac_backend"


def normalise(path: str) -> str:
    """Forward slashes, so a Windows path and a POSIX one compare equal."""
    return str(path).replace("\\", "/")


def canonical_test_path(path: str) -> str:
    """The path from the ``rbac_backend`` package root inward.

    ``/app/rbac_backend/tests/integration/test_x.py`` and
    ``C:/SaaS/projectDMS/backend/rbac_backend/tests/integration/test_x.py`` both
    canonicalise to ``rbac_backend/tests/integration/test_x.py``.

    The **last** occurrence of the anchor is used, not the first: a checkout that
    happens to live in a directory called ``rbac_backend`` would otherwise
    canonicalise to the whole path and match nothing. A path with no anchor at
    all is returned normalised and unchanged, so it can still fail to match
    rather than matching something it is not.
    """
    normalised = normalise(path)
    marker = f"/{PACKAGE_ANCHOR}/"
    index = normalised.rfind(marker)
    if index != -1:
        return normalised[index + 1:]
    if normalised.startswith(f"{PACKAGE_ANCHOR}/"):
        return normalised
    return normalised


def matches_required(path: str, required_files: Iterable[str]) -> bool:
    """Is this test file one of the required set, from any root?

    Both sides are reduced to their package-relative identity first, so the
    comparison is exact rather than a suffix guess: ``tests/test_x.py`` cannot
    satisfy a requirement for ``tests/integration/test_x.py``, and a copy of a
    required module in another package cannot satisfy it by name.
    """
    candidate = canonical_test_path(path)
    return any(candidate == canonical_test_path(required) for required in required_files)
