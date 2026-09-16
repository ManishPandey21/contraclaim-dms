"""Inspect, apply and re-apply the R-A9D role-contract alignment (role documents only).

    python -m rbac_backend.scripts.align_role_contract            # inspect (writes nothing)
    python -m rbac_backend.scripts.align_role_contract --apply    # apply, then prove a second pass adds nothing

This is the ONLY way the alignment runs: an explicit cutover step, not a catalogued
migration (`services/role_contract_alignment.py` says why). It prints the evidence the
owner asked for: BEFORE, the EXPECTED release contract, and each permission classified
as a canonical ADDITION, an owner-approved REMOVAL (F-A9D-1) or a production-only extra
that is PRESERVED and reported - with the proof that no addition and no removal is
outside what the owner approved, AFTER, and that a second apply is a no-op. Role keys,
permission names and counts only. Keep its JSON output as the cutover record.

Exit codes: 0 clean; 2 the migration reported warnings; 3 an addition outside the
release contract, a removal outside the owner decision (neither is expected; the plan
cannot produce one) or a non-empty second pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rbac_backend.core.database import disconnect, get_database
from rbac_backend.services import role_contract_alignment as alignment


def summarise(operations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for row in operations:
        contract = set(alignment.release_contract(row["role_id"]))
        unapproved = sorted(set(row["additions"]) - contract)
        rows.append(
            {
                "role_id": row["role_id"],
                "status": row["status"],
                "before_count": len(row["before"]),
                "expected_count": len(row["expected"]),
                "proposed_additions": row["additions"],
                "proposed_additions_count": len(row["additions"]),
                "unapproved_additions": unapproved,
                "proposed_removals": row.get("removals", []),
                "proposed_removals_count": len(row.get("removals", [])),
                "unapproved_removals": row.get("unapproved_removals", []),
                "retained_outside_contract": row["retained_outside_contract"],
                "retained_outside_contract_count": len(row["retained_outside_contract"]),
                "after_count": len(row["after"]) if "after" in row else None,
            }
        )
    return rows


async def run(apply: bool) -> Dict[str, Any]:
    db = await get_database()
    try:
        before = await alignment.align(db, dry_run=True)
        report: Dict[str, Any] = {
            "operation": alignment.OPERATION,
            "mode": "apply" if apply else "inspect",
            "aligned_role_ids": list(alignment.ALIGNED_ROLE_IDS),
            "owner_approved_removals": {
                role_id: list(permissions) for role_id, permissions in alignment.OWNER_APPROVED_REMOVALS.items()
            },
            "before": summarise(before.operations),
            "before_permissions": {row["role_id"]: row["before"] for row in before.operations},
            "expected_release_contract": {row["role_id"]: row["expected"] for row in before.operations},
            "warnings": list(before.warnings),
            "notices": list(before.notices),
        }
        if apply:
            applied = await alignment.align(db, dry_run=False)
            second = await alignment.align(db, dry_run=False)
            report["apply"] = summarise(applied.operations)
            report["after_permissions"] = {row["role_id"]: row.get("after") for row in applied.operations}
            report["second_apply"] = summarise(second.operations)
            report["second_apply_is_noop"] = all(
                not row["additions"] and not row.get("removals") for row in second.operations
            ) and all(row["status"] in (alignment.ALIGNED, alignment.ABSENT) for row in second.operations)
            report["warnings"] = sorted(set(report["warnings"]) | set(applied.warnings) | set(second.warnings))
        report["unapproved_additions_total"] = sum(len(row["unapproved_additions"]) for row in report["before"])
        report["unapproved_removals_total"] = sum(len(row["unapproved_removals"]) for row in report["before"])
        return report
    finally:
        await disconnect()


def exit_code(report: Dict[str, Any]) -> int:
    if (
        report["unapproved_additions_total"]
        or report.get("unapproved_removals_total")
        or report.get("second_apply_is_noop") is False
    ):
        return 3
    if report["warnings"]:
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or apply the R-A9D role-contract alignment.")
    parser.add_argument("--apply", action="store_true", help="Apply, then prove a second apply adds nothing.")
    args = parser.parse_args()
    report = asyncio.run(run(apply=args.apply))
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(exit_code(report))


if __name__ == "__main__":
    main()
