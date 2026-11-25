"""4B-32: submission section editing, with an "edited" mark and provenance kept.
Real HTTP, real MongoDB replica set, real RBAC.

The plan's proof: recompose does not overwrite an edited section without confirmation.
Also: an edit keeps every paragraph's sources and is marked with who and when; text that
states a figure the record does not is refused; a stale version is refused; a viewer with
restricted evidence cannot edit (D-25); the export carries the edited text.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_submission_evidence_restricted_mongo import SUBMISSION, _composed

pytestmark = pytest.mark.integration

SECTION = "executive_summary"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _view(env: Any, persona: str = "org_admin") -> dict:
    response = await env.call("GET", persona, SUBMISSION)
    assert response.status_code == 200, response.text[:300]
    return response.json()


def _section(view: dict, key: str = SECTION) -> dict:
    return next(item for item in view["sections"] if item["section"]["key"] == key)


async def _stored_version(env: Any) -> int:
    return int((await env.db.claim_submissions.find_one({"claim_id": CLAIM}))["version"])


def test_an_edit_is_marked_keeps_its_sources_and_stays_on_the_record(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _composed(env)
            before = _section(await _view(env))["section"]
            texts = [paragraph["text"] for paragraph in before["paragraphs"]]
            edited = ["In short: " + texts[0], *texts[1:]]
            version = await _stored_version(env)

            invented = await env.call("PUT", "org_admin", f"{SUBMISSION}/sections/{SECTION}",
                                      json={"version": version, "paragraphs": [texts[0] + " The sum claimed is INR 99,99,999.", *texts[1:]]})
            assert invented.status_code == 422 and _code(invented) == "submission_edit_not_on_record", invented.text[:300]
            assert await _stored_version(env) == version

            stale = await env.call("PUT", "org_admin", f"{SUBMISSION}/sections/{SECTION}",
                                   json={"version": version + 1, "paragraphs": edited})
            assert stale.status_code == 409, stale.text[:300]
            fewer = await env.call("PUT", "org_admin", f"{SUBMISSION}/sections/{SECTION}",
                                   json={"version": version, "paragraphs": edited[:1] + edited})
            assert fewer.status_code == 422, fewer.text[:300]

            saved = await env.call("PUT", "org_admin", f"{SUBMISSION}/sections/{SECTION}",
                                   json={"version": version, "paragraphs": edited})
            assert saved.status_code == 200, saved.text[:300]
            after = _section(saved.json())["section"]
            assert after["edited_by"] == "user-org_admin" and after["edited_at"]
            assert after["paragraphs"][0]["text"].startswith("In short: ") and after["paragraphs"][0]["edited"] is True
            assert [p["sources"] for p in after["paragraphs"]] == [p["sources"] for p in before["paragraphs"]]
            audit = await env.db.audit_events.find_one({"action": "claim.submission_section_edited"})
            assert audit and audit["actor_id"] == "user-org_admin" and audit["after"]["section"] == SECTION

            exported = await env.call("GET", "org_admin", f"{SUBMISSION}/export?format=docx")
            assert exported.status_code == 200, exported.text[:300]
            xml = zipfile.ZipFile(io.BytesIO(exported.content)).read("word/document.xml").decode()
            assert "In short:" in xml

            # D-25: a viewer for whom evidence is restricted cannot rewrite text they cannot fully see.
            denied = await env.call("PUT", "narrow", f"{SUBMISSION}/sections/{SECTION}",
                                    json={"version": await _stored_version(env), "paragraphs": edited})
            assert denied.status_code == 403 and _code(denied) == "evidence_restricted", denied.text[:300]

    _run(scenario())


def test_recompose_never_overwrites_an_edit_without_confirmation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _composed(env)
            texts = [p["text"] for p in _section(await _view(env))["section"]["paragraphs"]]
            edited = ["In short: " + texts[0], *texts[1:]]
            saved = await env.call("PUT", "org_admin", f"{SUBMISSION}/sections/{SECTION}",
                                   json={"version": await _stored_version(env), "paragraphs": edited})
            assert saved.status_code == 200, saved.text[:300]

            # Asked for by name, not confirmed: refused, naming the section, nothing changed.
            refused = await env.call("POST", "org_admin", f"{SUBMISSION}/compose", json={"sections": [SECTION]})
            assert refused.status_code == 409 and _code(refused) == "submission_section_edited", refused.text[:300]
            assert refused.json()["detail"]["sections"] == [SECTION]
            assert _section(await _view(env))["section"]["paragraphs"][0]["text"].startswith("In short: ")

            # The record changes so the section is stale: a general re-compose keeps the edit, shown stale.
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"title": "Delay to access - revised title"}})
            kept = await env.call("POST", "org_admin", f"{SUBMISSION}/compose", json={"sections": []})
            assert kept.status_code == 200, kept.text[:300]
            section = _section(kept.json())
            assert section["section"]["paragraphs"][0]["text"].startswith("In short: ")
            assert section["stale"] is True and section["section"]["edited_by"] == "user-org_admin"

            # Confirmed: replaced by a fresh composition, and no longer marked edited.
            replaced = await env.call("POST", "org_admin", f"{SUBMISSION}/compose",
                                      json={"sections": [SECTION], "confirm_overwrite": [SECTION]})
            assert replaced.status_code == 200, replaced.text[:300]
            section = _section(replaced.json())
            assert not section["section"]["paragraphs"][0]["text"].startswith("In short: ")
            assert section["section"]["edited_by"] is None and section["stale"] is False

    _run(scenario())
