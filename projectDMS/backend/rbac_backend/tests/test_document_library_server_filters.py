"""Document Library: search, filters and sort run in the query, before pagination.

The register shows 25 rows a page. Every criterion the page offers (search,
From/To date, Direction, Status, Tags, and the column sort) must narrow or
order the WHOLE authorised dataset in Mongo before ``skip``/``limit``, and
``total`` must count the filtered set. A criterion applied after pagination
would only ever see the first page.

Each fixture puts the single matching row deep in the unfiltered order
(logical row 45 of 60, page size 20), so a filter that ran on page 1 alone
returns nothing and the test fails.

Scope comes from the navbar selection (``X-Proj-Id`` / ``X-Org-Id``) and the
caller's membership, through the REAL ``active_scope`` and ``PolicyService``
(see ``test_cl4a_documents_contracts_scope``); a search term never widens it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.core.database import get_database as _original_get_database
from rbac_backend.tests.test_cl4a_documents_contracts_scope import (
    A1,
    A2,
    B1,
    MEMBER_A2,
    MEMBER_AB,
    ORG_A,
    ORG_B,
    ORG_ADMIN_A,
    SUPERADMIN,
    _AggregatingCollection,
    _as,
    _authorization_seams,  # noqa: F401 - autouse fixture: permission/entitlement seams
    _document,
)
from rbac_backend.tests.test_hindrance_register_http import _Database

PAGE = 20
ROWS = 60
TARGET_ROW = 45
BASE = datetime(2026, 9, 30, 12, 0)

TAG_CLAIMS = ObjectId()
TAG_OTHER = ObjectId()


def _row(index: int, **overrides: Any) -> dict[str, Any]:
    """Logical row ``index`` of the default order (``createdAt`` descending)."""
    fields: dict[str, Any] = {
        "letterNo": f"KNP/A1/{index:03d}",
        "subject": f"Routine correspondence {index:03d}",
        "from": "Engineer",
        "to": "Contractor",
        "uploadType": "incoming",
        "status": "Received",
        "tags": [str(TAG_OTHER)],
        "date": datetime(2025, 6, 1) + timedelta(days=index),
        "createdAt": BASE - timedelta(minutes=index),
    }
    fields.update(overrides)
    return _document(ObjectId(), ORG_A, A1, **fields)


TARGET = _row(
    TARGET_ROW,
    letterNo="KNP/A1/17.4-FINAL",
    subject="Notice of claim under Clause 17.4",
    uploadType="outgoing",
    status="Reply Received",
    tags=[str(TAG_CLAIMS)],
    date=datetime(2026, 3, 15),
)


def _seed() -> _Database:
    db = _Database()
    db._collections["documents"] = _AggregatingCollection("documents")
    db.projects.docs.extend(
        [
            {"_id": A1, "organization_id": ORG_A, "name": "A1", "is_active": True},
            {"_id": A2, "organization_id": ORG_A, "name": "A2", "is_active": True},
            {"_id": B1, "organization_id": ORG_B, "name": "B1", "is_active": True},
        ]
    )
    db.tags.docs.extend(
        [
            {"_id": TAG_CLAIMS, "name": "Claims"},
            {"_id": TAG_OTHER, "name": "General"},
        ]
    )
    rows = [_row(i) for i in range(ROWS)]
    rows[TARGET_ROW] = TARGET
    # Near-misses that a broken (unescaped, or unscoped) search would also return.
    rows.append(_row(70, letterNo="KNP/A1/1704", subject="Clause 1704 decoy"))
    db.documents.docs.extend(rows)
    # The same unique term, outside the caller's project and organisation.
    db.documents.docs.append(
        _document(
            ObjectId(),
            ORG_A,
            A2,
            letterNo="KNP/A2/17.4-FINAL",
            subject="Clause 17.4 in A2",
        )
    )
    db.documents.docs.append(
        _document(
            ObjectId(),
            ORG_B,
            B1,
            letterNo="KNP/B1/17.4-FINAL",
            subject="Clause 17.4 in B1",
        )
    )
    return db


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Database:
    database = _seed()

    async def _get_database() -> _Database:
        return database

    for name, module in list(sys.modules.items()):
        if (
            "rbac_backend" in name
            and getattr(module, "get_database", None) is _original_get_database
        ):
            monkeypatch.setattr(module, "get_database", _get_database)
    return database


def _letters(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["letterNo"] for row in response.json()["documents"]]


async def _page1(
    db: _Database,
    params: dict[str, Any],
    user: Any = MEMBER_AB,
    project: str | None = A1,
):
    async with _as(db, user, project, ORG_A if project else None) as client:
        return await client.get(
            "/api/documents", params={"skip": 0, "limit": PAGE, **params}
        )


async def test_unfiltered_page_one_does_not_contain_the_target(db: _Database) -> None:
    """Control: without a criterion the target is past page 1, so each test below
    can only pass if its criterion ran before pagination."""
    response = await _page1(db, {})
    assert TARGET["letterNo"] not in _letters(response)
    assert response.json()["total"] == ROWS + 1


async def test_search_finds_a_match_beyond_page_one(db: _Database) -> None:
    response = await _page1(db, {"search": "17.4-final"})
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    "term",
    ["clause 17.4", "NOTICE OF CLAIM", "  17.4-FINAL  "],
    ids=["partial-subject", "case-insensitive", "surrounding-whitespace"],
)
async def test_search_is_partial_case_insensitive_and_trimmed(
    db: _Database, term: str
) -> None:
    assert _letters(await _page1(db, {"search": term})) == [TARGET["letterNo"]]


async def test_direction_filter_runs_before_pagination(db: _Database) -> None:
    response = await _page1(db, {"uploadType": "outgoing"})
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1


async def test_status_filter_runs_before_pagination(db: _Database) -> None:
    response = await _page1(db, {"status": "Reply Received"})
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1


@pytest.mark.parametrize("tag", [str(TAG_CLAIMS), "Claims"], ids=["by-id", "by-name"])
async def test_tag_filter_runs_before_pagination(db: _Database, tag: str) -> None:
    response = await _page1(db, {"tags": tag})
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1


async def test_date_filter_runs_before_pagination(db: _Database) -> None:
    response = await _page1(db, {"date_from": "2026-03-01", "date_to": "2026-03-31"})
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1


async def test_combined_filters_are_anded(db: _Database) -> None:
    combined = {
        "uploadType": "outgoing",
        "status": "Reply Received",
        "tags": str(TAG_CLAIMS),
        "search": "17.4",
        "date_from": "2025-01-01",
        "date_to": "2026-12-31",
    }
    response = await _page1(db, combined)
    assert _letters(response) == [TARGET["letterNo"]]
    assert response.json()["total"] == 1

    # Any one criterion that the target fails empties the result: AND, not OR.
    for key, value in {
        "uploadType": "incoming",
        "status": "Received",
        "date_to": "2026-01-31",
    }.items():
        response = await _page1(db, {**combined, key: value})
        assert _letters(response) == [], key
        assert response.json()["total"] == 0, key


async def test_filtered_total_counts_the_whole_filtered_set(db: _Database) -> None:
    # Rows 20..59 are dated 2025-06-21 onward (40, the target included, its
    # 2026-03-15 date is later still), plus the decoy (row 70): 41 of 63.
    response = await _page1(db, {"date_from": "2025-06-21"})
    body = response.json()
    assert body["total"] == 41
    assert len(body["documents"]) == PAGE
    assert body["has_next"] is True

    async with _as(db, MEMBER_AB, A1, ORG_A) as client:
        last = await client.get(
            "/api/documents",
            params={"skip": 40, "limit": PAGE, "date_from": "2025-06-21"},
        )
    assert len(last.json()["documents"]) == 1
    assert last.json()["has_next"] is False


async def test_sort_runs_before_pagination(db: _Database) -> None:
    """Sort by letter date: the newest letter (the target, 2026-03-15) is row 45 by
    upload time; a page-only sort could never bring it to the top of page 1."""
    response = await _page1(db, {"sort_by": "date", "sort_order": "desc"})
    assert _letters(response)[0] == TARGET["letterNo"]

    response = await _page1(db, {"sort_by": "date", "sort_order": "asc"})
    assert _letters(response)[0] == "KNP/A1/000"


async def test_default_sort_is_unchanged_for_callers_that_send_none(
    db: _Database,
) -> None:
    letters = _letters(await _page1(db, {}))
    assert letters[:3] == ["KNP/A1/000", "KNP/A1/001", "KNP/A1/002"]


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "organization_id"},
        {"sort_by": "$where"},
        {"sort_by": "date", "sort_order": "sideways"},
    ],
    ids=["unlisted-field", "operator", "bad-order"],
)
async def test_unknown_sort_is_refused(db: _Database, params: dict[str, str]) -> None:
    assert (await _page1(db, params)).status_code == 422


def test_route_sort_fields_match_the_service_table() -> None:
    from typing import get_args

    from rbac_backend.routers.documents import DocumentListSortField
    from rbac_backend.services.document_service import DOCUMENT_LIST_SORT_FIELDS

    assert set(get_args(DocumentListSortField)) == set(DOCUMENT_LIST_SORT_FIELDS)


async def test_selected_project_bounds_the_search(db: _Database) -> None:
    """Member of A1 and A2, A1 selected: the A2 and B1 copies of the term stay out."""
    response = await _page1(db, {"search": "17.4-FINAL"})
    assert _letters(response) == [TARGET["letterNo"]]


async def test_foreign_project_cannot_retrieve_matches(db: _Database) -> None:
    # Member of A2 only, A2 selected: the A1 target is not visible to a search.
    assert _letters(await _page1(db, {"search": "17.4-FINAL"}, MEMBER_A2, A2)) == [
        "KNP/A2/17.4-FINAL"
    ]
    # Nothing selected: still bounded to membership.
    assert _letters(await _page1(db, {"search": "17.4-FINAL"}, MEMBER_A2, None)) == [
        "KNP/A2/17.4-FINAL"
    ]
    # A manipulated filter naming A1 is refused, not honoured.
    async with _as(db, MEMBER_A2, A2, ORG_A) as client:
        response = await client.get(
            "/api/documents", params={"search": "17.4", "project_id": A1}
        )
    assert response.status_code == 403


async def test_foreign_organisation_cannot_retrieve_matches(db: _Database) -> None:
    letters = _letters(await _page1(db, {"search": "17.4-FINAL"}, ORG_ADMIN_A, None))
    assert "KNP/B1/17.4-FINAL" not in letters
    async with _as(db, ORG_ADMIN_A, None) as client:
        response = await client.get(
            "/api/documents", params={"search": "17.4", "organization_id": ORG_B}
        )
    assert response.status_code == 403


async def test_superadmin_selection_still_constrains_the_search(db: _Database) -> None:
    assert _letters(await _page1(db, {"search": "17.4-FINAL"}, SUPERADMIN, A1)) == [
        TARGET["letterNo"]
    ]


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("17.4", ["KNP/A1/17.4-FINAL"]),  # "." is literal: the 1704 decoy stays out
        (".*", []),
        ("(", []),
        ("[a-z]+", []),
        ("\\", []),
        ('{"$ne": null}', []),
    ],
    ids=[
        "dot-literal",
        "wildcard",
        "open-paren",
        "char-class",
        "backslash",
        "operator-json",
    ],
)
async def test_special_characters_are_literal(
    db: _Database, term: str, expected: list[str]
) -> None:
    assert _letters(await _page1(db, {"search": term})) == expected


async def test_subject_filter_is_literal_not_a_regex(db: _Database) -> None:
    """``subject`` used to reach ``$regex`` unescaped: ``.*`` matched every row."""
    assert _letters(await _page1(db, {"subject": ".*"})) == []
    assert _letters(await _page1(db, {"subject": "clause 17.4"})) == [
        TARGET["letterNo"]
    ]
