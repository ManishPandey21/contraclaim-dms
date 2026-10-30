"""A blocked source must not satisfy a required-matrix presence gate.

The `matrix_review` approval gate (`workflow_service.py:757`) fires on
`missing_required_matrix` blockers, which are computed by testing
`_is_ready_row(row)` alone. `_is_ready_row` tests approval/review status, not
authority - so a blocked-but-approved matrix row (its derived content already
blanked and `authority_denied` stamped by `safe_matrix_rows`) still counts as
"the required matrix is present", and the gate mints its approval receipt on
the strength of a source nobody may consume.

That is material influence, not disclosure: the text is withheld, but the row's
PRESENCE advances the pleading workflow. chronology-matrix is the fully-exposed
case - a required matrix for all four pleading types with no authority check on
any path.

The requirement helper must exclude authority-denied rows.
"""

from __future__ import annotations

from rbac_backend.services.arbitration_drafting.workflow_domain import (
    _row_satisfies_requirement,
)


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


def test_a_clean_approved_row_satisfies_the_requirement() -> None:
    assert _row_satisfies_requirement(_row()) is True


def test_a_blocked_row_does_not_satisfy_the_requirement() -> None:
    assert _row_satisfies_requirement(_row(authority_denied=True)) is False, (
        "a blocked source's approved matrix row still satisfied the required-"
        "matrix gate, advancing the pleading workflow"
    )


def test_an_unapproved_row_does_not_satisfy_the_requirement() -> None:
    assert _row_satisfies_requirement(_row(approval_status="needs_review",
                                           human_approval_status="needs_review",
                                           readiness_status="needs_review",
                                           verification_status="needs_review")) is False
