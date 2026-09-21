"""A backup may not report success for a flush that reached nothing.

R-A9G moved production's FalkorDB persistence to `/data` with the Variant-A
sequence, which leaves the live engine running *out of band*: the compose
`falkordb` service stays stopped and preserved for rollback, and a separately
created container holds the `falkordb` network alias that the backend resolves.
`scripts/production_backup.sh` flushed with

    docker compose $COMPOSE_FILES exec -T falkordb sh -c '... BGSAVE' || true

so every backup from the cutover onwards printed `service "falkordb" is not
running`, threw that away with `|| true`, archived the volume and exited 0. The
post-move canonical backup in the cutover evidence is one of them. Nothing in
the run said the graph had not been flushed - which is the failure mode this
repository keeps rediscovering: success marked on a skipped step.

Two separate defects are fixed, and each one is measured here rather than read.

RESOLUTION. The compose service name is a deployment detail; the engine's
identity, as far as every consumer is concerned, is the network alias. So the
engine is looked up through compose first - the normal case, and the only one
that survives the service being renamed - and then by a real alias lookup, which
is the same question the backend's DNS asks. An out-of-band engine is found.

VERIFICATION. `BGSAVE` replies as soon as the fork starts, so its reply is not
evidence that anything was written. `LASTSAVE` advancing is. A BGSAVE that
answers and never lands is therefore reported `FAILED`, not `ok`.

The outcome is carried three ways, because an operator, a cron log and a later
audit read different things: a `FLUSH <label>: FAILED ...` line on stderr, a
`persistence_flush` block in the completion manifest, and exit status 3 - which
`backup_offsite_s3.sh` deliberately carries *through* its sync, because losing
off-site replication is a worse outcome than an unflushed engine.

`docker` is a stub on PATH that keeps a small state directory, so the script's
decisions are observed: which container it chose, which command it sent there,
and what it concluded. A static read of the script cannot tell a branch that
exists from a branch that is taken.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rbac_backend.tests.backup_archive_fixtures import RDB_BYTES

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_BACKUP = REPO_ROOT / "scripts" / "production_backup.sh"
OFFSITE_BACKUP = REPO_ROOT / "scripts" / "backup_offsite_s3.sh"

PROJECT = "flushproj"
STAMP = "20260921-004500-stub"

#: Distinctive values, so "the secret appears in an argument" is a substring
#: test that cannot be satisfied by a temporary path or a file name.
REDIS_SENTINEL = "redis-pw-SENTINEL-4e1b7"
FALKOR_SENTINEL = "falkor-pw-SENTINEL-9f3c2"

#: Everything the script asks `docker`, and nothing else. State lives in
#: `$STUB_STATE`: `ps` lists running container ids, `aliases/<id>` holds that
#: container's network aliases, `lastsave/<id>` holds its LASTSAVE clock, and
#: `compose/<service>` holds the id compose would return for that service.
#: `frozen/<id>` makes BGSAVE answer normally and never advance the clock, which
#: is the engine that accepts the command and writes nothing.
_DOCKER_STUB = r"""#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "docker $*" >>"$STUB_LOG"
to_unix() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }

if [[ "${1:-}" == "volume" && "${2:-}" == "inspect" ]]; then
  [[ -d "$STUB_VOLUMES/${3:-}" ]] && { echo "[{}]"; exit 0; }
  echo "Error response from daemon: get ${3:-}: no such volume" >&2; exit 1
fi

if [[ "${1:-}" == "ps" && "${2:-}" == "-q" ]]; then
  cat "$STUB_STATE/ps" 2>/dev/null || true
  exit 0
fi

if [[ "${1:-}" == "inspect" ]]; then
  fmt=""; target=""
  args=("$@")
  for ((i = 1; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "-f" ]]; then fmt=${args[$((i + 1))]}; i=$((i + 1)); else target=${args[$i]}; fi
  done
  case "$fmt" in
    *State.Running*)
      if grep -qx -- "$target" "$STUB_STATE/ps" 2>/dev/null; then echo true; else echo false; fi; exit 0 ;;
    *Aliases*)
      cat "$STUB_STATE/aliases/$target" 2>/dev/null || true; exit 0 ;;
  esac
  echo "unexpected docker inspect: $*" >&2; exit 96
fi

if [[ "${1:-}" == "exec" ]]; then
  cid=${2:-}
  command=${!#}
  case "$command" in
    LASTSAVE)
      cat "$STUB_STATE/lastsave/$cid" 2>/dev/null || exit 1 ;;
    BGSAVE)
      if [[ ! -f "$STUB_STATE/lastsave/$cid" ]]; then exit 1; fi
      if [[ ! -f "$STUB_STATE/frozen/$cid" ]]; then
        current=$(cat "$STUB_STATE/lastsave/$cid")
        printf '%s' "$((current + 1))" >"$STUB_STATE/lastsave/$cid"
      fi
      echo "Background saving started" ;;
    *) echo "unexpected redis command: $command" >&2; exit 95 ;;
  esac
  exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
  case " $* " in
    *" ps -q "*)
      service=${!#}
      cat "$STUB_STATE/compose/$service" 2>/dev/null || true
      exit 0 ;;
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
  (cd "$STUB_VOLUMES/$volume" && tar -czf "$(to_unix "$host")/$name" .)
  exit 0
fi

echo "unexpected docker call: $*" >&2
exit 98
"""


def _working_bash() -> str | None:
    for candidate in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
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


class _Docker:
    """The stubbed daemon's state, so a test says what production looks like."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for child in ("aliases", "lastsave", "compose", "frozen"):
            (root / child).mkdir(parents=True, exist_ok=True)
        (root / "ps").write_text("", encoding="utf-8", newline="\n")

    def engine(
        self,
        container: str,
        *,
        aliases: tuple[str, ...] = (),
        compose_service: str | None = None,
        lastsave: int = 1000,
        frozen: bool = False,
    ) -> None:
        with (self.root / "ps").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(container + "\n")
        (self.root / "aliases" / container).write_text(
            "".join(f"{alias}\n" for alias in (container, *aliases)), encoding="utf-8", newline="\n"
        )
        (self.root / "lastsave" / container).write_text(str(lastsave), encoding="utf-8", newline="\n")
        if compose_service:
            (self.root / "compose" / compose_service).write_text(
                container + "\n", encoding="utf-8", newline="\n"
            )
        if frozen:
            (self.root / "frozen" / container).write_text("", encoding="utf-8", newline="\n")

    def lastsave(self, container: str) -> int:
        return int((self.root / "lastsave" / container).read_text().strip())


@pytest.fixture()
def backup(tmp_path: Path):
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
    (volumes / f"{PROJECT}_qdrant_snapshots" / "tmp").mkdir(parents=True)
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

    state = tmp_path / "dockerstate"
    docker = _Docker(state)
    backup_root = tmp_path / "backups"
    log = tmp_path / "stub.log"

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
                # POSIX form: the manifest is a heredoc of raw paths, and a
                # Windows backslash is an invalid JSON escape. Production paths
                # are POSIX already.
                "BACKUP_ROOT": backup_root.as_posix(),
                "STAMP": STAMP,
                "COMPOSE_PROJECT_NAME": PROJECT,
                "PYTHON_BIN": sys.executable,
                "STUB_LOG": str(log),
                "STUB_VOLUMES": str(volumes),
                "STUB_STATE": str(state),
                "REDIS_FLUSH_TIMEOUT": "2",
                **extra,
            }
        )
        return subprocess.run(
            [BASH, str(script)], capture_output=True, text=True, env=environment, timeout=300
        )

    run.docker = docker
    run.root = backup_root
    run.log = log
    run.tmp = tmp_path
    return run


def _calls(backup) -> list[str]:
    return backup.log.read_text(encoding="utf-8").splitlines() if backup.log.exists() else []


def _artefacts_present(root: Path) -> dict[str, bool]:
    return {
        "mongo": (root / "mongo" / f"contraclaim-{STAMP}.archive.gz").is_file(),
        **{
            label: (root / "volumes" / f"{label}-{STAMP}.tar.gz").is_file()
            for label in ("backend-uploads", "qdrant-data", "qdrant-snapshots", "falkordb-data", "redis-data")
        },
        "checksums": (root / "manifests" / f"checksums-{STAMP}.sha256").is_file(),
        "manifest": (root / "manifests" / f"backup-{STAMP}.json").is_file(),
    }


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifests" / f"backup-{STAMP}.json").read_text(encoding="utf-8"))


def _both_engines_compose_managed(backup) -> None:
    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")
    backup.docker.engine("cidfalkor00001", aliases=("falkordb",), compose_service="falkordb")


# --------------------------------------------------------------------------- #
# The R-A9G topology: the compose service is stopped, the engine is not
# --------------------------------------------------------------------------- #


def test_an_out_of_band_engine_is_found_by_its_network_alias(backup) -> None:
    """`compose ps -q falkordb` returns nothing; a cutover container answers."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")
    backup.docker.engine("cidcutover0001", aliases=("falkordb",))  # no compose service

    result = backup()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLUSH falkordb: ok" in result.stdout, result.stdout
    assert backup.docker.lastsave("cidcutover0001") == 1001
    assert any(
        call.startswith("docker exec cidcutover0001") and call.endswith("BGSAVE")
        for call in _calls(backup)
    ), _calls(backup)
    assert _manifest(backup.root)["persistence_flush"] == {"redis": "ok", "falkordb": "ok"}


def test_the_compose_service_is_preferred_when_it_is_running(backup) -> None:
    """Normalised topology: compose answers, and the alias scan is not needed."""

    _both_engines_compose_managed(backup)

    result = backup()

    assert result.returncode == 0, result.stdout + result.stderr
    assert backup.docker.lastsave("cidfalkor00001") == 1001
    assert "docker ps -q" not in _calls(backup), _calls(backup)


# --------------------------------------------------------------------------- #
# The defect itself: a flush that reaches nothing
# --------------------------------------------------------------------------- #


def test_a_flush_that_reaches_nothing_is_reported_and_fails_the_run(backup) -> None:
    """The exact R-A9G state, minus the cutover container: nothing is listening."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")

    result = backup()

    assert result.returncode == 3, (result.returncode, result.stdout + result.stderr)
    assert "FLUSH falkordb: FAILED" in result.stderr, result.stderr
    assert "falkordb" in result.stderr
    assert "FLUSH redis: ok" in result.stdout, result.stdout


def test_a_failed_flush_still_writes_every_artefact(backup) -> None:
    """Exit 3 and not 1: the run completed, one guarantee did not hold."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")

    result = backup()

    assert result.returncode == 3
    produced = _artefacts_present(backup.root)
    assert all(produced.values()), f"a failed flush destroyed artefacts: {produced}"
    assert (backup.root / "manifests" / "latest.json").is_file()


def test_the_manifest_records_which_engine_was_not_flushed(backup) -> None:
    """A cron log rotates away; the manifest is what a later audit reads."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")

    assert backup().returncode == 3
    assert _manifest(backup.root)["persistence_flush"] == {"redis": "ok", "falkordb": "FAILED"}


def test_a_bgsave_that_never_lands_is_not_reported_as_ok(backup) -> None:
    """BGSAVE replies when the fork starts. Only LASTSAVE proves a write."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")
    backup.docker.engine("cidfalkor00001", aliases=("falkordb",), compose_service="falkordb", frozen=True)

    result = backup()

    assert result.returncode == 3, result.stdout + result.stderr
    assert "FLUSH falkordb: FAILED" in result.stderr
    assert "did not land" in result.stderr, result.stderr
    assert backup.docker.lastsave("cidfalkor00001") == 1000


def test_an_unreadable_lastsave_is_a_failure_not_a_flush(backup) -> None:
    """A resolved container with a wrong credential answers nothing usable."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")
    backup.docker.engine("cidfalkor00001", aliases=("falkordb",), compose_service="falkordb")
    (backup.tmp / "dockerstate" / "lastsave" / "cidfalkor00001").unlink()

    result = backup()

    assert result.returncode == 3
    assert "LASTSAVE unreadable" in result.stderr, result.stderr


# --------------------------------------------------------------------------- #
# The secret never reaches a host command line
# --------------------------------------------------------------------------- #


def test_the_password_is_never_an_argument_on_the_host(backup) -> None:
    """sudo writes argv to /var/log/auth.log and the journal keeps it (R-A9H D4)."""

    _both_engines_compose_managed(backup)

    assert backup().returncode == 0
    calls = _calls(backup)
    assert any("BGSAVE" in call for call in calls), calls
    for call in calls:
        assert REDIS_SENTINEL not in call, call
        assert FALKOR_SENTINEL not in call, call


# --------------------------------------------------------------------------- #
# Retention names what it could not delete
# --------------------------------------------------------------------------- #


def test_retention_forwards_the_paths_it_could_not_remove(tmp_path: Path) -> None:
    """`2>/dev/null` is what made "retention cleanup is required" unactionable."""

    if BASH is None:
        pytest.skip("no working bash")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "find").write_text(
        "#!/usr/bin/env bash\n"
        "echo \"find: cannot delete '/var/backups/contractdms/tag-migration/x.gz':"
        " Permission denied\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (bin_dir / "find").chmod(0o755)

    driver = tmp_path / "driver.sh"
    driver.write_text(
        f'. "{(REPO_ROOT / "scripts" / "lib" / "retention.sh").as_posix()}"\n'
        'retention_prune /var/backups/contractdms 14 || echo "status=$?"\n',
        encoding="utf-8",
        newline="\n",
    )

    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    result = subprocess.run(
        [BASH, str(driver)], capture_output=True, text=True, env=environment, timeout=60
    )

    assert "retention cleanup is required" in result.stderr
    assert "tag-migration/x.gz" in result.stderr, result.stderr
    assert "Permission denied" in result.stderr
    assert "status=1" in result.stdout


def test_retention_is_silent_when_everything_expired_could_be_removed(tmp_path: Path) -> None:
    if BASH is None:
        pytest.skip("no working bash")

    root = tmp_path / "backups"
    (root / "volumes").mkdir(parents=True)
    (root / "volumes" / "recent.tar.gz").write_bytes(b"x")

    driver = tmp_path / "driver.sh"
    driver.write_text(
        f'. "{(REPO_ROOT / "scripts" / "lib" / "retention.sh").as_posix()}"\n'
        f'retention_prune "{root.as_posix()}" 14 && echo CLEAN\n',
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run([BASH, str(driver)], capture_output=True, text=True, timeout=60)

    assert result.stderr.strip() == "", result.stderr
    assert "CLEAN" in result.stdout
    assert (root / "volumes" / "recent.tar.gz").is_file()


# --------------------------------------------------------------------------- #
# Off-site replication survives an unproven flush
# --------------------------------------------------------------------------- #


def test_the_offsite_sync_still_runs_after_an_unproven_flush(backup, tmp_path: Path) -> None:
    """Losing replication is worse than an unflushed engine; 3 is carried, not swallowed."""

    backup.docker.engine("cidredis00001", aliases=("redis",), compose_service="redis")

    aws_log = tmp_path / "aws.log"
    aws = tmp_path / "bin" / "aws"
    aws.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "aws $*" >>"{aws_log.as_posix()}"\nexit 0\n',
        encoding="utf-8",
        newline="\n",
    )
    aws.chmod(0o755)

    result = backup(
        OFFSITE_BACKUP,
        BACKUP_S3_BUCKET="stub-bucket",
        HOST_TAG="stubhost",
    )

    assert result.returncode == 3, result.stdout + result.stderr
    assert aws_log.is_file(), result.stdout + result.stderr
    assert "s3 sync" in aws_log.read_text(encoding="utf-8")
    assert "unproven persistence flush" in result.stdout, result.stdout
