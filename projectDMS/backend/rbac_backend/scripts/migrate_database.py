from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, List


if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rbac_backend.core.database import disconnect, get_database
from rbac_backend.migrations import MigrationRunner


def _json_default(value: Any) -> str:
    return str(value)


async def _main_async(args: argparse.Namespace) -> int:
    db = await get_database()
    runner = MigrationRunner(db)

    try:
        if args.list:
            rows: List[dict[str, Any]] = await runner.plan(target=args.target)
            print(json.dumps(rows, indent=2, default=_json_default))
            return 0

        results = await runner.run(apply=args.apply, target=args.target)
        print(json.dumps([result.to_dict() for result in results], indent=2, default=_json_default))

        failures = [result for result in results if result.warnings and args.fail_on_warning]
        if failures:
            return 2
        return 0
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Contraclaim DMS MongoDB migrations.")
    parser.add_argument("--apply", action="store_true", help="Apply pending migrations. Defaults to dry-run.")
    parser.add_argument("--list", action="store_true", help="List migration plan without executing upgrade code.")
    parser.add_argument("--target", help="Run/list migrations up to and including this version.")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return non-zero when a migration emits warnings, useful for release gates.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
