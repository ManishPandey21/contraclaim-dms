"""A blocked source must not be counted as supported evidence.

`_evidence_status` graded a row `supported` from `_is_ready_row` + revision ids
alone - no authority axis. The matrix projection blanks a blocked row's derived
CONTENT, but the row still exists and still counted toward
`analysis_summary.supported_rows`, improving readiness and legal completeness on
the strength of a document nobody may consume. That is material influence even
though no text leaks.

The fix carries the authority decision the projection already made onto the row
(`authority_denied`), and `_evidence_status` honours it. Model B and manual
evidence are unaffected because their rows are never denied.
"""

from __future__ import annotations

from rbac_backend.services.arbitration_drafting.workflow_domain import _evidence_status


def _row(**extra):
    row = {
        "_id": "r-1",
        "approval_status": "approved",
        "human_approval_status": "approved",
        "readiness_status": "approved",
        "verification_status": "approved",
    }
    row.update(extra)
    return row


REVS = ["rev-1"]


def test_a_clean_ready_row_is_supported() -> None:
    assert _evidence_status(_row(), REVS) == "supported"


def test_an_authority_denied_row_is_not_supported() -> None:
    assert _evidence_status(_row(authority_denied=True), REVS) != "supported", (
        "a blocked document's matrix row was still counted as supported evidence"
    )


def test_a_row_without_revisions_is_missing() -> None:
    assert _evidence_status(_row(), []) == "missing"
