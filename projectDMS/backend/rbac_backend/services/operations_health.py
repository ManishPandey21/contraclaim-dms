"""Local backup health: what exists, how old it is, and whether it restores.

The last of those three used to be missing. `falkordb-data: ok` was reported
for an 89-byte archive of an empty directory, every night, for months, because
"ok" meant "the file is younger than 26 hours and larger than zero bytes"
(R-A8P F3). A freshness check is a useful signal and it is not a claim that a
recovery artefact exists, so it no longer gets to say `ok`.

Every artifact now carries an explicit state:

    VALID           fresh, and its declared content contract is satisfied
    UNVERIFIED      fresh, non-empty, and no content contract exists for it
    UNEVALUATED     a contract exists and was NOT applied - a skipped step
    STALE           older than the freshness bound
    EMPTY           zero bytes
    MISSING         no such file
    UNREADABLE      present, not a readable archive
    INVALID_CONTENT present and readable, carrying nothing recoverable
    INVALID_ROOT    the payload is there, nested where a restore will not find it

`UNEVALUATED` is unhealthy, and that distinction is the point. "Never mark
success on a skipped step" is a house rule, and an archive whose contract was
skipped - because it exceeded the inline ceiling, or because the caller asked
for freshness only - is a skipped step. `UNVERIFIED` is healthy because no
contract was ever declared for that label; the gap is in this module, not in
the backup.

The content contract itself lives in `backup_archive_validation`, which
`backup_volume.sh`, the maintenance runbook and the recovery drill also call.
One definition, four callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .backup_archive_validation import (
    EMPTY,
    INVALID_CONTENT,
    INVALID_ROOT,
    MISSING,
    STALE,
    UNEVALUATED,
    UNREADABLE,
    UNVERIFIED,
    VALID,
    contract_for_label,
    validate_archive,
)

DEFAULT_VOLUME_LABELS = ("backend-uploads", "qdrant-data", "falkordb-data", "redis-data")

#: Validating content means decompressing the whole archive, and this runs on
#: `/health/operations`, which is polled. Above this ceiling the archive is
#: reported UNVERIFIED with the reason rather than read - honest about what was
#: measured, and bounded in cost. Redis-family archives are kilobytes to a few
#: megabytes, so in practice this only ever spares an oversized object store.
MAX_INLINE_VALIDATION_BYTES = 256 * 1024 * 1024

#: The two states that do not indicate a problem. `UNVERIFIED` is in this set
#: because a missing *contract* is a gap in this module, not a failure of the
#: backup - but it is named in the output so nobody reads it as proof.
#: `UNEVALUATED` is deliberately NOT in this set: there the contract exists and
#: was skipped, and a gate that goes green on a check it declined to run is the
#: R-A8P F3 shape all over again.
HEALTHY_STATES = frozenset({VALID, UNVERIFIED})


def _latest_file(paths: Iterable[Path]) -> Path | None:
    files = [path for path in paths if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)


def _age_hours(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 3600)


def _file_status(
    path: Path | None,
    *,
    now: datetime,
    max_age_hours: int,
    label: str,
    verify_content: bool = True,
    max_inline_validation_bytes: int = MAX_INLINE_VALIDATION_BYTES,
) -> dict[str, Any]:
    """One artifact's state.

    Severity order is deliberate: an archive that cannot be restored is a worse
    finding than one that is merely old, so content is decided before age. An
    operator reading `INVALID_CONTENT` on a two-hour-old archive is being told
    the right thing.
    """

    if path is None:
        return {"label": label, "status": MISSING, "path": None, "content_verified": False}

    age = _age_hours(path, now)
    size = path.stat().st_size
    record: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "age_hours": round(age, 2),
        "size_bytes": size,
        "content_verified": False,
    }

    if size <= 0:
        return {**record, "status": EMPTY, "detail": "the archive is zero bytes"}

    contract = contract_for_label(label)
    if contract and size > max_inline_validation_bytes:
        return {
            **record,
            "status": UNEVALUATED,
            "detail": (
                f"{size} bytes exceeds the {max_inline_validation_bytes}-byte inline validation "
                f"ceiling, so the content contract was not applied. Validate it out of band with "
                f"scripts/validate_backup_archive.py, or raise the ceiling."
            ),
        }

    if contract and not verify_content:
        return {
            **record,
            "status": UNEVALUATED,
            "detail": "a content contract is declared for this label and freshness-only was requested",
        }

    if verify_content and contract:
        verdict = validate_archive(path, **contract)
        record["content_verified"] = True
        record["contract"] = verdict.contract
        if verdict.status in {INVALID_CONTENT, INVALID_ROOT, UNREADABLE}:
            return {**record, "status": verdict.status, "detail": verdict.detail}
        record["detail"] = verdict.detail

    if age > max_age_hours:
        return {**record, "status": STALE, "detail": f"older than the {max_age_hours}h freshness bound"}

    if record["content_verified"]:
        return {**record, "status": VALID}

    return {
        **record,
        "status": UNVERIFIED,
        "detail": "fresh and non-empty; no content contract is declared for this label",
    }


def build_backup_health(
    backup_root: str,
    *,
    max_age_hours: int,
    required_volume_labels: Iterable[str] = DEFAULT_VOLUME_LABELS,
    now: datetime | None = None,
    verify_content: bool = True,
    max_inline_validation_bytes: int = MAX_INLINE_VALIDATION_BYTES,
) -> dict[str, Any]:
    """Summarize local backup health without exposing secret material."""

    current_time = now or datetime.now(timezone.utc)
    root = Path(backup_root).expanduser()
    if not root.exists():
        return {
            "status": "failed",
            "backup_root": str(root),
            "max_age_hours": max_age_hours,
            "latest_backup_age_hours": None,
            "missing_artifacts": ["backup_root"],
            "unhealthy_artifacts": ["backup_root"],
            "unverified_artifacts": [],
            "unevaluated_artifacts": [],
            "artifacts": {},
        }

    manifest = _latest_file((root / "manifests").glob("backup-*.json"))
    legacy_manifest = _latest_file((root / "manifests").glob("deployment-*.txt"))
    if manifest is None:
        manifest = legacy_manifest
    mongo = _latest_file((root / "mongo").glob("*.archive.gz"))

    def status_for(path: Path | None, label: str) -> dict[str, Any]:
        return _file_status(
            path,
            now=current_time,
            max_age_hours=max_age_hours,
            label=label,
            verify_content=verify_content,
            max_inline_validation_bytes=max_inline_validation_bytes,
        )

    artifacts: dict[str, Any] = {
        "manifest": status_for(manifest, "manifest"),
        "mongo": status_for(mongo, "mongo"),
    }
    for label in required_volume_labels:
        latest = _latest_file((root / "volumes").glob(f"{label}-*.tar.gz"))
        artifacts[label] = status_for(latest, label)

    latest_ages = [
        float(item["age_hours"])
        for item in artifacts.values()
        if isinstance(item, dict) and item.get("age_hours") is not None
    ]
    missing = [label for label, item in artifacts.items() if item.get("status") == MISSING]
    unhealthy = [label for label, item in artifacts.items() if item.get("status") not in HEALTHY_STATES]
    unverified = [label for label, item in artifacts.items() if item.get("status") == UNVERIFIED]
    unevaluated = [label for label, item in artifacts.items() if item.get("status") == UNEVALUATED]
    return {
        "status": "ok" if not unhealthy else "failed",
        "backup_root": str(root),
        "max_age_hours": max_age_hours,
        "latest_backup_age_hours": round(max(latest_ages), 2) if latest_ages else None,
        "missing_artifacts": missing,
        "unhealthy_artifacts": unhealthy,
        "unverified_artifacts": unverified,
        "unevaluated_artifacts": unevaluated,
        "artifacts": artifacts,
    }
