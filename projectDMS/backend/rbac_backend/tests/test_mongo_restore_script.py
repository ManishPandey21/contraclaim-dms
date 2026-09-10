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

R-A8M then found the third thing this script owed and did not do: it certified.
The Gate 8 idempotency run reported `0 document(s) restored successfully. 222
document(s) failed to restore.`, `mongorestore` exited 0 because duplicate-key
failures are not fatal to it, and the script printed `MongoDB restore completed`.
An exit status is now a necessary condition and not a sufficient one - what the
archive offered and what landed are counted and compared, and the comparison
decides the exit code (F-A8M-3).

These are behavioural tests. `mongorestore` and `docker` are replaced by stubs on
PATH that record their arguments, so the script's decisions are observed rather
than read - a static check cannot tell a documented branch from a taken one. Two
of them go further and replay verbatim output from the real tool, so the parser
is measured against the format it will actually meet.
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
#:
#: `/bin/sh` by absolute path, and shell builtins only - no shebang that has to
#: be resolved through PATH, and no `basename`. A stub that needs PATH to find
#: its own interpreter, or to find a utility, cannot run in the built layouts
#: below, where PATH holds only the directories a test created.
#:
#: F-A8M-3. The stub also speaks `mongorestore`'s accounting, because the script
#: now reads it. A stub that only recorded its arguments would let the script's
#: verification see nothing at all, and "nothing to read" is one of the outcomes
#: under test - so it is produced deliberately (`STUB_QUIET=1`) rather than by
#: default. All of it goes to stderr, which is where the real tool writes it.
_STUB = (
    "#!/bin/sh\n"
    'printf \'%s\' "${0##*/}" >>"$STUB_LOG"\n'
    'for arg in "$@"; do printf \' %s\' "$arg" >>"$STUB_LOG"; done\n'
    "printf '\\n' >>\"$STUB_LOG\"\n"
    # The pre-restore inventory pass. `mongorestore --dryRun -v` with no
    # --nsInclude names every namespace the archive carries, which is the only
    # source that can see a collection a wrong --nsInclude excluded. The stub
    # answers it the way mongo:8.0 does - measured, not invented.
    'case " $* " in\n'
    '  *" --dryRun "*)\n'
    '    if [ -z "${STUB_NO_DRYRUN:-}" ]; then\n'
    '      for offered in ${STUB_ARCHIVE_NS-${STUB_RESTORE_NS-${MONGO_DB:-db}.permissions} ${STUB_RESTORE_UNAPPLIED_NS:-}}; do\n'
    # Single-quoted format string: in `sh`, a backtick inside double quotes is
    # command substitution, so the real tool's backticked namespace has to be
    # quoted this way or the stub would try to execute `%s`.
    "        printf 'archive prelude `%s`\\n' \"$offered\" >&2\n"
    "      done\n"
    '      echo "dry run completed" >&2\n'
    '      echo "0 document(s) restored successfully. 0 document(s) failed to restore." >&2\n'
    "    fi\n"
    "    exit 0\n"
    "    ;;\n"
    "esac\n"
    'if [ -z "${STUB_QUIET:-}" ]; then\n'
    '  ns="${STUB_RESTORE_NS:-${MONGO_DB:-db}.permissions}"\n'
    '  docs="${STUB_RESTORE_DOCS:-198}"\n'
    '  failed="${STUB_RESTORE_FAILED:-0}"\n'
    '  applied="${STUB_RESTORE_APPLIED:-$docs}"\n'
    '  for offered in $ns ${STUB_RESTORE_UNAPPLIED_NS:-}; do\n'
    # The real tool quotes the source: ``... from `archive on stdin```. Quoted
    # here too, and never as the bare word, because `forbid-legacy-authz` greps
    # every staged .py for `from <word>archive` - a stub line that reads like an
    # import is a commit this file would silently block.
    '    echo "reading metadata for $offered from \'archive on stdin\'" >&2\n'
    "  done\n"
    '  for name in $ns; do\n'
    '    echo "finished restoring $name ($applied document(s), $failed failure(s))" >&2\n'
    "  done\n"
    '  echo "$applied document(s) restored successfully. $failed document(s) failed to restore." >&2\n'
    "fi\n"
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
    """Skip for a developer without bash; FAIL when the run is evidence.

    A whole shell suite that vanishes green is the shape `staging_gate.py`
    exists to prevent: the run reports success having measured nothing. On CI
    and inside a certification run there is always a bash, so an absent one
    there is a broken environment and must be reported as a failure, not as a
    skip.
    """
    if BASH is not None:
        return
    if os.environ.get("CI") or os.environ.get("CONTRACLAIM_STAGING_GATE"):
        pytest.fail(
            "no usable bash on this host, and this run is being offered as "
            "evidence (CI / CONTRACLAIM_STAGING_GATE). A skipped shell suite "
            "measures nothing."
        )
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
    run.log = log
    return run


@pytest.fixture()
def built_layout(tmp_path: Path, harness):
    """Run the script against a PATH this test built, never the one the host has.

    F-A8L-1. The no-resolver control used to create its precondition by stripping
    PATH to ``<stub bin>:/usr/bin``. On the Windows development host git-bash's
    ``/usr/bin`` carries no python and no getent, so the precondition held and the
    control passed. In the release image ``/usr/bin/getent`` exists - measured, not
    assumed - so a resolver always answered, the refusal branch was never reached,
    and the same control failed. The control was reading the host, not the script.

    Here every directory on PATH is created by the test, so which resolvers exist
    is *stated* rather than inherited, and the answer is the same on every host.
    Borrowing a system directory is rejected outright below, because that is the
    defect itself rather than a stylistic preference.

    ``ROOT_DIR`` is passed explicitly: the script derives it with ``dirname``,
    which is not on a built PATH. That seam already exists in the script and is
    used here rather than widening the script's behaviour.
    """
    system_dir = tmp_path / "built-system-bin"
    system_dir.mkdir()

    def run(*, resolvers=(), env=None, uri=STAGING_URI, db=STAGING_DB, argv=None):
        for name, body in resolvers:
            stub = system_dir / name
            stub.write_text(f"#!/bin/sh\n{body}", encoding="utf-8", newline="\n")
            stub.chmod(0o755)

        path_dirs = [harness.bin_dir, system_dir]
        for directory in path_dirs:
            assert tmp_path in directory.parents, (
                f"{directory} was not built by this test. The layout has to be "
                f"built rather than borrowed: borrowing a system directory is "
                f"exactly F-A8L-1, where the control passed on a host whose "
                f"/usr/bin had no resolver and failed in a container whose did."
            )
        run.path = os.pathsep.join(str(directory) for directory in path_dirs)

        environment = {
            "PATH": run.path,
            "STUB_LOG": str(harness.log),
            "ROOT_DIR": str(REPO_ROOT),
            "RESTORE_EXEC_CONTEXT": "auto",
        }
        if uri is not None:
            environment["MONGO_URI"] = uri
        if db is not None:
            environment["MONGO_DB"] = db
        environment.update(env or {})

        result = subprocess.run(
            [BASH, str(SCRIPT), *(argv if argv is not None else [str(harness.archive)])],
            capture_output=True,
            text=True,
            env=environment,
            cwd=str(REPO_ROOT),
        )
        result.stub_log = (
            harness.log.read_text(encoding="utf-8") if harness.log.exists() else ""
        )
        return result

    run.path = None
    run.system_dir = system_dir
    return run


#: A resolver stub answers the way `resolves_locally()` reads answers: with a word
#: on stdout for python, and with an exit status for getent. Each layout below
#: names the machine it stands for.
_NO_RESOLVER: tuple = ()
_PYTHON_SAYS_YES = (("python3", "printf 'YES\\n'\n"),)
_PYTHON_SAYS_NO = (("python3", "printf 'NO\\n'\n"),)
_GETENT_FINDS_IT = (("getent", "exit 0\n"),)
_GETENT_DOES_NOT = (("getent", "exit 2\n"),)


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


def test_auto_context_refuses_when_no_resolver_exists(built_layout) -> None:
    """"Cannot tell" and "definitely not local" must not collapse into each other.

    With no `getent` and no python on PATH, `auto` has no way to decide. Assuming
    "unresolvable" would send a perfectly local restore into a container that may
    not exist, so it refuses and names both explicit contexts instead.

    The refusal happens inside a command substitution, which only exits the
    subshell - `set -e` has to carry it out to the script, and if it did not the
    context would be empty, no branch would match, and the script would print
    "restore completed" having restored nothing.
    """
    result = built_layout(resolvers=_NO_RESOLVER)

    assert result.returncode != 0, result.stdout
    assert "resolver" in result.stderr
    assert "completed" not in result.stdout.lower()
    assert result.stub_log == ""


def test_the_no_resolver_layout_actually_has_no_resolver(built_layout) -> None:
    """The precondition is measured, not hoped for.

    This is the assertion F-A8L-1 was missing. A refusal test proves nothing if
    the condition it thinks it created does not exist: with a resolver on PATH the
    script correctly picks a context, the refusal never fires, and the failure
    reads as a script defect when it is a harness one. So the layout is probed
    with the same PATH the script was given, and every resolver `resolves_locally`
    consults must be absent from it.
    """
    built_layout(resolvers=_NO_RESOLVER)

    probe = subprocess.run(
        [BASH, "-c", "command -v python3 python getent || echo NONE"],
        capture_output=True,
        text=True,
        env={"PATH": built_layout.path},
    )

    assert probe.stdout.strip() == "NONE", (
        f"a resolver is reachable from the no-resolver layout: {probe.stdout!r}"
    )


@pytest.mark.parametrize(
    ("resolvers", "reason"),
    [
        pytest.param((("python3", "exit 9\n"),), "exits non-zero", id="exits-non-zero"),
        pytest.param(
            (("python3", "printf 'maybe\\n'\n"),), "answers neither word", id="unparseable"
        ),
    ],
)
def test_auto_context_refuses_when_the_resolver_cannot_answer(
    built_layout, resolvers, reason
) -> None:
    """A resolver that is present but broken is still "cannot tell".

    A Windows Store `python3` shim or a broken venv shebang is on PATH and exits
    non-zero for reasons that have nothing to do with the name being looked up.
    Reading that as "the host does not resolve" sends a perfectly local restore
    into a container. Only a literal YES or NO is an answer; anything else falls
    through, and with nothing left to ask the script refuses rather than guesses.
    """
    result = built_layout(resolvers=resolvers)

    assert result.returncode != 0, f"a resolver that {reason} must not decide"
    assert "resolver" in result.stderr
    assert result.stub_log == ""


@pytest.mark.parametrize(
    ("resolvers", "expected"),
    [
        pytest.param(_NO_RESOLVER, "refused", id="windows-git-bash-no-resolver"),
        pytest.param(_GETENT_DOES_NOT, "compose", id="debian-container-getent"),
        pytest.param(_GETENT_FINDS_IT, "host", id="debian-container-getent-resolves"),
        pytest.param(_PYTHON_SAYS_NO, "compose", id="python-resolver-says-no"),
        pytest.param(_PYTHON_SAYS_YES, "host", id="python-resolver-says-yes"),
    ],
)
def test_the_auto_decision_is_the_resolver_answer_in_every_layout(
    built_layout, resolvers, expected
) -> None:
    """The same five machines, whichever machine the suite runs on.

    `windows-git-bash-no-resolver` is the development host, whose `/usr/bin` has
    neither python nor getent. `debian-container-*` is the release image, whose
    `/usr/bin/getent` exists - that layout is why the old control could not hold
    in the container. Each row states its own resolver, so the row that runs is
    the row that was written, and no row is decided by the host underneath.
    """
    result = built_layout(resolvers=resolvers)

    if expected == "refused":
        assert result.returncode != 0
        assert "resolver" in result.stderr
        assert result.stub_log == ""
        return

    assert result.returncode == 0, result.stderr
    assert f"execution context: {expected}" in result.stdout
    if expected == "host":
        assert result.stub_log.startswith("mongorestore "), result.stub_log
    else:
        assert result.stub_log.startswith("docker "), result.stub_log


def test_a_relative_archive_path_survives_the_compose_branch(harness, tmp_path: Path) -> None:
    """The compose branch `cd`s to the repository root before running docker.

    A relative archive path stops resolving there - and it stops resolving *after*
    the production refusals have already passed, so the failure would arrive
    looking like a docker problem rather than a path one.
    """
    relative = harness.archive.name
    staged = REPO_ROOT / relative
    staged.write_bytes(harness.archive.read_bytes())
    try:
        result = harness(argv=[relative], env={"RESTORE_EXEC_CONTEXT": "compose"})
    finally:
        staged.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert result.stub_log.startswith("docker ")


# --------------------------------------------------------------------------- #
# F-A8M-3 - a restore is not certified by an exit code
# --------------------------------------------------------------------------- #
#
# R-A8M's Gate 8 idempotency run reported
#
#   0 document(s) restored successfully. 222 document(s) failed to restore.
#
# and this script printed "MongoDB restore completed" and exited 0, because
# duplicate-key failures are not fatal to `mongorestore`. The disaster path -
# restoring into an empty database - was unaffected, which is exactly why the
# defect survived: the run that matters most is the one that happens to agree
# with the wrong check.
#
# The stubs below speak `mongorestore`'s accounting, so each row states what the
# tool reported and the script's verdict is observed rather than read.


def _evidence(stdout: str) -> dict:
    """The structured block, parsed. Prose is deliberately not consulted."""
    fields = {}
    for line in stdout.splitlines():
        if "=" in line and not line.startswith("-"):
            key, _, value = line.partition("=")
            if key.isupper():
                fields[key] = value
    return fields


def test_the_r_a8m_shape_is_a_failure(harness) -> None:
    """0 restored, 222 failed, exit 0 - the exact run that certified itself."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_APPLIED": "0",
            "STUB_RESTORE_FAILED": "222",
        }
    )

    assert result.returncode != 0, "a restore that restored nothing reported success"
    assert "completed" not in result.stdout.lower()
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_DOCS"] == "222"
    assert evidence["RESTORED_DOCS"] == "0"
    assert evidence["FAILED_DOCS"] == "222"
    assert evidence["MATCH"] == "NO"
    assert evidence["STATUS"] == "FAILED"


def test_a_partial_restore_is_a_failure(harness) -> None:
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_APPLIED": "180",
            "STUB_RESTORE_FAILED": "18",
        }
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_DOCS"] == "198"
    assert evidence["RESTORED_DOCS"] == "180"
    assert evidence["STATUS"] == "FAILED"


def test_a_shortfall_against_an_operator_declared_expectation_is_a_failure(harness) -> None:
    """The archive's own accounting cannot see documents it never read. An
    operator who knows the count states it, and then the archive has to match."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_APPLIED": "150",
            "RESTORE_EXPECTED_DOCS": "222",
        }
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_SOURCE"] == "operator-declared"
    assert evidence["EXPECTED_DOCS"] == "222"
    assert evidence["RESTORED_DOCS"] == "150"
    assert evidence["STATUS"] == "FAILED"


def test_an_undeclared_zero_document_restore_is_a_failure(harness) -> None:
    """A wrong database, a wrong --nsInclude and a genuinely empty archive all
    look like this from here. Only the operator can tell them apart, so the
    benign reading has to be declared rather than assumed."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_APPLIED": "0",
            "STUB_RESTORE_NS": "",
        }
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert evidence["STATUS"] == "FAILED"
    assert "RESTORE_ALLOW_EMPTY" in result.stderr


def test_a_declared_empty_archive_is_an_explicit_success(harness) -> None:
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_APPLIED": "0",
            "STUB_RESTORE_NS": "",
            "RESTORE_ALLOW_EMPTY": "1",
        }
    )

    assert result.returncode == 0, result.stderr
    evidence = _evidence(result.stdout)
    assert evidence["STATUS"] == "OK_EMPTY"
    assert evidence["RESTORED_DOCS"] == "0"


def test_a_collection_the_archive_carried_and_the_restore_skipped_is_a_failure(
    harness,
) -> None:
    """"Command succeeded" and "everything arrived" are different claims. A
    namespace the archive offered and the restore never applied is a semantic
    mismatch even though every document it did apply landed."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_RESTORE_UNAPPLIED_NS": f"{STAGING_DB}.documents",
        }
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert f"{STAGING_DB}.documents" in evidence["MISSING_COLLECTIONS"]
    assert evidence["STATUS"] == "FAILED"


def test_a_restore_that_reports_nothing_is_unverified_not_successful(harness) -> None:
    """`mongorestore` exiting 0 while saying nothing this script can read is not
    evidence of anything. Certifying it would be the same mistake in a new
    format."""
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host", "STUB_QUIET": "1"})

    assert result.returncode != 0
    assert "UNVERIFIED" in result.stderr
    assert "completed" not in result.stdout.lower()


def test_verification_can_be_declined_but_never_silently(harness) -> None:
    """An operator may decide the check is not available to them. What they may
    not do is get a success message for it."""
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "host", "STUB_QUIET": "1", "RESTORE_VERIFY": "0"}
    )

    assert result.returncode == 0, result.stderr
    assert "UNVERIFIED" in result.stdout + result.stderr
    assert "restore completed" not in result.stdout.lower()


def test_a_healthy_restore_still_passes_and_says_what_it_did(harness) -> None:
    """The positive control. Without it every assertion above is satisfied by a
    script that refuses everything."""
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host"})

    assert result.returncode == 0, result.stderr
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_DATABASE"] == STAGING_DB
    assert evidence["RESTORED_DATABASE"] == STAGING_DB
    assert evidence["EXPECTED_DOCS"] == "198"
    assert evidence["RESTORED_DOCS"] == "198"
    assert evidence["FAILED_DOCS"] == "0"
    assert evidence["MISSING_COLLECTIONS"] == "<none>"
    assert evidence["MATCH"] == "YES"
    assert evidence["STATUS"] == "OK"
    assert "MongoDB restore completed" in result.stdout


def test_the_same_verification_applies_in_the_compose_context(harness) -> None:
    """The compose branch is the one Gate 8 actually runs. A verification that
    only guarded the host branch would have missed R-A8M entirely."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "compose",
            "STUB_RESTORE_APPLIED": "0",
            "STUB_RESTORE_FAILED": "222",
        }
    )

    assert result.returncode != 0
    assert _evidence(result.stdout)["STATUS"] == "FAILED"


def test_the_verification_does_not_write_the_connection_uri_to_a_temporary_file(
    harness, tmp_path: Path
) -> None:
    """The tool echoes the URI, which carries credentials, and a restore is
    routinely run as root. Capturing its output through a file in a shared
    temporary directory would be this script creating a secret leak of its own."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "$(mktemp" not in source, "the restore output is being staged through a file"
    assert "restore_output=$(run_restore 2>&1)" in source


# --------------------------------------------------------------------------- #
# The parser against the real tool's real output
# --------------------------------------------------------------------------- #
#
# The stubs above emit whatever this file tells them to, so on their own they
# prove the script's *logic* and nothing about its *format*. These two rows are
# verbatim `mongorestore` output, captured from mongo:8.0 - the image this
# deployment runs - restoring a 222-document archive into a disposable container
# in both states: an empty target, and the populated target that produced the
# R-A8M false success. Backticks, timestamps, tabs and all.

_REAL_MONGORESTORE_SUCCESS = """\
2026-09-06T20:27:45.736+0000\tpreparing collections to restore from
2026-09-06T20:27:45.747+0000\treading metadata for `ra8n_disposable.beta` from `archive on stdin`
2026-09-06T20:27:45.748+0000\treading metadata for `ra8n_disposable.alpha` from `archive on stdin`
2026-09-06T20:27:45.820+0000\trestoring `ra8n_disposable.beta` from `archive on stdin`
2026-09-06T20:27:45.833+0000\tfinished restoring `ra8n_disposable.beta` (92 documents, 0 failures)
2026-09-06T20:27:45.910+0000\trestoring `ra8n_disposable.alpha` from `archive on stdin`
2026-09-06T20:27:45.946+0000\tfinished restoring `ra8n_disposable.alpha` (130 documents, 0 failures)
2026-09-06T20:27:45.946+0000\tno indexes to restore for collection `ra8n_disposable.beta`
2026-09-06T20:27:45.946+0000\t222 document(s) restored successfully. 0 document(s) failed to restore.
"""

_REAL_MONGORESTORE_ALL_DUPLICATES = """\
2026-09-06T20:28:20.700+0000\tpreparing collections to restore from
2026-09-06T20:28:20.740+0000\treading metadata for `ra8n_disposable.beta` from `archive on stdin`
2026-09-06T20:28:20.741+0000\treading metadata for `ra8n_disposable.alpha` from `archive on stdin`
2026-09-06T20:28:20.828+0000\tcontinuing through error: E11000 duplicate key error collection: ra8n_disposable.alpha index: _id_ dup key: { _id: 129 }
2026-09-06T20:28:20.828+0000\tfinished restoring `ra8n_disposable.beta` (0 documents, 92 failures)
2026-09-06T20:28:20.828+0000\tfinished restoring `ra8n_disposable.alpha` (0 documents, 130 failures)
2026-09-06T20:28:20.828+0000\t0 document(s) restored successfully. 222 document(s) failed to restore.
"""


@pytest.fixture()
def replaying_harness(tmp_path: Path, harness):
    """A `mongorestore` that reads back a recorded transcript, byte for byte."""

    def run(transcript: str, *, env=None, db=STAGING_DB):
        recorded = tmp_path / "transcript.txt"
        recorded.write_text(transcript, encoding="utf-8", newline="\n")
        replay = harness.bin_dir / "mongorestore"
        replay.write_text(
            "#!/bin/sh\n"
            'printf \'%s\n\' "mongorestore" >>"$STUB_LOG"\n'
            f'while IFS= read -r line; do printf \'%s\n\' "$line" >&2; done <"{recorded}"\n'
            "exit 0\n",
            encoding="utf-8",
            newline="\n",
        )
        replay.chmod(0o755)
        return harness(env={"RESTORE_EXEC_CONTEXT": "host", **(env or {})}, db=db)

    return run


def test_the_parser_reads_real_mongorestore_success_output(replaying_harness) -> None:
    result = replaying_harness(
        _REAL_MONGORESTORE_SUCCESS, db="ra8n_disposable"
    )

    assert result.returncode == 0, result.stderr
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_DOCS"] == "222"
    assert evidence["RESTORED_DOCS"] == "222"
    assert evidence["FAILED_DOCS"] == "0"
    # Backticks stripped on both sides; comparing a quoted name against an
    # unquoted one would report every collection as missing.
    assert evidence["RESTORED_COLLECTIONS"] == (
        "ra8n_disposable.beta ra8n_disposable.alpha"
    )
    assert evidence["MISSING_COLLECTIONS"] == "<none>"
    assert evidence["STATUS"] == "OK"


def test_the_parser_reads_the_real_r_a8m_false_success_output(replaying_harness) -> None:
    """The transcript this script used to certify. Same bytes, opposite verdict."""
    result = replaying_harness(
        _REAL_MONGORESTORE_ALL_DUPLICATES, db="ra8n_disposable"
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert evidence["EXPECTED_DOCS"] == "222"
    assert evidence["RESTORED_DOCS"] == "0"
    assert evidence["FAILED_DOCS"] == "222"
    assert evidence["MATCH"] == "NO"
    assert evidence["STATUS"] == "FAILED"
    assert "completed" not in result.stdout.lower()


# --------------------------------------------------------------------------- #
# The pre-restore inventory
# --------------------------------------------------------------------------- #
#
# The document axis is reconciled from the real pass's own accounting, because a
# mongodump archive does not carry per-collection counts and `--dryRun` reports
# `0 document(s) restored successfully` by construction - measured against
# mongo:8.0, not assumed. What a dry run DOES give is the archive's namespace
# inventory, read before anything is written:
#
#   archive prelude `ra8n_disposable.beta`
#
# That is the only source that can see a collection the restore never reached
# because it was aimed at the wrong database - the real pass's own
# `reading metadata for` lines are already filtered by `--nsInclude`.


def test_a_restore_aimed_at_the_wrong_database_is_diagnosed_not_merely_refused(
    harness,
) -> None:
    """Before the inventory pass this was indistinguishable from an empty
    archive, and the operator was told to declare the archive empty - advice
    that would have hidden the real fault."""
    result = harness(
        env={
            "RESTORE_EXEC_CONTEXT": "host",
            "STUB_ARCHIVE_NS": "someotherdb.alpha someotherdb.beta",
            "STUB_RESTORE_NS": "",
            "STUB_RESTORE_APPLIED": "0",
        }
    )

    assert result.returncode != 0
    evidence = _evidence(result.stdout)
    assert evidence["STATUS"] == "FAILED"
    assert evidence["EXPECTED_COLLECTION_SOURCE"].startswith("archive prelude")
    assert "someotherdb.alpha" in evidence["EXCLUDED_BY_TARGET"]
    assert "aimed at the wrong database" in result.stderr


def test_the_inventory_is_taken_before_the_restore_writes_anything(harness) -> None:
    """Order matters: an inventory taken afterwards cannot be an inventory. The
    stub log records both invocations, and the dry run has to be first."""
    harness(env={"RESTORE_EXEC_CONTEXT": "host"})

    lines = [line for line in harness.log.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) >= 2, lines
    assert "--dryRun" in lines[0], f"the first mongorestore call was not the dry run: {lines[0]}"
    assert "--dryRun" not in lines[1]
    assert "--nsInclude" not in lines[0], (
        "the inventory pass must not be filtered by --nsInclude, or it cannot see "
        "the collections a wrong target excluded"
    )
    assert f"--nsInclude={STAGING_DB}.*" in lines[1]


def test_a_mongorestore_without_dryrun_degrades_to_a_named_weaker_source(harness) -> None:
    """An older tool yields no inventory. The verification must say so rather
    than compare a set against itself while reporting `MATCH=YES` unqualified."""
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host", "STUB_NO_DRYRUN": "1"})

    assert result.returncode == 0, result.stderr
    evidence = _evidence(result.stdout)
    assert evidence["STATUS"] == "OK"
    assert evidence["EXPECTED_COLLECTION_SOURCE"].startswith("restore metadata")


def test_an_unreachable_endpoint_is_never_certified(harness) -> None:
    """`mongorestore` exiting non-zero - a wrong endpoint, a wrong replica set, a
    container that is not running - must not reach the verification at all."""
    result = harness(env={"RESTORE_EXEC_CONTEXT": "host", "STUB_EXIT": "1"})

    assert result.returncode != 0
    assert "nothing is certified" in result.stderr
    assert "STATUS=OK" not in result.stdout
    assert "completed" not in result.stdout.lower()


def test_a_malformed_expectation_is_refused_before_the_restore_runs(harness) -> None:
    """A refusal that arrives after an irreversible operation is not a refusal."""
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "host", "RESTORE_EXPECTED_DOCS": "two hundred"}
    )

    assert result.returncode != 0
    assert "RESTORE_EXPECTED_DOCS" in result.stderr
    assert result.stub_log == "", "the restore ran before the input was validated"


def test_the_declared_expectation_is_never_echoed_back_verbatim(harness) -> None:
    """The value is operator input on a command line beside real secrets; the
    refusal names the variable, not what was in it."""
    result = harness(
        env={"RESTORE_EXEC_CONTEXT": "host", "RESTORE_EXPECTED_DOCS": "s3cr3t-looking"}
    )

    assert result.returncode != 0
    assert "s3cr3t-looking" not in result.stderr


def test_credentials_in_the_tools_output_are_redacted_before_being_printed(
    harness,
) -> None:
    """The captured output is echoed for the operator, and release evidence is a
    file an operator piped that output into. `compose config leaks secrets into
    evidence` is the same lesson: redact by value."""

    noisy = harness.bin_dir / "mongorestore"
    noisy.write_text(
        "#!/bin/sh\n"
        'printf \'%s\' "${0##*/}" >>"$STUB_LOG"\n'
        'for arg in "$@"; do printf \' %s\' "$arg" >>"$STUB_LOG"; done\n'
        "printf '\n' >>\"$STUB_LOG\"\n"
        'case " $* " in *" --dryRun "*) exit 0 ;; esac\n'
        'echo "connected to mongodb://appuser:hunter2@mongo1:27017/db" >&2\n'
        'echo "finished restoring db.permissions (1 documents, 0 failures)" >&2\n'
        'echo "1 document(s) restored successfully. 0 document(s) failed to restore." >&2\n',
        encoding="utf-8",
        newline="\n",
    )
    noisy.chmod(0o755)

    result = harness(env={"RESTORE_EXEC_CONTEXT": "host"}, db="db")

    combined = result.stdout + result.stderr
    assert "hunter2" not in combined, "the tool's credentials reached the operator's log"
    assert "appuser" not in combined
    assert "mongodb://***:***@mongo1:27017" in combined, (
        "the URI was removed entirely rather than redacted; the operator still "
        "needs to see which host was contacted"
    )


# --------------------------------------------------------------------------- #
# F-A8T2-3 - the production refusal was a case-SENSITIVE literal substring.
#
# `*"replicaSet=${PRODUCTION_REPLICA_SET}"*` matched one spelling. MongoDB
# connection-string options are case-insensitive, so `?replicaset=rs0` is the
# same production connection and walked straight past it. A member addressed
# directly with `directConnection=true` names no replica set at all, so the
# option check could not see it either - and the comment at the top of the
# script claims both cases are covered.
# --------------------------------------------------------------------------- #


def test_f_a8t2_3_a_lowercase_production_replica_set_is_refused(harness) -> None:
    result = harness(uri="mongodb://m:27017/?replicaset=rs0", db="contraclaim_staging")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "production replica set rs0" in result.stderr
    assert "mongorestore" not in result.stub_log


def test_f_a8t2_3b_a_mixed_case_production_replica_set_is_refused(harness) -> None:
    result = harness(uri="mongodb://m:27017/?ReplicaSet=RS0", db="contraclaim_staging")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "production replica set rs0" in result.stderr


def test_f_a8t2_3c_a_direct_connection_to_a_production_member_is_refused(harness) -> None:
    """No replica set is named, so the host is the only thing that can say."""

    result = harness(
        uri="mongodb://mongo1:27017/?directConnection=true", db="contraclaim_staging"
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "names no replica set" in result.stderr
    assert "mongorestore" not in result.stub_log


def test_f_a8t2_3d_the_production_database_name_is_still_refused(harness) -> None:
    result = harness(uri=STAGING_URI, db="contraclaim")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "production database" in result.stderr


def test_f_a8t2_3e_the_staging_replica_set_is_still_allowed(harness) -> None:
    """The affirmative half: the staging URI names mongo1 too, and must pass.

    The compose SERVICE name is identical in both stacks, which is why the host
    rule fires only on a URI that names no replica set at all.
    """

    result = harness(uri=STAGING_URI, db=STAGING_DB)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "mongorestore" in result.stub_log
