"""Staging Gate-2 mode: no live test may fall back to a development engine.

R-A6 found three live suites whose in-source defaults point at this development
machine's own containers:

* ``FALKOR_TEST_HOST`` / ``FALKOR_TEST_PORT`` default to ``localhost:6380`` —
  the ``contract-ai-falkordb-1`` container holding the protected business
  graphs ``G`` and ``contraclaim``;
* ``QDRANT_TEST_URL`` defaults to ``http://127.0.0.1:6333`` — the
  ``contract-ai-qdrant-1`` container;
* ``test_qdrant_containment_live.py`` falls back to reading
  ``config/secrets/qdrant_api_key`` out of the checkout when no key is in the
  environment.

Each of those is convenient for a developer and fatal for a gate. A staging run
launched on the same machine that forgot one override would produce "staging"
evidence measured against the development engines, and would write graphs into
the instance holding the business data. The failure mode is silent: every test
passes, against the wrong thing.

This module is the single seam that makes it impossible. One switch —
``CONTRACLAIM_STAGING_GATE=1`` — turns every convenience default into a hard
error, and the preflight runs *before* collection so the run stops with a
readable report rather than a wall of import errors.

Three rules, each with a tempting weaker version:

* **One flag, not several.** A second overlapping switch means a run can be
  half in staging mode, which is worse than not having the mode at all.
* **Absence is a failure, not a skip.** Outside this mode the live suites skip
  when their environment is missing, which is right for development. Inside it
  a skip is exactly the outcome the mode exists to prevent.
* **Nothing is read off the disk.** A credential that can come from the
  checkout is a credential that will come from the checkout on the day someone
  forgets to export it.

No secret value is ever returned by the reporting functions or placed in an
error message: a finding is ``PRESENT``, ``ABSENT`` or ``INVALID SOURCE``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

#: The one staging/live-gate switch. Deliberately singular.
STAGING_GATE_ENV = "CONTRACLAIM_STAGING_GATE"

#: The master live-integration switch the Gate 2 suites already read.
LIVE_TESTS_ENV = "RUN_EXTERNAL_INTEGRATION_TESTS"

#: The environment label a staging run must carry.
ENVIRONMENT_ENV = "ENVIRONMENT"
STAGING_ENVIRONMENT_LABEL = "staging"

#: Hostnames that mean "this machine". A staging endpoint may never be one of
#: them: on the development host every one of these resolves to a container in
#: the `contract-ai_*` project.
LOCAL_HOST_TOKENS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        "0.0.0.0",
        "host.docker.internal",
    }
)

#: Directory whose contents are development secrets checked out on disk. Live
#: credentials must arrive through the environment, never from here.
CHECKOUT_SECRETS_DIRNAME = "secrets"

PRESENT = "PRESENT"
ABSENT = "ABSENT"
INVALID_SOURCE = "INVALID SOURCE"

_TRUTHY = {"1", "true", "yes", "on"}


class StagingGateConfigurationError(RuntimeError):
    """The staging Gate-2 environment is not safe to measure evidence with."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def staging_gate_mode() -> bool:
    """Is this run being offered as staging Gate-2 evidence?"""
    return _env(STAGING_GATE_ENV).lower() in _TRUTHY


def live_tests_enabled() -> bool:
    return _env(LIVE_TESTS_ENV).lower() in _TRUTHY


def host_is_local(value: str) -> bool:
    """Does this host token address the machine the tests run on?"""
    return value.strip().strip("[]").lower() in {t.strip("[]") for t in LOCAL_HOST_TOKENS}


def url_is_local(value: str) -> bool:
    """Does this URL address the machine the tests run on?

    Parsed rather than substring-matched: ``redis://user@localhost.example.com``
    is a remote host whose name merely contains the token, and
    ``redis://:pw@127.0.0.1:6379/0`` hides the host behind credentials.
    """
    raw = value.strip()
    if not raw:
        return False
    parsed = urlsplit(raw if "//" in raw else f"//{raw}")
    hostname = parsed.hostname
    if hostname is None:
        # No recognisable authority; fall back to the whole string so a bare
        # "localhost:6333" is still caught.
        return host_is_local(raw.split("/")[0].split(":")[0])
    return host_is_local(hostname)


def is_checkout_secret(path: Path) -> bool:
    """Is this path a development secret living inside the checkout?"""
    return CHECKOUT_SECRETS_DIRNAME in {part.lower() for part in Path(path).parts}


def checkout_secret_is_forbidden(path: Path) -> bool:
    """May this checkout-local secret file be read for a live credential?

    Only the staging gate forbids it. Ordinary development keeps the fallback,
    which is the whole reason the file exists.
    """
    return staging_gate_mode() and is_checkout_secret(path)


# --------------------------------------------------------------------------- #
# Endpoint resolution — the seam every live suite calls
# --------------------------------------------------------------------------- #


def _reject(name: str, reason: str) -> "StagingGateConfigurationError":
    return StagingGateConfigurationError(
        f"{STAGING_GATE_ENV} is set, so this run is staging Gate-2 evidence and "
        f"{name} {reason}. Export the staging value explicitly; a Gate-2 result "
        "measured against a development engine proves nothing about staging."
    )


def resolve_host(name: str, default: str) -> str:
    """A hostname for a live suite. Refuses a development default in staging mode."""
    value = _env(name)
    if not staging_gate_mode():
        return value or default
    if not value:
        raise _reject(name, "is not set")
    if host_is_local(value):
        raise _reject(name, f"points at this machine ({value!r})")
    return value


def resolve_port(name: str, default: int) -> int:
    """A port for a live suite. Must be supplied explicitly in staging mode."""
    value = _env(name)
    if not staging_gate_mode():
        return int(value or default)
    if not value:
        raise _reject(name, "is not set")
    try:
        return int(value)
    except ValueError as exc:
        raise _reject(name, f"is not a port number ({value!r})") from exc


def resolve_url(name: str, default: str) -> str:
    """A service URL for a live suite. Refuses a development default in staging mode."""
    value = _env(name)
    if not staging_gate_mode():
        return value or default
    if not value:
        raise _reject(name, "is not set")
    if url_is_local(value):
        raise _reject(name, f"points at this machine ({value!r})")
    return value


def resolve_secret(*names: str) -> str:
    """A credential, from the environment only.

    Returns the first non-empty value. In staging mode an absent credential is
    a hard error rather than an empty string, because an empty key silently
    turns an authenticated request into an unauthenticated one.
    """
    for name in names:
        value = _env(name)
        if value:
            return value
    if staging_gate_mode():
        raise _reject(" / ".join(names), "is not set in the environment")
    return ""


# --------------------------------------------------------------------------- #
# Preflight — reported by status only, never by value
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreflightFinding:
    name: str
    status: str
    detail: str
    mandatory: bool

    @property
    def ok(self) -> bool:
        return self.status == PRESENT


def _switch_finding(name: str, *, mandatory: bool = True) -> PreflightFinding:
    raw = _env(name)
    if not raw:
        return PreflightFinding(name, ABSENT, "not set", mandatory)
    if raw.lower() not in _TRUTHY:
        return PreflightFinding(name, INVALID_SOURCE, "set to a non-enabling value", mandatory)
    return PreflightFinding(name, PRESENT, "enabled", mandatory)


def _label_finding() -> PreflightFinding:
    raw = _env(ENVIRONMENT_ENV)
    if not raw:
        return PreflightFinding(ENVIRONMENT_ENV, ABSENT, "not set", True)
    if raw.lower() != STAGING_ENVIRONMENT_LABEL:
        return PreflightFinding(
            ENVIRONMENT_ENV,
            INVALID_SOURCE,
            f"labelled {raw!r}, not {STAGING_ENVIRONMENT_LABEL!r}",
            True,
        )
    return PreflightFinding(ENVIRONMENT_ENV, PRESENT, "labelled staging", True)


def _endpoint_finding(name: str, is_local: Callable[[str], bool], *, mandatory: bool = True) -> PreflightFinding:
    raw = _env(name)
    if not raw:
        return PreflightFinding(name, ABSENT, "not set", mandatory)
    if is_local(raw):
        return PreflightFinding(name, INVALID_SOURCE, "addresses the local development host", mandatory)
    return PreflightFinding(name, PRESENT, "explicit remote endpoint", mandatory)


def _port_finding(name: str, *, mandatory: bool = True) -> PreflightFinding:
    raw = _env(name)
    if not raw:
        return PreflightFinding(name, ABSENT, "not set", mandatory)
    if not raw.isdigit():
        return PreflightFinding(name, INVALID_SOURCE, "not a port number", mandatory)
    return PreflightFinding(name, PRESENT, "explicit", mandatory)


def _credential_finding(
    names: Sequence[str],
    *,
    mandatory: bool,
    checkout_fallbacks: Sequence[Path] = (),
) -> PreflightFinding:
    label = " / ".join(names)
    for name in names:
        if _env(name):
            return PreflightFinding(label, PRESENT, "supplied by the environment", mandatory)
    for candidate in checkout_fallbacks:
        if Path(candidate).is_file():
            return PreflightFinding(
                label,
                INVALID_SOURCE,
                "absent from the environment while a checkout-local development "
                "secret exists that the live suite would otherwise read",
                mandatory,
            )
    return PreflightFinding(label, ABSENT, "not set", mandatory)


def qdrant_checkout_secret_candidates() -> Tuple[Path, ...]:
    """The exact paths `test_qdrant_containment_live.py::_api_key` would read.

    That module resolves them from its own location, two directories deeper
    than this one, so the parent indexes differ while the directories are the
    same two: `<projectDMS>/config/secrets` and `<backend>/config/secrets`.
    """
    here = Path(__file__).resolve()
    return (
        here.parents[3] / "config" / "secrets" / "qdrant_api_key",
        here.parents[2] / "config" / "secrets" / "qdrant_api_key",
    )


def preflight_findings() -> List[PreflightFinding]:
    """Every staging Gate-2 precondition, by status only.

    Mandatory findings gate the run. Conditional ones (OpenAI, AWS) are
    reported so the operator knows which bullets the run can carry; the live
    tests that need them fail on their own under this mode rather than skipping.
    """
    findings = [
        _switch_finding(STAGING_GATE_ENV),
        _switch_finding(LIVE_TESTS_ENV),
        _label_finding(),
        _endpoint_finding("FALKOR_TEST_HOST", host_is_local),
        _port_finding("FALKOR_TEST_PORT"),
        _endpoint_finding("QDRANT_TEST_URL", url_is_local),
        _endpoint_finding("REDIS_TEST_URL", url_is_local),
        _credential_finding(
            ("QDRANT_API_KEY", "QDRANT_TEST_API_KEY"),
            mandatory=True,
            checkout_fallbacks=qdrant_checkout_secret_candidates(),
        ),
    ]

    mongo = _endpoint_finding("DATABASE_URL", url_is_local)
    if not mongo.ok and _env("MONGODB_URI"):
        mongo = _endpoint_finding("MONGODB_URI", url_is_local)
    findings.append(mongo)

    findings.extend(
        [
            _credential_finding(("OPENAI_API_KEY",), mandatory=False),
            _credential_finding(("AWS_ACCESS_KEY_ID",), mandatory=False),
            _credential_finding(("AWS_SECRET_ACCESS_KEY",), mandatory=False),
            _endpoint_finding("AWS_BUCKET_NAME", lambda _v: False, mandatory=False),
            _endpoint_finding("BACKUP_S3_BUCKET", lambda _v: False, mandatory=False),
        ]
    )
    return findings


def render_report(findings: Optional[Sequence[PreflightFinding]] = None) -> str:
    """A status table. Contains no secret value, by construction."""
    rows = list(findings if findings is not None else preflight_findings())
    width = max((len(f.name) for f in rows), default=0)
    lines = ["Staging Gate-2 preflight:"]
    for finding in rows:
        tier = "required" if finding.mandatory else "optional"
        lines.append(f"  {finding.name.ljust(width)}  {finding.status:<14} ({tier}) {finding.detail}")
    return "\n".join(lines)


def assert_staging_preflight() -> None:
    """Fail the run before collection if staging mode is not safely configured."""
    if not staging_gate_mode():
        return
    findings = preflight_findings()
    failures = [f for f in findings if f.mandatory and not f.ok]
    if not failures:
        return
    raise StagingGateConfigurationError(
        "staging Gate-2 mode is enabled but the environment is not safe to "
        "measure evidence with. Every endpoint and live credential must be "
        "supplied explicitly, and none may address this development machine.\n"
        + render_report(findings)
    )


# --------------------------------------------------------------------------- #
# Skip-is-failure, for the suites Gate 2 scores
# --------------------------------------------------------------------------- #
#
# Same mechanism as the G29/G30/G31/G32 certification modes in
# `authority_band_graph.py`: a required suite that skips proves nothing, so
# under this flag a skip inside one of these files is reported as a failure.
# Gate 2 bullet 5 is the reason the Redis module is listed: R-A6 recorded that
# it had no evidence-eligible artefact at all, so its absence — or its silent
# skip — must be loud.

#: The live modules Gate 2's six bullets are scored from.
GATE2_REQUIRED_LIVE_FILES = (
    # bullets 1, 2 and 3 — the master switch, the OpenAI round trip, and the
    # Qdrant/LangChain vector round trip all live in this module.
    "backend/rbac_backend/tests/integration/test_external_services_integration.py",
    # bullet 3 — Qdrant containment against a real engine.
    "backend/rbac_backend/tests/integration/test_qdrant_containment_live.py",
    # bullet 4 — FalkorDB graph round trip, disposable namespace only.
    "backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py",
    # bullet 5 — Redis queue and runtime-state paths.
    "backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py",
)


def _normalise(path: str) -> str:
    return str(path).replace("\\", "/")


def is_gate2_required_file(path: str) -> bool:
    """Does this test file carry evidence one of the Gate 2 bullets is scored from?"""
    candidate = _normalise(path)
    for required in GATE2_REQUIRED_LIVE_FILES:
        required_norm = _normalise(required)
        if candidate == required_norm or candidate.endswith("/" + required_norm):
            return True
    return False


def staging_skip_is_gate_failure(path: str, outcome: str) -> bool:
    """The whole decision, as one pure function — the shape the other gates use."""
    if outcome != "skipped":
        return False
    if not staging_gate_mode():
        return False
    return is_gate2_required_file(path)


def require_live_environment(*names: str, skip: Callable[[str], None], fail: Callable[[str], None]) -> None:
    """Gate a live test on its environment.

    Outside staging mode a missing variable skips, which is correct for
    development. Inside it, a skip is the outcome the mode exists to prevent,
    so the same condition fails instead.
    """
    if not live_tests_enabled():
        message = f"Set {LIVE_TESTS_ENV}=1 to run live external integration tests."
        if staging_gate_mode():
            fail(message)
            return
        skip(message)
        return

    missing = [name for name in names if not _env(name)]
    if not missing:
        return
    message = f"Missing required integration environment variables: {', '.join(missing)}"
    if staging_gate_mode():
        fail(
            f"{message}. {STAGING_GATE_ENV} is set, so this run is staging "
            "Gate-2 evidence and a required live variable may not be skipped over."
        )
        return
    skip(message)
