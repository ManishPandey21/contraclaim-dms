"""Contract Document Appraisal (v2 Phase 1): completeness, generation, job
lifecycle, versioning, scope isolation."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.contract_appraisal import GenerateAppraisalRequest, ReportEditRequest
from rbac_backend.routers.contract_appraisal import (
    delete_appraisal,
    edit_appraisal,
    generate_appraisal,
)
from rbac_backend.services.contract_appraisal.generator import AppraisalGenerator, build_structured_output
from rbac_backend.services.contract_appraisal.service import AppraisalService, assess_completeness
from rbac_backend.services.contract_appraisal.prompts import STANDARD_DISCLAIMER, section_questions
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- completeness (pure) --------------------------------------------------


def test_completeness_complete_when_all_mandatory_present():
    present = {"Letter of Acceptance", "GCC", "SCC", "Employer's Requirements", "BOQ"}
    status, missing = assess_completeness(present)
    assert status.value == "complete" and missing == []


def test_completeness_incomplete_lists_missing():
    status, missing = assess_completeness({"Letter of Acceptance", "GCC"})
    assert status.value == "incomplete"
    assert any("SCC" in m or "Special" in m for m in missing)
    assert any("BOQ" in m or "Bill" in m for m in missing)


def test_completeness_requires_review_when_unknown():
    status, missing = assess_completeness(set())
    assert status.value == "requires_review" and missing == []


def test_completeness_classifies_employers_requirements_from_filename():
    # H1: a single Employer's Requirements upload (filename "...ER.pdf") must be
    # detected so it is NOT reported as missing — only the other four classes are.
    status, missing = assess_completeness({"contract", "KNPCC-05 Vol-3 (Part-I) ER.pdf"})
    assert status.value == "incomplete"
    assert not any("Employer" in m for m in missing)
    assert any("Letter of Acceptance" in m for m in missing)


def test_completeness_requires_review_when_only_generic_type():
    # H1: generic "contract" type + unclassifiable filename → honest "needs review",
    # never a false "all five missing" banner.
    status, missing = assess_completeness({"contract", "scan001.pdf"})
    assert status.value == "requires_review" and missing == []


def test_completeness_abbr_matches_whole_word_only():
    # H1: "er" must not match inside words like "tender"/"register".
    status, _ = assess_completeness({"tender register.pdf"})
    assert status.value == "requires_review"


# --- generator ------------------------------------------------------------


class _Citation:
    def __init__(self, clause, score=0.9):
        self.clause_number = clause
        self.score = score
        self.document_title = "GCC"
        self.page = 45

    def model_dump(self):
        return {"clause_number": self.clause_number, "score": self.score, "document_title": "GCC", "page": 45}


class _Resp:
    def __init__(self, answer, citations):
        self.answer = answer
        self.citations = citations
        self.trace = []


class _RetrievalSupported:
    def __init__(self):
        self.requests = []

    async def contract_iterative_qa(self, request, current_user):
        self.requests.append(request)
        return _Resp("The contract provides for this at clause 8.4.", [_Citation("8.4")])


class _RetrievalNotFound:
    async def contract_iterative_qa(self, request, current_user):
        return _Resp("Information not found", [])


class _RetrievalRaises:
    """A retrieval backend that fails mid-generation (LLM/vector error)."""

    async def contract_iterative_qa(self, request, current_user):
        raise RuntimeError("LLM backend unavailable")


@pytest.mark.asyncio
async def test_generator_assembles_all_sections_with_citations():
    retrieval = _RetrievalSupported()
    gen = AppraisalGenerator(retrieval)
    result = await gen.generate(
        organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=SimpleNamespace(id="u1")
    )
    n_sections = len(section_questions())
    assert len(result["sections"]) == n_sections
    # one retrieval call per section, each scoped to org/project
    assert len(retrieval.requests) == n_sections
    assert retrieval.requests[0].filters.org_id == "org-A"
    assert retrieval.requests[0].filters.project_id == "proj-A"
    assert retrieval.requests[0].require_citations is True
    # single document → retrieval scoped to it
    assert retrieval.requests[0].filters.document_id == "d1"
    # full support → confidence 1.0, disclaimer appended, citations aggregated
    assert result["confidence_score"] == 1.0
    assert STANDARD_DISCLAIMER in result["full_report_markdown"]
    assert len(result["citations"]) == n_sections
    assert result["executive_summary"].startswith("The contract")


@pytest.mark.asyncio
async def test_generator_flags_unsupported_sections():
    gen = AppraisalGenerator(_RetrievalNotFound())
    result = await gen.generate(
        organization_id="org-A", project_id="proj-A", document_ids=[], current_user=SimpleNamespace(id="u1")
    )
    assert all(s["supported"] is False for s in result["sections"])
    assert result["confidence_score"] == 0.0
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_unsupported_section_uses_selected_document_wording():
    # Grounding: where the selected document does not address a section, the
    # report must say exactly "Not found in the selected document." — never
    # infer, and never the old "uploaded documents" phrasing.
    gen = AppraisalGenerator(_RetrievalNotFound())
    result = await gen.generate(
        organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=SimpleNamespace(id="u1")
    )
    assert "Not found in the selected document." in result["full_report_markdown"]
    for section in result["sections"]:
        assert section["markdown"] == "Not found in the selected document."


@pytest.mark.asyncio
async def test_generator_propagates_section_error_no_placeholder():
    # A real generation error must NOT be swallowed into a placeholder section;
    # it propagates so the job fails and the user can retry.
    gen = AppraisalGenerator(_RetrievalRaises())
    with pytest.raises(RuntimeError, match="LLM backend unavailable"):
        await gen.generate(
            organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=SimpleNamespace(id="u1")
        )


def test_build_request_sets_grounding_document_ids():
    # Hard-scope: the selected document set is carried as grounding_document_ids
    # so retrieval can restrict evidence to it. Empty selection (whole-project
    # mode) stays unrestricted.
    gen = AppraisalGenerator(_RetrievalSupported())
    req = gen._build_request("q", "org-A", "proj-A", ["d1", "d2"])
    assert set(req.grounding_document_ids) == {"d1", "d2"}
    req_empty = gen._build_request("q", "org-A", "proj-A", [])
    assert req_empty.grounding_document_ids is None


# --- job lifecycle + persistence ------------------------------------------


class _Coll:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one(self, query, sort=None):
        if "_id" in query and not isinstance(query["_id"], dict):
            d = self.docs.get(query["_id"])
            return dict(d) if d else None
        matches = [d for d in self.docs.values() if all(d.get(k) == v for k, v in query.items() if not isinstance(v, dict))]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: d.get(key) or 0, reverse=direction < 0)
        return dict(matches[0]) if matches else None

    async def find_one_and_update(self, query, update, return_document=True):
        d = self.docs.get(query.get("_id"))
        if not d:
            return None
        d.update(update.get("$set", {}))
        for k, v in update.get("$inc", {}).items():
            d[k] = (d.get(k) or 0) + v
        self.docs[d["_id"]] = d
        return dict(d)

    async def update_one(self, query, update):
        d = self.docs.get(query.get("_id"))
        if d:
            d.update(update.get("$set", {}))
            for k, v in update.get("$inc", {}).items():
                d[k] = (d.get(k) or 0) + v

    async def delete_one(self, query):
        existed = query.get("_id") in self.docs
        self.docs.pop(query.get("_id"), None)
        return SimpleNamespace(deleted_count=1 if existed else 0)

    async def delete_many(self, query):
        to_drop = [k for k, d in self.docs.items() if all(d.get(f) == v for f, v in query.items())]
        for k in to_drop:
            del self.docs[k]
        return SimpleNamespace(deleted_count=len(to_drop))

    async def insert_many(self, docs):
        for d in docs:
            self.docs[d["_id"]] = dict(d)
        return SimpleNamespace(inserted_ids=[d["_id"] for d in docs])

    async def update_many(self, query, update):
        def _match(d):
            for k, v in query.items():
                dv = d.get(k)
                if isinstance(v, dict):
                    if "$in" in v and dv not in v["$in"]:
                        return False
                    if "$lt" in v and not (dv is not None and dv < v["$lt"]):
                        return False
                    if "$nin" in v and dv in v["$nin"]:
                        return False
                elif dv != v:
                    return False
            return True

        n = 0
        for d in self.docs.values():
            if _match(d):
                d.update(update.get("$set", {}))
                n += 1
        return SimpleNamespace(modified_count=n)

    def find(self, query):
        docs = [d for d in self.docs.values() if all(d.get(k) == v for k, v in query.items() if not isinstance(v, dict))]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                self._it = iter(self._items)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        return _Cursor(docs)


class _Docs:
    def find(self, *_a, **_k):
        class _C:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return _C()


class _DB:
    def __init__(self):
        self.contract_appraisal_jobs = _Coll()
        self.contract_appraisal_reports = _Coll()
        self.contract_appraisal_review_comments = _Coll()
        self.contract_obligations = _Coll()
        self.contract_risks = _Coll()
        self.contract_key_dates = _Coll()
        self.documents = _Docs()


@pytest.mark.asyncio
async def test_section_error_fails_job_without_saving_placeholder_report():
    # The core fix: a generation error must fail the job with the REAL error and
    # save NO report — instead of silently persisting a report whose sections read
    # "Requires Human Review (generation error)." and marking the job completed.
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    job = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)

    result = await svc.run_job(job["_id"], user, retrieval_service=_RetrievalRaises())

    assert result == {}
    assert len(db.contract_appraisal_reports.docs) == 0  # no placeholder report saved
    refreshed = await svc.get_job(job["_id"])
    assert refreshed["status"] == "failed"
    assert "LLM backend unavailable" in (refreshed.get("error_message") or "")


@pytest.mark.asyncio
async def test_run_job_persists_draft_v1_and_completes():
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    job = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)

    report = await svc.run_job(job["_id"], user, retrieval_service=_RetrievalSupported())

    assert report["status"] == "draft"
    assert report["report_version"] == 1
    assert report["is_locked"] is False
    refreshed_job = await svc.get_job(job["_id"])
    assert refreshed_job["status"] == "completed"
    assert refreshed_job["report_id"] == report["_id"]
    assert refreshed_job["progress"] == 100


@pytest.mark.asyncio
async def test_version_is_per_document_selection():
    # M2: a whole-project appraisal and a single-document appraisal version
    # independently instead of sharing one per-project counter.
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    j1 = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=[], current_user=user)
    r1 = await svc.run_job(j1["_id"], user, retrieval_service=_RetrievalSupported())
    j2 = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)
    r2 = await svc.run_job(j2["_id"], user, retrieval_service=_RetrievalSupported())
    assert r1["report_version"] == 1
    assert r2["report_version"] == 1  # distinct selection → its own counter, not 2


@pytest.mark.asyncio
async def test_reap_stuck_jobs_fails_only_old_running_jobs():
    # M1: jobs left RUNNING/GENERATING by a crash are reaped; recent and terminal
    # jobs are untouched.
    db = _DB()
    svc = AppraisalService(db)
    old = datetime.utcnow() - timedelta(hours=2)
    db.contract_appraisal_jobs.docs["j1"] = {"_id": "j1", "status": "running", "started_at": old}
    db.contract_appraisal_jobs.docs["j2"] = {"_id": "j2", "status": "generating", "started_at": datetime.utcnow()}
    db.contract_appraisal_jobs.docs["j3"] = {"_id": "j3", "status": "completed", "started_at": old}
    reaped = await svc.reap_stuck_jobs(max_age_seconds=60)
    assert reaped == 1
    assert db.contract_appraisal_jobs.docs["j1"]["status"] == "failed"
    assert db.contract_appraisal_jobs.docs["j2"]["status"] == "generating"
    assert db.contract_appraisal_jobs.docs["j3"]["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_before_run_produces_no_report():
    # H2: a job cancelled before generation starts must not run or create a report.
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    job = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)
    cancelled = await svc.cancel_job(job["_id"])
    assert cancelled["status"] == "cancelled"

    result = await svc.run_job(job["_id"], user, retrieval_service=_RetrievalSupported())
    assert result == {}
    assert (await svc.get_job(job["_id"]))["status"] == "cancelled"
    assert len(db.contract_appraisal_reports.docs) == 0


@pytest.mark.asyncio
async def test_cancel_completed_job_is_noop():
    # H2: cancellation must not clobber an already-finished job back to cancelled.
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    job = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)
    await svc.run_job(job["_id"], user, retrieval_service=_RetrievalSupported())
    after = await svc.cancel_job(job["_id"])
    assert after["status"] == "completed"


@pytest.mark.asyncio
async def test_regenerate_creates_new_version_and_supersedes_prior():
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    job1 = await svc.create_job(organization_id="org-A", project_id="proj-A", document_ids=["d1"], current_user=user)
    report1 = await svc.run_job(job1["_id"], user, retrieval_service=_RetrievalSupported())
    await svc.approve(report1, user)

    # regenerate → new job → v2; v1 is archived (superseded + locked), not overwritten
    job2 = await svc.regenerate(report1, user)
    report2 = await svc.run_job(job2["_id"], user, retrieval_service=_RetrievalSupported())
    assert report2["report_version"] == 2
    assert report2["_id"] != report1["_id"]
    v1 = await svc.get_report(report1["_id"])
    assert v1["report_version"] == 1
    assert v1["is_locked"] is True and v1["status"] == "superseded"
    # only the newest version is live for this selection
    live = await svc.find_existing_report("org-A", "proj-A", ["d1"])
    assert live["_id"] == report2["_id"]


# --- scope / RBAC at the endpoint -----------------------------------------


class _Allow:
    async def user_has_permission(self, *_a, **_k):
        return True


class _EntAllow:
    async def check_permission_entitlement(self, **_k):
        return True, "ok"


class _ScopeCursor:
    async def to_list(self, length=None):
        return []


class _ScopeColl:
    def find(self, *_a, **_k):
        return _ScopeCursor()


class _Projects:
    async def find_one(self, _query):
        return {"_id": "proj-A", "organization_id": "org-A"}


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()
    projects = _Projects()


class _Audit:
    async def emit(self, **_k):
        return None


def _policy():
    return PolicyService(
        permission_service=_Allow(),
        scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(),
        audit_service=_Audit(),
    )


def _user(org="org-A"):
    return SimpleNamespace(
        id="u1", roles=["orgadmin"], organization_id=org, organizations=[org], projects=["proj-A"], account_type="client_user",
    )


@pytest.mark.asyncio
async def test_generate_denies_cross_tenant():
    payload = GenerateAppraisalRequest(organization_id="org-B", project_id="proj-B", document_ids=[])
    with pytest.raises(HTTPException) as exc:
        await generate_appraisal(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_edit_locked_report_returns_409():
    db = _DB()
    # seed an approved/locked report owned by org-A
    locked = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A",
        "is_locked": True, "status": "approved", "report_version": 1,
    }
    db.contract_appraisal_reports.docs["r1"] = locked
    with pytest.raises(HTTPException) as exc:
        await edit_appraisal(
            "r1", ReportEditRequest(executive_summary="x"), db=db, current_user=_user(org="org-A"), policy=_policy()
        )
    assert exc.value.status_code == 409


# --- Phase 2: structured output + registers -------------------------------


def _section(key, citations, confidence=0.9):
    return {"key": key, "title": key, "markdown": "x", "citations": citations, "supported": bool(citations), "confidence": confidence}


def test_structured_output_maps_citations_to_register_rows():
    cite_high = {"clause_number": "8.4", "score": 0.92, "document_title": "GCC", "page": 45, "snippet": "EOT clause"}
    cite_low = {"clause_number": "60.1", "score": 0.6, "document_title": "GCC", "page": 80, "snippet": "payment"}
    sections = [
        _section("employer_obligations", [cite_high]),
        _section("contractor_obligations", [cite_low], confidence=0.6),
        _section("risk_register", [cite_high]),
        _section("time_and_delay", [cite_high]),
        _section("scope_of_work", [cite_high]),  # not register-bearing
    ]
    so = build_structured_output(sections, confidence_score=0.8)
    assert len(so["obligations"]) == 2
    assert {o["party"] for o in so["obligations"]} == {"employer", "contractor"}
    assert len(so["risks"]) == 1 and len(so["key_dates"]) == 1
    # every row keeps a citation; low-confidence flagged for human review
    assert so["obligations"][0]["clause_reference"] == "8.4"
    low = next(o for o in so["obligations"] if o["party"] == "contractor")
    assert low["verification_status"] == "requires_human_review"
    assert so["overall_appraisal"]["overall_risk_rating"] == "medium"  # coverage 0.8


@pytest.mark.asyncio
async def test_create_registers_populates_and_is_idempotent():
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    report = {
        "_id": "rep1", "organization_id": "org-A", "project_id": "proj-A",
        "structured_output": {
            "obligations": [{"party": "employer", "obligation_title": "Give access", "clause_reference": "2.1", "confidence_score": 0.9, "verification_status": "ai_generated"}],
            "risks": [{"risk_title": "Time-bar", "clause_reference": "20.1", "confidence_score": 0.9, "verification_status": "ai_generated"}],
            "key_dates": [],
        },
    }
    counts = await svc.create_registers(report, user)
    assert counts == {"obligations": 1, "risks": 1, "key_dates": 0}
    rows = await svc.list_register("obligations", {"organization_id": "org-A"})
    assert len(rows) == 1 and rows[0]["report_id"] == "rep1"

    # re-running replaces, never duplicates
    await svc.create_registers(report, user)
    rows2 = await svc.list_register("obligations", {"organization_id": "org-A"})
    assert len(rows2) == 1


@pytest.mark.asyncio
async def test_update_register_item_verification():
    db = _DB()
    svc = AppraisalService(db)
    user = SimpleNamespace(id="u1", organization_id="org-A")
    db.contract_risks.docs["x1"] = {"_id": "x1", "organization_id": "org-A", "project_id": "proj-A", "report_id": "rep1", "verification_status": "requires_human_review"}
    updated = await svc.update_register_item("risks", "x1", {"verification_status": "verified"}, user)
    assert updated["verification_status"] == "verified"


def test_build_pdf_returns_pdf_bytes():
    report = {"full_report_markdown": "# Title\n\n## 1. Executive Summary\nHello.", "report_version": 1}
    data = AppraisalService.build_pdf(report)
    assert data[:4] == b"%PDF"


# --- Phase 3+: generate-once lifecycle ------------------------------------


def test_generator_request_uses_full_coverage_and_doc_scope():
    gen = AppraisalGenerator(_RetrievalSupported())
    req = gen._build_request("q", "org-A", "proj-A", ["d1"])
    assert req.limit == 50
    assert req.filters.document_id == "d1"
    req2 = gen._build_request("q", "org-A", "proj-A", [])
    assert req2.filters.document_id is None


def test_generator_request_respects_configured_breadth(monkeypatch):
    # M3: retrieval breadth is tunable per deployment.
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "CONTRACT_APPRAISAL_RETRIEVAL_LIMIT", 30, raising=False)
    monkeypatch.setattr(settings, "CONTRACT_APPRAISAL_QA_MAX_ITERATIONS", 5, raising=False)
    req = AppraisalGenerator(_RetrievalSupported())._build_request("q", "org-A", "proj-A", ["d1"])
    assert req.limit == 30 and req.max_iterations == 5


def test_generator_request_clamps_breadth_to_engine_bounds(monkeypatch):
    # M3: over-eager settings are clamped to the engine's bounds (limit<=50, iter<=5)
    # so they can't raise a validation error.
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "CONTRACT_APPRAISAL_RETRIEVAL_LIMIT", 999, raising=False)
    monkeypatch.setattr(settings, "CONTRACT_APPRAISAL_QA_MAX_ITERATIONS", 99, raising=False)
    req = AppraisalGenerator(_RetrievalSupported())._build_request("q", "org-A", "proj-A", ["d1"])
    assert req.limit == 50 and req.max_iterations == 5


@pytest.mark.asyncio
async def test_find_existing_report_matches_document_set():
    db = _DB()
    db.contract_appraisal_reports.docs["r1"] = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A", "document_ids": ["d1"], "status": "draft",
    }
    db.contract_appraisal_reports.docs["r2"] = {
        "_id": "r2", "organization_id": "org-A", "project_id": "proj-A", "document_ids": [], "status": "draft",
    }
    svc = AppraisalService(db)
    assert (await svc.find_existing_report("org-A", "proj-A", ["d1"]))["_id"] == "r1"
    assert (await svc.find_existing_report("org-A", "proj-A", []))["_id"] == "r2"  # whole-project is distinct
    assert await svc.find_existing_report("org-A", "proj-A", ["d9"]) is None


@pytest.mark.asyncio
async def test_find_existing_ignores_superseded_and_rejected():
    db = _DB()
    db.contract_appraisal_reports.docs["r1"] = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A", "document_ids": ["d1"], "status": "superseded",
    }
    svc = AppraisalService(db)
    assert await svc.find_existing_report("org-A", "proj-A", ["d1"]) is None


@pytest.mark.asyncio
async def test_generate_denied_when_report_exists():
    db = _DB()
    db.contract_appraisal_reports.docs["r1"] = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A", "document_ids": ["d1"], "status": "draft",
    }
    payload = GenerateAppraisalRequest(organization_id="org-A", project_id="proj-A", document_ids=["d1"])
    with pytest.raises(HTTPException) as exc:
        await generate_appraisal(payload, db=db, current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 409
    assert exc.value.detail["report_id"] == "r1"


@pytest.mark.asyncio
async def test_delete_locked_report_returns_409():
    # H4: an approved/locked report cannot be deleted (immutability is preserved);
    # regeneration is the path to a new version.
    db = _DB()
    db.contract_appraisal_reports.docs["r1"] = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A",
        "is_locked": True, "status": "approved", "document_ids": ["d1"],
    }
    with pytest.raises(HTTPException) as exc:
        await delete_appraisal("r1", db=db, current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 409
    assert "r1" in db.contract_appraisal_reports.docs  # not deleted


@pytest.mark.asyncio
async def test_delete_report_cascades_registers_and_comments():
    db = _DB()
    report = {"_id": "r1", "organization_id": "org-A", "project_id": "proj-A"}
    db.contract_appraisal_reports.docs["r1"] = dict(report)
    db.contract_obligations.docs["o1"] = {"_id": "o1", "report_id": "r1"}
    db.contract_risks.docs["x1"] = {"_id": "x1", "report_id": "r1"}
    db.contract_appraisal_review_comments.docs["c1"] = {"_id": "c1", "report_id": "r1"}

    await AppraisalService(db).delete_report(report, _user(org="org-A"))
    assert "r1" not in db.contract_appraisal_reports.docs
    assert db.contract_obligations.docs == {}
    assert db.contract_risks.docs == {}
    assert db.contract_appraisal_review_comments.docs == {}


@pytest.mark.asyncio
async def test_delete_endpoint_then_generate_allowed():
    db = _DB()
    db.contract_appraisal_reports.docs["r1"] = {
        "_id": "r1", "organization_id": "org-A", "project_id": "proj-A", "document_ids": ["d1"], "status": "draft",
    }
    # delete clears the report so the selection is free again
    await delete_appraisal("r1", db=db, current_user=_user(org="org-A"), policy=_policy())
    assert "r1" not in db.contract_appraisal_reports.docs
    svc = AppraisalService(db)
    assert await svc.find_existing_report("org-A", "proj-A", ["d1"]) is None
