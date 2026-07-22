"""Independent, read-only candidate projection for LangGraph shadow parity."""

from __future__ import annotations

from typing import Any, Dict

from .case_workspace import ArbitrationCaseWorkspaceService, _is_ready_row
from .matrix_registry import MATRIX_COLLECTIONS
from .workflow_domain import (
    ANALYSIS_BRANCHES,
    PLEADING_MATRIX_REQUIREMENTS,
    _branch_matrices,
    _canonical_row,
    deterministic_merge,
)


class ArbitrationShadowCandidate:
    """Recompute normalized graph inputs without authoritative artifact writes.

    This deliberately does not call ``ArbitrationWorkflowDomain.analyze`` and
    cannot insert snapshots, matrices, versions, approvals or exports. Shared
    canonical row primitives keep the comparison schema stable while the
    orchestration and repository read path remain independent.
    """

    def __init__(self, db: Any) -> None:
        self.cases = ArbitrationCaseWorkspaceService(db)

    async def analyze(self, run: Dict[str, Any]) -> Dict[str, Any]:
        case_id = str(run["case_id"])
        pleading_type = str(run.get("pleading_type") or "")
        rows = {
            slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=run.get("draft_id"))
            for slug in MATRIX_COLLECTIONS
        }
        branches = []
        for name in ANALYSIS_BRANCHES:
            selected = [
                _canonical_row(matrix, row)
                for matrix in _branch_matrices(name, pleading_type)
                for row in rows.get(matrix) or []
            ]
            selected.sort(key=lambda item: (item["matrix"], item["row_id"], item["payload_hash"]))
            branches.append({"branch": name, "rows": selected})
        merged = deterministic_merge(branches)
        blockers = [
            {"code": "missing_required_matrix", "matrix": slug}
            for slug in PLEADING_MATRIX_REQUIREMENTS.get(pleading_type, ())
            if not any(_is_ready_row(row) for row in rows.get(slug) or [])
        ]
        return {**merged, "blockers": blockers, "namespace": "shadow_candidate", "authoritative_writes": False}
