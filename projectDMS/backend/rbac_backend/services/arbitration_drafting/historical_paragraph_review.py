"""Conservative review of historical defence/rejoinder paragraph rows.

Legacy draft-bound matrix rows predate the authoritative
``arbitration_paragraph_responses`` model. This module never guesses across
multiple candidates. Audit is read-only by default; resolution is allowed only
when an explicit response ID or a unique normalized paragraph number identifies
exactly one authoritative response.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, status

from .paragraph_positions import sync_paragraph_position_projection
from .repository import _collect


HISTORICAL_MATRIX_COLLECTIONS = {
    "defence-matrix": "arbitration_defence_matrix",
    "rejoinder-matrix": "arbitration_rejoinder_matrix",
}


def _normalized_paragraph_number(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "", text)
    return re.sub(r"^0+(?=\d)", "", normalized)


def _candidate_number(row: Dict[str, Any], matrix_slug: str) -> str:
    keys = (
        ("source_soc_para", "source_claim_no", "source_paragraph_number", "paragraph_number")
        if matrix_slug == "defence-matrix"
        else ("source_sod_para", "source_paragraph_number", "paragraph_number")
    )
    for key in keys:
        value = _normalized_paragraph_number(row.get(key))
        if value:
            return value
    return ""


def classify_historical_row(
    row: Dict[str, Any],
    responses: Iterable[Dict[str, Any]],
    *,
    matrix_slug: str,
) -> Dict[str, Any]:
    response_rows = list(responses)
    explicit_id = str(
        row.get("source_paragraph_response_id")
        or row.get("paragraph_response_id")
        or ""
    ).strip()
    if explicit_id:
        explicit = [
            response for response in response_rows
            if str(response.get("_id") or "") == explicit_id
        ]
        if len(explicit) == 1:
            return {
                "classification": "unambiguous_explicit",
                "candidate_response_ids": [explicit_id],
                "resolvable": True,
            }
        return {
            "classification": "unmatched_explicit_reference",
            "candidate_response_ids": [],
            "resolvable": False,
        }

    number = _candidate_number(row, matrix_slug)
    if not number:
        return {
            "classification": "missing_paragraph_identity",
            "candidate_response_ids": [],
            "resolvable": False,
        }
    matches = [
        response
        for response in response_rows
        if _normalized_paragraph_number(response.get("source_paragraph_number")) == number
    ]
    ids = sorted(str(response.get("_id") or "") for response in matches if response.get("_id"))
    if len(ids) == 1:
        return {
            "classification": "unambiguous_paragraph_number",
            "candidate_response_ids": ids,
            "resolvable": True,
        }
    if len(ids) > 1:
        return {
            "classification": "ambiguous_multiple_responses",
            "candidate_response_ids": ids,
            "resolvable": False,
        }
    return {
        "classification": "unmatched_paragraph_number",
        "candidate_response_ids": [],
        "resolvable": False,
    }


async def audit_historical_paragraph_positions(
    db: Any,
    *,
    case_id: Optional[str] = None,
    apply_review_labels: bool = False,
) -> Dict[str, Any]:
    findings = []
    counts: Dict[str, int] = {}
    for matrix_slug, collection_name in HISTORICAL_MATRIX_COLLECTIONS.items():
        query: Dict[str, Any] = {
            "draft_id": {"$type": "string"},
            "deleted_at": {"$exists": False},
            "$or": [
                {"projection_source": {"$exists": False}},
                {"projection_source": {"$ne": "paragraph_response"}},
            ],
        }
        if case_id:
            query["case_id"] = str(case_id)
        rows = await _collect(db[collection_name].find(query))
        for row in rows:
            draft_id = str(row.get("draft_id") or "")
            responses = await _collect(
                db.arbitration_paragraph_responses.find(
                    {"draft_id": draft_id, "deleted_at": {"$exists": False}}
                )
            )
            result = classify_historical_row(row, responses, matrix_slug=matrix_slug)
            classification = str(result["classification"])
            counts[classification] = counts.get(classification, 0) + 1
            finding = {
                "matrix": matrix_slug,
                "row_id": str(row.get("_id") or ""),
                "case_id": str(row.get("case_id") or ""),
                "draft_id": draft_id,
                **result,
            }
            findings.append(finding)
            if apply_review_labels:
                now = datetime.now(timezone.utc)
                await db[collection_name].update_one(
                    {"_id": row.get("_id"), "draft_id": draft_id},
                    {
                        "$set": {
                            "historical_review_status": (
                                "candidate_unambiguous"
                                if result["resolvable"]
                                else "needs_legal_review"
                            ),
                            "historical_review_classification": classification,
                            "historical_candidate_response_ids": result["candidate_response_ids"],
                            "historical_reviewed_at": now,
                            "historical_resolution_requires_legal_review": True,
                        }
                    },
                )
    findings.sort(key=lambda item: (item["matrix"], item["case_id"], item["draft_id"], item["row_id"]))
    return {
        "schema_version": 1,
        "mode": "labels_applied" if apply_review_labels else "dry_run",
        "total": len(findings),
        "resolvable": sum(1 for finding in findings if finding["resolvable"]),
        "requires_legal_review": sum(1 for finding in findings if not finding["resolvable"]),
        "counts": {key: counts[key] for key in sorted(counts)},
        "findings": findings,
    }


async def resolve_historical_paragraph_position(
    db: Any,
    *,
    matrix_slug: str,
    row_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    collection_name = HISTORICAL_MATRIX_COLLECTIONS.get(matrix_slug)
    if not collection_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown paragraph-position matrix")
    row = await db[collection_name].find_one(
        {"_id": row_id, "deleted_at": {"$exists": False}}
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Historical matrix row not found")
    draft_id = str(row.get("draft_id") or "")
    draft = await db.arbitration_drafts.find_one(
        {"_id": draft_id, "case_id": row.get("case_id"), "deleted_at": {"$exists": False}}
    )
    if not draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Historical row has no current case-linked draft")
    responses = await _collect(
        db.arbitration_paragraph_responses.find(
            {"draft_id": draft_id, "deleted_at": {"$exists": False}}
        )
    )
    classification = classify_historical_row(row, responses, matrix_slug=matrix_slug)
    if not classification["resolvable"] or len(classification["candidate_response_ids"]) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Historical paragraph position is ambiguous and requires legal review",
                "classification": classification["classification"],
                "candidate_response_ids": classification["candidate_response_ids"],
            },
        )
    response_id = classification["candidate_response_ids"][0]
    response = next(item for item in responses if str(item.get("_id") or "") == response_id)
    projection = await sync_paragraph_position_projection(
        db,
        draft,
        response,
        actor_id=actor_id,
    )
    if not projection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft type cannot own a paragraph-position projection")
    now = datetime.now(timezone.utc)
    resolved = await db[collection_name].find_one_and_update(
        {"_id": projection["_id"], "draft_id": draft_id},
        {
            "$set": {
                "historical_review_status": "resolved_unambiguous",
                "historical_review_classification": classification["classification"],
                "historical_resolved_by": actor_id,
                "historical_resolved_at": now,
                "historical_resolution_requires_legal_review": False,
            }
        },
        return_document=True,
    )
    return resolved or projection
