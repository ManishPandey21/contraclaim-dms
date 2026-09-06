"""The staging Gate-2 preflight, and the hazards it exists to close.

R-A6 found that three live suites default to this development machine's own
containers, and that Gate 2 bullet 5 had no evidence-eligible module at all. Two
different failures with the same shape: a Gate 2 run that reports green while
measuring the wrong thing, or nothing.

These guards are written the way `test_release_gate_specification.py` is: each
rule is exercised against an environment that breaks exactly one condition, so a
validator that stopped checking anything would fail here rather than pass
quietly. Asserting only that a correct environment passes would be satisfied by
a function that returns success unconditionally.

Nothing here needs a live engine, and nothing here reads a real credential.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from rbac_backend.tests import staging_gate
from rbac_backend.tests import authority_band_graph
from rbac_backend.tests.authority_band_graph import falkor_host, falkor_port

TESTS = Path(__file__).resolve().parent
PROJECT = TESTS.parents[2]

#: The variables the preflight reads. Cleared before every case so a value
#: inherited from the developer's own shell cannot make a RED case pass.
MANAGED_ENV = (
    staging_gate.STAGING_GATE_ENV,
    staging_gate.LIVE_TESTS_ENV,
    staging_gate.ENVIRONMENT_ENV,
    "FALKOR_TEST_HOST",
    "FALKOR_TEST_PORT",
    "QDRANT_TEST_URL",
    "QDRANT_API_KEY",
    "QDRANT_TEST_API_KEY",
    "REDIS_TEST_URL",
    "DATABASE_URL",
    "MONGODB_URI",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_BUCKET_NAME",
    "BACKUP_S3_BUCKET",
)

#: A fully explicit staging environment. Every endpoint names a remote host and
#: every credential comes from the environment. No value here is real.
SOUND_STAGING_ENV: Dict[str, str] = {
    staging_gate.STAGING_GATE_ENV: "1",
    staging_gate.LIVE_TESTS_ENV: "1",
    staging_gate.ENVIRONMENT_ENV: "staging",
    "FALKOR_TEST_HOST": "falkordb.contraclaim-stg.internal",
    "FALKOR_TEST_PORT": "6379",
    "QDRANT_TEST_URL": "http://qdrant.contraclaim-stg.internal:6333",
    "QDRANT_API_KEY": "staging-qdrant-key-placeholder",
    "REDIS_TEST_URL": "redis://:staging-redis-placeholder@redis.contraclaim-stg.internal:6379/0",
    "DATABASE_URL": "mongodb://stg:pw@mongo1.contraclaim-stg.internal:27017/contraclaim_staging?replicaSet=rsstg",
}


@pytest.fixture()
def staging_env(monkeypatch: pytest.MonkeyPatch):
    """Install a sound staging environment; the caller breaks one thing."""
    for name in MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in SOUND_STAGING_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.fixture()
def developer_env(monkeypatch: pytest.MonkeyPatch):
    """No staging gate, no live switch - the ordinary developer environment."""
    for name in MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _failed(names: List[staging_gate.PreflightFinding]) -> Dict[str, str]:
    return {f.name: f.status for f in names if f.mandatory and not f.ok}


# --------------------------------------------------------------------------- #
# 6. The anchor: a fully explicit staging environment passes
# --------------------------------------------------------------------------- #


def test_a_fully_explicit_staging_environment_passes(staging_env) -> None:
    """Without this the REDs below could all be passing for the wrong reason."""
    staging_gate.assert_staging_preflight()
    assert _failed(staging_gate.preflight_findings()) == {}


def test_the_report_names_every_mandatory_variable(staging_env) -> None:
    report = staging_gate.render_report()
    for name in (
        staging_gate.STAGING_GATE_ENV,
        staging_gate.LIVE_TESTS_ENV,
        "FALKOR_TEST_HOST",
        "FALKOR_TEST_PORT",
        "QDRANT_TEST_URL",
        "REDIS_TEST_URL",
        "DATABASE_URL",
    ):
        assert name in report


def test_the_report_never_prints_a_secret_value(staging_env) -> None:
    """A preflight that leaks the key into CI output is worse than no preflight."""
    report = staging_gate.render_report()
    for secret in (
        SOUND_STAGING_ENV["QDRANT_API_KEY"],
        "staging-redis-placeholder",
        "pw@mongo1.contraclaim-stg.internal",
    ):
        assert secret not in report
    assert staging_gate.PRESENT in report


# --------------------------------------------------------------------------- #
# 1-5. One RED per hazard, each breaking exactly one condition
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "variable, broken_value, expected_status",
    [
        pytest.param("FALKOR_TEST_HOST", None, staging_gate.ABSENT, id="1-falkor-endpoint-missing"),
        pytest.param("FALKOR_TEST_HOST", "localhost", staging_gate.INVALID_SOURCE, id="2-falkor-localhost"),
        pytest.param("FALKOR_TEST_HOST", "127.0.0.1", staging_gate.INVALID_SOURCE, id="2b-falkor-loopback-ip"),
        pytest.param("FALKOR_TEST_PORT", None, staging_gate.ABSENT, id="1b-falkor-port-missing"),
        pytest.param("QDRANT_TEST_URL", "http://127.0.0.1:6333", staging_gate.INVALID_SOURCE, id="3-qdrant-localhost"),
        pytest.param("QDRANT_TEST_URL", "http://localhost:6333", staging_gate.INVALID_SOURCE, id="3b-qdrant-localhost-name"),
        pytest.param("QDRANT_TEST_URL", None, staging_gate.ABSENT, id="3c-qdrant-missing"),
        pytest.param("REDIS_TEST_URL", None, staging_gate.ABSENT, id="5-redis-endpoint-missing"),
        pytest.param(
            "REDIS_TEST_URL",
            "redis://:pw@127.0.0.1:6379/0",
            staging_gate.INVALID_SOURCE,
            id="5b-redis-loopback-behind-credentials",
        ),
        pytest.param("DATABASE_URL", None, staging_gate.ABSENT, id="mongo-uri-missing"),
        pytest.param(
            "DATABASE_URL",
            "mongodb://localhost:27017/contraclaim",
            staging_gate.INVALID_SOURCE,
            id="mongo-uri-localhost",
        ),
        pytest.param(staging_gate.LIVE_TESTS_ENV, None, staging_gate.ABSENT, id="live-switch-missing"),
        pytest.param(staging_gate.LIVE_TESTS_ENV, "0", staging_gate.INVALID_SOURCE, id="live-switch-disabled"),
        pytest.param(staging_gate.ENVIRONMENT_ENV, "development", staging_gate.INVALID_SOURCE, id="environment-not-staging"),
        pytest.param(staging_gate.ENVIRONMENT_ENV, None, staging_gate.ABSENT, id="environment-unlabelled"),
    ],
)
def test_each_hazard_fails_the_preflight(
    staging_env, variable: str, broken_value, expected_status: str
) -> None:
    if broken_value is None:
        staging_env.delenv(variable, raising=False)
    else:
        staging_env.setenv(variable, broken_value)

    failures = _failed(staging_gate.preflight_findings())
    matching = [name for name in failures if variable in name]
    assert matching, f"breaking {variable} did not fail the preflight: {failures}"
    assert failures[matching[0]] == expected_status

    with pytest.raises(staging_gate.StagingGateConfigurationError) as excinfo:
        staging_gate.assert_staging_preflight()
    assert variable in str(excinfo.value)


def test_a_local_mongo_uri_is_not_masked_by_its_sibling_variable(staging_env) -> None:
    """The stack reads both names; a passing one must not cover a failing one.

    The first version resolved `DATABASE_URL` and, on failure, fell through to
    `MONGODB_URI` - so `DATABASE_URL=mongodb://localhost:27017` was *overwritten*
    by a passing sibling and the preflight went green while the variable the
    stack actually uses pointed at this machine. That is the precise hazard
    class the whole gate exists to close, reintroduced by a convenience.
    """
    staging_env.setenv("DATABASE_URL", "mongodb://localhost:27017/contraclaim")
    staging_env.setenv(
        "MONGODB_URI",
        "mongodb://stg:pw@mongo1.contraclaim-stg.internal:27017/contraclaim_staging",
    )

    failures = _failed(staging_gate.preflight_findings())
    assert failures.get("DATABASE_URL") == staging_gate.INVALID_SOURCE, (
        "a localhost DATABASE_URL was masked by a remote MONGODB_URI: " + repr(failures)
    )
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        staging_gate.assert_staging_preflight()

    # And the other way round, so neither name is the privileged one.
    staging_env.setenv(
        "DATABASE_URL",
        "mongodb://stg:pw@mongo1.contraclaim-stg.internal:27017/contraclaim_staging",
    )
    staging_env.setenv("MONGODB_URI", "mongodb://127.0.0.1:27017/contraclaim")
    assert _failed(staging_gate.preflight_findings()).get("MONGODB_URI") == staging_gate.INVALID_SOURCE


def test_either_mongo_variable_alone_satisfies_the_preflight(staging_env) -> None:
    """Or the rule above would just ban using MONGODB_URI."""
    staging_env.delenv("DATABASE_URL", raising=False)
    staging_env.setenv(
        "MONGODB_URI",
        "mongodb://stg:pw@mongo1.contraclaim-stg.internal:27017/contraclaim_staging",
    )
    staging_gate.assert_staging_preflight()


def test_openai_and_aws_are_enforced_by_the_test_that_selects_them(developer_env) -> None:
    """Reported `if selected`, and enforced where selection is actually known.

    The preflight cannot know which live tests a run selected, so it reports
    these rather than gating on them. That is only honest if something else
    makes them mandatory when they ARE needed - which is
    `require_live_environment` failing, not skipping, under the gate.
    """
    findings = {f.name: f for f in staging_gate.preflight_findings()}
    for name in ("OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_BUCKET_NAME", "BACKUP_S3_BUCKET"):
        assert findings[name].mandatory is False

    developer_env.setenv(staging_gate.STAGING_GATE_ENV, "1")
    developer_env.setenv(staging_gate.LIVE_TESTS_ENV, "1")
    developer_env.delenv("OPENAI_API_KEY", raising=False)
    failures: List[str] = []
    staging_gate.require_live_environment(
        "OPENAI_API_KEY",
        skip=lambda message: pytest.fail(f"a selected OpenAI live test skipped: {message}"),
        fail=failures.append,
    )
    assert failures and "OPENAI_API_KEY" in failures[0]


def test_a_missing_qdrant_key_with_no_checkout_fallback_is_absent(
    staging_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABSENT, not INVALID SOURCE - the two are different failures.

    Parametrised with the others this case would be environment-dependent: the
    release worktree really does carry `config/secrets/qdrant_api_key`, so the
    same missing variable reports INVALID SOURCE there and ABSENT in CI. The
    fallback set is pinned here so the distinction is asserted, not observed.
    """
    monkeypatch.setattr(staging_gate, "qdrant_checkout_secret_candidates", lambda: ())
    staging_env.delenv("QDRANT_API_KEY", raising=False)
    staging_env.delenv("QDRANT_TEST_API_KEY", raising=False)

    failures = _failed(staging_gate.preflight_findings())
    key_findings = [name for name in failures if "QDRANT_API_KEY" in name]
    assert key_findings, failures
    assert failures[key_findings[0]] == staging_gate.ABSENT
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        staging_gate.assert_staging_preflight()


def test_4_a_checkout_local_secret_is_reported_as_an_invalid_source(
    staging_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact R-A6 hazard: the key absent from the environment, present on disk.

    Reported as INVALID SOURCE rather than ABSENT because the distinction is the
    whole point - an ABSENT key fails loudly, while a key sitting in the
    checkout gets picked up and the run authenticates with the DEV credential.
    """
    secret = tmp_path / "config" / "secrets" / "qdrant_api_key"
    secret.parent.mkdir(parents=True)
    secret.write_text("dev-key-that-must-never-reach-staging", encoding="utf-8")
    monkeypatch.setattr(staging_gate, "qdrant_checkout_secret_candidates", lambda: (secret,))

    staging_env.delenv("QDRANT_API_KEY", raising=False)
    staging_env.delenv("QDRANT_TEST_API_KEY", raising=False)

    failures = _failed(staging_gate.preflight_findings())
    key_findings = [name for name in failures if "QDRANT_API_KEY" in name]
    assert key_findings, failures
    assert failures[key_findings[0]] == staging_gate.INVALID_SOURCE

    assert staging_gate.checkout_secret_is_forbidden(secret) is True


def test_a_checkout_secret_stays_legal_outside_staging_mode(
    developer_env, tmp_path: Path
) -> None:
    """The fallback exists for developers; the gate is what withdraws it."""
    secret = tmp_path / "config" / "secrets" / "qdrant_api_key"
    secret.parent.mkdir(parents=True)
    secret.write_text("dev-key", encoding="utf-8")
    assert staging_gate.checkout_secret_is_forbidden(secret) is False


# --------------------------------------------------------------------------- #
# The endpoint seam itself - behaviour, not just reporting
# --------------------------------------------------------------------------- #


def test_falkor_endpoint_refuses_its_development_default_in_staging_mode(staging_env) -> None:
    """If `falkor_host()` ever goes back to `os.environ.get(..., "localhost")`.

    The preflight alone would not catch that: the preflight reads the
    environment, while the suites read this function. Both have to refuse.
    """
    staging_env.delenv("FALKOR_TEST_HOST", raising=False)
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        falkor_host()

    staging_env.setenv("FALKOR_TEST_HOST", "localhost")
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        falkor_host()

    staging_env.delenv("FALKOR_TEST_PORT", raising=False)
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        falkor_port()


def test_qdrant_endpoint_refuses_its_development_default_in_staging_mode(staging_env) -> None:
    staging_env.setenv("QDRANT_TEST_URL", "http://127.0.0.1:6333")
    with pytest.raises(staging_gate.StagingGateConfigurationError):
        staging_gate.resolve_url("QDRANT_TEST_URL", "http://127.0.0.1:6333")


def test_a_hostname_merely_containing_localhost_is_not_treated_as_local() -> None:
    """Substring matching would reject a legitimate staging host.

    A rule that over-rejects gets switched off, which is how the hazard comes
    back.
    """
    assert staging_gate.url_is_local("http://localhost-staging.example.com:6333") is False
    assert staging_gate.url_is_local("redis://:pw@my-localhost.internal:6379/0") is False
    assert staging_gate.url_is_local("redis://:pw@127.0.0.1:6379/0") is True
    assert staging_gate.host_is_local("LOCALHOST") is True


# --------------------------------------------------------------------------- #
# 7. Developer mode keeps its existing semantics
# --------------------------------------------------------------------------- #


def test_developer_mode_keeps_the_local_defaults(developer_env) -> None:
    assert staging_gate.staging_gate_mode() is False
    assert falkor_host() == "localhost"
    assert falkor_port() == 6380
    assert staging_gate.resolve_url("QDRANT_TEST_URL", "http://127.0.0.1:6333") == "http://127.0.0.1:6333"
    assert staging_gate.resolve_secret("QDRANT_API_KEY") == ""
    # And the preflight is inert: it must never fail a developer's ordinary run.
    staging_gate.assert_staging_preflight()


def test_developer_mode_still_honours_an_explicit_override(developer_env) -> None:
    developer_env.setenv("FALKOR_TEST_HOST", "127.0.0.1")
    developer_env.setenv("FALKOR_TEST_PORT", "6390")
    assert falkor_host() == "127.0.0.1"
    assert falkor_port() == 6390


def test_missing_live_env_skips_for_a_developer_and_fails_under_the_gate(
    developer_env,
) -> None:
    """Bullet 5's real requirement: the gate run may not silently skip."""
    calls: Dict[str, List[str]] = {"skip": [], "fail": []}
    recorder = dict(
        skip=lambda message: calls["skip"].append(message),
        fail=lambda message: calls["fail"].append(message),
    )

    staging_gate.require_live_environment("REDIS_TEST_URL", **recorder)
    assert len(calls["skip"]) == 1 and calls["fail"] == []

    developer_env.setenv(staging_gate.STAGING_GATE_ENV, "1")
    staging_gate.require_live_environment("REDIS_TEST_URL", **recorder)
    assert len(calls["fail"]) == 1, "staging mode skipped a required live variable"

    # And with the switch on but the variable still missing, it is still a fail.
    developer_env.setenv(staging_gate.LIVE_TESTS_ENV, "1")
    staging_gate.require_live_environment("REDIS_TEST_URL", **recorder)
    assert len(calls["fail"]) == 2
    assert "REDIS_TEST_URL" in calls["fail"][-1]


def test_a_supplied_live_env_neither_skips_nor_fails(developer_env) -> None:
    """Or the gate would just ban running the live suite at all."""
    developer_env.setenv(staging_gate.LIVE_TESTS_ENV, "1")
    developer_env.setenv("REDIS_TEST_URL", "redis://redis.contraclaim-stg.internal:6379/0")
    staging_gate.require_live_environment(
        "REDIS_TEST_URL",
        skip=lambda message: pytest.fail(f"unexpected skip: {message}"),
        fail=lambda message: pytest.fail(f"unexpected fail: {message}"),
    )


# --------------------------------------------------------------------------- #
# Gate 2 evidence capability - the R-A6 §8.1 gap, structurally
# --------------------------------------------------------------------------- #

REDIS_LIVE_MODULE = "backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py"


def test_every_gate2_required_live_module_exists() -> None:
    """A required set naming a file that does not exist enforces nothing."""
    missing = [name for name in staging_gate.GATE2_REQUIRED_LIVE_FILES if not (PROJECT / name).is_file()]
    assert not missing, f"Gate 2's required live set names non-existent modules: {missing}"


def test_bullet_5_has_a_live_redis_evidence_module() -> None:
    """R-A6 §8.1: bullet 5 was the one criterion with no evidence-eligible artefact.

    Gate 2's evidence convention requires `evidence: <path>` naming a repository
    path that exists. Delete this module, or drop it from the required set, and
    bullet 5 becomes unsatisfiable again - so both are asserted.
    """
    assert (PROJECT / REDIS_LIVE_MODULE).is_file(), (
        "the Redis live-integration module is gone; Gate 2 bullet 5 has no "
        "evidence-eligible artefact again and the gate caps at 5/6"
    )
    assert staging_gate.is_gate2_required_file(REDIS_LIVE_MODULE), (
        "the Redis live module is no longer in the Gate 2 required set, so a "
        "staging run that skipped it would still report green"
    )


def _code_identifiers(path: Path) -> set:
    """Every name the module's CODE references - docstrings and comments excluded.

    Substring matching over the file text would be satisfied by the module's own
    prose: `RuntimeStateService` is named in its docstring, so a version of that
    module with every test body deleted would still have passed a text check.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # String constants count as code: an env-var name only exists as one.
            names.add(node.value)
    return names


def test_the_redis_evidence_module_is_gated_and_can_skip() -> None:
    """It must be opt-in for CI and capable of skipping, or the gate is inert.

    A module that runs unconditionally would fail every ordinary CI run; a
    module that cannot skip makes the staging skip-is-failure conversion
    meaningless, because there would be nothing to convert.
    """
    module = PROJECT / REDIS_LIVE_MODULE
    identifiers = _code_identifiers(module)

    assert "require_live_environment" in identifiers, (
        "the Redis live module no longer routes its environment gate through "
        "staging_gate, so a missing endpoint would skip even under the gate"
    )
    assert "REDIS_TEST_URL" in identifiers
    assert "skip" in identifiers, "the module can no longer skip, so there is nothing to convert"

    # It must exercise the production classes, not raw commands: a PING-only
    # module would satisfy every check above and prove nothing about the app.
    for symbol in ("RuntimeStateService", "ContractIngestQueue", "InMemoryCache"):
        assert symbol in identifiers, (
            f"{REDIS_LIVE_MODULE} no longer exercises {symbol}; bullet 5 claims the "
            "production queue and runtime-state paths work, not that Redis answers PING"
        )

    # And the production queue primitive specifically. A module that enqueued
    # and then read the list back with LRANGE would test Redis lists, not the
    # thing that makes a crashed worker recoverable.
    for primitive in ("brpoplpush", "enqueue", "_recover_stale_processing_jobs_once"):
        assert primitive in identifiers, (
            f"{REDIS_LIVE_MODULE} no longer exercises {primitive}"
        )


def test_the_redis_module_ships_its_own_negative_controls() -> None:
    """A live suite that cannot fail is not evidence.

    Named individually rather than counted: "at least two tests with 'refused'
    in the name" would be satisfied by renaming, and the four controls the gate
    brief requires are specific ones.
    """
    tree = ast.parse((PROJECT / REDIS_LIVE_MODULE).read_text(encoding="utf-8"))
    tests = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }
    for control in (
        "test_wrong_port_is_refused_live",
        "test_wrong_password_is_refused_live",
        "test_the_queue_assertions_are_value_sensitive_live",
        "test_this_run_left_no_redis_residue_live",
    ):
        assert control in tests, (
            f"{control} is gone from {REDIS_LIVE_MODULE}; without it the suite's "
            "positive assertions are unproven"
        )


# --------------------------------------------------------------------------- #
# The skip-is-failure conversion - the mechanism, and the harness applying it
# --------------------------------------------------------------------------- #


def test_the_conversion_decision_is_scoped_to_staging_and_to_the_required_set(
    developer_env,
) -> None:
    """The pure function, over its four axes.

    Each `assert False` below is a way the conversion could be too wide: firing
    outside staging mode would break every ordinary developer run, and firing on
    a file Gate 2 does not score would convert skips that mean nothing.
    """
    unrelated = "backend/rbac_backend/tests/test_contract_master.py"

    # Not in staging mode: nothing converts, whatever skipped.
    assert staging_gate.staging_skip_is_gate_failure(REDIS_LIVE_MODULE, "skipped") is False

    developer_env.setenv(staging_gate.STAGING_GATE_ENV, "1")
    assert staging_gate.staging_skip_is_gate_failure(REDIS_LIVE_MODULE, "skipped") is True
    # Absolute and Windows-separator paths reach the hook, not repo-relative ones.
    assert staging_gate.staging_skip_is_gate_failure(
        str(PROJECT / REDIS_LIVE_MODULE), "skipped"
    ) is True
    # A passing or failing test is untouched.
    assert staging_gate.staging_skip_is_gate_failure(REDIS_LIVE_MODULE, "passed") is False
    assert staging_gate.staging_skip_is_gate_failure(REDIS_LIVE_MODULE, "failed") is False
    # A file Gate 2 does not score is untouched.
    assert staging_gate.staging_skip_is_gate_failure(unrelated, "skipped") is False


def test_the_conftest_hook_actually_applies_the_conversion() -> None:
    """The decision function is inert unless something applies it everywhere.

    Deleting the `_CERTIFICATION_GATES` entry leaves every other test in this
    file passing, which is exactly the regression this guards.
    """
    conftest = TESTS / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"))
    identifiers = _code_identifiers(conftest)

    # Scoped to the TABLE, not to the module. Removing the entry while leaving
    # the import in place is the natural shape of this regression, and a
    # module-wide identifier check passes straight through it - as the mutation
    # proof showed.
    table = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_CERTIFICATION_GATES" for t in node.targets
            )
        ),
        None,
    )
    assert table is not None, "the certification-gate table is gone"
    entry_names = {n.id for n in ast.walk(table) if isinstance(n, ast.Name)}
    assert "staging_skip_is_gate_failure" in entry_names, (
        "the staging gate is no longer an entry in the conftest's "
        "certification-gate table, so a skipped Gate 2 module would report green"
    )
    assert "STAGING_GATE_ENV" in entry_names

    assert "assert_staging_preflight" in identifiers, (
        "the preflight is no longer invoked before collection"
    )
    hooks = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("pytest_")
    }
    assert "pytest_configure" in hooks and "pytest_runtest_makereport" in hooks


def test_a_skipped_gate2_module_fails_the_run_under_the_gate(tmp_path: Path) -> None:
    """The harness proof, in a real subprocess - the shape G30/G31 already use.

    Everything above asserts the decision. This asserts that pytest reports it:
    with the staging gate on and a reachable-looking but dead Qdrant endpoint,
    the containment suite exercises its OWN skip block, and the run must be RED.
    The same invocation without the flag must be GREEN, or the conversion is not
    what made the difference.
    """
    target = "backend/rbac_backend/tests/integration/test_qdrant_containment_live.py"
    # Inherit the interpreter's environment (pytest needs HOME/USERPROFILE and
    # the platform variables), then clear every variable this gate reads so a
    # value from the developer's own shell cannot decide the outcome. Same shape
    # as `test_g30_reader_certification._run_pytest`.
    base = {k: v for k, v in os.environ.items() if k not in MANAGED_ENV}
    base.update({
        "TEMP": str(tmp_path),
        "ENVIRONMENT": "staging",
        "RUN_EXTERNAL_INTEGRATION_TESTS": "1",
        "FALKOR_TEST_HOST": "falkordb.contraclaim-stg.invalid",
        "FALKOR_TEST_PORT": "6379",
        # `.invalid` is reserved by RFC 2606 and never resolves, so the suite
        # reaches its unreachable-engine skip rather than touching any engine.
        "QDRANT_TEST_URL": "http://qdrant.contraclaim-stg.invalid:6333",
        "QDRANT_API_KEY": "not-a-real-key",
        "REDIS_TEST_URL": "redis://redis.contraclaim-stg.invalid:6379/0",
        "DATABASE_URL": "mongodb://mongo1.contraclaim-stg.invalid:27017/contraclaim_staging",
        "QDRANT_TEST_TIMEOUT": "2",
    })

    def run(extra):
        return subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
            cwd=str(PROJECT),
            env={**base, **extra},
            capture_output=True,
            text=True,
        )

    permissive = run({})
    assert permissive.returncode == 0, (
        "an ordinary run must still be allowed to skip on an unreachable engine:\n"
        + permissive.stdout[-2000:]
    )
    assert "skipped" in permissive.stdout

    certifying = run({staging_gate.STAGING_GATE_ENV: "1"})
    assert certifying.returncode != 0, (
        "a skipped Gate 2 module reported GREEN under the staging gate, so the "
        "run could carry a checked bullet while measuring nothing:\n"
        + certifying.stdout[-2000:]
    )
    assert staging_gate.STAGING_GATE_ENV in certifying.stdout


def test_the_qdrant_module_refuses_the_checkout_secret_under_the_gate() -> None:
    """Pins the wiring, not just the decision function.

    `checkout_secret_is_forbidden` returning True is useless if `_api_key` stops
    calling it, and that regression is invisible without a live Qdrant.
    """
    module = PROJECT / "backend/rbac_backend/tests/integration/test_qdrant_containment_live.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    api_key = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_api_key"),
        None,
    )
    assert api_key is not None, "_api_key is gone; re-derive how the Qdrant key is resolved"
    called = {
        node.func.attr
        for node in ast.walk(api_key)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "checkout_secret_is_forbidden" in called, (
        "the Qdrant live suite reads config/secrets/qdrant_api_key without asking "
        "the staging gate, so a staging run can authenticate with the dev key"
    )


# --------------------------------------------------------------------------- #
# R-A8J F2 - the guard must fire in the layout that produces Gate 2 evidence
# --------------------------------------------------------------------------- #
#
# The whole mechanism above was inert in the only place Gate 2 evidence can be
# measured. Inside the backend container the application is installed at `/app`,
# so pytest's rootdir is `/app` and the path reaching the hook is
# `/app/rbac_backend/tests/integration/<file>`. Every required entry was written
# with a `backend/` prefix and matched by suffix, so no container path could ever
# match: R-A8I's 21 required Falkor tests skipped, the conversion did not fire,
# and pytest exited 0 with bullet 4 unmeasured.
#
# The requirement is a test IDENTITY, not a checkout layout. These tests pin it
# in both layouts at once, so a fix that hard-codes either root fails the other.

#: The two roots a Gate 2 run is actually launched from. Neither may be
#: privileged over the other, and neither may be the only one that works.
CONTAINER_ROOT = "/app"
REPOSITORY_PREFIX = "backend"


def _container_path(required: str) -> str:
    """The same required module, as the backend container addresses it.

    `backend/rbac_backend/tests/x.py` becomes `/app/rbac_backend/tests/x.py` -
    the image copies the *contents* of `backend/` to `/app`, so the `backend/`
    segment does not exist inside the container at all.
    """
    assert required.startswith(REPOSITORY_PREFIX + "/"), required
    return f"{CONTAINER_ROOT}/{required[len(REPOSITORY_PREFIX) + 1:]}"


@pytest.mark.parametrize("required", staging_gate.GATE2_REQUIRED_LIVE_FILES)
def test_a_required_module_is_recognised_in_the_repository_layout(required: str) -> None:
    assert staging_gate.is_gate2_required_file(required) is True
    assert staging_gate.is_gate2_required_file(str(PROJECT / required)) is True


@pytest.mark.parametrize("required", staging_gate.GATE2_REQUIRED_LIVE_FILES)
def test_a_required_module_is_recognised_in_the_container_layout(required: str) -> None:
    """R-A8I F2, as a permanent test.

    `is_gate2_required_file('/app/rbac_backend/tests/integration/...')` returned
    False, which is why an all-skipped Gate 2 module reported green.
    """
    assert staging_gate.is_gate2_required_file(_container_path(required)) is True


def test_the_recognition_is_not_hard_coded_to_either_root() -> None:
    """Any checkout root, not just this machine's and not just `/app`.

    A fix that swapped one hard-coded prefix for another would satisfy the two
    tests above and fail here - which is the whole point of having it.
    """
    tail = "rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py"
    for root in ("/srv/contraclaim", "/opt/contraclaim-stg/projectDMS/backend", "D:/ci/w1"):
        assert staging_gate.is_gate2_required_file(f"{root}/{tail}") is True
    # Windows separators reach the hook on a developer machine.
    assert staging_gate.is_gate2_required_file(
        r"C:\ci\work\rbac_backend\tests\integration\test_redis_queue_runtime_state_live.py"
    ) is True


def test_an_unrelated_module_is_not_recognised_in_either_layout() -> None:
    """Root independence must not become basename matching.

    A module with the same file name in another package is a different test, and
    a guard that converted its skips would fail runs Gate 2 does not score.
    """
    for candidate in (
        "backend/rbac_backend/tests/test_contract_master.py",
        "/app/rbac_backend/tests/test_contract_master.py",
        "/app/rbac_backend/tests/test_redis_queue_runtime_state_live.py",
        "/app/other_package/tests/integration/test_redis_queue_runtime_state_live.py",
        "/app/integration/test_redis_queue_runtime_state_live.py",
    ):
        assert staging_gate.is_gate2_required_file(candidate) is False, candidate


def test_a_required_skip_in_the_container_layout_is_a_gate_failure(developer_env) -> None:
    """The end of the chain: the decision the conftest hook actually consults."""
    container = _container_path(
        "backend/rbac_backend/tests/integration/"
        "test_graph_end_to_end_material_influence_falkor.py"
    )

    # Outside staging mode a skip stays a skip, in either layout.
    assert staging_gate.staging_skip_is_gate_failure(container, "skipped") is False

    developer_env.setenv(staging_gate.STAGING_GATE_ENV, "1")
    assert staging_gate.staging_skip_is_gate_failure(container, "skipped") is True
    assert staging_gate.staging_skip_is_gate_failure(container, "passed") is False
    assert staging_gate.staging_skip_is_gate_failure(container, "failed") is False
    assert staging_gate.staging_skip_is_gate_failure(
        "/app/rbac_backend/tests/test_contract_master.py", "skipped"
    ) is False


#: The other four certification gates carry the identical defect, because they
#: share the shape. Fixing Gate 2 alone would leave G29/G30/G31/G32-STATE inert
#: inside the same container.
_CERTIFICATION_REQUIRED_SETS = (
    ("G31 writer", authority_band_graph.G31_REQUIRED_WRITER_FILES, authority_band_graph.is_g31_required_file),
    ("G30 reader", authority_band_graph.G30_REQUIRED_READER_FILES, authority_band_graph.is_g30_required_file),
    ("G32-STATE", authority_band_graph.G32_STATE_REQUIRED_FILES, authority_band_graph.is_g32_state_required_file),
    ("G29 umbrella", authority_band_graph.G29_UMBRELLA_REQUIRED_FILES, authority_band_graph.is_g29_umbrella_required_file),
)


@pytest.mark.parametrize("gate,required_files,predicate", _CERTIFICATION_REQUIRED_SETS)
def test_every_certification_gate_recognises_the_container_layout(
    gate: str, required_files, predicate
) -> None:
    for required in required_files:
        assert predicate(required) is True, f"{gate}: repository layout {required}"
        assert predicate(_container_path(required)) is True, f"{gate}: container layout {required}"


@pytest.mark.parametrize("gate,required_files,predicate", _CERTIFICATION_REQUIRED_SETS)
def test_no_certification_gate_matches_by_basename(gate: str, required_files, predicate) -> None:
    for required in required_files:
        basename = required.rsplit("/", 1)[1]
        assert predicate(f"/app/somewhere_else/{basename}") is False, f"{gate}: {basename}"
