from __future__ import annotations

from typing import Dict


MATRIX_COLLECTIONS: Dict[str, str] = {
    "document-index": "arbitration_document_index",
    "chronology-matrix": "arbitration_chronology_matrix",
    "clause-matrix": "arbitration_clause_matrix",
    "issue-matrix": "arbitration_issue_matrix",
    "claim-matrix": "arbitration_claim_matrix",
    "defence-matrix": "arbitration_defence_matrix",
    "counterclaim-matrix": "arbitration_counterclaim_matrix",
    "rejoinder-matrix": "arbitration_rejoinder_matrix",
    "quantum-annexures": "arbitration_quantum_annexures",
    "notice-compliance": "arbitration_notice_compliance",
    "jurisdiction-matrix": "arbitration_jurisdiction_matrix",
    "expert-alignment": "arbitration_expert_alignment",
}


__all__ = ["MATRIX_COLLECTIONS"]
