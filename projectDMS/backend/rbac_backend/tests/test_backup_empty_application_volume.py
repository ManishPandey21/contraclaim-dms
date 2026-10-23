"""A fresh install's empty uploads volume is a valid backup; an empty graph is not.

R-A8W Stage B, F-A8W-B1. A brand-new staging deployment ran the release's own
`scripts/production_backup.sh` and it aborted on the first volume. The uploads
volume of a fresh install holds nothing but an empty `__chunks/` directory;
`production_backup.sh` declared no contract for it, so `backup_volume.sh` applied
the bare "readable, and holds at least one non-empty regular file" contract, which
refused it, and `set -e` stopped the script before Qdrant, FalkorDB, Redis, the
checksum file and the completion manifest were written. `/health/operations`
stayed 503 and the canonical smoke after deploy could not pass without an
operator replaying the script's remaining steps by hand.

The fix is not to weaken the bare contract - F-A8T2-8 made it refuse an archive
that restores nothing, and that is still right for anything that is supposed to
hold state. Data classes differ:

    PROFILE              CAN BE EMPTY?  REQUIRED MEMBERS          CONTENT VALIDATION
    redis-persistence    no             dump.rdb or AOF at root   RDB magic + size, AOF members
    qdrant-data any-of   no             collections or raft state non-empty file
    application-volume   yes            the archive root entry    readable gzip tar, every member
                                                                  restores inside the volume root

`application-volume` is the explicit profile for volumes whose emptiness is a
legitimate state (user uploads before the first upload, Qdrant's snapshot scratch
before the first snapshot). It is declared per label, never inferred from size.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rbac_backend.services.backup_archive_validation import (
    INVALID_CONTENT,
    INVALID_ROOT,
    PROFILE_BY_LABEL,
    UNREADABLE,
    UNVERIFIED,
    VALID,
    contract_for_label,
    validate_archive,
)
from rbac_backend.services.operations_health import DEFAULT_VOLUME_LABELS, build_backup_health

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_BACKUP = REPO_ROOT / "scripts" / "production_backup.sh"

RDB_BYTES = b"REDIS0011\xfa\x09redis-ver\x057.4.0\xff\x00\x00\x00\x00\x00\x00\x00\x00"

ALLOW_EMPTY_PROFILE = "application-volume"
ALLOW_EMPTY_LABELS = {"backend-uploads", "qdrant-snapshots"}
STATE_LABELS = {"falkordb-data", "falkordb-persistence", "redis-data", "qdrant-data"}


# --------------------------------------------------------------------------- #
# Archives, built the way `tar -czf ARCHIVE .` builds them
# --------------------------------------------------------------------------- #


def _tar_dir(source: Path, archive: Path) -> Path:
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(str(source), arcname=".")
    return archive


def _tar_members(archive: Path, members: dict[str, bytes | None]) -> Path:
    """`None` is a directory entry, bytes are a regular file."""
    with tarfile.open(archive, "w:gz") as handle:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            if payload is None:
                info.type = tarfile.DIRTYPE
                handle.addfile(info)
            else:
                info.size = len(payload)
                handle.addfile(info, io.BytesIO(payload))
    return archive


def _extract(archive: Path, target: Path) -> None:
    """Extract the way a restore would, refusing unsafe members where tarfile can."""
    with tarfile.open(archive, "r:gz") as handle:
        if hasattr(tarfile, "data_filter"):
            handle.extractall(target, filter="data")
        else:  # pragma: no cover - interpreters without PEP 706
            handle.extractall(target)


def _fresh_uploads(tmp_path: Path) -> Path:
    volume = tmp_path / "uploads"
    (volume / "__chunks").mkdir(parents=True)
    return volume


# --------------------------------------------------------------------------- #
# The profile
# --------------------------------------------------------------------------- #


def test_a_fresh_install_uploads_archive_is_valid(tmp_path: Path) -> None:
    archive = _tar_dir(_fresh_uploads(tmp_path), tmp_path / "backend-uploads.tar.gz")
    verdict = validate_archive(archive, profile=ALLOW_EMPTY_PROFILE)
    assert verdict.status == VALID, verdict.detail


def test_the_same_archive_is_still_refused_with_no_contract(tmp_path: Path) -> None:
    """F-A8T2-8 stands. The bare contract is not what changed."""
    archive = _tar_dir(_fresh_uploads(tmp_path), tmp_path / "backend-uploads.tar.gz")
    assert validate_archive(archive).status == INVALID_CONTENT


def test_an_uploads_archive_with_files_is_valid_and_restores(tmp_path: Path) -> None:
    volume = _fresh_uploads(tmp_path)
    (volume / "org1").mkdir()
    (volume / "org1" / "letter.pdf").write_bytes(b"%PDF-1.7 payload")
    archive = _tar_dir(volume, tmp_path / "backend-uploads.tar.gz")

    assert validate_archive(archive, profile=ALLOW_EMPTY_PROFILE).status == VALID

    restored = tmp_path / "restored"
    restored.mkdir()
    _extract(archive, restored)
    assert (restored / "org1" / "letter.pdf").read_bytes() == b"%PDF-1.7 payload"
    assert (restored / "__chunks").is_dir()


def test_a_corrupt_uploads_archive_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "backend-uploads.tar.gz"
    archive.write_bytes(b"\x1f\x8b\x08\x00 this is not a tarball")
    assert validate_archive(archive, profile=ALLOW_EMPTY_PROFILE).status == UNREADABLE


def test_a_truncated_uploads_archive_is_refused(tmp_path: Path) -> None:
    volume = _fresh_uploads(tmp_path)
    (volume / "big.bin").write_bytes(os.urandom(64_000))
    whole = _tar_dir(volume, tmp_path / "whole.tar.gz").read_bytes()
    archive = tmp_path / "backend-uploads.tar.gz"
    archive.write_bytes(whole[: len(whole) // 2])
    assert validate_archive(archive, profile=ALLOW_EMPTY_PROFILE).status == UNREADABLE


@pytest.mark.parametrize("cut", [8, 12, 20])
def test_an_archive_cut_in_its_gzip_trailer_is_refused(tmp_path: Path, cut: int) -> None:
    """R-A8X review: tarfile stops at the tar end marker and never read the trailer.

    Cutting the last 8-20 bytes left every member listable, so both profiles
    certified VALID an archive that `gzip -t` and `tar -xzf` refuse.
    """
    volume = _fresh_uploads(tmp_path)
    (volume / "letter.pdf").write_bytes(b"%PDF-1.7 payload")
    whole = _tar_dir(volume, tmp_path / "whole.tar.gz").read_bytes()
    uploads = tmp_path / "backend-uploads.tar.gz"
    uploads.write_bytes(whole[:-cut])
    assert validate_archive(uploads, profile=ALLOW_EMPTY_PROFILE).status == UNREADABLE

    falkor_whole = _tar_members(tmp_path / "f.tar.gz", {".": None, "dump.rdb": RDB_BYTES}).read_bytes()
    falkor = tmp_path / "falkordb-data.tar.gz"
    falkor.write_bytes(falkor_whole[:-cut])
    assert validate_archive(falkor, profile="redis-persistence").status == UNREADABLE


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_device_and_fifo_entries_are_refused(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "backend-uploads.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        special = tarfile.TarInfo("dev-node")
        special.type = kind
        handle.addfile(special)
    assert validate_archive(archive, profile=ALLOW_EMPTY_PROFILE).status == INVALID_CONTENT


def test_a_hard_link_must_point_at_an_entry_already_carried(tmp_path: Path) -> None:
    def build(name: str, with_target: bool) -> Path:
        archive = tmp_path / name
        with tarfile.open(archive, "w:gz") as handle:
            if with_target:
                info = tarfile.TarInfo("org1/letter.pdf")
                info.size = 4
                handle.addfile(info, io.BytesIO(b"%PDF"))
            link = tarfile.TarInfo("org1/copy.pdf")
            link.type = tarfile.LNKTYPE
            link.linkname = "org1/letter.pdf"
            handle.addfile(link)
        return archive

    assert validate_archive(build("ok.tar.gz", True), profile=ALLOW_EMPTY_PROFILE).status == VALID
    assert validate_archive(build("bad.tar.gz", False), profile=ALLOW_EMPTY_PROFILE).status == INVALID_CONTENT


def test_a_gzip_stream_with_no_tar_entry_is_not_a_volume_archive(tmp_path: Path) -> None:
    """Emptiness is judged from the entries, never from the byte count.

    `tar -czf ARCHIVE .` over an empty directory still records the root entry. A
    stream with no entry at all was not produced by archiving a volume.
    """
    archive = _tar_members(tmp_path / "backend-uploads.tar.gz", {})
    verdict = validate_archive(archive, profile=ALLOW_EMPTY_PROFILE)
    assert verdict.status == INVALID_CONTENT, verdict.detail


@pytest.mark.parametrize("escape", ["/etc/cron.d/job", "../outside.txt", "a/../../outside.txt"])
def test_a_member_that_restores_outside_the_volume_is_refused(tmp_path: Path, escape: str) -> None:
    archive = _tar_members(tmp_path / "backend-uploads.tar.gz", {".": None, escape: b"x"})
    verdict = validate_archive(archive, profile=ALLOW_EMPTY_PROFILE)
    assert verdict.status == INVALID_ROOT, verdict.detail


def test_an_empty_falkordb_archive_is_still_refused(tmp_path: Path) -> None:
    volume = tmp_path / "falkor"
    volume.mkdir()
    archive = _tar_dir(volume, tmp_path / "falkordb-data.tar.gz")
    assert validate_archive(archive, profile="redis-persistence").status == INVALID_CONTENT
    assert validate_archive(archive, **contract_for_label("falkordb-data")).status == INVALID_CONTENT


def test_a_wrong_root_falkordb_archive_is_still_refused(tmp_path: Path) -> None:
    archive = _tar_members(
        tmp_path / "falkordb-data.tar.gz", {"FalkorDB": None, "FalkorDB/dump.rdb": RDB_BYTES}
    )
    assert validate_archive(archive, **contract_for_label("falkordb-data")).status == INVALID_ROOT


def test_the_allow_empty_profile_is_declared_where_the_archive_is_written() -> None:
    """Declared by the backup script, per volume, and never for state."""
    script = PRODUCTION_BACKUP.read_text(encoding="utf-8")
    for volume in ("backend_uploads", "qdrant_snapshots"):
        prefix = 'backup_volume "${project_name}_' + volume + '"'
        line = next(text for text in script.splitlines() if text.startswith(prefix))
        assert f"--profile {ALLOW_EMPTY_PROFILE}" in line, line
    for label in STATE_LABELS:
        assert contract_for_label(label).get("profile") != ALLOW_EMPTY_PROFILE, (
            f"{label} holds state; an empty archive of it is a lost dataset, not a fresh install"
        )
    assert PROFILE_BY_LABEL["falkordb-data"] == "redis-persistence"


def test_health_does_not_validate_the_uploads_archive_inline() -> None:
    """R-A8X review: production's uploads archive is ~355 MB.

    Mapped for health, `/health/operations` would read it UNEVALUATED past the
    inline ceiling - unhealthy - and answer 503 after deploy. Content is
    enforced at backup time instead, where `set -e` makes a refusal stop the backup.
    """
    for label in ALLOW_EMPTY_LABELS:
        assert contract_for_label(label) == {}, (
            f"{label} gained a health-time contract; a large archive would make health 503"
        )


# --------------------------------------------------------------------------- #
# Backup health, which drives /health/operations
# --------------------------------------------------------------------------- #


def _fresh_backup_set(root: Path, tmp_path: Path, stamp: str = "20260913-160530") -> None:
    (root / "manifests").mkdir(parents=True)
    (root / "manifests" / f"backup-{stamp}.json").write_text("{}", encoding="utf-8")
    (root / "mongo").mkdir()
    (root / "mongo" / f"contraclaim-{stamp}.archive.gz").write_bytes(gzip.compress(b"mongodump"))
    volumes = root / "volumes"
    volumes.mkdir()
    _tar_dir(_fresh_uploads(tmp_path), volumes / f"backend-uploads-{stamp}.tar.gz")
    _tar_members(
        volumes / f"qdrant-data-{stamp}.tar.gz",
        {".": None, "collections/contracts/segments/0/segment.json": b"{}"},
    )
    _tar_members(volumes / f"falkordb-data-{stamp}.tar.gz", {".": None, "dump.rdb": RDB_BYTES})
    _tar_members(volumes / f"redis-data-{stamp}.tar.gz", {".": None, "dump.rdb": RDB_BYTES})


def test_backup_health_is_ok_for_a_fresh_install_with_empty_uploads(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    _fresh_backup_set(root, tmp_path)

    result = build_backup_health(str(root), max_age_hours=26, now=datetime.now(timezone.utc))

    assert result["status"] == "ok", result
    assert result["artifacts"]["backend-uploads"]["status"] == UNVERIFIED
    assert "backend-uploads" in DEFAULT_VOLUME_LABELS


def test_a_production_sized_uploads_archive_keeps_health_ok(tmp_path: Path) -> None:
    """The regression the review found, reproduced with a small inline ceiling.

    The uploads archive is larger than the 64-byte ceiling used here, as
    production's 355 MB archive is larger than the real one. It must stay a
    healthy UNVERIFIED, never an unhealthy UNEVALUATED.
    """
    root = tmp_path / "backups"
    _fresh_backup_set(root, tmp_path)

    result = build_backup_health(
        str(root),
        max_age_hours=26,
        now=datetime.now(timezone.utc),
        required_volume_labels=["backend-uploads"],
        max_inline_validation_bytes=64,
    )

    assert result["artifacts"]["backend-uploads"]["size_bytes"] > 64
    assert result["artifacts"]["backend-uploads"]["status"] == UNVERIFIED, result
    assert result["status"] == "ok", result


# --------------------------------------------------------------------------- #
# production_backup.sh itself, end to end, with docker replaced by a stub
# --------------------------------------------------------------------------- #


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

#: Speaks only the docker invocations production_backup.sh makes. Volume
#: `NAME` is served from `$STUB_VOLUMES/NAME`; `STUB_CORRUPT_VOLUME` makes the
#: archive of that one volume garbage, as a disk or a killed tar would. Both
#: persistence engines are compose-managed and flush successfully - LASTSAVE
#: advances on every BGSAVE - because the flush itself is measured in
#: `test_backup_persistence_flush.py`, not here.
_DOCKER_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "docker $*" >>"$STUB_LOG"
to_unix() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
if [[ "${1:-}" == "volume" && "${2:-}" == "inspect" ]]; then
  [[ -d "$STUB_VOLUMES/${3:-}" ]] && { echo "[{}]"; exit 0; }
  echo "Error response from daemon: get ${3:-}: no such volume" >&2; exit 1
fi
if [[ "${1:-}" == "inspect" ]]; then
  echo true; exit 0
fi
if [[ "${1:-}" == "exec" ]]; then
  clock="$(dirname "$STUB_LOG")/lastsave-${2:-engine}"
  [[ -f "$clock" ]] || printf '1000' >"$clock"
  case "${!#}" in
    LASTSAVE) cat "$clock" ;;
    BGSAVE) printf '%s' "$(($(cat "$clock") + 1))" >"$clock"; echo "Background saving started" ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
  case " $* " in
    *" ps -q "*) echo "stub-${!#}"; exit 0 ;;
    *" ps "*) echo "stub ps"; exit 0 ;;
    *" mongodump "*) printf 'mongodump archive' | gzip -c; exit 0 ;;
  esac
  echo "unexpected compose call: $*" >&2; exit 97
fi
if [[ "${1:-}" == "run" ]]; then
  volume="" host=""
  args=("$@")
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "-v" ]]; then
      spec=${args[$((i + 1))]}
      case "$spec" in
        *:/source:ro) volume=${spec%%:/source:ro} ;;
        *:/backup) host=${spec%:/backup} ;;
      esac
    fi
  done
  last=${args[$((${#args[@]} - 1))]}
  name=${last#*tar -czf /backup/}
  name=${name%% *}
  dest="$(to_unix "$host")/$name"
  if [[ "$volume" == "${STUB_CORRUPT_VOLUME:-}" ]]; then
    printf 'truncated by the stub' >"$dest"
    exit 0
  fi
  (cd "$STUB_VOLUMES/$volume" && tar -czf "$dest" .)
  exit 0
fi
echo "unexpected docker call: $*" >&2
exit 98
"""

PROJECT = "stubproj"


@pytest.fixture()
def backup_run(tmp_path: Path):
    if BASH is None:
        pytest.skip("no working bash to run production_backup.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(_DOCKER_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    volumes = tmp_path / "volumes"
    (volumes / f"{PROJECT}_backend_uploads" / "__chunks").mkdir(parents=True)
    (volumes / f"{PROJECT}_qdrant_data" / "collections" / "contracts").mkdir(parents=True)
    (volumes / f"{PROJECT}_qdrant_data" / "collections" / "contracts" / "config.json").write_text("{}")
    (volumes / f"{PROJECT}_qdrant_data" / "raft_state.json").write_text("{}")
    (volumes / f"{PROJECT}_qdrant_snapshots" / "tmp" / "upload").mkdir(parents=True)
    for label in ("falkordb_data", "redis_data"):
        (volumes / f"{PROJECT}_{label}").mkdir()
        (volumes / f"{PROJECT}_{label}" / "dump.rdb").write_bytes(RDB_BYTES)

    env_file = tmp_path / "stub.env"
    env_file.write_text(
        "DATABASE_URL=mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0\n"
        "MONGO_DB=contraclaim\n"
        "REDIS_PASSWORD=stub\n"
        "FALKORDB_PASSWORD=stub\n",
        encoding="utf-8",
        newline="\n",
    )
    backup_root = tmp_path / "backups"

    def run(script: Path = PRODUCTION_BACKUP, **extra: str) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"MONGO_URI", "DATABASE_URL", "MONGO_DB", "MONGODB_DATABASE"}
        }
        environment.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "ROOT_DIR": str(REPO_ROOT),
                "ENV_FILE": str(env_file),
                "BACKUP_ROOT": str(backup_root),
                "STAMP": "20260913-160530-stub",
                "COMPOSE_PROJECT_NAME": PROJECT,
                "PYTHON_BIN": sys.executable,
                "STUB_LOG": str(tmp_path / "stub.log"),
                "STUB_VOLUMES": str(volumes),
                **extra,
            }
        )
        return subprocess.run(
            [BASH, str(script)], capture_output=True, text=True, env=environment, timeout=300
        )

    run.root = backup_root
    run.volumes = volumes
    run.tmp = tmp_path
    return run


def _outputs(root: Path, stamp: str = "20260913-160530-stub") -> dict[str, bool]:
    return {
        "mongo": (root / "mongo" / f"contraclaim-{stamp}.archive.gz").is_file(),
        **{
            label: (root / "volumes" / f"{label}-{stamp}.tar.gz").is_file()
            for label in ("backend-uploads", "qdrant-data", "qdrant-snapshots", "falkordb-data", "redis-data")
        },
        "checksums": (root / "manifests" / f"checksums-{stamp}.sha256").is_file(),
        "manifest": (root / "manifests" / f"backup-{stamp}.json").is_file(),
        "latest": (root / "manifests" / "latest.json").is_file(),
    }


def test_production_backup_completes_on_a_fresh_install(backup_run) -> None:
    result = backup_run()
    assert result.returncode == 0, result.stdout + result.stderr

    produced = _outputs(backup_run.root)
    assert all(produced.values()), f"artefacts missing from a fresh-install backup: {produced}"
    checksums = (backup_run.root / "manifests" / "checksums-20260913-160530-stub.sha256").read_text()
    assert len(checksums.strip().splitlines()) == 6

    health = build_backup_health(str(backup_run.root), max_age_hours=26, now=datetime.now(timezone.utc))
    assert health["status"] == "ok", health


def test_production_backup_with_uploads_present_still_passes_and_restores(backup_run) -> None:
    uploaded = backup_run.volumes / f"{PROJECT}_backend_uploads" / "org1" / "letter.pdf"
    uploaded.parent.mkdir()
    uploaded.write_bytes(b"%PDF-1.7 payload")

    result = backup_run()
    assert result.returncode == 0, result.stdout + result.stderr

    restored = backup_run.tmp / "restored"
    restored.mkdir()
    archive = backup_run.root / "volumes" / "backend-uploads-20260913-160530-stub.tar.gz"
    _extract(archive, restored)
    assert (restored / "org1" / "letter.pdf").read_bytes() == b"%PDF-1.7 payload"


def test_production_backup_fails_visibly_on_a_corrupt_uploads_archive(backup_run) -> None:
    result = backup_run(STUB_CORRUPT_VOLUME=f"{PROJECT}_backend_uploads")
    assert result.returncode != 0, "a corrupt uploads archive was accepted as a backup"
    assert not _outputs(backup_run.root)["manifest"], "a completion manifest was written for a failed backup"


def test_production_backup_still_fails_on_an_empty_falkordb_volume(backup_run) -> None:
    (backup_run.volumes / f"{PROJECT}_falkordb_data" / "dump.rdb").unlink()
    result = backup_run()
    assert result.returncode != 0, "an empty FalkorDB volume was accepted as a backup"
    assert not _outputs(backup_run.root)["manifest"]


def test_production_backup_refuses_a_volume_that_does_not_exist(backup_run) -> None:
    """R-A8X review: `docker run -v NAME:/source` creates a missing named volume.

    Empty, it would archive as a valid fresh install under application-volume,
    so a misnamed or deleted uploads volume would read as a healthy backup.
    """
    shutil.rmtree(backup_run.volumes / f"{PROJECT}_backend_uploads")
    result = backup_run()
    assert result.returncode != 0, "a backup of a volume that does not exist succeeded"
    assert "does not exist" in result.stdout + result.stderr
    assert not _outputs(backup_run.root)["manifest"]


def test_reverting_uploads_to_the_generic_contract_is_caught(backup_run) -> None:
    """Mutation control: restore the generic non-empty requirement to uploads.

    Only the uploads line changes. The fresh-install run must go red and stop
    before the later volumes, which is exactly F-A8W-B1.
    """
    original = PRODUCTION_BACKUP.read_text(encoding="utf-8")
    line = next(
        text for text in original.splitlines() if text.startswith('backup_volume "${project_name}_backend_uploads"')
    )
    assert f"--profile {ALLOW_EMPTY_PROFILE}" in line, line
    mutated = backup_run.tmp / "production_backup_mutated.sh"
    mutated.write_text(
        original.replace(line, 'backup_volume "${project_name}_backend_uploads" "backend-uploads"', 1),
        encoding="utf-8",
        newline="\n",
    )

    result = backup_run(script=mutated)

    assert result.returncode != 0
    produced = _outputs(backup_run.root)
    assert not produced["redis-data"] and not produced["manifest"], produced
