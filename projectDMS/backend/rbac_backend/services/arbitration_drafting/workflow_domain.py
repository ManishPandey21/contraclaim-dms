from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from fastapi import HTTPException, status

from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id, _is_ready_row
from .matrix_registry import MATRIX_COLLECTIONS
from .repository import _jsonable
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

PLEADING_MATRIX_REQUIREMENTS = {
    "statement_of_claim": (
        "document-index", "chronology-matrix", "clause-matrix", "issue-matrix", "claim-matrix",
        "jurisdiction-matrix", "notice-compliance", "quantum-annexures", "expert-alignment",
    ),
    "statement_of_defence": (
        "document-index", "defence-matrix", "issue-matrix", "jurisdiction-matrix",
        "quantum-annexures",
    ),
    "counterclaim": (
        "document-index", "chronology-matrix", "clause-matrix", "counterclaim-matrix",
        "jurisdiction-matrix", "notice-compliance", "quantum-annexures",
    ),
    "rejoinder": ("document-index", "rejoinder-matrix", "defence-matrix", "issue-matrix"),
}

OPPONENT_REQUIREMENTS = {
    "statement_of_defence": ("statement_of_claim",),
    "rejoinder": ("statement_of_claim", "statement_of_defence"),
}


def _canonical_row(matrix: str, row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "matrix": matrix,
        "row_id": str(row.get("_id") or ""),
        "status": str(row.get("approval_status") or row.get("human_approval_status") or "needs_review"),
        "revision": int(row.get("revision") or 1),
        "source_id": row.get("source_id"),
        "source_hash": row.get("source_hash"),
        "updated_at": row.get("updated_at"),
        "payload_hash": artifact_hash({key: value for key, value in row.items() if key not in {"approval_log", "review_comments", "review_assignments"}}),
    }


def deterministic_merge(branch_results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for branch in branch_results:
        for row in branch.get("rows") or []:
            rows.append(_jsonable(row))
    rows.sort(key=lambda row: (str(row.get("matrix") or ""), str(row.get("row_id") or ""), str(row.get("payload_hash") or "")))
    return {"rows": rows, "revision_hash": artifact_hash(rows), "revision_set_id": f"mrs_{artifact_hash(rows)[:24]}"}


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
                }
            )
        present_types = {item["draft_type"] for item in snapshots}
        missing_types = set(required or ()).difference(present_types)
        if missing_types:
            raise HTTPException(status_code=422, detail={"message": "Required immutable opponent pleadings are missing", "missing_pleading_types": sorted(missing_types)})
        snapshots.sort(key=lambda item: (item["draft_type"], item["draft_id"], item["version_id"]))
        payload = {"pleadings": snapshots}
        return await self.repository.create_snapshot(run_id=run_id, kind="opponent_pleading", payload=payload, effect_key=f"{run_id}:snapshot:opponent")

    async def analyze(self, run: Dict[str, Any], *, parallel: bool = True) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        rows_by_matrix = {slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id")) for slug in MATRIX_COLLECTIONS}

        async def branch(name: str) -> Dict[str, Any]:
            # Branches are read-only projections over authoritative matrices.
            selected = []
            for matrix, rows in rows_by_matrix.items():
                for row in rows:
                    selected.append(_canonical_row(matrix, row))
            return {"branch": name, "rows": selected}

        if parallel:
            branch_results = await asyncio.gather(*(branch(name) for name in ANALYSIS_BRANCHES))
        else:
            branch_results = [await branch(name) for name in ANALYSIS_BRANCHES]
        # De-duplicate projections emitted by independent analytical views.
        unique: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        for result in branch_results:
            for row in result["rows"]:
                unique[(row["matrix"], row["row_id"], row["payload_hash"])] = row
        merged = deterministic_merge([{"rows": list(unique.values())}])
        required = PLEADING_MATRIX_REQUIREMENTS.get(str(run.get("pleading_type")), ())
        blockers = [
            {"code": "missing_required_matrix", "matrix": slug, "message": f"No approved {slug} row is available"}
            for slug in required if not any(_is_ready_row(row) for row in rows_by_matrix.get(slug) or [])
        ]
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]), kind="matrix_revision_set",
            payload={"revision_set_id": merged["revision_set_id"], "rows": merged["rows"], "required_matrices": list(required)},
            effect_key=f"{run['_id']}:snapshot:matrix:{merged['revision_hash']}",
        )
        return {**merged, "snapshot_id": snapshot["_id"], "blockers": blockers}

    async def build_plan(self, run: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        rows = {slug: [row for row in await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id")) if _is_ready_row(row)] for slug in MATRIX_COLLECTIONS}
        structure = {
            "statement_of_claim": ["introduction", "jurisdiction", "facts", "claims", "quantum", "relief"],
            "statement_of_defence": ["introduction", "preliminary_objections", "paragraph_responses", "defences", "quantum", "relief"],
            "counterclaim": ["introduction", "jurisdiction", "facts", "legal_basis", "causation", "quantum", "relief"],
            "rejoinder": ["introduction", "paragraph_replies", "defence_rebuttal", "counterclaim_reply", "relief"],
        }.get(str(run.get("pleading_type")), [])
        plan_payload = {
            "issues": rows["issue-matrix"],
            "positions": rows["defence-matrix"] + rows["counterclaim-matrix"] + rows["rejoinder-matrix"],
            "legal_basis": rows["clause-matrix"] + rows["jurisdiction-matrix"],
            "burden_of_proof": [{"issue_id": row.get("_id"), "burden": row.get("burden_of_proof")} for row in rows["issue-matrix"]],
            "anticipated_arguments": rows["defence-matrix"],
            "causation_theory": [{"claim_id": row.get("_id"), "causation": row.get("causation")} for row in rows["claim-matrix"] + rows["counterclaim-matrix"]],
            "quantum_theory": rows["quantum-annexures"],
            "evidentiary_gaps": list(run.get("blockers") or []),
            "relief_requested": [{"claim_id": row.get("_id"), "relief": row.get("relief") or row.get("relief_sought")} for row in rows["claim-matrix"] + rows["counterclaim-matrix"]],
            "section_structure": [{"order": index + 1, "key": key} for index, key in enumerate(structure)],
            "source_mapping": rows["document-index"],
            "decisions": {"new_matter": any(row.get("new_matter") for row in rows["rejoinder-matrix"]), "counterclaim": bool(rows["counterclaim-matrix"])},
        }
        plan_hash = artifact_hash(plan_payload)
        plan = {
            "_id": f"plan_{plan_hash[:24]}", "run_id": run["_id"], "case_id": case_id,
            "draft_id": run.get("draft_id"), "version": 1, "plan_hash": plan_hash,
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
