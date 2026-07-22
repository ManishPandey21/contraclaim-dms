from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Sequence

from fastapi import HTTPException, status

from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id, _is_ready_row
from .matrix_registry import MATRIX_COLLECTIONS
from .repository import _collect, _jsonable
from .workflow_repository import ArbitrationWorkflowRepository, artifact_hash
from ...core.config import settings


ANALYSIS_BRANCHES = (
    "document_understanding",
    "chronology",
    "clause_interpretation",
    "jurisdiction",
    "limitation",
    "pre_arbitration_requirements",
    "notices",
    "pleading_position",
    "quantum",
    "expert_alignment",
)

ANALYSIS_NODE_BRANCHES = {
    "analyze_documents": ("document_understanding",),
    "analyze_chronology": ("chronology",),
    "analyze_clause_jurisdiction_notice": (
        "clause_interpretation",
        "jurisdiction",
        "limitation",
        "pre_arbitration_requirements",
        "notices",
    ),
    "analyze_pleading_position": ("pleading_position",),
    "analyze_quantum_expert": ("quantum", "expert_alignment"),
}

ANALYSIS_BRANCH_MATRICES = {
    "document_understanding": ("document-index",),
    "chronology": ("chronology-matrix",),
    "clause_interpretation": ("clause-matrix",),
    "jurisdiction": ("jurisdiction-matrix",),
    "limitation": ("claim-matrix", "defence-matrix", "counterclaim-matrix", "jurisdiction-matrix"),
    "pre_arbitration_requirements": ("jurisdiction-matrix", "notice-compliance"),
    "notices": ("notice-compliance",),
    "pleading_position": ("issue-matrix",),
    "quantum": ("quantum-annexures",),
    "expert_alignment": ("expert-alignment",),
}

ANALYSIS_FINDING_FIELDS = {
    "document_understanding": ("title", "document_type", "exhibit_id", "relevance_note"),
    "chronology": ("date", "event", "document_ref", "significance"),
    "clause_interpretation": ("clause_number", "clause_text_excerpt", "interpretation"),
    "jurisdiction": ("check_type", "scope_status", "basis"),
    "limitation": ("limitation_status", "limitation_base_date", "limitation_expiry_date", "basis"),
    "pre_arbitration_requirements": ("step", "required", "compliance_status", "contractual_requirement"),
    "notices": ("notice_type", "notice_date", "compliance_status", "contractual_requirement"),
    "pleading_position": ("issue", "claim_head", "defence", "claimant_reply", "admission_denial"),
    "quantum": ("cost_head", "amount", "currency", "calculation_type"),
    "expert_alignment": ("expert_type", "claim_no", "alignment_status", "verified_amount", "contradictions"),
}

DOCUMENT_SIGNAL_TERMS = (
    "notice", "delay", "extension of time", "clause", "jurisdiction",
    "limitation", "quantum", "payment", "expert", "counterclaim",
)

PLEADING_POSITION_MATRICES = {
    "statement_of_claim": ("claim-matrix",),
    "statement_of_defence": ("defence-matrix",),
    "counterclaim": ("counterclaim-matrix",),
    "rejoinder": ("rejoinder-matrix", "defence-matrix"),
}

PLEADING_MATRIX_REQUIREMENTS = {
    "statement_of_claim": (
        "document-index", "chronology-matrix", "clause-matrix", "issue-matrix", "claim-matrix",
        "jurisdiction-matrix", "notice-compliance", "quantum-annexures", "expert-alignment",
    ),
    "statement_of_defence": (
        "document-index", "chronology-matrix", "defence-matrix", "issue-matrix", "jurisdiction-matrix",
        "quantum-annexures",
    ),
    "counterclaim": (
        "document-index", "chronology-matrix", "clause-matrix", "issue-matrix", "counterclaim-matrix",
        "jurisdiction-matrix", "notice-compliance", "quantum-annexures",
    ),
    "rejoinder": ("document-index", "chronology-matrix", "rejoinder-matrix", "defence-matrix", "issue-matrix"),
}

OPPONENT_REQUIREMENTS = {
    "statement_of_defence": ("statement_of_claim",),
    "rejoinder": ("statement_of_claim", "statement_of_defence"),
}


def _source_revision_ids(row: Dict[str, Any]) -> List[str]:
    revisions = {
        str(value).strip()
        for key in (
            "source_revision_id",
            "source_version_id",
            "source_document_version_id",
            "source_pleading_version_id",
            "current_version_id",
            "chronology_event_id",
            "clause_source_id",
        )
        for value in [row.get(key)]
        if value is not None and str(value).strip()
    }
    for key in ("source_revision_ids", "evidence_ids", "supporting_source_ids", "source_ids"):
        values = row.get(key) or []
        if isinstance(values, (str, int)):
            values = [values]
        revisions.update(str(value).strip() for value in values if str(value).strip())
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        discriminator = (
            row.get("source_hash")
            or row.get("source_version_id")
            or row.get("current_version_id")
            or row.get("revision")
            or "unversioned"
        )
        revisions.add(f"{source_id}@{discriminator}")
    return sorted(revisions)


def _evidence_status(row: Dict[str, Any], source_revision_ids: Sequence[str]) -> str:
    if not source_revision_ids:
        return "missing"
    if not _is_ready_row(row) or any(str(value).endswith("@unversioned") for value in source_revision_ids):
        return "needs_review"
    return "supported"


def _canonical_row(matrix: str, row: Dict[str, Any]) -> Dict[str, Any]:
    source_revision_ids = _source_revision_ids(row)
    payload_hash = artifact_hash(
        {key: value for key, value in row.items() if key not in {"approval_log", "review_comments", "review_assignments"}}
    )
    return {
        "matrix": matrix,
        "row_id": str(row.get("_id") or ""),
        "status": str(row.get("approval_status") or row.get("human_approval_status") or "needs_review"),
        "revision": int(row.get("revision") or 1),
        "source_id": row.get("source_id"),
        "source_hash": row.get("source_hash"),
        "source_revision_ids": source_revision_ids,
        "evidence_status": _evidence_status(row, source_revision_ids),
        "matrix_row_revision_id": f"{matrix}:{row.get('_id') or 'unassigned'}:{payload_hash}",
        "updated_at": row.get("updated_at"),
        "payload_hash": payload_hash,
    }


def deterministic_merge(branch_results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    unique: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for branch in branch_results:
        for row in branch.get("rows") or []:
            canonical = _jsonable(row)
            unique[(str(canonical.get("matrix")), str(canonical.get("row_id")), str(canonical.get("payload_hash")))] = canonical
    rows = list(unique.values())
    rows.sort(key=lambda row: (str(row.get("matrix") or ""), str(row.get("row_id") or ""), str(row.get("payload_hash") or "")))
    return {"rows": rows, "revision_hash": artifact_hash(rows), "revision_set_id": f"mrs_{artifact_hash(rows)[:24]}"}


def _opponent_paragraphs(version: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = str(version.get("full_markdown") or version.get("markdown") or version.get("text") or "").strip()
    if not raw and version.get("sections"):
        sections = version.get("sections")
        values = sections.values() if isinstance(sections, dict) else sections
        raw = "\n\n".join(
            str(item.get("markdown") or item.get("text") or item.get("content") or item)
            if isinstance(item, dict)
            else str(item)
            for item in values
        )
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", raw) if chunk.strip()]
    paragraphs: List[Dict[str, str]] = []
    for index, chunk in enumerate(chunks, start=1):
        match = re.match(r"^(?:#{1,6}\s*)?(\d+(?:\.\d+)*)[\).\s-]+(.+)$", chunk, flags=re.S)
        number = match.group(1) if match else str(index)
        text = re.sub(r"\s+", " ", match.group(2) if match else chunk).strip()[:8000]
        paragraphs.append({"number": number, "text": text, "paragraph_hash": artifact_hash({"number": number, "text": text})})
    return paragraphs


def _branch_matrices(branch: str, pleading_type: str) -> tuple[str, ...]:
    matrices = list(ANALYSIS_BRANCH_MATRICES[branch])
    if branch == "pleading_position":
        matrices.extend(PLEADING_POSITION_MATRICES.get(pleading_type, ()))
    return tuple(dict.fromkeys(matrices))


class ArbitrationWorkflowDomain:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.repository = ArbitrationWorkflowRepository(db)

    @asynccontextmanager
    async def _fanout_slot(self, run: Dict[str, Any], branch: str):
        """Distributed bounded-concurrency lease scoped to tenant and project."""

        collection = getattr(self.db, "arbitration_analysis_leases", None)
        if collection is None:
            # Lightweight test doubles may omit the operational collection.
            yield
            return
        scope_hash = artifact_hash(
            {
                "organization_id": run.get("organization_id"),
                "project_id": run.get("project_id"),
            }
        )[:32]
        limit = max(1, int(settings.ARBITRATION_ENGINE_MAX_FANOUT_PER_SCOPE))
        owner = f"{run.get('_id')}:{branch}:{uuid.uuid4()}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30.0
        claimed_id = None
        while claimed_id is None:
            now = datetime.now(timezone.utc)
            for slot in range(limit):
                lease_id = f"arbitration-fanout:{scope_hash}:{slot}"
                await collection.update_one(
                    {"_id": lease_id},
                    {
                        "$setOnInsert": {
                            "scope_hash": scope_hash,
                            "slot": slot,
                            "owner": None,
                            "lease_expires_at": now,
                        }
                    },
                    upsert=True,
                )
                claimed = await collection.find_one_and_update(
                    {
                        "_id": lease_id,
                        "$or": [
                            {"owner": None},
                            {"lease_expires_at": {"$lte": now}},
                        ],
                    },
                    {
                        "$set": {
                            "owner": owner,
                            "run_id": str(run.get("_id")),
                            "branch": branch,
                            "lease_expires_at": now + timedelta(minutes=2),
                            "updated_at": now,
                        }
                    },
                    return_document=True,
                )
                if claimed and claimed.get("owner") == owner:
                    claimed_id = lease_id
                    break
            if claimed_id is None:
                if loop.time() >= deadline:
                    raise TimeoutError("Tenant-scoped arbitration analysis concurrency lease timed out")
                await asyncio.sleep(0.02)
        try:
            yield
        finally:
            await collection.update_one(
                {"_id": claimed_id, "owner": owner},
                {
                    "$set": {
                        "owner": None,
                        "lease_expires_at": datetime.now(timezone.utc),
                        "released_at": datetime.now(timezone.utc),
                    }
                },
            )

    async def capture_opponent_snapshot(
        self,
        run_id: str,
        case_id: str,
        pleading_type: str,
        opponent_draft_id: Any,
        opponent_version_id: Any,
        opponent_pleadings: Iterable[Any] = (),
    ) -> Dict[str, Any] | None:
        required = OPPONENT_REQUIREMENTS.get(pleading_type)
        selections = []
        for item in opponent_pleadings or []:
            value = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            selections.append((value.get("draft_id"), value.get("version_id")))
        if opponent_draft_id and opponent_version_id:
            selections.append((opponent_draft_id, opponent_version_id))
        selections = list(dict.fromkeys((str(draft_id), str(version_id)) for draft_id, version_id in selections if draft_id and version_id))
        if not required and not selections:
            return None
        if required and not selections:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{pleading_type} requires immutable opponent pleading versions")
        snapshots = []
        for selected_draft_id, selected_version_id in selections:
            draft = await self.db.arbitration_drafts.find_one({"_id": selected_draft_id, "case_id": case_id, "deleted_at": {"$exists": False}})
            if not draft:
                raise HTTPException(status_code=422, detail="Opponent pleading is outside the arbitration case")
            version_query = {"draft_id": selected_draft_id, "_id": selected_version_id}
            version = await self.db.arbitration_draft_versions.find_one(version_query)
            if not version:
                try:
                    version_query = {"draft_id": selected_draft_id, "version": int(selected_version_id)}
                except (TypeError, ValueError):
                    pass
                version = await self.db.arbitration_draft_versions.find_one(version_query)
            if not version:
                raise HTTPException(status_code=422, detail="Selected opponent pleading version is unavailable")
            snapshots.append(
                {
                    "draft_id": selected_draft_id, "version_id": str(version.get("_id")), "version": version.get("version"),
                    "version_hash": version.get("version_hash") or artifact_hash(version), "draft_type": str(draft.get("draft_type")),
                    "paragraphs": _opponent_paragraphs(version),
                }
            )
            snapshots[-1]["paragraph_count"] = len(snapshots[-1]["paragraphs"])
            snapshots[-1]["paragraphs_hash"] = artifact_hash(snapshots[-1]["paragraphs"])
            snapshots[-1]["parse_status"] = "parsed" if snapshots[-1]["paragraphs"] else "missing_content"
        present_types = {item["draft_type"] for item in snapshots}
        missing_types = set(required or ()).difference(present_types)
        if missing_types:
            raise HTTPException(status_code=422, detail={"message": "Required immutable opponent pleadings are missing", "missing_pleading_types": sorted(missing_types)})
        snapshots.sort(key=lambda item: (item["draft_type"], item["draft_id"], item["version_id"]))
        payload = {"pleadings": snapshots}
        return await self.repository.create_snapshot(run_id=run_id, kind="opponent_pleading", payload=payload, effect_key=f"{run_id}:snapshot:opponent")

    async def _analysis_context(self, run: Dict[str, Any]) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        pleading_type = str(run.get("pleading_type") or "")
        rows_by_matrix = {slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id")) for slug in MATRIX_COLLECTIONS}
        case = await self.cases.get_case(case_id)
        document_manifest = (
            await self.db.arbitration_workflow_snapshots.find_one(
                {"_id": run.get("document_manifest_id"), "run_id": str(run["_id"]), "kind": "document_manifest"}
            )
            if run.get("document_manifest_id")
            else None
        )
        document_signals: List[Dict[str, Any]] = []
        for item in ((document_manifest or {}).get("payload") or {}).get("documents") or []:
            query = {"_id": item.get("document_id")}
            if case.get("organization_id"):
                query["organization_id"] = case.get("organization_id")
            if case.get("project_id"):
                query["project_id"] = case.get("project_id")
            record = await self.db.documents.find_one(query)
            if not record:
                document_signals.append({"document_id": item.get("document_id"), "resolution_status": "missing_or_out_of_scope"})
                continue
            searchable = " ".join(
                str(record.get(key) or "")
                for key in ("subject", "filename", "summary", "ocrText", "text", "text_enriched")
            ).lower()
            document_signals.append(
                {
                    "document_id": item.get("document_id"),
                    "version_id": record.get("current_version_id"),
                    "sha256": record.get("sha256"),
                    "updated_at": record.get("updated_at"),
                    "content_digest": artifact_hash(searchable),
                    "signals": [term for term in DOCUMENT_SIGNAL_TERMS if term in searchable],
                    "resolution_status": "resolved",
                }
            )
        opponent_snapshot = (
            await self.db.arbitration_workflow_snapshots.find_one(
                {"_id": run.get("opponent_pleading_snapshot_id"), "run_id": str(run["_id"]), "kind": "opponent_pleading"}
            )
            if run.get("opponent_pleading_snapshot_id")
            else None
        )
        return {
            "pleading_type": pleading_type,
            "rows_by_matrix": rows_by_matrix,
            "document_signals": document_signals,
            "opponent_snapshot": opponent_snapshot,
        }

    async def _analyze_branch(
        self,
        run: Dict[str, Any],
        name: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute one read-only evidence branch and persist only its immutable artifact."""

        pleading_type = str(context["pleading_type"])
        rows_by_matrix = context["rows_by_matrix"]
        async with self._fanout_slot(run, name):
            matrices = _branch_matrices(name, pleading_type)
            selected: List[Dict[str, Any]] = []
            findings: List[Dict[str, Any]] = []
            allowed_fields = ANALYSIS_FINDING_FIELDS[name]
            for matrix in matrices:
                for row in rows_by_matrix.get(matrix) or []:
                    canonical = _canonical_row(matrix, row)
                    selected.append(canonical)
                    findings.append(
                        {
                            "matrix": matrix,
                            "row_id": canonical["row_id"],
                            "source_revision_ids": canonical["source_revision_ids"],
                            "evidence_status": canonical["evidence_status"],
                            "facts": {
                                key: row.get(key)
                                for key in allowed_fields
                                if row.get(key) not in (None, "", [])
                            },
                        }
                    )
            selected.sort(key=lambda row: (row["matrix"], row["row_id"], row["payload_hash"]))
            findings.sort(key=lambda row: (row["matrix"], row["row_id"]))
            opponent_inputs = []
            if name == "pleading_position":
                opponent_inputs = [
                    {
                        "draft_type": item.get("draft_type"),
                        "version_id": item.get("version_id"),
                        "version_hash": item.get("version_hash"),
                        "paragraph_count": item.get("paragraph_count"),
                        "paragraphs_hash": item.get("paragraphs_hash"),
                    }
                    for item in ((context.get("opponent_snapshot") or {}).get("payload") or {}).get("pleadings") or []
                ]
        payload = {
            "branch": name,
            "pleading_type": pleading_type,
            "matrices": list(matrices),
            "rows": selected,
            "findings": findings,
            "document_signals": context["document_signals"] if name == "document_understanding" else [],
            "opponent_pleadings": opponent_inputs,
            "analysis_summary": {
                "row_count": len(selected),
                "supported_rows": sum(1 for row in selected if row.get("evidence_status") == "supported"),
                "needs_review_rows": sum(1 for row in selected if row.get("evidence_status") != "supported"),
            },
            "authoritative": False,
            "review_status": "needs_review",
            "input_snapshot_hash": run.get("input_snapshot_hash"),
            "evidence_snapshot_hash": run.get("evidence_snapshot_hash"),
            "opponent_pleading_snapshot_hash": run.get("opponent_pleading_snapshot_hash"),
        }
        branch_hash = artifact_hash(payload)
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="analysis_artifact",
            payload=payload,
            effect_key=f"{run['_id']}:analysis:{name}:{branch_hash}",
        )
        return {
            "branch": name,
            "rows": selected,
            "artifact_id": snapshot["_id"],
            "artifact_hash": snapshot["snapshot_hash"],
            "authoritative": False,
        }

    async def analyze_node(self, run: Dict[str, Any], node: str) -> Dict[str, Any]:
        """Execute the bounded branch set owned by one official graph node."""

        branch_names = ANALYSIS_NODE_BRANCHES.get(node)
        if not branch_names:
            raise ValueError(f"Unknown arbitration analysis node: {node}")
        context = await self._analysis_context(run)
        branch_results = await asyncio.gather(
            *(self._analyze_branch(run, branch, context) for branch in branch_names)
        )
        refs = [
            {
                "branch": result["branch"],
                "artifact_id": result["artifact_id"],
                "artifact_hash": result["artifact_hash"],
            }
            for result in sorted(branch_results, key=lambda item: item["branch"])
        ]
        payload = {
            "node": node,
            "branches": refs,
            "input_snapshot_hash": run.get("input_snapshot_hash"),
            "evidence_snapshot_hash": run.get("evidence_snapshot_hash"),
            "opponent_pleading_snapshot_hash": run.get("opponent_pleading_snapshot_hash"),
            "authoritative": False,
        }
        node_hash = artifact_hash(payload)
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="analysis_node_result",
            payload=payload,
            effect_key=f"{run['_id']}:analysis-node:{node}:{node_hash}",
        )
        return {
            "node": node,
            "artifact_id": snapshot["_id"],
            "artifact_hash": snapshot["snapshot_hash"],
            "branches": refs,
        }

    async def merge_analysis_nodes(
        self,
        run: Dict[str, Any],
        node_results: Dict[str, Dict[str, str]],
    ) -> Dict[str, Any]:
        """Verify every fan-out result and perform the sole deterministic merge."""

        branch_results: List[Dict[str, Any]] = []
        for node in ANALYSIS_NODE_BRANCHES:
            ref = node_results.get(node) or {}
            snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {
                    "_id": ref.get("artifact_id"),
                    "run_id": str(run["_id"]),
                    "kind": "analysis_node_result",
                }
            )
            if not snapshot or snapshot.get("snapshot_hash") != ref.get("artifact_hash"):
                raise HTTPException(status_code=409, detail=f"Analysis node artifact is missing or drifted: {node}")
            for branch_ref in (snapshot.get("payload") or {}).get("branches") or []:
                branch = await self.db.arbitration_workflow_snapshots.find_one(
                    {
                        "_id": branch_ref.get("artifact_id"),
                        "run_id": str(run["_id"]),
                        "kind": "analysis_artifact",
                    }
                )
                if not branch or branch.get("snapshot_hash") != branch_ref.get("artifact_hash"):
                    raise HTTPException(status_code=409, detail="Analysis branch artifact is missing or drifted")
                branch_results.append(
                    {
                        "branch": branch_ref.get("branch"),
                        "rows": list((branch.get("payload") or {}).get("rows") or []),
                        "artifact_id": branch["_id"],
                        "artifact_hash": branch["snapshot_hash"],
                        "authoritative": False,
                    }
                )
        context = await self._analysis_context(run)
        return await self._merge_analysis_results(run, context, branch_results)

    async def _merge_analysis_results(
        self,
        run: Dict[str, Any],
        context: Dict[str, Any],
        branch_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pleading_type = str(context["pleading_type"])
        rows_by_matrix = context["rows_by_matrix"]
        merged = deterministic_merge(branch_results)
        required = PLEADING_MATRIX_REQUIREMENTS.get(pleading_type, ())
        blockers = [
            {"code": "missing_required_matrix", "matrix": slug, "message": f"No approved {slug} row is available"}
            for slug in required if not any(_is_ready_row(row) for row in rows_by_matrix.get(slug) or [])
        ]
        opponent_snapshot = context.get("opponent_snapshot")
        if OPPONENT_REQUIREMENTS.get(pleading_type):
            if not opponent_snapshot:
                blockers.append(
                    {
                        "code": "opponent_pleading_snapshot_required",
                        "pleading_types": sorted(OPPONENT_REQUIREMENTS[pleading_type]),
                        "message": "Required immutable opponent pleading snapshot is unavailable",
                    }
                )
            missing_paragraphs = [
                item.get("draft_type")
                for item in ((opponent_snapshot or {}).get("payload") or {}).get("pleadings") or []
                if not item.get("paragraph_count")
            ]
            if missing_paragraphs:
                blockers.append(
                    {
                        "code": "opponent_paragraph_parse_required",
                        "pleading_types": sorted(str(item) for item in missing_paragraphs),
                        "message": "Required immutable opponent pleading versions contain no parseable paragraphs",
                    }
                )
        artifact_refs = [
            {
                "branch": result["branch"],
                "artifact_id": result["artifact_id"],
                "artifact_hash": result["artifact_hash"],
                "authoritative": False,
            }
            for result in sorted(branch_results, key=lambda item: item["branch"])
        ]
        artifact_set_payload = {
            "pleading_type": pleading_type,
            "artifacts": artifact_refs,
            "authoritative": False,
        }
        artifact_set_hash = artifact_hash(
            {
                "pleading_type": pleading_type,
                "artifacts": [
                    {
                        "branch": item["branch"],
                        "artifact_hash": item["artifact_hash"],
                        "authoritative": item["authoritative"],
                    }
                    for item in artifact_refs
                ],
                "authoritative": False,
            }
        )
        artifact_set_payload["artifact_set_hash"] = artifact_set_hash
        artifact_set = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="analysis_artifact_set",
            payload=artifact_set_payload,
            effect_key=f"{run['_id']}:analysis-set:{artifact_set_hash}",
        )
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]), kind="matrix_revision_set",
            payload={
                "revision_set_id": merged["revision_set_id"],
                "rows": merged["rows"],
                "required_matrices": list(required),
                "analysis_artifact_set_id": artifact_set["_id"],
                "analysis_artifact_set_hash": artifact_set_hash,
                "authoritative": False,
                "review_status": "needs_review",
            },
            effect_key=f"{run['_id']}:snapshot:matrix:{merged['revision_hash']}",
        )
        return {
            **merged,
            "snapshot_id": snapshot["_id"],
            "analysis_artifact_set_id": artifact_set["_id"],
            "analysis_artifact_set_hash": artifact_set_hash,
            "analysis_artifacts": artifact_refs,
            "blockers": blockers,
        }

    async def analyze(self, run: Dict[str, Any], *, parallel: bool = True) -> Dict[str, Any]:
        """Compatibility entry point built from the same node-owned primitives."""

        context = await self._analysis_context(run)
        if parallel:
            branch_results = await asyncio.gather(
                *(self._analyze_branch(run, name, context) for name in ANALYSIS_BRANCHES)
            )
        else:
            branch_results = [
                await self._analyze_branch(run, name, context)
                for name in ANALYSIS_BRANCHES
            ]
        return await self._merge_analysis_results(run, context, branch_results)

    async def inspect_matrix_revision(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """Read the current matrix revision without creating analysis effects."""

        context = await self._analysis_context(run)
        pleading_type = str(context["pleading_type"])
        rows_by_matrix = context["rows_by_matrix"]
        rows = [
            _canonical_row(matrix, row)
            for matrix in sorted(rows_by_matrix)
            for row in rows_by_matrix[matrix]
        ]
        merged = deterministic_merge(({"rows": rows},))
        required = PLEADING_MATRIX_REQUIREMENTS.get(pleading_type, ())
        blockers = [
            {
                "code": "missing_required_matrix",
                "matrix": slug,
                "message": f"No approved {slug} row is available",
            }
            for slug in required
            if not any(_is_ready_row(row) for row in rows_by_matrix.get(slug) or [])
        ]
        return {**merged, "blockers": blockers}

    async def build_plan(self, run: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        rows = {slug: [row for row in await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id")) if _is_ready_row(row)] for slug in MATRIX_COLLECTIONS}
        structure = {
            "statement_of_claim": ["introduction", "jurisdiction", "facts", "claims", "quantum", "relief"],
            "statement_of_defence": ["introduction", "preliminary_objections", "paragraph_responses", "defences", "quantum", "relief"],
            "counterclaim": ["introduction", "jurisdiction", "facts", "legal_basis", "causation", "quantum", "relief"],
            "rejoinder": ["introduction", "paragraph_replies", "defence_rebuttal", "counterclaim_reply", "relief"],
        }.get(str(run.get("pleading_type")), [])
        claim_rows = rows["claim-matrix"] + rows["counterclaim-matrix"]
        position_rows = rows["defence-matrix"] + rows["counterclaim-matrix"] + rows["rejoinder-matrix"]

        def source_revisions(*slugs: str) -> List[str]:
            return sorted(
                {
                    str(source_revision_id)
                    for slug in slugs
                    for row in rows[slug]
                    for source_revision_id in row.get("source_revision_ids") or []
                    if source_revision_id
                }
            )

        section_sources = {
            "introduction": source_revisions("document-index"),
            "jurisdiction": source_revisions("clause-matrix", "jurisdiction-matrix", "notice-compliance"),
            "facts": source_revisions("chronology-matrix", "document-index"),
            "claims": source_revisions("claim-matrix", "issue-matrix"),
            "legal_basis": source_revisions("counterclaim-matrix", "clause-matrix"),
            "causation": source_revisions("claim-matrix", "counterclaim-matrix", "chronology-matrix"),
            "preliminary_objections": source_revisions("defence-matrix", "jurisdiction-matrix"),
            "paragraph_responses": source_revisions("defence-matrix", "issue-matrix"),
            "paragraph_replies": source_revisions("rejoinder-matrix", "issue-matrix"),
            "defences": source_revisions("defence-matrix", "issue-matrix"),
            "defence_rebuttal": source_revisions("rejoinder-matrix", "defence-matrix"),
            "counterclaim_reply": source_revisions("rejoinder-matrix", "counterclaim-matrix"),
            "quantum": source_revisions("quantum-annexures", "claim-matrix", "counterclaim-matrix"),
            "relief": source_revisions("claim-matrix", "counterclaim-matrix"),
        }
        plan_payload = {
            "issues": rows["issue-matrix"],
            "positions": position_rows,
            "paragraph_mapping": [
                {
                    "matrix_row_id": row.get("_id"),
                    "matrix_row_revision_id": row.get("matrix_row_revision_id"),
                    "source_paragraph_number": row.get("source_paragraph_number") or row.get("paragraph_number"),
                    "position": row.get("position") or row.get("response_type") or row.get("reply_type"),
                    "source_revision_ids": row.get("source_revision_ids") or [],
                }
                for row in position_rows
            ],
            "claim_theory": claim_rows,
            "legal_basis": rows["clause-matrix"] + rows["jurisdiction-matrix"],
            "burden_of_proof": [{"issue_id": row.get("_id"), "burden": row.get("burden_of_proof")} for row in rows["issue-matrix"]],
            "anticipated_arguments": rows["defence-matrix"],
            "causation_theory": [{"claim_id": row.get("_id"), "causation": row.get("causation")} for row in claim_rows],
            "quantum_theory": rows["quantum-annexures"],
            "evidentiary_gaps": list(run.get("blockers") or []),
            "relief_requested": [{"claim_id": row.get("_id"), "relief": row.get("relief") or row.get("relief_sought")} for row in claim_rows],
            "section_structure": [{"order": index + 1, "key": key} for index, key in enumerate(structure)],
            "section_source_mapping": [
                {"section_key": key, "source_revision_ids": section_sources.get(key, [])}
                for key in structure
            ],
            "source_mapping": rows["document-index"],
            "decisions": {"new_matter": any(row.get("new_matter") for row in rows["rejoinder-matrix"]), "counterclaim": bool(rows["counterclaim-matrix"])},
        }
        plan_hash = artifact_hash(plan_payload)
        existing_plans = await _collect(self.db.arbitration_plans.find({"run_id": run["_id"]}))
        plan_version = max((int(item.get("version") or 0) for item in existing_plans), default=0) + 1
        plan = {
            "_id": f"plan_{plan_hash[:24]}", "run_id": run["_id"], "case_id": case_id,
            "draft_id": run.get("draft_id"), "version": plan_version, "plan_hash": plan_hash,
            "status": "needs_review", **plan_payload, "created_by": _actor_id(current_user), "created_at": datetime.now(timezone.utc),
        }
        return await self.repository.create_plan(plan)

    @staticmethod
    def material_questions(blockers: List[Dict[str, Any]], pleading_type: str) -> List[Dict[str, Any]]:
        questions = []
        for blocker in blockers:
            matrix = str(blocker.get("matrix") or "evidence")
            questions.append(
                {
                    "question_id": f"resolve_{matrix.replace('-', '_')}",
                    "question_version": 1,
                    "required": True,
                    "prompt": f"How should counsel address the missing approved {matrix} material before {pleading_type.replace('_', ' ')} drafting?",
                    "artifact": matrix,
                }
            )
        return questions
