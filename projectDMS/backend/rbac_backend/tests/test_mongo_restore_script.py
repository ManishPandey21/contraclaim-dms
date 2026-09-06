"""`mongo_restore.sh` must be runnable in the topology it is documented against.

R-A8I ran the Gate 8 restore drill and found the documented restore cannot reach
the database it is documented to restore. `production_backup.sh` already knows
that docker-internal replica-set hostnames do not resolve on the host and runs
`mongodump` inside a replica-set container; `mongo_restore.sh` had no counterpart
and failed with `dial tcp: lookup mongo1 on 127.0.0.53:53: server misbehaving`.
The drill completed only because it was re-run by hand inside
`contraclaim-stg-mongo1-1`, which no runbook instructs. The production URI has the
same shape, so the gap is production-relevant.

The fix is not a hostname special case. `production_backup.sh` branches on the
literal `mongo1:` appearing in the URI, which is a guess that happens to be right
here; a service renamed in compose silently returns it to the broken path. The
restore instead names its **execution context** - host, compose, or resolve it -
and the resolution is a real name lookup rather than a string match.

The second half is safety. A restore is the one operation that can overwrite a
live database with an old one, and R-A8I recorded what a restore racing a running
application does to the permission catalogue. The default target is now nothing at
all: the database must be named, and a production name or the production replica
set is refused unless a separate, explicit production-restore authorisation is
given.

These are behavioural tests. `mongorestore` and `docker` are replaced by stubs on
PATH that record their arguments, so the script's decisions are observed rather
than read - a static check cannot tell a documented branch from a taken one.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "mongo_restore.sh"

STAGING_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rsstg"
STAGING_DB = "contraclaim_staging"
PRODUCTION_URI = "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0"
PRODUCTION_DB = "contraclaim"

#: Two stubs, one log. `mongorestore` is what a host execution reaches; `docker`
#: is what a compose execution reaches. Recording both in one file means a test
#: can assert not only that the right one ran but that the other one did not.
_STUB = (
    "#!/usr/bin/env bash\n"
    'printf \'%s\' "$(basename "$0")" >>"$STUB_LOG"\n'
    'for arg in "$@"; do printf \' %s\' "$arg" >>"$STUB_LOG"; done\n'
    "printf '\\n' >>\"$STUB_LOG\"\n"
    "exit ${STUB_EXIT:-0}\n"
)

_MANAGED = ("MONGO_URI", "DATABASE_URL", "MONGO_DB", "MONGODB_DATABASE")


def _usable_bash():
    """A bash that can actually run a script.

    `shutil.which("bash")` on a Windows host finds the WSL relay stub first,
    which answers to the name and then fails with
    `execvpe(/bin/bash) failed: No such file or directory` when no distribution
    is installed. A `bash` that cannot run `true` is not a bash, so every
    candidate is proven before it is used.
    """
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run([candidate, "-c", "true"], capture_output=True, timeout=30)
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    return None


BASH = _usable_bash()


@pytest.fixture(scope="module", autouse=True)
def _bash_available() -> None:
    if BASH is None:
        pytest.skip("no usable bash on this host")


@pytest.fixture()
def harness(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "stub.log"
    for name in ("mongorestore", "docker"):
        stub = bin_dir / name
        stub.write_text(_STUB, encoding="utf-8", newline="\n")
        stub.chmod(0o755)

    archive = tmp_path / "contraclaim_staging-20260906.archive.gz"
    archive.write_bytes(gzip.compress(b"not a real dump, but a valid gzip member"))

    def run(*, argv=None, env=None, uri=STAGING_URI, db=STAGING_DB):
        environment = {k: v for k, v in os.environ.items() if k not in _MANAGED}
        environment["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        environment["STUB_LOG"] = str(log)
        if uri is not None:
            environment["MONGO_URI"] = uri
        if db is not None:
            environment["MONGO_DB"] = db
        environment.update(env or {})
        result = subprocess.run(
            [BASH, str(SCRIPT), *(argv if argv is not None else [str(archive)])],
            capture_output=True,
            text=True,
            env=environment,
            cwd=str(REPO_ROOT),
        )
        result.stub_log = log.read_text(encoding="utf-8") if log.exists() else ""
        return result

    run.archive = archive
    run.bin_dir = bin_dir
    return run


# --------------------------------------------------------------------------- #
# The script itself
# --------------------------------------------------------------------------- #


def test_the_script_parses() -> None:
    result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_script_stops_on_error() -> None:
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# F3 - execution context
# --------------------------------------------------------------------------- #


def test_host_context_runs_mongorestore_directly(harness) -> None:
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("mongorestore "), result.stub_log
    assert "docker" not in result.stub_log
    assert f"--nsInclude={STAGING_DB}.*" in result.stub_log


def test_compose_context_runs_mongorestore_inside_the_replica_set_container(harness) -> None:
    """The branch `production_backup.sh` has and the restore did not.

    Docker-internal replica-set hostnames resolve inside the compose network and
    nowhere else, so the restore has to run where the names mean something.
    """
    result = harness(env={"RESTORE_EXEC_CONTEXT": "compose"})

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("docker "), result.stub_log
    assert " exec " in result.stub_log
    assert " mongo1 " in result.stub_log
    assert " mongorestore " in result.stub_log
    # Streamed on stdin, not by path: the archive lives on the host and the
    # container has no route to it.
    assert " --archive" in result.stub_log
    assert str(harness.archive) not in result.stub_log


def test_the_compose_service_is_configurable_not_a_hostname_guess(harness) -> None:
    """`production_backup.sh` branches on the literal `mongo1:`. Renaming the
    service in compose silently returns that script to the broken path; naming
    the service explicitly cannot rot the same way."""
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "compose", "MONGO_EXEC_SERVICE": "mongo-primary"}
    )

    assert result.returncode == 0, result.stderr
    assert " mongo-primary " in result.stub_log


def test_auto_context_uses_the_host_when_the_names_resolve(harness) -> None:
    """A URI whose hosts resolve locally needs no container."""
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "auto"},
        uri="mongodb://localhost:27017/?replicaSet=rsstg",
    )

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("mongorestore "), result.stub_log


def test_auto_context_falls_back_to_compose_when_a_name_does_not_resolve(harness) -> None:
    """`mongo1` is a compose service name; on the host it is nothing.

    Resolution is a real name lookup, so a service called anything at all lands
    on the same branch - which is the difference between this and matching the
    string `mongo1:`.
    """
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "auto"},
        uri="mongodb://primary.invalid:27017,secondary.invalid:27017/?replicaSet=rsstg",
    )

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("docker "), result.stub_log


def test_an_unknown_execution_context_is_refused(harness) -> None:
    result = harness(env={"RESTORE_EXEC_CONTEXT": "kubernetes"})

    assert result.returncode != 0
    assert "RESTORE_EXEC_CONTEXT" in result.stderr
    assert result.stub_log == ""


# --------------------------------------------------------------------------- #
# F3 safety - negative controls
# --------------------------------------------------------------------------- #


def test_a_production_database_name_is_refused(harness) -> None:
    result = harness(db=PRODUCTION_DB, env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode != 0
    assert "ALLOW_PRODUCTION_RESTORE" in result.stderr
    assert result.stub_log == "", "the refusal must happen before anything runs"


def test_the_production_replica_set_is_refused(harness) -> None:
    """`rs0` is production's replica set. A staging database name on a
    production replica set is still a production connection."""
    result = harness(uri=PRODUCTION_URI, env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode != 0
    assert "rs0" in result.stderr
    assert result.stub_log == ""


def test_an_explicitly_authorised_production_restore_is_allowed(harness) -> None:
    """The refusal is a gate, not a wall: the drill has to be runnable when the
    owner has decided to run it, or it will be run around instead."""
    result = harness(
        uri=PRODUCTION_URI,
        db=PRODUCTION_DB,
        env={"RESTORE_EXEC_CONTEXT": "host", "ALLOW_PRODUCTION_RESTORE": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("mongorestore ")


def test_a_missing_database_target_is_refused(harness) -> None:
    """There is deliberately no default. The old one was `contraclaim` - the
    production database - so an operator who forgot the variable was pointed at
    production by the script's own fallback."""
    result = harness(db=None, env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode != 0
    assert "MONGO_DB" in result.stderr
    assert result.stub_log == ""


def test_a_missing_uri_is_refused(harness) -> None:
    result = harness(uri=None, env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode != 0
    assert "MONGO_URI" in result.stderr
    assert result.stub_log == ""


def test_a_missing_archive_is_refused(harness, tmp_path: Path) -> None:
    result = harness(
        argv=[str(tmp_path / "absent.archive.gz")], env={"RESTORE_EXEC_CONTEXT": "host"}
    )

    assert result.returncode != 0
    assert "not found" in result.stderr.lower()
    assert result.stub_log == ""


def test_a_corrupt_archive_is_refused(harness, tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.archive.gz"
    corrupt.write_bytes(b"this is not gzip")

    result = harness(argv=[str(corrupt)], env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode != 0
    assert result.stub_log == ""


def test_a_failing_restore_is_a_failing_script(harness) -> None:
    """`mongorestore` exiting non-zero must not be reported as a completed
    restore. A restore that says it worked and did not is the worst outcome
    available to this script."""
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host", "STUB_EXIT": "3"})

    assert result.returncode != 0
    assert "completed" not in result.stdout.lower()


def test_the_completion_message_names_the_target_it_actually_restored(harness) -> None:
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host"})

    assert STAGING_DB in result.stdout


def test_auto_context_refuses_when_no_resolver_exists(tmp_path: Path, harness) -> None:
    """"Cannot tell" and "definitely not local" must not collapse into each other.

    With no `getent` and no python on PATH, `auto` has no way to decide. Assuming
    "unresolvable" would send a perfectly local restore into a container that may
    not exist, so it refuses and names both explicit contexts instead.

    The refusal happens inside a command substitution, which only exits the
    subshell - `set -e` has to carry it out to the script, and if it did not the
    context would be empty, no branch would match, and the script would print
    "restore completed" having restored nothing.
    """
    log = tmp_path / "stub.log"
    stripped_path = f"{harness.bin_dir}{os.pathsep}/usr/bin"

    result = subprocess.run(
        [BASH, str(SCRIPT), str(harness.archive)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            "PATH": stripped_path,
            "STUB_LOG": str(log),
            "MONGO_URI": STAGING_URI,
            "MONGO_DB": STAGING_DB,
            "RESTORE_EXEC_CONTEXT": "auto",
        },
    )

    assert result.returncode != 0, result.stdout
    assert "resolver" in result.stderr
    assert "completed" not in result.stdout.lower()
    assert not log.exists() or log.read_text(encoding="utf-8") == ""
