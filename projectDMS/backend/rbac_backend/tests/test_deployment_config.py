"""Static guards for deployment-config contracts (2026-07 production audit).

These tests read the deployment files as text and pin the fixes for:

- H1: uvicorn must trust proxy headers, or request.client.host is the Apache
  gateway IP for every request — the per-IP login limiter collapses into one
  shared bucket (61st failed login platform-wide 429s everyone) and audit
  logs record the proxy instead of the client.
- The nginx edge must overwrite X-Forwarded-For with $remote_addr; appending
  ($proxy_add_x_forwarded_for) lets a client spoof the leftmost entry and
  choose its own rate-limit bucket / audit identity.
- CSP frame-src must not allow framing arbitrary https:/data: content; only
  self, blob: previews, and the S3 hosts presigned URLs point at.
- The gateway healthcheck must exercise Apache over HTTP, not just stat the
  health file on disk.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
NGINX_CONF = REPO_ROOT / "config" / "nginx-contraclaim.conf"
HTTPD_CONF = REPO_ROOT / "config" / "httpd.conf"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_MONGO_REPLICA = REPO_ROOT / "docker-compose.mongo-replicaset.yml"
PRODUCTION_BACKUP = REPO_ROOT / "scripts" / "production_backup.sh"
BACKUP_VOLUME = REPO_ROOT / "scripts" / "backup_volume.sh"


def test_uvicorn_trusts_proxy_headers() -> None:
    cmd_lines = [
        line
        for line in BACKEND_DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("CMD")
    ]
    assert cmd_lines, f"no CMD line found in {BACKEND_DOCKERFILE}"
    cmd = cmd_lines[-1]
    assert "--proxy-headers" in cmd, (
        "uvicorn CMD must pass --proxy-headers; without it request.client.host "
        "is the gateway IP and per-IP login rate limiting shares one bucket "
        "for every user (audit H1)"
    )
    assert "--forwarded-allow-ips" in cmd, (
        "uvicorn CMD must pass --forwarded-allow-ips alongside --proxy-headers"
    )


def test_nginx_overwrites_x_forwarded_for() -> None:
    # Directives only: comments may (and do) mention the forbidden variable
    # while explaining why it is forbidden.
    text = "\n".join(
        line
        for line in NGINX_CONF.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    assert "$proxy_add_x_forwarded_for" not in text, (
        "nginx must set X-Forwarded-For from $remote_addr, not append with "
        "$proxy_add_x_forwarded_for: the edge is the first trusted hop and a "
        "client-supplied header must be discarded, or the client IP the "
        "backend resolves is attacker-chosen"
    )
    assert re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", text
    ), "nginx must set X-Forwarded-For to $remote_addr"


def test_csp_frame_src_is_scoped() -> None:
    text = HTTPD_CONF.read_text(encoding="utf-8")
    match = re.search(r"frame-src\s+([^;]+);", text)
    assert match, "httpd.conf CSP must declare frame-src explicitly"
    sources = match.group(1).split()
    assert "https:" not in sources, (
        "frame-src must not allow bare https: (any HTTPS origin could be "
        "framed inside the app); scope it to the S3 hosts previews use"
    )
    assert "data:" not in sources, (
        "frame-src must not allow data: URIs (nothing in the client frames "
        "data: content; it only enables phishing overlays)"
    )
    assert "'self'" in sources
    assert "blob:" in sources, "blob: is required for fetched PDF previews"


def test_gateway_healthcheck_probes_http() -> None:
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    # Find the gateway service's healthcheck test line.
    gateway_block = text.split("gateway:", 1)[1]
    test_lines = [
        line for line in gateway_block.splitlines() if "test:" in line
    ]
    assert test_lines, "gateway service must define a healthcheck test"
    probe = test_lines[0]
    assert "GET /health" in probe, (
        "gateway healthcheck must issue an HTTP request to /health so it "
        "proves Apache is serving, not merely that health.txt exists on disk"
    )


def test_falkordb_entrypoint_loads_graph_module() -> None:
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    falkor_block = text.split("\n  falkordb:", 1)[1].split("\n  redis:", 1)[0]

    assert "REDIS_ARGS:" in falkor_block, (
        "Redis options must be passed through REDIS_ARGS so the FalkorDB image "
        "entrypoint still loads the graph module"
    )
    assert "\n    command:" not in falkor_block, (
        "overriding the FalkorDB command starts plain Redis without falkordb.so"
    )
    assert "COMMAND INFO GRAPH.QUERY" in falkor_block, (
        "FalkorDB health must verify graph-command availability, not only PING"
    )


def test_falkordb_persists_inside_the_volume_that_gets_backed_up() -> None:
    """The graph must be written where the backup looks for it.

    The FalkorDB image's WORKDIR is /FalkorDB and Redis defaults ``dir`` to the
    working directory, so without an explicit ``--dir`` the RDB and the AOF are
    written into the container's writable layer. The backup then archives an
    empty volume, exits 0, and the graph does not survive the container being
    replaced. Proven end to end by scripts/falkordb_recovery_drill.sh.
    """
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    falkor_block = text.split("\n  falkordb:", 1)[1].split("\n  redis:", 1)[0]

    mount = re.search(r"- +falkordb_data:(\S+)", falkor_block)
    assert mount, "the falkordb service must mount the falkordb_data volume"
    mount_path = mount.group(1)

    assert re.search(rf"--dir +{re.escape(mount_path)}(\s|$)", falkor_block), (
        f"FalkorDB must be told to persist into {mount_path}, the volume "
        f"scripts/production_backup.sh archives; without --dir it writes to the "
        f"image WORKDIR inside the container and the backup captures nothing"
    )


def test_production_backup_requires_real_persistence_files_in_each_state_archive() -> None:
    """`tar` exiting 0 is not a backup.

    Every archive that is supposed to carry recoverable state declares what it
    must contain, so a volume that holds nothing fails the backup loudly
    instead of producing a 89-byte archive that restores an empty database.
    """
    text = PRODUCTION_BACKUP.read_text(encoding="utf-8")
    calls = {
        line.split('"')[1]: line
        for line in text.splitlines()
        if line.startswith("backup_volume ")
    }

    for volume_suffix in ("falkordb_data", "redis_data", "qdrant_data"):
        call = next(
            (line for name, line in calls.items() if name.endswith(volume_suffix)),
            None,
        )
        assert call, f"production_backup.sh no longer backs up {volume_suffix}"
        assert call.count('"') > 4, (
            f"the {volume_suffix} backup must declare the entries its archive has "
            f"to contain, or an empty volume is archived as a successful backup"
        )


def _working_bash() -> str | None:
    """A bash that actually runs.

    On Windows `shutil.which("bash")` resolves the WSL launcher stub, which
    fails with `execvpe(/bin/bash)` when no distribution is installed. Probing
    is the only way to tell a usable interpreter from a stub.
    """
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


def _verify_archive(archive: Path, *patterns: str) -> subprocess.CompletedProcess[str]:
    # Run from the archive's directory and name it relatively: GNU tar reads a
    # leading "C:" as a remote host, so a Windows absolute path never reaches it.
    assert BASH is not None
    return subprocess.run(
        [BASH, str(BACKUP_VOLUME), "--verify", archive.name, *patterns],
        cwd=archive.parent,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_refuses_an_archive_without_the_required_entries(
    tmp_path: Path,
) -> None:
    """The exact shape the FalkorDB backup produced while reporting success."""
    empty = tmp_path / "empty.tar.gz"
    with tarfile.open(empty, "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("./"))

    result = _verify_archive(empty, "*dump.rdb", "*appendonlydir*")

    assert result.returncode != 0, (
        "an archive holding one empty directory entry must fail verification; "
        "this is the artifact the FalkorDB backup produced for months"
    )
    assert "none of" in result.stderr


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_accepts_an_archive_carrying_persisted_state(
    tmp_path: Path,
) -> None:
    # The payload lives in a subdirectory: under Git Bash the shell expands a
    # "*dump.rdb" argument against the working directory before the script
    # sees it, which would quietly turn the pattern into a literal name.
    source = tmp_path / "state"
    source.mkdir()
    payload = source / "dump.rdb"
    payload.write_bytes(b"REDIS0011")
    good = tmp_path / "good.tar.gz"
    with tarfile.open(good, "w:gz") as archive:
        archive.add(payload, arcname="./dump.rdb")

    result = _verify_archive(good, "*dump.rdb", "*appendonlydir*")

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_refuses_a_missing_or_unreadable_archive(tmp_path: Path) -> None:
    missing = _verify_archive(tmp_path / "absent.tar.gz", "*dump.rdb")
    assert missing.returncode != 0

    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(b"not a gzip stream")
    assert _verify_archive(truncated, "*dump.rdb").returncode != 0


def test_volume_restore_does_not_depend_on_a_parseable_env_file() -> None:
    """A restore runs during an incident, on whatever the host's .env holds.

    The script sourced `.env` and used nothing from it, so one malformed line
    ("XAI_API_KEY = value") aborted the restore with `command not found` under
    `set -e`.
    """
    text = (REPO_ROOT / "scripts" / "production_restore_volumes.sh").read_text(encoding="utf-8")

    assert "source " not in text and "ENV_FILE" not in text, (
        "production_restore_volumes.sh must not source .env: it reads nothing "
        "from it, and a malformed line there aborts the restore"
    )


def test_mongo_replica_services_have_restore_safe_limits_and_replication_health() -> None:
    text = COMPOSE_MONGO_REPLICA.read_text(encoding="utf-8")

    assert "soft: 64000" in text and "hard: 64000" in text, (
        "MongoDB restore/index builds exceed the container default of 1024 file "
        "descriptors; every replica member must inherit the restore-safe nofile limit"
    )
    assert text.count("ulimits: *mongo-ulimits") == 3
    assert "rs.status()" in text and "s.myState === 1" in text and "s.myState === 2" in text, (
        "MongoDB health must require a PRIMARY or SECONDARY replica state; ping-only "
        "health reports a recovering or isolated member as healthy"
    )
    mongo_init_block = text.split("\n  mongo-init:", 1)[1]
    assert mongo_init_block.count("condition: service_started") == 3, (
        "replica initialization must start before replication-aware health can pass"
    )
