from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Iterable, List, Optional

from ...core.config import settings
from .workflow_repository import artifact_hash


VALIDATION_SCHEMA_VERSION = "phase4-v1"
VALIDATION_BRANCHES = (
    "citations",
    "assertions",
    "legal_structure",
    "new_matter",
    "quantum",
    "duplication",
    "exhibits",
    "source_drift",
)

_CITATION_RE = re.compile(r"\[((?:S\d+)|(?:SRC-\d+))(?::[^\]]+)?\]")
_ADJACENT_DUPLICATE_CITATION_RE = re.compile(
    r"(\[(?:(?:S\d+)|(?:SRC-\d+))(?::[^\]]+)?\])(?:\s+\1)+"
)
_AMOUNT_RE = re.compile(
    r"\b(?:INR|USD|EUR|GBP|AED|SAR|QAR|\u20b9|\$|\u20ac|\u00a3)\s*[0-9][0-9,]*(?:\.\d+)?\b",
    flags=re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")


def _issue(
    branch: str,
    code: str,
    message: str,
    *,
    severity: str = "blocker",
    remediable: bool = False,
) -> Dict[str, Any]:
    return {
        "branch": branch,
        "code": code,
        "message": message,
        "severity": severity,
        "remediable": remediable,
    }


def _dedupe_issues(issues: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for issue in issues:
        key = (str(issue.get("branch")), str(issue.get("code")), str(issue.get("message")))
        unique[key] = issue
    return [unique[key] for key in sorted(unique)]


class ArbitrationValidationOrchestrator:
    """Parallel, immutable validation with narrowly bounded safe remediation.

    Validation branches never write draft or evidence state. Automatic
    remediation is limited to removing immediately repeated copies of the same
    existing citation token. It cannot add a source, date, amount, entity, or
    legal proposition; every other issue terminates in human revision/review.
    """

    async def evaluate(
        self,
        version: Dict[str, Any],
        *,
        dependency_hashes: Optional[Dict[str, str]] = None,
        remediation_cycle: int = 0,
    ) -> Dict[str, Any]:
        markdown = str(version.get("full_markdown") or "")
        ledger = list(version.get("source_ledger") or [])
        source_keys = {str(row.get("source_key")) for row in ledger if row.get("source_key")}
        cited = set(_CITATION_RE.findall(markdown))
        structured = version.get("structured_output") or {}
        existing_blockers = [str(item) for item in structured.get("approval_blockers") or []]
        existing_warnings = [str(item) for item in (structured.get("validation_warnings") or version.get("warnings") or [])]
        missing_evidence = [str(item) for item in version.get("missing_evidence") or []]
        version_hash = str(version.get("version_hash") or artifact_hash({
            "draft_id": version.get("draft_id"),
            "version": version.get("version"),
            "full_markdown": markdown,
            "source_ledger": ledger,
        }))
        dependency_hashes = {str(key): str(value) for key, value in sorted((dependency_hashes or {}).items()) if value}
        validation_input_hash = artifact_hash(
            {
                "schema_version": VALIDATION_SCHEMA_VERSION,
                "version_hash": version_hash,
                "source_ledger_hash": artifact_hash(ledger),
                "dependencies": dependency_hashes,
            }
        )

        async def validate_branch(branch: str) -> Dict[str, Any]:
            issues: List[Dict[str, Any]] = []
            combined = [*existing_blockers, *existing_warnings]
            if branch == "citations":
                issues.extend(
                    _issue(branch, "unknown_source_key", f"Draft cites source key {key} which is absent from the immutable source ledger.")
                    for key in sorted(cited - source_keys)
                )
                if not ledger and "[Evidence required]" not in markdown:
                    issues.append(_issue(branch, "missing_source_ledger", "Draft has no source ledger and no missing-evidence marker."))
            elif branch == "assertions":
                issues.extend(
                    _issue(branch, "missing_evidence", item)
                    for item in missing_evidence
                )
                for item in existing_blockers:
                    lower = item.lower()
                    if any(token in lower for token in ("evidence", "source", "monetary", "amount", "date", "entity", "assert")):
                        issues.append(_issue(branch, "unsupported_assertion", item))
                if str(version.get("validation_status") or "") == "blocked" and not existing_blockers:
                    issues.append(_issue(branch, "upstream_validation_blocked", "The immutable candidate is blocked by its generation-time validator."))
            elif branch == "legal_structure":
                for item in combined:
                    lower = item.lower()
                    if any(token in lower for token in ("paragraph", "jurisdiction", "limitation", "notice", "relief", "section", "case workspace")):
                        severity = "blocker" if item in existing_blockers else "warning"
                        issues.append(_issue(branch, "legal_structure_review", item, severity=severity))
                if not markdown.strip():
                    issues.append(_issue(branch, "empty_draft", "Candidate draft text is empty."))
            elif branch == "new_matter":
                for item in combined:
                    lower = item.lower()
                    if "new matter" in lower or "new claim" in lower or "permission" in lower or "leave" in lower:
                        severity = "blocker" if item in existing_blockers else "warning"
                        issues.append(_issue(branch, "new_matter_review", item, severity=severity))
            elif branch == "quantum":
                for item in combined:
                    lower = item.lower()
                    if any(token in lower for token in ("quantum", "amount", "monetary", "cost head", "global claim")):
                        severity = "blocker" if item in existing_blockers else "warning"
                        issues.append(_issue(branch, "quantum_review", item, severity=severity))
            elif branch == "duplication":
                if _ADJACENT_DUPLICATE_CITATION_RE.search(markdown):
                    issues.append(
                        _issue(
                            branch,
                            "adjacent_duplicate_citation",
                            "The same immutable citation token is repeated adjacently.",
                            severity="warning",
                            remediable=True,
                        )
                    )
                issues.extend(
                    _issue(branch, "duplicate_claim_review", item, severity="warning")
                    for item in existing_warnings
                    if "duplicat" in item.lower()
                )
            elif branch == "exhibits":
                for item in combined:
                    if "exhibit" in item.lower() or "annexure" in item.lower():
                        severity = "blocker" if item in existing_blockers else "warning"
                        issues.append(_issue(branch, "exhibit_unavailable", item, severity=severity))
            elif branch == "source_drift":
                for row in ledger:
                    source_key = str(row.get("source_key") or row.get("source_id") or "unknown")
                    flags = {str(flag).lower() for flag in row.get("quality_flags") or []}
                    verification = str(row.get("verification_status") or "").lower()
                    revision_drift = (
                        row.get("current_revision_id")
                        and row.get("source_revision_id")
                        and str(row.get("current_revision_id")) != str(row.get("source_revision_id"))
                    )
                    content_drift = (
                        row.get("current_sha256")
                        and row.get("sha256")
                        and str(row.get("current_sha256")) != str(row.get("sha256"))
                    )
                    if flags.intersection({"manual_or_unverified", "source_drift", "stale"}) or verification in {
                        "draft", "needs_review", "pending", "selected", "unverified",
                    } or revision_drift or content_drift:
                        issues.append(
                            _issue(
                                branch,
                                "source_not_authoritative",
                                f"Source {source_key} is unverified, unavailable, or has drifted from the candidate's immutable ledger.",
                            )
                        )

            normalized = _dedupe_issues(issues)
            branch_status = "blocked" if any(item["severity"] == "blocker" for item in normalized) else (
                "warning" if normalized else "passed"
            )
            artifact = {
                "schema_version": VALIDATION_SCHEMA_VERSION,
                "branch": branch,
                "validation_input_hash": validation_input_hash,
                "version_hash": version_hash,
                "status": branch_status,
                "issues": normalized,
                "authoritative": False,
            }
            artifact["artifact_hash"] = artifact_hash(artifact)
            return artifact

        artifacts = await asyncio.gather(*(validate_branch(branch) for branch in VALIDATION_BRANCHES))
        all_issues = _dedupe_issues(issue for artifact in artifacts for issue in artifact["issues"])
        blockers = [issue for issue in all_issues if issue["severity"] == "blocker"]
        warnings = [issue for issue in all_issues if issue["severity"] == "warning"]
        remediation_candidates = [issue for issue in all_issues if issue.get("remediable")]
        report = {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "validation_input_hash": validation_input_hash,
            "version_hash": version_hash,
            "dependency_hashes": dependency_hashes,
            "artifacts": artifacts,
            "blockers": blockers,
            "warnings": warnings,
            "status": "blocked" if blockers else "passed",
            "remediation_candidates": remediation_candidates,
            "remediation_cycle": max(0, int(remediation_cycle)),
            "max_remediation_cycles": int(settings.ARBITRATION_ENGINE_MAX_REMEDIATION_CYCLES),
            "human_review_required": bool(blockers or warnings),
            "remediation_policy": "existing-citation-deduplication-only",
        }
        return self.bounded_remediation(report)

    def bounded_remediation(self, report: Dict[str, Any]) -> Dict[str, Any]:
        current = max(0, int(report.get("remediation_cycle") or 0))
        maximum = max(0, int(report.get("max_remediation_cycles") or 0))
        candidates = list(report.get("remediation_candidates") or [])
        blockers = list(report.get("blockers") or [])
        if candidates and current < maximum:
            route = "remediate"
            termination_reason = None
            human_review_required = bool(blockers)
        elif blockers:
            route = "human_revision"
            termination_reason = "no_safe_automatic_remediation" if not candidates else "remediation_limit_reached"
            human_review_required = True
        else:
            route = "legal_review"
            termination_reason = "remediation_limit_reached" if candidates else None
            human_review_required = bool(report.get("warnings"))
        updated = {
            **report,
            "route": route,
            "termination_reason": termination_reason,
            "human_review_required": human_review_required,
        }
        updated["report_hash"] = artifact_hash({key: value for key, value in updated.items() if key != "report_hash"})
        return updated

    def remediate(self, version: Dict[str, Any], report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a text candidate only when the no-new-facts invariant holds."""
        if report.get("route") != "remediate":
            return None
        before = str(version.get("full_markdown") or "")
        after = _ADJACENT_DUPLICATE_CITATION_RE.sub(r"\1", before)
        if after == before:
            return None
        before_sources = set(_CITATION_RE.findall(before))
        after_sources = set(_CITATION_RE.findall(after))
        before_amounts = set(_AMOUNT_RE.findall(before))
        after_amounts = set(_AMOUNT_RE.findall(after))
        before_dates = set(_DATE_RE.findall(before))
        after_dates = set(_DATE_RE.findall(after))
        if not after_sources.issubset(before_sources) or not after_amounts.issubset(before_amounts) or not after_dates.issubset(before_dates):
            raise ValueError("Bounded remediation attempted to add a source, amount, or date")
        return {
            "full_markdown": after,
            "sections": [
                {
                    **section,
                    "body": _ADJACENT_DUPLICATE_CITATION_RE.sub(r"\1", str(section.get("body") or "")),
                }
                for section in version.get("sections") or []
            ],
            "applied_fixes": ["adjacent_duplicate_citation"],
            "before_content_hash": artifact_hash(before),
            "after_content_hash": artifact_hash(after),
            "source_keys_preserved": True,
            "amounts_preserved": True,
            "dates_preserved": True,
        }
