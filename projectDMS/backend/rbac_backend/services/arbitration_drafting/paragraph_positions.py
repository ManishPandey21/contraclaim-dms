"""Authoritative paragraph positions and read-only matrix projections.

Paragraph responses own the substantive response to an opponent pleading.  The
legacy defence/rejoinder collections remain available to readiness, review and
export code, but draft-bound rows in those collections are projections only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, Optional


PARAGRAPH_POSITION_MATRICES = {"defence-matrix", "rejoinder-matrix"}


def projection_matrix_slug(draft_type: Any) -> Optional[str]:
    value = str(getattr(draft_type, "value", draft_type) or "")
    if value == "statement_of_defence":
        return "defence-matrix"
    if value == "rejoinder":
        return "rejoinder-matrix"
    return None


def is_paragraph_position_projection(row: Dict[str, Any]) -> bool:
    return bool(row.get("projection_read_only")) and row.get("projection_source") == "paragraph_response"


def _collection_name(matrix_slug: str) -> str:
    return {
        "defence-matrix": "arbitration_defence_matrix",
        "rejoinder-matrix": "arbitration_rejoinder_matrix",
    }[matrix_slug]


def _review_defaults() -> Dict[str, Any]:
    return {
        "approval_status": "needs_review",
        "human_approval_status": "needs_review",
        "verification_status": "needs_review",
        "readiness_status": "needs_review",
        "review_status": "needs_review",
        "review_completed_roles": [],
        "approved_by": None,
        "approved_at": None,
    }


def _admission_denial(response_type: Any) -> str:
    value = str(getattr(response_type, "value", response_type) or "require_proof")
    return {
        "admit": "admitted",
        "part_admit_part_deny": "partly_admitted",
        "deny": "denied",
    }.get(value, "not_admitted")


def _projection_content(matrix_slug: str, response: Dict[str, Any]) -> Dict[str, Any]:
    number = str(response.get("source_paragraph_number") or "")
    text = response.get("response_text") or response.get("response_reason") or "[Evidence required]"
    common = {
        "source_paragraph_response_id": str(response.get("_id") or ""),
        "source_pleading_document_id": response.get("source_pleading_document_id"),
        "source_pleading_version_id": response.get("source_pleading_version_id"),
        "source_pleading_version_hash": response.get("source_pleading_version_hash"),
        "source_paragraph_text": response.get("source_paragraph_text"),
        "response_type": str(getattr(response.get("response_type"), "value", response.get("response_type")) or "require_proof"),
        "supporting_source_ids": sorted({str(item) for item in response.get("supporting_source_ids") or []}),
        "missing_evidence": list(response.get("missing_evidence") or []),
        "projection_read_only": True,
        "projection_source": "paragraph_response",
    }
    if matrix_slug == "defence-matrix":
        common.update(
            {
                "source_soc_para": number,
                "source_claim_no": number,
                "admission_denial": _admission_denial(response.get("response_type")),
                "defence": text,
                "positive_case": response.get("response_reason"),
                "evidence_ids": common["supporting_source_ids"],
            }
        )
    else:
        common.update(
            {
                "source_sod_para": number,
                "nature_of_defence": response.get("response_reason"),
                "claimant_reply": text,
                "reply_to_counterclaim": response.get("reply_to_counterclaim"),
                "new_matter": bool(response.get("new_matter")),
                "permission_required": bool(response.get("permission_required")),
                "permission_obtained": bool(response.get("permission_obtained")),
                "permission_source_id": response.get("permission_source_id"),
                "permission_approved_by": response.get("permission_approved_by"),
                "permission_approved_at": response.get("permission_approved_at"),
            }
        )
    return common


def _projection_hash(content: Dict[str, Any]) -> str:
    excluded = {"permission_approved_at", "permission_approved_by", "permission_obtained", "permission_source_id"}
    stable = {key: value for key, value in content.items() if key not in excluded}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def sync_paragraph_position_projection(
    db: Any,
    draft: Dict[str, Any],
    response: Dict[str, Any],
    *,
    actor_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Create/update the reviewable projection for one authoritative response."""

    matrix_slug = projection_matrix_slug(draft.get("draft_type"))
    if not matrix_slug or not draft.get("case_id"):
        return None
    collection = db[_collection_name(matrix_slug)]
    response_id = str(response.get("_id") or "")
    draft_id = str(draft.get("_id") or response.get("draft_id") or "")
    number_key = "source_soc_para" if matrix_slug == "defence-matrix" else "source_sod_para"
    number = str(response.get("source_paragraph_number") or "")
    base_query = {"case_id": str(draft["case_id"]), "draft_id": draft_id, "deleted_at": {"$exists": False}}
    existing = await collection.find_one({**base_query, "source_paragraph_response_id": response_id})
    if not existing and number:
        existing = await collection.find_one({**base_query, number_key: number})

    content = _projection_content(matrix_slug, response)
    content_hash = _projection_hash(content)
    now = datetime.utcnow()
    update = {
        **content,
        "projection_hash": content_hash,
        "last_material_editor_id": actor_id,
        "updated_by": actor_id,
        "updated_at": now,
    }
    if not existing or existing.get("projection_hash") != content_hash:
        update.update(_review_defaults())

    if existing:
        return await collection.find_one_and_update(
            {"_id": existing["_id"], "case_id": str(draft["case_id"])},
            {"$set": update},
            return_document=True,
        )

    row = {
        "_id": str(uuid.uuid4()),
        "case_id": str(draft["case_id"]),
        "draft_id": draft_id,
        "organization_id": draft.get("organization_id"),
        "project_id": draft.get("project_id"),
        "contract_id": draft.get("contract_id"),
        "created_by": actor_id,
        "created_at": now,
        **update,
    }
    await collection.insert_one(row)
    return row


async def replace_paragraph_position_projections(
    db: Any,
    draft: Dict[str, Any],
    responses: Iterable[Dict[str, Any]],
    *,
    actor_id: Optional[str],
) -> None:
    """Retire prior projections and rebuild them after an immutable import change."""

    matrix_slug = projection_matrix_slug(draft.get("draft_type"))
    if not matrix_slug or not draft.get("case_id"):
        return
    collection = db[_collection_name(matrix_slug)]
    now = datetime.utcnow()
    await collection.update_many(
        {
            "case_id": str(draft["case_id"]),
            "draft_id": str(draft.get("_id") or ""),
            "projection_source": "paragraph_response",
            "deleted_at": {"$exists": False},
        },
        {"$set": {"deleted_at": now, "deleted_by": actor_id, "updated_at": now}},
    )
    for response in responses:
        await sync_paragraph_position_projection(db, draft, response, actor_id=actor_id)
