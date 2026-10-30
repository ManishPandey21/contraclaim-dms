"""Organisation-level correspondence (``project_id = None``) through canonical retrieval.

An organisation-level letter is stored in Mongo with ``project_id: ""`` (the
``Document`` model's default) and in Qdrant with ``project_id: null``. A project
search matches by equality and so never returns it (``build_scope_query``
gives the same answer for the Mongo rows). It must still be reachable - by an
actor whose entitlement covers the whole organisation.

The semantics pinned here:

* ``project_id = None`` in a retrieval request means ORGANISATION-LEVEL
  documents only (``project_id IS NULL``). It never means "all projects".
* The gate (``PolicyService``) answers membership. Row visibility is decided by
  the service from ``ScopeService.has_organization_wide_scope``: an
  organisation-wide actor sees the organisation-level letters; a project-tier
  actor, or no actor at all, sees nothing.
* A caller-supplied ``null`` therefore cannot widen anything.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.retrieval.models import SearchFilters, SearchRequest
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    QdrantHarness,
    correspondence_document,
    retrieval_service,
    run,
    upload_and_process,
)

ORG_A, PROJECT_A, PROJECT_A2 = str(ObjectId()), str(ObjectId()), str(ObjectId())
ORG_B, PROJECT_B = str(ObjectId()), str(ObjectId())
BODY = (
    "Notice of delay to the viaduct pier foundations caused by late access to "
    "the railway corridor. The contractor requests an extension of time. "
) * 4
QUERY = "viaduct pier foundations delay railway corridor"


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


def _user(roles: List[str], org: str, projects: List[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id=str(ObjectId()),
        username="u",
        email="u@example.com",
        roles=roles,
        organization_id=org,
        projects=projects or [],
    )


ORG_ADMIN_A = _user(["orgadmin"], ORG_A)
ORG_USER_A = _user(["orguser"], ORG_A)
PROJECT_USER_A = _user(["projectuser"], ORG_A, [PROJECT_A])
PROJECT_ADMIN_A = _user(["projectadmin"], ORG_A, [PROJECT_A])
ORG_ADMIN_B = _user(["orgadmin"], ORG_B)


@pytest.fixture
def seeded(harness, monkeypatch, tmp_path):
    db = Database()
    rows = {
        "org_a": correspondence_document(
            organization_id=ORG_A, project_id="", letter_no="OA/1", subject="Delay"
        ),
        "proj_a": correspondence_document(
            organization_id=ORG_A, project_id=PROJECT_A, letter_no="PA/1", subject="Delay"
        ),
        "proj_a2": correspondence_document(
            organization_id=ORG_A, project_id=PROJECT_A2, letter_no="PA2/1", subject="Delay"
        ),
        "org_b": correspondence_document(
            organization_id=ORG_B, project_id="", letter_no="OB/1", subject="Delay"
        ),
        "proj_b": correspondence_document(
            organization_id=ORG_B, project_id=PROJECT_B, letter_no="PB/1", subject="Delay"
        ),
    }
    for key, row in rows.items():
        (tmp_path / key).mkdir()
        run(upload_and_process(monkeypatch, tmp_path / key, db, harness, row, body=BODY))
    ids = {key: str(row["_id"]) for key, row in rows.items()}
    # Positive control: every letter is indexed and globally searchable, and the
    # two organisation-level letters really carry a null project.
    points = harness.scroll()
    assert {p.payload["document_id"] for p in points} == set(ids.values())
    for key in ("org_a", "org_b"):
        assert {p.payload["project_id"] for p in points if p.payload["document_id"] == ids[key]} == {None}
    return db, ids


def _found(db, harness, user, org: str, project: Any, **extra: Any) -> List[str]:
    request = SearchRequest(
        query=QUERY,
        limit=20,
        filters=SearchFilters(org_id=org, project_id=project, **extra),
    )
    response = run(retrieval_service(db, harness).search(request, user, log_run=False))
    return sorted({result.document_id for result in response.results})


# A --------------------------------------------------------------------------


@pytest.mark.parametrize("actor", [ORG_ADMIN_A, ORG_USER_A], ids=["orgadmin", "orguser"])
def test_org_wide_actor_retrieves_the_org_level_letter(seeded, harness, actor) -> None:
    db, ids = seeded
    # Organisation-level ONLY: not the project letters, not the other tenant.
    assert _found(db, harness, actor, ORG_A, None) == [ids["org_a"]]


# B / D ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor", [PROJECT_USER_A, PROJECT_ADMIN_A], ids=["projectuser", "projectadmin"]
)
def test_project_tier_actor_gets_nothing_from_an_org_level_request(seeded, harness, actor) -> None:
    db, ids = seeded
    # Submitting null neither reaches the org-level letter nor widens to projects.
    assert _found(db, harness, actor, ORG_A, None) == []
    # Still works for the project it is assigned to.
    assert _found(db, harness, actor, ORG_A, PROJECT_A) == [ids["proj_a"]]


def test_no_actor_and_a_null_project_fails_closed(seeded, harness) -> None:
    db, _ids = seeded
    assert _found(db, harness, None, ORG_A, None) == []


def test_caller_metadata_cannot_reintroduce_a_project_or_null(seeded, harness) -> None:
    db, ids = seeded
    for hostile in (
        {"project_id": PROJECT_A},
        {"project_id": None},
        {"org_id": ORG_B},
        {"organization_id": ORG_B},
    ):
        assert _found(db, harness, ORG_ADMIN_A, ORG_A, None, metadata=hostile) == [ids["org_a"]]
        assert _found(db, harness, PROJECT_USER_A, ORG_A, None, metadata=hostile) == []


# C ----------------------------------------------------------------------------


def test_foreign_org_level_letter_is_not_returned(seeded, harness) -> None:
    db, ids = seeded
    # Org A's admin naming Org B: no organisation-wide scope there, so nothing.
    assert _found(db, harness, ORG_ADMIN_A, ORG_B, None) == []
    assert _found(db, harness, ORG_ADMIN_B, ORG_B, None) == [ids["org_b"]]


# E / F ------------------------------------------------------------------------


def test_project_searches_are_unchanged_and_never_include_org_level(seeded, harness) -> None:
    db, ids = seeded
    assert _found(db, harness, ORG_ADMIN_A, ORG_A, PROJECT_A) == [ids["proj_a"]]
    assert _found(db, harness, ORG_ADMIN_A, ORG_A, PROJECT_A2) == [ids["proj_a2"]]
    assert _found(db, harness, PROJECT_USER_A, ORG_A, PROJECT_A) == [ids["proj_a"]]
    assert ids["proj_b"] not in _found(db, harness, ORG_ADMIN_A, ORG_A, PROJECT_A)
    # The empty-string legacy sentinel stays a no-match, not "organisation level".
    assert _found(db, harness, ORG_ADMIN_A, ORG_A, "") == []


def test_project_id_must_be_supplied_explicitly() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchFilters(org_id=ORG_A)  # type: ignore[call-arg]


def test_contract_retrieval_still_requires_a_project() -> None:
    from rbac_backend.retrieval.service import RetrievalService

    service = RetrievalService.__new__(RetrievalService)
    request = SearchRequest(
        query="q",
        filters=SearchFilters(org_id=ORG_A, project_id=None, metadata={"uploadType": "contract"}),
    )
    with pytest.raises(ValueError):
        run(service.search(request, ORG_ADMIN_A, log_run=False))


# the HTTP routes -----------------------------------------------------------------


class _RecordingPolicy:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def authorize(self, user, permission, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_search_route_accepts_null_project_and_authorizes_the_org(seeded, harness) -> None:
    from rbac_backend.routers import retrieval_engine

    db, ids = seeded
    policy = _RecordingPolicy()
    service = retrieval_service(db, harness)

    class _Observability:
        async def log_run(self, **_kwargs: Any) -> None:
            return None

    service.observability = _Observability()  # type: ignore[assignment]
    request = SearchRequest(query=QUERY, limit=20, filters=SearchFilters(org_id=ORG_A, project_id=None))
    response = run(
        retrieval_engine.search(
            request,
            retrieval_service=service,
            current_user=ORG_ADMIN_A,
            policy=policy,  # type: ignore[arg-type]
        )
    )
    assert sorted({r.document_id for r in response.results}) == [ids["org_a"]]
    assert policy.calls == [
        {"resource_type": "retrieval", "organization_id": ORG_A, "project_id": None}
    ]


@pytest.mark.parametrize("org,project", [("", None), (ORG_A, ""), (ORG_A, "  ")])
def test_search_route_still_rejects_blank_scope(org, project) -> None:
    from fastapi import HTTPException

    from rbac_backend.routers import retrieval_engine

    request = SearchRequest(query="q", filters=SearchFilters(org_id=org, project_id=project))
    with pytest.raises(HTTPException) as refused:
        run(
            retrieval_engine.search(
                request,
                retrieval_service=None,  # type: ignore[arg-type]
                current_user=ORG_ADMIN_A,
                policy=_RecordingPolicy(),  # type: ignore[arg-type]
            )
        )
    assert refused.value.status_code == 400


@pytest.mark.parametrize("route", ["search", "rag"])
@pytest.mark.parametrize(
    "contract_marker",
    [{"uploadType": "contract"}, {"document_type": "contract"}],
)
def test_org_level_contract_request_is_a_400_not_a_500(route, contract_marker) -> None:
    from fastapi import HTTPException

    from rbac_backend.retrieval.models import RagRequest
    from rbac_backend.retrieval.service import RetrievalService
    from rbac_backend.routers import retrieval_engine

    model = RagRequest if route == "rag" else SearchRequest
    request = model(
        query="q",
        filters=SearchFilters(org_id=ORG_A, project_id=None, metadata=contract_marker),
    )
    service = RetrievalService.__new__(RetrievalService)
    with pytest.raises(HTTPException) as refused:
        run(
            getattr(retrieval_engine, route)(
                request,
                retrieval_service=service,
                current_user=ORG_ADMIN_A,
                policy=_RecordingPolicy(),  # type: ignore[arg-type]
            )
        )
    assert refused.value.status_code == 400
