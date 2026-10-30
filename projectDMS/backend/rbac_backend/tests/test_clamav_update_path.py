"""ClamAV must be able to update its signatures, keep them, and gain nothing else.

R-A8Y production diagnosis (read-only, 2026-09-14): `contraclaim-clamav-1` was
attached only to `service-net`, which is `internal: true`. Inside the container
there was no default route (`ip route get 1.1.1.1` -> Network unreachable) and
Docker's embedded resolver answered SERVFAIL for `database.clamav.net`. freshclam
was running and failed every daily check since the container was created -
68 runs, 0 updates - so clamd served the database baked into the image on
2026-07-05. Nothing mounted `/var/lib/clamav`, so even a successful update would
have been discarded the next time the container was recreated.

The release design, asserted here against the compose file as data:

* clamav joins `egress-net` (the existing non-internal network) in addition to
  `service-net`, and nothing else - not `data-net`, not `edge-net`;
* `service-net` and `data-net` stay internal;
* every peer clamav gains on `egress-net` is already its peer on `service-net`,
  so the only new reachability is outbound internet for the update;
* `/var/lib/clamav` lives on a dedicated named volume no other service mounts;
* no privilege, host namespace, published port, Docker socket or bind mount.

Each rule has a mutation control that breaks exactly that rule and requires the
guard to name it.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_STAGING = REPO_ROOT / "docker-compose.staging.yml"
PRODUCTION_BACKUP = REPO_ROOT / "scripts" / "production_backup.sh"
HEALTH_ROUTER = REPO_ROOT / "backend" / "rbac_backend" / "routers" / "health.py"

CLAMAV = "clamav"
DB_PATH = "/var/lib/clamav"
DB_VOLUME = "clamav_db"
ALLOWED_NETWORKS = {"service-net", "egress-net"}
MUST_STAY_INTERNAL = ("service-net", "data-net")
#: Signature checks per day. The image default is 1: one missed check (mirror
#: outage, a restart at the wrong moment) leaves a daily database up to ~48 h
#: old, which is the FAIL threshold itself. 12 is freshclam's documented default
#: and stays far inside the mirror's fair-use guidance.
MIN_CHECKS_PER_DAY, MAX_CHECKS_PER_DAY = 2, 24
#: Measured in the R-A8Y drill (1.4.6, daily 28122, ConcurrentDatabaseReload on):
#: clamd holds ~1 GiB steady and ~2x while a reload builds the new engine beside
#: the old one. Under the previous 2 GiB limit that left ~5% headroom - and
#: freshclam now actually updates, so the reload path runs every day and the
#: database only grows. 3 GiB keeps ~1 GiB spare.
RELOAD_PEAK_MIB = 1952
MIN_MEMORY_MIB = 3072


def _load(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _networks(service: Dict[str, Any]) -> List[str]:
    nets = service.get("networks") or []
    return list(nets.keys()) if isinstance(nets, dict) else list(nets)


def _volume_specs(service: Dict[str, Any]) -> List[str]:
    specs = []
    for entry in service.get("volumes") or []:
        if isinstance(entry, dict):
            specs.append(f"{entry.get('source', '')}:{entry.get('target', '')}")
        else:
            specs.append(str(entry))
    return specs


def clamav_update_path_violations(compose: Dict[str, Any]) -> List[str]:
    """Every way the ClamAV service definition departs from the R-A8Y design."""
    violations: List[str] = []
    services = compose.get("services") or {}
    clamav = services.get(CLAMAV)
    if clamav is None:
        return ["no clamav service"]
    top_networks = compose.get("networks") or {}
    top_volumes = compose.get("volumes") or {}

    nets = set(_networks(clamav))
    external_capable = {n for n in nets if not (top_networks.get(n) or {}).get("internal", False)}
    if not external_capable:
        violations.append("egress: clamav is attached to no non-internal network, so freshclam cannot resolve or reach the mirror")
    if nets - ALLOWED_NETWORKS:
        violations.append(f"exposure: clamav joins networks beyond {sorted(ALLOWED_NETWORKS)}: {sorted(nets - ALLOWED_NETWORKS)}")
    if "service-net" not in nets:
        violations.append("reachability: clamav left service-net, where the backend reaches it")

    for name in MUST_STAY_INTERNAL:
        if not (top_networks.get(name) or {}).get("internal", False):
            violations.append(f"internal: {name} is no longer internal")

    service_peers = {n for n, s in services.items() if "service-net" in _networks(s or {})}
    for net in nets - {"service-net"}:
        new_peers = {n for n, s in services.items() if n != CLAMAV and net in _networks(s or {})} - service_peers
        if new_peers:
            violations.append(f"exposure: clamav gains new peers on {net}: {sorted(new_peers)}")

    specs = _volume_specs(clamav)
    if f"{DB_VOLUME}:{DB_PATH}" not in specs:
        violations.append(f"persistence: {DB_PATH} is not mounted from the {DB_VOLUME} named volume")
    if DB_VOLUME not in top_volumes:
        violations.append(f"persistence: top-level volume {DB_VOLUME} is not declared")
    elif (top_volumes.get(DB_VOLUME) or {}).get("external"):
        violations.append(f"persistence: {DB_VOLUME} must be project-owned, not external")
    for spec in specs:
        source = spec.split(":", 1)[0]
        if spec != f"{DB_VOLUME}:{DB_PATH}":
            violations.append(f"mounts: clamav mounts something besides its database volume: {spec}")
        if source.startswith(("/", ".", "~", "$")) or "docker.sock" in spec:
            violations.append(f"mounts: clamav bind-mounts a host path: {spec}")
    for other, definition in services.items():
        if other != CLAMAV and any(s.split(":", 1)[0] == DB_VOLUME for s in _volume_specs(definition or {})):
            violations.append(f"sharing: {other} mounts {DB_VOLUME}")

    for key in ("privileged",):
        if clamav.get(key):
            violations.append(f"privilege: clamav sets {key}")
    for key in ("network_mode", "pid", "ipc", "userns_mode"):
        if key in clamav:
            violations.append(f"namespace: clamav sets {key}={clamav[key]}")
    for key in ("ports", "cap_add", "devices"):
        if clamav.get(key):
            violations.append(f"privilege: clamav declares {key}")

    limit = str((((clamav.get("deploy") or {}).get("resources") or {}).get("limits") or {}).get("memory", ""))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([gGmM])[bB]?", limit)
    limit_mib = 0.0 if not match else float(match.group(1)) * (1024 if match.group(2).lower() == "g" else 1)
    if limit_mib < MIN_MEMORY_MIB:
        violations.append(
            f"memory: limit {limit or 'unset'} leaves no headroom for a concurrent database reload "
            f"(R-A8Y drill peak {RELOAD_PEAK_MIB} MiB); at least {MIN_MEMORY_MIB} MiB is required"
        )

    healthcheck = clamav.get("healthcheck") or {}
    probe = " ".join(str(part) for part in healthcheck.get("test") or [])
    if "--ping" not in probe:
        violations.append("health: the healthcheck no longer requires clamd to answer PING")
    if not all(token in probe for token in ("zVERSION", "daily.cld", "--reload")):
        violations.append(
            "self-heal: the healthcheck no longer reloads clamd when the daily database on disk is newer "
            "than the loaded one, so the /init startup race leaves stale signatures loaded"
        )
    if "-mmin +10" not in probe or "clamd-reload-requested" not in probe:
        violations.append(
            "reload-storm: the healthcheck must request a reload once and turn unhealthy if the newer database "
            "is still not loaded 10 minutes later, not re-request every probe"
        )
    if "*[!0-9]*) echo" not in probe:
        violations.append("visible: an unreadable clamd VERSION must make the container unhealthy, not skip the reload")

    environment = clamav.get("environment") or {}
    checks = environment.get("FRESHCLAM_CHECKS") if isinstance(environment, dict) else None
    try:
        checks_per_day = int(str(checks))
    except ValueError:
        checks_per_day = 0
    if not MIN_CHECKS_PER_DAY <= checks_per_day <= MAX_CHECKS_PER_DAY:
        violations.append(
            f"cadence: FRESHCLAM_CHECKS={checks!r}; one check a day can leave the database at the FAIL threshold"
        )
    return violations


@pytest.fixture(scope="module")
def production() -> Dict[str, Any]:
    return _load(COMPOSE_PROD)


def test_the_release_compose_satisfies_the_update_path_design(production: Dict[str, Any]) -> None:
    assert clamav_update_path_violations(production) == []


def _mutate(production: Dict[str, Any], change) -> List[str]:
    mutated = copy.deepcopy(production)
    change(mutated)
    return clamav_update_path_violations(mutated)


def _set(path: List[str], value: Any):
    def change(doc: Dict[str, Any]) -> None:
        node = doc
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    return change


MUTATIONS = {
    "egress-net removed": (lambda d: d["services"][CLAMAV].__setitem__("networks", ["service-net"]), "egress:"),
    "database volume removed": (lambda d: d["services"][CLAMAV].pop("volumes"), "persistence:"),
    "top-level volume removed": (lambda d: d["volumes"].pop(DB_VOLUME), "persistence:"),
    "volume shared with backend": (
        lambda d: d["services"]["backend"]["volumes"].append(f"{DB_VOLUME}:/app/clamav"),
        "sharing:",
    ),
    "service-net made routable": (_set(["networks", "service-net", "internal"], False), "internal:"),
    "data-net joined": (lambda d: d["services"][CLAMAV]["networks"].append("data-net"), "exposure:"),
    "edge-net joined": (lambda d: d["services"][CLAMAV]["networks"].append("edge-net"), "exposure:"),
    "privileged": (_set(["services", CLAMAV, "privileged"], True), "privilege:"),
    "host networking": (_set(["services", CLAMAV, "network_mode"], "host"), "namespace:"),
    "docker socket": (
        lambda d: d["services"][CLAMAV]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"),
        "mounts:",
    ),
    "published port": (_set(["services", CLAMAV, "ports"], ["3310:3310"]), "privilege:"),
    "egress-net gains a data-tier peer": (
        lambda d: d["services"]["qdrant"]["networks"].append("egress-net"),
        "exposure:",
    ),
    "one check a day": (_set(["services", CLAMAV, "environment", "FRESHCLAM_CHECKS"], "1"), "cadence:"),
    "reload requested every probe": (
        lambda d: d["services"][CLAMAV]["healthcheck"].__setitem__(
            "test",
            [
                "CMD-SHELL",
                d["services"][CLAMAV]["healthcheck"]["test"][1].replace("-mmin +10", "-mmin +0").replace(
                    "clamd-reload-requested", "x"
                ),
            ],
        ),
        "reload-storm:",
    ),
    "unreadable VERSION tolerated": (
        lambda d: d["services"][CLAMAV]["healthcheck"].__setitem__(
            "test",
            ["CMD-SHELL", d["services"][CLAMAV]["healthcheck"]["test"][1].replace("*[!0-9]*) echo", "*[!0-9]*) true")],
        ),
        "visible:",
    ),
    "memory back to 2g": (_set(["services", CLAMAV, "deploy", "resources", "limits", "memory"], "2g"), "memory:"),
    "healthcheck without the stale-load reload": (
        _set(["services", CLAMAV, "healthcheck", "test"], ["CMD-SHELL", "clamdscan --ping=1 || exit 1"]),
        "self-heal:",
    ),
}


@pytest.mark.parametrize("label", sorted(MUTATIONS))
def test_each_mutation_is_named_by_the_guard(production: Dict[str, Any], label: str) -> None:
    change, expected = MUTATIONS[label]
    violations = _mutate(production, change)
    assert any(v.startswith(expected) for v in violations), f"{label}: guard reported {violations}"


def test_the_staging_override_does_not_redefine_clamav() -> None:
    """Staging evidence about ClamAV is evidence about production only if the
    service definition is production's."""
    staging = _load(COMPOSE_STAGING)
    assert set((staging["services"].get(CLAMAV) or {})) <= {"labels"}


def test_the_signature_cache_is_not_a_backup_obligation() -> None:
    """The database is re-downloadable and changes daily. Archiving it would add
    ~110 MB of churn per backup and a restore would put OLD signatures back."""
    assert DB_VOLUME not in PRODUCTION_BACKUP.read_text(encoding="utf-8")


def test_liveness_depends_on_nothing_external() -> None:
    """A mirror outage must never take the application out of rotation."""
    tree = ast.parse(HEALTH_ROUTER.read_text(encoding="utf-8"))
    liveness = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "liveness")
    assert not any(isinstance(n, ast.Await) for n in ast.walk(liveness))
