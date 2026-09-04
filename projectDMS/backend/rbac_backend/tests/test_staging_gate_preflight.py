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
from pathlib import Path
from typing import Dict, List

import pytest

from rbac_backend.tests import staging_gate
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
    assert set(staging_gate.PRESENT.split()) <= set(report.split())


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


def test_a_missing_qdrant_key_with_no_checkout_fallback_is_absent(
    staging_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABSENT, not INVALID SOURCE - the two are different failures.

    Parametrised with the others this case would be environment-dependent: the
    release worktree really does carry `config/secrets/qdrant_api_key`, so the
    same missing variable reports INVALID SOURCE there and ABSENT in CI. The
    fallback set is pinned here so the distinction is asserted, not observed.
    """
    monkeypatch.setattr(staging_gate, "qdrant_checkout_secret_candidates", tuple)
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


def test_the_redis_evidence_module_is_gated_and_can_skip() -> None:
    """It must be opt-in for CI and capable of skipping, or the gate is inert.

    A module that runs unconditionally would fail every ordinary CI run; a
    module that cannot skip makes the staging skip-is-failure conversion
    meaningless, because there would be nothing to convert.
    """
    source = (PROJECT / REDIS_LIVE_MODULE).read_text(encoding="utf-8")
    assert "require_live_environment" in source, (
        "the Redis live module no longer routes its environment gate through "
        "staging_gate, so a missing endpoint would skip even under the gate"
    )
    assert "REDIS_TEST_URL" in source
    assert "pytest.skip" in source or "skip=pytest.skip" in source
    # It must exercise the production classes, not raw commands: a PING-only
    # module would satisfy every check above and prove nothing about the app.
    for symbol in ("RuntimeStateService", "ContractIngestQueue", "InMemoryCache"):
        assert symbol in source, (
            f"{REDIS_LIVE_MODULE} no longer exercises {symbol}; bullet 5 claims the "
            "production queue and runtime-state paths work, not that Redis answers PING"
        )


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
