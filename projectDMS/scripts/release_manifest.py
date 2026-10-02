#!/usr/bin/env python3
"""Release manifests: which exact image every app service must run.

A deployment declares its scope - never a free-form flag - and carries a
release manifest: for every app service, the image id (``docker image inspect
.Id``, the local image config id a retag keeps; not a registry RepoDigest) and
the commit it was built from.

    FULL          deploys backend, contract-worker, document-worker and client
    BACKEND_ONLY  deploys the backend-image services
    CLIENT_ONLY   deploys client
    UNCHANGED     deploys nothing (maintenance): every service must still run
                  exactly its approved image

Commands:

    certify  record the images certified for a commit; each needs a Trivy JSON
             report of that exact image id with no fixable HIGH/CRITICAL
             finding (the CI image-scan policy).
    target   write the manifest of a deploy: deployed services from certified
             images only; every other service copied from the approved manifest.
    verify   compare the running containers with the manifest, the deployed
             images with the certification it names (post_deploy_verify.sh
             calls it). ``--receipt`` writes a pending receipt for the exact
             bytes it evaluated, only when every finding passed.
    confirm  turn that pending receipt into the receipt - post_deploy_verify.sh
             does it at the end of a run with no failure at all.
    promote  make a verified manifest the approved state (``current.json``).
    preflight  before any restart: refuse a FULL or BACKEND_ONLY deploy while a
             document-worker canary runs that the manifest does not declare
             (release_preflight.sh calls it). Read-only.

Policy (owner decision 2026-10-02 - strict, service-scoped):

* a service the scope deploys runs exactly the certified image the manifest
  records, built from exactly the deployed commit;
* a service the scope does not deploy runs exactly the image the approved
  manifest recorded, and its build context has not changed between the commit
  that image was built from and the deployed commit;
* the approved manifest is one that verified green (a receipt) - a hand-edited
  ``current.json`` is not approval;
* a running service - or any container of the project on an app image - that
  the manifest does not name is drift;
* anything that cannot be established fails: no scope, no manifest, no
  certification, an image without its identity (``unknown``), a modified or
  untracked build input, a checkout root without the build contexts.

What a certification record proves is that someone recorded the image ids as
certified, with evidence; it is not a signature. Keep the manifests directory
writable only by the release operators.

Standard library only: it runs on the deployment host's python3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

SCHEMA = "contraclaim.release-manifest.v1"
CERT_SCHEMA = "contraclaim.certified-images.v1"
RECEIPT_SUFFIX = ".verified"

#: Every app service and the build context its image comes from.
CONTEXTS: Dict[str, str] = {
    "backend": "backend",
    "contract-worker": "backend",
    "document-worker": "backend",
    "document-worker-canary": "backend",
    "client": "client",
}
#: Services that must be running after any deploy. The canary runs 0 replicas
#: outside an authorised canary; with no entry of its own it is held to the
#: document-worker image it is a canary of.
REQUIRED = ("backend", "contract-worker", "document-worker", "client")
CANARY_OF = {"document-worker-canary": "document-worker"}

BACKEND_IMAGE = (
    "backend",
    "contract-worker",
    "document-worker",
    "document-worker-canary",
)
SCOPES: Dict[str, frozenset] = {
    "FULL": frozenset(CONTEXTS),
    "CLIENT_ONLY": frozenset({"client"}),
    "BACKEND_ONLY": frozenset(BACKEND_IMAGE),
    "UNCHANGED": frozenset(),
}
_SHA = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


# --------------------------------------------------------------------------- #
# the checkout
# --------------------------------------------------------------------------- #


def _dockerignore_patterns(root: Path, context: str) -> Optional[List[str]]:
    """The context's .dockerignore patterns; ``None`` when they use a negation
    this check does not model (so nothing is treated as excluded)."""
    path = root / context / ".dockerignore"
    if not path.is_file():
        return []
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            return None
        patterns.append(line.strip("/"))
    return patterns


def _pattern_regex(pattern: str) -> "re.Pattern[str]":
    """Docker's .dockerignore semantics: ``*`` and ``?`` never cross ``/`` (so
    ``*.pyc`` is root-level only), ``**`` matches any number of directories."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _excluded(relative: str, patterns: Sequence[str]) -> bool:
    """A path is excluded when it, or any parent directory of it, matches."""
    parts = relative.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    for pattern in patterns:
        regex = _pattern_regex(pattern)
        if any(regex.match(candidate) for candidate in prefixes):
            return True
    return False


@dataclass
class Checkout:
    """Git facts about the deployed checkout (the directory holding ./backend)."""

    root: Path

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True
        )

    def head(self) -> str:
        result = self._git("rev-parse", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""

    def has_tree(self, commit: str, path: str) -> bool:
        result = self._git("ls-tree", commit, "--", path)
        return result.returncode == 0 and bool(result.stdout.strip())

    def is_commit(self, commit: str) -> bool:
        return (
            bool(_SHA.match(commit or ""))
            and self._git("cat-file", "-e", f"{commit}^{{commit}}").returncode == 0
        )

    def stray_inputs(self, context: str) -> List[str]:
        """Build inputs under ``context`` that HEAD does not hold: modified or
        deleted tracked files, and untracked or git-ignored files the context's
        .dockerignore does not exclude (``COPY . .`` would bake them in)."""
        changed = self._git("diff", "--name-only", "--relative", "HEAD", "--", context)
        others = self._git(
            "ls-files", "--others", "--", context
        )  # untracked AND ignored, per file
        if changed.returncode != 0 or others.returncode != 0:
            return [f"<git failed: {(changed.stderr or others.stderr).strip()}>"]
        stray = [line for line in changed.stdout.splitlines() if line]
        patterns = _dockerignore_patterns(self.root, context)
        for path in others.stdout.splitlines():
            if not path:
                continue
            relative = (
                path[len(context) + 1 :] if path.startswith(context + "/") else path
            )
            if patterns is not None and _excluded(relative, patterns):
                continue
            stray.append(path)
        return stray

    def unchanged(self, old: str, new: str, path: str) -> bool:
        return self._git("diff", "--quiet", old, new, "--", path).returncode == 0


def _checkout_problems(checkout: Checkout, head: str) -> List[str]:
    problems = []
    for context in sorted(set(CONTEXTS.values())):
        if not checkout.has_tree(head, context):
            problems.append(
                f"no ./{context} at {head[:12]} under {checkout.root}: wrong checkout root, build contexts cannot be compared"
            )
            continue
        stray = checkout.stray_inputs(context)
        if stray:
            problems.append(
                f"./{context} holds build inputs the commit does not ({', '.join(stray[:5])}"
                f"{', ...' if len(stray) > 5 else ''}): no image can be shown to be the deployed commit"
            )
    return problems


# --------------------------------------------------------------------------- #
# receipts: approval is a manifest that verified green
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


PENDING_SUFFIX = RECEIPT_SUFFIX + ".pending"


def _record_receipt(path: str, digest: str, *, pending: bool) -> None:
    _write(
        path + (PENDING_SUFFIX if pending else RECEIPT_SUFFIX),
        {
            "manifest_sha256": digest,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def confirm_receipt(path: str) -> None:
    """The pending receipt ``verify --receipt`` wrote becomes the receipt - only
    for the same bytes it evaluated."""
    pending = Path(path + PENDING_SUFFIX)
    if not pending.is_file():
        raise SystemExit(f"{path} has no pending receipt: verify it first")
    try:
        recorded = json.loads(pending.read_text(encoding="utf-8")).get(
            "manifest_sha256"
        )
    except ValueError:
        raise SystemExit(f"{pending} is not a receipt")
    if recorded != _sha256(Path(path)):
        raise SystemExit(f"{path} changed after it was verified; verify it again")
    pending.replace(path + RECEIPT_SUFFIX)


def receipt_problem(path: Optional[str]) -> Optional[str]:
    """``None`` when ``path`` carries a receipt for its exact bytes."""
    if not path:
        return "no manifest"
    manifest = Path(path)
    receipt = Path(path + RECEIPT_SUFFIX)
    if not manifest.is_file():
        return f"{path} does not exist"
    if not receipt.is_file():
        return f"{path} has no verification receipt ({receipt.name}): it never verified green"
    try:
        recorded = json.loads(receipt.read_text(encoding="utf-8")).get(
            "manifest_sha256"
        )
    except ValueError:
        return f"{receipt} is not a receipt"
    if recorded != _sha256(manifest):
        return (
            f"{path} changed after it verified green (receipt does not match its bytes)"
        )
    return None


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

#: service -> one entry per running container: {"id", "image_id", "revision"};
#: ``_others``: every other container of the compose project ({"service", ...}).
Running = Mapping[str, Sequence[Mapping[str, str]]]


@dataclass(frozen=True)
class Finding:
    ok: bool
    message: str


def _entry_problems(name: str, entry: object) -> List[str]:
    if not isinstance(entry, dict):
        return [f"{name}: no manifest entry"]
    problems = []
    if not _IMAGE_ID.match(str(entry.get("image_id") or "")):
        problems.append(
            f"{name}: manifest image_id {entry.get('image_id')!r} is not an image id"
        )
    if not _SHA.match(str(entry.get("revision") or "")):
        problems.append(
            f"{name}: manifest revision {entry.get('revision')!r} is not a commit (built without RELEASE_SHA?)"
        )
    return problems


def evaluate(
    scope: Optional[str],
    target: Optional[Mapping],
    approved: Optional[Mapping],
    running: Running,
    checkout: Checkout,
    *,
    approved_receipt: Optional[str] = None,
    target_receipt: Optional[str] = None,
    certified: Optional[Mapping] = None,
    certified_problem: Optional[str] = None,
) -> List[Finding]:
    """``approved_receipt`` / ``target_receipt``: ``receipt_problem`` of the
    files they were read from (``None`` = a valid receipt). ``certified``: the
    certification record the manifest names, ``certified_problem`` why it could
    not be read as that record (``None`` = it was)."""
    out: List[Finding] = []

    def fail(message: str) -> None:
        out.append(Finding(False, message))

    def ok(message: str) -> None:
        out.append(Finding(True, message))

    if scope not in SCOPES:
        fail(
            f"deployment scope {scope!r} is not one of {', '.join(SCOPES)} (DEPLOY_SCOPE)"
        )
        return out
    if not isinstance(target, Mapping) or target.get("schema") != SCHEMA:
        fail(
            "no release manifest for this deploy (RELEASE_MANIFEST): nothing says which images must run"
        )
        return out
    release = str(target.get("release_sha") or "")
    if not _SHA.match(release):
        fail(f"release manifest names no release commit (release_sha={release!r})")
        return out
    if scope == "UNCHANGED":
        # Nothing deployed: the manifest IS the approved state, and must be one.
        if target_receipt is not None:
            fail(
                f"scope UNCHANGED needs the approved manifest as RELEASE_MANIFEST: {target_receipt}"
            )
            return out
        approved = target
    elif target.get("scope") != scope:
        fail(
            f"release manifest was written for scope {target.get('scope')!r}, the deploy declares {scope}"
        )
        return out

    head = checkout.head()
    if not head:
        fail(f"cannot read the deployed commit under {checkout.root}")
        return out
    if head != release:
        fail(
            f"deployed checkout is {head[:12]}, the release manifest is for {release[:12]}"
        )
    for problem in _checkout_problems(checkout, head):
        fail(problem)

    deployed = SCOPES[scope]
    services: Dict[str, object] = dict(target.get("services") or {})
    certified_images: Dict[str, object] = {}
    certification_loaded = False
    if deployed:
        if certified_problem is not None:
            fail(f"the deployed images cannot be shown certified: {certified_problem}")
        elif not (
            isinstance(certified, Mapping) and certified.get("schema") == CERT_SCHEMA
        ):
            fail(
                "the release manifest names no certification record for its deployed images"
            )
        elif certified.get("release_sha") != release:
            fail(
                f"the certification is for {str(certified.get('release_sha'))[:12]}, the release is {release[:12]}"
            )
        elif not (isinstance(certified.get("images"), dict) and certified["images"]):
            fail("the certification record certifies no images")
        else:
            certified_images = dict(certified["images"])
            certification_loaded = True
    if scope == "FULL" and isinstance(approved, Mapping) and approved_receipt is None:
        pass  # only feeds the drift set below
    elif scope not in ("FULL", "UNCHANGED"):
        if not (isinstance(approved, Mapping) and approved.get("schema") == SCHEMA):
            fail(
                f"scope {scope} needs the approved manifest of the release it replaces (APPROVED_MANIFEST)"
            )
            approved = None
        elif approved_receipt is not None:
            fail(f"the approved manifest is not approval: {approved_receipt}")
            approved = None
    previous: Dict[str, object] = dict((approved or {}).get("services") or {})

    if not running.get("_project"):
        fail(
            "cannot read the compose project from the configuration: one-off and "
            "orphan containers were not scanned"
        )
    for row in running.get("_others") or []:
        # Both app Dockerfiles always write the revision label (``unknown``
        # without RELEASE_SHA); an orphan carrying one runs an app build.
        if row.get("known") is False and str(row.get("revision") or "").strip():
            fail(
                f"{row.get('service')!s} {str(row.get('id'))[:12]}: an orphan of the compose project "
                f"(no such service) running an app image (revision {str(row.get('revision'))[:12]}) (drift)"
            )
        if row.get("oneoff"):
            fail(
                f"{row.get('service')!s} {str(row.get('id'))[:12]}: a one-off `compose run` container of an app "
                "service is running - nothing approves it (drift)"
            )
    app_images = {
        str(e.get("image_id"))
        for e in list(services.values()) + list(previous.values())
        if isinstance(e, dict)
    }
    for row in running.get("_others") or []:
        if row.get("image_id") in app_images:
            fail(
                f"{row.get('service')!s} {str(row.get('id'))[:12]}: runs an app image under a service the manifest does not name (drift)"
            )

    names = (
        set(services)
        | {s for s, rows in running.items() if rows and not s.startswith("_")}
        | set(REQUIRED)
    )
    for name in sorted(names):
        if name not in CONTEXTS:
            fail(f"{name}: not an app service this manifest knows")
            continue
        entry = services.get(name)
        held_as = name
        containers = list(running.get(name) or [])
        if entry is None and name in CANARY_OF and containers:
            held_as = CANARY_OF[name]
            entry = services.get(held_as)
        if entry is None:
            if containers:
                fail(
                    f"{name}: running, but the release manifest does not name it (drift)"
                )
            elif name in REQUIRED:
                fail(f"{name}: missing from the release manifest")
            continue
        problems = _entry_problems(name, entry)
        for problem in problems:
            fail(problem)
        if problems:
            continue
        assert isinstance(entry, dict)
        if held_as in deployed:
            cert = certified_images.get(held_as)
            # Without a loaded record the failure is already reported above;
            # with one, every deployed image must be in it.
            if certification_loaded and (
                not isinstance(cert, dict) or cert.get("image_id") != entry["image_id"]
            ):
                fail(
                    f"{name}: image {entry['image_id'][:19]} is not the one certified for {release[:12]}"
                )
            if entry["revision"] != release:
                fail(
                    f"{name}: deployed by this {scope} release but its image was built from {entry['revision'][:12]}, not {release[:12]}"
                )
        else:
            before = previous.get(held_as)
            if not isinstance(before, dict):
                fail(f"{name}: not deployed by {scope}, and no approved entry holds it")
            elif (before.get("image_id"), before.get("revision")) != (
                entry["image_id"],
                entry["revision"],
            ):
                fail(
                    f"{name}: not deployed by {scope}, but the release manifest changes its image from the approved one"
                )
            if not checkout.is_commit(entry["revision"]):
                fail(
                    f"{name}: its image's commit {entry['revision'][:12]} is not in this checkout"
                )
            elif not checkout.unchanged(entry["revision"], head, CONTEXTS[name]):
                fail(
                    f"{name}: ./{CONTEXTS[name]} changed between {entry['revision'][:12]} and {head[:12]} but "
                    f"{scope} does not deploy it - declare a scope that does"
                )
        if not containers:
            if name in REQUIRED:
                fail(f"{name}: no running container")
            continue
        for row in containers:
            cid = str(row.get("id") or "?")[:12]
            if row.get("image_id") != entry["image_id"]:
                fail(
                    f"{name} {cid}: runs image {str(row.get('image_id'))[:19]}, the manifest requires {entry['image_id'][:19]}"
                )
            elif row.get("revision") != entry["revision"]:
                fail(
                    f"{name} {cid}: image label revision {row.get('revision')!r} != manifest {entry['revision'][:12]}"
                )
            else:
                verb = "deployed" if held_as in deployed else "unchanged"
                ok(
                    f"{name} {cid}: {verb}, image {entry['image_id'][7:19]} built from {entry['revision'][:12]}"
                )
    return out


# --------------------------------------------------------------------------- #
# writing manifests
# --------------------------------------------------------------------------- #


#: The CI image-scan policy (ci.yml): HIGH and CRITICAL findings that have a fix.
BLOCKING_SEVERITIES = ("HIGH", "CRITICAL")


def preflight(
    scope: Optional[str], target: Optional[Mapping], running: Mapping
) -> List[Finding]:
    """What must hold before a deploy changes anything.

    A canary with no manifest entry of its own is held to the document-worker
    image (``CANARY_OF``). FULL and BACKEND_ONLY replace that image, and no
    deploy step rebuilds or recreates the canary, so a canary running through
    such a deploy can only fail verification - after production has changed.
    It has to be stopped first, or declared: built, scanned, certified and
    named in the manifest (``target --image document-worker-canary=...``),
    which the verifier then holds it to. CLIENT_ONLY leaves the worker image as
    approved, so the verifier's existing rule stands there. Nothing here
    relaxes ``evaluate``.
    """
    out: List[Finding] = []
    if scope not in SCOPES:
        out.append(
            Finding(False, f"deployment scope {scope!r} is not one of {', '.join(SCOPES)} (DEPLOY_SCOPE)")
        )
        return out
    if not isinstance(target, Mapping) or target.get("schema") != SCHEMA:
        out.append(
            Finding(False, "no release manifest for this deploy (RELEASE_MANIFEST): write it before any restart")
        )
        return out
    if scope != "UNCHANGED" and target.get("scope") != scope:
        out.append(
            Finding(
                False,
                f"release manifest was written for scope {target.get('scope')!r}, the deploy declares {scope}",
            )
        )
        return out
    services = target.get("services") or {}
    for canary, worker in sorted(CANARY_OF.items()):
        containers = [row for row in (running.get(canary) or []) if row]
        if not containers:
            out.append(Finding(True, f"{canary}: not running"))
        elif worker not in SCOPES[scope]:
            out.append(
                Finding(
                    True,
                    f"{canary}: running; scope {scope} leaves the {worker} image it is held to as approved",
                )
            )
        elif canary in services:
            out.append(Finding(True, f"{canary}: running, and declared by the release manifest"))
        else:
            ids = ", ".join(str(row.get("id"))[:12] for row in containers)
            out.append(
                Finding(
                    False,
                    f"{canary}: {len(containers)} container(s) running ({ids}) that this {scope} "
                    f"release manifest does not declare. The deploy replaces the {worker} image "
                    f"it is held to and never recreates it, so verification would fail after the "
                    f"restart. Before deploying, stop it (scale {canary} to 0), or declare it: "
                    f"build, scan and certify its image and pass "
                    f"--image {canary}=<image> to `release_manifest.py target`.",
                )
            )
    return out


def scan_problems(report: object, image_id: str) -> List[str]:
    """A Trivy JSON report (``trivy image --format json``) certifies an image
    only if it is a scan of THAT image id and holds no fixable HIGH/CRITICAL
    finding. A scan of another build of the same commit (CI builds and discards
    its own) says nothing about this binary."""
    if not isinstance(report, dict):
        return ["not a Trivy JSON report"]
    scanned = (report.get("Metadata") or {}).get("ImageID")
    if scanned != image_id:
        return [f"it scanned image {str(scanned)[:19]}, not {image_id[:19]}"]
    results = [r for r in report.get("Results") or [] if isinstance(r, dict)]
    if not any(r.get("Class") in ("os-pkgs", "lang-pkgs") for r in results):
        # A vulnerability scan of an image always reports its packages; a
        # report without them was not one (e.g. --scanners secret only).
        return ["it holds no package scan (os-pkgs / lang-pkgs results)"]
    blocking = []
    for result in results:
        for secret in result.get("Secrets") or []:
            # trivy-action scans for secrets too, and CI fails on them
            if secret.get("Severity") in BLOCKING_SEVERITIES:
                blocking.append(
                    f"secret {secret.get('RuleID')} in {result.get('Target')}"
                )
        for vulnerability in result.get("Vulnerabilities") or []:
            if vulnerability.get(
                "Severity"
            ) in BLOCKING_SEVERITIES and vulnerability.get("FixedVersion"):
                blocking.append(str(vulnerability.get("VulnerabilityID")))
    if blocking:
        return [
            f"blocking HIGH/CRITICAL findings: {', '.join(sorted(set(blocking))[:5])}"
        ]
    return []


def build_certification(
    release: str,
    images: Mapping[str, Mapping[str, str]],
    scans: Mapping[str, Mapping[str, object]],
) -> Dict:
    """``scans``: service -> {"report": parsed Trivy JSON, "path", "sha256"}."""
    if not _SHA.match(release):
        raise SystemExit(f"release {release!r} is not a full commit id")
    if not images:
        raise SystemExit("certify at least one --image")
    recorded = {}
    for name, entry in images.items():
        problems = _entry_problems(name, entry)
        if problems:
            raise SystemExit("; ".join(problems))
        if entry["revision"] != release:
            raise SystemExit(
                f"{name}: image built from {entry['revision']!r}, not {release}"
            )
        scan = scans.get(name)
        if not scan:
            raise SystemExit(
                f"{name}: pass --scan {name}=<trivy JSON report of that image>"
            )
        problems = scan_problems(scan.get("report"), entry["image_id"])
        if problems:
            raise SystemExit(
                f"{name}: the scan does not certify it: " + "; ".join(problems)
            )
        recorded[name] = {
            "image_id": entry["image_id"],
            "revision": entry["revision"],
            "scan": {"path": str(scan.get("path")), "sha256": str(scan.get("sha256"))},
        }
    extra = set(scans) - set(images)
    if extra:
        raise SystemExit(f"--scan for images not certified here: {sorted(extra)}")
    return {
        "schema": CERT_SCHEMA,
        "release_sha": release,
        "images": recorded,
        "certified_at": datetime.now(timezone.utc).isoformat(),
    }


def build_target(
    scope: str,
    release: str,
    images: Mapping[str, Mapping[str, str]],
    approved: Optional[Mapping],
    certified: Optional[Mapping] = None,
    certified_ref: Optional[Mapping[str, str]] = None,
) -> Dict:
    """``images``: deployed service -> {"image_id", "revision"} of the image built
    or retagged for it. It must be one ``certified`` records for this release.
    Non-deployed services are copied from ``approved``."""
    if scope not in SCOPES or scope == "UNCHANGED":
        raise SystemExit(
            f"scope must be one of {', '.join(s for s in SCOPES if s != 'UNCHANGED')}"
        )
    if not _SHA.match(release):
        raise SystemExit(f"release {release!r} is not a full commit id")
    if not (isinstance(certified, Mapping) and certified.get("schema") == CERT_SCHEMA):
        raise SystemExit(
            "--certified is required: the certification record of this release's images"
        )
    if certified.get("release_sha") != release:
        raise SystemExit(
            f"the certification is for {certified.get('release_sha')!r}, not the release {release}"
        )
    deployed = SCOPES[scope]
    services: Dict[str, Dict[str, str]] = {}
    for name in sorted(deployed):
        if name not in images:
            if name in REQUIRED:
                raise SystemExit(
                    f"scope {scope} deploys {name}: pass --image {name}=<image>"
                )
            continue
        entry = {
            "image_id": images[name]["image_id"],
            "revision": images[name]["revision"],
        }
        problems = _entry_problems(name, entry)
        if problems:
            raise SystemExit("; ".join(problems))
        if entry["revision"] != release:
            raise SystemExit(
                f"{name}: image built from {entry['revision']!r}, not the release {release}"
            )
        if (certified.get("images") or {}).get(name, {}).get("image_id") != entry[
            "image_id"
        ]:
            raise SystemExit(
                f"{name}: image {entry['image_id'][:19]} is not the one certified for {release[:12]}"
            )
        services[name] = entry
    extra = set(images) - deployed
    if extra:
        raise SystemExit(f"scope {scope} does not deploy {sorted(extra)}")
    if scope != "FULL":
        if not (isinstance(approved, Mapping) and approved.get("schema") == SCHEMA):
            raise SystemExit(
                f"scope {scope} needs --approved: the manifest of the release it replaces"
            )
        for name, entry in (approved.get("services") or {}).items():
            if name not in deployed:
                services[name] = dict(entry)
    manifest: Dict = {
        "schema": SCHEMA,
        "release_sha": release,
        "scope": scope,
        "services": services,
    }
    if certified_ref:
        # verify reloads this exact record: a manifest is only as certified as
        # the record it names.
        manifest["certification"] = dict(certified_ref)
    return manifest


def _inspect_image(ref: str) -> Dict[str, str]:
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "-f",
            '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}',
            ref,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"cannot inspect image {ref}: {result.stderr.strip()}")
    image_id, _, revision = result.stdout.strip().partition(" ")
    return {"image_id": image_id, "revision": revision}


def _load(path: Optional[str]) -> Optional[Dict]:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read manifest {path}: {exc}")


def _images(items: Sequence[str]) -> Dict[str, Dict[str, str]]:
    images = {}
    for item in items:
        name, sep, ref = item.partition("=")
        if not sep or name not in CONTEXTS:
            raise SystemExit(
                f"--image {item!r}: expected SERVICE=IMAGE with SERVICE in {sorted(CONTEXTS)}"
            )
        images[name] = _inspect_image(ref)
    return images


def _clean_head(path: str) -> str:
    checkout = Checkout(Path(path))
    head = checkout.head()
    if not head:
        raise SystemExit(f"cannot read the commit under {path}")
    problems = _checkout_problems(checkout, head)
    if problems:
        raise SystemExit("; ".join(problems))
    return head


def _write(path: str, document: Mapping) -> None:
    Path(path).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("certify", help="record the certified images of a release")
    c.add_argument("--checkout", default=".")
    c.add_argument("--image", action="append", default=[], metavar="SERVICE=IMAGE")
    c.add_argument(
        "--scan",
        action="append",
        default=[],
        metavar="SERVICE=REPORT",
        help="Trivy JSON report of that exact image (trivy image --format json)",
    )
    c.add_argument("--out", required=True)
    t = sub.add_parser("target", help="write the release manifest of a deploy")
    t.add_argument(
        "--scope", required=True, choices=sorted(s for s in SCOPES if s != "UNCHANGED")
    )
    t.add_argument(
        "--checkout", default=".", help="the deployed checkout (holds ./backend)"
    )
    t.add_argument("--image", action="append", default=[], metavar="SERVICE=IMAGE")
    t.add_argument(
        "--certified",
        required=True,
        help="certification record of this release (certify)",
    )
    t.add_argument(
        "--approved",
        help="approved manifest of the release this one replaces (required unless FULL)",
    )
    t.add_argument("--out", required=True)
    v = sub.add_parser(
        "verify", help="compare running containers with the release manifest"
    )
    v.add_argument("--scope", default="")
    v.add_argument("--target", default="")
    v.add_argument("--approved", default="")
    v.add_argument("--checkout", default=".")
    v.add_argument(
        "--running", required=True, help="JSON: service -> [{id, image_id, revision}]"
    )
    v.add_argument(
        "--receipt",
        action="store_true",
        help="on an all-pass result, write a pending receipt for the evaluated bytes",
    )
    r = sub.add_parser("confirm", help="confirm the pending receipt of a green run")
    r.add_argument("--manifest", required=True)
    p = sub.add_parser("promote", help="make a verified manifest the approved state")
    p.add_argument("--manifest", required=True)
    p.add_argument("--current", required=True)
    f = sub.add_parser(
        "preflight", help="before any restart: what must hold for this deploy (read-only)"
    )
    f.add_argument("--scope", default="")
    f.add_argument("--target", default="")
    f.add_argument(
        "--running", required=True, help="JSON: service -> [{id, image_id, revision}]"
    )
    args = parser.parse_args(argv)

    if args.command == "preflight":
        try:
            running_rows = json.loads(Path(args.running).read_text(encoding="utf-8"))
            target_doc = _load(args.target) if args.target else None
        except (SystemExit, OSError, ValueError) as exc:
            print(f"FAIL {exc}")
            return 1
        results = preflight(args.scope or None, target_doc, running_rows)
        for finding in results:
            print(("PASS " if finding.ok else "FAIL ") + finding.message)
        return 0 if results and all(f.ok for f in results) else 1

    if args.command == "certify":
        head = _clean_head(args.checkout)
        scans: Dict[str, Dict[str, object]] = {}
        for item in args.scan:
            name, sep, path = item.partition("=")
            if not sep or name not in CONTEXTS:
                raise SystemExit(f"--scan {item!r}: expected SERVICE=REPORT")
            report_path = Path(path)
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SystemExit(f"--scan {item!r}: {exc}")
            scans[name] = {
                "report": report,
                "path": str(report_path.resolve()),
                "sha256": _sha256(report_path),
            }
        record = build_certification(head, _images(args.image), scans)
        _write(args.out, record)
        print(
            f"wrote {args.out}: {len(record['images'])} certified image(s) of {head[:12]}"
        )
        return 0

    if args.command == "target":
        head = _clean_head(args.checkout)
        if args.approved:
            problem = receipt_problem(args.approved)
            if problem:
                raise SystemExit(f"--approved is not approval: {problem}")
            if Path(args.approved).resolve() == Path(args.out).resolve():
                raise SystemExit("--out must not overwrite the approved manifest")
        manifest = build_target(
            args.scope,
            head,
            _images(args.image),
            _load(args.approved),
            _load(args.certified),
            {
                "path": str(Path(args.certified).resolve()),
                "sha256": _sha256(Path(args.certified)),
            },
        )
        _write(args.out, manifest)
        print(
            f"wrote {args.out}: {args.scope} release {head[:12]}, {len(manifest['services'])} service(s)"
        )
        return 0

    if args.command == "confirm":
        confirm_receipt(args.manifest)
        print(f"receipt confirmed for {args.manifest}")
        return 0

    if args.command == "promote":
        problem = receipt_problem(args.manifest)
        if problem:
            raise SystemExit(f"refusing to promote: {problem}")
        shutil.copyfile(args.manifest, args.current)
        shutil.copyfile(args.manifest + RECEIPT_SUFFIX, args.current + RECEIPT_SUFFIX)
        print(f"{args.current} is now {args.manifest}")
        return 0

    try:
        running = json.loads(Path(args.running).read_text(encoding="utf-8"))
        # Read once: the receipt covers exactly the bytes evaluated.
        target_bytes = Path(args.target).read_bytes() if args.target else b""
        target = json.loads(target_bytes) if target_bytes else None
        approved = _load(args.approved) if args.approved else None
    except (SystemExit, OSError, ValueError) as exc:
        print(f"FAIL {exc}")
        return 1
    certified, certified_problem = None, None
    reference = (
        (target or {}).get("certification") if isinstance(target, dict) else None
    )
    if isinstance(reference, dict) and reference.get("path"):
        cert_path = Path(str(reference["path"]))
        if not cert_path.is_file():
            certified_problem = f"its certification record {cert_path} does not exist"
        elif _sha256(cert_path) != reference.get("sha256"):
            certified_problem = f"its certification record {cert_path} changed after the manifest was written"
        else:
            try:
                certified = json.loads(cert_path.read_text(encoding="utf-8"))
            except ValueError:
                certified_problem = f"{cert_path} is not a certification record"
    same = (
        bool(args.target and args.approved)
        and Path(args.target).resolve() == Path(args.approved).resolve()
    )
    findings = evaluate(
        args.scope or None,
        target,
        approved,
        running,
        Checkout(Path(args.checkout)),
        approved_receipt="it is the release manifest itself"
        if same
        else (receipt_problem(args.approved) if args.approved else None),
        target_receipt=receipt_problem(args.target) if args.target else "no manifest",
        certified=certified,
        certified_problem=certified_problem,
    )
    for finding in findings:
        print(("PASS " if finding.ok else "FAIL ") + finding.message)
    passed = bool(findings) and all(f.ok for f in findings)
    if passed and args.receipt and args.target:
        _record_receipt(
            args.target, hashlib.sha256(target_bytes).hexdigest(), pending=True
        )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
