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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
NGINX_CONF = REPO_ROOT / "config" / "nginx-contraclaim.conf"
HTTPD_CONF = REPO_ROOT / "config" / "httpd.conf"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_MONGO_REPLICA = REPO_ROOT / "docker-compose.mongo-replicaset.yml"


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
