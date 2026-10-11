"""Static guards for the staging deployment definition.

R-A6 stopped staging provisioning on this host and left two configuration
findings behind. Both are closed here, and both are the kind that fail silently:

* the three-member replica set bounds nothing, so each mongod sizes its
  WiredTiger cache at half of visible RAM minus 1 GB **against the whole VM**.
  Three members on a 16 GB container budget intend ~22.5 GB of cache between
  them. Nothing errors; the members simply thrash, and it looks like an
  application problem;
* a staging stack that reused a `contract-ai_*` volume, or published a data-tier
  port, or relaxed the application's own hardening, would still come up green
  and would then be measured as if it were production-shaped.

The rules are asserted against the compose files as data rather than as text,
and each one is paired with the production behaviour it must NOT change: a
staging fix that silently re-tunes production is not a staging fix.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_MONGO_REPLICA = REPO_ROOT / "docker-compose.mongo-replicaset.yml"
COMPOSE_STAGING = REPO_ROOT / "docker-compose.staging.yml"
ENV_STAGING_TEMPLATE = REPO_ROOT / ".env.staging.example"

REPLICA_MEMBERS = ("mongo1", "mongo2", "mongo3")

#: The isolated namespace. Docker prefixes every network and volume with the
#: project name, so pinning this one string is what produces
#: `contraclaim-stg_data-net`, `contraclaim-stg_mongo1_data` and the rest.
STAGING_PROJECT_NAME = "contraclaim-stg"

#: The development project. Nothing in the staging definition may name it: an
#: `external: true` volume called `contract-ai_falkordb_data` would attach the
#: staging stack to the instance holding the business graphs.
DEV_PROJECT_PREFIX = "contract-ai"

#: Services that appear in CI builds but in no production compose file. Staging
#: mirrors production, so it must not deploy them.
NOT_IN_PRODUCTION = ("langgraph", "docling", "graphiti")


def _load(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _directives(path: Path) -> str:
    """The file without its comment lines.

    Every rule below is about what the file DECLARES. A comment explaining why
    a key is deliberately absent must not read as that key being present -
    which is exactly what the first version of the FalkorDB guard did to
    itself.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


@pytest.fixture(scope="module")
def replica() -> Dict[str, Any]:
    return _load(COMPOSE_MONGO_REPLICA)


@pytest.fixture(scope="module")
def staging() -> Dict[str, Any]:
    return _load(COMPOSE_STAGING)


@pytest.fixture(scope="module")
def production() -> Dict[str, Any]:
    return _load(COMPOSE_PROD)


def _command_text(service: Dict[str, Any]) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


# --------------------------------------------------------------------------- #
# The WiredTiger cache policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("member", REPLICA_MEMBERS)
def test_every_replica_member_declares_the_cache_policy(replica: Dict[str, Any], member: str) -> None:
    """All three, or the unbounded one takes the whole budget for itself.

    Two bounded members and one unbounded member is not two thirds of a fix: the
    unbounded member still sizes itself against the entire host, and it is the
    one that will be PRIMARY often enough to matter.
    """
    service = replica["services"][member]
    command = _command_text(service)
    assert "--wiredTigerCacheSizeGB" in command, (
        f"{member} does not bound its WiredTiger cache, so it will size the cache "
        "at half of the whole host's RAM regardless of what the other members do"
    )
    assert "MONGO_WIREDTIGER_CACHE_GB" in command, (
        f"{member}'s cache bound is hard-coded rather than configurable; the same "
        "mechanism has to be usable on hosts of different sizes"
    )
    environment = service.get("environment") or {}
    assert "MONGO_WIREDTIGER_CACHE_GB" in environment, (
        f"{member} interpolates the cache variable in its command but never "
        "receives it, so the flag would silently expand to nothing"
    )


def test_the_cache_bound_defaults_to_todays_production_behaviour(replica: Dict[str, Any]) -> None:
    """Unset means unbounded, argument for argument.

    Production runs this file. Giving the variable a non-empty default would
    re-tune a live replica set as a side effect of a staging fix - a change that
    belongs to a production decision, not to this one.
    """
    for member in REPLICA_MEMBERS:
        environment = replica["services"][member]["environment"]
        assert environment["MONGO_WIREDTIGER_CACHE_GB"] == "${MONGO_WIREDTIGER_CACHE_GB:-}", (
            "the cache variable has acquired a default; production would then "
            "adopt a cache bound nobody approved"
        )
        # `:+` appends the flag only when the value is non-empty, so an unset
        # variable yields exactly `mongod --replSet rs0 --bind_ip_all`.
        assert "${MONGO_WIREDTIGER_CACHE_GB:+" in _command_text(replica["services"][member]).replace("$$", "$")


def test_the_replica_set_name_is_configurable_and_defaults_to_rs0(replica: Dict[str, Any]) -> None:
    """And it is NOT the application's own MONGODB_REPLICA_SET.

    That variable is supplied by the operator for the backend's connection
    string. Feeding it to mongod would rename a live replica set on the day the
    two disagree, which is an outage rather than a configuration error.
    """
    for member in REPLICA_MEMBERS:
        command = _command_text(replica["services"][member])
        assert "MONGO_REPLICA_SET_NAME" in command
        assert ":-rs0}" in command, f"{member} no longer defaults to the historical rs0"
        environment = replica["services"][member]["environment"]
        assert environment["MONGO_REPLICA_SET_NAME"] == "${MONGO_REPLICA_SET_NAME:-rs0}"
        assert "MONGODB_REPLICA_SET" not in environment, (
            f"{member} takes its replica-set name from the application's variable; "
            "a disagreement between the two renames a live set"
        )

    init_env = replica["services"]["mongo-init"].get("environment") or {}
    assert init_env.get("MONGODB_REPLICA_SET") == "${MONGO_REPLICA_SET_NAME:-rs0}", (
        "the initiator must name the same set the members were started under, or "
        "rs.initiate writes a config no member accepts"
    )


def test_the_restore_safe_limits_survived_the_command_change(replica: Dict[str, Any]) -> None:
    """The nofile ulimit and the replication-aware health check are older fixes.

    Rewriting `command:` is exactly the kind of edit that drops a sibling key.
    """
    for member in REPLICA_MEMBERS:
        service = replica["services"][member]
        assert service["ulimits"]["nofile"] == {"soft": 64000, "hard": 64000}
        assert "s.myState === 1" in " ".join(service["healthcheck"]["test"])


# --------------------------------------------------------------------------- #
# The staging override
# --------------------------------------------------------------------------- #


def test_the_staging_override_pins_the_isolated_project_name(staging: Dict[str, Any]) -> None:
    assert staging.get("name") == STAGING_PROJECT_NAME, (
        "without the project name pinned in the file, the namespace depends on "
        "COMPOSE_PROJECT_NAME being exported correctly every single time"
    )


@pytest.mark.parametrize("member", REPLICA_MEMBERS)
def test_the_staging_override_bounds_each_member(staging: Dict[str, Any], member: str) -> None:
    service = staging["services"][member]
    environment = service["environment"]
    assert environment["MONGO_REPLICA_SET_NAME"] == "rsstg", (
        "staging must not use rs0: a mis-set DATABASE_URL should fail to connect, "
        "not quietly reach a production-shaped replica set"
    )
    cache = environment["MONGO_WIREDTIGER_CACHE_GB"]
    assert cache and cache != "${MONGO_WIREDTIGER_CACHE_GB:-}", f"{member} inherits the unbounded default"
    assert re.search(r":-\s*([0-9.]+)\s*}", cache), f"{member}'s cache bound has no concrete default: {cache!r}"
    assert float(re.search(r":-\s*([0-9.]+)\s*}", cache).group(1)) <= 2, (
        "three members at more than 2 GB of cache each would leave nothing for "
        "ClamAV's 3 GB limit and FalkorDB's 2 GB maxmemory on a 16 GB budget"
    )
    limits = service["deploy"]["resources"]["limits"]["memory"]
    assert limits, f"{member} declares no hard memory ceiling behind the cache bound"


def test_the_staging_initiator_agrees_with_the_members(staging: Dict[str, Any]) -> None:
    assert staging["services"]["mongo-init"]["environment"]["MONGODB_REPLICA_SET"] == "rsstg"


def test_the_staging_override_touches_no_development_resource(staging: Dict[str, Any]) -> None:
    """The isolation guard. Mutation target: point it at a `contract-ai_*` volume.

    An `external: true` volume is how a staging stack acquires production or
    development data by name, and it is a one-line edit.
    """
    assert DEV_PROJECT_PREFIX not in _directives(COMPOSE_STAGING), (
        "the staging override names the development project; a staging stack that "
        "attaches a contract-ai_* volume writes into the instance holding the "
        "business graphs"
    )
    for section in ("volumes", "networks"):
        declared = staging.get(section) or {}
        for name, spec in declared.items():
            assert not (spec or {}).get("external"), (
                f"staging {section[:-1]} {name!r} is external, so it is attached by "
                "name to something this stack did not create"
            )


def test_the_staging_override_publishes_no_port(staging: Dict[str, Any]) -> None:
    """Ports are APPENDED across compose files, not replaced.

    Redeclaring the gateway's `ports` here would publish 8080 *and* the staging
    port. The host binding is therefore set through `HTTP_PORT`, which
    production already parameterises.
    """
    offenders = {name: svc["ports"] for name, svc in staging["services"].items() if svc.get("ports")}
    assert not offenders, (
        f"the staging override publishes ports {offenders}; compose appends port "
        "lists across files, so this adds to production's binding instead of "
        "replacing it. Use HTTP_PORT."
    )


def test_the_staging_override_declares_no_service_production_does_not_have(
    staging: Dict[str, Any], production: Dict[str, Any], replica: Dict[str, Any]
) -> None:
    """Staging must not fork the architecture, in either direction."""
    available = set(production["services"]) | set(replica["services"])
    unknown = sorted(set(staging["services"]) - available)
    assert not unknown, f"the staging override invents services production does not define: {unknown}"

    declarations = _directives(COMPOSE_STAGING).lower()
    for absent in NOT_IN_PRODUCTION:
        assert absent not in declarations, (
            f"{absent} is not in any production compose file; CI building an image "
            "is not a reason to deploy it to staging"
        )


def test_the_staging_override_does_not_relax_the_application_hardening(staging: Dict[str, Any]) -> None:
    """`ENVIRONMENT` gates fail-closed behaviour; staging must stay production-shaped.

    Entitlement checks, antivirus enforcement and the startup config gate are all
    keyed on `ENVIRONMENT == production`. A staging stack labelled `staging`
    would certify a weaker application than the one that ships.
    """
    for name, service in staging["services"].items():
        environment = service.get("environment") or {}
        assert "ENVIRONMENT" not in environment, (
            f"{name} overrides ENVIRONMENT in staging; production's own hardening "
            "is keyed on that value"
        )
        for relaxed in ("ALLOW_DEV_HEADERS", "RBAC_ENTITLEMENT_FAIL_OPEN", "CLAMAV_FAIL_OPEN"):
            assert relaxed not in environment, f"{name} relaxes {relaxed} for staging"


def test_the_staging_override_leaves_falkordb_persistence_alone(staging: Dict[str, Any]) -> None:
    """`--dir /data` is load-bearing for the Gate 8 restore drill.

    Without it FalkorDB writes its RDB and AOF to the container layer, the backup
    archives an empty `/data`, and every run reports success.
    """
    assert "REDIS_ARGS" not in _directives(COMPOSE_STAGING), (
        "the staging override redeclares FalkorDB's REDIS_ARGS; the production "
        "value carries --dir /data and the backup silently archives nothing without it"
    )
    prod_args = _load(COMPOSE_PROD)["services"]["falkordb"]["environment"]["REDIS_ARGS"]
    assert "--dir /data" in prod_args


# --------------------------------------------------------------------------- #
# The staging environment template
# --------------------------------------------------------------------------- #


def test_the_staging_template_exists_and_carries_no_real_value() -> None:
    """It has to survive a clone, so it has to be safe to commit."""
    assert ENV_STAGING_TEMPLATE.is_file()
    text = ENV_STAGING_TEMPLATE.read_text(encoding="utf-8")
    assert "sk-" not in text, "the staging template appears to carry a real OpenAI key"
    assert not re.search(r"^AKIA[0-9A-Z]{16}", text, re.MULTILINE), "a real AWS access key id"
    assert "contraclaim.com" not in text, (
        "the staging template names the production host; staging must never reach it"
    )


def test_the_staging_template_isolates_every_named_resource() -> None:
    text = ENV_STAGING_TEMPLATE.read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values.get("MONGO_REPLICA_SET_NAME") == "rsstg"
    assert values.get("MONGODB_REPLICA_SET") == "rsstg"
    assert values.get("MONGODB_DATABASE") == "contraclaim_staging"
    assert values.get("FALKORDB_GRAPH_NAME") == "contraclaim_staging", (
        "the staging application graph must not be `contraclaim`, which is the "
        "protected business graph"
    )
    assert values.get("COMPOSE_PROJECT_NAME") == STAGING_PROJECT_NAME
    assert values.get("BACKUP_ROOT", "").endswith("contraclaim-stg")
    assert float(values.get("MONGO_WIREDTIGER_CACHE_GB", "0")) > 0


def test_the_staging_template_binds_the_gateway_to_loopback_off_the_dev_port() -> None:
    """The dev stack already holds 8080, 6333, 6379 and 6380 on this class of host."""
    text = ENV_STAGING_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"^HTTP_PORT=(.+)$", text, re.MULTILINE)
    assert match, "the staging template does not pin HTTP_PORT, so the gateway takes production's binding"
    binding = match.group(1).strip()
    assert binding.startswith("127.0.0.1:"), (
        "the bundled gateway speaks plain HTTP while the backend sets secure "
        "cookies; the staging port must be loopback-only and reached over a tunnel"
    )
    assert not binding.endswith(":8080"), "8080 is already taken by the development gateway"


def test_the_staging_template_names_no_data_tier_port() -> None:
    """`pre_deploy_readiness.sh` fails a compose that publishes a data-service port."""
    directives = _directives(ENV_STAGING_TEMPLATE)
    # A docker publish mapping is `[host:]host-port:container-port`, so a
    # data-tier port is only *published* when it appears between two colons.
    # Matching a bare `:6333` would reject `QDRANT_URL=http://qdrant:6333`,
    # which is an internal service address and exactly what staging should
    # use - and a rule that rejects the correct configuration is a rule
    # somebody deletes.
    for port in (6333, 6334, 6379, 6380, 27017):
        assert f":{port}:" not in directives, (
            f"the staging template appears to publish data-tier port {port}; "
            "pre_deploy_readiness.sh fails a compose that publishes one"
        )
