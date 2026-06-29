from __future__ import annotations

import os
from datetime import datetime, timezone

from rbac_backend.services.operations_health import build_backup_health


def _write(path, content="ok"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_backup_health_fails_when_root_is_missing(tmp_path):
    result = build_backup_health(str(tmp_path / "missing"), max_age_hours=26)

    assert result["status"] == "failed"
    assert result["missing_artifacts"] == ["backup_root"]


def test_backup_health_passes_with_required_recent_artifacts(tmp_path):
    root = tmp_path / "backups"
    _write(root / "manifests" / "backup-20260629-010000.json", "{}")
    _write(root / "mongo" / "contraclaim-20260629-010000.archive.gz", "mongo")
    for label in ["backend-uploads", "qdrant-data", "falkordb-data", "redis-data"]:
        _write(root / "volumes" / f"{label}-20260629-010000.tar.gz", "volume")

    result = build_backup_health(
        str(root),
        max_age_hours=26,
        now=datetime.now(timezone.utc),
    )

    assert result["status"] == "ok"
    assert result["missing_artifacts"] == []
    assert result["unhealthy_artifacts"] == []


def test_backup_health_fails_for_stale_artifacts(tmp_path):
    root = tmp_path / "backups"
    files = [
        _write(root / "manifests" / "backup-20260629-010000.json", "{}"),
        _write(root / "mongo" / "contraclaim-20260629-010000.archive.gz", "mongo"),
    ]
    for label in ["backend-uploads", "qdrant-data", "falkordb-data", "redis-data"]:
        files.append(_write(root / "volumes" / f"{label}-20260629-010000.tar.gz", "volume"))
    old = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    for item in files:
        os.utime(item, (old, old))

    result = build_backup_health(
        str(root),
        max_age_hours=26,
        now=datetime(2026, 6, 29, tzinfo=timezone.utc),
    )

    assert result["status"] == "failed"
    assert "mongo" in result["unhealthy_artifacts"]
    assert result["artifacts"]["mongo"]["status"] == "stale"
