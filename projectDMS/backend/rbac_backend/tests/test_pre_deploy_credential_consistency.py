"""The release gates must not require credentials that cannot both be supplied.

R-A8I hit a condition no checkout can satisfy. `pre_deploy_readiness.sh` FAILs
unless `config/secrets/qdrant_api_key` exists and is non-empty;
`staging_gate.py` classifies that same file as an `INVALID SOURCE` and refuses any
Gate 2 run that would read it. One gate demands a plaintext credential inside the
working tree and the other exists to make sure no credential is ever read from
there.

The readiness check was also measuring the wrong thing. `docker-compose.prod.yml`
takes the Qdrant key from `${QDRANT_API_KEY}` in the environment - the checkout
file is referenced by nothing the deployment runs. So the check demanded an
artefact production does not use, in the one place a credential must never live,
and said nothing about the variable that actually has to be set.

The second half is `LANGGRAPH_API_TOKEN`. The production compose file requires it
with `:?`, so `docker compose config` fails outright without a value - yet
`LANGGRAPH_ENABLED` defaults to `false`, there is no `langgraph` service in that
file, and the application's own production gate requires the token *only* when the
service is enabled. Hard-requiring it at render time therefore does not protect
anything; it guarantees that every environment carries a fabricated value for a
service that is not running, which is exactly what R-A8I found in the staging
render.

One rule covers both, and it is the rule the application already follows: a
credential is REQUIRED when the service it belongs to is reachable, and ABSENT
when that service is disabled. Requiring a real secret for something switched off
teaches operators to invent secrets, and an invented secret is indistinguishable
from a real one at the point where it matters.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rbac_backend.tests import staging_gate

REPO_ROOT = Path(__file__).resolve().parents[3]
READINESS = REPO_ROOT / "scripts" / "pre_deploy_readiness.sh"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
CONFIG = REPO_ROOT / "backend" / "rbac_backend" / "core" / "config.py"


@pytest.fixture(scope="module")
def readiness() -> str:
    assert READINESS.is_file(), f"pre_deploy_readiness.sh not found at {READINESS}"
    return READINESS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    assert PROD_COMPOSE.is_file()
    return PROD_COMPOSE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The contradiction itself
# --------------------------------------------------------------------------- #


def _failing_blocks(script: str) -> str:
    """Everything the script says under a `fail`, joined.

    A path mentioned in a `warn` is advice; the same path under a `fail` is a
    precondition, and only the second one can contradict another gate.
    """
    # Comments are prose, not conditions. This file explains *why* the
    # checkout-local secret is no longer required, and a scan that could not tell
    # the explanation from the requirement would forbid writing it down.
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("#")]

    # The unit is the enclosing `if ... fi`, not a window of N lines above the
    # `fail`. A fixed window reaches backwards into whatever happened to be
    # written before it - here, a neighbouring `warn` block - and reports a
    # contradiction that is not in the condition at all.
    blocks: list = []
    current: list = []
    depth = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("if ") or stripped.startswith("if["):
            if depth == 0:
                current = []
            depth += 1
        if depth > 0:
            current.append(line)
        if stripped == "fi" and depth > 0:
            depth -= 1
            if depth == 0:
                blocks.append("\n".join(current))
                current = []

    # A bare one-line `... && pass ... || fail ...` is not inside an if/fi.
    blocks.extend(line for line in lines if "fail " in line and not line.strip().startswith("fail "))

    return "\n".join(block for block in blocks if re.search(r"(^|\s)fail ", block, re.M))


def test_no_readiness_check_requires_a_credential_from_inside_the_checkout(readiness: str) -> None:
    """The exact R-A8I contradiction, as a permanent test.

    `staging_gate.is_checkout_secret` is the same predicate the Gate 2 preflight
    uses, so the two gates cannot drift apart: if one starts calling a path an
    invalid source, the other cannot go on requiring it.
    """
    failing = _failing_blocks(readiness)
    offenders = [
        candidate
        for candidate in re.findall(r"[\w./$\{\}-]*config/secrets/[\w./-]+", failing)
        if staging_gate.is_checkout_secret(Path(candidate))
    ]

    assert not offenders, (
        "pre_deploy_readiness.sh FAILs unless a checkout-local secret exists, and "
        "the staging gate refuses to run against exactly those files. No single "
        f"checkout can satisfy both: {sorted(set(offenders))}"
    )


def test_the_qdrant_key_is_required_from_the_environment_the_deployment_reads(
    readiness: str, compose: str
) -> None:
    """Requiring the file said nothing about whether the deployment would start.

    `docker-compose.prod.yml` interpolates `${QDRANT_API_KEY}`; nothing it runs
    opens `config/secrets/qdrant_api_key`. A readiness check that passed on the
    file and never looked at the variable would let a deploy through that cannot
    render at all.
    """
    assert "QDRANT_API_KEY" in compose
    assert "get_env QDRANT_API_KEY" in readiness, (
        "readiness does not check the variable the production render requires"
    )
    assert "QDRANT_API_KEY" in _failing_blocks(readiness), (
        "a missing QDRANT_API_KEY must be a FAIL: the production render cannot "
        "resolve without it"
    )


def test_a_checkout_local_secret_is_reported_as_a_hazard_not_ignored(readiness: str) -> None:
    """Dropping the check entirely would be the other way to get this wrong.

    The file still being there is worth saying - it is a plaintext credential in
    the working tree that nothing reads - but it is a warning about something to
    remove, not a precondition for deploying.
    """
    assert "config/secrets/qdrant_api_key" in readiness, (
        "the checkout-local secret is no longer mentioned at all, so an operator "
        "gets no warning that a plaintext credential is sitting in the tree"
    )
    warn_context = "\n".join(
        line
        for index, line in enumerate(readiness.splitlines())
        if line.strip().startswith("warn ")
        or "config/secrets/qdrant_api_key" in line
    )
    assert "warn" in warn_context


# --------------------------------------------------------------------------- #
# Disabled services may not demand real credentials
# --------------------------------------------------------------------------- #


def _hard_required(compose_text: str) -> set:
    """Variables `docker compose config` refuses to render without."""
    return set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose_text))


def _enable_flags(compose_text: str) -> dict:
    """`<PREFIX>_ENABLED` and the value it takes when the operator sets nothing."""
    flags = {}
    pattern = r"^\s*([A-Z0-9_]+_ENABLED):\s*\"(?:\$\{[A-Z0-9_]+:-)?(true|false)\}?\""
    for match in re.finditer(pattern, compose_text, re.M):
        flags[match.group(1)] = match.group(2) == "true"
    return flags


def test_no_credential_for_a_disabled_service_is_hard_required_by_the_render(
    compose: str,
) -> None:
    """The general form of the LangGraph finding.

    A `${VAR:?}` makes `docker compose config` fail without a value. For a service
    whose enable flag defaults to false, that does not protect the deployment - it
    forces every environment to carry a fabricated value for something that is not
    running, and a fabricated value is indistinguishable from a real one at the
    point it matters. R-A8I recorded exactly that: the only placeholder in the
    staging render was `LANGGRAPH_API_TOKEN`.
    """
    required = _hard_required(compose)
    disabled_prefixes = [
        flag[: -len("_ENABLED")] for flag, enabled in _enable_flags(compose).items() if not enabled
    ]

    offenders = sorted(
        variable
        for variable in required
        for prefix in disabled_prefixes
        if variable.startswith(prefix + "_")
    )

    assert not offenders, (
        "the production render hard-requires credentials belonging to services "
        f"that default to disabled: {offenders}. Give them a `:-` default and let "
        "the application's own production gate require them when the service is "
        "switched on."
    )


def test_the_application_gate_still_requires_the_token_when_the_service_is_on() -> None:
    """Relaxing the render must not relax the requirement.

    The gate that matters is the one that can see whether the service is enabled,
    and it must stay conditional-but-present: a token that is optional everywhere
    is a service that authenticates with nothing.
    """
    source = CONFIG.read_text(encoding="utf-8")

    assert "langgraph_enabled and not langgraph_token" in source, (
        "the production configuration gate no longer requires LANGGRAPH_API_TOKEN "
        "when LANGGRAPH_ENABLED is true, so nothing requires it anywhere"
    )


def test_an_absent_langgraph_token_refuses_service_calls_rather_than_allowing_them() -> None:
    """The reason the render may stop demanding a value at all.

    `verify_langgraph_token` rejects when no token is configured. Were it to treat
    an empty expected value as "no check", removing the `:?` would turn a
    fabricated placeholder into an open door.
    """
    from rbac_backend.routers import documents

    import inspect

    source = inspect.getsource(documents.verify_langgraph_token)
    assert "if not expected" in source, (
        "verify_langgraph_token no longer fails closed on an unconfigured token"
    )
