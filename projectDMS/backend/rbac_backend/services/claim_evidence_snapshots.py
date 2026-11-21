"""Claim-specific snapshots of the documents behind a head's delay records (owner decision 2026-11-18).

A time head's days rest on hindrances and key-date EOT records, and those records
carry their own supporting documents (JG-4). The records and documents are shared:
other claims and registers use them, so escalation must never lock or rewrite them.
Instead, at escalation, this records what the claim relied on, for this claim and
this case only: for each (head, source record, document) the document id, the
version, the content hash, the source record, the link and who made it, the claim's
contract identity, and when it was captured and by whom.

Captured fields are never rewritten. When the source moves on - a new document
version, different content, the link to the record removed, the head no longer
relying on the record, the document withdrawn from use - the snapshot is marked
``stale`` with the reason. A stale snapshot blocks NEW approval of a pleading in
the case (``assert_current_for_case``) until someone authorised reviews it: re-pin
(a new snapshot of the current version supersedes it) or withdraw (the evidence no
longer supports the head). Approved versions and their audit trails are untouched.

Snapshots name ids and hashes only, never content; readers see a document's entry
only where they may read the document.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from .audit_event_service import AuditEventService
from .publication_policy import document_id_candidates, is_consumable, resolve_canonical_document

COLLECTION = "claim_evidence_snapshots"
SCHEMA_VERSION = 1

#: Head link target type -> the entity type its documents are linked under.
DELAY_SOURCE_ENTITY: Dict[str, str] = {
    "hindrance": "delay_event",
    "eot_submission": "eot_submission",
    "eot_determination": "eot_determination",
}

STALE_CODE = "delay_evidence_stale"
STALE_MESSAGE = (
    "Documents behind this case's delay records have changed since the claim was escalated. "
    "Someone who can manage the claim must review them (re-pin or withdraw) before a pleading is approved."
)


def _snapshot_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:40]


async def _document(db: Any, document_id: str) -> Optional[Dict[str, Any]]:
    return await resolve_canonical_document(db, document_id)


async def _version(db: Any, document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    version_id = document.get("current_version_id")
    if not version_id:
        return None
    return await db.document_versions.find_one(
        {"_id": {"$in": document_id_candidates(str(version_id))}}
    )


def _content_hash(document: Dict[str, Any], version: Optional[Dict[str, Any]]) -> Optional[str]:
    return str((version or {}).get("sha256") or document.get("sha256") or "") or None


def _in_scope(document: Dict[str, Any], claim: Dict[str, Any]) -> bool:
    org = str(document.get("organization_id") or document.get("organizationId") or "")
    project = str(document.get("project_id") or document.get("projectId") or "")
    return org == str(claim.get("organization_id") or "") and project == str(claim.get("project_id") or "")


async def delay_sources(db: Any, claim: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every (head, source record, document) the claim's delay assessment relies on, with provenance."""
    from .claim_delay_lab import ClaimDelayLab

    lab = ClaimDelayLab(db, None)
    organization_id = str(claim.get("organization_id") or "")
    project_id = str(claim.get("project_id") or "")
    out: List[Dict[str, Any]] = []
    for link in await lab.links_for_claim(str(claim["_id"])):
        entity = DELAY_SOURCE_ENTITY.get(link.target_type)
        if entity is None:
            continue
        async for row in db.entity_document_links.find(
            {"organization_id": organization_id, "project_id": project_id, "target_type": entity,
             "target_id": str(link.target_id), "removed_at": None}
        ):
            out.append({
                "head_id": link.head_id, "head_link_id": link.id, "source_type": link.target_type,
                "source_id": str(link.target_id), "document_id": str(row.get("document_id") or ""),
                "provenance": {"via": "link", "entity_type": entity, "link_id": str(row.get("_id")),
                               "relationship_role": row.get("relationship_role"),
                               "linked_by": row.get("created_by"), "linked_at": row.get("created_at")},
            })
        record = await lab.load_target(link.target_type, link.target_id)
        for legacy in (record or {}).get("linked_document_ids") or []:
            out.append({
                "head_id": link.head_id, "head_link_id": link.id, "source_type": link.target_type,
                "source_id": str(link.target_id), "document_id": str(legacy or ""),
                "provenance": {"via": "register_field", "entity_type": entity, "link_id": None,
                               "relationship_role": None, "linked_by": None, "linked_at": None},
            })
    return out


async def capture(db: Any, claim: Dict[str, Any], case_id: str, actor_id: Optional[str]) -> List[Dict[str, Any]]:
    """Snapshot what the claim's delay records rest on, for this case. Idempotent; safe concurrently."""
    captured: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for source in await delay_sources(db, claim):
        document = await _document(db, source["document_id"])
        if not document or not is_consumable(document) or not _in_scope(document, claim):
            continue  # escalation's evidence checks decide what is carried; nothing out of scope is pinned
        version = await _version(db, document)
        document_id = str(document.get("_id"))
        version_id = str(document.get("current_version_id") or "") or None
        row = {
            "_id": _snapshot_id(claim["_id"], case_id, source["head_id"], source["source_type"],
                                source["source_id"], document_id, version_id),
            "schema_version": SCHEMA_VERSION,
            "claim_id": str(claim["_id"]),
            "case_id": str(case_id),
            "organization_id": claim.get("organization_id"),
            "project_id": claim.get("project_id"),
            "head_id": source["head_id"],
            "head_link_id": source["head_link_id"],
            "source_record": {"type": source["source_type"], "id": source["source_id"]},
            "document_id": document_id,
            "document_version_id": version_id,
            "content_hash": _content_hash(document, version),
            "contract_id": claim.get("contract_id"),
            "document_contract_id": document.get("contract_id"),
            "provenance": source["provenance"],
            "captured_at": now,
            "captured_by": actor_id,
            "status": "current",
        }
        try:
            await db[COLLECTION].insert_one(row)
        except DuplicateKeyError:
            row = await db[COLLECTION].find_one({"_id": row["_id"]}) or row
        captured.append(row)
    return captured


async def _drift(db: Any, snapshot: Dict[str, Any]) -> Optional[str]:
    """Why ``snapshot`` no longer matches its source, or None."""
    document = await _document(db, snapshot["document_id"])
    if not document:
        return "the document no longer exists"
    if not is_consumable(document):
        return "the document has been withdrawn from use (quarantined or deleted)"
    if str(document.get("current_version_id") or "") != str(snapshot.get("document_version_id") or ""):
        return "the document has a new version"
    current_hash = _content_hash(document, await _version(db, document))
    if snapshot.get("content_hash") and current_hash and current_hash != snapshot["content_hash"]:
        return "the document's content has changed"
    provenance = snapshot.get("provenance") or {}
    if provenance.get("via") == "link":
        link = await db.entity_document_links.find_one({"_id": {"$in": document_id_candidates(provenance.get("link_id"))}})
        if not link or link.get("removed_at"):
            return "the document's link to the delay record has been removed"
    head_link = await db.claim_head_links.find_one(
        {"claim_id": snapshot["claim_id"], "head_id": snapshot["head_id"], "status": "active",
         "target_type": snapshot["source_record"]["type"], "target_id": snapshot["source_record"]["id"]}
    )
    if not head_link:
        return "the head no longer relies on this delay record"
    return None


async def refresh(db: Any, *, case_id: Optional[str] = None, claim_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Mark every current snapshot whose source has moved on as stale; return them all."""
    query: Dict[str, Any] = {}
    if case_id:
        query["case_id"] = str(case_id)
    if claim_id:
        query["claim_id"] = str(claim_id)
    rows = await db[COLLECTION].find(query).sort("captured_at", 1).to_list(length=None)
    for row in rows:
        if row.get("status") != "current":
            continue
        reason = await _drift(db, row)
        if not reason:
            continue
        now = datetime.utcnow()
        result = await db[COLLECTION].update_one(
            {"_id": row["_id"], "status": "current"},
            {"$set": {"status": "stale", "stale_reason": reason, "stale_detected_at": now}},
        )
        if getattr(result, "modified_count", 0):
            row.update(status="stale", stale_reason=reason, stale_detected_at=now)
            await AuditEventService(db).emit(
                action="claim.evidence_snapshot.stale", actor_id=None, resource_type="claim",
                resource_id=row["claim_id"], organization_id=row.get("organization_id"),
                project_id=row.get("project_id"),
                metadata={"snapshot_id": row["_id"], "case_id": row["case_id"], "reason": reason},
            )
        else:
            row.update(await db[COLLECTION].find_one({"_id": row["_id"]}) or {})
    return rows


async def assert_current_for_case(db: Any, case_id: str) -> None:
    """Refuse a new pleading approval in a case whose delay evidence has moved on."""
    stale = [row for row in await refresh(db, case_id=case_id) if row.get("status") == "stale"]
    if stale:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": STALE_CODE, "user_message": STALE_MESSAGE, "detail": STALE_MESSAGE,
                    "stale_snapshot_ids": [row["_id"] for row in stale]},
        )


async def review(
    db: Any, claim: Dict[str, Any], snapshot_id: str, action: str, reason: str, actor_id: Optional[str]
) -> Dict[str, Any]:
    """Re-pin a stale snapshot to the source as it now is, or withdraw it. Audited; nothing is deleted."""
    snapshot = await db[COLLECTION].find_one({"_id": snapshot_id, "claim_id": str(claim["_id"])})
    if not snapshot:
        raise HTTPException(status_code=404, detail="Evidence snapshot not found")
    if snapshot.get("status") != "stale":
        raise HTTPException(
            status_code=409,
            detail={"code": "evidence_snapshot_not_stale", "user_message": "Only a snapshot whose source has changed is reviewed.",
                    "detail": None},
        )
    now = datetime.utcnow()
    replacement = None
    if action == "repin":
        source = {"head_id": snapshot["head_id"], "source_type": snapshot["source_record"]["type"],
                  "source_id": snapshot["source_record"]["id"], "document_id": snapshot["document_id"]}
        current = [
            item for item in await delay_sources(db, claim)
            if all(item[key] == source[key] for key in source)
        ]
        document = await _document(db, snapshot["document_id"])
        if not current or not document or not is_consumable(document) or not _in_scope(document, claim):
            raise HTTPException(
                status_code=409,
                detail={"code": "evidence_snapshot_unavailable",
                        "user_message": "The document no longer supports this delay record, so it cannot be re-pinned. Withdraw it instead.",
                        "detail": None},
            )
        version = await _version(db, document)
        version_id = str(document.get("current_version_id") or "") or None
        replacement = {
            **{key: snapshot[key] for key in ("claim_id", "case_id", "organization_id", "project_id", "head_id",
                                               "head_link_id", "source_record", "document_id", "contract_id")},
            "_id": _snapshot_id(snapshot["claim_id"], snapshot["case_id"], snapshot["head_id"],
                                snapshot["source_record"]["type"], snapshot["source_record"]["id"],
                                snapshot["document_id"], version_id, "repin", snapshot["_id"]),
            "schema_version": SCHEMA_VERSION,
            "document_version_id": version_id,
            "content_hash": _content_hash(document, version),
            "document_contract_id": document.get("contract_id"),
            "provenance": current[0]["provenance"],
            "captured_at": now,
            "captured_by": actor_id,
            "status": "current",
            "repinned_from": snapshot["_id"],
        }
    result = await db[COLLECTION].update_one(
        {"_id": snapshot_id, "status": "stale"},
        {"$set": {"status": "superseded" if replacement else "withdrawn", "reviewed_by": actor_id,
                  "reviewed_at": now, "review_reason": reason,
                  "superseded_by": replacement["_id"] if replacement else None}},
    )
    if not getattr(result, "modified_count", 0):
        raise HTTPException(
            status_code=409,
            detail={"code": "evidence_snapshot_reviewed", "user_message": "Someone else has just reviewed this snapshot.",
                    "detail": None},
        )
    if replacement:
        await db[COLLECTION].insert_one(replacement)
    await AuditEventService(db).emit(
        action=f"claim.evidence_snapshot.{'repinned' if replacement else 'withdrawn'}", actor_id=actor_id,
        resource_type="claim", resource_id=str(claim["_id"]), organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        metadata={"snapshot_id": snapshot_id, "replacement_id": (replacement or {}).get("_id"), "reason": reason},
    )
    return replacement or await db[COLLECTION].find_one({"_id": snapshot_id})


async def withdraw_for_case(db: Any, claim_id: str, case_id: str, reason: str, actor_id: Optional[str]) -> int:
    """An undone escalation: its snapshots stop counting; they are kept for the record."""
    result = await db[COLLECTION].update_many(
        {"claim_id": str(claim_id), "case_id": str(case_id), "status": {"$in": ["current", "stale"]}},
        {"$set": {"status": "withdrawn", "reviewed_by": actor_id, "reviewed_at": datetime.utcnow(),
                  "review_reason": reason}},
    )
    return int(getattr(result, "modified_count", 0))


async def readable_documents(db: Any, actor: Any, rows: List[Dict[str, Any]]) -> Set[str]:
    """The snapshot documents ``actor`` may read, each checked in its delay record's context.

    Asked per document, not per current link: a document unlinked since escalation is
    still one the reader may or may not open (D-25), and saying so is not a leak."""
    from fastapi import HTTPException as _HTTPException

    from .document_relationship_service import DocumentRelationshipError, DocumentRelationshipService

    service = DocumentRelationshipService(db)
    by_source: Dict[tuple, Set[str]] = {}
    for row in rows:
        source = row.get("source_record") or {}
        entity = DELAY_SOURCE_ENTITY.get(str(source.get("type") or ""))
        if entity and row.get("document_id"):
            by_source.setdefault((entity, str(source.get("id"))), set()).add(str(row["document_id"]))
    readable: Set[str] = set()
    for (entity, source_id), wanted in by_source.items():
        try:
            ids = await service.authorized_document_ids(actor, entity, source_id, legacy_document_ids=sorted(wanted))
        except (DocumentRelationshipError, _HTTPException):
            continue
        readable.update(str(item) for item in ids if str(item) in wanted)
    return readable


def visible(rows: List[Dict[str, Any]], readable: Set[str]) -> List[Dict[str, Any]]:
    """A reader's view: a document they may not read is a restricted entry, with no id, version or hash."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        entry = {key: row.get(key) for key in (
            "_id", "head_id", "status", "stale_reason", "stale_detected_at", "captured_at", "captured_by",
            "repinned_from", "superseded_by", "reviewed_by", "reviewed_at", "review_reason", "case_id")}
        if row.get("document_id") in readable:
            entry.update({key: row.get(key) for key in (
                "document_id", "document_version_id", "content_hash", "source_record", "contract_id",
                "document_contract_id", "provenance")})
        else:
            entry.update({"restricted": True, "document_id": None})
        out.append(entry)
    return out


__all__ = [
    "COLLECTION", "STALE_CODE", "assert_current_for_case", "capture", "delay_sources", "readable_documents", "refresh", "review",
    "visible", "withdraw_for_case",
]
