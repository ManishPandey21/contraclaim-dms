"""Phase 1 of the multi-agent letter-drafting workflow:

Document Understanding Agent — the incoming analyzer consumes the AI metadata
already stored on the incoming document (subject, letter no., parties, summary,
contractual clauses, key reply points, linked references) with regex as
fallback; the reply matrix is seeded from Key Reply Points; and the
Evidence Retrieval Agent adds structured clause records, register rows
(key dates / variations / bank guarantees) and the previous-position source.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.models.letter_drafting import (
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
    SourceEvidence,
)
from rbac_backend.services.letter_drafting.context import DraftContextBuilder
from rbac_backend.services.letter_drafting.incoming_analyzer import IncomingLetterAnalyzer
from rbac_backend.services.letter_drafting.planning import PlanningSheetBuilder


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeDocumentService:
    def __init__(self, doc=None):
        self.doc = doc

    async def get_documents_by_ids(self, ids):
        return [self.doc] if self.doc is not None else []


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **k):
        return self

    def limit(self, _n):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.last_query = None

    def find(self, query):
        self.last_query = query
        out = []
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                out.append(dict(doc))
        return _Cursor(out)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


LETTER = SimpleNamespace(
    id="letter-1",
    subject="Reply to EOT notice",
    recipient="Typsa JV",
    content=None,
    strategy_role="contractor",
    reference=None,
)

STORED_DOC = SimpleNamespace(
    id="doc-1",
    subject="Extension of Time for sewer diversion",
    letterNo="AFC/PM/KNPCC-06/2308",
    date=datetime(2023, 11, 17),
    from_="AFCONS - SAM India Consortium",
    to="Typsa - Italferr JV",
    summary="Contractor notified delay events and requested EOT under Clause 8.4.",
    contractual_clauses=["GCC 8.4", "SCC 9.2"],
    key_reply_points=[
        "Confirm delay notice validity under Clause 8.4",
        "Reserve rights on prolongation cost",
    ],
    reference=[{"letter_no": "AFC/PM/KNPCC-06/2201", "date": "01-10-2023"}],
    full_text=None,
    ocrText=None,
    content=None,
)


# --------------------------------------------------------------------------- #
# Document Understanding Agent: stored metadata first
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_analyzer_uses_stored_metadata():
    analyzer = IncomingLetterAnalyzer()
    request = DraftRunCreateRequest(incoming_document_id="doc-1")

    analysis = await analyzer.analyze(LETTER, request, FakeDocumentService(STORED_DOC))

    assert analysis.letter_no == "AFC/PM/KNPCC-06/2308"
    assert analysis.letter_date == "2023-11-17"
    assert analysis.sender == "AFCONS - SAM India Consortium"
    assert analysis.subject == "Extension of Time for sewer diversion"
    # Stored AI metadata is authoritative.
    assert analysis.key_reply_points == [
        "Confirm delay notice validity under Clause 8.4",
        "Reserve rights on prolongation cost",
    ]
    assert analysis.linked_references == ["AFC/PM/KNPCC-06/2201"]
    # Stored contractual clauses lead the cited-clause list.
    assert analysis.clauses_cited[:2] == ["GCC 8.4", "SCC 9.2"]
    assert analysis.extraction_confidence == 0.85


@pytest.mark.asyncio
async def test_analyzer_regex_fallback_without_stored_metadata():
    analyzer = IncomingLetterAnalyzer()
    request = DraftRunCreateRequest(
        points="The Engineer cites Clause 8.4 and requires a reply within 14 days."
    )

    analysis = await analyzer.analyze(LETTER, request, FakeDocumentService(None))

    assert "8.4" in analysis.clauses_cited
    assert analysis.key_reply_points == []
    assert analysis.extraction_confidence == 0.65


@pytest.mark.asyncio
async def test_analyzer_no_text_no_metadata_low_confidence():
    analyzer = IncomingLetterAnalyzer()
    request = DraftRunCreateRequest()
    letter = SimpleNamespace(
        id="l2", subject=None, recipient=None, content=None, strategy_role=None, reference=None
    )
    analysis = await analyzer.analyze(letter, request, FakeDocumentService(None))
    assert analysis.extraction_confidence == 0.25


# --------------------------------------------------------------------------- #
# Reply matrix seeded from Key Reply Points
# --------------------------------------------------------------------------- #
def test_reply_matrix_leads_with_key_reply_points():
    builder = PlanningSheetBuilder()
    analysis = IncomingLetterAnalysis(
        main_request="Grant EOT of 60 days",
        key_reply_points=[
            "Confirm delay notice validity under Clause 8.4",
            "Reserve rights on prolongation cost",
        ],
    )
    request = DraftRunCreateRequest(points="Address programme impact")

    rows = builder._reply_matrix(request, [], analysis)
    incoming = [row.incoming_point for row in rows]
    assert incoming[0] == "Confirm delay notice validity under Clause 8.4"
    assert incoming[1] == "Reserve rights on prolongation cost"
    assert "Grant EOT of 60 days" in incoming
    assert "Address programme impact" in incoming


# --------------------------------------------------------------------------- #
# Evidence Retrieval Agent: clause records + registers + previous position
# --------------------------------------------------------------------------- #
def _builder(db) -> DraftContextBuilder:
    return DraftContextBuilder(
        document_service=SimpleNamespace(),
        conversation_service=SimpleNamespace(),
        contract_service=SimpleNamespace(),
        graph_service=SimpleNamespace(enabled=False),
        db=db,
    )


@pytest.mark.asyncio
async def test_clause_record_sources_use_structured_records():
    db = FakeDB()
    db["contract_clauses"].docs = [
        {
            "clause_uid": "uid-84",
            "org_id": "org-A",
            "project_id": "proj-A",
            "is_current": True,
            "is_authorised_for_ai": True,
            "clause_no": "8.4",
            "clause_title": "Extension of Time",
            "cleaned_text": "The Contractor shall be entitled to an extension of time...",
            "page_start": 52,
            "page_end": 53,
            "document_id": "doc-gcc",
            "document_type": "GCC",
        },
        {  # superseded record must be excluded by the scope query
            "clause_uid": "uid-old",
            "org_id": "org-A",
            "project_id": "proj-A",
            "is_current": False,
            "is_authorised_for_ai": True,
            "clause_no": "8.4",
            "cleaned_text": "old extension of time text",
        },
    ]
    builder = _builder(db)
    request = DraftRunCreateRequest(clauses_to_consider=["8.4"], purpose="extension of time reply")

    warnings: list = []
    sources = await builder._clause_record_sources(LETTER, request, "org-A", "proj-A", warnings)

    assert len(sources) == 1
    src = sources[0]
    assert src.source_id == "clause_record:uid-84"
    assert src.clause_number == "8.4"
    assert src.allowed_use == "clause"
    assert src.page_numbers == [52, 53]
    assert src.metadata.get("structured") is True
    # Scope + lifecycle filters were part of the query itself.
    query = db["contract_clauses"].last_query
    assert query["org_id"] == "org-A" and query["is_current"] is True


@pytest.mark.asyncio
async def test_register_sources_cover_key_dates_variations_bgs():
    db = FakeDB()
    scope = {"organization_id": "org-A", "project_id": "proj-A"}
    db["key_date_milestones"].docs = [
        {**scope, "_id": "kd1", "title": "Section 1 completion",
         "current_approved_key_date": datetime(2026, 3, 1), "remarks": "EOT-1 applied"},
    ]
    db["variations"].docs = [
        {**scope, "_id": "v1", "variation_number": "VO-07",
         "description": "Additional sewer diversion works", "submitted_amount": 4500000},
    ]
    db["bank_guarantees"].docs = [
        {**scope, "_id": "bg1", "bg_number": "BG-123", "bg_type": "performance",
         "bg_amount": 10000000, "currency": "INR", "bg_expiry_date": datetime(2026, 6, 30),
         "bg_status": "valid"},
    ]
    builder = _builder(db)

    warnings: list = []
    sources = await builder._register_sources("org-A", "proj-A", warnings)

    labels = [s.label for s in sources]
    assert any(label.startswith("Key date:") for label in labels)
    assert any(label.startswith("Variation") for label in labels)
    assert any(label.startswith("Bank guarantee") for label in labels)
    assert all(s.allowed_use == "fact" for s in sources)
    assert not warnings


@pytest.mark.asyncio
async def test_register_sources_scoped_to_project():
    db = FakeDB()
    db["variations"].docs = [
        {"organization_id": "org-B", "project_id": "proj-B", "_id": "v9",
         "variation_number": "VO-99", "description": "other org"},
    ]
    builder = _builder(db)
    sources = await builder._register_sources("org-A", "proj-A", [])
    assert sources == []


def test_previous_position_promotes_latest_prior_letter():
    prior = [
        SourceEvidence(
            source_id="prior:1", source_type="prior_correspondence", allowed_use="history_only",
            label="Old letter", text="early position", letter_id="l-1",
        ),
        SourceEvidence(
            source_id="prior:2", source_type="prior_correspondence", allowed_use="history_only",
            label="GC reply 2310", text="We maintained entitlement under Clause 8.4.",
            letter_id="l-2",
        ),
    ]
    promoted = DraftContextBuilder._previous_position_sources(prior, "org-A", "proj-A")
    assert len(promoted) == 1
    src = promoted[0]
    assert src.allowed_use == "fact"
    assert src.label.startswith("Previous position")
    assert src.letter_id == "l-2"
    assert src.metadata.get("previous_position") is True
