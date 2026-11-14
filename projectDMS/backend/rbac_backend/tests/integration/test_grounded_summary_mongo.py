"""Release 1.2 grounded summary over real HTTP, Mongo and RBAC.

Reuses the Release 1.1.1 extraction-review environment: genuine page evidence
published through ``DocumentPageStore``, signed JWTs through the real
``get_current_user`` / ``ActiveScope`` / ``PolicyService`` chain, no dependency
override. The GLM 4299 shape is the fixture: page 5's dense tables fail the
quality gate and hold the document for human review.

What this pins:

* the summary route answers exactly who may open the document;
* a document held for review serves no summary, and generation is refused;
* approval regenerates it from the same revision, with page 5 marked
  machine-failed and human-approved - the failure never disappears;
* a reprocess makes the stored summary stale and it is no longer served;
* the legacy ``summary`` field and ``GET /documents/{id}`` are unchanged.

Opt-in: set ``GROUNDED_SUMMARY_MONGODB_URI`` to a disposable replica set.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import pytest
from fastapi import FastAPI

from rbac_backend.tests.integration import test_extraction_review_mongo as review_env
from rbac_backend.tests.integration.test_extraction_review_mongo import (
    DOC_R,
    DOC_R_B,
    DOC_R_DEL,
    DOC_R_DUP,
    ORG_A,
    PROJ_A1,
    PROJ_A2,
    _env,
    _glm_pages,
    _publish_evidence,
    _run,
)

MONGODB_URI_ENV = "GROUNDED_SUMMARY_MONGODB_URI"

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    # The shared environment reads its own variable; point it at ours.
    monkeypatch.setenv(review_env.MONGODB_URI_ENV, uri)
    monkeypatch.delenv("GROUNDED_SUMMARY_LLM_ENABLED", raising=False)

    def _app() -> FastAPI:
        from rbac_backend.routers.document_extraction_review import router as review_router
        from rbac_backend.routers.document_grounded_summary import router as summary_router
        from rbac_backend.routers.documents import router as documents_router

        app = FastAPI()
        app.include_router(review_router, prefix="/api")
        app.include_router(summary_router, prefix="/api")
        app.include_router(documents_router, prefix="/api")
        return app

    monkeypatch.setattr(review_env, "_app", _app)


async def _summary(env: Any, persona: str, doc: Any, project: Optional[str] = None):
    return await env.client.get(f"/api/documents/{doc}/grounded-summary", headers=env.headers(persona, project))


async def _generate(env: Any, persona: str, doc: Any, project: Optional[str] = None):
    return await env.client.post(
        f"/api/documents/{doc}/grounded-summary/generate", headers=env.headers(persona, project)
    )


def test_held_document_serves_no_summary_and_cannot_generate_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await _summary(env, "project_admin", DOC_R)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["summary_status"] == "blocked_for_review"
            assert body["detailed_summary"] is None and body["short_summary"] is None

            generated = await _generate(env, "project_admin", DOC_R)
            assert generated.status_code == 200, generated.text
            assert generated.json()["summary_status"] == "blocked_for_review"
            stored = await env.document(DOC_R)
            assert "detailed_summary" not in stored

    _run(scenario())


def test_approval_regenerates_the_summary_with_the_machine_failure_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            before = await env.document(DOC_R)
            response = await env.decide("project_admin", DOC_R, 5)
            assert response.status_code == 200, response.text
            document = await env.document(DOC_R)
            assert document["processing_status"] == "completed"
            assert document.get("summary") == before.get("summary")  # legacy field untouched

            body = (await _summary(env, "project_admin", DOC_R)).json()
            assert body["summary_status"] in {"current", "degraded"}, body
            detailed = body["detailed_summary"]
            head = await env.db.document_extraction_heads.find_one({"document_id": str(DOC_R)})
            assert detailed["generated_from"]["canonical_revision"] == head["canonical_revision"]
            assert detailed["generated_from"]["canonical_sha256"] == head["canonical_sha256"]
            assert detailed["generated_from"]["review_resolution"]["approved_pages"] == [5]
            qualification = [u for u in detailed["uncertainties"] if "Page 5 failed automated quality checks" in u["text"]]
            assert qualification and "numeric_integrity" in qualification[0]["text"]
            page5 = [f for f in detailed["facts"] if f["origin"] == "canonical_text" and 5 in f["page_numbers"]]
            assert all(f["verification_status"] == "human_approved" for f in page5)
            # The machine verdict on the page row is untouched.
            row = await env.db.document_ocr_pages.find_one({"document_id": str(DOC_R), "page_number": 5})
            assert row["quality_verdict"] == "fail" and row["needs_review"] is True

    _run(scenario())


@pytest.mark.parametrize(
    "persona,doc,project,expected",
    [
        ("project_admin", DOC_R_B, PROJ_A1, 404),  # foreign organisation's record outside the selection
        ("foreign_org_admin", DOC_R, PROJ_A1, 403),  # foreign organisation widening its selection
        ("superadmin", DOC_R, PROJ_A2, 403),  # super admin obeys the active selection
        ("project_admin", DOC_R_DEL, PROJ_A1, 404),  # deleted
    ],
)
def test_summary_inherits_the_document_gate(monkeypatch: pytest.MonkeyPatch, persona: str, doc: Any,
                                            project: str, expected: int) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await _summary(env, persona, doc, project)
            assert response.status_code in {expected, 403, 404} and response.status_code != 200, response.text
            assert response.status_code != 401  # a scope refusal is never a forced logout

    _run(scenario())


def test_quarantined_duplicate_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            response = await _summary(env, "project_admin", DOC_R_DUP)
            assert response.status_code == 200, response.text
            assert response.json()["summary_status"] == "withheld"
            assert response.json()["detailed_summary"] is None

    _run(scenario())


def test_reprocess_makes_the_summary_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            assert (await _summary(env, "project_admin", DOC_R)).json()["detailed_summary"]

            await _publish_evidence(env.db, DOC_R, ORG_A, PROJ_A1, "job-reprocess", _glm_pages(review_pages=()))
            body = (await _summary(env, "project_admin", DOC_R)).json()
            assert body["summary_status"] == "stale"
            assert body["detailed_summary"] is None and body["short_summary"] is None

            regenerated = await _generate(env, "project_admin", DOC_R)
            assert regenerated.status_code == 200, regenerated.text
            assert regenerated.json()["summary_status"] in {"current", "degraded"}
            assert regenerated.json()["generated_from"]["canonical_revision"] == 2

    _run(scenario())


def test_document_payload_does_not_change(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as (env, _):
            assert (await env.decide("project_admin", DOC_R, 5)).status_code == 200
            response = await env.client.get(f"/api/documents/{DOC_R}", headers=env.headers("project_admin"))
            assert response.status_code == 200, response.text
            payload = response.json()
            assert "detailed_summary" not in payload and "short_summary" not in payload

    _run(scenario())
