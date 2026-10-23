"""Certification modes: a required suite may not skip (GRAPH-GATES U5).

Five independent switches share one mechanism - `G31_WRITER_CERTIFICATION`,
`G30_READER_CERTIFICATION`, `G32_STATE_REHEARSAL`, `G29_UMBRELLA_CERTIFICATION`
and `CONTRACLAIM_STAGING_GATE` - each over its own required-file set. They are
deliberately not one flag: a certification claim must be exactly as wide as the
evidence behind it, and the umbrella's set is wider than any predecessor's
precisely because what it asserts is a property of the four composed. The fifth
is the release programme's staging gate, over the four live modules Gate 2's six
bullets are scored from.

This module also runs the staging preflight in `pytest_configure`, before
anything is collected, so a staging run whose endpoints are absent or point at
the development machine stops with one status table instead of importing suites
that would quietly measure the wrong engine.

Every Falkor suite calls `pytest.skip` when the engine is unreachable. That is
correct for ordinary development and unacceptable for a certification run: it
lets a GREEN result carry zero real-infrastructure evidence, which
`GRAPH-GATES.md` names as invalid closure evidence for G29/G31 alike.

This hook is the smallest mechanism that closes it. It changes nothing unless
`G31_WRITER_CERTIFICATION=1` is set. When it is, a skipped test inside one of
`authority_band_graph.G31_REQUIRED_WRITER_FILES` is reported as a FAILURE
instead, so a certification invocation cannot be green while proving nothing.

Deliberately NOT done here:

* no accepted regression file is edited and no `pytest.skip` block is removed -
  the developer workflow must keep skipping when Falkor is absent;
* no test framework redesign - the decision is one pure function
  (`g31_skip_is_certification_failure`) that this hook only applies.
"""

from __future__ import annotations

import pytest

from rbac_backend.tests.authority_band_graph import (
    G29_UMBRELLA_CERTIFICATION_ENV,
    G30_CERTIFICATION_ENV,
    G31_CERTIFICATION_ENV,
    G32_STATE_REHEARSAL_ENV,
    g29_umbrella_skip_is_certification_failure,
    g30_skip_is_certification_failure,
    g31_skip_is_certification_failure,
    g32_state_skip_is_rehearsal_failure,
)
from rbac_backend.tests.staging_gate import (
    STAGING_GATE_ENV,
    StagingGateConfigurationError,
    assert_staging_preflight,
    gate2_withdrawn_from_certification,
    staging_gate_mode,
    staging_skip_is_gate_failure,
)

#: Node ids deselected as withdrawn Gate-2 criteria, carried from the collection
#: hook to the terminal summary. A `config.stash` key rather than a module global
#: so `-p xdist` workers cannot share one list.
_WITHDRAWN_STASH = pytest.StashKey[list]()

#: One hook, three independent gates. Each entry is (flag, decision, subject),
#: and the FIRST match wins - a file in more than one required set is reported
#: under whichever certification is actually running, and if several flags are
#: set the message names the first. They are separate switches over separate
#: sets on purpose: see `authority_band_graph.G30_REQUIRED_READER_FILES` and
#: `authority_band_graph.G32_STATE_REQUIRED_FILES`.
_CERTIFICATION_GATES = (
    (G31_CERTIFICATION_ENV, g31_skip_is_certification_failure, "G31 writer", "writer"),
    (G30_CERTIFICATION_ENV, g30_skip_is_certification_failure, "G30 reader", "reader"),
    (
        G32_STATE_REHEARSAL_ENV,
        g32_state_skip_is_rehearsal_failure,
        "G32-STATE migration",
        "migration rehearsal",
    ),
    (
        G29_UMBRELLA_CERTIFICATION_ENV,
        g29_umbrella_skip_is_certification_failure,
        "G29 umbrella",
        "umbrella end-to-end",
    ),
    # The fifth switch, and the same reasoning one more time. Gate 2 scores six
    # bullets from four live modules; a skipped one is a bullet that cannot be
    # ticked and a run that looks green while measuring nothing. Bullet 5's
    # Redis module is in that set precisely because R-A6 found it missing
    # entirely - its silence must be loud.
    (
        STAGING_GATE_ENV,
        staging_skip_is_gate_failure,
        "staging Gate-2",
        "live external",
    ),
)


def pytest_configure(config):
    """Refuse an unsafe staging Gate-2 environment before anything is collected.

    Deliberately here rather than in a fixture: the live modules resolve their
    endpoints at import time, so a missing or localhost endpoint would surface
    as a wall of collection errors. One readable status table, before
    collection, is the whole point.
    """
    del config  # the decision is entirely environmental
    try:
        assert_staging_preflight()
    except StagingGateConfigurationError as exc:
        # Re-raised as a UsageError so the operator gets the status table and
        # nothing else. Letting the original propagate out of a hook makes
        # pytest print INTERNALERROR with a pluggy traceback above the report,
        # which buries the one thing they need to read. Both exit non-zero; only
        # one of them is legible.
        raise pytest.UsageError(str(exc)) from exc


def pytest_collection_modifyitems(config, items):
    """Deselect the Gate-2 criteria the gate document has formally withdrawn.

    Only under `CONTRACLAIM_STAGING_GATE`. Required Gate-2 membership is decided
    per file, so a module carrying three live bullets also carried
    `test_falkordb_vector_service_round_trip_live`, whose criterion was
    superseded on 2026-09-04 and whose `FT.CREATE` the production FalkorDB pin
    does not implement. R-A8Q's certification run was 44/45 for exactly that
    reason — F-A8Q-1.

    Deselected, not skipped and not silenced:

    * a skip inside a required module is converted to a FAILURE two hooks below,
      which is the correct rule and the wrong answer here — the test is not
      unmeasured, it is not asked for;
    * an `xfail` would report a result for a requirement that no longer exists;
    * outside this mode nothing is removed at all, so the legacy coverage stays.

    The deselection is announced in the terminal summary. A gate that quietly
    drops a test is the failure mode this whole module exists to prevent.
    """
    if not staging_gate_mode():
        return

    withdrawn = [item for item in items if gate2_withdrawn_from_certification(item.nodeid)]
    if not withdrawn:
        return

    remaining = [item for item in items if item not in withdrawn]
    config.hook.pytest_deselected(items=withdrawn)
    items[:] = remaining
    config.stash[_WITHDRAWN_STASH] = [item.nodeid for item in withdrawn]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say which Gate-2 criteria were withdrawn, and where the decision lives."""
    del exitstatus
    nodeids = config.stash.get(_WITHDRAWN_STASH, [])
    if not nodeids:
        return
    terminalreporter.write_sep("-", "staging Gate-2: withdrawn criteria")
    for nodeid in nodeids:
        terminalreporter.write_line(f"  deselected {nodeid}")
    terminalreporter.write_line(
        "  Superseded by docs/PRODUCTION_READINESS_RELEASE_GATE.md "
        '("Superseded requirement: FalkorDB vector round trip", 2026-09-04). '
        "The inventory is staging_gate.GATE2_WITHDRAWN_LIVE_TESTS and "
        "test_gate2_required_inventory.py is what stops it drifting."
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    for env_var, decide, gate, subject in _CERTIFICATION_GATES:
        if decide(str(item.fspath), report.outcome):
            break
    else:
        return

    reason = ""
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        reason = str(report.longrepr[2])
    elif report.longrepr is not None:
        reason = str(report.longrepr)

    # The remedy differs by gate. The four graph certifications want a
    # reachable local FalkorDB; the staging gate wants explicitly supplied
    # staging endpoints, and naming localhost there would be advice to do the
    # exact thing that gate exists to forbid.
    if env_var == STAGING_GATE_ENV:
        remedy = (
            "A skipped live suite proves nothing. Supply every staging endpoint "
            "and live credential explicitly (the preflight report names each one "
            f"by status), or drop {env_var} and stop calling the run staging "
            "evidence."
        )
    else:
        remedy = (
            "A skipped Falkor suite proves nothing. Start FalkorDB (default "
            "localhost:6380, or set FALKOR_TEST_HOST / FALKOR_TEST_PORT) and "
            f"re-run, or drop {env_var} and stop calling the run certification "
            "evidence."
        )

    report.outcome = "failed"
    report.longrepr = (
        f"{env_var} is set, so this run is {gate} certification "
        f"evidence and a required {subject} suite may not skip.\n"
        f"{item.nodeid} skipped: {reason}\n" + remedy
    )
    # `wasxfail` is deliberately NOT set here. Setting it - even to None - makes
    # `hasattr(report, "wasxfail")` true, and pytest's session then declines to
    # count the failure, so the run reported errors and still EXITED ZERO. A
    # conversion that does not change the exit code closes nothing.
