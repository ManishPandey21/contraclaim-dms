"""What makes a backup archive a recovery artefact rather than a file.

Before this module there were three answers to that question and they
disagreed:

* `scripts/backup_volume.sh` matched shell globs against the tar entry list and
  passed if *any* pattern matched anything - including a directory entry that
  `tar` records whether or not it holds a byte;
* `scripts/backup_status.py`, through `operations_health`, looked at mtime and
  `st_size > 0`, which an 89-byte archive of an empty directory satisfies;
* the maintenance runbook read its own `--verify A B` invocation as "both must
  be present" and got "either will do".

The result was `falkordb-data: ok` for an archive containing nothing, and a
14.7 MB rescue archive that could not be restored because its entries sat under
a `FalkorDB/` prefix instead of at the root the volume is mounted at.

This module is the single answer. Every one of those callers now routes here.

Three ways to state a contract, and they compose:

    validate_archive(path)                                  # readable, non-empty
    validate_archive(path, require=("*a", "*b"))            # every pattern
    validate_archive(path, any_of=("*a", "*b"))             # at least one
    validate_archive(path, profile="redis-persistence")     # semantic

A pattern is satisfied only by a **non-empty regular file**. That single rule is
what closes the empty-`appendonlydir/` hole in the generic modes, and it is why
`require` and `any_of` are safe to expose rather than only the profile. Since
R-A8U the *bare* form applies the same rule with no pattern to match: an archive
holding no non-empty regular file restores nothing, whatever was expected of it
(F-A8T2-8).
"""

from __future__ import annotations

import fnmatch
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: The archive satisfies its declared contract.
VALID = "VALID"
#: The archive reads, its entries are in the right place, and the payload the
#: contract requires is not there.
INVALID_CONTENT = "INVALID_CONTENT"
#: The payload exists but under a prefix, so restoring it puts the files
#: somewhere the engine will not look.
INVALID_ROOT = "INVALID_ROOT"
#: No file at that path.
MISSING = "MISSING"
#: A file that is not a readable gzip tarball.
UNREADABLE = "UNREADABLE"
#: Present and fresh, and no content contract is declared for this label. The
#: archive may well be fine; nothing here has shown that it is. Never report
#: this as "ok".
UNVERIFIED = "UNVERIFIED"
#: Present and fresh, a content contract IS declared, and it was not applied -
#: the archive exceeded the inline validation ceiling, or the caller asked for
#: freshness only. This is a skipped step, not a passed one, and callers must
#: treat it as unhealthy: marking success on a skipped step is the exact shape
#: of the defect this module exists to close.
UNEVALUATED = "UNEVALUATED"
#: Present, content valid, but older than the freshness bound. Freshness is the
#: caller's concern; the constant lives here so the vocabulary has one home.
STALE = "STALE"
#: A zero-byte file. Freshness again, and here for the same reason.
EMPTY = "EMPTY"

#: First bytes of any RDB file, and of the `*.base.rdb` member of a multi-part
#: AOF. Redis writes "REDIS" followed by a four-digit version.
RDB_MAGIC = b"REDIS"

#: How many leading bytes each candidate payload is read for. An RDB file opens
#: `REDIS` followed by a four-digit ASCII version (`REDIS0011`), so five bytes
#: prove only that something wrote the word - a five-byte file containing
#: exactly `REDIS` matched the magic and certified VALID.
RDB_HEAD_BYTES = 9

#: The smallest byte count an RDB file can honestly have: the nine-byte header,
#: the one-byte `0xFF` end-of-file opcode, and the eight-byte CRC64 trailer.
#: Anything shorter cannot be restored, whatever it begins with.
RDB_MIN_BYTES = 18

#: The members an `appendonlydir/` must actually carry. Redis writes a manifest
#: plus a base file and zero or more incrementals; a directory holding only a
#: manifest, a README or any other passenger restores nothing. Accepting "any
#: non-empty file under appendonlydir/" certified exactly that.
AOF_PAYLOAD_SUFFIXES = (".base.rdb", ".base.aof", ".incr.aof")


def _is_restorable_rdb(head: bytes, size: int) -> bool:
    """Does this look like an RDB snapshot rather than the word `REDIS`?"""

    if size < RDB_MIN_BYTES:
        return False
    if head[: len(RDB_MAGIC)] != RDB_MAGIC:
        return False
    version = head[len(RDB_MAGIC) : RDB_HEAD_BYTES]
    return len(version) == 4 and version.isdigit()

_AOF_DIRECTORY = "appendonlydir"


class ArchiveValidationError(ValueError):
    """A contract that cannot be evaluated. Never downgraded to a warning."""


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    is_file: bool

    @property
    def depth(self) -> int:
        return len(self.name.split("/")) - 1

    @property
    def root_component(self) -> str:
        return self.name.split("/", 1)[0]


@dataclass(frozen=True)
class ArchiveVerdict:
    status: str
    contract: str
    detail: str
    path: str
    member_count: int = 0
    members: Sequence[str] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == VALID

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "contract": self.contract,
            "detail": self.detail,
            "path": self.path,
            "member_count": self.member_count,
        }


def _normalise(name: str) -> str:
    cleaned = name.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/")


def _read_members(path: Path) -> tuple[list[ArchiveMember], dict[str, bytes]]:
    """Entry list plus the leading bytes of every candidate RDB member.

    The header bytes are collected in the same single pass as the listing: a
    gzip stream has to be decompressed from the start either way, and reopening
    it per member turns one pass into several over a multi-gigabyte archive.
    """

    members: list[ArchiveMember] = []
    heads: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as tar:
        for info in tar:
            name = _normalise(info.name)
            if not name:
                continue
            members.append(ArchiveMember(name=name, size=info.size, is_file=info.isreg()))
            if info.isreg() and info.size > 0 and name.endswith(".rdb"):
                handle = tar.extractfile(info)
                if handle is not None:
                    heads[name] = handle.read(RDB_HEAD_BYTES)
    return members, heads


def _matches(members: Iterable[ArchiveMember], pattern: str) -> bool:
    """A glob is satisfied by a non-empty regular file and by nothing else.

    Matched against both the normalised name and its `./` form, because callers
    write patterns like `*dump.rdb` against entry lists that `tar` may have
    written either way.
    """
    for member in members:
        if not member.is_file or member.size <= 0:
            continue
        if fnmatch.fnmatch(member.name, pattern) or fnmatch.fnmatch(f"./{member.name}", pattern):
            return True
    return False


def _validate_redis_persistence(
    path: Path, members: Sequence[ArchiveMember], heads: dict[str, bytes]
) -> ArchiveVerdict:
    """Redis-family persistence (FalkorDB and Redis both), restore-root shaped.

    Valid when the archive carries, **at its root**, either a usable RDB
    snapshot or a usable multi-part AOF directory. Both is normal; either alone
    is a complete recovery artefact and is accepted as one.
    """

    def verdict(status: str, detail: str) -> ArchiveVerdict:
        return ArchiveVerdict(
            status=status,
            contract="redis-persistence",
            detail=detail,
            path=str(path),
            member_count=len(members),
            members=tuple(member.name for member in members[:50]),
        )

    root_rdb = next((m for m in members if m.name == "dump.rdb"), None)
    aof_payload = [
        m
        for m in members
        if m.is_file and m.size > 0 and m.name.startswith(f"{_AOF_DIRECTORY}/")
    ]

    rdb_problem = ""
    rdb_ok = False
    if root_rdb is not None:
        if not root_rdb.is_file or root_rdb.size <= 0:
            rdb_problem = "dump.rdb is present but empty"
        elif not _is_restorable_rdb(heads.get("dump.rdb", b""), root_rdb.size):
            rdb_problem = (
                f"dump.rdb is not a restorable RDB snapshot: it must open with the "
                f"{RDB_MAGIC.decode()} magic followed by a four-digit version and be at "
                f"least {RDB_MIN_BYTES} bytes (header, EOF opcode and CRC64), and it is "
                f"{root_rdb.size} byte(s)"
            )
        else:
            rdb_ok = True

    aof_problem = ""
    aof_ok = False
    if aof_payload:
        # "Any non-empty file under appendonlydir/" is not a payload. A manifest
        # alone, or a README a helper script dropped there, satisfied that and
        # certified an archive with nothing to replay.
        restorable = [
            m
            for m in aof_payload
            if m.name.endswith(AOF_PAYLOAD_SUFFIXES)
        ]
        bad_base = [
            m.name
            for m in restorable
            if m.name.endswith(".base.rdb") and not _is_restorable_rdb(heads.get(m.name, b""), m.size)
        ]
        if bad_base:
            aof_problem = (
                f"AOF base file(s) that are not restorable RDB snapshots: {', '.join(sorted(bad_base))}"
            )
        elif not restorable:
            aof_problem = (
                f"{_AOF_DIRECTORY}/ carries no replayable member: expected at least one "
                f"file ending {', '.join(AOF_PAYLOAD_SUFFIXES)}, found only "
                f"{', '.join(sorted(m.name for m in aof_payload))}"
            )
        else:
            aof_ok = True

    if rdb_ok or aof_ok:
        carried = []
        if rdb_ok:
            carried.append("dump.rdb")
        if aof_ok:
            carried.append(f"{_AOF_DIRECTORY}/ ({len(aof_payload)} file(s))")
        return verdict(VALID, f"restore-root persistence present: {', '.join(carried)}")

    # Nothing usable at the root. Say why, and distinguish "the data is in the
    # wrong place" from "there is no data", because the operator response
    # differs: one is a re-pack, the other is a lost graph.
    misplaced = sorted(
        {
            member.root_component
            for member in members
            if member.is_file
            and member.size > 0
            and member.depth > 0
            and (member.name.endswith("dump.rdb") or f"/{_AOF_DIRECTORY}/" in member.name)
        }
    )
    if misplaced:
        return verdict(
            INVALID_ROOT,
            (
                f"persistence files are nested under {', '.join(misplaced)}/ instead of the archive root. "
                f"Restoring this into a volume mounted at /data yields /data/{misplaced[0]}/dump.rdb, "
                f"where the engine does not look. Re-pack with `tar -czf ARCHIVE -C <persistence-dir> .`"
            ),
        )

    problems = [text for text in (rdb_problem, aof_problem) if text]
    detail = "; ".join(problems) if problems else (
        f"no dump.rdb and no non-empty {_AOF_DIRECTORY}/ member at the archive root"
    )
    return verdict(INVALID_CONTENT, detail)


#: Semantic contracts, by name. Adding one here is how a new volume declares
#: what a usable archive of it looks like.
PROFILES = {
    "redis-persistence": _validate_redis_persistence,
}

#: Which archive label carries which contract. `operations_health` reads this
#: so the nightly signal and the backup script cannot drift apart.
PROFILE_BY_LABEL = {
    "falkordb-data": "redis-persistence",
    "falkordb-persistence": "redis-persistence",
    "redis-data": "redis-persistence",
}

#: Labels whose contract is a pattern list rather than a semantic profile.
#: Qdrant persists either a collections tree or a raft state file, and either
#: proves the volume held state, so this one is a genuine disjunction - which is
#: why `any_of` is exposed as its own thing rather than being what a bare
#: pattern list happens to mean.
#:
#: A label in neither mapping has no content contract at all and is reported
#: UNVERIFIED, never VALID, so nothing claims more than it measured.
ANY_OF_BY_LABEL = {
    "qdrant-data": ("*/collections/*", "*raft_state*"),
}


def validate_archive(
    path: Any,
    *,
    profile: str | None = None,
    require: Sequence[str] = (),
    any_of: Sequence[str] = (),
) -> ArchiveVerdict:
    """Decide whether one archive satisfies its declared contract."""

    if profile is not None and profile not in PROFILES:
        raise ArchiveValidationError(
            f"unknown archive profile {profile!r}; known profiles: {', '.join(sorted(PROFILES))}"
        )

    target = Path(path)
    contract = profile or (
        "require:" + ",".join(require) if require else ("any-of:" + ",".join(any_of) if any_of else "readable")
    )

    if not target.is_file():
        return ArchiveVerdict(
            status=MISSING,
            contract=contract,
            detail=f"no archive at {target}",
            path=str(target),
        )

    try:
        members, heads = _read_members(target)
    except (tarfile.TarError, OSError, EOFError) as exc:
        return ArchiveVerdict(
            status=UNREADABLE,
            contract=contract,
            detail=f"{target} is not a readable gzip tarball: {exc}",
            path=str(target),
        )

    if profile is not None:
        return PROFILES[profile](target, members, heads)

    listed = tuple(member.name for member in members[:50])

    # F-A8T2-8. With no profile, no `--require` and no `--any-of`, the verdict
    # was "it is a readable gzip tarball" and a **zero-entry** archive - or one
    # holding nothing but the `./` directory `tar -czf` over an empty directory
    # writes - printed VALID and exited 0. `operations_health.py` always supplies
    # a contract, so this was a CLI hazard rather than an automated hole; it is
    # still the CLI an operator reaches for when checking a rescue archive by
    # hand before an outage, and "this archive restores nothing" must not read
    # as VALID under any invocation. The bare contract is now "readable, and it
    # holds at least one non-empty regular file": the weakest claim that is
    # still a claim about recoverability.
    #
    # Only when NOTHING was declared. With a `--require`/`--any-of` contract in
    # hand, that contract's own message names the entries it wanted, and CI
    # caught this generic one replacing it: `test_deployment_config.py` asserts
    # the `any-of` refusal says "none of the accepted entries are present".
    # A refusal that stops saying what was expected is a worse refusal.
    if not require and not any_of and not any(
        member.is_file and member.size > 0 for member in members
    ):
        return ArchiveVerdict(
            status=INVALID_CONTENT,
            contract=contract,
            detail=(
                "the archive is readable and holds no non-empty regular file, so "
                "it restores nothing. Declare a --profile, --require or --any-of "
                "contract if a specific payload is expected."
            ),
            path=str(target),
            member_count=len(members),
            members=listed,
        )

    missing = [pattern for pattern in require if not _matches(members, pattern)]
    if missing:
        return ArchiveVerdict(
            status=INVALID_CONTENT,
            contract=contract,
            detail=(
                f"required entr{'y' if len(missing) == 1 else 'ies'} absent or empty: {', '.join(missing)}. "
                f"A pattern is satisfied only by a non-empty regular file, so an empty directory whose "
                f"name matches does not count."
            ),
            path=str(target),
            member_count=len(members),
            members=listed,
        )

    if any_of and not any(_matches(members, pattern) for pattern in any_of):
        return ArchiveVerdict(
            status=INVALID_CONTENT,
            contract=contract,
            detail=(
                f"none of the accepted entries are present as a non-empty file: {', '.join(any_of)}. "
                f"The volume this archived holds no persisted state."
            ),
            path=str(target),
            member_count=len(members),
            members=listed,
        )

    return ArchiveVerdict(
        status=VALID,
        contract=contract,
        detail=f"{len(members)} entr{'y' if len(members) == 1 else 'ies'} satisfy the declared contract",
        path=str(target),
        member_count=len(members),
        members=listed,
    )


def contract_for_label(label: str) -> dict[str, Any]:
    """The declared contract for a backup label, or an empty one if there is none."""
    if label in PROFILE_BY_LABEL:
        return {"profile": PROFILE_BY_LABEL[label]}
    if label in ANY_OF_BY_LABEL:
        return {"any_of": ANY_OF_BY_LABEL[label]}
    return {}
