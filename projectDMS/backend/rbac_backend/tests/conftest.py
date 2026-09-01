"""Certification modes: a required suite may not skip (GRAPH-GATES U5).

Four independent switches share one mechanism - `G31_WRITER_CERTIFICATION`,
`G30_READER_CERTIFICATION`, `G32_STATE_REHEARSAL` and
`G29_UMBRELLA_CERTIFICATION` - each over its own required-file set. They are
deliberately not one flag: a certification claim must be exactly as wide as the
evidence behind it, and the umbrella's set is wider than any predecessor's
precisely because what it asserts is a property of the four composed.

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

    report.outcome = "failed"
    report.longrepr = (
        f"{env_var} is set, so this run is {gate} certification "
        f"evidence and a required {subject} suite may not skip.\n"
        f"{item.nodeid} skipped: {reason}\n"
        "A skipped Falkor suite proves nothing. Start FalkorDB (default "
        "localhost:6380, or set FALKOR_TEST_HOST / FALKOR_TEST_PORT) and re-run, "
        f"or drop {env_var} and stop calling the run certification "
        "evidence."
    )
    # `wasxfail` is deliberately NOT set here. Setting it - even to None - makes
    # `hasattr(report, "wasxfail")` true, and pytest's session then declines to
    # count the failure, so the run reported errors and still EXITED ZERO. A
    # conversion that does not change the exit code closes nothing.
