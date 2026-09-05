#!/usr/bin/env python3
"""Refuse a release-evidence directory that contains a live credential.

Why this exists
---------------
R-A8H wrote ``docker compose config`` output into the evidence directory. That
command *resolves* environment values, so the rendered JSON carried the AWS
access key and secret, the OpenAI key and every service password in plaintext,
inside the one directory the release programme requires to hold no credential.

The first remediation pass redacted by key *name* and missed them again: the
same values also appear inside composed connection strings and container command
arguments, where no key name is adjacent to them. So this scans for the
**values**, read from the env files that declare them, and reports counts only -
it never prints a secret.

What "occurrence" means here
---------------------------
A secret that reaches evidence rarely arrives verbatim. A Mongo URI
percent-encodes the password, ``docker inspect`` JSON escapes ``/`` as ``\\/``,
and a tar archive holds the bytes with no text encoding at all. A scan that only
looks for the literal string reports CLEAN on all three - which is the same
silent success this check exists to prevent. Every declared value is therefore
searched in each of its plausible encodings, over the raw bytes of each file, and
over the file names as well.

Usage
-----
    scripts/evidence_secret_scan.py <evidence-dir> <env-file> [<env-file> ...]
    scripts/evidence_secret_scan.py <evidence-dir> <env-file> --json

Exit codes
----------
    0  CLEAN - no secret value and no provider-shaped token in any file
    1  NOT CLEAN - at least one finding, listed by file and source variable
    2  the scan could not be trusted (bad usage, missing env file, nothing to
       hunt, or an unreadable subtree). A scan that measured nothing must never
       exit 0.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.parse import quote, quote_plus, urlsplit

#: Substrings that mark a variable as holding a credential. Matched anywhere in
#: the name and case-insensitively, not anchored to the end: ``RAZORPAY_KEY_ID``
#: and ``DB_PASSWD`` are credentials, and an end-anchored ``KEY$`` misses both.
SENSITIVE_STEMS = (
    "KEY",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PWD",
    "PASS",
    "TOKEN",
    "CREDENTIAL",
    "SALT",
    "AUTH",
    "SIGNATURE",
    "PRIVATE",
    "DSN",
    "URI",
    "URL",
)

#: Values common enough that hunting them would flood the report and bury a real
#: hit. Compared case-insensitively against the whole value, never a substring.
NOISE_VALUES = frozenset(
    {
        "true", "false", "yes", "no", "on", "off", "none", "null", "nil",
        "0", "1", "localhost", "127.0.0.1", "0.0.0.0", "change", "changeme",
        "development", "production", "staging", "test", "default", "enabled",
        "disabled", "http", "https", "redis", "mongodb", "admin",
    }
)

#: The floor exists to stop one-character values matching half the corpus, not
#: to excuse short credentials. A six-character database password is exactly the
#: thing the phase instruction names, so the floor sits below it.
MIN_SECRET_LENGTH = 4

#: Credential shapes worth catching even when no env file declares them - a key
#: pasted into a log line, or one belonging to an account this host cannot read.
SHAPE_PATTERNS = (
    ("provider OpenAI token", re.compile(rb"sk-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(rb"\b(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b")),
    ("AWS secret access key assignment", re.compile(rb"(?i)aws_secret_access_key[\"'\s:=]+[A-Za-z0-9/+=]{40}")),
    ("PEM private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(rb"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
)


@dataclass(frozen=True)
class Secret:
    """One declared credential: where it came from, and how it might appear."""

    source: str
    name: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.source}:{self.name}"


@dataclass(frozen=True)
class Dismissal:
    """A declared value ruled out as topology, reported rather than dropped."""

    label: str
    shown: str


@dataclass(frozen=True)
class Finding:
    """One occurrence of a credential inside the evidence directory."""

    path: str
    label: str
    encoding: str
    occurrences: int


def encodings_of(value: str) -> dict[str, bytes]:
    """Every form a secret plausibly takes by the time it reaches evidence.

    Keyed by encoding name so a finding can say *how* the value survived - which
    is the difference between "redact this file" and "fix the writer".
    """
    forms: dict[str, bytes] = {}

    def add(name: str, text: str) -> None:
        encoded = text.encode("utf-8", "surrogateescape")
        if encoded and encoded not in forms.values():
            forms[name] = encoded

    add("literal", value)
    add("percent-encoded", quote(value, safe=""))
    add("percent-encoded (+)", quote_plus(value))
    # json.dumps wraps in quotes; strip them so the escaped body is what matches.
    add("json-escaped", json.dumps(value)[1:-1])
    add("backslash-escaped /", value.replace("/", "\\/"))
    add("base64", base64.b64encode(value.encode()).decode())
    try:
        add("utf-16-le", value.encode("utf-16-le").decode("latin-1"))
    except UnicodeError:  # pragma: no cover - defensive
        pass
    return forms


def read_env(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (name, value) for every assignment in a dotenv-style file."""
    for raw in path.read_bytes().split(b"\n"):
        line = raw.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        if line.startswith(b"export "):
            line = line[len(b"export "):].lstrip()
        name, value = line.split(b"=", 1)
        value = value.strip()
        if value[:1] in (b'"', b"'") and value[-1:] == value[:1]:
            value = value[1:-1]
        yield name.decode(errors="ignore").strip(), value.decode(errors="ignore")


def is_topology(value: str) -> bool:
    """Is this URL a service address rather than a credential?

    Staging and production routinely share the identical docker-internal name,
    and a scan that cries wolf on ``http://qdrant:6333`` is how a real hit gets
    waved through. But the dismissal has to be narrow: userinfo, a query string
    or a non-trivial path can each carry the secret, so any of them keeps it.
    """
    if "://" not in value:
        return False
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    return parts.path.strip("/") == ""


def collect(env_paths: Sequence[Path]) -> tuple[list[Secret], list[Dismissal]]:
    """Split declared values into credentials to hunt and topology to dismiss."""
    secrets: list[Secret] = []
    dismissed: list[Dismissal] = []
    seen: set[str] = set()
    for path in env_paths:
        source = path.name
        for name, value in read_env(path):
            upper = name.upper()
            if not any(stem in upper for stem in SENSITIVE_STEMS):
                continue
            if len(value) < MIN_SECRET_LENGTH or value.lower() in NOISE_VALUES:
                continue
            if is_topology(value):
                parts = urlsplit(value)
                dismissed.append(Dismissal(f"{source}:{name}", f"{parts.scheme}://{parts.netloc}"))
                continue
            if value in seen:
                continue
            seen.add(value)
            secrets.append(Secret(source, name, value))
    return secrets, dismissed


def walk(evidence_dir: Path) -> tuple[list[Path], list[str]]:
    """Every file under the directory, plus any subtree that could not be read.

    ``os.walk`` swallows permission errors by default, so an unreadable subtree
    would silently contribute a CLEAN result. Collect those instead and let the
    caller refuse to certify.
    """
    files: list[Path] = []
    errors: list[str] = []

    def on_error(exc: OSError) -> None:
        errors.append(f"{exc.filename}: {exc}")

    for root, _dirs, names in os.walk(evidence_dir, onerror=on_error, followlinks=False):
        for name in sorted(names):
            files.append(Path(root) / name)
    return files, errors


def scan_blob(blob: bytes, rel: str, secrets: Iterable[Secret]) -> list[Finding]:
    findings: list[Finding] = []
    for secret in secrets:
        for encoding, needle in encodings_of(secret.value).items():
            count = blob.count(needle)
            if count:
                findings.append(Finding(rel, secret.label, encoding, count))
    for label, pattern in SHAPE_PATTERNS:
        matches = pattern.findall(blob)
        if matches:
            findings.append(Finding(rel, label, "shape match", len(matches)))
    return findings


def scan(evidence_dir: Path, secrets: Sequence[Secret]) -> tuple[list[Path], list[Finding], list[str]]:
    files, errors = walk(evidence_dir)
    findings: list[Finding] = []
    for path in files:
        rel = str(path.relative_to(evidence_dir)).replace("\\", "/")
        # The file name is evidence too: a probe object or archive can carry the
        # secret in its name and nowhere in its bytes.
        findings.extend(scan_blob(rel.encode("utf-8", "surrogateescape"), rel + " (file name)", secrets))
        try:
            # Raw bytes, never a decoded string: a tar.gz or any binary artefact
            # is unscannable once decoding has thrown its bytes away.
            blob = path.read_bytes()
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        findings.extend(scan_blob(blob, rel, secrets))
    return files, findings, errors


def report(evidence_dir, files, secrets, dismissed, findings, errors) -> None:
    print("evidence secret value scan")
    print(f"  evidence directory : {evidence_dir}")
    print(f"  files scanned      : {len(files)}")
    print(f"  values hunted      : {len(secrets)}")
    # Encoding count varies by value - a password containing "/" or "@" survives
    # in more forms than an alphanumeric one - so report the range actually used
    # rather than a single number measured off a probe string.
    counts = [len(encodings_of(s.value)) for s in secrets] or [0]
    print(f"  encodings per value: {min(counts)}-{max(counts)}")
    print(f"  shape patterns     : {len(SHAPE_PATTERNS)}")
    print(f"  dismissed as topology, not credentials ({len(set(dismissed))}):")
    for item in sorted(set(dismissed), key=lambda d: d.label):
        print(f"    {item.label:<36} {item.shown}")
    for error in errors:
        print(f"  UNREADABLE {error}")
    if findings:
        print(f"  RESULT: NOT CLEAN - {len(findings)} finding(s)")
        for finding in findings:
            print(
                f"    {finding.path:<44} {finding.label:<38}"
                f" as {finding.encoding:<20} occurrences={finding.occurrences}"
            )
        return
    print("  RESULT: CLEAN - no declared secret value and no provider-shaped token appears in")
    print("          any file under the evidence directory, in any checked encoding.")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence_secret_scan.py",
        description=(
            "Refuse a release-evidence directory that contains a live credential. "
            "Hunts declared values in every encoding they plausibly survive in, "
            "and reports counts only - never a value."
        ),
    )
    parser.add_argument("evidence_dir", help="the evidence directory to scan")
    parser.add_argument("env_file", nargs="+", help="dotenv file(s) declaring the values to hunt")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON on stdout")
    args = parser.parse_args(list(argv))

    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_dir():
        print(f"not a directory: {evidence_dir}", file=sys.stderr)
        return 2

    env_paths = [Path(p) for p in args.env_file]
    missing = [str(p) for p in env_paths if not p.is_file()]
    if missing:
        # A mistyped path used to print a note and still certify CLEAN with zero
        # values hunted. Refusing is the only safe reading of a scan that never
        # loaded the thing it was asked to look for.
        print("env file(s) not found: " + ", ".join(missing), file=sys.stderr)
        return 2

    secrets, dismissed = collect(env_paths)
    if not secrets:
        print("no credential-shaped values were declared by the given env files", file=sys.stderr)
        return 2

    files, findings, errors = scan(evidence_dir, secrets)

    if args.json:
        print(
            json.dumps(
                {
                    "evidence_dir": str(evidence_dir),
                    "files_scanned": len(files),
                    "values_hunted": len(secrets),
                    "dismissed": [{"label": d.label, "endpoint": d.shown} for d in sorted(set(dismissed), key=lambda d: d.label)],
                    "unreadable": errors,
                    "findings": [
                        {
                            "path": f.path,
                            "source": f.label,
                            "encoding": f.encoding,
                            "occurrences": f.occurrences,
                        }
                        for f in findings
                    ],
                    "result": "NOT CLEAN" if (findings or errors) else "CLEAN",
                },
                indent=2,
            )
        )
    else:
        report(evidence_dir, files, secrets, dismissed, findings, errors)

    if findings:
        return 1
    if errors:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
