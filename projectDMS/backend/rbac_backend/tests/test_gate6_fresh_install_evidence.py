"""Gate 6 bullet 2's tick is inherited from a staging run; keep it honest.

The bullet - "fresh install path is tested against real MongoDB" - was closed in
R-A8S by reading the R-A8Q Stage B measurements against the wording, not by
running anything new. That is a legitimate way to close a bullet and a dangerous
one to leave unattended, for two reasons this module guards.

**The evidence has to be readable.** R-A8Q's receipt lives under `.claude/`,
which `projectDMS/.gitignore` excludes, so it does not survive a clone. A gate
that cites evidence a reviewer cannot open is citing nothing.
`docs/GATE_6_FRESH_INSTALL_EVIDENCE.md` is the tracked mapping, and the tick and
the mapping have to agree.

**The evidence has to still apply.** The run measured **19 migrations** applied
to an empty database. A twentieth migration has never been applied to one. The
tick therefore expires when the migration catalogue moves, which forces it to be
re-earned rather than silently inherited by work the run never saw. This is the
same rule the FalkorDB supersession uses - re-derive the load-bearing fact on
every run, and fail the moment it stops holding.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RBAC_BACKEND = Path(__file__).resolve().parents[1]
PROJECT = RBAC_BACKEND.parents[1]
GATE_FILE = PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"
EVIDENCE = PROJECT / "docs" / "GATE_6_FRESH_INSTALL_EVIDENCE.md"
MIGRATIONS = RBAC_BACKEND / "migrations"

#: What R-A8Q Stage B actually applied to the empty `contraclaim_staging`
#: database on 2026-09-08. Not a target - a measurement.
MIGRATIONS_PROVEN_ON_A_FRESH_DATABASE = 19


def _gate6_bullets():
    text = GATE_FILE.read_text(encoding="utf-8")
    section = re.search(r"^### Gate 6:.*?$(.*?)^### Gate 7:", text, re.S | re.M)
    assert section, "the Gate 6 section is gone from the release gate document"
    return re.findall(r"^- \[([ xX])\] (.+)$", section.group(1), re.M)


def _fresh_install_bullet():
    for checked, text in _gate6_bullets():
        if "fresh install" in text.lower():
            return checked.lower() == "x", text
    raise AssertionError("Gate 6 no longer has a fresh-install bullet")


def _registered_migrations() -> int:
    """The catalogue the runner enumerates - not a filename glob over it.

    This counted `MIGRATIONS.glob("v*.py")`, which is a proxy and not the thing.
    R-A8T's review found the tell: the exclusion set `{__init__.py, catalog.py,
    runner.py}` can never match `v*.py`, so the glob and the intent behind it
    were written at different times. A twentieth migration whose module name did
    not begin with `v` would leave the pin unmoved and let the Gate 6 tick be
    inherited by a migration the fresh-database run never saw - which is exactly
    what this pin exists to prevent.

    `catalog.MIGRATIONS` is what `python -m rbac_backend.scripts.migrate_database
    --list` walks, so it is the only count that can expire the tick correctly.
    """
    from rbac_backend.migrations import catalog  # noqa: PLC0415

    return len(catalog.MIGRATIONS)


def test_the_catalogue_and_the_modules_on_disk_agree() -> None:
    """Anti-vacuity for the count above, in both directions.

    If the catalogue and the migration modules ever disagree, one of them is
    lying about what a fresh database would receive, and the number this file
    pins would be measuring the wrong population.
    """
    on_disk = {
        path.stem
        for path in MIGRATIONS.glob("v*.py")
        if path.name not in {"__init__.py", "catalog.py", "runner.py"}
    }
    assert on_disk, "no migration modules found; the glob no longer matches anything"
    assert len(on_disk) == _registered_migrations(), (
        f"{len(on_disk)} migration modules on disk but "
        f"{_registered_migrations()} entries in catalog.MIGRATIONS - one of them "
        "is not what the runner applies"
    )


def test_gate6_still_has_six_bullets() -> None:
    """R-A8S ticked a bullet; it must not have added or removed one.

    The readiness score is `checked / total` per gate, so a denominator change
    moves the score without evidence.
    """
    assert len(_gate6_bullets()) == 6


def test_the_migration_catalogue_is_the_one_the_staging_run_measured() -> None:
    """The tick expires when a migration lands that has never seen an empty database."""
    registered = _registered_migrations()
    assert registered == MIGRATIONS_PROVEN_ON_A_FRESH_DATABASE, (
        f"the migration catalogue now has {registered} migrations; R-A8Q Stage B "
        f"applied {MIGRATIONS_PROVEN_ON_A_FRESH_DATABASE} to a fresh database on "
        "2026-09-08. Gate 6 bullet 2's tick covers what that run measured and "
        "nothing after it - re-run the fresh-install path against a real MongoDB, "
        "record it in docs/GATE_6_FRESH_INSTALL_EVIDENCE.md, and update this "
        "number. Do not update the number alone."
    )


def test_the_tick_and_the_evidence_document_agree() -> None:
    checked, bullet = _fresh_install_bullet()
    if not checked:
        pytest.skip("Gate 6 bullet 2 is unticked; there is nothing to keep honest")

    assert EVIDENCE.is_file(), (
        f"Gate 6 bullet 2 is ticked and {EVIDENCE.name} does not exist"
    )
    assert "GATE_6_FRESH_INSTALL_EVIDENCE.md" in bullet, (
        "the ticked bullet does not name the tracked evidence mapping, so a "
        f"reviewer has nothing to open: {bullet!r}"
    )

    text = EVIDENCE.read_text(encoding="utf-8")
    assert "SATISFIED" in text
    assert "R-A8Q" in text


def test_the_evidence_document_does_not_claim_the_upgrade_path() -> None:
    """A fresh install and a restored production copy are different surfaces.

    Conflating them is how a migration that only works on an empty database
    reaches production. Bullet 3 stays open on its own evidence.
    """
    text = EVIDENCE.read_text(encoding="utf-8")
    assert "not** a claim about Gate 6 bullet 3" in text or (
        "bullet 3" in text.lower() and "still open" in text.lower()
    ), "the evidence document no longer distinguishes itself from the upgrade path"

    upgrade_checked, upgrade_bullet = next(
        (
            (checked.lower() == "x", text)
            for checked, text in _gate6_bullets()
            if "upgrade path" in text.lower()
        ),
        (False, ""),
    )
    if not upgrade_checked:
        return
    # R-A8W measured the upgrade path against a restored production copy. The
    # tick is accepted only on its own evidence document, never on this one.
    assert "GATE_6_FRESH_INSTALL_EVIDENCE.md" not in upgrade_bullet, (
        "Gate 6 bullet 3 cites the fresh-install evidence; a restored production "
        "copy needs its own evidence document"
    )
    upgrade_evidence = EVIDENCE.with_name("GATE_6_UPGRADE_PATH_EVIDENCE.md")
    assert "GATE_6_UPGRADE_PATH_EVIDENCE.md" in upgrade_bullet and upgrade_evidence.is_file(), (
        "Gate 6 bullet 3 is ticked without naming an existing "
        "GATE_6_UPGRADE_PATH_EVIDENCE.md, so a reviewer has nothing to open"
    )
    upgrade_text = upgrade_evidence.read_text(encoding="utf-8")
    assert "SATISFIED" in upgrade_text and "R-A8W" in upgrade_text
    assert "restored" in upgrade_text.lower() and "rsstg" in upgrade_text


def test_the_entry_point_the_evidence_names_still_exists() -> None:
    """The evidence cites `migrate_database`; a renamed script invalidates it."""
    assert (RBAC_BACKEND / "scripts" / "migrate_database.py").is_file(), (
        "the migration entry point Gate 6 bullet 2's evidence names is gone"
    )
    text = EVIDENCE.read_text(encoding="utf-8")
    assert "rbac_backend.scripts.migrate_database" in text


def test_the_evidence_names_a_measurement_for_every_requirement() -> None:
    """The mapping is a table with a verdict per row; an empty verdict is a gap."""
    rows = [
        line
        for line in EVIDENCE.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ") and re.match(r"^\|\s*\d+\s*\|", line)
    ]
    assert len(rows) >= 6, f"the evidence mapping has only {len(rows)} requirement rows"
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 5, f"malformed mapping row: {row}"
        assert cells[2], f"requirement {cells[0]} names no evidence"
        assert cells[4] in {"**YES**", "**NO**", "**PARTIAL**"}, (
            f"requirement {cells[0]} has no verdict: {cells[4]!r}"
        )
    verdicts = {
        [cell.strip() for cell in row.strip().strip("|").split("|")][4] for row in rows
    }
    assert verdicts == {"**YES**"}, (
        f"the bullet is ticked but the mapping records {sorted(verdicts)}"
    )
