"""Guards that the CI static/security gates actually execute.

Two defects motivated this module, both of which CI reported as a *green* or a
*fast* job rather than as a missing check:

- The `backend-checks` job ran `pre-commit run --all-files` from the checkout
  root, where no `.pre-commit-config.yaml` existed (the file lived under
  `projectDMS/`). Every run died with `InvalidConfigError` before a single hook
  executed.
- Every path-scoped hook was anchored at `^backend/...`, the layout from when
  `projectDMS` was its own repository. pre-commit hands hooks paths relative to
  the git root, which here are `projectDMS/backend/...`, so ruff, ruff-format,
  mypy and forbid-print each matched **zero** files and reported
  `(no files to check) Skipped`.

A static or security hook that matches nothing still passes. These tests fail
instead.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

# parents[3] is projectDMS; the git repository root is one level above it and
# holds both `.github/` and `projectDMS/`.
GIT_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = GIT_ROOT / ".github" / "workflows" / "ci.yml"

# Hooks whose whole purpose is to inspect backend source. Each must select at
# least one file, and only files under the tree it is meant to police.
BACKEND_SCOPED_HOOKS = ("ruff", "ruff-format", "mypy", "forbid-print")
BACKEND_SCOPE_PREFIX = "projectDMS/backend/rbac_backend/"

# Repository-wide hook: python/ts/tsx everywhere, backend included.
REPO_WIDE_HOOKS = ("forbid-legacy-authz",)

# Deny-list hook. Zero matches is its *pass* condition, so it cannot be proven
# live by counting files - it is proven against constructed paths instead.
DENY_LIST_HOOK = "forbid-dead-code-paths"
DENY_LIST_SHOULD_MATCH = (
    "projectDMS/backend/rbac_backend/archive/old_router.py",
    "projectDMS/client/src/pages/Dashboard1.tsx",
    "projectDMS/backend/rbac_backend/main_minimal.py",
)

# Trees a backend hook must never be broadened into.
FORBIDDEN_SCOPE_SUBSTRINGS = ("/node_modules/", "projectDMS/client/", "projectDMS/docs/")


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=GIT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _backend_checks_steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["backend-checks"]["steps"]


def _step_named(name: str) -> dict:
    for step in _backend_checks_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"backend-checks has no step named {name!r}")


def _configured_precommit_config() -> Path:
    """The config path the CI pre-commit command will actually resolve."""
    step = _step_named("Run pre-commit")
    command = str(step["run"])
    base = GIT_ROOT / str(step.get("working-directory", "."))
    match = re.search(r"--config[= ]+(\S+)", command)
    return (base / match.group(1)).resolve() if match else (base / ".pre-commit-config.yaml").resolve()


def _hook_patterns() -> dict[str, str]:
    config = yaml.safe_load(_configured_precommit_config().read_text(encoding="utf-8"))
    return {
        hook["id"]: hook.get("files", "")
        for repo in config["repos"]
        for hook in repo["hooks"]
    }


def test_the_ci_precommit_command_resolves_a_real_config() -> None:
    config = _configured_precommit_config()

    assert config.is_file(), (
        f"the backend-checks pre-commit step resolves {config}, which does not "
        f"exist; pre-commit aborts with InvalidConfigError before any hook runs"
    )


def test_exactly_one_precommit_config_is_tracked() -> None:
    configs = [f for f in _tracked_files() if f.endswith(".pre-commit-config.yaml")]

    assert configs == [".pre-commit-config.yaml"], (
        f"expected a single canonical config at the git root, found {configs}; "
        f"pre-commit only looks at the repository root, and a second copy drifts"
    )


@pytest.mark.parametrize("hook_id", BACKEND_SCOPED_HOOKS + REPO_WIDE_HOOKS)
def test_each_required_hook_selects_at_least_one_file(hook_id: str) -> None:
    pattern = _hook_patterns()[hook_id]
    matched = [f for f in _tracked_files() if re.search(pattern, f)]

    assert matched, (
        f"hook {hook_id!r} matches no tracked file with files={pattern!r}. "
        f"pre-commit reports '(no files to check) Skipped' and the job passes "
        f"without the check ever running"
    )


@pytest.mark.parametrize("hook_id", BACKEND_SCOPED_HOOKS)
def test_backend_hooks_stay_inside_the_backend_tree(hook_id: str) -> None:
    pattern = _hook_patterns()[hook_id]
    matched = [f for f in _tracked_files() if re.search(pattern, f)]

    strayed = [
        f
        for f in matched
        if not f.startswith(BACKEND_SCOPE_PREFIX)
        or any(bad in f for bad in FORBIDDEN_SCOPE_SUBSTRINGS)
    ]
    assert not strayed, (
        f"hook {hook_id!r} reaches outside {BACKEND_SCOPE_PREFIX}: {strayed[:5]}"
    )


def test_the_dead_code_deny_list_still_matches_forbidden_paths() -> None:
    pattern = _hook_patterns()[DENY_LIST_HOOK]

    for path in DENY_LIST_SHOULD_MATCH:
        assert re.search(pattern, path), (
            f"{DENY_LIST_HOOK} no longer matches {path!r}; the deny-list passes "
            f"on every repository, including one that reintroduced the file"
        )
    reintroduced = [f for f in _tracked_files() if re.search(pattern, f)]
    assert not reintroduced, f"archived/duplicate paths are back: {reintroduced[:5]}"


def test_the_backend_test_step_runs_where_its_imports_resolve() -> None:
    step = _step_named("Run backend test suite")

    assert step.get("working-directory") == "projectDMS", (
        "the backend suite must run from projectDMS; 22 test modules import "
        "`backend.rbac_backend...`, which needs projectDMS on sys.path"
    )
    assert "python -m pytest" in str(step["run"]), (
        "invoke pytest as `python -m pytest`: the console script does not put "
        "the working directory on sys.path, so `backend.*` imports fail at "
        "collection"
    )
