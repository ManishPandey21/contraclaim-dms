"""Shared helpers for building backup archives in tests.

Three test modules were each carrying their own `RDB_BYTES`, their own
`tar.gz` builder and their own `bash` probe. Three copies of a fixture is how
one of them quietly stops matching what the code under test actually sees -
and "seed fixtures with complete, model-valid shapes" is a house rule here for
exactly that reason.

Nothing in this module asserts anything. It builds artefacts that look like the
real ones: a real RDB header, a real multi-part AOF layout, a real gzip member
list.
"""

from __future__ import annotations

import io
import random
import shutil
import subprocess
import tarfile
from pathlib import Path

#: The first bytes of any RDB file: the ASCII magic plus a four-digit version.
#: `backup_archive_validation` checks exactly this, so a fixture that omits it
#: is not a snapshot.
RDB_BYTES = b"REDIS0011\xfa\x09redis-ver\x057.4.0\xff\x00\x00\x00\x00\x00\x00\x00\x00"

#: A multi-part AOF as Redis 7 writes one: a manifest naming a base RDB and an
#: incremental file, both of which exist.
AOF_MANIFEST = b"file appendonly.aof.1.base.rdb seq 1 type b\nfile appendonly.aof.1.incr.aof seq 1 type i\n"
AOF_INCR = b"*2\r\n$6\r\nSELECT\r\n$1\r\n0\r\n"

AOF_MEMBERS = {
    "./appendonlydir/appendonly.aof.manifest": AOF_MANIFEST,
    "./appendonlydir/appendonly.aof.1.base.rdb": RDB_BYTES,
    "./appendonlydir/appendonly.aof.1.incr.aof": AOF_INCR,
}


def incompressible(size: int) -> bytes:
    """A payload gzip cannot shrink, so an archive's size is a real number.

    R-A8O's unrestorable rescue archive was 14.7 MB. A test that a large archive
    still fails is undermined if its filler compresses away to nothing.
    """
    return b"\x7fELF" + random.Random(1729).randbytes(size)


def write_archive(
    path: Path,
    members: dict[str, bytes] | None = None,
    *,
    directories: tuple[str, ...] = (),
) -> Path:
    """Write a gzip tarball holding exactly these entries.

    `directories` exist so a test can build the entry `tar` records for a
    directory whether or not anything was written into it - the 89-byte
    artefact, and the empty `appendonlydir/` that used to satisfy a glob.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name in directories:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, payload in (members or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def write_empty_data_archive(path: Path) -> Path:
    """The 89-byte artefact production produces nightly: one directory, no graph."""
    return write_archive(path, {}, directories=("./",))


def write_rdb_archive(path: Path) -> Path:
    return write_archive(path, {"./dump.rdb": RDB_BYTES})


def write_aof_archive(path: Path) -> Path:
    return write_archive(path, dict(AOF_MEMBERS), directories=("./appendonlydir/",))


def write_nested_archive(path: Path) -> Path:
    """R-A8O's shape: the payload one directory too deep, plus image binaries."""
    return write_archive(
        path,
        {
            "FalkorDB/dump.rdb": RDB_BYTES,
            "FalkorDB/appendonlydir/appendonly.aof.manifest": AOF_MANIFEST,
            "bin/src/falkordb.so": incompressible(65536),
        },
    )


def working_bash() -> str | None:
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
