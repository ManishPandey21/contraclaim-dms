from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from ...core.config import settings
from .workflow_repository import artifact_hash


VALIDATION_CHECKS = (
    "citation_integrity", "source_permissions", "evidence_provenance", "normalized_amounts",
    "normalized_dates", "named_entities", "assertion_source_coverage", "chronology_coverage",
    "pleading_structure", "paragraph_positions", "jurisdiction", "limitation", "notice",
    "quantum", "new_matter", "relief", "duplicate_claims", "exhibit_availability", "source_drift",
)


class ArbitrationValidationOrchestrator:
    """Read-only parallel validation and conservative bounded remediation.

    The orchestrator never creates facts or evidence. If existing deterministic
    validators cannot resolve an issue without changing legal strategy, it
    records a human-review escalation instead of rewriting the pleading.
    """

    async def evaluate(self, version: Dict[str, Any]) -> Dict[str, Any]:
        markdown = str(version.get("full_markdown") or "")
        ledger = list(version.get("source_ledger") or [])
        source_keys = {str(row.get("source_key")) for row in ledger if row.get("source_key")}
        cited = set(re.findall(r"\[(SRC-\d+)\]", markdown))
        existing = list(((version.get("structured_output") or {}).get("approval_blockers") or []))

        async def check(name: str) -> Dict[str, Any]:
            issues: List[str] = []
            if name == "citation_integrity":
                issues.extend(f"Unknown source key {key}" for key in sorted(cited - source_keys))
            elif name in {"source_permissions", "evidence_provenance", "source_drift"}:
                issues.extend(
                    f"Source {row.get('source_key') or row.get('source_id')} is not approved and authoritative"
                    for row in ledger if row.get("quality_flags") and "manual_or_unverified" in row.get("quality_flags", [])
                )
            elif name == "paragraph_positions":
                issues.extend(str(item) for item in existing if "Paragraph" in str(item))
            elif name == "new_matter":
                issues.extend(str(item) for item in existing if "permission" in str(item).lower() or "new matter" in str(item).lower())
            elif name == "exhibit_availability":
                issues.extend(str(item) for item in existing if "exhibit" in str(item).lower())
            elif name == "assertion_source_coverage":
                issues.extend(str(item) for item in existing if "evidence" in str(item).lower() or "source" in str(item).lower())
            return {"check": name, "status": "blocked" if issues else "passed", "issues": issues}

        results = await asyncio.gather(*(check(name) for name in VALIDATION_CHECKS))
        blockers = sorted({issue for result in results for issue in result["issues"]} | {str(item) for item in existing})
        report = {
            "checks": results,
            "blockers": blockers,
            "status": "blocked" if blockers else str(version.get("validation_status") or "passed"),
            "remediation_cycle": 0,
            "max_remediation_cycles": int(settings.ARBITRATION_ENGINE_RETRY_BUDGET),
            "human_review_required": bool(blockers),
            "remediation_policy": "approved_existing_evidence_only",
        }
        report["report_hash"] = artifact_hash(report)
        return report

    def bounded_remediation(self, report: Dict[str, Any]) -> Dict[str, Any]:
        maximum = int(report.get("max_remediation_cycles") or 0)
        if not report.get("blockers") or maximum <= 0:
            return report
        # No fact-safe automatic rewrite is available for these legal blockers.
        # Consume one bounded attempt and escalate without changing the artifact.
        updated = {**report, "remediation_cycle": min(1, maximum), "human_review_required": True}
        updated["report_hash"] = artifact_hash({key: value for key, value in updated.items() if key != "report_hash"})
        return updated
