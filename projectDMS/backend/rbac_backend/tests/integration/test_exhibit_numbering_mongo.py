"""4B-35 (D-17, M-05 part): exhibit numbers are unique per case, under concurrency.

Numbering was max + 1 over the case's document index. The startup indexes
(``core.database.ensure_indexes``) already hold a unique index on
``(case_id, exhibit_id)``, so two rows created at once could not share a
number, but the loser failed with an unhandled duplicate-key error (a 500).
Now an assigned number that loses the race is re-assigned; a number the user
chose that is taken is refused with 409.

Opt-in: ``CLAIM_ESCALATION_MONGODB_URI`` (a disposable replica set); run in
CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.arbitration_drafting import ArbitrationCaseCreate, ArbitrationMatrixRowCreate
from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService

pytestmark = pytest.mark.integration

MONGODB_URI_ENV = "CLAIM_ESCALATION_MONGODB_URI"
ACTOR = SimpleNamespace(id="drafter", organization_id="org-A", roles=["orgadmin"])


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


@asynccontextmanager
async def _db() -> AsyncIterator[Any]:
    from rbac_backend.core.database import ensure_indexes

    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"exhibits_{uuid.uuid4().hex[:12]}"
    db = client[name]
    try:
        # The production startup indexes, not a copy of their definition.
        await ensure_indexes(db)
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def _case(db: Any) -> str:
    case = await ArbitrationCaseWorkspaceService(db).create_case(
        ArbitrationCaseCreate(project_id="proj-A", organization_id="org-A", title="ABC v. Authority"), ACTOR
    )
    return str(case["_id"])


def _document(n: int, **extra: Any) -> ArbitrationMatrixRowCreate:
    return ArbitrationMatrixRowCreate(title=f"Letter {n}", source_type="document", source_id=f"doc-{n}", **extra)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_unique_exhibit_index_is_present() -> None:
    async def scenario() -> None:
        async with _db() as db:
            indexes = await db.arbitration_document_index.index_information()
            assert any(
                spec.get("unique") and [key for key, _ in spec["key"]] == ["case_id", "exhibit_id"]
                for spec in indexes.values()
            ), indexes

    _run(scenario())


def test_concurrent_exhibit_creation_yields_distinct_numbers() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _case(db)
            workspace = ArbitrationCaseWorkspaceService(db)
            results = await asyncio.gather(
                *(workspace.create_matrix_row(case_id, "document-index", _document(n), ACTOR) for n in range(8)),
                return_exceptions=True,
            )
            failures = [item for item in results if isinstance(item, BaseException)]
            assert failures == [], failures
            rows = await db.arbitration_document_index.find({"case_id": case_id}).to_list(length=None)
            ids = sorted(row["exhibit_id"] for row in rows)
            assert len(rows) == 8 and len(set(ids)) == 8, ids
            assert sorted(int(row["exhibit_number"]) for row in rows) == list(range(1, 9))

    _run(scenario())


def test_an_exhibit_number_the_user_chose_that_is_taken_is_refused() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _case(db)
            workspace = ArbitrationCaseWorkspaceService(db)
            await workspace.create_matrix_row(case_id, "document-index", _document(1, exhibit_number=5), ACTOR)
            with pytest.raises(HTTPException) as taken:
                await workspace.create_matrix_row(case_id, "document-index", _document(2, exhibit_number=5), ACTOR)
            assert taken.value.status_code == 409
            # The same number in another case, or under another prefix, is fine.
            other = await _case(db)
            await workspace.create_matrix_row(other, "document-index", _document(3, exhibit_number=5), ACTOR)
            await workspace.create_matrix_row(case_id, "document-index", _document(4, exhibit_number=5, exhibit_prefix="R"), ACTOR)
            assert await db.arbitration_document_index.count_documents({"exhibit_id": "C-5"}) == 2

    _run(scenario())
