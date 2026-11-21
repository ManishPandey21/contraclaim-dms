"""4B-45 (D-26): existing callers of POST /arbitration/drafts keep working, over real HTTP,
MongoDB and RBAC.

Phase 4 made a case-less draft a 422 unless ``scratch: true`` was sent, which broke any
caller written before it. ``ARBITRATION_CASELESS_DRAFT_MODE`` now decides:
``implicit_scratch`` (default) creates a scratch draft and says so (Deprecation header,
a warning, a logged caller); ``strict`` refuses with ``scratch_required``. A scratch draft
can be neither approved nor exported for filing, so no gate is bypassed in either mode.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from rbac_backend.tests.integration.test_arbitration_case_lookup_mongo import DRAFTS
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BODY = {
    "organization_id": ORG_A,
    "project_id": PROJ_A1,
    "draft_type": "statement_of_claim",
    "party_role": "claimant",
    "title": "Statement of Claim",
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _id(response: Any) -> str:
    body = response.json()
    return str(body.get("id") or body.get("_id"))


class _Captured(logging.Handler):
    """Collects records on the router's own logger.

    ``caplog`` listens on the root logger. When this is the first test in the
    process to start the app, the app's logging setup replaces the root handlers
    and the warning never reaches it, so the test passed or failed by its
    position in the run. A handler on the emitting logger does not depend on that.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_an_older_caller_gets_a_scratch_draft_with_a_deprecation_and_no_gate_is_bypassed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            captured = _Captured()
            router_logger = logging.getLogger("rbac_backend.routers.arbitration_drafting")
            monkeypatch.setattr(router_logger, "disabled", False)
            if router_logger.getEffectiveLevel() > logging.WARNING:
                monkeypatch.setattr(router_logger, "level", logging.WARNING)
            router_logger.addHandler(captured)
            try:
                created = await env.client.request(
                    "POST", DRAFTS, json=BODY, headers={**env.headers("org_admin"), "User-Agent": "legacy-integration/1.4"}
                )
            finally:
                router_logger.removeHandler(captured)
            assert created.status_code == 201, created.text[:300]
            assert created.headers.get("Deprecation") == "true"
            assert "scratch" in created.headers.get("Warning", "")
            assert any("scratch draft" in warning for warning in created.json()["warnings"])
            draft_id = _id(created)
            stored = await env.db.arbitration_drafts.find_one({"_id": draft_id})
            assert stored["is_scratch"] is True and not stored.get("case_id")
            logged = [message for message in captured.messages if "implicit_scratch" in message]
            assert logged and "legacy-integration/1.4" in logged[0] and "user-org_admin" in logged[0]

            # Give it a passing version: it is still neither approvable nor exportable for filing.
            await env.db.arbitration_drafts.update_one({"_id": draft_id}, {"$set": {"current_version": 1}})
            await env.db.arbitration_draft_versions.insert_one(
                {"_id": f"{draft_id}-v1", "draft_id": draft_id, "version": 1, "full_markdown": "text",
                 "validation_status": "passed", "missing_evidence": [], "structured_output": {"approval_blockers": []},
                 "source_ledger": [], "created_by": "someone-else"}
            )
            approve = await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/approve")
            assert approve.status_code == 409 and "scratch" in approve.text, approve.text[:300]
            await env.db.arbitration_drafts.update_one(
                {"_id": draft_id},
                {"$set": {"status": "approved", "is_locked": True, "approved_version": 1, "approved_version_id": f"{draft_id}-v1"}},
            )
            export = await env.call("GET", "org_admin", f"{DRAFTS}/{draft_id}/export/docx")
            assert export.status_code == 409 and "scratch" in export.text, export.text[:300]

            # A caller that asks for a scratch draft by name gets no deprecation.
            explicit = await env.call("POST", "org_admin", DRAFTS, json={**BODY, "scratch": True})
            assert explicit.status_code == 201, explicit.text[:300]
            assert "Deprecation" not in explicit.headers and explicit.json()["warnings"] == []

    _run(scenario())


def test_strict_mode_refuses_a_caseless_draft_that_does_not_ask_to_be_scratch(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.core.config import settings

        monkeypatch.setattr(settings, "ARBITRATION_CASELESS_DRAFT_MODE", "strict")
        async with _env(monkeypatch) as env:
            refused = await env.call("POST", "org_admin", DRAFTS, json=BODY)
            assert refused.status_code == 422, refused.text[:300]
            detail = refused.json()["detail"]
            assert detail["code"] == "scratch_required" and "scratch=true" in detail["detail"]
            assert await env.db.arbitration_drafts.count_documents({}) == 0
            explicit = await env.call("POST", "org_admin", DRAFTS, json={**BODY, "scratch": True})
            assert explicit.status_code == 201 and "Deprecation" not in explicit.headers

    _run(scenario())
