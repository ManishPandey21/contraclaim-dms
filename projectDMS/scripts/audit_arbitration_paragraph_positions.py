"""Dry-run-first audit for historical defence/rejoinder paragraph rows.

Examples:
  python scripts/audit_arbitration_paragraph_positions.py
  python scripts/audit_arbitration_paragraph_positions.py --case-id CASE
  python scripts/audit_arbitration_paragraph_positions.py --apply-review-labels
  python scripts/audit_arbitration_paragraph_positions.py --resolve defence-matrix ROW_ID --actor USER_ID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConfigurationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from rbac_backend.core.config import settings  # noqa: E402
from rbac_backend.services.arbitration_drafting.historical_paragraph_review import (  # noqa: E402
    audit_historical_paragraph_positions,
    resolve_historical_paragraph_position,
)


async def _run(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(str(settings.DATABASE_URL), serverSelectionTimeoutMS=5000)
    try:
        db = client.get_default_database()
    except ConfigurationError:
        db = client["contraclaim"]
    try:
        await db.command("ping")
        if args.resolve:
            matrix_slug, row_id = args.resolve
            resolved = await resolve_historical_paragraph_position(
                db,
                matrix_slug=matrix_slug,
                row_id=row_id,
                actor_id=args.actor,
            )
            print(
                json.dumps(
                    {
                        "mode": "resolved",
                        "matrix": matrix_slug,
                        "row_id": str(resolved.get("_id") or ""),
                        "historical_review_status": resolved.get("historical_review_status"),
                    },
                    sort_keys=True,
                )
            )
            return 0
        report = await audit_historical_paragraph_positions(
            db,
            case_id=args.case_id,
            apply_review_labels=args.apply_review_labels,
        )
        if not args.include_row_ids:
            report = {key: value for key, value in report.items() if key != "findings"}
        print(json.dumps(report, sort_keys=True, default=str))
        return 2 if report.get("requires_legal_review") else 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id")
    parser.add_argument("--apply-review-labels", action="store_true")
    parser.add_argument("--include-row-ids", action="store_true")
    parser.add_argument(
        "--resolve",
        nargs=2,
        metavar=("MATRIX", "ROW_ID"),
        choices=None,
    )
    parser.add_argument("--actor")
    args = parser.parse_args()
    if args.resolve:
        if args.resolve[0] not in {"defence-matrix", "rejoinder-matrix"}:
            parser.error("--resolve MATRIX must be defence-matrix or rejoinder-matrix")
        if not args.actor:
            parser.error("--actor is required with --resolve")
    if args.resolve and args.apply_review_labels:
        parser.error("--resolve and --apply-review-labels are mutually exclusive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
