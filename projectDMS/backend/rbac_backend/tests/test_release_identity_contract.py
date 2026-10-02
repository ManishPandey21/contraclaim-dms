"""Every app service runs exactly the image its release says - scoped, strict.

``post_deploy_verify.sh`` used to require a ``RELEASE_SHA`` the operator had to
export by hand (documented in OPERATIONS 7a, absent from the deployment guide),
so following the guide built every image as ``unknown`` and the verifier failed;
and it compared the contract-worker image with HEAD, so a client-only deploy
failed against correct, unrebuilt backend images.

The contract now (owner decision 2026-10-02 - keep strict image verification,
make it service-scoped):

* the identity is a **build argument** baked into every app image (backend
  images: label + process ``RELEASE_SHA``; client: label), never an ``up``-time
  variable, and every documented build derives it from the checkout;
* the operator declares the deployment scope - ``FULL``, ``CLIENT_ONLY`` or
  ``BACKEND_ONLY`` - and carries a release manifest of exact image ids;
* a deployed service runs the manifest's image, built from the deployed commit;
  a service the scope does not deploy runs exactly its approved image, with an
  unchanged build context; anything unnamed is drift; anything unprovable fails.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[3]
MANIFEST_SCRIPT = PROJECT / "scripts" / "release_manifest.py"
VERIFY = PROJECT / "scripts" / "post_deploy_verify.sh"
DOCKERFILES = {
    "backend": PROJECT / "backend" / "Dockerfile",
    "client": PROJECT / "client" / "Dockerfile",
}
PROD_COMPOSE = PROJECT / "docker-compose.prod.yml"
REPLICA_COMPOSE = PROJECT / "docker-compose.mongo-replicaset.yml"

#: The documents an operator deploys from. Dated evidence records (audits, the
#: 2026-07-09 migration log) are history, not steps.
DEPLOY_DOCS = [
    PROJECT / "docs" / "CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md",
    PROJECT / "docs" / "OPERATIONS.md",
    PROJECT / "docs" / "DOCKER_INSTALL_RUNBOOK.md",
    PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md",
]
DERIVED = 'RELEASE_SHA="$(git rev-parse HEAD)"'
APP_IMAGE_SERVICES = {
    "backend",
    "contract-worker",
    "document-worker",
    "document-worker-canary",
    "client",
}


def _rm():
    spec = importlib.util.spec_from_file_location("release_manifest", MANIFEST_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


RM = _rm()


def _app_image_services(compose: Dict) -> Dict[str, Dict]:
    return {
        name: svc
        for name, svc in (compose.get("services") or {}).items()
        if isinstance(svc.get("build"), dict)
        and str(svc["build"].get("context", "")).rstrip("/")
        in ("./backend", "./client")
    }


# --------------------------------------------------------------------------- #
# every image carries its identity, from one build argument
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_the_dockerfile_bakes_the_build_argument_into_the_revision_label(name):
    text = DOCKERFILES[name].read_text(encoding="utf-8")
    assert re.search(r"^ARG RELEASE_SHA=unknown$", text, re.M)
    assert re.search(
        r"^LABEL org\.opencontainers\.image\.revision=\$\{RELEASE_SHA\}$", text, re.M
    )
    if name == "backend":
        # The runtime heartbeat reports it; from the image, never from `up`.
        assert re.search(r"^ENV RELEASE_SHA=\$\{RELEASE_SHA\}$", text, re.M)
        assert text.index("ARG RELEASE_SHA") > text.index("pip install")


def test_every_production_app_service_builds_with_the_identity_and_never_sets_it_at_up():
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    services = _app_image_services(compose)
    assert set(services) == APP_IMAGE_SERVICES, sorted(services)
    for name, svc in services.items():
        assert (svc["build"].get("args") or {}).get(
            "RELEASE_SHA"
        ) == "${RELEASE_SHA:-unknown}", name
        assert "RELEASE_SHA" not in (svc.get("environment") or {}), (
            f"{name} sets RELEASE_SHA at `up`, which overrides the identity baked into its image"
        )


def test_every_compose_file_that_builds_an_app_image_passes_the_identity():
    for path in sorted(PROJECT.glob("docker-compose*.yml")):
        compose = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, svc in _app_image_services(compose).items():
            assert (svc["build"].get("args") or {}).get(
                "RELEASE_SHA"
            ) == "${RELEASE_SHA:-unknown}", (
                f"{path.name}:{name} builds an app image without the release identity"
            )


def test_the_scripted_deploy_derives_the_identity_from_the_checkout():
    text = (PROJECT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert 'RELEASE_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"' in text
    assert text.index("export RELEASE_SHA") < text.index("up -d --build")


# --------------------------------------------------------------------------- #
# the documented flow
# --------------------------------------------------------------------------- #


def _commands(doc: Path) -> Iterator[Tuple[int, str]]:
    """Every shell command in a fenced bash block, continuations joined."""
    lines = doc.read_text(encoding="utf-8").splitlines()
    inside, buffer, start = False, "", 0
    for number, raw in enumerate(lines, 1):
        line = re.sub(r"^\s*>\s?", "", raw)
        if line.strip().startswith("```"):
            inside = line.strip() in ("```bash", "```sh") if not inside else False
            buffer = ""
            continue
        if not inside:
            continue
        if not buffer:
            start = number
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        yield start, re.sub(r"\s+", " ", (buffer + line).strip())
        buffer = ""


def _builds_an_app_image(command: str) -> bool:
    if "docker compose" not in command:
        return False
    words = command.split()
    if "build" in words:
        targets = words[words.index("build") + 1 :]
    elif "up" in words and "--build" in words:
        targets = words[words.index("up") + 1 :]
    else:
        return False
    services = [w for w in targets if not w.startswith("-")]
    return not services or bool(APP_IMAGE_SERVICES & set(services))


def test_every_documented_app_image_build_derives_the_identity_from_the_checkout():
    found = [
        f"{doc.name}:{line}: {command}"
        for doc in DEPLOY_DOCS
        for line, command in _commands(doc)
        if _builds_an_app_image(command) and not command.startswith(DERIVED + " ")
    ]
    assert not found, (
        "documented builds that would label the image `unknown`:\n" + "\n".join(found)
    )


def test_the_documented_flows_were_found():
    seen = {
        doc.name
        for doc in DEPLOY_DOCS
        for _, c in _commands(doc)
        if _builds_an_app_image(c)
    }
    assert {
        "CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md",
        "OPERATIONS.md",
        "DOCKER_INSTALL_RUNBOOK.md",
    } <= seen


def test_the_guide_declares_every_scope_and_verifies_with_its_manifests():
    commands = [c for _, c in _commands(DEPLOY_DOCS[0])]
    verifies = [c for c in commands if "post_deploy_verify.sh" in c]
    for scope in ("FULL", "CLIENT_ONLY", "BACKEND_ONLY"):
        matching = [c for c in verifies if f"DEPLOY_SCOPE={scope}" in c]
        assert matching, f"the guide never verifies a {scope} deploy"
        assert all("RELEASE_MANIFEST=" in c for c in matching), scope
        if scope != "FULL":
            assert all("APPROVED_MANIFEST=" in c for c in matching), scope
        assert any(
            f"--scope {scope}" in c and "release_manifest.py target" in c
            for c in commands
        ), scope


def test_a_client_only_build_is_still_an_app_image_build():
    assert _builds_an_app_image("docker compose -f a.yml build client")
    assert _builds_an_app_image("docker compose $COMPOSE_FILES up -d --build")
    assert not _builds_an_app_image("docker compose -f a.yml up -d --no-deps client")


def test_the_guide_rendered_through_compose_labels_the_deployed_commit(tmp_path):
    docker = shutil.which("docker")
    if (
        not docker
        or subprocess.run(
            [docker, "compose", "version"], capture_output=True
        ).returncode
    ):
        pytest.skip("docker compose unavailable")
    head = subprocess.run(
        ["git", "-C", str(PROJECT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    required = sorted(
        set(
            re.findall(
                r"\$\{([A-Z0-9_]+):\?",
                PROD_COMPOSE.read_text() + REPLICA_COMPOSE.read_text(),
            )
        )
    )
    env_file = tmp_path / "placeholder.env"
    env_file.write_text(
        "".join(f"{name}=placeholder\n" for name in required), encoding="utf-8"
    )
    rendered = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(PROD_COMPOSE),
            "-f",
            str(REPLICA_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "RELEASE_SHA": head},
        cwd=PROJECT,
        check=True,
    )
    services = json.loads(rendered.stdout)["services"]
    for name in APP_IMAGE_SERVICES:
        assert services[name]["build"]["args"]["RELEASE_SHA"] == head, name
        assert "RELEASE_SHA" not in (services[name].get("environment") or {}), name


# --------------------------------------------------------------------------- #
# the scoped, strict image policy
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """Commits: v1 (both trees), v2 client-only change, v3 backend change."""
    if not shutil.which("git"):
        pytest.skip("git required")
    root = tmp_path / "projectDMS"
    (root / "backend").mkdir(parents=True)
    (root / "client").mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    (root / "backend" / "app.py").write_text("v = 1\n")
    (root / "backend" / ".dockerignore").write_text("*.log\n__pycache__/\n")
    (root / "client" / "app.js").write_text("v = 1\n")
    (root / "client" / ".dockerignore").write_text("node_modules/\n")
    (root / ".gitignore").write_text("*.log\n__pycache__/\nnode_modules/\n.venv312/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "v1")
    commits = {"v1": _git(root, "rev-parse", "HEAD")}
    (root / "client" / "app.js").write_text("v = 2\n")
    _git(root, "commit", "-qam", "v2 client")
    commits["v2"] = _git(root, "rev-parse", "HEAD")
    (root / "backend" / "app.py").write_text("v = 3\n")
    _git(root, "commit", "-qam", "v3 backend")
    commits["v3"] = _git(root, "rev-parse", "HEAD")
    return root, commits


def _image(n: int) -> str:
    return "sha256:" + f"{n:064x}"


BACKEND_SERVICES = ("backend", "contract-worker", "document-worker")


def _manifest(release: str, scope: str, entries: Dict[str, Tuple[int, str]]) -> Dict:
    return {
        "schema": RM.SCHEMA,
        "release_sha": release,
        "scope": scope,
        "services": {
            name: {"image_id": _image(img), "revision": rev}
            for name, (img, rev) in entries.items()
        },
    }


def _running(entries: Dict[str, Tuple[int, str]]) -> Dict:
    running: Dict = {
        name: [{"id": f"{name}-cid", "image_id": _image(img), "revision": rev}]
        for name, (img, rev) in entries.items()
    }
    running["_project"] = "contraclaim"  # the shell always reports it
    return running


_AUTO = object()


def _cert_for(target, scope) -> Optional[Dict]:
    """The certification a correct target run would have checked: the deployed
    entries of ``target``, certified for its release."""
    if not isinstance(target, dict) or scope not in RM.SCOPES:
        return None
    deployed = RM.SCOPES[scope]
    images = {
        n: dict(e) for n, e in (target.get("services") or {}).items() if n in deployed
    }
    return {
        "schema": RM.CERT_SCHEMA,
        "release_sha": target.get("release_sha"),
        "images": images,
        "evidence": "test",
    }


def _check(
    root: Path, scope: Optional[str], target, approved, running, certified=_AUTO
) -> List:
    if certified is _AUTO:
        certified = _cert_for(target, scope)
    return RM.evaluate(
        scope, target, approved, running, RM.Checkout(root), certified=certified
    )


def _approve(path: Path) -> None:
    """What a green verification leaves behind (verify --receipt, then confirm)."""
    RM._record_receipt(str(path), RM._sha256(path), pending=False)


def _trivy(image_id: str, *vulnerabilities: Dict) -> Dict:
    """The shape `trivy image --format json` writes, reduced to what certify reads."""
    return {
        "ArtifactName": "contraclaim-backend:latest",
        "Metadata": {"ImageID": image_id},
        "Results": [
            {
                "Target": "os",
                "Class": "os-pkgs",
                "Vulnerabilities": list(vulnerabilities),
            }
        ],
    }


def _scans(images: Dict[str, Dict[str, str]]) -> Dict[str, Dict]:
    return {
        n: {
            "report": _trivy(e["image_id"]),
            "path": f"/scans/{n}.json",
            "sha256": "0" * 64,
        }
        for n, e in images.items()
    }


def _release_files(tmp_path: Path, scope: str, release: str, state, approved_path=None):
    """Certification + manifest on disk, as certify and target write them."""
    deployed = {n: v for n, v in state.items() if n in RM.SCOPES[scope]}
    cert = tmp_path / f"certified-{scope}.json"
    cert.write_text(
        json.dumps(
            RM.build_certification(
                release,
                {
                    n: {"image_id": _image(i), "revision": r}
                    for n, (i, r) in deployed.items()
                },
                _scans({n: {"image_id": _image(i)} for n, (i, r) in deployed.items()}),
            )
        )
    )
    approved = json.loads(approved_path.read_text()) if approved_path else None
    manifest = RM.build_target(
        scope,
        release,
        {n: {"image_id": _image(i), "revision": r} for n, (i, r) in deployed.items()},
        approved,
        json.loads(cert.read_text()),
        {"path": str(cert.resolve()), "sha256": RM._sha256(cert)},
    )
    target = tmp_path / f"release-{scope}.json"
    target.write_text(json.dumps(manifest))
    return target, cert


def _failures(findings) -> List[str]:
    return [f.message for f in findings if not f.ok]


def _v1_full(c):
    return {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (2, c["v1"])}


def test_A_full_deploy_with_every_service_on_its_certified_image_passes(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    findings = _check(
        root, "FULL", _manifest(c["v3"], "FULL", state), None, _running(state)
    )
    assert _failures(findings) == []
    assert len(findings) == 4


def test_B_client_only_deploy_keeps_the_backend_on_its_approved_images(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    findings = _check(
        root,
        "CLIENT_ONLY",
        _manifest(c["v2"], "CLIENT_ONLY", state),
        approved,
        _running(state),
    )
    assert _failures(findings) == []
    assert any("backend" in f.message and "unchanged" in f.message for f in findings)


def test_C_client_only_deploy_with_the_backend_changed_underneath_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    running = _running({**state, "backend": (99, c["v2"])})  # someone rebuilt the API
    failures = _failures(
        _check(
            root,
            "CLIENT_ONLY",
            _manifest(c["v2"], "CLIENT_ONLY", state),
            approved,
            running,
        )
    )
    assert any(
        m.startswith("backend") and "manifest requires" in m for m in failures
    ), failures


def test_C2_client_only_scope_for_a_release_that_changed_backend_code_fails(repo):
    """The running images are the approved ones, but the checkout now holds
    backend code nothing runs: the scope was mis-declared."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v3"])}
    failures = _failures(
        _check(
            root,
            "CLIENT_ONLY",
            _manifest(c["v3"], "CLIENT_ONLY", state),
            approved,
            _running(state),
        )
    )
    assert any("./backend changed" in m for m in failures), failures


def test_C3_a_scoped_manifest_that_rewrites_a_non_deployed_service_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {
        **{s: (1, c["v1"]) for s in BACKEND_SERVICES},
        "client": (20, c["v2"]),
        "backend": (77, c["v1"]),
    }
    failures = _failures(
        _check(
            root,
            "CLIENT_ONLY",
            _manifest(c["v2"], "CLIENT_ONLY", state),
            approved,
            _running(state),
        )
    )
    assert any("changes its image from the approved one" in m for m in failures), (
        failures
    )


def test_D_client_on_the_wrong_image_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    running = _running({**state, "client": (2, c["v1"])})  # the old client is still up
    failures = _failures(
        _check(
            root,
            "CLIENT_ONLY",
            _manifest(c["v2"], "CLIENT_ONLY", state),
            approved,
            running,
        )
    )
    assert any(m.startswith("client") for m in failures), failures


def test_E_full_deploy_with_one_worker_on_the_wrong_image_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    running = _running({**state, "document-worker": (1, c["v1"])})
    failures = _failures(
        _check(root, "FULL", _manifest(c["v3"], "FULL", state), None, running)
    )
    assert len(failures) == 1 and failures[0].startswith("document-worker"), failures


def test_E2_full_deploy_whose_manifest_holds_an_older_build_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {
        **{s: (10, c["v3"]) for s in BACKEND_SERVICES},
        "client": (11, c["v3"]),
        "contract-worker": (1, c["v1"]),
    }
    failures = _failures(
        _check(root, "FULL", _manifest(c["v3"], "FULL", state), None, _running(state))
    )
    assert any(
        m.startswith("contract-worker") and "deployed by this FULL release" in m
        for m in failures
    )


@pytest.mark.parametrize(
    "case",
    [
        "no_scope",
        "free_form_scope",
        "no_manifest",
        "unknown_revision",
        "manifest_for_other_commit",
        "scope_mismatch",
        "no_approved",
    ],
)
def test_F_missing_or_unprovable_identity_fails(repo, case):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    scope, target, approved = "FULL", _manifest(c["v3"], "FULL", state), None
    if case == "no_scope":
        scope = None
    elif case == "free_form_scope":
        scope = "SKIP"
    elif case == "no_manifest":
        target = None
    elif case == "unknown_revision":
        target["services"]["backend"]["revision"] = "unknown"
    elif case == "manifest_for_other_commit":
        target["release_sha"] = c["v1"]
    elif case == "scope_mismatch":
        target["scope"] = "CLIENT_ONLY"
    elif case == "no_approved":
        scope, target["scope"] = "BACKEND_ONLY", "BACKEND_ONLY"
    findings = _check(root, scope, target, approved, _running(state))
    assert _failures(findings), case


def test_F2_a_canary_on_another_image_than_its_worker_fails(repo):
    """With no entry of its own the canary is held to the document-worker image
    it is a canary of; any other image is drift to an unapproved image."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)
    on_worker = _running({**state, "document-worker-canary": (10, c["v3"])})
    assert _failures(_check(root, "FULL", target, None, on_worker)) == []
    elsewhere = _running({**state, "document-worker-canary": (99, c["v3"])})
    failures = _failures(_check(root, "FULL", target, None, elsewhere))
    assert any(m.startswith("document-worker-canary") for m in failures), failures


def test_F3_an_app_image_under_an_unnamed_service_is_drift(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    running = _running(state)
    running["_others"] = [
        {"id": "mongo1", "service": "mongo1", "image_id": _image(500)},
        {"id": "renamed", "service": "api-old", "image_id": _image(10)},
    ]
    failures = _failures(
        _check(root, "FULL", _manifest(c["v3"], "FULL", state), None, running)
    )
    assert failures == [
        "api-old renamed: runs an app image under a service the manifest does not name (drift)"
    ], failures


def test_F4_an_approved_manifest_without_a_receipt_is_not_approval(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    findings = RM.evaluate(
        "CLIENT_ONLY",
        _manifest(c["v2"], "CLIENT_ONLY", state),
        approved,
        _running(state),
        RM.Checkout(root),
        approved_receipt="current.json has no verification receipt",
    )
    assert any("is not approval" in m for m in _failures(findings))


def test_UNCHANGED_holds_every_service_to_the_approved_manifest(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    current = _manifest(
        c["v2"],
        "CLIENT_ONLY",
        {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])},
    )
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    assert (
        _failures(
            RM.evaluate("UNCHANGED", current, None, _running(state), RM.Checkout(root))
        )
        == []
    )
    moved = _running({**state, "client": (21, c["v2"])})
    assert _failures(RM.evaluate("UNCHANGED", current, None, moved, RM.Checkout(root)))
    unapproved = RM.evaluate(
        "UNCHANGED",
        current,
        None,
        _running(state),
        RM.Checkout(root),
        target_receipt="no receipt",
    )
    assert any("needs the approved manifest" in m for m in _failures(unapproved))


def test_G_a_modified_build_context_fails_closed(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)

    def failures():
        return _failures(_check(root, "FULL", target, None, _running(state)))

    (root / "backend" / "app.py").write_text("v = 'patched'\n")
    assert any("build inputs the commit does not" in m for m in failures())
    (root / "backend" / "app.py").write_text("v = 3\n")
    # What .dockerignore keeps out of the image is not a build input.
    (root / "backend" / "local.log").write_text("x\n")
    (root / "backend" / "__pycache__").mkdir()
    (root / "backend" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"x")
    assert failures() == []
    # An untracked module would be baked in by `COPY . .`.
    (root / "backend" / "patch.py").write_text("PATCHED = True\n")
    assert any("backend/patch.py" in m for m in failures())
    (root / "backend" / "patch.py").unlink()
    # Git-ignored is not docker-ignored: a local venv inside the context ships.
    (root / "backend" / ".venv312").mkdir()
    (root / "backend" / ".venv312" / "site.py").write_text("x\n")
    assert any("backend/.venv312/site.py" in m for m in failures())


def test_G2_the_wrong_checkout_root_fails_instead_of_matching_everything(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    failures = _failures(
        RM.evaluate(
            "CLIENT_ONLY",
            _manifest(c["v2"], "CLIENT_ONLY", state),
            approved,
            _running(state),
            RM.Checkout(root / "backend"),
        )
    )
    assert any("wrong checkout root" in m for m in failures), failures


def test_G3_a_non_deployed_image_from_a_commit_this_checkout_lacks_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    ghost = "f" * 40
    approved = _manifest(
        ghost,
        "FULL",
        {**{s: (1, ghost) for s in BACKEND_SERVICES}, "client": (2, ghost)},
    )
    state = {**{s: (1, ghost) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    failures = _failures(
        _check(
            root,
            "CLIENT_ONLY",
            _manifest(c["v2"], "CLIENT_ONLY", state),
            approved,
            _running(state),
        )
    )
    assert any("is not in this checkout" in m for m in failures), failures


def test_a_required_service_that_is_not_running_fails(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    running = _running(state)
    running["contract-worker"] = []
    failures = _failures(
        _check(root, "FULL", _manifest(c["v3"], "FULL", state), None, running)
    )
    assert any("contract-worker: no running container" in m for m in failures), failures


# --------------------------------------------------------------------------- #
# writing the manifest
# --------------------------------------------------------------------------- #


def _certified(release: str, entries: Dict[str, Tuple[int, str]]) -> Dict:
    images = {
        name: {"image_id": _image(img), "revision": rev}
        for name, (img, rev) in entries.items()
    }
    return RM.build_certification(release, images, _scans(images))


def test_a_scoped_manifest_copies_the_approved_entries_it_does_not_deploy(repo):
    _, c = repo
    approved = _manifest(c["v1"], "FULL", _v1_full(c))
    target = RM.build_target(
        "CLIENT_ONLY",
        c["v2"],
        {"client": {"image_id": _image(20), "revision": c["v2"]}},
        approved,
        _certified(c["v2"], {"client": (20, c["v2"])}),
    )
    assert target["services"]["backend"] == approved["services"]["backend"]
    assert target["services"]["client"] == {"image_id": _image(20), "revision": c["v2"]}
    assert target["scope"] == "CLIENT_ONLY" and target["release_sha"] == c["v2"]


@pytest.mark.parametrize(
    "scope,images,approved,why",
    [
        ("CLIENT_ONLY", {"client": (20, "v2")}, None, "needs --approved"),
        ("FULL", {"client": (20, "v2")}, None, "pass --image backend"),
        ("CLIENT_ONLY", {"client": (20, "v1")}, "v1", "not the release"),
        ("CLIENT_ONLY", {"client": (20, "unknown")}, "v1", "not a commit"),
        (
            "CLIENT_ONLY",
            {"client": (20, "v2"), "backend": (1, "v2")},
            "v1",
            "does not deploy",
        ),
        ("ANYTHING", {}, None, "scope must be one of"),
        ("UNCHANGED", {}, "v1", "scope must be one of"),
        ("CLIENT_ONLY", {"client": (21, "v2")}, "v1", "is not the one certified"),
        ("CLIENT_ONLY", {"client": (20, "v2")}, "v1", "NO_CERT"),
        ("CLIENT_ONLY", {"client": (20, "v2")}, "v1", "OTHER_CERT"),
    ],
)
def test_a_manifest_that_cannot_be_vouched_for_is_refused(
    repo, scope, images, approved, why
):
    _, c = repo
    built = {
        name: {"image_id": _image(img), "revision": c.get(rev, rev)}
        for name, (img, rev) in images.items()
    }
    previous = _manifest(c["v1"], "FULL", _v1_full(c)) if approved else None
    certified = _certified(c["v2"], {"client": (20, c["v2"])})
    if why == "NO_CERT":
        certified, why = None, "--certified is required"
    elif why == "OTHER_CERT":
        certified, why = (
            _certified(c["v1"], {"client": (20, c["v1"])}),
            "the certification is for",
        )
    with pytest.raises(SystemExit) as refused:
        RM.build_target(scope, c["v2"], built, previous, certified)
    assert why in str(refused.value)


def test_a_certification_needs_a_clean_scan_of_the_exact_image(repo):
    """Evidence about another binary is not evidence: CI scans and discards its
    own build of the commit, a host build has another image id."""
    _, c = repo
    image = {"client": {"image_id": _image(1), "revision": c["v2"]}}
    record = RM.build_certification(c["v2"], image, _scans(image))
    assert record["images"]["client"]["scan"]["path"] == "/scans/client.json"
    with pytest.raises(SystemExit, match="pass --scan client"):
        RM.build_certification(c["v2"], image, {})
    other_build = {"client": {"report": _trivy(_image(2)), "path": "x", "sha256": "0"}}
    with pytest.raises(SystemExit, match="it scanned image"):
        RM.build_certification(c["v2"], image, other_build)
    fixable = {"Severity": "HIGH", "FixedVersion": "1.2.3", "VulnerabilityID": "CVE-1"}
    unfixed = {"Severity": "CRITICAL", "FixedVersion": "", "VulnerabilityID": "CVE-2"}
    dirty = {
        "client": {"report": _trivy(_image(1), fixable), "path": "x", "sha256": "0"}
    }
    with pytest.raises(SystemExit, match="CVE-1"):
        RM.build_certification(c["v2"], image, dirty)
    accepted = {
        "client": {"report": _trivy(_image(1), unfixed), "path": "x", "sha256": "0"}
    }
    assert RM.build_certification(
        c["v2"], image, accepted
    )  # the CI policy ignores unfixed
    with pytest.raises(SystemExit, match="not "):
        RM.build_certification(
            c["v2"],
            {"client": {"image_id": _image(1), "revision": c["v1"]}},
            _scans(image),
        )


def test_approval_comes_only_from_a_green_verify_then_confirm(repo, tmp_path):
    """No command writes a receipt by itself: ``verify --receipt`` leaves one
    pending for the exact bytes it evaluated, only on an all-pass result, and
    ``confirm`` (run by post_deploy_verify.sh after a run with no failure)
    turns it into the receipt ``promote`` requires."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target, _ = _release_files(tmp_path, "FULL", c["v3"], state)
    running = tmp_path / "running.json"
    current = tmp_path / "current.json"
    verify = [
        "verify",
        "--scope",
        "FULL",
        "--target",
        str(target),
        "--checkout",
        str(root),
        "--running",
        str(running),
        "--receipt",
    ]
    with pytest.raises(SystemExit):
        RM.main(["receipt", "--manifest", str(target), "--scope", "FULL"])

    running.write_text(json.dumps(_running({**state, "client": (99, c["v3"])})))
    assert RM.main(verify) == 1
    assert not Path(str(target) + RM.PENDING_SUFFIX).exists(), (
        "a failed verify left a receipt"
    )
    with pytest.raises(SystemExit, match="no pending receipt"):
        RM.main(["confirm", "--manifest", str(target)])

    running.write_text(json.dumps(_running(state)))
    assert RM.main(verify) == 0
    with pytest.raises(SystemExit, match="never verified green"):
        RM.main(["promote", "--manifest", str(target), "--current", str(current)])
    original = target.read_bytes()
    target.write_bytes(original.replace(b'"FULL"', b'"FULL" '))  # edited after verify
    with pytest.raises(SystemExit, match="changed after it was verified"):
        RM.main(["confirm", "--manifest", str(target)])
    target.write_bytes(original)
    assert RM.main(["confirm", "--manifest", str(target)]) == 0
    assert (
        RM.main(["promote", "--manifest", str(target), "--current", str(current)]) == 0
    )
    assert RM.receipt_problem(str(current)) is None
    current.write_text('{"schema": "hand-edited"}')
    assert "changed after it verified green" in RM.receipt_problem(str(current))


def test_the_gate_itself_refuses_an_uncertified_deployed_image(repo, tmp_path):
    """Certification is checked by verify, not only when the manifest is
    written: a hand-written manifest naming HEAD-labelled but uncertified
    images must not pass case A."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)
    assert _failures(
        _check(root, "FULL", target, None, _running(state), certified=None)
    )
    other = _cert_for(target, "FULL")
    other["images"]["contract-worker"] = {"image_id": _image(77), "revision": c["v3"]}
    failures = _failures(
        _check(root, "FULL", target, None, _running(state), certified=other)
    )
    assert any(
        m.startswith("contract-worker") and "not the one certified" in m
        for m in failures
    )
    stale = dict(_cert_for(target, "FULL"), release_sha=c["v1"])
    assert _failures(
        _check(root, "FULL", target, None, _running(state), certified=stale)
    )
    # A record that certifies nothing certifies nothing - it must not skip the check.
    for empty in ({}, [], None):
        hollow = dict(_cert_for(target, "FULL"), images=empty)
        failures = _failures(
            _check(root, "FULL", target, None, _running(state), certified=hollow)
        )
        assert any("certifies no images" in m for m in failures), (empty, failures)
    # The CLI follows the record the manifest names, and its exact bytes.
    path, cert = _release_files(tmp_path, "FULL", c["v3"], state)
    running = tmp_path / "running.json"
    running.write_text(json.dumps(_running(state)))
    args = [
        "verify",
        "--scope",
        "FULL",
        "--target",
        str(path),
        "--checkout",
        str(root),
        "--running",
        str(running),
    ]
    assert RM.main(args) == 0
    cert.write_text(cert.read_text().replace('"certified_at"', '"certified_at_edited"'))
    assert RM.main(args) == 1


def test_a_one_off_compose_run_of_an_app_service_is_drift(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    running = _running(state)
    running["_others"] = [
        {"id": "run-1", "service": "backend", "image_id": _image(10), "oneoff": True}
    ]
    failures = _failures(
        _check(root, "FULL", _manifest(c["v3"], "FULL", state), None, running)
    )
    assert any("one-off" in m for m in failures), failures


def test_docker_ignore_patterns_do_not_cross_directories(repo):
    """Docker's `*.pyc` and `__pycache__/` exclude only at the context root; a
    nested one is baked in by `COPY . .` and must be reported."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    nested = root / "backend" / "pkg" / "__pycache__"
    nested.mkdir(parents=True)
    (nested / "mod.cpython-312.pyc").write_bytes(b"x")
    (root / "backend" / "pkg" / "debug.log").write_text("x")
    stray = RM.Checkout(root).stray_inputs("backend")
    assert "backend/pkg/__pycache__/mod.cpython-312.pyc" in stray
    assert "backend/pkg/debug.log" in stray
    assert RM._excluded("deep/dir/x.env", ["**/*.env"])
    assert not RM._excluded("deep/x.pyc", ["*.pyc"])


def test_the_cli_refuses_an_approved_manifest_that_is_the_release_itself(
    repo, tmp_path
):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    target = tmp_path / "release.json"
    target.write_text(json.dumps(_manifest(c["v2"], "CLIENT_ONLY", state)))
    _approve(target)
    running = tmp_path / "running.json"
    running.write_text(json.dumps(_running(state)))
    args = [
        "verify",
        "--scope",
        "CLIENT_ONLY",
        "--target",
        str(target),
        "--approved",
        str(target),
    ]
    assert RM.main([*args, "--checkout", str(root), "--running", str(running)]) == 1


# --------------------------------------------------------------------------- #
# the verifier runs the policy, read-only
# --------------------------------------------------------------------------- #


def test_the_verifier_runs_the_manifest_check_and_maps_every_line():
    text = VERIFY.read_text(encoding="utf-8")
    assert 'scripts/release_manifest.py" verify' in text
    for flag in (
        '--scope "$DEPLOY_SCOPE"',
        '--target "$RELEASE_MANIFEST"',
        '--approved "$APPROVED_MANIFEST"',
    ):
        assert flag in text, flag
    assert '"FAIL "*) fail' in text and '"PASS "*) pass' in text
    assert '*) fail "release image check: $line"' in text, (
        "an unrecognised line must not pass silently"
    )
    assert 'fail "No python interpreter for the release image check' in text
    # The process must report the identity its image was built with.
    assert 'container_env "$cid" RELEASE_SHA' in text
    assert '-e "CW_RELEASES=${contract_worker_releases}"' in text
    # Equality with HEAD and the content-equivalence fallback are both gone.
    assert '"$revision" == "$deployed_commit"' not in text
    assert "release_identity" not in text


def test_the_cli_verifies_end_to_end_and_fails_without_a_scope(repo, tmp_path):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target, _ = _release_files(tmp_path, "FULL", c["v3"], state)
    running = tmp_path / "running.json"
    running.write_text(json.dumps(_running(state)))
    args = [
        "verify",
        "--target",
        str(target),
        "--checkout",
        str(root),
        "--running",
        str(running),
    ]
    assert RM.main([*args, "--scope", "FULL"]) == 0
    assert RM.main(args) == 1


def _verify_section() -> str:
    text = VERIFY.read_text(encoding="utf-8")
    start = text.index("printf '\\n--- Release images")
    end = text.index('rm -f "$running_json"', start) + len('rm -f "$running_json"')
    return text[start:end]


def _run_section(
    root: Path,
    tmp_path: Path,
    env: Dict[str, str],
    containers: Dict[str, List[Tuple[str, str, str]]],
):
    """Run the verifier's release-image section with docker stubbed: the shell
    half (the running-container JSON, the line mapping) is what this proves."""
    bash = shutil.which("bash")
    git = shutil.which("git")
    if not bash or not git:
        pytest.skip("bash and git required")
    table = tmp_path / "containers.tsv"
    table.write_text(
        "".join(
            f"{svc}\t{cid}\t{image}\t{rev}\n"
            for svc, rows in containers.items()
            for cid, image, rev in rows
        ),
        encoding="utf-8",
    )
    python = Path(sys.executable).as_posix()
    prelude = rf"""
set -euo pipefail
failures=0
pass() {{ printf 'PASS: %s\n' "$1"; }}
fail() {{ printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }}
ROOT_DIR="{root.as_posix()}"
SCRIPTS="{(PROJECT / "scripts").as_posix()}"
TABLE="{table.as_posix()}"
compose_ps_q() {{ awk -F'\t' -v s="$1" '$1 == s {{ print $2 }}' "$TABLE"; }}
image_revision() {{ awk -F'\t' -v c="$1" '$2 == c {{ print $4 }}' "$TABLE"; }}
docker() {{
  case "$1" in
    compose) printf '%s' '{{"name": "proj", "services": {{"backend": {{}}, "mongo1": {{}}}}}}' ;;
    ps) ;;
    *) awk -F'\t' -v c="${{@: -1}}" '$2 == c {{ print $3 }}' "$TABLE" ;;
  esac
}}
resolve_python_bin() {{ printf '%s' "{python}"; }}
backend_cid=""
ENV_FILE=/dev/null
COMPOSE_FILES=""
DEPLOY_SCOPE="${{DEPLOY_SCOPE:-}}"; RELEASE_MANIFEST="${{RELEASE_MANIFEST:-}}"; APPROVED_MANIFEST="${{APPROVED_MANIFEST:-}}"
"""
    section = _verify_section().replace(
        '"$ROOT_DIR/scripts/release_manifest.py"', '"$SCRIPTS/release_manifest.py"'
    )
    script = tmp_path / "section.sh"
    script.write_text(
        prelude + section + '\necho "failures=$failures"\n',
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [bash, str(script)], capture_output=True, text=True, env={**os.environ, **env}
    )
    return result.stdout + result.stderr


def test_the_verifier_section_passes_a_client_only_deploy_and_fails_drift(
    repo, tmp_path
):
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = tmp_path / "current.json"
    approved.write_text(json.dumps(_manifest(c["v1"], "FULL", _v1_full(c))))
    _approve(approved)
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    target, _ = _release_files(tmp_path, "CLIENT_ONLY", c["v2"], state, approved)
    containers = {
        name: [(f"{name}-1", _image(img), rev)] for name, (img, rev) in state.items()
    }
    env = {
        "DEPLOY_SCOPE": "CLIENT_ONLY",
        "RELEASE_MANIFEST": str(target),
        "APPROVED_MANIFEST": str(approved),
    }

    out = _run_section(root, tmp_path, env, containers)
    assert "failures=0" in out, out
    assert out.count("PASS: ") == 4, out
    # The image check passed, but this run ended without the script's final
    # confirm: the EXIT trap removed the pending receipt, and nothing is approval.
    assert not Path(str(target) + RM.PENDING_SUFFIX).exists()
    assert not Path(str(target) + RM.RECEIPT_SUFFIX).exists()

    drifted = {**containers, "backend": [("backend-1", _image(99), c["v2"])]}
    out = _run_section(root, tmp_path, env, drifted)
    assert "FAIL: backend" in out and "failures=0" not in out, out

    out = _run_section(root, tmp_path, {**env, "DEPLOY_SCOPE": ""}, containers)
    assert "FAIL: deployment scope" in out, out


def _all_doc_commands() -> Iterator[Tuple[str, int, str]]:
    for doc in sorted((PROJECT / "docs").rglob("*.md")):
        # Dated records and agent plans are history, not procedures.
        if {"history", "superpowers"} & set(doc.relative_to(PROJECT / "docs").parts):
            continue
        for line, command in _commands(doc):
            yield doc.relative_to(PROJECT / "docs").as_posix(), line, command


def test_every_documented_verifier_run_declares_its_scope_and_manifests():
    """A verifier run without them is a guaranteed red - and a check that is
    always red teaches operators to ignore it."""
    found = []
    for doc, line, command in _all_doc_commands():
        if command.startswith(("git ", "bash -n")) or not re.search(
            r"(^|[\s/])post_deploy_verify\.sh\b", command
        ):
            continue
        scope = re.search(r"DEPLOY_SCOPE=(\S+)", command)
        problems = []
        if not scope:
            problems.append("no DEPLOY_SCOPE")
        if "RELEASE_MANIFEST=" not in command:
            problems.append("no RELEASE_MANIFEST")
        if (
            scope
            and scope.group(1) in ("CLIENT_ONLY", "BACKEND_ONLY")
            and "APPROVED_MANIFEST=" not in command
        ):
            problems.append("scoped deploy without APPROVED_MANIFEST")
        if problems:
            found.append(f"{doc}:{line}: {', '.join(problems)}: {command}")
    assert not found, "\n".join(found)


def test_every_documented_manifest_is_certified_and_promoted_not_copied():
    found = []
    for doc, line, command in _all_doc_commands():
        if "release_manifest.py target" in command and "--certified" not in command:
            found.append(f"{doc}:{line}: target without --certified")
        if re.search(r"\bcp\b[^\n]*current\.json", command):
            found.append(f"{doc}:{line}: current.json written by cp, not promote")
    assert not found, "\n".join(found)


def test_an_unreadable_compose_project_and_an_orphan_app_image_fail(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)
    running = _running(state)
    running["_project"] = ""
    failures = _failures(_check(root, "FULL", target, None, running))
    assert any("cannot read the compose project" in m for m in failures), failures

    running = _running(state)
    running["_project"] = "contraclaim"
    running["_others"] = [
        # a data service of the project: known, not an app image
        {
            "id": "m1",
            "service": "mongo1",
            "image_id": _image(500),
            "revision": "",
            "known": True,
        },
        # a renamed-away service still running an old app build
        {
            "id": "o1",
            "service": "api-v0",
            "image_id": _image(3),
            "revision": c["v1"],
            "known": False,
        },
    ]
    failures = _failures(_check(root, "FULL", target, None, running))
    assert failures == [
        f"api-v0 o1: an orphan of the compose project (no such service) running an app image (revision {c['v1'][:12]}) (drift)"
    ], failures


def test_every_documented_certification_scans_each_image_it_certifies():
    found = []
    for doc, line, command in _all_doc_commands():
        if "release_manifest.py certify" not in command:
            continue
        images = set(re.findall(r"--image ([a-z-]+)=", command))
        scans = set(re.findall(r"--scan ([a-z-]+)=", command))
        if not images or images != scans:
            found.append(
                f"{doc}:{line}: images {sorted(images)} vs scans {sorted(scans)}"
            )
    assert not found, "\n".join(found)


def test_an_orphan_on_an_unlabelled_build_and_a_missing_project_fail(repo):
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)
    running = _running(state)
    # built before RELEASE_SHA existed: the Dockerfile still writes `unknown`
    running["_others"] = [
        {
            "id": "o2",
            "service": "worker-v0",
            "image_id": _image(4),
            "revision": "unknown",
            "known": False,
        },
        {
            "id": "g1",
            "service": "graphiti-x",
            "image_id": _image(6),
            "revision": "",
            "known": False,
        },
    ]
    failures = _failures(_check(root, "FULL", target, None, running))
    assert len(failures) == 1 and failures[0].startswith("worker-v0 o2"), failures
    running = _running(state)
    del running["_project"]
    assert any(
        "cannot read the compose project" in m
        for m in _failures(_check(root, "FULL", target, None, running))
    )


def test_a_report_without_a_package_scan_or_with_a_secret_certifies_nothing(repo):
    _, c = repo
    image = {"client": {"image_id": _image(1), "revision": c["v2"]}}
    secrets_only = {
        "Metadata": {"ImageID": _image(1)},
        "Results": [{"Target": "/app", "Class": "secret", "Secrets": []}],
    }
    with pytest.raises(SystemExit, match="no package scan"):
        RM.build_certification(
            c["v2"],
            image,
            {"client": {"report": secrets_only, "path": "x", "sha256": "0"}},
        )
    leaked = _trivy(_image(1))
    leaked["Results"].append(
        {
            "Target": "/app/.env",
            "Class": "secret",
            "Secrets": [{"RuleID": "aws-key", "Severity": "CRITICAL"}],
        }
    )
    with pytest.raises(SystemExit, match="secret aws-key"):
        RM.build_certification(
            c["v2"], image, {"client": {"report": leaked, "path": "x", "sha256": "0"}}
        )


def test_a_failing_rerun_withdraws_an_earlier_green_receipt(repo, tmp_path):
    """Approval is this run's verdict: a manifest that verified green once and
    fails now cannot be promoted on the old receipt."""
    root, c = repo
    _git(root, "checkout", "-q", c["v2"])
    approved = tmp_path / "current.json"
    approved.write_text(json.dumps(_manifest(c["v1"], "FULL", _v1_full(c))))
    _approve(approved)
    state = {**{s: (1, c["v1"]) for s in BACKEND_SERVICES}, "client": (20, c["v2"])}
    target, _ = _release_files(tmp_path, "CLIENT_ONLY", c["v2"], state, approved)
    _approve(target)  # an earlier green run
    containers = {
        name: [(f"{name}-1", _image(img), rev)] for name, (img, rev) in state.items()
    }
    drifted = {**containers, "backend": [("backend-1", _image(99), c["v2"])]}
    env = {
        "DEPLOY_SCOPE": "CLIENT_ONLY",
        "RELEASE_MANIFEST": str(target),
        "APPROVED_MANIFEST": str(approved),
    }
    out = _run_section(root, tmp_path, env, drifted)
    assert "FAIL: backend" in out, out
    assert not Path(str(target) + RM.RECEIPT_SUFFIX).exists(), (
        "the old receipt survived a failing run"
    )


# --------------------------------------------------------------------------- #
# an undeclared document-worker canary fails BEFORE the deploy, not after it
# --------------------------------------------------------------------------- #
#
# A running canary with no manifest entry of its own is held to the
# document-worker image. FULL and BACKEND_ONLY replace that image and the guide
# never rebuilds or recreates the canary (no `image:` key: it is its own
# `<project>-document-worker-canary` image), so post-deploy verification could
# only fail - after production had already changed. `release_manifest.py
# preflight` answers before any restart: stop the canary, or declare it.

CANARY = "document-worker-canary"


def _preflight_state(release: str):
    state = {**{s: (10, release) for s in BACKEND_SERVICES}, "client": (11, release)}
    return state


@pytest.mark.parametrize("scope", ["FULL", "BACKEND_ONLY"])
def test_preflight_passes_a_backend_deploy_with_no_canary(scope):
    release = "a" * 40
    target = _manifest(release, scope, _preflight_state(release))
    findings = RM.preflight(scope, target, _running(_preflight_state("b" * 40)))
    assert findings and _failures(findings) == [], findings


@pytest.mark.parametrize("scope", ["FULL", "BACKEND_ONLY"])
def test_preflight_fails_a_backend_deploy_while_an_undeclared_canary_runs(scope):
    release = "a" * 40
    target = _manifest(release, scope, _preflight_state(release))
    running = _running({**_preflight_state("b" * 40), CANARY: (10, "b" * 40)})
    failures = _failures(RM.preflight(scope, target, running))
    assert len(failures) == 1, failures
    message = failures[0]
    assert message.startswith(CANARY), message
    # It says what to do: stop it, or declare it in the manifest.
    assert "stop" in message and "--image document-worker-canary=" in message, message


@pytest.mark.parametrize("scope", ["FULL", "BACKEND_ONLY"])
def test_preflight_passes_a_canary_the_manifest_declares(scope):
    release = "a" * 40
    target = _manifest(release, scope, {**_preflight_state(release), CANARY: (12, release)})
    running = _running({**_preflight_state("b" * 40), CANARY: (10, "b" * 40)})
    assert _failures(RM.preflight(scope, target, running)) == []


def test_preflight_leaves_client_only_to_the_existing_canary_rule():
    """CLIENT_ONLY does not touch the document-worker image, so a canary on it
    stays as the verifier already judges it (held to the approved worker)."""
    release = "a" * 40
    target = _manifest(release, "CLIENT_ONLY", _preflight_state(release))
    running = _running({**_preflight_state("b" * 40), CANARY: (10, "b" * 40)})
    assert _failures(RM.preflight("CLIENT_ONLY", target, running)) == []


@pytest.mark.parametrize(
    ("scope", "target"),
    [(None, "ok"), ("SOMETIMES", "ok"), ("FULL", None), ("FULL", "other_scope")],
    ids=["no_scope", "bad_scope", "no_manifest", "manifest_for_another_scope"],
)
def test_preflight_fails_what_it_cannot_establish(scope, target):
    release = "a" * 40
    manifest = _manifest(release, "FULL", _preflight_state(release))
    if target == "other_scope":
        manifest["scope"] = "CLIENT_ONLY"
    findings = RM.preflight(scope, manifest if target else None, _running(_preflight_state(release)))
    assert _failures(findings), findings


def test_preflight_cli_exits_non_zero_on_an_undeclared_canary(tmp_path):
    release = "a" * 40
    target = tmp_path / "target.json"
    running = tmp_path / "running.json"
    target.write_text(json.dumps(_manifest(release, "FULL", _preflight_state(release))))
    running.write_text(
        json.dumps(_running({**_preflight_state("b" * 40), CANARY: (10, "b" * 40)}))
    )
    args = ["preflight", "--scope", "FULL", "--target", str(target), "--running", str(running)]
    assert RM.main(args) == 1
    running.write_text(json.dumps(_running(_preflight_state("b" * 40))))
    assert RM.main(args) == 0


def test_post_deploy_still_holds_an_undeclared_canary_to_the_worker_image(repo):
    """The fail-fast is in front of the deploy; the verifier is not loosened."""
    root, c = repo
    _git(root, "checkout", "-q", c["v3"])
    state = {**{s: (10, c["v3"]) for s in BACKEND_SERVICES}, "client": (11, c["v3"])}
    target = _manifest(c["v3"], "FULL", state)
    stale_canary = _running({**state, CANARY: (99, c["v1"])})
    failures = _failures(_check(root, "FULL", target, None, stale_canary))
    assert any(m.startswith(CANARY) for m in failures), failures


def test_the_guide_runs_the_preflight_before_each_backend_restart():
    guide = (PROJECT / "docs" / "CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md").read_text(
        encoding="utf-8"
    )
    preflight = guide.find("scripts/release_preflight.sh")
    restart = guide.find("## 7. Restart the Updated Containers")
    assert preflight != -1, "the guide never runs the release preflight"
    assert restart != -1 and preflight < restart, "the preflight must run before any restart"


def test_the_preflight_script_asks_the_manifest_tool_and_fails_closed():
    text = (PROJECT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "release_manifest.py\" preflight" in text or "release_manifest.py preflight" in text
    assert "ps -q document-worker-canary" in text
    # Read-only: it never changes a container.
    for verb in (" up ", " stop ", " rm ", " restart ", " kill ", "--scale"):
        assert verb not in text.replace("# ", ""), verb
