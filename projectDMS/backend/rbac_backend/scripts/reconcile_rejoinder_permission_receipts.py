"""Operator action: clean up interrupted rejoinder permission decisions.

    python -m rbac_backend.scripts.reconcile_rejoinder_permission_receipts [--case-id <id>] [--apply]

Run inside the backend container. Without `--apply` it writes nothing and lists
the `pending` rejoinder permission receipts older than the stale age (they
would be aborted) and the receipts whose grants would be withdrawn, including
grants left behind by an earlier abort. With `--apply` it aborts them (only
while still pending) and withdraws the grants that still cite an aborted
receipt. It never commits a receipt or approves a permission; readers already
refuse these grants, so this only tidies the record.

The report is always printed. Any error is also written to stderr and the exit
status is 1, so a partial run is never mistaken for a clean one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional


async def _main(case_id: Optional[str], apply: bool) -> int:
    from ..core.database import connect, disconnect, get_database
    from ..services.arbitration_drafting.permission_receipts import reconcile_stale_permission_receipts

    await connect()
    try:
        db = await get_database()
        report = await reconcile_stale_permission_receipts(db, case_id=case_id, dry_run=not apply)
        sys.stdout.write(json.dumps({"dry_run": not apply, "case_id": case_id, **report}, default=str) + "\n")
        for error in report["errors"]:
            sys.stderr.write(f"error: {error}\n")
        return 1 if report["errors"] else 0
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.case_id, args.apply)))


if __name__ == "__main__":
    main()
