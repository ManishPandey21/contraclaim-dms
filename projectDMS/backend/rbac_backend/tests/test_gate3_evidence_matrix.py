"""A Gate 3 bullet may not be ticked without staging-executable evidence.

R-A8I ran 33 of 33 Playwright tests green against staging and earned no Gate 3
checkbox, and the reverse failure is the more dangerous one: nine bullets, one of
them already ticked, and nothing in the repository connecting any of them to a
test that could establish it. The one bullet that was ticked carries no
`evidence:` reference at all, and the only spec covering its subject intercepts
`**/api/**`.

Gate 2 already has this protection - `test_release_gate_specification.py` rejects
a checked bullet with no evidence reference, and rejects a reference naming a path
that does not exist - but it validates Gate 2 alone. This is the same rule for
Gate 3, plus the part Gate 2 does not need: a Gate 3 bullet's evidence must be
capable of running against a **deployment**, and a mocked suite is not.

`docs/GATE_3_EVIDENCE_MATRIX.md` is the declaration. This module is the check that
the declaration and the gate document cannot drift apart, and that neither can
claim coverage from an artefact that is not there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"
MATRIX = REPO_ROOT / "docs" / "GATE_3_EVIDENCE_MATRIX.md"
PLAYWRIGHT_CONFIG = REPO_ROOT / "client" / "playwright.config.ts"

EXECUTABLE = "EXECUTABLE"
MOCKED = "MOCKED"
MISSING = "MISSING"
MANUAL = "MANUAL"
EXECUTABLE_PARTIAL = "EXECUTABLE_PARTIAL"
STATUSES = {EXECUTABLE, EXECUTABLE_PARTIAL, MOCKED, MISSING, MANUAL}


def _gate3_bullets() -> list:
    text = GATE.read_text(encoding="utf-8")
    section = re.search(r"^### Gate 3:.*?$(.*?)^### Gate 4:", text, re.S | re.M)
    assert section, "the Gate 3 section is gone from the release gate document"
    return re.findall(r"^- \[([ x])\] (.+)$", section.group(1), re.M)


def _matrix_rows() -> list:
    text = MATRIX.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not cells[0].isdigit():
            continue
        rows.append(
            {
                "number": int(cells[0]),
                "bullet": cells[1],
                "property": cells[2],
                "evidence": cells[3],
                "status": cells[4].strip("`"),
                "gap": cells[5],
            }
        )
    return rows


def _artefacts(cell: str) -> list:
    """The repository paths a matrix row names, ignoring any prose around them."""
    return [
        match.strip("`")
        for match in re.findall(r"`([^`]*/[^`]*\.(?:ts|py|md|sh))`", cell)
    ]


@pytest.fixture(scope="module")
def rows() -> list:
    assert MATRIX.is_file(), f"the Gate 3 evidence matrix is missing at {MATRIX}"
    return _matrix_rows()


# --------------------------------------------------------------------------- #
# The matrix covers the gate, exactly
# --------------------------------------------------------------------------- #


def test_every_gate3_bullet_has_exactly_one_matrix_row(rows: list) -> None:
    bullets = _gate3_bullets()
    assert bullets, "no Gate 3 bullets found"

    assert len(rows) == len(bullets), (
        f"the matrix has {len(rows)} rows for {len(bullets)} Gate 3 bullets; a bullet "
        "with no row is a bullet nobody has decided how to evidence"
    )
    assert [row["number"] for row in rows] == list(range(1, len(bullets) + 1))

    for (_, bullet), row in zip(bullets, rows):
        # Compared on the leading words rather than the whole line: the gate text
        # gains `evidence:` suffixes as bullets are earned, and the matrix should
        # not have to be re-edited for that.
        head = " ".join(bullet.split()[:4]).rstrip(".,").lower()
        assert head in row["bullet"].lower(), (
            f"matrix row {row['number']} does not describe gate bullet "
            f"{row['number']}: {head!r} vs {row['bullet']!r}"
        )


def test_every_status_is_one_of_the_declared_values(rows: list) -> None:
    unknown = {row["status"] for row in rows} - STATUSES
    assert not unknown, f"undeclared coverage status: {sorted(unknown)}"


def test_every_named_artefact_exists(rows: list) -> None:
    missing = {}
    for row in rows:
        for artefact in _artefacts(row["evidence"]):
            if not (REPO_ROOT / artefact).exists():
                missing.setdefault(row["number"], []).append(artefact)

    assert not missing, (
        f"the matrix claims evidence from artefacts that do not exist: {missing}"
    )


def test_a_row_claiming_executable_evidence_names_an_artefact(rows: list) -> None:
    empty = [
        row["number"]
        for row in rows
        if row["status"] in {EXECUTABLE, EXECUTABLE_PARTIAL} and not _artefacts(row["evidence"])
    ]
    assert not empty, (
        f"rows {empty} claim executable evidence and name no artefact, which is a "
        "claim nothing can be checked against"
    )


# --------------------------------------------------------------------------- #
# The tick rule
# --------------------------------------------------------------------------- #


def test_no_bullet_is_ticked_without_executable_evidence(rows: list) -> None:
    """The rule Gate 2 already has, plus the part only Gate 3 needs.

    A mocked suite proves the component renders its own states. It cannot prove
    the deployment does anything, and Gate 3's own preamble says every bullet is
    satisfied only by a run against a real staging environment.
    """
    offenders = []
    for (checked, bullet), row in zip(_gate3_bullets(), rows):
        if checked != "x":
            continue
        if row["status"] != EXECUTABLE:
            offenders.append(
                f"bullet {row['number']} is ticked but its evidence is {row['status']}: {bullet!r}"
            )
            continue
        reference = re.search(r"evidence:\s*(\S+)", bullet)
        if reference is None:
            offenders.append(f"bullet {row['number']} is ticked with no evidence reference")
            continue
        path = reference.group(1).strip("`.,;")
        if "/" in path and not (REPO_ROOT / path).exists():
            offenders.append(f"bullet {row['number']} names a path that does not exist: {path}")

    assert not offenders, "\n".join(offenders)


# --------------------------------------------------------------------------- #
# The suite can actually reach a deployment
# --------------------------------------------------------------------------- #


def test_the_playwright_config_can_target_a_deployed_stack() -> None:
    """The reason R-A8I's 33 green tests earned nothing.

    `baseURL` was the literal dev server and `webServer` always started Vite, so
    reaching staging needed a config file created for the run and deleted after -
    which means the evidence was produced by an artefact not in the repository.
    """
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    assert "E2E_BASE_URL" in config, (
        "the Playwright config cannot be pointed at a deployment, so no Gate 3 "
        "bullet can be measured against one"
    )
    assert re.search(r"webServer:\s*\w*\s*\?\s*undefined", config) or "? undefined" in config, (
        "the config still starts a dev server unconditionally; a webServer "
        "alongside an external target is how a run measures localhost while "
        "reporting a staging URL"
    )


def test_the_mocked_suites_are_excluded_when_targeting_a_deployment() -> None:
    """Pointed at staging they would assert on their own fixtures.

    That is not a harmless no-op: it is 33 green tests that look like deployment
    evidence, which is precisely the reading R-A8I had to argue against.
    """
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    assert "testIgnore" in config, "no suite is excluded when targeting a deployment"
    for mocked in ("contract-workflows.spec.ts", "contract-master.spec.ts"):
        assert mocked in config, f"{mocked} mocks the API and is not excluded"


def test_the_staging_suite_fails_rather_than_skips_under_its_own_switch() -> None:
    """Same rule as the backend's Gate-2 harness, for the same reason.

    A live suite that skips on a missing variable reports green having measured
    nothing. Gate 2 learned this in R-A6 and R-A8I; Gate 3 must not have to.
    """
    helper = REPO_ROOT / "client" / "e2e" / "staging" / "staging-target.ts"
    assert helper.is_file(), "the staging Gate 3 suite has no environment gate"

    source = helper.read_text(encoding="utf-8")
    assert "CONTRACLAIM_STAGING_E2E" in source, "there is no strict switch"
    assert "throw new Error" in source, (
        "a missing staging variable still skips under the strict switch, which is "
        "the outcome the switch exists to prevent"
    )
