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
arguments, where no key name is adjacent to them.

So this scans for the **values**, read from the env files that define them, and
reports counts only. It never prints a secret.

Usage
-----
    scripts/evidence_secret_scan.py <evidence-dir> <env-file> [<env-file> ...]

Exit codes
----------
    0  CLEAN - no secret value and no provider-shaped token in any file
    1  NOT CLEAN - at least one finding, listed by file and source variable
    2  usage error
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit

#: A variable whose value is treated as a credential unless dismissed below.
SENSITIVE_NAME = re.compile(
    r"(KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL|DSN|URI|URL)$"
    r"|^(AWS_ACCESS_KEY_ID|OPENAI_API_KEY)$"
)

#: Values too short or too common to be a credential; matching them would drown
#: a real finding in noise.
SKIP_VALUES = {"", "true", "false", "0", "1", "none", "null", "localhost"}

MIN_SECRET_LENGTH = 8

#: Credential shapes worth catching even when no env file declares them - a key
#: pasted into a log line, or one belonging to an account this host cannot read.
SHAPE_PATTERNS = [
    ("provider OpenAI token", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
]


def read_env(path):
    """Yield (name, value) for every assignment in a dotenv-style file."""
    with open(path, "rb") as handle:
        for line in handle.read().split(b"\n"):
            if b"=" not in line or line.lstrip().startswith(b"#"):
                continue
            name, value = line.split(b"=", 1)
            value = value.strip()
            if value[:1] in (b'"', b"'") and value[-1:] == value[:1]:
                value = value[1:-1]
            yield name.decode(errors="ignore").strip(), value.decode(errors="ignore")


def collect(env_paths):
    """Split declared values into credentials to hunt and topology to dismiss.

    A service URL carrying no userinfo and no query string is topology, not a
    credential, and staging and production routinely share the identical
    docker-internal name. Counting those as secrets makes the scan cry wolf,
    which is how a real hit gets waved through - so they are dismissed by name
    in the report rather than dropped in silence.
    """
    secrets, dismissed = [], []
    for path in env_paths:
        if not os.path.exists(path):
            print("  NOTE: env file not present, skipped: %s" % path)
            continue
        label = os.path.basename(path)
        for name, value in read_env(path):
            if not SENSITIVE_NAME.search(name):
                continue
            if len(value) < MIN_SECRET_LENGTH or value.lower() in SKIP_VALUES:
                continue
            if "://" in value:
                parts = urlsplit(value)
                if not (parts.username or parts.password or parts.query):
                    dismissed.append(
                        ("%s:%s" % (label, name),
                         "%s://%s:%s" % (parts.scheme, parts.hostname, parts.port))
                    )
                    continue
            secrets.append(("%s:%s" % (label, name), value))
    return secrets, dismissed


def scan(evidence_dir, secrets):
    findings = []
    files = []
    for root, _dirs, names in os.walk(evidence_dir):
        for name in sorted(names):
            files.append(os.path.join(root, name))
    for path in files:
        try:
            with open(path, "rb") as handle:
                blob = handle.read().decode("utf-8", "ignore")
        except OSError as exc:
            print("  UNREADABLE %s: %s" % (path, exc))
            continue
        rel = os.path.relpath(path, evidence_dir)
        for label, value in secrets:
            if value and value in blob:
                findings.append((rel, label, blob.count(value)))
        for label, pattern in SHAPE_PATTERNS:
            matches = pattern.findall(blob)
            if matches:
                findings.append((rel, label + " (shape match)", len(matches)))
    return files, findings


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip())
        return 2
    evidence_dir, env_paths = argv[1], argv[2:]
    if not os.path.isdir(evidence_dir):
        print("not a directory: %s" % evidence_dir)
        return 2

    print("evidence secret value scan")
    print("  evidence directory : %s" % evidence_dir)
    secrets, dismissed = collect(env_paths)
    files, findings = scan(evidence_dir, secrets)
    print("  files scanned      : %d" % len(files))
    print("  values hunted      : %d (from %d env file(s))" % (len(secrets), len(env_paths)))
    print("  shape patterns     : %d" % len(SHAPE_PATTERNS))
    print("  dismissed as topology, not credentials (%d):" % len(set(dismissed)))
    for label, shown in sorted(set(dismissed)):
        print("    %-36s %s" % (label, shown))

    if findings:
        print("  RESULT: NOT CLEAN - %d finding(s)" % len(findings))
        for rel, label, count in findings:
            print("    %-44s %-40s occurrences=%d" % (rel, label, count))
        return 1

    print("  RESULT: CLEAN - no declared secret value and no provider-shaped token appears in")
    print("          any file under the evidence directory.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
