"""Deterministic, read-only inventory for legacy Bank Guarantee evidence."""

from __future__ import annotations

from typing import Any

from .publication_policy import is_consumable, resolve_canonical_document


#: Parent-level evidence carries no event provenance; extension history does,
#: via `revision_number`. They are separate discovery sources so the operator
#: inventory can never confuse "belongs to this exact event" with "belonged to
#: the guarantee somehow".
PARENT_SOURCE_KIND = "legacy_bg_parent_array"
EXTENSION_SOURCE_KIND = "legacy_bg_extension_history"

CLASSIFICATIONS = (
    "valid",
    "missing_document",
    "deleted_document",
    "blocked_document",
    "cross_organisation",
    "cross_project",
    "invalid_id",
    "duplicate",
    "ambiguous_role",
    "ambiguous_event",
    "missing_event_date",
    "missing_parent",
    "manual_review",
    "resolvable_event",
    "ambiguous_extension_ownership",
    "unresolved_evidence",
    "release_history_inconsistency",
    "missing_event",
)


def _plain(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


async def _document_findings(
    db: Any,
    *,
    raw_document_id: Any,
    organization_id: str,
    project_id: str,
) -> tuple[str, list[str], str]:
    valid_shape = isinstance(raw_document_id, str) and bool(raw_document_id.strip())
    document_id = raw_document_id.strip() if valid_shape else str(raw_document_id or "")
    if not valid_shape:
        return document_id, ["invalid_id"], "invalid_id"
    document = await resolve_canonical_document(db, document_id)
    if document is None:
        return document_id, ["missing_document"], "missing_document"
    if str(document.get("lifecycle_state") or "") == "deleted":
        return document_id, ["deleted_document"], "deleted_document"
    if not is_consumable(document):
        return document_id, ["blocked_document"], "blocked_document"
    document_org = str(document.get("organization_id") or document.get("organizationId") or "")
    document_project = str(document.get("project_id") or document.get("projectId") or "")
    if document_org != organization_id:
        return document_id, ["cross_organisation"], "cross_organisation"
    if document_project != project_id:
        return document_id, ["cross_project"], "cross_project"
    return document_id, ["valid"], "manual_review"



async def _resolve_extension_event(
    db: Any, *, bg_id: str, revision_number: Any
) -> tuple[Any, str]:
    """Map a legacy extension row to EXACTLY one first-class extension event.

    `revision_number` is written onto extension events at creation, so it is a
    real correlator rather than a date heuristic — several extensions can share
    an `extension_date`, but not a revision. Exactly one match resolves; zero is
    `missing_event`; more than one is `ambiguous_event`. Never "pick the first".
    """
    # Match the strictness the BG read path already applies: a bool is not an
    # int here, a float must not truncate into someone else's revision, and a
    # non-numeric value refuses instead of raising for the whole register.
    if isinstance(revision_number, bool) or not isinstance(revision_number, int):
        return None, "ambiguous_event"
    if revision_number < 1:
        return None, "ambiguous_event"
    matches = [
        event
        async for event in db.bank_guarantee_events.find(
            {"bank_guarantee_id": bg_id}
        )
        if _plain(event.get("event_type")) == "extension"
        and isinstance(event.get("revision_number"), int)
        and not isinstance(event.get("revision_number"), bool)
        and event.get("revision_number") == revision_number
    ]
    if not matches:
        return None, "missing_event"
    if len(matches) > 1:
        return None, "ambiguous_event"
    return matches[0], ""


async def classify_legacy_bank_guarantee_links(db: Any) -> dict[str, Any]:
    """Classify legacy parent arrays and extension history without writing."""

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []
    extension_candidates: list[dict[str, Any]] = []

    parents = [row async for row in db.bank_guarantees.find({})]
    parents.sort(key=lambda row: str(row.get("_id") or ""))
    parent_by_id = {str(row.get("_id") or ""): row for row in parents}

    for bg in parents:
        bg_id = str(bg.get("_id") or "")
        organization_id = str(bg.get("organization_id") or "")
        project_id = str(bg.get("project_id") or "")
        seen: set[str] = set()
        occurrences: dict[str, int] = {}
        for raw_document_id in bg.get("linked_document_ids") or []:
            document_id = (
                raw_document_id.strip()
                if isinstance(raw_document_id, str)
                else str(raw_document_id or "")
            )
            occurrences[document_id] = occurrences.get(document_id, 0) + 1
            if document_id in seen:
                findings = ["duplicate"]
                classification = "duplicate"
            else:
                seen.add(document_id)
                document_id, findings, classification = await _document_findings(
                    db,
                    raw_document_id=raw_document_id,
                    organization_id=organization_id,
                    project_id=project_id,
                )
                if findings == ["valid"]:
                    findings.extend(["ambiguous_role", "ambiguous_event", "manual_review"])
            for finding in findings:
                counts[finding] += 1
            candidates.append(
                {
                    "bank_guarantee_id": bg_id,
                    "target_type": "bank_guarantee_event",
                    "target_id": None,
                    "parent_type": "bank_guarantee",
                    "parent_id": bg_id,
                    "source_kind": PARENT_SOURCE_KIND,
                    "legacy_field": "linked_document_ids",
                    "document_id": document_id,
                    "occurrence": occurrences[document_id],
                    "relationship_role": None,
                    "classification": classification,
                    "findings": findings,
                }
            )

    history = [row async for row in db.bg_extension_history.find({})]
    history.sort(
        key=lambda row: (
            str(row.get("bg_id") or ""),
            row.get("revision_number") if isinstance(row.get("revision_number"), int)
            and not isinstance(row.get("revision_number"), bool) else 0,
            str(row.get("_id") or ""),
        )
    )
    for legacy in history:
        bg_id = str(legacy.get("bg_id") or "")
        parent = parent_by_id.get(bg_id)
        findings: list[str] = []
        if parent is None:
            findings.extend(["missing_parent", "ambiguous_extension_ownership"])
        else:
            same_scope = (
                str(legacy.get("organization_id") or parent.get("organization_id") or "")
                == str(parent.get("organization_id") or "")
                and str(legacy.get("project_id") or parent.get("project_id") or "")
                == str(parent.get("project_id") or "")
            )
            if not same_scope:
                findings.append("ambiguous_extension_ownership")
            if not legacy.get("extension_date"):
                findings.append("missing_event_date")
            if same_scope and legacy.get("extension_date") and legacy.get("new_expiry_date"):
                findings.append("resolvable_event")
            else:
                findings.append("manual_review")

        evidence_findings: list[dict[str, Any]] = []
        if parent is not None:
            for raw_document_id in legacy.get("linked_document_ids") or []:
                document_id, doc_findings, classification = await _document_findings(
                    db,
                    raw_document_id=raw_document_id,
                    organization_id=str(parent.get("organization_id") or ""),
                    project_id=str(parent.get("project_id") or ""),
                )
                evidence_findings.append(
                    {
                        "document_id": document_id,
                        "classification": classification,
                        "findings": doc_findings,
                    }
                )
                if doc_findings != ["valid"]:
                    findings.append("unresolved_evidence")

        # Resolve the exact event and emit one common-shaped candidate per
        # Document, so the shared orchestrator can consume BG like any other
        # module while event identity stays mechanically proven here.
        if parent is not None:
            event, event_finding = await _resolve_extension_event(
                db, bg_id=bg_id, revision_number=legacy.get("revision_number")
            )
            if event_finding:
                findings.append(event_finding)
            scope_ok = "ambiguous_extension_ownership" not in findings
            for entry in evidence_findings:
                candidate_findings = list(entry["findings"])
                if candidate_findings == ["valid"]:
                    # Authority is clean; event identity decides migratability.
                    if event is not None and scope_ok:
                        candidate_findings.extend(["ambiguous_role", "manual_review"])
                    else:
                        candidate_findings.append(event_finding or "ambiguous_event")
                        candidate_findings.append("manual_review")
                candidates.append(
                    {
                        "bank_guarantee_id": bg_id,
                        # Scope is the canonical parent's, never the legacy row's.
                        "target_type": "bank_guarantee_event",
                        "target_id": str((event or {}).get("_id") or "") or None,
                        "parent_type": "bank_guarantee",
                        "parent_id": bg_id,
                        "source_kind": EXTENSION_SOURCE_KIND,
                        "legacy_field": "linked_document_ids",
                        "legacy_history_id": str(legacy.get("_id") or ""),
                        "legacy_revision_number": legacy.get("revision_number"),
                        "event_resolution_source": (
                            "extension_revision_number" if event is not None else None
                        ),
                        "document_id": entry["document_id"],
                        "relationship_role": None,
                        "classification": (
                            "manual_review"
                            if "valid" in candidate_findings
                            else entry["classification"]
                        ),
                        "findings": candidate_findings,
                    }
                )

        for finding in dict.fromkeys(findings):
            counts[finding] += 1
        extension_candidates.append(
            {
                "history_id": str(legacy.get("_id") or ""),
                "bank_guarantee_id": bg_id,
                "revision_number": (
                    legacy.get("revision_number")
                    if isinstance(legacy.get("revision_number"), int)
                    and not isinstance(legacy.get("revision_number"), bool)
                    else 0
                ),
                "findings": list(dict.fromkeys(findings)),
                "evidence": evidence_findings,
            }
        )

    release_events = [
        event
        async for event in db.bank_guarantee_events.find({})
        if _plain(event.get("event_type")) == "release"
    ]
    valid_release_keys = {
        (
            str(event.get("bank_guarantee_id") or ""),
            str(event.get("organization_id") or ""),
            str(event.get("project_id") or ""),
        )
        for event in release_events
    }
    release_inconsistencies: list[dict[str, Any]] = []
    for bg in parents:
        bg_id = str(bg.get("_id") or "")
        parent_key = (
            bg_id,
            str(bg.get("organization_id") or ""),
            str(bg.get("project_id") or ""),
        )
        if _plain(bg.get("bg_status")) == "released" and parent_key not in valid_release_keys:
            release_inconsistencies.append(
                {"bank_guarantee_id": bg_id, "kind": "missing_release_event"}
            )
    for event in release_events:
        bg_id = str(event.get("bank_guarantee_id") or "")
        parent = parent_by_id.get(bg_id)
        if parent is None:
            release_inconsistencies.append(
                {"bank_guarantee_id": bg_id, "event_id": str(event.get("_id") or ""), "kind": "missing_parent"}
            )
            continue
        if (
            str(event.get("organization_id") or "")
            != str(parent.get("organization_id") or "")
            or str(event.get("project_id") or "")
            != str(parent.get("project_id") or "")
        ):
            release_inconsistencies.append(
                {"bank_guarantee_id": bg_id, "event_id": str(event.get("_id") or ""), "kind": "scope_mismatch"}
            )
        elif _plain(parent.get("bg_status")) != "released":
            release_inconsistencies.append(
                {"bank_guarantee_id": bg_id, "event_id": str(event.get("_id") or ""), "kind": "parent_not_released"}
            )
    counts["release_history_inconsistency"] = len(release_inconsistencies)

    return {
        "dry_run": True,
        "target_type": "bank_guarantee_event",
        "candidate_count": len(candidates),
        "extension_candidate_count": len(extension_candidates),
        "counts": counts,
        "candidates": candidates,
        "extension_candidates": extension_candidates,
        "release_history_inconsistencies": release_inconsistencies,
    }
