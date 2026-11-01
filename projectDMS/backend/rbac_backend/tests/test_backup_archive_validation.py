"""One definition of "valid backup archive", and it is not "the file exists".

Three separate defects converge here, all found by R-A8P:

* F2 - `backup_volume.sh --verify A B` passed when *either* pattern matched,
  while the runbook invoked it as though both were required. An archive holding
  an empty `appendonlydir/` and no `dump.rdb` was certified.
* F3 - `post_deploy_verify.sh` reported `falkordb-data: ok` for an 89-byte
  archive of an empty directory. It checked mtime and size, never content.
* F4 - the R-A8O rescue archive stored its entries under a `FalkorDB/` prefix,
  so restoring it into a volume mounted at `/data` produced
  `/data/FalkorDB/dump.rdb`, where the engine does not look. 14.7 MB of
  unrestorable archive.

Each of those is a different answer to the same question. These tests pin one
answer, and every caller is routed through it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rbac_backend.tests.backup_archive_fixtures import (
    AOF_MEMBERS,
    RDB_BYTES,
    incompressible,
    working_bash,
    write_archive,
)
from rbac_backend.services.backup_archive_validation import (
    INVALID_CONTENT,
    INVALID_ROOT,
    MISSING,
    UNREADABLE,
    VALID,
    ArchiveValidationError,
    validate_archive,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "scripts" / "validate_backup_archive.py"
BACKUP_VOLUME = REPO_ROOT / "scripts" / "backup_volume.sh"

# ------------------------------------------------------- the Falkor profile


def test_an_archive_with_dump_rdb_at_the_root_is_valid(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "rdb-only.tar.gz", {"./dump.rdb": RDB_BYTES})

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status == VALID
    assert verdict.ok is True


def test_an_aof_only_archive_is_valid(tmp_path: Path) -> None:
    archive = write_archive(
        tmp_path / "aof-only.tar.gz",
        dict(AOF_MEMBERS),
        directories=("./appendonlydir/",),
    )

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status == VALID


def test_an_archive_with_both_rdb_and_aof_is_valid(tmp_path: Path) -> None:
    archive = write_archive(
        tmp_path / "both.tar.gz",
        {
            "./dump.rdb": RDB_BYTES,
            **AOF_MEMBERS,
        },
        directories=("./appendonlydir/",),
    )

    assert validate_archive(archive, profile="redis-persistence").status == VALID


def test_the_eighty_nine_byte_empty_data_archive_is_refused(tmp_path: Path) -> None:
    """The exact artefact production has been producing nightly.

    `tar -czf` over an empty `/data` yields one directory entry and 89 bytes,
    and every layer above it called that a backup.
    """
    archive = write_archive(tmp_path / "empty.tar.gz", {}, directories=("./",))

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status == INVALID_CONTENT
    assert verdict.ok is False
    assert archive.stat().st_size < 200


def test_an_empty_appendonlydir_with_no_dump_rdb_is_refused(tmp_path: Path) -> None:
    """F2's concrete failure: the archive the OR semantics certified."""
    archive = write_archive(tmp_path / "hollow.tar.gz", {}, directories=("./", "./appendonlydir/"))

    assert validate_archive(archive, profile="redis-persistence").status == INVALID_CONTENT


def test_a_nested_falkordb_prefix_is_refused_as_the_wrong_restore_root(tmp_path: Path) -> None:
    """F4. Restoring this into /data yields /data/FalkorDB/dump.rdb."""
    archive = write_archive(
        tmp_path / "nested.tar.gz",
        {
            "FalkorDB/dump.rdb": RDB_BYTES,
            "FalkorDB/appendonlydir/appendonly.aof.manifest": AOF_MEMBERS[
                "./appendonlydir/appendonly.aof.manifest"
            ],
            "bin/src/falkordb.so": incompressible(65536),
        },
    )

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status == INVALID_ROOT
    assert "FalkorDB" in verdict.detail
    assert archive.stat().st_size > 1000, "size is not the signal; a big archive can be unrestorable"


def test_an_archive_of_only_binary_module_files_is_refused(tmp_path: Path) -> None:
    archive = write_archive(
        tmp_path / "modules.tar.gz",
        {"./bin/src/falkordb.so": incompressible(65536)},
    )

    assert validate_archive(archive, profile="redis-persistence").status == INVALID_CONTENT


def test_a_zero_byte_dump_rdb_is_refused(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "hollow-rdb.tar.gz", {"./dump.rdb": b""})

    assert validate_archive(archive, profile="redis-persistence").status == INVALID_CONTENT


def test_a_dump_rdb_without_the_rdb_magic_is_refused(tmp_path: Path) -> None:
    """Structural signature, where it is feasible to check one."""
    archive = write_archive(tmp_path / "fake-rdb.tar.gz", {"./dump.rdb": b"this is not an RDB file at all"})

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status == INVALID_CONTENT
    assert "REDIS" in verdict.detail


def test_a_missing_archive_is_missing_not_invalid(tmp_path: Path) -> None:
    assert validate_archive(tmp_path / "absent.tar.gz", profile="redis-persistence").status == MISSING


def test_an_unreadable_archive_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"not a gzip stream at all")

    assert validate_archive(broken, profile="redis-persistence").status == UNREADABLE


def test_an_unknown_profile_fails_closed(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "x.tar.gz", {"./dump.rdb": RDB_BYTES})

    with pytest.raises(ArchiveValidationError):
        validate_archive(archive, profile="no-such-profile")


# ------------------------------------------------- explicit ALL/ANY semantics


def _two_member_archive(tmp_path: Path, name: str, *members: str) -> Path:
    return write_archive(tmp_path / name, {member: b"payload" for member in members})


def test_require_means_every_pattern_must_be_present(tmp_path: Path) -> None:
    both = _two_member_archive(tmp_path, "both.tar.gz", "./a.dat", "./b.dat")

    verdict = validate_archive(both, require=("*a.dat", "*b.dat"))

    assert verdict.status == VALID


@pytest.mark.parametrize(
    "present",
    [("./a.dat",), ("./b.dat",), ()],
)
def test_require_refuses_when_any_required_pattern_is_absent(tmp_path: Path, present: tuple[str, ...]) -> None:
    archive = write_archive(tmp_path / "partial.tar.gz", {name: b"payload" for name in present}, directories=("./",))

    verdict = validate_archive(archive, require=("*a.dat", "*b.dat"))

    assert verdict.status == INVALID_CONTENT
    assert verdict.ok is False


def test_any_of_still_means_at_least_one(tmp_path: Path) -> None:
    archive = _two_member_archive(tmp_path, "one.tar.gz", "./a.dat")

    assert validate_archive(archive, any_of=("*a.dat", "*b.dat")).status == VALID
    assert validate_archive(archive, any_of=("*x.dat", "*y.dat")).status == INVALID_CONTENT


def test_a_pattern_is_satisfied_only_by_a_non_empty_file(tmp_path: Path) -> None:
    """An empty directory whose name matches the glob is not a persisted artefact.

    This is the same hole as the 89-byte archive, one layer down: `tar` records
    `appendonlydir/` as an entry whether or not anything was ever written into
    it, and a glob match against the entry list cannot tell the difference.
    """
    archive = write_archive(
        tmp_path / "dir-only.tar.gz",
        {},
        directories=("./", "./appendonlydir/"),
    )

    assert validate_archive(archive, any_of=("*appendonlydir*",)).status == INVALID_CONTENT
    assert validate_archive(archive, require=("*appendonlydir*",)).status == INVALID_CONTENT


def test_an_empty_file_does_not_satisfy_a_pattern(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "zero.tar.gz", {"./a.dat": b""})

    assert validate_archive(archive, require=("*a.dat",)).status == INVALID_CONTENT


def test_unexpected_extra_files_are_allowed(tmp_path: Path) -> None:
    archive = write_archive(
        tmp_path / "extra.tar.gz",
        {"./dump.rdb": RDB_BYTES, "./nodes.conf": b"whatever", "./README": b"hi"},
    )

    assert validate_archive(archive, profile="redis-persistence").status == VALID


def test_no_contract_at_all_still_refuses_an_archive_with_no_payload(
    tmp_path: Path,
) -> None:
    """F-A8T2-8. **This test asserted the defect as intended behaviour.**

    It built an archive holding nothing but the `./` directory entry that
    `tar -czf` over an empty directory writes, ran the bare contract over it and
    asserted VALID. `operations_health.py` always supplies a contract, so no
    automated caller could reach the hole - but this is the CLI an operator
    reaches for to check a rescue archive by hand before an outage, and "this
    archive restores nothing" must not read as VALID under any invocation.

    The bare contract is now the weakest claim that is still a claim about
    recoverability: readable, **and** holding at least one non-empty regular file.
    """
    archive = write_archive(tmp_path / "bare.tar.gz", {}, directories=("./",))

    verdict = validate_archive(archive)

    assert verdict.status == INVALID_CONTENT
    assert verdict.contract == "readable"
    assert "restores nothing" in verdict.detail


def test_no_contract_at_all_verifies_only_that_the_archive_reads(tmp_path: Path) -> None:
    """The legitimate purpose of the bare contract survives.

    An archive carrying real content and no declared expectation is still VALID:
    the bare contract has never claimed to know *what* should be in an archive,
    only that something is.
    """
    archive = write_archive(tmp_path / "bare.tar.gz", {"./anything.dat": b"payload"})

    verdict = validate_archive(archive)

    assert verdict.status == VALID
    assert verdict.contract == "readable"


def test_a_zero_entry_tarball_is_refused_by_the_bare_contract(tmp_path: Path) -> None:
    """The reviewer's exact reproduction: an archive with no members at all."""
    archive = write_archive(tmp_path / "nothing.tar.gz", {})

    verdict = validate_archive(archive)

    assert verdict.status == INVALID_CONTENT
    assert verdict.member_count == 0


def test_an_archive_of_only_empty_files_is_refused_by_the_bare_contract(
    tmp_path: Path,
) -> None:
    """A zero-byte member is a name, not a payload - the same rule `--require`
    has always applied, now applied when nothing was required."""
    archive = write_archive(tmp_path / "hollow.tar.gz", {"./dump.rdb": b""})

    verdict = validate_archive(archive)

    assert verdict.status == INVALID_CONTENT


def test_the_cli_exits_non_zero_for_a_payload_free_archive(tmp_path: Path) -> None:
    """The half that made this a hazard rather than a note: exit 0 and the word
    VALID on stdout are what an operator or a shell script reads."""
    archive = write_archive(tmp_path / "bare.tar.gz", {}, directories=("./",))

    result = _run_validator(str(archive))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "VALID:" not in result.stdout


# -------------------------------------------------------------- the CLI seam


def _run_validator(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_the_validator_cli_reports_valid(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "good.tar.gz", {"./dump.rdb": RDB_BYTES})

    result = _run_validator(str(archive), "--profile", "redis-persistence", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == VALID


def test_the_validator_cli_refuses_the_emptywrite_archive(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.tar.gz", {}, directories=("./",))

    result = _run_validator(str(archive), "--profile", "redis-persistence")

    assert result.returncode == 1
    assert INVALID_CONTENT in result.stdout + result.stderr


def test_the_validator_cli_rejects_an_unknown_profile(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "good.tar.gz", {"./dump.rdb": RDB_BYTES})

    result = _run_validator(str(archive), "--profile", "nope")

    assert result.returncode == 2


# ------------------------------------------------ backup_volume.sh delegates


BASH = working_bash()


def _verify(archive: Path, *args: str, python_bin: str | None = sys.executable) -> "subprocess.CompletedProcess[str]":
    assert BASH is not None
    env = dict(os.environ)
    if python_bin is None:
        env.pop("PYTHON_BIN", None)
        env["PATH"] = str(archive.parent)
    else:
        env["PYTHON_BIN"] = python_bin
    return subprocess.run(
        [BASH, str(BACKUP_VOLUME), "--verify", archive.name, *args],
        cwd=archive.parent,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_the_shell_verifier_uses_the_same_profile_contract(tmp_path: Path) -> None:
    good = write_archive(tmp_path / "good.tar.gz", {"./dump.rdb": RDB_BYTES})
    hollow = write_archive(tmp_path / "hollow.tar.gz", {}, directories=("./", "./appendonlydir/"))
    nested = write_archive(tmp_path / "nested.tar.gz", {"FalkorDB/dump.rdb": RDB_BYTES})

    assert _verify(good, "--profile", "redis-persistence").returncode == 0
    assert _verify(hollow, "--profile", "redis-persistence").returncode != 0
    assert _verify(nested, "--profile", "redis-persistence").returncode != 0


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_the_shell_verifier_honours_require_as_a_conjunction(tmp_path: Path) -> None:
    both = write_archive(tmp_path / "both.tar.gz", {"./a.dat": b"x", "./b.dat": b"y"})
    only_a = write_archive(tmp_path / "only-a.tar.gz", {"./a.dat": b"x"})

    assert _verify(both, "--require", "*a.dat", "--require", "*b.dat").returncode == 0
    assert _verify(only_a, "--require", "*a.dat", "--require", "*b.dat").returncode != 0
    assert _verify(only_a, "--any-of", "*a.dat", "--any-of", "*b.dat").returncode == 0


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_the_shell_verifier_fails_closed_without_an_interpreter(tmp_path: Path) -> None:
    """No Python means the contract cannot be evaluated, which is not a pass."""
    good = write_archive(tmp_path / "good.tar.gz", {"./dump.rdb": RDB_BYTES})

    result = _verify(good, "--profile", "redis-persistence", python_bin="/nonexistent/python")

    assert result.returncode != 0
    assert "python" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# F-A8T2-2 - `redis-persistence` certified payloads that cannot be restored.
#
# `rdb_ok` compared the five-byte `REDIS` magic and nothing else, so a five-byte
# file containing exactly that word validated. `aof_ok` was true for ANY
# non-empty member under `appendonlydir/` when no `*.base.rdb` was present, so a
# manifest alone - or a README a helper script dropped there - validated.
#
# This is the profile `falkordb_rescue_archive.sh` certifies its output through,
# and a valid rescue archive is a NO-GO gate before every same-host outage. An
# archive that certifies VALID and restores nothing is the exact silent success
# the module exists to prevent.
# --------------------------------------------------------------------------- #


def test_f_a8t2_2_a_five_byte_dump_rdb_is_refused(tmp_path: Path) -> None:
    """The word `REDIS` is not an RDB snapshot."""

    archive = write_archive(tmp_path / "magic-only.tar.gz", {"./dump.rdb": b"REDIS"})

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status != "VALID"
    assert "restorable RDB snapshot" in verdict.detail


def test_f_a8t2_2b_a_dump_rdb_without_a_version_is_refused(tmp_path: Path) -> None:
    """`REDISxxxx` carries the magic but no four-digit version."""

    payload = b"REDIS" + b"abcd" + b"\xff" + b"\x00" * 8
    archive = write_archive(tmp_path / "no-version.tar.gz", {"./dump.rdb": payload})

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status != "VALID"


def test_f_a8t2_2c_an_appendonlydir_of_passengers_is_refused(tmp_path: Path) -> None:
    """A non-empty directory is not a replayable AOF."""

    archive = write_archive(
        tmp_path / "aof-junk.tar.gz", {"./appendonlydir/README.txt": b"hello"}
    )

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status != "VALID"
    assert "no replayable member" in verdict.detail


def test_f_a8t2_2d_an_appendonlydir_of_manifest_alone_is_refused(tmp_path: Path) -> None:
    """The manifest names files; it is not one of them."""

    archive = write_archive(
        tmp_path / "aof-manifest-only.tar.gz",
        {"./appendonlydir/appendonly.aof.manifest": AOF_MEMBERS["./appendonlydir/appendonly.aof.manifest"]},
    )

    verdict = validate_archive(archive, profile="redis-persistence")

    assert verdict.status != "VALID"
    assert "no replayable member" in verdict.detail


def test_f_a8t2_2e_the_genuine_shapes_are_still_valid(tmp_path: Path) -> None:
    """The affirmative half - neither real artefact may be caught by the fix."""

    rdb = write_archive(tmp_path / "rdb.tar.gz", {"./dump.rdb": RDB_BYTES})
    aof = write_archive(tmp_path / "aof.tar.gz", dict(AOF_MEMBERS))

    assert validate_archive(rdb, profile="redis-persistence").status == "VALID"
    assert validate_archive(aof, profile="redis-persistence").status == "VALID"


def test_a_declared_contract_keeps_its_own_refusal_message(tmp_path: Path) -> None:
    """R-A8U's first cut of F-A8T2-8 replaced it, and CI caught that.

    The zero-payload rule belongs to the BARE contract. With `--require` or
    `--any-of` in hand, the contract's own message names the entries it wanted -
    `test_deployment_config.py` asserts the `any-of` refusal says "none of the
    accepted entries are present" - and a generic "restores nothing" in its
    place tells the operator strictly less.
    """
    archive = write_archive(tmp_path / "empty.tar.gz", {}, directories=("./",))

    verdict = validate_archive(archive, any_of=("*dump.rdb", "*appendonlydir*"))

    assert verdict.status == INVALID_CONTENT
    assert "none of the accepted entries are present" in verdict.detail

    required = validate_archive(archive, require=("*dump.rdb",))
    assert required.status == INVALID_CONTENT
    assert "absent or empty" in required.detail
