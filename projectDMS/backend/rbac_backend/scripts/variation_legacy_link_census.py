"""READ-ONLY census of legacy Variation Document links (CL-2 pre-migration step).

Counts only: Variation rows, rows carrying ``linked_document_ids``, total linked
ids, missing / deleted / foreign-organisation / foreign-project / duplicate ids,
the ``uploadType`` of each resolved Document (incoming / outgoing / contract /
other / missing), and how ``letter_reference`` values resolve. No identifier,
letter number, subject or party name is printed, so the output may be read out of
production.

Run it inside the backend container (the host interpreter has none of the app's
dependencies)::

    docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \\
        exec -T backend python -m rbac_backend.scripts.variation_legacy_link_census

Running it against production needs its own authorization; CL-2 did not run it.

Read-only by construction for the census's own code path: the database handle
is wrapped so any write method (and ``aggregate`` / ``command``) raises before it
reaches the driver. This guards against accidents, not against deliberate
misuse - attributes such as ``collection.database`` or ``with_options`` still
return unwrapped handles. For a hard guarantee run it with a read-only Mongo
user.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any


if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rbac_backend.core.database import disconnect, get_database
from rbac_backend.services.variation_document_link_migration import variation_legacy_census


_WRITE_METHODS = frozenset(
    {
        "insert_one", "insert_many", "update_one", "update_many", "replace_one",
        "delete_one", "delete_many", "find_one_and_update", "find_one_and_delete",
        "find_one_and_replace", "bulk_write", "create_index", "create_indexes",
        "drop", "drop_index", "drop_indexes", "rename", "aggregate",
    }
)


class ReadOnlyCollection:
    """A collection handle that refuses every write (and ``aggregate``, which
    can write through ``$out`` / ``$merge``)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name in _WRITE_METHODS:
            raise PermissionError(f"read-only census refused {name}")
        return getattr(self._inner, name)


class ReadOnlyDatabase:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name in {"command", "drop_collection", "create_collection"}:
            raise PermissionError(f"read-only census refused {name}")
        return ReadOnlyCollection(getattr(self._inner, name))

    def __getitem__(self, name: str) -> Any:
        return ReadOnlyCollection(self._inner[name])


async def _main_async(args: argparse.Namespace) -> int:
    db = ReadOnlyDatabase(await get_database())
    try:
        census = await variation_legacy_census(
            db,
            organization_id=args.organization_id,
            project_id=args.project_id,
        )
        print(json.dumps(census, indent=2, sort_keys=True))
        return 0
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only, counts-only census of legacy Variation Document links."
    )
    parser.add_argument("--organization-id", help="Limit the census to one organisation.")
    parser.add_argument("--project-id", help="Limit the census to one project.")
    raise SystemExit(asyncio.run(_main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
