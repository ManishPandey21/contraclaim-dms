#!/usr/bin/env python3
"""Refuse a destructive staging command that could reach production.

R-A8A's verdict puts the pre-cutover staging rehearsal on the production host,
inside a maintenance window. One Docker daemon then holds both stacks, and
`docker compose down -v` is indifferent to which one it was pointed at. The two
ways it goes wrong are not typos anybody notices:

* the project name is wrong - `-p contraclaim`, or no `-p` at all, in which case
  compose derives the project from the working directory;
* the project name is right and the *rendered configuration* names a production
  resource anyway, through `external: true` or a pinned `name:`. Compose then
  attaches staging to the production volume and removes it on teardown.

This guard answers over the rendered configuration - what Docker would actually
operate on - rather than over a command line, because that is where both hide::

    docker compose -p contraclaim-stg \\
      -f docker-compose.prod.yml \\
      -f docker-compose.mongo-replicaset.yml \\
      -f docker-compose.staging.yml \\
      --env-file .env.staging config --format json >/tmp/staging-rendered.json

    scripts/staging_teardown_guard.py \\
      --project contraclaim-stg \\
      --rendered-config /tmp/staging-rendered.json \\
      -- docker compose -p contraclaim-stg \\
           -f docker-compose.prod.yml \\
           -f docker-compose.mongo-replicaset.yml \\
           -f docker-compose.staging.yml \\
           --env-file .env.staging down -v

The command after `--` runs only if every rule passes. Otherwise the guard exits
2 and the command is not executed.

It reports resource *names* only. `docker compose config` renders resolved
environment values, so echoing the document would print secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: The one project a destructive staging command may target. An allowlist of
#: one, not a denylist: a denylist only refuses the names somebody thought of.
STAGING_PROJECT = "contraclaim-stg"

#: What Docker prefixes onto every volume and network of that project. The
#: trailing underscore is load-bearing - without it `contraclaim-stg-2_mongo1`
#: would read as staging-owned.
STAGING_PREFIX = f"{STAGING_PROJECT}_"

#: The production project sharing the daemon. Named so the refusal can say what
#: the operator nearly did rather than just "not allowed".
PRODUCTION_PROJECT = "contraclaim"

EXIT_REFUSED = 2
EXIT_USAGE = 64


@dataclass(frozen=True)
class Verdict:
    """The answer, plus the blast radius that answer authorises.

    A guard that returns only yes/no leaves the operator unable to check what
    they are about to destroy, so an allowed verdict carries the list.
    """

    allowed: bool
    refusals: tuple[str, ...] = ()
    owned_volumes: tuple[str, ...] = ()
    owned_networks: tuple[str, ...] = ()


@dataclass
class _Findings:
    refusals: list[str] = field(default_factory=list)

    def refuse(self, message: str) -> None:
        self.refusals.append(message)


def _effective_name(project: str, key: str, definition: Any) -> str:
    """The name Docker will actually use for a declared volume or network.

    `name:` wins when present; otherwise Docker composes `<project>_<key>`.
    Checking `external` alone misses a pinned `name:` on a non-external volume,
    which this project would both create and destroy under the production
    volume's exact name.
    """

    if isinstance(definition, Mapping):
        pinned = definition.get("name")
        if isinstance(pinned, str) and pinned.strip():
            return pinned.strip()
    return f"{project}_{key}"


def _is_external(definition: Any) -> bool:
    if not isinstance(definition, Mapping):
        return False
    external = definition.get("external")
    if isinstance(external, Mapping):
        # Compose's legacy long form: `external: {name: ...}`.
        return True
    return bool(external)


def _check_resources(
    findings: _Findings,
    *,
    project: str,
    kind: str,
    declared: Mapping[str, Any],
) -> list[str]:
    """Enumerate one resource class and refuse everything outside the project."""

    if not isinstance(declared, Mapping):
        # A render that is not shaped like a render is a render nobody has
        # checked. Refuse rather than raise: a traceback and a refusal both stop
        # the command, but only one of them tells the operator what to fix.
        findings.refuse(
            f"the rendered configuration's '{kind}s' section is "
            f"{type(declared).__name__}, not a mapping; this is not "
            f"`docker compose config --format json` output"
        )
        return []

    owned: list[str] = []
    for key, definition in sorted(declared.items()):
        name = _effective_name(project, key, definition)
        if _is_external(definition):
            # An external resource survives `down -v`, so staging state would
            # outlive the rehearsal, and an external *production* name is the
            # attachment this guard exists to stop. Refuse both as one rule:
            # a disposable stack owns nothing it did not create.
            findings.refuse(
                f"{kind} '{key}' is declared external as '{name}'; a disposable staging "
                f"stack must own every resource it names, and an external name is "
                f"attached verbatim - including a production one"
            )
        if not name.startswith(STAGING_PREFIX):
            findings.refuse(
                f"{kind} '{key}' resolves to '{name}', which is outside the "
                f"'{STAGING_PREFIX}' namespace"
                + (
                    f" and belongs to the production project '{PRODUCTION_PROJECT}'"
                    if name.startswith(f"{PRODUCTION_PROJECT}_")
                    else ""
                )
            )
            continue
        owned.append(name)
    return owned


def _project_names_in_argv(argv: Sequence[str]) -> list[str]:
    """Every project name the command line itself selects.

    Checking one project and then executing another is a guard that proves
    nothing, so the two must agree.
    """

    found: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-p", "--project-name"):
            if index + 1 < len(argv):
                found.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("--project-name="):
            found.append(token.split("=", 1)[1])
        elif token.startswith("-p") and len(token) > 2 and not token.startswith("--"):
            found.append(token[2:])
        index += 1
    return found


def assess(
    *,
    project: str | None,
    rendered: Mapping[str, Any] | None,
    argv: Sequence[str] = (),
    existing_volumes: Iterable[str] | None = None,
    existing_networks: Iterable[str] | None = None,
) -> Verdict:
    """Decide whether a destructive command may run against `project`.

    Pure: it reads a rendered configuration and, optionally, the daemon's
    resource lists. It never touches Docker itself, which is what makes every
    rule below testable without a daemon.
    """

    findings = _Findings()

    name = (project or "").strip()
    if not name:
        findings.refuse(
            "no compose project name was supplied; without one compose derives the "
            "project from the working directory, which on this host is the production "
            "checkout"
        )
    elif name != STAGING_PROJECT:
        detail = (
            f" - that is the PRODUCTION project on this daemon"
            if name == PRODUCTION_PROJECT
            else ""
        )
        findings.refuse(
            f"project '{name}' is not the isolated staging project "
            f"'{STAGING_PROJECT}'{detail}"
        )

    if rendered is None:
        # Fail closed: an unreadable render is not evidence of a safe render.
        findings.refuse(
            "no rendered compose configuration was available to inspect; render it "
            "with `docker compose ... config --format json` and pass it in"
        )
        return Verdict(allowed=False, refusals=tuple(findings.refusals))

    rendered_name = str(rendered.get("name") or "").strip()
    if name and rendered_name and rendered_name != name:
        findings.refuse(
            f"the rendered configuration names project '{rendered_name}' but the "
            f"teardown targets '{name}'; compose resolves one of them and the "
            f"operator reads the other"
        )
    if not rendered_name:
        findings.refuse("the rendered configuration declares no project name")

    # The command that RUNS must select the project that was CHECKED. Refusing
    # only a command whose `-p` disagrees leaves the first failure mode this
    # module exists to stop wide open: a command carrying no `-p` at all selects
    # nothing, so the disagreement loop never runs, the guard reports PASS and
    # then executes it. `docker compose -f docker-compose.prod.yml down -v` and
    # `rm -rf /opt/contraclaim-dms` both have that shape. Silence is not
    # agreement, so an unnamed project is refused rather than passed over.
    if argv:
        selected_projects = _project_names_in_argv(argv)
        if not selected_projects:
            findings.refuse(
                "the command line selects no compose project (no -p/--project-name), "
                "so nothing ties it to the checked project "
                f"'{name or '<unset>'}'; compose would derive the project from the "
                "working directory and a non-compose command is not bounded by the "
                "render at all"
            )
        for selected in selected_projects:
            if selected != name:
                findings.refuse(
                    f"the command line targets project '{selected}' while the checked "
                    f"project is '{name or '<unset>'}'"
                )

    # Enumerate against the project that was ASKED for, not the one that was
    # rendered: a mismatch is already a refusal, and deriving names from the
    # rendered value would let a wrong render define what "owned" means.
    namespace = name or rendered_name
    volumes = _check_resources(
        findings,
        project=namespace,
        kind="volume",
        declared=rendered.get("volumes") or {},
    )
    networks = _check_resources(
        findings,
        project=namespace,
        kind="network",
        declared=rendered.get("networks") or {},
    )

    # Host inventory narrows the answer to what actually exists; it must never
    # widen it, so it is applied as a filter over the names already proven to
    # be inside the namespace.
    if existing_volumes is not None:
        present = set(existing_volumes)
        volumes = [item for item in volumes if item in present]
    if existing_networks is not None:
        present = set(existing_networks)
        networks = [item for item in networks if item in present]

    return Verdict(
        allowed=not findings.refusals,
        refusals=tuple(findings.refusals),
        owned_volumes=tuple(sorted(volumes)),
        owned_networks=tuple(sorted(networks)),
    )


def _read_rendered(path: Path) -> Mapping[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, Mapping) else None


def _report(verdict: Verdict, *, project: str) -> None:
    if verdict.allowed:
        print(f"staging teardown guard: PASS for project '{project}'")
        print(f"  volumes that will be destroyed ({len(verdict.owned_volumes)}):")
        for item in verdict.owned_volumes:
            print(f"    {item}")
        print(f"  networks that will be removed ({len(verdict.owned_networks)}):")
        for item in verdict.owned_networks:
            print(f"    {item}")
        return

    print(f"staging teardown guard: REFUSED for project '{project or '<unset>'}'", file=sys.stderr)
    for refusal in verdict.refusals:
        print(f"  - {refusal}", file=sys.stderr)
    print(
        "  Nothing was executed. Only a rendered configuration whose every volume "
        f"and network sits under '{STAGING_PREFIX}' may be torn down here.",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refuse a destructive staging compose command unless it targets the "
            f"isolated '{STAGING_PROJECT}' project and every volume and network it "
            "would destroy carries that project's prefix."
        )
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("COMPOSE_PROJECT_NAME", ""),
        help="compose project name; defaults to COMPOSE_PROJECT_NAME",
    )
    parser.add_argument(
        "--rendered-config",
        required=True,
        type=Path,
        help="JSON from `docker compose ... config --format json`",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="after `--`, the command to run only if the guard passes",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]

    verdict = assess(
        project=args.project,
        rendered=_read_rendered(args.rendered_config),
        argv=command,
    )
    _report(verdict, project=args.project)

    if not verdict.allowed:
        return EXIT_REFUSED
    if not command:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    sys.exit(main())
