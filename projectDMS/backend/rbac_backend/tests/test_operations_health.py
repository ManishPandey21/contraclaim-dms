from __future__ import annotations

import io
import os
import tarfile
from datetime import datetime, timezone

from rbac_backend.services.backup_archive_validation import STALE
from rbac_backend.services.operations_health import build_backup_health

#: A minimal but real RDB snapshot header. These archives used to hold the
#: literal text "volume", which passed while the check was mtime plus size and
#: cannot pass now that the FalkorDB and Redis archives are opened and read.
RDB_BYTES = b"REDIS0011\xfa\x09redis-ver\x057.4.0\xff\x00\x00\x00\x00\x00\x00\x00\x00"

#: Which volume labels carry a content contract, and what a valid archive of
#: each looks like. `backend-uploads` has none, so a plain file is still fine.
VOLUME_FIXTURES = {
    "backend-uploads": None,
    "qdrant-data": {"./collections/contracts/segments/0/segment.json": b"{}"},
    "falkordb-data": {"./dump.rdb": RDB_BYTES},
    "redis-data": {"./dump.rdb": RDB_BYTES},
}


def _write(path, content="ok"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _tar(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def _volume_archive(root, label, stamp="20260629-010000"):
    target = root / "volumes" / f"{label}-{stamp}.tar.gz"
    members = VOLUME_FIXTURES[label]
    return _write(target, "volume") if members is None else _tar(target, members)


def test_backup_health_fails_when_root_is_missing(tmp_path):
    result = build_backup_health(str(tmp_path / "missing"), max_age_hours=26)

    assert result["status"] == "failed"
    assert result["missing_artifacts"] == ["backup_root"]


def test_backup_health_passes_with_required_recent_artifacts(tmp_path):
    root = tmp_path / "backups"
    _write(root / "manifests" / "backup-20260629-010000.json", "{}")
    _write(root / "mongo" / "contraclaim-20260629-010000.archive.gz", "mongo")
    for label in VOLUME_FIXTURES:
        _volume_archive(root, label)

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
    for label in VOLUME_FIXTURES:
        files.append(_volume_archive(root, label))
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
    assert result["artifacts"]["mongo"]["status"] == STALE
