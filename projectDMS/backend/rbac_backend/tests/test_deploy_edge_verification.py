"""Production and staging do not have the same edge, and must not share one check.

`post_deploy_verify.sh` verified the public gateway like this::

    if [[ -n "$PUBLIC_BASE_URL" ]]; then
      http_check "${PUBLIC_BASE_URL%/}/health" "Public gateway health responded"
    fi

Absence is a silent skip. A production verification run with that variable unset
- unexported, misspelt, dropped from `.env`, or shadowed by an earlier
`env_file_load` - reports the same "10 PASS, 0 failures" as one that reached the
public edge and got 200. The single control over the surface every user arrives
through was the one control that could vanish without saying so.

R-A8Q found the mirror-image problem. Same-host staging is deliberately built
with **no public DNS**: its gateway is reached over loopback with a run-owned TLS
terminator. A check that demanded a public hostname there would be demanding a
property staging is designed not to have, and the only ways to satisfy it are to
point the staging run at production's hostname - verifying production and filing
it as staging evidence - or to delete the check.

So the mode is an input, and the two modes have different requirements:

* **production**: an edge URL is REQUIRED, must be TLS, and must be a public DNS
  name. Absent is a FAILURE, never a skip.
* **staging**: an edge URL is REQUIRED and must be TLS, because
  `AUTH_COOKIE_SECURE=true` means the session cookie will not survive plain HTTP.
  Loopback and private addresses are ACCEPTED - that is the staging topology, not
  a defect. What staging may not do is address a production host.

Neither mode is inferred loosely: an undecidable mode refuses rather than picking
one, the same rule `check_maintenance_time_budget.py` applies to a window it
cannot parse. Guessing "staging" would hand production the staging exemption;
guessing "production" would make every staging run demand public DNS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "scripts" / "lib" / "edge_target.sh"
POST_DEPLOY = REPO_ROOT / "scripts" / "post_deploy_verify.sh"

BASH = shutil.which("bash") or "/bin/bash"

pytestmark = pytest.mark.skipif(
    not Path(BASH).exists(), reason="bash is required to exercise the shell library"
)

#: A refusal, so an operator can tell "we could not decide" from "it answered 404".
REFUSED = 2


def run_plan(
    *,
    mode: Optional[str] = None,
    environment: Optional[str] = None,
    public_base_url: Optional[str] = None,
    staging_edge_base_url: Optional[str] = None,
    production_public_hosts: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Resolve the mode and print the plan, in a shell with nothing inherited."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    for name, value in (
        ("DEPLOY_VERIFY_MODE", mode),
        ("ENVIRONMENT", environment),
        ("PUBLIC_BASE_URL", public_base_url),
        ("STAGING_EDGE_BASE_URL", staging_edge_base_url),
        ("PRODUCTION_PUBLIC_HOSTS", production_public_hosts),
    ):
        if value is not None:
            env[name] = value

    script = f'. "{LIB.as_posix()}"; edge_verification_plan'
    return subprocess.run(
        [BASH, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def field(stdout: str, key: str) -> str:
    for token in stdout.split():
        name, separator, value = token.partition("=")
        if separator and name == key:
            return value
    raise AssertionError(f"{key!r} is not in the plan: {stdout!r}")


def test_the_library_exists_and_is_sourced_by_the_verifier() -> None:
    """A helper nothing calls is documentation with a shebang."""
    assert LIB.is_file()
    text = POST_DEPLOY.read_text(encoding="utf-8")
    assert "lib/edge_target.sh" in text, "post_deploy_verify.sh no longer sources the edge library"
    assert "edge_verification_plan" in text, (
        "post_deploy_verify.sh no longer resolves an edge plan, so the edge check "
        "is back to whatever it decides inline"
    )


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "environment, expected",
    [("production", "production"), ("Production", "production"), ("staging", "staging")],
)
def test_the_mode_follows_the_deployment_label(environment: str, expected: str) -> None:
    result = run_plan(
        environment=environment,
        public_base_url="https://app.example.com",
        staging_edge_base_url="https://127.0.0.1:8443",
    )
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "mode") == expected


def test_an_explicit_mode_overrides_the_label() -> None:
    """A staging stack rendered from a production env file still labels itself production.

    R-A8M ran the readiness script against staging with `ENVIRONMENT=production`
    for exactly that reason. The override exists so the operator can say which
    deployment is in front of them without editing the environment file.
    """
    result = run_plan(
        mode="staging",
        environment="production",
        staging_edge_base_url="https://127.0.0.1:8443",
    )
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "mode") == "staging"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="nothing-set"),
        pytest.param({"environment": ""}, id="empty-label"),
        pytest.param({"environment": "development"}, id="unrecognised-label"),
        pytest.param({"mode": "prod"}, id="unrecognised-mode"),
    ],
)
def test_an_undecidable_mode_is_refused_rather_than_guessed(kwargs: dict) -> None:
    result = run_plan(public_base_url="https://app.example.com", **kwargs)
    assert result.returncode == REFUSED, result.stdout
    assert "cannot decide" in result.stderr.lower()
    assert "DEPLOY_VERIFY_MODE" in result.stderr


# --------------------------------------------------------------------------- #
# Production: the public edge is required
# --------------------------------------------------------------------------- #


def test_production_accepts_a_public_tls_edge() -> None:
    result = run_plan(mode="production", public_base_url="https://web.contraclaim.com/")
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "url") == "https://web.contraclaim.com"
    assert field(result.stdout, "tls") == "required"
    assert field(result.stdout, "public_dns") == "required"


def test_production_refuses_a_missing_edge() -> None:
    """The whole defect. An unset variable used to mean "do not check"."""
    result = run_plan(mode="production")
    assert result.returncode == REFUSED
    assert "public edge is not configured" in result.stderr


def test_production_refuses_a_plaintext_edge() -> None:
    result = run_plan(mode="production", public_base_url="http://web.contraclaim.com")
    assert result.returncode == REFUSED
    assert "not TLS" in result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "https://127.0.0.1:8443",
        "https://[::1]",
        "https://10.1.2.3",
        "https://172.20.0.5",
        "https://192.168.1.10",
        "https://169.254.10.10",
        "https://gateway",
        "https://gateway.internal",
        "https://gateway.local",
    ],
)
def test_production_refuses_an_edge_that_is_not_public(url: str) -> None:
    """A production run pointed at the container network verifies the wrong thing.

    It would also pass: the gateway answers on its own network. "10 PASS" while
    every real user gets a certificate error is the outcome this refuses.
    """
    result = run_plan(mode="production", public_base_url=url)
    assert result.returncode == REFUSED, result.stdout
    assert "not a public" in result.stderr


def test_production_ignores_the_staging_edge_variable() -> None:
    """A staging override left in the environment must not satisfy production."""
    result = run_plan(mode="production", staging_edge_base_url="https://127.0.0.1:8443")
    assert result.returncode == REFUSED
    assert "public edge is not configured" in result.stderr


# --------------------------------------------------------------------------- #
# Staging: its own edge, and no inherited public-DNS requirement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:8443", "https://localhost:8443", "https://staging.example.internal"],
)
def test_staging_accepts_a_loopback_or_private_tls_edge(url: str) -> None:
    """Staging has no public DNS by design. That is topology, not a gap."""
    result = run_plan(mode="staging", staging_edge_base_url=url)
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "tls") == "required"
    assert field(result.stdout, "public_dns") == "not-required"


def test_staging_falls_back_to_the_public_variable() -> None:
    """A staging stack whose edge is configured under the ordinary name still verifies."""
    result = run_plan(mode="staging", public_base_url="https://127.0.0.1:8443")
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "url") == "https://127.0.0.1:8443"


def test_staging_prefers_its_own_variable_over_the_public_one() -> None:
    """Otherwise a production `.env` loaded into a staging run decides the target."""
    result = run_plan(
        mode="staging",
        public_base_url="https://web.contraclaim.com",
        staging_edge_base_url="https://127.0.0.1:8443",
        production_public_hosts="web.contraclaim.com",
    )
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "url") == "https://127.0.0.1:8443"


def test_staging_refuses_a_missing_edge() -> None:
    """Staging is exempt from public DNS, not from having an edge at all."""
    result = run_plan(mode="staging")
    assert result.returncode == REFUSED
    assert "staging edge is not configured" in result.stderr


def test_staging_refuses_a_plaintext_edge() -> None:
    """`AUTH_COOKIE_SECURE=true`, so a session over plain HTTP has no cookie.

    R-A8Q had to build a TLS terminator before Gate 3 bullet 1 could be measured
    at all. A staging run over `http://` measures a stack no session can survive.
    """
    result = run_plan(mode="staging", staging_edge_base_url="http://127.0.0.1:8080")
    assert result.returncode == REFUSED
    assert "not TLS" in result.stderr


@pytest.mark.parametrize(
    "url",
    ["https://web.contraclaim.com", "https://WEB.CONTRACLAIM.COM/", "https://contraclaim.com"],
)
def test_staging_refuses_an_edge_that_addresses_a_production_host(url: str) -> None:
    """The failure this whole mode split exists to prevent.

    A staging verification pointed at production's hostname passes, and files a
    measurement of production as staging evidence. R-A6 recorded the same shape
    for the Gate 2 engines; the edge had no equivalent.
    """
    result = run_plan(
        mode="staging",
        staging_edge_base_url=url,
        production_public_hosts="web.contraclaim.com,contraclaim.com",
    )
    assert result.returncode == REFUSED, result.stdout
    assert "production host" in result.stderr


def test_the_production_host_refusal_needs_no_configuration_to_be_safe() -> None:
    """With no list supplied the rule cannot fire, so it must not claim to have.

    The plan says which hosts it refused against. A run that names none is a run
    that checked none, and the operator can see that rather than assume.
    """
    result = run_plan(mode="staging", staging_edge_base_url="https://web.contraclaim.com")
    assert result.returncode == 0, result.stderr
    assert field(result.stdout, "production_hosts") == "none-declared"


# --------------------------------------------------------------------------- #
# The verifier must not be able to skip the check it just resolved
# --------------------------------------------------------------------------- #


def test_the_verifier_has_no_conditional_edge_check_left() -> None:
    """The old shape, in the file, byte for byte.

    A regression here is one `if [[ -n ... ]]` away, and it reads as caution.
    """
    text = POST_DEPLOY.read_text(encoding="utf-8")
    assert 'if [[ -n "$PUBLIC_BASE_URL" ]]; then' not in text, (
        "the public edge check is conditional on the variable being set again; an "
        "unset PUBLIC_BASE_URL would silently skip the only check over the surface "
        "users reach"
    )


def test_a_refused_plan_fails_the_verifier_rather_than_skipping_it() -> None:
    """Read from the source: the refusal branch must call `fail`, not `warn`.

    A warning is how a control stops being one. `pre_deploy_readiness.sh` carried
    that exact shape into R-A8Q and it had to be closed there too.
    """
    text = POST_DEPLOY.read_text(encoding="utf-8")
    start = text.index("edge_verification_plan")
    window = text[start : start + 1200]
    assert "fail " in window, (
        "the edge-plan refusal branch does not call `fail`; a deployment whose edge "
        "cannot be resolved would be reported as verified"
    )
    assert "warn " not in window.split("fail ")[0], (
        "the edge-plan refusal is downgraded to a warning before it reaches `fail`"
    )
