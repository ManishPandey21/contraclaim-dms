"""The runbook is where the R-A8P failure actually lived.

The time gate could exist in `scripts/` and be perfectly tested and still not
prevent anything, because the operator follows the runbook. These are static
checks on the document and on the two scripts it now calls: they cannot prove a
window goes well, but they do prove the gate is written down, that it sits
before the stop rather than after it, and that the archive verification the
runbook prescribes is the corrected one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = REPO_ROOT / "docs" / "SAME_HOST_STAGING_MAINTENANCE_WINDOW.md"
TIME_GATE = REPO_ROOT / "scripts" / "check_maintenance_time_budget.py"
RESCUE = REPO_ROOT / "scripts" / "falkordb_rescue_archive.sh"
BACKUP_VOLUME = REPO_ROOT / "scripts" / "backup_volume.sh"
VALIDATOR = REPO_ROOT / "scripts" / "validate_backup_archive.py"


@pytest.fixture(scope="module")
def runbook() -> str:
    assert RUNBOOK.is_file(), f"the maintenance runbook is missing at {RUNBOOK}"
    return RUNBOOK.read_text(encoding="utf-8")


def _working_bash() -> str | None:
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=30)
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    return None


BASH = _working_bash()


# --------------------------------------------------------------- F1, in prose


def test_the_runbook_names_the_time_budget_gate(runbook: str) -> None:
    assert "check_maintenance_time_budget.py" in runbook, (
        "the runbook must call the time-budget gate; R-A8P's stop was authorised by a "
        "24-item GO in which not one item was time-valued"
    )


def test_the_runbook_states_both_halves_of_the_safety_contract(runbook: str) -> None:
    assert "NOW + EXPECTED_EXECUTION_BUDGET + RECOVERY_RESERVE" in runbook
    assert "NOW < HARD_RECOVERY_START" in runbook
    assert "LATEST_SAFE_STOP" in runbook


def test_the_runbook_requires_two_gates_and_puts_the_second_before_the_stop(runbook: str) -> None:
    """Order is the property. A gate after the stop is a post-mortem."""
    assert "TIME-BUDGET GATE #1" in runbook
    assert "TIME-BUDGET GATE #2" in runbook

    gate_two = runbook.index("TIME-BUDGET GATE #2")
    stop_command = runbook.index("$PROD stop gateway client backend contract-worker", gate_two)
    assert gate_two < stop_command, "gate #2 must be read before the application tier is stopped"


def test_the_runbook_refuses_a_stale_go_in_so_many_words(runbook: str) -> None:
    assert "was green earlier" in runbook, (
        "the runbook has to say that a previously computed GO is not evidence, because "
        "that is exactly the reasoning that produced a stop seven minutes into the reserve"
    )


def test_the_runbook_carries_the_measured_execution_budget_and_its_provenance(runbook: str) -> None:
    assert "59 minutes 23 seconds" in runbook, "the budget must name the measurement it derives from"
    assert "120 minutes" in runbook, "the budget must state the planning value"
    assert "Do not plan with 59" in runbook


def test_the_time_gate_script_exists_and_compiles() -> None:
    assert TIME_GATE.is_file()
    subprocess.run(["python", "-m", "py_compile", str(TIME_GATE)], check=True, capture_output=True)


# ------------------------------------------------------------ F2/F3/F4, in prose


def test_the_runbook_no_longer_verifies_with_bare_patterns(runbook: str) -> None:
    """The invocation that read as a conjunction and behaved as a disjunction."""
    assert "--verify \"$ARCH\" '*dump.rdb' '*appendonlydir*'" not in runbook


def test_the_runbook_verifies_the_rescue_archive_through_the_shared_profile(runbook: str) -> None:
    assert "--profile redis-persistence" in runbook


def test_the_runbook_delegates_the_rescue_capture_to_the_helper(runbook: str) -> None:
    assert "scripts/falkordb_rescue_archive.sh" in runbook
    assert "docker cp contraclaim-falkordb-1:/FalkorDB/appendonlydir" not in runbook, (
        "the hand-typed recipe is what produced R-A8O's archive rooted at FalkorDB/"
    )


def test_the_runbook_says_size_is_not_evidence(runbook: str) -> None:
    # Prose wraps, so the sentence is matched with its line breaks collapsed.
    flattened = " ".join(runbook.lower().split())
    assert "archive size is not evidence that an archive restores" in flattened


def test_the_runbook_drops_the_ok_vocabulary_for_backup_artifacts(runbook: str) -> None:
    assert "never `ok`" in runbook


# ------------------------------------------------------------- the two scripts


@pytest.mark.skipif(BASH is None, reason="a working bash is required")
@pytest.mark.parametrize("script", [RESCUE, BACKUP_VOLUME])
def test_the_shell_scripts_parse(script: Path) -> None:
    assert BASH is not None
    result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", [RESCUE, BACKUP_VOLUME])
def test_the_shell_scripts_stop_on_error(script: Path) -> None:
    assert "set -euo pipefail" in script.read_text(encoding="utf-8")


def test_the_rescue_helper_reads_the_persistence_directory_rather_than_assuming_it() -> None:
    """It has to keep working after the cutover moves persistence to /data."""
    text = RESCUE.read_text(encoding="utf-8")

    assert "CONFIG GET dir" in text
    assert "persistence_dir" in text


def test_the_rescue_helper_archives_from_the_persistence_root() -> None:
    """`tar -C "$staging" .` is the whole fix: entries land at ./, not under a prefix."""
    text = RESCUE.read_text(encoding="utf-8")

    assert 'tar -czf "$archive_dir/$archive_name" -C "$staging" .' in text
    assert '"$staging/$member"' not in text or 'native_path "$staging"' in text


def test_the_rescue_helper_validates_before_it_exits_zero() -> None:
    text = RESCUE.read_text(encoding="utf-8")

    assert "backup_volume.sh" in text and "--profile redis-persistence" in text, (
        "a rescue archive that has not been validated is not a rescue archive"
    )


def test_the_rescue_helper_refuses_when_there_is_nothing_to_rescue() -> None:
    text = RESCUE.read_text(encoding="utf-8")

    assert "an empty archive is worse than none" in text


def test_backup_volume_delegates_to_the_one_validator() -> None:
    """Four definitions of "valid archive" is how they drifted. There is one."""
    text = BACKUP_VOLUME.read_text(encoding="utf-8")

    assert "validate_backup_archive.py" in text
    assert VALIDATOR.is_file()


def test_backup_volume_fails_closed_without_an_interpreter() -> None:
    text = BACKUP_VOLUME.read_text(encoding="utf-8")

    assert "an unevaluated contract is" in text
    assert "not a pass" in text
