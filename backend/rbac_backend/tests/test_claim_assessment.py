"""Clause-grounded claim assessment (Phase 4 / Module 5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.services.claim_assessment_service import (
    ClaimAssessmentService,
    build_assessment_query,
)


# --- query builder (pure) -------------------------------------------------


def test_query_is_type_specific_and_includes_claim_detail():
    claim = {
        "type": "eot",
        "title": "Monsoon delay",
        "description": "20-day stoppage due to abnormal rainfall",
        "contract_clauses": ["8.4", "20.1"],
    }
    q = build_assessment_query(claim)
    assert "extension of time" in q.lower()
    assert "Monsoon delay" in q
    assert "20-day stoppage" in q
    assert "8.4" in q and "20.1" in q
    # precedence + citation instruction always present
    assert "precedence" in q.lower() and "cite" in q.lower()


def test_query_falls_back_for_unknown_type():
    q = build_assessment_query({"type": "other", "title": "Misc"})
    assert "contractual basis" in q.lower()


# --- service: runs engine + persists --------------------------------------


class _Citation:
    def __init__(self, clause):
        self.clause_number = clause

    def model_dump(self):
        return {"clause_number": self.clause_number, "snippet": "..."}


class _QAResponse:
    def __init__(self):
        self.answer = "Entitlement is arguable under clause 8.4."
        self.citations = [_Citation("8.4")]
        self.trace = []


class _Retrieval:
    def __init__(self):
        self.calls = []

    async def contract_iterative_qa(self, request, current_user):
        self.calls.append(request)
        return _QAResponse()


class _Assessments:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc["_id"])


class _Audit:
    def __init__(self):
        self.events = []

    async def emit(self, **kw):
        self.events.append(kw)


class _DB:
    def __init__(self):
        self.claim_assessments = _Assessments()


@pytest.mark.asyncio
async def test_assess_runs_engine_and_persists_citations(monkeypatch):
    db = _DB()
    svc = ClaimAssessmentService(db)
    audit = _Audit()
    svc.audit = audit  # capture audit
    retrieval = _Retrieval()
    claim = {
        "_id": "c1",
        "type": "eot",
        "title": "Monsoon delay",
        "organization_id": "org-A",
        "project_id": "proj-A",
    }
    user = SimpleNamespace(id="u1")

    record = await svc.assess(claim, user, retrieval)

    # The engine was invoked with a contract-scoped, citation-required request.
    assert len(retrieval.calls) == 1
    req = retrieval.calls[0]
    assert req.filters.org_id == "org-A" and req.filters.project_id == "proj-A"
    assert req.require_citations is True
    assert req.filters.metadata.get("uploadType") == "contract"

    # Persisted record carries answer + citations + provenance, and audited.
    assert record["claim_id"] == "c1"
    assert record["answer"].startswith("Entitlement")
    assert record["citations"][0]["clause_number"] == "8.4"
    assert record["created_by"] == "u1"
    assert db.claim_assessments.docs and db.claim_assessments.docs[0]["_id"] == record["_id"]
    assert audit.events and audit.events[0]["action"] == "claim.assessed"
