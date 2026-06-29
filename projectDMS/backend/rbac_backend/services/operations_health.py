from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_VOLUME_LABELS = ("backend-uploads", "qdrant-data", "falkordb-data", "redis-data")


def _latest_file(paths: Iterable[Path]) -> Path | None:
    files = [path for path in paths if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)


def _age_hours(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 3600)


def _file_status(path: Path | None, *, now: datetime, max_age_hours: int, label: str) -> dict[str, Any]:
    if path is None:
        return {"label": label, "status": "missing", "path": None}
    age = _age_hours(path, now)
    size = path.stat().st_size
    status = "ok" if age <= max_age_hours and size > 0 else "stale"
    if size <= 0:
        status = "empty"
    return {
        "label": label,
        "status": status,
        "path": str(path),
        "age_hours": round(age, 2),
        "size_bytes": size,
    }


def build_backup_health(
    backup_root: str,
    *,
    max_age_hours: int,
    required_volume_labels: Iterable[str] = DEFAULT_VOLUME_LABELS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize local backup freshness without exposing secret material."""

    current_time = now or datetime.now(timezone.utc)
    root = Path(backup_root).expanduser()
    if not root.exists():
        return {
            "status": "failed",
            "backup_root": str(root),
            "max_age_hours": max_age_hours,
            "latest_backup_age_hours": None,
            "missing_artifacts": ["backup_root"],
            "artifacts": {},
        }

    manifest = _latest_file((root / "manifests").glob("backup-*.json"))
    legacy_manifest = _latest_file((root / "manifests").glob("deployment-*.txt"))
    if manifest is None:
        manifest = legacy_manifest
    mongo = _latest_file((root / "mongo").glob("*.archive.gz"))
    artifacts: dict[str, Any] = {
        "manifest": _file_status(manifest, now=current_time, max_age_hours=max_age_hours, label="manifest"),
        "mongo": _file_status(mongo, now=current_time, max_age_hours=max_age_hours, label="mongo"),
    }
    for label in required_volume_labels:
        latest = _latest_file((root / "volumes").glob(f"{label}-*.tar.gz"))
        artifacts[label] = _file_status(latest, now=current_time, max_age_hours=max_age_hours, label=label)

    latest_ages = [
        float(item["age_hours"])
        for item in artifacts.values()
        if isinstance(item, dict) and item.get("age_hours") is not None
    ]
    missing = [label for label, item in artifacts.items() if item.get("status") == "missing"]
    unhealthy = [label for label, item in artifacts.items() if item.get("status") not in {"ok"}]
    status = "ok" if not unhealthy else "failed"
    return {
        "status": status,
        "backup_root": str(root),
        "max_age_hours": max_age_hours,
        "latest_backup_age_hours": round(max(latest_ages), 2) if latest_ages else None,
        "missing_artifacts": missing,
        "unhealthy_artifacts": unhealthy,
        "artifacts": artifacts,
    }
