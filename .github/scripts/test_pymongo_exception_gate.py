"""Tests for the R4 PyMongo exception gate (.github/scripts/pymongo_exception_gate.py).

No network: PyPI answers are synthetic. The live run happens in CI's
dependency-scan job, after these pass.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymongo_exception_gate as gate  # noqa: E402

TODAY = dt.date(2026, 10, 6)
REQS = "motor==3.7.0\npymongo==4.16.0\nlanggraph-checkpoint-mongodb==0.4.0\n"


def _pypi(releases: dict[str, str | None], yanked: tuple[str, ...] = ()):
    """A fake fetcher: releases maps version -> pymongo requirement (None = none)."""

    def fetch(url: str):
        if url.endswith(f"/{gate.CHECKPOINT_PACKAGE}/json"):
            return {"releases": {v: [{"yanked": v in yanked}] for v in releases}}
        version = url.rstrip("/").split("/")[-2]
        requirement = releases[version]
        requires = ["langgraph-checkpoint>=3.0.0", 'pymongo[srv]>=4.0; extra == "test"']
        if requirement is not None:
            requires.append(requirement)
        return {"info": {"requires_dist": requires}}

    return fetch


#: PyPI as observed on 2026-10-06.
CURRENT_PYPI = {
    "0.3.1": "pymongo<4.16,>=4.12",
    "0.4.0": "pymongo<4.17,>=4.12",
    "0.5.0": "pymongo<4.18,>=4.12",
}


def _repo(tmp_path: Path, requirements: str = REQS) -> Path:
    target = tmp_path / gate.REQUIREMENTS
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(requirements, encoding="utf-8")
    return tmp_path


def test_the_exception_holds_today_for_exactly_the_four_reviewed_ids(tmp_path):
    message = gate.run_gate(_repo(tmp_path), today=TODAY, fetch=_pypi(CURRENT_PYPI))
    assert "in force" in message
    assert gate.ADVISORIES == ("CVE-2026-88029", "CVE-2026-96747", "CVE-2026-96748", "CVE-2026-96749")
    assert set(gate.TRIVY_ADVISORIES) <= set(gate.ADVISORIES)


def test_the_last_day_still_applies_and_the_next_day_fails_with_the_required_message(tmp_path):
    gate.run_gate(_repo(tmp_path), today=gate.EXPIRES, fetch=_pypi(CURRENT_PYPI))
    with pytest.raises(gate.GateFailure, match="PyMongo temporary security exception expired; reassess or upgrade"):
        gate.run_gate(_repo(tmp_path), today=gate.EXPIRES + dt.timedelta(days=1), fetch=_pypi(CURRENT_PYPI))


def test_expiry_is_short_and_not_silently_extended():
    # Accepted 2026-10-06 for at most 45 days. Moving EXPIRES needs a new decision,
    # and this assertion makes that a visible change.
    assert gate.EXPIRES == dt.date(2026, 11, 5)
    assert (gate.EXPIRES - dt.date(2026, 10, 6)).days <= 45


def test_expiry_is_checked_before_anything_else(tmp_path):
    def unreachable(url):
        raise AssertionError("an expired exception must fail without consulting PyPI")

    with pytest.raises(gate.GateFailure, match="expired"):
        gate.run_gate(tmp_path, today=gate.EXPIRES + dt.timedelta(days=1), fetch=unreachable)


@pytest.mark.parametrize("pin", ["4.18.2", "4.17.0", "4.15.5"])
def test_a_changed_pin_fails_so_the_exception_is_removed_with_the_upgrade(tmp_path, pin):
    repo = _repo(tmp_path, REQS.replace("pymongo==4.16.0", f"pymongo=={pin}"))
    with pytest.raises(gate.GateFailure, match="remove the\\s+exception|not the accepted"):
        gate.run_gate(repo, today=TODAY, fetch=_pypi(CURRENT_PYPI))


@pytest.mark.parametrize("requirements", ["motor==3.7.0\n", "pymongo==4.16.0\npymongo==4.16.0\n"])
def test_an_unreadable_pin_fails_closed(tmp_path, requirements):
    with pytest.raises(gate.GateFailure, match="exactly one"):
        gate.run_gate(_repo(tmp_path, requirements), today=TODAY, fetch=_pypi(CURRENT_PYPI))


@pytest.mark.parametrize(
    "requirement",
    ["pymongo>=4.18.2", "pymongo<5,>=4.12", "pymongo (>=4.12)", "pymongo[srv]>=4.18.2", None],
    ids=["exact-fix", "open-range", "parenthesised", "with-extra", "no-pymongo-requirement"],
)
def test_a_released_checkpoint_that_permits_the_fix_ends_the_exception(tmp_path, requirement):
    pypi = dict(CURRENT_PYPI, **{"0.6.0": requirement})
    with pytest.raises(gate.GateFailure, match="0.6.0 is released and permits pymongo>=4.18.2"):
        gate.run_gate(_repo(tmp_path), today=TODAY, fetch=_pypi(pypi))


@pytest.mark.parametrize("version", ["0.6.0rc1", "0.6.0.dev1"])
def test_a_prerelease_is_not_an_unblock(tmp_path, version):
    pypi = dict(CURRENT_PYPI, **{version: "pymongo>=4.18.2"})
    gate.run_gate(_repo(tmp_path), today=TODAY, fetch=_pypi(pypi))


def test_a_yanked_release_is_not_an_unblock(tmp_path):
    pypi = dict(CURRENT_PYPI, **{"0.6.0": "pymongo>=4.18.2"})
    gate.run_gate(_repo(tmp_path), today=TODAY, fetch=_pypi(pypi, yanked=("0.6.0",)))


def test_a_release_that_still_excludes_the_fix_does_not_unblock(tmp_path):
    pypi = dict(CURRENT_PYPI, **{"0.6.0": "pymongo<4.18.2,>=4.12"})
    gate.run_gate(_repo(tmp_path), today=TODAY, fetch=_pypi(pypi))


@pytest.mark.parametrize(
    "answer",
    [{}, {"releases": {}}, {"releases": None}, "not json"],
    ids=["empty", "no-releases", "null", "garbage"],
)
def test_unreadable_release_metadata_fails_closed(tmp_path, answer):
    with pytest.raises(gate.GateFailure):
        gate.run_gate(_repo(tmp_path), today=TODAY, fetch=lambda url: answer)


def test_a_network_failure_fails_closed(tmp_path):
    def offline(url):
        raise OSError("connection refused")

    with pytest.raises(gate.GateFailure, match="could not check"):
        gate.run_gate(_repo(tmp_path), today=TODAY, fetch=offline)


def test_main_reports_a_failure_as_a_github_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(gate, "EXPIRES", TODAY - dt.timedelta(days=1))
    assert gate.main(["--repo-root", str(_repo(tmp_path))]) == 1
    assert "::error::PyMongo temporary security exception expired" in capsys.readouterr().out
