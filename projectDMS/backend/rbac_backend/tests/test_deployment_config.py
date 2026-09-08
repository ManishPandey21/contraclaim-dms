"""Static guards for deployment-config contracts (2026-07 production audit).

These tests read the deployment files as text and pin the fixes for:

- H1: uvicorn must trust proxy headers, or request.client.host is the Apache
  gateway IP for every request — the per-IP login limiter collapses into one
  shared bucket (61st failed login platform-wide 429s everyone) and audit
  logs record the proxy instead of the client.
- The nginx edge must overwrite X-Forwarded-For with $remote_addr; appending
  ($proxy_add_x_forwarded_for) lets a client spoof the leftmost entry and
  choose its own rate-limit bucket / audit identity.
- CSP frame-src must not allow framing arbitrary https:/data: content; only
  self, blob: previews, and the S3 hosts presigned URLs point at.
- The gateway healthcheck must exercise Apache over HTTP, not just stat the
  health file on disk.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
NGINX_CONF = REPO_ROOT / "config" / "nginx-contraclaim.conf"
HTTPD_CONF = REPO_ROOT / "config" / "httpd.conf"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_MONGO_REPLICA = REPO_ROOT / "docker-compose.mongo-replicaset.yml"
PRODUCTION_BACKUP = REPO_ROOT / "scripts" / "production_backup.sh"
BACKUP_VOLUME = REPO_ROOT / "scripts" / "backup_volume.sh"
LEGACY_BACKUP = REPO_ROOT / "scripts" / "backup.sh"
CLIENT_DOCKERFILE = REPO_ROOT / "client" / "Dockerfile"
CLIENT_DOCKERIGNORE = REPO_ROOT / "client" / ".dockerignore"
PYTHON_SERVICE_DOCKERFILES = (
    BACKEND_DOCKERFILE,
    REPO_ROOT / "services" / "docling" / "Dockerfile",
    REPO_ROOT / "services" / "langgraph" / "Dockerfile",
    REPO_ROOT / "services" / "graphiti" / "Dockerfile",
)


def test_uvicorn_trusts_proxy_headers() -> None:
    cmd_lines = [
        line
        for line in BACKEND_DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("CMD")
    ]
    assert cmd_lines, f"no CMD line found in {BACKEND_DOCKERFILE}"
    cmd = cmd_lines[-1]
    assert "--proxy-headers" in cmd, (
        "uvicorn CMD must pass --proxy-headers; without it request.client.host "
        "is the gateway IP and per-IP login rate limiting shares one bucket "
        "for every user (audit H1)"
    )
    assert "--forwarded-allow-ips" in cmd, (
        "uvicorn CMD must pass --forwarded-allow-ips alongside --proxy-headers"
    )


def test_nginx_overwrites_x_forwarded_for() -> None:
    # Directives only: comments may (and do) mention the forbidden variable
    # while explaining why it is forbidden.
    text = "\n".join(
        line
        for line in NGINX_CONF.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    assert "$proxy_add_x_forwarded_for" not in text, (
        "nginx must set X-Forwarded-For from $remote_addr, not append with "
        "$proxy_add_x_forwarded_for: the edge is the first trusted hop and a "
        "client-supplied header must be discarded, or the client IP the "
        "backend resolves is attacker-chosen"
    )
    assert re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", text
    ), "nginx must set X-Forwarded-For to $remote_addr"


def test_csp_frame_src_is_scoped() -> None:
    text = HTTPD_CONF.read_text(encoding="utf-8")
    match = re.search(r"frame-src\s+([^;]+);", text)
    assert match, "httpd.conf CSP must declare frame-src explicitly"
    sources = match.group(1).split()
    assert "https:" not in sources, (
        "frame-src must not allow bare https: (any HTTPS origin could be "
        "framed inside the app); scope it to the S3 hosts previews use"
    )
    assert "data:" not in sources, (
        "frame-src must not allow data: URIs (nothing in the client frames "
        "data: content; it only enables phishing overlays)"
    )
    assert "'self'" in sources
    assert "blob:" in sources, "blob: is required for fetched PDF previews"


def test_gateway_healthcheck_probes_http() -> None:
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    # Find the gateway service's healthcheck test line.
    gateway_block = text.split("gateway:", 1)[1]
    test_lines = [
        line for line in gateway_block.splitlines() if "test:" in line
    ]
    assert test_lines, "gateway service must define a healthcheck test"
    probe = test_lines[0]
    assert "GET /health" in probe, (
        "gateway healthcheck must issue an HTTP request to /health so it "
        "proves Apache is serving, not merely that health.txt exists on disk"
    )


def test_falkordb_entrypoint_loads_graph_module() -> None:
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    falkor_block = text.split("\n  falkordb:", 1)[1].split("\n  redis:", 1)[0]

    assert "REDIS_ARGS:" in falkor_block, (
        "Redis options must be passed through REDIS_ARGS so the FalkorDB image "
        "entrypoint still loads the graph module"
    )
    assert "\n    command:" not in falkor_block, (
        "overriding the FalkorDB command starts plain Redis without falkordb.so"
    )
    assert "COMMAND INFO GRAPH.QUERY" in falkor_block, (
        "FalkorDB health must verify graph-command availability, not only PING"
    )


def test_falkordb_persists_inside_the_volume_that_gets_backed_up() -> None:
    """The graph must be written where the backup looks for it.

    The FalkorDB image's WORKDIR is /FalkorDB and Redis defaults ``dir`` to the
    working directory, so without an explicit ``--dir`` the RDB and the AOF are
    written into the container's writable layer. The backup then archives an
    empty volume, exits 0, and the graph does not survive the container being
    replaced. Proven end to end by scripts/falkordb_recovery_drill.sh.
    """
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    falkor_block = text.split("\n  falkordb:", 1)[1].split("\n  redis:", 1)[0]

    mount = re.search(r"- +falkordb_data:(\S+)", falkor_block)
    assert mount, "the falkordb service must mount the falkordb_data volume"
    mount_path = mount.group(1)

    assert re.search(rf"--dir +{re.escape(mount_path)}(\s|$)", falkor_block), (
        f"FalkorDB must be told to persist into {mount_path}, the volume "
        f"scripts/production_backup.sh archives; without --dir it writes to the "
        f"image WORKDIR inside the container and the backup captures nothing"
    )


#: What each stateful volume's backup call must declare. The Redis-family
#: volumes carry a semantic profile rather than a pattern list: a pattern list
#: cannot tell an `appendonlydir/` holding an AOF from an empty directory with
#: the same name, which is how the 89-byte FalkorDB archive verified clean.
REQUIRED_BACKUP_CONTRACTS = {
    "falkordb_data": "--profile redis-persistence",
    "redis_data": "--profile redis-persistence",
    "qdrant_data": "--any-of",
}


def test_production_backup_requires_real_persistence_files_in_each_state_archive() -> None:
    """`tar` exiting 0 is not a backup.

    Every archive that is supposed to carry recoverable state declares what it
    must contain, so a volume that holds nothing fails the backup loudly
    instead of producing a 89-byte archive that restores an empty database.
    """
    for script in (PRODUCTION_BACKUP, LEGACY_BACKUP):
        calls = [
            line
            for line in script.read_text(encoding="utf-8").splitlines()
            if line.startswith("backup_volume ")
        ]
        assert calls, f"{script.name} no longer backs up any volume"

        for volume_suffix, contract in REQUIRED_BACKUP_CONTRACTS.items():
            call = next(
                (line for line in calls if line.split('"')[1].endswith(volume_suffix)),
                None,
            )
            assert call, f"{script.name} no longer backs up {volume_suffix}"
            assert contract in call, (
                f"{script.name}'s {volume_suffix} backup must declare `{contract}`, or an "
                f"empty volume is archived as a successful backup"
            )


def test_no_backup_call_relies_on_bare_patterns_meaning_a_conjunction() -> None:
    """R-A8P F2: bare patterns are a disjunction, and the runbook read them as
    a conjunction.

    Every caller now says which it means. A bare pattern list is still accepted
    by the script for compatibility, and nothing in this repository uses one,
    so no reader has to guess again.
    """
    for script in (PRODUCTION_BACKUP, LEGACY_BACKUP):
        for line in script.read_text(encoding="utf-8").splitlines():
            if not line.startswith("backup_volume "):
                continue
            trailing = "".join(line.split('"')[4:]).strip()
            if not trailing:
                continue
            assert trailing.startswith(("--profile", "--any-of", "--require")), (
                f"{script.name} declares an archive contract without saying whether it is a "
                f"conjunction or a disjunction: {line}"
            )


def _working_bash() -> str | None:
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


BASH = _working_bash()


def _verify_archive(archive: Path, *patterns: str) -> subprocess.CompletedProcess[str]:
    # Run from the archive's directory and name it relatively: GNU tar reads a
    # leading "C:" as a remote host, so a Windows absolute path never reaches it.
    assert BASH is not None
    return subprocess.run(
        [BASH, str(BACKUP_VOLUME), "--verify", archive.name, *patterns],
        cwd=archive.parent,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_refuses_an_archive_without_the_required_entries(
    tmp_path: Path,
) -> None:
    """The exact shape the FalkorDB backup produced while reporting success."""
    empty = tmp_path / "empty.tar.gz"
    with tarfile.open(empty, "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("./"))

    result = _verify_archive(empty, "*dump.rdb", "*appendonlydir*")

    assert result.returncode != 0, (
        "an archive holding one empty directory entry must fail verification; "
        "this is the artifact the FalkorDB backup produced for months"
    )
    assert "none of" in result.stderr


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_accepts_an_archive_carrying_persisted_state(
    tmp_path: Path,
) -> None:
    # The payload lives in a subdirectory: under Git Bash the shell expands a
    # "*dump.rdb" argument against the working directory before the script
    # sees it, which would quietly turn the pattern into a literal name.
    source = tmp_path / "state"
    source.mkdir()
    payload = source / "dump.rdb"
    payload.write_bytes(b"REDIS0011")
    good = tmp_path / "good.tar.gz"
    with tarfile.open(good, "w:gz") as archive:
        archive.add(payload, arcname="./dump.rdb")

    result = _verify_archive(good, "*dump.rdb", "*appendonlydir*")

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(BASH is None, reason="a working bash is required to run the backup script")
def test_backup_verification_refuses_a_missing_or_unreadable_archive(tmp_path: Path) -> None:
    missing = _verify_archive(tmp_path / "absent.tar.gz", "*dump.rdb")
    assert missing.returncode != 0

    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(b"not a gzip stream")
    assert _verify_archive(truncated, "*dump.rdb").returncode != 0


def test_volume_restore_does_not_depend_on_a_parseable_env_file() -> None:
    """A restore runs during an incident, on whatever the host's .env holds.

    The script sourced `.env` and used nothing from it, so one malformed line
    ("XAI_API_KEY = value") aborted the restore with `command not found` under
    `set -e`.
    """
    text = (REPO_ROOT / "scripts" / "production_restore_volumes.sh").read_text(encoding="utf-8")

    assert "source " not in text and "ENV_FILE" not in text, (
        "production_restore_volumes.sh must not source .env: it reads nothing "
        "from it, and a malformed line there aborts the restore"
    )


def _client_runtime_stage() -> str:
    """The last FROM block of client/Dockerfile — what actually ships."""
    text = CLIENT_DOCKERFILE.read_text(encoding="utf-8")
    return "FROM" + text.split("\nFROM")[-1]


def test_the_client_runtime_stage_carries_no_package_manager() -> None:
    """The image that serves the SPA runs `node scripts/serve-dist.mjs`.

    `node:*-slim` bundles the npm CLI, corepack and yarn. Nothing at runtime
    invokes any of them, and their vendored dependency trees under
    /usr/local/lib/node_modules were the source of every Node-side finding in
    the image scan — 1 CRITICAL (tar) and 19 HIGH — none of which came from
    this application's own dependencies. Serving static files does not need a
    package manager.
    """
    runtime = _client_runtime_stage()

    for removed in (
        "/usr/local/lib/node_modules/npm",
        "/usr/local/lib/node_modules/corepack",
        "/usr/local/bin/npm",
        "/usr/local/bin/npx",
        "/usr/local/bin/corepack",
        "/usr/local/bin/yarn",
    ):
        assert removed in runtime, (
            f"the client runtime stage must remove {removed}; a package manager "
            f"in a static-asset image is attack surface with no runtime purpose"
        )


def test_the_client_runtime_stage_copies_only_built_output() -> None:
    """No builder artefact may cross into the runtime stage.

    The scan showed the COPY boundary was already correct; this keeps it that
    way, because the cheapest way to reintroduce the whole builder tree is one
    careless `COPY --from=builder /app .`.
    """
    runtime = _client_runtime_stage()
    copies = [line.strip() for line in runtime.splitlines() if line.strip().startswith("COPY")]
    assert copies, "the runtime stage must copy the built client in"

    allowed_sources = {"/app/dist", "/app/scripts/serve-dist.mjs"}
    for copy in copies:
        parts = copy.split()
        assert parts[1].startswith("--from="), f"runtime COPY must come from a build stage: {copy}"
        source = parts[2]
        assert source in allowed_sources, (
            f"the client runtime stage may only copy {sorted(allowed_sources)}; "
            f"{source!r} would carry builder content (node_modules, sources, "
            f"package metadata) into the shipped image"
        )


def _run_instructions(dockerfile: Path) -> list[str]:
    """The RUN instructions of a Dockerfile, one string per layer it builds.

    Line continuations are folded, so an instruction split across several
    source lines comes back as the single layer Docker builds from it.
    Comments and blank lines are dropped.
    """
    continuation = chr(92) + chr(10)
    folded = dockerfile.read_text(encoding="utf-8").replace(continuation, " ")
    return [
        " ".join(stripped.split())
        for stripped in (line.strip() for line in folded.splitlines())
        if stripped.startswith("RUN ")
    ]


@pytest.mark.parametrize(
    "dockerfile", PYTHON_SERVICE_DOCKERFILES, ids=lambda p: p.parent.name
)
def test_python_service_images_do_not_ship_pip(dockerfile: Path) -> None:
    """Every Python image runs uvicorn; none of them installs anything at runtime.

    pip nevertheless stays in the image, and Trivy reads its vendored manifest
    (`pip/_vendor/vendor.txt`) as a package list: `msgpack==1.1.2` and
    `setuptools==70.3.0` were reported as HIGH findings in all four images even
    though msgpack is not importable in any of them and the installed setuptools
    is 84.0.0. Upgrading pip does not help - pip 26.2.1 still pins those
    versions in its vendored manifest. Removing the tool removes the finding.
    """
    runs = _run_instructions(dockerfile)

    assert any("pip install" in run for run in runs), (
        f"{dockerfile} no longer installs dependencies"
    )
    assert any("pip uninstall -y pip" in run for run in runs), (
        f"{dockerfile} must remove pip after installing dependencies; leaving it "
        f"ships a package manager, and its vendored manifest, in an image that "
        f"never installs a package"
    )


@pytest.mark.parametrize(
    "dockerfile", PYTHON_SERVICE_DOCKERFILES, ids=lambda p: p.parent.name
)
def test_pip_is_removed_in_the_same_layer_that_installs_it(dockerfile: Path) -> None:
    """Removing pip in a later layer does not remove it from the image.

    An image is scanned layer by layer, and a whiteout in an upper layer does
    not erase what a lower layer holds. While these Dockerfiles carried a
    standalone `RUN pip uninstall -y pip`, the dependency-install layer beneath
    it still shipped `pip/_vendor/vendor.txt`, and the exact CI Trivy policy
    (`--ignore-unfixed --severity CRITICAL,HIGH --exit-code 1`) reported
    `msgpack 1.1.2` and `setuptools 70.3.0` as fixable HIGH findings against the
    distributable backend image. That was measured on the release artefact, not
    theorised: the runtime filesystem never contained either package, and the
    scan failed anyway, because the lower layer did.

    Installing and removing in one instruction means no layer ever contains the
    manifest, and that instruction's opaque whiteout over `site-packages/pip`
    also masks the copy inherited from `python:3.12-slim`, which pins
    `setuptools==70.3.0` too.

    So the property is *same layer*, not *present somewhere in the file*: no
    instruction may remove pip without installing in it, and the last
    instruction that installs with pip must also be the one that removes it.
    """
    runs = _run_instructions(dockerfile)
    installs = [run for run in runs if "pip install" in run]
    removals = [run for run in runs if "pip uninstall" in run]

    assert installs, f"{dockerfile} no longer installs dependencies"
    assert removals, f"{dockerfile} no longer removes pip"

    for removal in removals:
        assert "pip install" in removal, (
            f"{dockerfile} removes pip in an instruction of its own: {removal!r}. "
            f"That builds a layer in which pip, and its vendored manifest, still "
            f"exist; the image ships that layer and the CI scanner reads it. "
            f"Remove pip in the same RUN that installs the dependencies."
        )

    assert installs[-1] in removals, (
        f"{dockerfile} ends with a layer that still contains pip: "
        f"{installs[-1]!r}. The last pip install must also remove pip."
    )


def test_the_client_build_context_excludes_developer_and_test_output() -> None:
    """Everything here is either large, secret, or irrelevant to the build."""
    ignored = {
        line.strip()
        for line in CLIENT_DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    for pattern in ("node_modules/", "dist/", ".git", "coverage/", ".env*"):
        assert pattern in ignored, f"client/.dockerignore must exclude {pattern}"

    for pattern in ("test-results/", "playwright-report/", ".claude/"):
        assert pattern in ignored, (
            f"client/.dockerignore must exclude {pattern}: it is uploaded to the "
            f"Docker daemon on every build and belongs in no image"
        )


def test_mongo_replica_services_have_restore_safe_limits_and_replication_health() -> None:
    text = COMPOSE_MONGO_REPLICA.read_text(encoding="utf-8")

    assert "soft: 64000" in text and "hard: 64000" in text, (
        "MongoDB restore/index builds exceed the container default of 1024 file "
        "descriptors; every replica member must inherit the restore-safe nofile limit"
    )
    assert text.count("ulimits: *mongo-ulimits") == 3
    assert "rs.status()" in text and "s.myState === 1" in text and "s.myState === 2" in text, (
        "MongoDB health must require a PRIMARY or SECONDARY replica state; ping-only "
        "health reports a recovering or isolated member as healthy"
    )
    mongo_init_block = text.split("\n  mongo-init:", 1)[1]
    assert mongo_init_block.count("condition: service_started") == 3, (
        "replica initialization must start before replication-aware health can pass"
    )


REQUIREMENTS_FILES = (
    REPO_ROOT / "backend" / "rbac_backend" / "requirements.txt",
    REPO_ROOT / "backend" / "requirements.txt",
    REPO_ROOT / "requirements.txt",
)

# Only backend/rbac_backend/requirements.txt reaches the image, CI and
# pip-audit. The other two are installed by hand and by scripts/deploy_*.sh,
# and they drifted: PYSEC-2026-3552 was fixed in rbac_backend by pinning
# cryptography 50.0.0 while both other files still pinned 44.0.2, which the
# same advisory covers. A pin that only one file carries is not a fix, and
# nothing scans the other two.
SECURITY_PINNED_PACKAGES = ("cryptography", "PyJWT")


def _pinned_version(text: str, package: str) -> str | None:
    match = re.search(
        rf"^{re.escape(package)}(?:\[[^\]]+\])?==(?P<version>[^\s#]+)\s*$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    return match.group("version") if match else None


@pytest.mark.parametrize("package", SECURITY_PINNED_PACKAGES)
def test_security_pinned_packages_agree_across_requirements_files(package: str) -> None:
    pinned = {
        path.name if path.parent == REPO_ROOT else str(path.relative_to(REPO_ROOT)): version
        for path in REQUIREMENTS_FILES
        for version in [_pinned_version(path.read_text(encoding="utf-8"), package)]
        if version is not None
    }

    assert len(pinned) == len(REQUIREMENTS_FILES), (
        f"{package} must stay pinned in every requirements file; found {pinned}"
    )
    assert len(set(pinned.values())) == 1, (
        f"{package} is pinned at different versions across requirements files: "
        f"{pinned}. An advisory fixed in one file leaves the others installable "
        f"at the vulnerable version, and only backend/rbac_backend is scanned"
    )
