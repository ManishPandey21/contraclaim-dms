from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from fastapi import HTTPException, status

from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id, _is_ready_row
from .matrix_registry import MATRIX_COLLECTIONS
from .repository import _collect, _jsonable
from .workflow_repository import ArbitrationWorkflowRepository, artifact_hash


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

PLEADING_POSITION_MATRICES = {
    "statement_of_claim": ("claim-matrix",),
    "statement_of_defence": ("defence-matrix", "counterclaim-matrix"),
    "counterclaim": ("counterclaim-matrix",),
    "rejoinder": ("rejoinder-matrix", "defence-matrix", "counterclaim-matrix"),
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

    async def analyze(self, run: Dict[str, Any], *, parallel: bool = True) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        pleading_type = str(run.get("pleading_type") or "")
        rows_by_matrix = {slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id")) for slug in MATRIX_COLLECTIONS}

        async def branch(name: str) -> Dict[str, Any]:
            # Branches never mutate matrix rows. Their only write is an immutable,
            # explicitly non-authoritative analysis artifact.
            matrices = _branch_matrices(name, pleading_type)
            selected: List[Dict[str, Any]] = []
            for matrix in matrices:
                rows = rows_by_matrix.get(matrix) or []
                for row in rows:
                    selected.append(_canonical_row(matrix, row))
            selected.sort(key=lambda row: (row["matrix"], row["row_id"], row["payload_hash"]))
            payload = {
                "branch": name,
                "pleading_type": pleading_type,
                "matrices": list(matrices),
                "rows": selected,
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

        if parallel:
            branch_results = await asyncio.gather(*(branch(name) for name in ANALYSIS_BRANCHES))
        else:
            branch_results = [await branch(name) for name in ANALYSIS_BRANCHES]
        merged = deterministic_merge(branch_results)
        required = PLEADING_MATRIX_REQUIREMENTS.get(pleading_type, ())
        blockers = [
            {"code": "missing_required_matrix", "matrix": slug, "message": f"No approved {slug} row is available"}
            for slug in required if not any(_is_ready_row(row) for row in rows_by_matrix.get(slug) or [])
        ]
        opponent_snapshot = None
        if run.get("opponent_pleading_snapshot_id"):
            opponent_snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {
                    "_id": run.get("opponent_pleading_snapshot_id"),
                    "run_id": str(run["_id"]),
                    "kind": "opponent_pleading",
                }
            )
        if OPPONENT_REQUIREMENTS.get(pleading_type):
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
