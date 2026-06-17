"""Evidence bundle / data-room export (Phase 4 / Module 4).

Assembles a single ZIP for a claim — the concrete arbitration deliverable:

    claim.json            the claim record
    audit-trail.csv       the claim's ordered audit events (reuses M7 export)
    documents/<file>      every linked, in-scope correspondence document
    manifest.json         contents + provenance + per-document status

``build_evidence_zip`` is pure (bytes in → bytes out) so it is trivially
testable; ``EvidenceBundleService`` adds DB/storage gathering and the in-scope
document filter. Only documents in the *same tenant scope* as the claim are ever
included.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from .audit_export import audit_events_to_csv

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "").strip()).strip("._")
    return cleaned or fallback


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, default=str, indent=2, sort_keys=True).encode("utf-8")


def build_evidence_zip(
    *,
    claim: Dict[str, Any],
    audit_csv: str,
    documents: List[Dict[str, Any]],
    generated_by: Optional[str] = None,
    generated_at: Optional[datetime] = None,
    audit_event_count: int = 0,
) -> bytes:
    """Build the evidence ZIP. ``documents`` items: {name, data: bytes, status, ...meta}."""
    generated_at = generated_at or datetime.utcnow()
    claim_id = str(claim.get("_id") or claim.get("id") or "claim")

    manifest_docs: List[Dict[str, Any]] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("claim.json", _json_bytes(claim))
        archive.writestr("audit-trail.csv", audit_csv or "")

        used_names: set[str] = set()
        for index, doc in enumerate(documents):
            entry = {
                "document_id": doc.get("document_id"),
                "filename": doc.get("name"),
                "status": doc.get("status", "included"),
                "bytes": len(doc.get("data") or b""),
            }
            if doc.get("data") is not None:
                base = _safe_filename(doc.get("name") or "", f"document-{index + 1}")
                name = base
                suffix = 1
                while name in used_names:
                    suffix += 1
                    name = f"{suffix}_{base}"
                used_names.add(name)
                archive.writestr(f"documents/{name}", doc["data"])
                entry["archived_as"] = f"documents/{name}"
            manifest_docs.append(entry)

        manifest = {
            "claim_id": claim_id,
            "claim_ref": claim.get("claim_ref"),
            "claim_title": claim.get("title"),
            "organization_id": claim.get("organization_id"),
            "project_id": claim.get("project_id"),
            "generated_at": generated_at.isoformat() + "Z",
            "generated_by": generated_by,
            "audit_event_count": audit_event_count,
            "document_count": len([d for d in manifest_docs if d.get("archived_as")]),
            "documents": manifest_docs,
        }
        archive.writestr("manifest.json", _json_bytes(manifest))

    return buffer.getvalue()


class EvidenceBundleService:
    def __init__(self, db: Any = None, file_object_service: Any = None) -> None:
        self.db = db
        self._file_object_service = file_object_service

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        from ..core.database import get_database

        return await get_database()

    def _file_service(self):
        if self._file_object_service is not None:
            return self._file_object_service
        from .file_object_service import FileObjectService

        return FileObjectService()

    async def _gather_documents(self, claim: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch linked documents, keeping only those in the claim's tenant scope."""
        db = await self._get_db()
        claim_org = claim.get("organization_id")
        results: List[Dict[str, Any]] = []
        for doc_id in claim.get("linked_document_ids") or []:
            try:
                document = await db.documents.find_one({"_id": doc_id})
            except Exception:  # pragma: no cover - defensive
                document = None
            if not document:
                results.append({"document_id": doc_id, "name": None, "status": "not_found", "data": None})
                continue
            # In-scope guard: never bundle another tenant's document.
            if claim_org and document.get("organization_id") not in (None, claim_org):
                results.append({"document_id": doc_id, "name": document.get("filename"), "status": "out_of_scope", "data": None})
                continue
            data = await self._read_bytes(document)
            results.append(
                {
                    "document_id": doc_id,
                    "name": document.get("filename") or f"{doc_id}",
                    "status": "included" if data is not None else "unavailable",
                    "data": data,
                }
            )
        return results

    async def _read_bytes(self, document: Dict[str, Any]) -> Optional[bytes]:
        file_object_id = document.get("file_object_id")
        if not file_object_id:
            return None
        try:
            path = await self._file_service().materialize_to_temp(file_object_id)
            try:
                return path.read_bytes()
            finally:
                try:
                    path.unlink()
                except OSError:  # pragma: no cover - best effort cleanup
                    pass
        except Exception:  # pragma: no cover - storage best-effort
            logger.warning("Evidence bundle: could not read document %s", document.get("_id"))
            return None

    async def build(
        self,
        claim: Dict[str, Any],
        audit_events: List[Dict[str, Any]],
        *,
        generated_by: Optional[str] = None,
    ) -> bytes:
        documents = await self._gather_documents(claim)
        return build_evidence_zip(
            claim=claim,
            audit_csv=audit_events_to_csv(audit_events),
            documents=documents,
            generated_by=generated_by,
            audit_event_count=len(audit_events or []),
        )
