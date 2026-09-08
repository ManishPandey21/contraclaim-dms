"""`falkordb-data: ok` for an 89-byte empty archive. That is F3, reproduced.

`build_backup_health` decided every artifact on mtime and `st_size > 0`, so the
archive R-A8O identified as an empty `/data` capture - 89 bytes, one directory
entry, no graph - was reported `ok` by `backup_status.py`, by
`post_deploy_verify.sh` and by `/health/operations`, once a night, for months.

These tests pin the distinction the old code could not express: a file that is
fresh is not a recovery artefact, and only the states `VALID` and `UNVERIFIED`
mean "no problem found" - the second of which says out loud that nothing was
proven.
"""

from __future__ import annotations

import gzip
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rbac_backend.services.backup_archive_validation import (
    INVALID_CONTENT,
    INVALID_ROOT,
    MISSING,
    STALE,
    UNEVALUATED,
    UNVERIFIED,
    VALID,
)
from rbac_backend.services.operations_health import build_backup_health
from rbac_backend.tests.backup_archive_fixtures import (
    RDB_BYTES,
    write_archive,
    write_empty_data_archive,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKUP_STATUS = REPO_ROOT / "scripts" / "backup_status.py"

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _gzip(path: Path, payload: bytes) -> Path:
    """A mongodump archive is a gzip stream, so the fixture is one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return path


def _tar(path: Path, members: dict[str, bytes], directories: tuple[str, ...] = ()) -> Path:
    return write_archive(path, members, directories=directories)


def _healthy_root(tmp_path: Path) -> Path:
    """Every artifact present, fresh, and carrying what it claims to."""
    root = tmp_path / "backups"
    manifest = root / "manifests" / "backup-20260908-010000.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    # Real archives, not placeholder text. Neither label declares a content
    # contract today, so text would pass - and would stop passing, silently and
    # for the wrong reason, the moment one is declared.
    _gzip(root / "mongo" / "contraclaim-20260908-010000.archive.gz", b"mongodump archive body")
    write_archive(root / "volumes" / "backend-uploads-20260908-010000.tar.gz", {"./letters/1.pdf": b"%PDF-1.4"})
    _tar(
        root / "volumes" / "qdrant-data-20260908-010000.tar.gz",
        {"./collections/contracts/segments/0/segment.json": b"{}", "./raft_state.json": b"{}"},
    )
    _tar(root / "volumes" / "falkordb-data-20260908-010000.tar.gz", {"./dump.rdb": RDB_BYTES})
    _tar(root / "volumes" / "redis-data-20260908-010000.tar.gz", {"./dump.rdb": RDB_BYTES})
    return root


def _replace_falkor_archive(tmp_path: Path) -> Path:
    """A healthy root with the FalkorDB archive removed, ready to be replaced.

    `_latest_file` picks by mtime, and two archives written in the same test
    can share a timestamp at the filesystem's resolution. Removing the good one
    makes "the archive under test is the one that is read" a fact rather than
    a race.
    """
    root = _healthy_root(tmp_path)
    for path in (root / "volumes").glob("falkordb-data-*.tar.gz"):
        path.unlink()
    return root


def test_a_backup_root_carrying_real_archives_is_healthy(tmp_path: Path) -> None:
    result = build_backup_health(str(_healthy_root(tmp_path)), max_age_hours=26, now=NOW)

    assert result["status"] == "ok"
    assert result["unhealthy_artifacts"] == []
    assert result["artifacts"]["falkordb-data"]["status"] == VALID
    assert result["artifacts"]["falkordb-data"]["content_verified"] is True


def test_the_eighty_nine_byte_falkordb_archive_is_no_longer_ok(tmp_path: Path) -> None:
    """The reproduction, and the fix, in one test."""
    root = _replace_falkor_archive(tmp_path)
    empty = write_empty_data_archive(root / "volumes" / "falkordb-data-20260908-020000.tar.gz")
    assert empty.stat().st_size < 200, "this must be the artefact production actually produces"

    result = build_backup_health(str(root), max_age_hours=26, now=NOW)

    assert result["artifacts"]["falkordb-data"]["status"] == INVALID_CONTENT
    assert result["status"] == "failed"
    assert "falkordb-data" in result["unhealthy_artifacts"]


def test_the_r_a8o_nested_rescue_archive_is_reported_as_the_wrong_root(tmp_path: Path) -> None:
    root = _replace_falkor_archive(tmp_path)
    _tar(
        root / "volumes" / "falkordb-data-20260908-020000.tar.gz",
        {"FalkorDB/dump.rdb": RDB_BYTES},
    )

    result = build_backup_health(str(root), max_age_hours=26, now=NOW)

    assert result["artifacts"]["falkordb-data"]["status"] == INVALID_ROOT
    assert result["status"] == "failed"


def test_an_artifact_with_no_content_contract_is_unverified_never_ok(tmp_path: Path) -> None:
    """`mongo` and `backend-uploads` have no declared contract yet.

    They must not be reported with the same word as an archive that was opened
    and found to carry a graph.
    """
    result = build_backup_health(str(_healthy_root(tmp_path)), max_age_hours=26, now=NOW)

    for label in ("manifest", "mongo", "backend-uploads"):
        assert result["artifacts"][label]["status"] == UNVERIFIED
        assert result["artifacts"][label]["content_verified"] is False
    assert set(result["unverified_artifacts"]) == {"manifest", "mongo", "backend-uploads"}
    assert result["status"] == "ok"


def test_no_artifact_is_ever_reported_as_ok(tmp_path: Path) -> None:
    """The word that made F3 possible is gone from the per-artifact vocabulary."""
    result = build_backup_health(str(_healthy_root(tmp_path)), max_age_hours=26, now=NOW)

    for artifact in result["artifacts"].values():
        assert artifact["status"] != "ok"


def test_staleness_is_still_reported(tmp_path: Path) -> None:
    result = build_backup_health(
        str(_healthy_root(tmp_path)),
        max_age_hours=26,
        now=NOW + timedelta(days=5),
    )

    assert result["status"] == "failed"
    assert result["artifacts"]["mongo"]["status"] == STALE
    assert result["artifacts"]["falkordb-data"]["status"] == STALE


def test_an_invalid_archive_outranks_its_freshness(tmp_path: Path) -> None:
    """Two hours old and unrestorable is an unrestorable archive, not a fresh one."""
    root = _replace_falkor_archive(tmp_path)
    write_empty_data_archive(root / "volumes" / "falkordb-data-20260908-020000.tar.gz")

    result = build_backup_health(str(root), max_age_hours=26, now=NOW)

    assert result["artifacts"]["falkordb-data"]["status"] == INVALID_CONTENT


def test_a_missing_artifact_is_still_missing(tmp_path: Path) -> None:
    root = _healthy_root(tmp_path)
    for path in (root / "volumes").glob("falkordb-data-*.tar.gz"):
        path.unlink()

    result = build_backup_health(str(root), max_age_hours=26, now=NOW)

    assert result["artifacts"]["falkordb-data"]["status"] == MISSING
    assert result["missing_artifacts"] == ["falkordb-data"]


def test_freshness_only_mode_claims_nothing_about_content(tmp_path: Path) -> None:
    """The escape hatch does not become a way to get a green light cheaply.

    Declining to run a declared contract is a skipped step, and a skipped step
    is not a pass - so the aggregate goes red too, not only the label.
    """
    root = _replace_falkor_archive(tmp_path)
    write_empty_data_archive(root / "volumes" / "falkordb-data-20260908-020000.tar.gz")

    result = build_backup_health(str(root), max_age_hours=26, now=NOW, verify_content=False)

    assert result["artifacts"]["falkordb-data"]["status"] == UNEVALUATED
    assert "falkordb-data" in result["unevaluated_artifacts"]
    assert result["status"] == "failed"
    assert "falkordb-data" in result["unhealthy_artifacts"]


# ------------------------------------------------------------------ the CLI


def test_the_status_cli_fails_on_an_empty_falkordb_archive(tmp_path: Path) -> None:
    root = _replace_falkor_archive(tmp_path)
    write_empty_data_archive(root / "volumes" / "falkordb-data-20260908-020000.tar.gz")

    result = subprocess.run(
        [sys.executable, str(BACKUP_STATUS), "--root", str(root), "--max-age-hours", "100000"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode == 1
    assert f"falkordb-data: {INVALID_CONTENT}" in result.stdout
    assert "falkordb-data: ok" not in result.stdout


def test_an_archive_above_the_inline_ceiling_is_unevaluated_and_unhealthy(tmp_path: Path) -> None:
    """`/health/operations` is polled, and decompression is not free.

    Above the ceiling the archive is reported for what it is - unread - and the
    aggregate goes red, rather than the archive being read on every request or
    quietly counted as healthy.
    """
    root = _replace_falkor_archive(tmp_path)
    _tar(root / "volumes" / "falkordb-data-20260908-020000.tar.gz", {"./dump.rdb": RDB_BYTES})

    result = build_backup_health(
        str(root),
        max_age_hours=26,
        now=NOW,
        max_inline_validation_bytes=1,
    )

    artifact = result["artifacts"]["falkordb-data"]
    assert artifact["status"] == UNEVALUATED
    assert artifact["content_verified"] is False
    assert "ceiling" in artifact["detail"]
    assert result["status"] == "failed", (
        "an archive whose contract was skipped for cost must not be counted as healthy; "
        "that is the R-A8P F3 shape with a different reason attached"
    )
