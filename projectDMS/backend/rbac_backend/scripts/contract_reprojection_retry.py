"""Operator action: rebuild one Contract Master instrument's projection.

    python -m rbac_backend.scripts.contract_reprojection_retry --contract-document-id <id> [--dry-run]

Run inside the backend or contract-worker container. It re-opens the work for the
instrument's LIVE classification revision (a failed, exhausted, completed or
in-flight generation) and moves the instrument back to PENDING; the
contract-worker's reprojection runtime then rebuilds and, only if that succeeds,
publishes CURRENT. It never writes a legal field and never marks anything
CURRENT itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _main(contract_document_id: str, dry_run: bool) -> int:
    from ..core.database import connect, disconnect, get_database
    from ..services.contract_document_store import CONTRACT_DOCUMENTS_COLLECTION
    from ..services.contract_reprojection_worker import ContractReprojectionWorker

    await connect()
    try:
        db = await get_database()
        record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
        if record is None:
            sys.stderr.write(f"no instrument {contract_document_id}\n")
            return 2
        worker = ContractReprojectionWorker(db)
        revision = int(record.get("classification_revision") or 0)
        before = {
            "projection_status": record.get("projection_status"),
            "projection_revision": record.get("projection_revision"),
            "classification_revision": revision,
            "projection_work": await worker.claim_state(contract_document_id, revision),
        }
        if dry_run:
            sys.stdout.write(json.dumps({"dry_run": True, "before": before}, default=str) + "\n")
            return 0
        result = await worker.request_retry(contract_document_id, reason="operator retry (CLI)")
        sys.stdout.write(json.dumps({"before": before, "result": result}, default=str) + "\n")
        return 0
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract-document-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.contract_document_id, args.dry_run)))


if __name__ == "__main__":
    main()
