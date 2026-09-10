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

import importlib.util
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


def _job(name: str) -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert name in workflow["jobs"], f"{WORKFLOW.name} has no job named {name!r}"
    return workflow["jobs"][name]


def _backend_checks_steps() -> list[dict]:
    return _job("backend-checks")["steps"]


def _step_using(job: str, action: str) -> dict:
    """Fail with a sentence, not a bare StopIteration, when a step disappears."""
    for step in _job(job)["steps"]:
        if str(step.get("uses", "")).startswith(action):
            return step
    raise AssertionError(f"job {job!r} has no step using {action!r}")


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


def test_only_the_root_workflow_directory_is_canonical() -> None:
    """GitHub reads workflows from the repository root and nowhere else.

    A copy under `projectDMS/.github/workflows/` looks like CI, accepts edits,
    and runs never. One lived there for the whole life of this branch, still
    anchored at the pre-move `backend/...` paths.
    """
    workflows = [
        f
        for f in _tracked_files()
        if "/.github/workflows/" in f or f.startswith(".github/workflows/")
    ]
    misplaced = [f for f in workflows if not f.startswith(".github/workflows/")]

    assert not misplaced, (
        f"workflow files outside the repository root are never executed by "
        f"GitHub, and drift from the ones that are: {misplaced}"
    )
    assert workflows, "no workflow found at .github/workflows/"


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


# --- the staged mypy baseline ------------------------------------------------

MYPY_GATE = GIT_ROOT / "projectDMS" / "scripts" / "mypy_staged_gate.py"
MYPY_BASELINE = GIT_ROOT / "projectDMS" / "backend" / "mypy-baseline.txt"


def _baseline_entries() -> list[tuple[int, str, str, str]]:
    entries = []
    for number, line in enumerate(MYPY_BASELINE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) == 4, (
            f"{MYPY_BASELINE.name}:{number}: expected four tab-separated fields "
            f"(count, path, code, message), got {len(parts)}: {line!r}"
        )
        count, path, code, message = parts
        assert count.isdigit() and int(count) >= 1, (
            f"{MYPY_BASELINE.name}:{number}: count must be a positive integer, got {count!r}"
        )
        assert path and code and message, f"{MYPY_BASELINE.name}:{number}: empty field"
        entries.append((int(count), path, code, message))
    return entries


def test_the_mypy_hook_still_invokes_mypy() -> None:
    """A baseline gate is only honest if it actually type-checks.

    The hook was swapped from mirrors-mypy to a local gate so the historical
    backlog could be accepted without disabling error codes. The swap is
    worthless - worse than worthless - if the gate stops running mypy.
    """
    hook = next(
        h
        for repo in yaml.safe_load(_configured_precommit_config().read_text(encoding="utf-8"))["repos"]
        for h in repo["hooks"]
        if h["id"] == "mypy"
    )

    assert "mypy_staged_gate.py" in hook["entry"], hook["entry"]
    assert MYPY_GATE.is_file(), f"{MYPY_GATE} is missing"

    source = MYPY_GATE.read_text(encoding="utf-8")
    assert '"-m", "mypy"' in source, "the gate no longer runs mypy"
    assert "--ignore-missing-imports" in source and "--scripts-are-modules" in source, (
        "the gate must pass the flags mirrors-mypy passed, or the finding set "
        "becomes a property of the invocation rather than of the code"
    )
    # mypy must be pinned in the hook's own environment, not inherited.
    assert any(str(dep).startswith("mypy==") for dep in hook["additional_dependencies"])


def test_the_mypy_baseline_is_well_formed_and_not_empty() -> None:
    entries = _baseline_entries()

    assert entries, "an empty baseline means the gate accepts nothing - regenerate it"
    assert all(path.startswith("projectDMS/") for _, path, _, _ in entries), (
        "baseline paths must be repository-relative, or they will not match at runtime"
    )


def test_the_mypy_baseline_holds_no_release_only_scope_escape() -> None:
    """The baseline may only ever grow by a reviewed regeneration.

    It cannot be checked against the merge-base from inside a unit test, so what
    is checked here is the property that made that comparison meaningful: the
    file is machine-generated in a fixed shape, records its provenance commit,
    and nobody has hand-appended an entry in a different format.
    """
    header = MYPY_BASELINE.read_text(encoding="utf-8").split("\n\n", 1)[0]

    assert "MACHINE-GENERATED" in header
    assert "Provenance" in header and "merge-base" in header
    assert "mypy_staged_gate.py --regenerate" in header

# --- the secret scan ----------------------------------------------------------

GITLEAKS_CONFIG_PATH = GIT_ROOT / ".gitleaks.toml"

#: Synthetic, non-live credentials. No allowlist regex may match any of these -
#: neither as a bare value nor as the line that carries one. This is the check
#: that stops an exemption like `^.*$`, or one keyed on a suffix a real secret
#: can also have, from passing an "is it anchored?" test while silencing the
#: scanner.
CANARY_SECRETS = (
    "AKIA2E0A8F3B7C1D9K4M",
    "kQ7dR2xN9pL4vT6yB8wC1zA3sE5gH0jM2nP4rU6t",
    "9f3c1a7e4b25d806c1e93a7fb52d40e8",
    "9f3c1a7e4b25d806c1e93a7fb52d40e8.docx",
    '  qdrant_api_key_doc = "9f3c1a7e4b25d806c1e93a7fb52d40e8.docx"',
    '  api_key = "Zx9Qr7Tv2Lp4Hn8Kd3Ms6Wb1Yc5Ff0Gj"',
    "-----BEGIN RSA PRIVATE KEY-----",
)


def _load_gitleaks_settings() -> dict:
    # tomllib is stdlib from 3.11; CI runs 3.12, the local venv is 3.10 and has
    # tomli, which exposes the same loads(). Never skip - a skipped guard is the
    # failure mode this module exists to prevent.
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
        import tomli as tomllib  # type: ignore[no-redef]

    return tomllib.loads(GITLEAKS_CONFIG_PATH.read_text(encoding="utf-8"))


def _allowlists() -> list[dict]:
    """Every allowlist gitleaks will honour, however it is spelled.

    `[allowlist]` singular is a separate and equally powerful key from
    `[[allowlists]]`, and rules carry their own. A guard that reads only the
    plural top-level form can be walked straight around.
    """
    settings = _load_gitleaks_settings()
    found = list(settings.get("allowlists", []))
    if "allowlist" in settings:
        found.append(settings["allowlist"])
    for rule in settings.get("rules", []):
        found.extend(rule.get("allowlists", []))
        if "allowlist" in rule:
            found.append(rule["allowlist"])
    return found


def test_exactly_one_gitleaks_config_is_tracked_and_it_is_at_the_root() -> None:
    """gitleaks-action passes no --config; gitleaks reads the scan root.

    A copy under `projectDMS/` would be invisible to it, which is how the
    pre-commit config went the life of this branch without running a hook.
    """
    configs = [f for f in _tracked_files() if f.endswith(".gitleaks.toml")]

    assert configs == [".gitleaks.toml"], (
        f"expected a single config at the repository root, found {configs}; "
        f"gitleaks loads the one at the root of the checkout it scans"
    )
    assert GITLEAKS_CONFIG_PATH.is_file()


def test_the_secret_scan_job_scans_history_with_the_canonical_config() -> None:
    assert _step_using("secret-scan", "gitleaks/gitleaks-action@")

    checkout = _step_using("secret-scan", "actions/checkout@")
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "gitleaks scans a commit range; a shallow checkout cannot resolve it"
    )

    # Either of these would orphan the root config the test above protects.
    rendered = str(_job("secret-scan"))
    assert "GITLEAKS_CONFIG" not in rendered, (
        "GITLEAKS_CONFIG would point gitleaks at some other file"
    )
    assert "--config" not in rendered, "--config would bypass the root config"


def test_the_gitleaks_config_keeps_every_default_rule() -> None:
    """An allowlist is only defensible on top of the full upstream rule set."""
    settings = _load_gitleaks_settings()
    extend = settings.get("extend", {})

    assert extend.get("useDefault") is True, (
        "the config must extend the default rules, not replace them"
    )
    assert "path" not in extend, (
        "[extend].path swaps in a different base config, so the default rules "
        "are no longer what is running"
    )
    assert not extend.get("disabledRules"), (
        f"disabledRules switches default rules off while useDefault still "
        f"reads true: {extend.get('disabledRules')}"
    )
    assert "rules" not in settings, (
        "defining [[rules]] here replaces upstream detection; this config is "
        "meant to allowlist known non-secrets, not to narrow what is detected"
    )


def test_no_allowlist_exempts_a_whole_path() -> None:
    """`paths` is the trap, not a convenience.

    gitleaks ORs the clauses of one allowlist, so a `paths` entry beside a
    `regexes` entry exempts every finding under that path rather than
    narrowing the exemption to the value. A synthetic token planted in
    `test_arbitration_drafting.py` went unreported until the path clause was
    removed, and `matchCondition = "AND"` did not change it on 8.30.1.
    """
    for allowlist in _allowlists():
        assert "paths" not in allowlist, (
            f"allowlist {allowlist.get('description', '')[:60]!r} declares "
            f"paths; combined with regexes that exempts the whole path"
        )


def test_every_allowlist_names_the_rules_it_relaxes() -> None:
    for allowlist in _allowlists():
        assert allowlist.get("targetRules"), (
            f"allowlist {allowlist.get('description', '')[:60]!r} has no "
            f"targetRules, so it relaxes every rule gitleaks has, including "
            f"ones nobody looked at"
        )


def test_every_allowlist_matches_only_anchored_values() -> None:
    """An unanchored regex exempts anything merely containing the shape."""
    for allowlist in _allowlists():
        for pattern in allowlist.get("regexes", []):
            assert pattern.startswith("^") and pattern.endswith("$"), (
                f"allowlist regex {pattern!r} is not anchored"
            )


@pytest.mark.parametrize("canary", CANARY_SECRETS)
def test_no_allowlist_can_swallow_a_real_secret(canary: str) -> None:
    """The property that "is it anchored?" cannot express.

    `^.*$` is anchored. So was `^[A-Za-z0-9._-]+\\.docx$`, which silenced a
    32-character hex credential written as `<hex>.docx` - reported under the
    default rules, invisible with that exemption in place.
    """
    for allowlist in _allowlists():
        for pattern in allowlist.get("regexes", []):
            assert re.search(pattern, canary) is None, (
                f"allowlist regex {pattern!r} matches the synthetic credential "
                f"{canary[:28]!r}; it would suppress the real thing"
            )


# --- the accepted no-fix dependency advisory -----------------------------------

NOFIX_RECORD = GIT_ROOT / "projectDMS" / "docs" / "NOFIX_ADVISORY_ACCEPTANCE.md"
REACHABILITY_GUARD = (
    GIT_ROOT
    / "projectDMS"
    / "backend"
    / "rbac_backend"
    / "tests"
    / "test_nltk_advisory_reachability.py"
)

#: The one advisory the owner accepted for this release, in the identifier
#: pip-audit prints in its own ID column.
ACCEPTED_ADVISORY = "PYSEC-2026-3740"

#: Flags that would weaken the scan itself rather than exempt one advisory.
#: `--ignore-vuln` is pip-audit's ONLY suppression mechanism, so a wider waiver
#: has to come from one of these instead - and every one of them is real,
#: checked against `pip-audit --help`. An earlier revision of this tuple listed
#: four flags pip-audit does not have, so the test could never fail.
SCAN_WEAKENING_FLAGS = (
    "--dry-run",      # collects dependencies and audits nothing
    "--no-deps",      # skips resolution, and NLTK is transitive
    "--skip-editable",
    "--osv-url",      # a different advisory source
    "--vulnerability-service",
)

#: Ways to make a failing job report success.
FAILURE_SWALLOWING = ("|| true", "|| exit 0", "; exit 0", "continue-on-error")


def _named_step(job: str, name: str) -> dict:
    for step in _job(job)["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"job {job!r} has no step named {name!r}")


def _python_scan_command() -> str:
    return str(_named_step("dependency-scan", "Scan Python dependencies")["run"])


def _ignored_advisories() -> list[str]:
    return re.findall(r"--ignore-vuln[= ]+(\S+)", _python_scan_command())


def test_the_python_scan_still_runs_pip_audit_over_the_release_requirements() -> None:
    command = _python_scan_command()

    assert "pip-audit" in command
    assert "-r projectDMS/backend/rbac_backend/requirements.txt" in command, (
        "the scan must read the requirements file the backend image installs"
    )


def test_exactly_one_advisory_is_ignored_and_it_is_the_accepted_one() -> None:
    """A second id cannot be slipped in beside the first."""
    ignored = _ignored_advisories()

    assert ignored == [ACCEPTED_ADVISORY], (
        f"dependency-scan ignores {ignored}; the owner accepted exactly "
        f"[{ACCEPTED_ADVISORY!r}]. Another advisory needs its own decision "
        f"record, not another flag."
    )


def test_nothing_wider_than_one_advisory_is_ignored() -> None:
    """No package-wide suppression, and no quietly weakened scan."""
    command = _python_scan_command()

    for flag in SCAN_WEAKENING_FLAGS:
        assert flag not in command, (
            f"{flag} weakens the scan itself rather than exempting one advisory"
        )
    assert "nltk" not in command.lower(), (
        "the exception is keyed on an advisory id, never on the package: a "
        "second NLTK advisory must still fail this job"
    )


def test_the_dependency_scan_can_still_fail_the_job() -> None:
    """An exemption is pointless if the job cannot go red anyway."""
    step = _named_step("dependency-scan", "Scan Python dependencies")
    job = _job("dependency-scan")

    assert step.get("continue-on-error") is not True, (
        "continue-on-error makes every advisory advisory-only"
    )
    assert job.get("continue-on-error") is not True, (
        "the whole dependency-scan job is marked continue-on-error"
    )
    for form in FAILURE_SWALLOWING:
        assert form not in str(step["run"]), (
            f"{form!r} in the scan step swallows pip-audit's exit code"
        )


def test_the_accepted_advisory_has_a_written_owner_decision() -> None:
    """An exception with no recorded reasoning is a waiver."""
    assert NOFIX_RECORD.is_file(), f"{NOFIX_RECORD} is missing"
    record = NOFIX_RECORD.read_text(encoding="utf-8")

    assert ACCEPTED_ADVISORY in record, (
        f"{NOFIX_RECORD.name} does not mention {ACCEPTED_ADVISORY}"
    )
    for heading in ("Fix available", "Dependency chain", "Reachability", "Review triggers"):
        assert heading in record, f"{NOFIX_RECORD.name} has no {heading!r} section"
    assert "Accepted for" in record and "release/contraclaim-rc1" in record, (
        "the record must name the release it is accepted for, so it expires"
    )


def test_the_reachability_guard_backing_the_acceptance_exists() -> None:
    """The acceptance rests on this test; it may not quietly disappear."""
    assert REACHABILITY_GUARD.is_file(), f"{REACHABILITY_GUARD} is missing"

    # Read the guard's own list, not the file's text. A substring search over
    # the source passes while a name sits only in a docstring: deleting
    # "save_to_json" from VULNERABLE_APIS went unnoticed that way, because the
    # module docstring still spells out PerceptronTagger.save_to_json.
    spec = importlib.util.spec_from_file_location("_nltk_guard", REACHABILITY_GUARD)
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    # Every component the advisory names, listed here independently on purpose.
    missing = {
        "TransitionParser",
        "AveragedPerceptron",
        "PerceptronTagger",
        "save_to_json",
        "save_maxent_params",
    } - set(guard.VULNERABLE_APIS)
    assert not missing, f"the guard no longer checks for {sorted(missing)}"


# ------------------------------------------------- R-A8U / F-A8T2-9
#
# The disk-reclaim step of `docker-build-and-scan` runs under
# `set -euo pipefail`. Under `set -u` an *unset* variable aborts the step on the
# expansion itself, before the `|| true` that was written to cover exactly this
# can see it. It fails closed - the job goes red rather than green with a full
# disk - so no false green was ever possible, and that is why it is a LOW
# finding rather than a blocker. It still fails for a reason nobody would
# recognise, and the runner image is free to stop exporting the variable.


def _reclaim_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "Reclaim runner disk" in str(step.get("name", "")):
                return step
    raise AssertionError("the disk-reclaim step is gone from the workflow")


def test_the_disk_reclaim_step_still_runs_strictly() -> None:
    """The strictness is deliberate; the fix must not have removed it."""
    script = _reclaim_step()["run"]
    assert "set -euo pipefail" in script, (
        "the disk-reclaim step no longer runs strictly, which is a bigger change "
        "than the one F-A8T2-9 asked for"
    )


def test_every_variable_the_disk_reclaim_step_expands_has_a_default() -> None:
    """No bare `$VAR` may survive in a `set -u` step whose failure mode is a
    cryptic abort. `${VAR:-}` is the fix and `${VAR}` is not."""
    script = _reclaim_step()["run"]
    bare = re.findall(r'\$(?!\{)(?![({])([A-Za-z_][A-Za-z0-9_]*)', script)
    assert not bare, f"these expand without a default under `set -u`: {sorted(set(bare))}"

    braced = re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}', script)
    undefaulted = [name for name, rest in braced if not rest.startswith((":-", ":=", ":?", "-"))]
    assert not undefaulted, (
        f"these expand without a default under `set -u`: {sorted(set(undefaulted))}"
    )
