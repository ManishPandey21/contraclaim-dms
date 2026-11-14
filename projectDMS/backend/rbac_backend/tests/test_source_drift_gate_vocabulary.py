"""The filing gate was checking a vocabulary no producer emits.

`source_drift` is the only validation branch that inspects the per-row safety
state of the immutable source ledger, and it gates `legal_review` approval
(`workflow_service.py:776`). It tested for three things:

* `quality_flags` intersecting `{manual_or_unverified, source_drift, stale}`
* `current_revision_id` differing from `source_revision_id`
* `current_sha256` differing from `sha256`

All three names appear **only** inside that branch. Repo-wide there is no
writer for any of them: the sole producer of `quality_flags` is
`_annotate_source_quality`, whose complete vocabulary is missing_citation,
missing_snippet, authority_denied, missing_exhibit_id, missing_source_link,
not_verified_for_filing, user_supplied, unverified_ai_suggestion. The
intersection is empty for every row that can exist, and the two drift
comparisons are always False.

So the branch was dead, and making `authority_denied` survive `_ledger_row`
propagated it into a gate that reads a different schema - the same defect one
layer further out.

The repair is to derive the blocking vocabulary from the producer instead of
restating it beside it, and to pin that every flag the producer can emit has
been explicitly classified as blocking or not.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from rbac_backend.services.arbitration_drafting.context import (
    ArbitrationContextBuilder,
)
from rbac_backend.services.arbitration_drafting.workflow_validation import (
    ArbitrationValidationOrchestrator,
)

BODY = "THE BLOCKED DOCUMENT BODY"
DRAFT = {"organization_id": "org-1", "project_id": "project-1", "case_id": "case-1"}


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            ok = True
            for key, expected in query.items():
                actual = row.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if not any(str(actual) == str(c) for c in expected["$in"]):
                        ok = False
                elif str(actual) != str(expected):
                    ok = False
            if ok:
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._c = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _real_ledger_row(status: str) -> Dict[str, Any]:
    """A ledger row built the way production builds it - no hand-written dict.

    Hand-built ledger literals are how this class of defect stays green: every
    existing validator test supplies a shape that carries no quality_flags at
    all, so no test could ever have exercised the branch.
    """
    document = {
        "_id": "doc-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "subject": "Delay notice",
        "ocrText": BODY,
        "processing_status": status,
    }
    builder = ArbitrationContextBuilder(_DB(documents=[document]))
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "document", "source_id": "doc-1"}
        )
    )
    row = builder._ledger_row(reference, 1)
    asyncio.run(builder._annotate_source_quality([row], []))
    return row


def _source_drift(row: Dict[str, Any]) -> Dict[str, Any]:
    version = {
        "full_markdown": "The Employer was notified. [S1]",
        "source_ledger": [row],
        "version_hash": "v-hash",
    }
    result = asyncio.run(
        ArbitrationValidationOrchestrator().evaluate(version, branches=["source_drift"])
    )
    return result


# --- the premise ---------------------------------------------------------------


def test_the_blocked_row_really_does_carry_the_denial() -> None:
    row = _real_ledger_row("human_review_required")

    assert row["quality_flags"] == ["authority_denied"]
    assert row["verification_status"] == "authority_denied"


# --- the gate ------------------------------------------------------------------


def test_the_filing_gate_rejects_an_authority_denied_source() -> None:
    result = _source_drift(_real_ledger_row("human_review_required"))

    assert result["status"] != "passed", (
        "the only per-source safety branch in the filing gate passed a "
        "blocked document, because it checks flag names nothing produces"
    )


def test_the_gate_names_the_offending_source() -> None:
    result = _source_drift(_real_ledger_row("human_review_required"))
    issues = str(result)

    assert "source_not_authoritative" in issues


def test_a_clean_source_still_passes_the_gate() -> None:
    """The gate must not become a blanket refusal."""
    result = _source_drift(_real_ledger_row("metadata_extracted"))

    assert result["status"] == "passed", result


def test_an_operationally_failed_source_still_passes() -> None:
    """Model B: a worker crash must not block a filing."""
    result = _source_drift(_real_ledger_row("failed"))

    assert result["status"] == "passed", result


# --- the class defect, not just this instance ----------------------------------


def test_every_producible_quality_flag_is_explicitly_classified() -> None:
    """Fail the build when a new flag appears with no filing decision made.

    The branch drifted from its producer silently. Deriving the blocking set
    from the producer's own vocabulary means a new flag cannot be invented
    without someone deciding whether it blocks a legal filing.
    """
    from rbac_backend.services.arbitration_drafting.context import (
        PRODUCIBLE_QUALITY_FLAGS,
    )
    from rbac_backend.services.arbitration_drafting.workflow_validation import (
        BLOCKING_QUALITY_FLAGS,
        NON_BLOCKING_QUALITY_FLAGS,
    )

    unclassified = PRODUCIBLE_QUALITY_FLAGS - BLOCKING_QUALITY_FLAGS - NON_BLOCKING_QUALITY_FLAGS

    assert not unclassified, (
        f"quality flags with no filing decision: {sorted(unclassified)}"
    )


def test_the_gate_only_looks_for_flags_that_can_actually_be_produced() -> None:
    """The defect stated as a property.

    Every flag the gate blocks on must be one the producer can emit; otherwise
    the check is decoration.
    """
    from rbac_backend.services.arbitration_drafting.context import (
        PRODUCIBLE_QUALITY_FLAGS,
    )
    from rbac_backend.services.arbitration_drafting.workflow_validation import (
        BLOCKING_QUALITY_FLAGS,
    )

    phantom = BLOCKING_QUALITY_FLAGS - PRODUCIBLE_QUALITY_FLAGS

    assert not phantom, (
        f"the filing gate blocks on flags no code can produce: {sorted(phantom)}"
    )
