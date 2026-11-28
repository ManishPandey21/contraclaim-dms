"""Safe loading of skill bundles.

An uploaded bundle is untrusted until published. The loader therefore:

- never extracts to disk (members are read in memory),
- rejects path traversal, absolute paths, symlinks and non-text members,
- caps member count, per-file size and total size (zip-bomb guard),
- requires exactly one ``SKILL.md`` with ``name``/``description`` front matter.

The bundle hash is computed over normalised text so the same content hashes
identically regardless of zip ordering, timestamps or line endings.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Tuple

MAX_MEMBERS = 40
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
ALLOWED_SUFFIXES = {".md", ".txt"}
SKILL_FILE = "SKILL.md"
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class SkillBundleError(ValueError):
    """The bundle is unsafe or malformed. The message is safe to show the uploader."""


@dataclass(frozen=True)
class LoadedReference:
    path: str
    text: str
    content_hash: str


@dataclass(frozen=True)
class LoadedSkill:
    skill_key: str
    name: str
    description: str
    skill_md: str
    references: List[LoadedReference] = field(default_factory=list)
    content_hash: str = ""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def bundle_hash(skill_md: str, references: List[LoadedReference]) -> str:
    digest = hashlib.sha256()
    digest.update(b"SKILL.md\0")
    digest.update(normalize_text(skill_md).encode("utf-8"))
    for ref in sorted(references, key=lambda item: item.path):
        digest.update(b"\0" + ref.path.encode("utf-8") + b"\0")
        digest.update(normalize_text(ref.text).encode("utf-8"))
    return digest.hexdigest()


def parse_front_matter(skill_md: str) -> Tuple[Dict[str, str], str]:
    """Return (front matter fields, body). Only flat ``key: value`` pairs are read."""
    match = _FRONT_MATTER.match(skill_md)
    if not match:
        raise SkillBundleError("SKILL.md must start with a --- front matter block")
    fields: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields, skill_md[match.end():]


def _safe_member_path(name: str) -> PurePosixPath:
    if "\\" in name or "\0" in name:
        raise SkillBundleError(f"Unsafe path in bundle: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
        raise SkillBundleError(f"Unsafe path in bundle: {name!r}")
    if re.match(r"^[A-Za-z]:", name):
        raise SkillBundleError(f"Unsafe path in bundle: {name!r}")
    return path


def _decode(raw: bytes, name: str) -> str:
    if b"\0" in raw:
        raise SkillBundleError(f"{name} is not a text file")
    try:
        return normalize_text(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise SkillBundleError(f"{name} is not valid UTF-8 text") from exc


def _assemble(files: Dict[str, str]) -> LoadedSkill:
    """Build a LoadedSkill from {relative posix path: text}, root-stripped."""
    skill_paths = [path for path in files if PurePosixPath(path).name == SKILL_FILE]
    if len(skill_paths) != 1:
        raise SkillBundleError("A bundle must contain exactly one SKILL.md")
    root = PurePosixPath(skill_paths[0]).parent
    skill_md = files[skill_paths[0]]
    fields, _body = parse_front_matter(skill_md)
    key = fields.get("name", "")
    if not _KEY.match(key):
        raise SkillBundleError(
            "SKILL.md front matter needs a lowercase hyphenated 'name' (2-64 characters)"
        )
    description = fields.get("description", "")
    if not description:
        raise SkillBundleError("SKILL.md front matter needs a 'description'")

    references: List[LoadedReference] = []
    for path, text in sorted(files.items()):
        if path == skill_paths[0]:
            continue
        pure = PurePosixPath(path)
        try:
            relative = pure.relative_to(root) if str(root) != "." else pure
        except ValueError as exc:
            raise SkillBundleError(f"{path} lies outside the skill folder") from exc
        references.append(
            LoadedReference(path=str(relative), text=text, content_hash=sha256_text(text))
        )
    return LoadedSkill(
        skill_key=key,
        name=_title(skill_md, key),
        description=description,
        skill_md=skill_md,
        references=references,
        content_hash=bundle_hash(skill_md, references),
    )


def _title(skill_md: str, fallback: str) -> str:
    for line in skill_md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def load_skill_zip(data: bytes) -> LoadedSkill:
    """Parse an uploaded zip into a LoadedSkill, or raise SkillBundleError."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise SkillBundleError("Bundle is larger than the 2 MB limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillBundleError("File is not a valid zip archive") from exc

    files: Dict[str, str] = {}
    total = 0
    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > MAX_MEMBERS:
            raise SkillBundleError(f"Bundle has more than {MAX_MEMBERS} files")
        for info in members:
            path = _safe_member_path(info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise SkillBundleError(f"Symbolic links are not allowed: {info.filename}")
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                raise SkillBundleError(
                    f"Only .md and .txt files are allowed in a skill bundle: {info.filename}"
                )
            if info.file_size > MAX_FILE_BYTES:
                raise SkillBundleError(f"{info.filename} is larger than 256 KB")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise SkillBundleError("Bundle contents exceed 1 MB uncompressed")
            # Read with a hard cap: the declared size in a zip header can lie.
            with archive.open(info) as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise SkillBundleError(f"{info.filename} is larger than 256 KB")
            files[str(path)] = _decode(raw, info.filename)
    return _assemble(files)


def load_skill_directory(directory: Path) -> LoadedSkill:
    """Load a trusted, repo-bundled skill folder (used for system skill seeds)."""
    files: Dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        files[path.relative_to(directory).as_posix()] = _decode(path.read_bytes(), path.name)
    return _assemble(files)


__all__ = [
    "LoadedReference",
    "LoadedSkill",
    "SkillBundleError",
    "bundle_hash",
    "load_skill_directory",
    "load_skill_zip",
    "parse_front_matter",
    "sha256_text",
]
