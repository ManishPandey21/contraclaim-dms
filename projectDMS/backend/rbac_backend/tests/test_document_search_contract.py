from __future__ import annotations

import pytest
from fastapi import HTTPException

from rbac_backend.routers.documents import (
    _apply_linkable_document_constraints,
    _resolve_document_search_term,
    router,
)


def test_canonical_document_search_route_is_publicly_registered() -> None:
    paths = {route.path for route in router.routes}

    assert "/document-search" in paths
    assert "/documents" in paths


def test_q_is_the_canonical_search_name_and_search_is_a_compatible_alias() -> None:
    assert _resolve_document_search_term("notice", None) == "notice"
    assert _resolve_document_search_term(None, "notice") == "notice"
    assert _resolve_document_search_term("notice", "notice") == "notice"


def test_conflicting_q_and_search_terms_fail_closed() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _resolve_document_search_term("notice", "invoice")

    assert exc_info.value.status_code == 422


def test_linkable_search_excludes_project_null_and_non_authoritative_documents() -> None:
    query = _apply_linkable_document_constraints(
        {"organization_id": "org-1", "project_id": "project-1"}
    )

    assert query["project_id"] == "project-1"
    assert query["duplicate_status"] == {"$ne": "duplicate"}
    assert query["lifecycle_state"] == {
        "$nin": ["deleted", "duplicate_review", "duplicate"]
    }
    assert query["processing_status"] == {"$nin": ["human_review_required"]}


def test_linkable_search_fails_closed_for_unspecified_project_scope() -> None:
    query = _apply_linkable_document_constraints({"organization_id": "org-1"})

    assert query["project_id"] == {"$nin": [None, ""]}
