"""Guards for the same-host staging teardown gate.

R-A8A's verdict puts the staging rehearsal on the production host, inside a
maintenance window. That decision buys a hardware fit and pays for it with
blast-radius adjacency: one Docker daemon holds both stacks, so
`docker compose down -v` typed against the wrong project, or a staging compose
file that names a production volume, destroys the business data the window
exists to protect. Compose reports neither as an error - it does exactly what it
was asked.

`scripts/staging_teardown_guard.py` is the thing that says no. It refuses unless
the project is exactly the isolated staging project AND every volume and network
the rendered configuration would destroy carries that project's prefix. The
tests below are the controls: each one is a way the guard could be green while
pointed at production.

The guard decides over a *rendered* compose configuration
(`docker compose ... config --format json`), not over a command line. A command
line says what somebody typed; the rendered configuration says what Docker would
actually operate on, which is where an `external:` volume or a pinned `name:`
hides.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPO_ROOT / "scripts" / "staging_teardown_guard.py"
COMPOSE_STAGING = REPO_ROOT / "docker-compose.staging.yml"

#: The only project a destructive staging command may target.
STAGING_PROJECT = "contraclaim-stg"

#: The production project on the same daemon. Named here so a test that stops
#: being about production fails loudly rather than quietly passing.
PRODUCTION_PROJECT = "contraclaim"

#: Parametrisation marker. The real refusal code is read off the guard module
#: inside the test, so a change to it fails the assertion rather than silently
#: re-labelling what "refused" means here.
EXIT_REFUSED_SENTINEL = -1


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staging_teardown_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {GUARD_PATH}"
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves its own module out of `sys.modules` to interpret the
    # string annotations `from __future__ import annotations` produces, so the
    # module has to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard() -> ModuleType:
    return _load_guard()


def _rendered(
    *,
    name: str = STAGING_PROJECT,
    volumes: Dict[str, Any] | None = None,
    networks: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """A rendered compose configuration shaped like `docker compose config`.

    The default is the isolated staging stack: every volume and network is
    declared without `external` and without a pinned `name`, so Docker derives
    `contraclaim-stg_<key>` for each one.
    """

    if volumes is None:
        volumes = {
            key: {}
            for key in (
                "mongo1_data",
                "mongo2_data",
                "mongo3_data",
                "qdrant_data",
                "qdrant_snapshots",
                "falkordb_data",
                "redis_data",
                "backend_uploads",
                "backend_logs",
            )
        }
    if networks is None:
        networks = {key: {} for key in ("data-net", "service-net", "edge-net", "egress-net")}
    return {"name": name, "services": {}, "volumes": volumes, "networks": networks}


def _refusals(verdict: Any) -> str:
    return "\n".join(verdict.refusals)


# --------------------------------------------------------------------------- #
# The five required controls                                                   #
# --------------------------------------------------------------------------- #


def _staging_named_render(name: str) -> Dict[str, Any]:
    """A render whose every resource is already inside the staging namespace.

    Used where the *project* rule is what is under test: with the resource names
    beyond reproach, a refusal can only have come from the project check. A
    production project against a production render is refused by four rules at
    once, so it proves none of them individually.
    """

    return {
        "name": name,
        "services": {},
        "volumes": {"mongo1_data": {"name": "contraclaim-stg_mongo1_data"}},
        "networks": {"data-net": {"name": "contraclaim-stg_data-net"}},
    }


def test_1_the_production_project_name_is_refused(guard: ModuleType) -> None:
    """`-p contraclaim` is the single keystroke that ends the company."""

    verdict = guard.assess(
        project=PRODUCTION_PROJECT,
        rendered=_rendered(name=PRODUCTION_PROJECT),
    )

    assert not verdict.allowed
    assert PRODUCTION_PROJECT in _refusals(verdict)


def test_1b_the_production_project_is_refused_on_the_project_name_alone(
    guard: ModuleType,
) -> None:
    """The project rule, isolated from the resource rules.

    Every resource here is already staging-named, so nothing but the project
    check can produce a refusal. Without this case, deleting the project check
    leaves the suite green: a production project normally owns production
    volumes and the prefix rule catches those instead.
    """

    verdict = guard.assess(
        project=PRODUCTION_PROJECT, rendered=_staging_named_render(PRODUCTION_PROJECT)
    )

    assert not verdict.allowed
    assert f"'{PRODUCTION_PROJECT}' is not the isolated staging project" in _refusals(
        verdict
    )


def test_2_an_empty_project_name_is_refused(guard: ModuleType) -> None:
    """No `-p` at all means compose derives the project from the directory.

    Run from `/opt/contraclaim-dms/projectDMS` that derivation is `projectdms`;
    run from a checkout someone named `contraclaim` it is production. Neither is
    a thing to guess about, so an unset project is a refusal in itself.
    """

    for blank in ("", "   ", None):
        verdict = guard.assess(project=blank, rendered=_staging_named_render(STAGING_PROJECT))
        assert not verdict.allowed, f"blank project {blank!r} was allowed"
        assert "project" in _refusals(verdict).lower()


def test_2b_the_blank_refusal_says_what_compose_would_have_done(
    guard: ModuleType,
) -> None:
    """"Not the staging project" is true of a blank name and useless to read.

    The remedy differs: a wrong `-p` is corrected by typing the right one, while
    a missing `-p` means compose silently falls back to the working directory,
    which on this host is the production checkout. The operator has to be told
    which of the two happened.
    """

    verdict = guard.assess(project="", rendered=_staging_named_render(STAGING_PROJECT))

    assert not verdict.allowed
    assert "working directory" in _refusals(verdict)


def test_3_a_staging_project_naming_a_production_mongo_volume_is_refused(
    guard: ModuleType,
) -> None:
    """The project is right and the target is still production.

    This is the failure the project-name check alone cannot catch: an
    `external: true` volume is attached by its literal name, so the staging
    stack writes into - and `down -v` deletes - the production replica set.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(
            volumes={
                "mongo1_data": {"external": True, "name": "contraclaim_mongo1_data"},
                "redis_data": {},
            }
        ),
    )

    assert not verdict.allowed
    assert "contraclaim_mongo1_data" in _refusals(verdict)


def test_4_a_staging_project_naming_the_production_falkor_volume_is_refused(
    guard: ModuleType,
) -> None:
    """FalkorDB holds the letter/clause graph and has no second copy."""

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(
            volumes={
                "falkordb_data": {"external": True, "name": "contraclaim_falkordb_data"},
            }
        ),
    )

    assert not verdict.allowed
    assert "contraclaim_falkordb_data" in _refusals(verdict)


def test_5_the_isolated_staging_project_is_allowed(guard: ModuleType) -> None:
    """The positive case. Without it every rule above is satisfied by "no"."""

    verdict = guard.assess(project=STAGING_PROJECT, rendered=_rendered())

    assert verdict.allowed, _refusals(verdict)
    assert verdict.refusals == ()


# --------------------------------------------------------------------------- #
# The ways the five above could pass while still being wrong                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "project",
    ["contraclaim-prod", "staging", "contraclaim-stg-2", "Contraclaim-Stg", "contraclaim_stg"],
)
def test_any_project_that_is_not_the_staging_project_is_refused(
    guard: ModuleType, project: str
) -> None:
    """An allowlist of one, not a denylist of the names somebody thought of.

    `contraclaim-stg-2` matters specifically: it carries the staging prefix as a
    substring, so a `startswith` check on the project name would wave it
    through while it owns an entirely different set of volumes.

    Each render is staging-named throughout, so the project check is the only
    rule that can refuse.
    """

    verdict = guard.assess(project=project, rendered=_staging_named_render(project))

    assert not verdict.allowed
    assert f"'{project}' is not the isolated staging project" in _refusals(verdict)


def test_a_rendered_name_that_disagrees_with_the_project_is_refused(
    guard: ModuleType,
) -> None:
    """`-p contraclaim-stg` against a compose file that pins `name: contraclaim`.

    Compose resolves one of the two and the operator sees the other.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT, rendered=_rendered(name=PRODUCTION_PROJECT)
    )

    assert not verdict.allowed
    assert PRODUCTION_PROJECT in _refusals(verdict)


def test_a_pinned_name_on_a_non_external_volume_is_refused(guard: ModuleType) -> None:
    """`external` is not the only way to reach a production volume.

    A declared volume with `name: contraclaim_mongo1_data` and no `external`
    key is created by this project and removed by its `down -v` - under the
    production volume's exact name. Checking `external` alone misses it.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(volumes={"mongo1_data": {"name": "contraclaim_mongo1_data"}}),
    )

    assert not verdict.allowed
    assert "contraclaim_mongo1_data" in _refusals(verdict)


def test_a_production_network_is_refused(guard: ModuleType) -> None:
    """Networks are enumerated with the same rule as volumes.

    `down` removes the project's networks. Attaching to `contraclaim_data-net`
    would also put staging containers on the production data tier while the
    stack is up, which is the worse half of the same mistake.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(
            networks={"data-net": {"external": True, "name": "contraclaim_data-net"}}
        ),
    )

    assert not verdict.allowed
    assert "contraclaim_data-net" in _refusals(verdict)


@pytest.mark.parametrize("legacy", ["falkordb_data", "qdrant_data"])
def test_the_loose_pre_compose_volumes_are_refused(guard: ModuleType, legacy: str) -> None:
    """Two unprefixed volumes from the pre-compose era still exist on the host.

    They carry no project label, so nothing else on the daemon protects them.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(volumes={"falkordb_data": {"external": True, "name": legacy}}),
    )

    assert not verdict.allowed
    assert legacy in _refusals(verdict)


def test_an_external_volume_is_refused_even_when_it_carries_the_staging_prefix(
    guard: ModuleType,
) -> None:
    """A disposable stack owns nothing it did not create.

    An `external` volume survives `down -v`, so staging state would outlive the
    rehearsal and the next run would inherit it. The staging override declares
    no external resource today; this keeps that true.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(
            volumes={"mongo1_data": {"external": True, "name": "contraclaim-stg_mongo1_data"}}
        ),
    )

    assert not verdict.allowed
    assert "external" in _refusals(verdict).lower()


def test_a_missing_rendered_configuration_is_refused(guard: ModuleType) -> None:
    """Fail closed. An unreadable render is not evidence of a safe render."""

    verdict = guard.assess(project=STAGING_PROJECT, rendered=None)

    assert not verdict.allowed


def test_a_command_line_that_targets_another_project_is_refused(
    guard: ModuleType,
) -> None:
    """The checked project and the executed project must be the same one.

    Checking a rendered staging configuration and then running
    `docker compose -p contraclaim down -v` is a guard that proves nothing.
    """

    for argv in (
        ["docker", "compose", "-p", PRODUCTION_PROJECT, "down", "-v"],
        ["docker", "compose", f"--project-name={PRODUCTION_PROJECT}", "down", "-v"],
    ):
        verdict = guard.assess(project=STAGING_PROJECT, rendered=_rendered(), argv=argv)
        assert not verdict.allowed, argv
        assert PRODUCTION_PROJECT in _refusals(verdict)


def test_a_command_line_that_agrees_with_the_project_is_allowed(
    guard: ModuleType,
) -> None:
    argv = ["docker", "compose", "-p", STAGING_PROJECT, "down", "-v"]

    verdict = guard.assess(project=STAGING_PROJECT, rendered=_rendered(), argv=argv)

    assert verdict.allowed, _refusals(verdict)


# --------------------------------------------------------------------------- #
# Ownership enumeration - Part 9's precondition                                #
# --------------------------------------------------------------------------- #


def test_an_allowed_verdict_enumerates_what_it_would_destroy(guard: ModuleType) -> None:
    """"Enumerate resource ownership before destructive teardown."

    A guard that answers only yes/no leaves the operator unable to check the
    blast radius, so the answer carries the list.
    """

    verdict = guard.assess(project=STAGING_PROJECT, rendered=_rendered())

    assert "contraclaim-stg_mongo1_data" in verdict.owned_volumes
    assert "contraclaim-stg_falkordb_data" in verdict.owned_volumes
    assert "contraclaim-stg_data-net" in verdict.owned_networks
    assert all(name.startswith("contraclaim-stg_") for name in verdict.owned_volumes)
    assert all(name.startswith("contraclaim-stg_") for name in verdict.owned_networks)


def test_a_host_resource_outside_the_project_is_never_listed_as_owned(
    guard: ModuleType,
) -> None:
    """Host inventory narrows the answer; it must never widen it.

    Passing the daemon's real volume list must not cause a production volume to
    appear in the set the teardown claims.
    """

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(),
        existing_volumes=[
            "contraclaim_mongo1_data",
            "contraclaim_falkordb_data",
            "falkordb_data",
            "contraclaim-stg_mongo1_data",
        ],
        existing_networks=["contraclaim_data-net", "contraclaim-stg_data-net"],
    )

    assert verdict.allowed, _refusals(verdict)
    assert "contraclaim_mongo1_data" not in verdict.owned_volumes
    assert "contraclaim_data-net" not in verdict.owned_networks


def test_every_refusal_names_the_thing_it_refused(guard: ModuleType) -> None:
    """A refusal the operator cannot act on becomes a refusal they bypass."""

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered=_rendered(
            volumes={
                "mongo1_data": {"external": True, "name": "contraclaim_mongo1_data"},
                "redis_data": {"name": "contraclaim_redis_data"},
            },
            networks={"data-net": {"external": True, "name": "contraclaim_data-net"}},
        ),
    )

    assert not verdict.allowed
    text = _refusals(verdict)
    for offender in (
        "contraclaim_mongo1_data",
        "contraclaim_redis_data",
        "contraclaim_data-net",
    ):
        assert offender in text, f"{offender} was refused without being named"


# --------------------------------------------------------------------------- #
# The guard as an executable gate                                              #
# --------------------------------------------------------------------------- #


def _run_cli(
    args: List[str], env: Dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.pop("COMPOSE_PROJECT_NAME", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, str(GUARD_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
        env=child_env,
    )


def test_the_cli_refuses_and_does_not_run_the_command(
    guard: ModuleType, tmp_path: Path
) -> None:
    rendered = tmp_path / "rendered.json"
    rendered.write_text(json.dumps(_rendered(name=PRODUCTION_PROJECT)), encoding="utf-8")
    witness = tmp_path / "witness"

    result = _run_cli(
        [
            "--project",
            PRODUCTION_PROJECT,
            "--rendered-config",
            str(rendered),
            "--",
            sys.executable,
            "-c",
            f"open({str(witness)!r}, 'w').write('ran')",
        ]
    )

    assert result.returncode == guard.EXIT_REFUSED, result.stdout + result.stderr
    assert not witness.exists(), "the guard refused and ran the command anyway"
    assert PRODUCTION_PROJECT in (result.stdout + result.stderr)


def test_the_cli_allows_and_runs_the_command(guard: ModuleType, tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.json"
    rendered.write_text(json.dumps(_rendered()), encoding="utf-8")
    witness = tmp_path / "witness"

    result = _run_cli(
        [
            "--project",
            STAGING_PROJECT,
            "--rendered-config",
            str(rendered),
            "--",
            sys.executable,
            "-c",
            f"open({str(witness)!r}, 'w').write('ran')",
        ]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert witness.read_text(encoding="utf-8") == "ran"


def test_the_cli_refuses_an_unreadable_rendered_configuration(
    guard: ModuleType, tmp_path: Path
) -> None:
    result = _run_cli(
        ["--project", STAGING_PROJECT, "--rendered-config", str(tmp_path / "absent.json")]
    )

    assert result.returncode == guard.EXIT_REFUSED, result.stdout + result.stderr


def test_the_cli_prints_no_secret_from_the_rendered_configuration(
    tmp_path: Path,
) -> None:
    """`docker compose config` renders resolved environment values.

    The guard reads that document, so anything it echoes could carry a
    password. It reports names only.
    """

    rendered = _rendered()
    rendered["services"] = {
        "falkordb": {
            "environment": {
                "FALKORDB_PASSWORD": "not-a-real-password-2f8c1d",
                "REDIS_ARGS": "--dir /data --requirepass not-a-real-password-2f8c1d",
            }
        }
    }
    path = tmp_path / "rendered.json"
    path.write_text(json.dumps(rendered), encoding="utf-8")

    result = _run_cli(["--project", STAGING_PROJECT, "--rendered-config", str(path)])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "not-a-real-password-2f8c1d" not in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "env, expected_exit",
    [
        ({}, EXIT_REFUSED_SENTINEL),
        ({"COMPOSE_PROJECT_NAME": ""}, EXIT_REFUSED_SENTINEL),
        ({"COMPOSE_PROJECT_NAME": PRODUCTION_PROJECT}, EXIT_REFUSED_SENTINEL),
        ({"COMPOSE_PROJECT_NAME": STAGING_PROJECT}, 0),
    ],
)
def test_the_cli_takes_its_project_from_compose_project_name(
    guard: ModuleType, tmp_path: Path, env: Dict[str, str], expected_exit: int
) -> None:
    """The brief's rule, through the entry point an operator actually types.

    `COMPOSE_PROJECT_NAME` is what compose itself reads when no `-p` is given,
    so the guard has to agree with compose about which project is selected -
    including when nothing selects one.
    """

    rendered = tmp_path / "rendered.json"
    rendered.write_text(json.dumps(_rendered()), encoding="utf-8")

    result = _run_cli(["--rendered-config", str(rendered)], env=env)

    if expected_exit == EXIT_REFUSED_SENTINEL:
        assert result.returncode == guard.EXIT_REFUSED, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# The guard and the compose file must agree                                    #
# --------------------------------------------------------------------------- #


def test_the_guard_and_the_staging_override_name_the_same_project(
    guard: ModuleType,
) -> None:
    """Two copies of one string is how a guard stops guarding the real stack."""

    override = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))

    assert override["name"] == STAGING_PROJECT
    assert guard.STAGING_PROJECT == STAGING_PROJECT
    assert guard.STAGING_PREFIX == f"{STAGING_PROJECT}_"


def test_the_staging_override_would_pass_its_own_guard(guard: ModuleType) -> None:
    """The definition R-A8C will tear down is one the guard accepts today.

    The override declares no `volumes:` or `networks:` of its own - it inherits
    production's, which are all non-external - so the rendered stack is the
    production declaration under the staging project name.
    """

    production = yaml.safe_load(
        (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    replica = yaml.safe_load(
        (REPO_ROOT / "docker-compose.mongo-replicaset.yml").read_text(encoding="utf-8")
    )
    volumes = {**(production.get("volumes") or {}), **(replica.get("volumes") or {})}
    networks = {**(production.get("networks") or {}), **(replica.get("networks") or {})}

    verdict = guard.assess(
        project=STAGING_PROJECT,
        rendered={
            "name": STAGING_PROJECT,
            "services": {},
            "volumes": {k: (v or {}) for k, v in volumes.items()},
            "networks": {k: (v or {}) for k, v in networks.items()},
        },
    )

    assert verdict.allowed, _refusals(verdict)
    assert "contraclaim-stg_falkordb_data" in verdict.owned_volumes
