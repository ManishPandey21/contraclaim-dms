"""G30: letter context-document disclosure must be bounded by the ACTOR's
canonical row visibility AND by canonical publication authority — never by the
parent letter's authorisation alone.

`GET /api/letters/{letter_id}/context-documents` authorised the *letter*
(`controller.get_letter(letter_id, current_user)`) and then called
`LetterService.get_context_documents(letter_id)` with no actor at all.

Downstream, `DocumentService.list_documents_by_letter_no` ran::

    db.documents.find({"letterNo": letter_no}).sort("createdAt", -1).limit(12)

Letter codes are global strings. They collide across projects and across
organisations, and a string association is not tenant authority. The serializer
then published `subject`, `summary`, `keywords`, `organization_id` and
`project_id` for every row it received, with no `is_consumable` gate — so a
quarantined duplicate, a soft-deleted row, or a document held for human review
republished its extraction-derived summary.

Two INDEPENDENT conditions are pinned here, and neither substitutes for the
other:

* **SCOPE** — canonical actor entitlement via `build_scope_query`
  (`core/security.py`), the single row-visibility primitive, with the parent
  letter's own organisation/project passed as *narrowing inside entitlement*
  and never as the authority source.
* **PUBLICATION AUTHORITY** — canonical `is_consumable`
  (`services/publication_policy.py`). The seam reads canonical `documents`
  rows directly, so `is_consumable` on the row IS the positive primitive;
  `resolve_document_authority` exists for DERIVED records that carry only a
  `document_id` and would otherwise make the guard vacuous.

Assertions are on the ACTUAL RESPONSE PAYLOAD — `document_ids`, `documents`,
`suggested_documents` and a whole-payload marker scan — not on a query dict or
on "a helper was called", because the defect is material disclosure. Field
stripping is explicitly NOT the fix: an unauthorised document must be absent,
id and all.

Model-B last-known-good is preserved: `processing_status == "failed"` is an
OPERATIONAL state (the pipeline broke without reaching a verdict), not an
adverse judgement, so such a document stays visible. Do not "fix" that test.

Accepted, do not reopen:

* the endpoint is bounded by the letter's own organisation/project as well as
  by entitlement — a global actor is bounded, not exempted;
* an unrecognised role denies all (the canonical `build_scope_query`
  behaviour carried from G-A2), never an endpoint-local organisation-wide
  fallback;
* `document_ids` is filtered to the authorised set too, so a stored curated id
  cannot disclose the existence of a document the caller may not see.
"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson.objectid import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.routers.letters import get_letter_context_documents
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.letter_service import LetterService

ORG_A = "org-A"
ORG_B = "org-B"
PROJ_A = "proj-A"
PROJ_B = "proj-B"
PROJ_C = "proj-C"

SHARED_CODE = "SHARED-001"
CURATED_CODE = "CURATED-900"

SAFE_MARKER = "ORG_A_PROJ_A_SAFE_MARKER_20260831"
SIBLING_PROJECT_MARKER = "PROJECT_B_CONFIDENTIAL_MARKER_20260831"
FOREIGN_ORG_MARKER = "ORG_B_CONFIDENTIAL_MARKER_20260831"
HUMAN_REVIEW_MARKER = "HUMAN_REVIEW_CONFIDENTIAL_MARKER_20260831"
DUPLICATE_MARKER = "DUPLICATE_CONFIDENTIAL_MARKER_20260831"
DELETED_MARKER = "DELETED_CONFIDENTIAL_MARKER_20260831"
OPERATIONAL_FAILURE_MARKER = "OPERATIONAL_FAILURE_LAST_KNOWN_GOOD_MARKER_20260831"

CURATED_SAFE_MARKER = "CURATED_ORG_A_PROJ_A_SAFE_MARKER_20260831"
CURATED_SIBLING_MARKER = "CURATED_PROJECT_B_CONFIDENTIAL_MARKER_20260831"
CURATED_FOREIGN_MARKER = "CURATED_ORG_B_CONFIDENTIAL_MARKER_20260831"
CURATED_REVIEW_MARKER = "CURATED_HUMAN_REVIEW_CONFIDENTIAL_MARKER_20260831"

#: Every marker that must never reach the caller in this fixture.
FORBIDDEN_MARKERS = (
    SIBLING_PROJECT_MARKER,
    FOREIGN_ORG_MARKER,
    HUMAN_REVIEW_MARKER,
    DUPLICATE_MARKER,
    DELETED_MARKER,
    CURATED_SIBLING_MARKER,
    CURATED_FOREIGN_MARKER,
    CURATED_REVIEW_MARKER,
)

LETTER_ID = "6531a1b2c3d4e5f601020304"


# ---------------------------------------------------------------------------
# Mongo boundary. The scope filter IS the thing under test, so the fake applies
# it for real, exactly as the driver would.
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                candidates = expected["$in"]
                if isinstance(actual, list):
                    if not any(_same(item, c) for item in actual for c in candidates):
                        return False
                    continue
                if not any(_same(actual, candidate) for candidate in candidates):
                    return False
                continue
            if "$nin" in expected:
                if any(_same(actual, candidate) for candidate in expected["$nin"]):
                    return False
                continue
            if "$ne" in expected:
                if _same(actual, expected["$ne"]):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


def _sort_key(row: Dict[str, Any], spec: Dict[str, Any]):
    key: List[Any] = []
    for field in spec:
        value = row.get(field)
        key.append((value is None, "" if value is None else str(value)))
    return key


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, spec: Any = None, direction: Any = None) -> "_Cursor":
        if isinstance(spec, str):
            spec = {spec: direction or 1}
        if isinstance(spec, dict):
            self.rows.sort(key=lambda row: _sort_key(row, spec))
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]
        self.queries: List[Optional[Dict[str, Any]]] = []

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        self.queries.append(deepcopy(query))
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def count_documents(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> int:
        return sum(1 for row in self.rows if _matches(row, query))


class _Database:
    def __init__(self, collections: Dict[str, List[Dict[str, Any]]]) -> None:
        self._collections: Dict[str, _Collection] = {
            name: _Collection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ---------------------------------------------------------------------------
# Fixtures: one letter in Organisation A / Project A whose letter number
# collides with documents in a sibling project and in a foreign organisation.
# ---------------------------------------------------------------------------


def _document(
    *,
    doc_id: str,
    organization_id: str,
    project_id: str,
    letter_no: str,
    marker: str,
    processing_status: Optional[str] = "metadata_extracted",
    duplicate_status: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "_id": doc_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{marker}.pdf",
        "filetype": "application/pdf",
        "filesize": 1024,
        "uploadType": "incoming",
        "letterNo": letter_no,
        "date": "2026-08-31T00:00:00Z",
        "subject": f"{marker} SUBJECT",
        "summary": f"{marker} SUMMARY BODY",
        "ocrText": f"{marker} OCR BODY",
        "keywords": [f"{marker}_KEYWORD"],
        "status": "active",
        "createdAt": f"2026-08-31T00:00:00Z",
        "updatedAt": "2026-08-31T00:00:00Z",
        "createdBy": "user-seed",
    }
    if processing_status is not None:
        row["processing_status"] = processing_status
    if duplicate_status is not None:
        row["duplicate_status"] = duplicate_status
    if lifecycle_state is not None:
        row["lifecycle_state"] = lifecycle_state
    return row


# --- suggestion path: everything shares SHARED_CODE with the letter ---------
DOC_SAFE = _document(
    doc_id="doc-safe",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=SHARED_CODE,
    marker=SAFE_MARKER,
)
DOC_SIBLING_PROJECT = _document(
    doc_id="doc-sibling",
    organization_id=ORG_A,
    project_id=PROJ_B,
    letter_no=SHARED_CODE,
    marker=SIBLING_PROJECT_MARKER,
)
DOC_FOREIGN_ORG = _document(
    doc_id="doc-foreign",
    organization_id=ORG_B,
    project_id=PROJ_C,
    letter_no=SHARED_CODE,
    marker=FOREIGN_ORG_MARKER,
)
DOC_HUMAN_REVIEW = _document(
    doc_id="doc-review",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=SHARED_CODE,
    marker=HUMAN_REVIEW_MARKER,
    processing_status="human_review_required",
)
DOC_DUPLICATE = _document(
    doc_id="doc-duplicate",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=SHARED_CODE,
    marker=DUPLICATE_MARKER,
    duplicate_status="duplicate",
)
DOC_DELETED = _document(
    doc_id="doc-deleted",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=SHARED_CODE,
    marker=DELETED_MARKER,
    lifecycle_state="deleted",
)
DOC_OPERATIONAL_FAILURE = _document(
    doc_id="doc-failed",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=SHARED_CODE,
    marker=OPERATIONAL_FAILURE_MARKER,
    processing_status="failed",
)

# --- curated path: stored in letter.context_document_ids --------------------
CTX_SAFE = _document(
    doc_id="ctx-safe",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=CURATED_CODE,
    marker=CURATED_SAFE_MARKER,
)
CTX_SIBLING_PROJECT = _document(
    doc_id="ctx-sibling",
    organization_id=ORG_A,
    project_id=PROJ_B,
    letter_no=CURATED_CODE,
    marker=CURATED_SIBLING_MARKER,
)
CTX_FOREIGN_ORG = _document(
    doc_id="ctx-foreign",
    organization_id=ORG_B,
    project_id=PROJ_C,
    letter_no=CURATED_CODE,
    marker=CURATED_FOREIGN_MARKER,
)
CTX_HUMAN_REVIEW = _document(
    doc_id="ctx-review",
    organization_id=ORG_A,
    project_id=PROJ_A,
    letter_no=CURATED_CODE,
    marker=CURATED_REVIEW_MARKER,
    processing_status="human_review_required",
)

#: A curated id with no canonical `documents` row at all. The seam reads
#: canonical rows directly, so this is the only unresolvable-canonical-state
#: case it can encounter, and it must contribute nothing.
CTX_UNRESOLVABLE_ID = "ctx-does-not-exist"

ALL_DOCUMENTS = [
    DOC_SAFE,
    DOC_SIBLING_PROJECT,
    DOC_FOREIGN_ORG,
    DOC_HUMAN_REVIEW,
    DOC_DUPLICATE,
    DOC_DELETED,
    DOC_OPERATIONAL_FAILURE,
    CTX_SAFE,
    CTX_SIBLING_PROJECT,
    CTX_FOREIGN_ORG,
    CTX_HUMAN_REVIEW,
]

CONTEXT_IDS = [
    CTX_SAFE["_id"],
    CTX_SIBLING_PROJECT["_id"],
    CTX_FOREIGN_ORG["_id"],
    CTX_HUMAN_REVIEW["_id"],
    CTX_UNRESOLVABLE_ID,
]

LETTER_ROW = {
    "_id": ObjectId(LETTER_ID),
    "title": "Letter A",
    "recipient": "Engineer",
    "subject": "Letter A subject",
    "content": "body",
    "status": "Draft",
    "created_by": "user-seed",
    "assigned_to": "user-seed",
    "organization_id": ORG_A,
    "project_id": PROJ_A,
    "letter_no": SHARED_CODE,
    "context_document_ids": list(CONTEXT_IDS),
}


def _user(
    roles: List[str],
    *,
    organization_id: Optional[str],
    projects: List[str],
    organizations: Optional[List[str]] = None,
) -> CurrentUser:
    if organizations is None:
        organizations = [organization_id] if organization_id else []
    return CurrentUser(
        id="user-1",
        username="drafter",
        email="drafter@example.com",
        roles=roles,
        organization_id=organization_id,
        organizations=organizations,
        projects=projects,
    )


PROJECT_A_ACTOR = _user(
    ["projectuser"], organization_id=ORG_A, projects=[PROJ_A]
)
ORG_A_ADMIN = _user(["orgadmin"], organization_id=ORG_A, projects=[])
SUPERADMIN = _user(["superadmin"], organization_id=None, projects=[])
UNRECOGNISED_ROLE_ACTOR = _user(
    ["reporter"], organization_id=ORG_A, projects=[PROJ_A]
)


class _Controller:
    """The router's controller seam.

    `get_letter` stands in for the real letter authorisation, which SUCCEEDS
    here: the whole point is that authorisation to the parent letter must not
    transfer to a foreign context document.
    """

    def __init__(self, letter_service: LetterService) -> None:
        self.letter_service = letter_service
        self.get_letter_calls: List[Any] = []

    async def get_letter(self, letter_id: str, current_user: Any):
        self.get_letter_calls.append((letter_id, current_user))
        return await self.letter_service.get_letter(letter_id)


def _build(letter_row: Optional[Dict[str, Any]] = None):
    db = _Database(
        {
            "documents": ALL_DOCUMENTS,
            "letters": [letter_row or LETTER_ROW],
        }
    )
    return db, _Controller(LetterService(db))


async def _call(current_user: CurrentUser, letter_row: Optional[Dict[str, Any]] = None):
    _db, controller = _build(letter_row)
    return await get_letter_context_documents(
        letter_id=LETTER_ID,
        controller=controller,
        current_user=current_user,
    )


def _payload_text(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _ids(rows: Iterable[Dict[str, Any]]) -> List[str]:
    return [str(row.get("id")) for row in rows]


def _all_returned_ids(payload: Dict[str, Any]) -> List[str]:
    return (
        [str(value) for value in payload.get("document_ids", [])]
        + _ids(payload.get("documents", []))
        + _ids(payload.get("suggested_documents", []))
    )


# ---------------------------------------------------------------------------
# SCOPE — the first of the two independent boundaries.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_document_sharing_a_letter_number_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_SIBLING_PROJECT["_id"] not in _all_returned_ids(payload)
    assert SIBLING_PROJECT_MARKER not in _payload_text(payload)


@pytest.mark.asyncio
async def test_a_foreign_organisation_document_sharing_a_letter_number_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_FOREIGN_ORG["_id"] not in _all_returned_ids(payload)
    assert FOREIGN_ORG_MARKER not in _payload_text(payload)


@pytest.mark.asyncio
async def test_parent_letter_authorisation_does_not_transfer_to_a_foreign_document():
    """The letter IS authorised; a matching `letterNo` still authorises nothing."""
    _db, controller = _build()
    payload = await get_letter_context_documents(
        letter_id=LETTER_ID,
        controller=controller,
        current_user=PROJECT_A_ACTOR,
    )

    assert controller.get_letter_calls, "the parent letter must still be authorised"

    by_id = {row["_id"]: row for row in ALL_DOCUMENTS}
    for identifier in _all_returned_ids(payload):
        row = by_id.get(identifier)
        assert row is not None, f"unresolvable id disclosed: {identifier}"
        assert row["organization_id"] == ORG_A
        assert row["project_id"] == PROJ_A


@pytest.mark.asyncio
async def test_curated_context_documents_are_bounded_by_actor_scope():
    payload = await _call(PROJECT_A_ACTOR)
    text = _payload_text(payload)

    assert CURATED_SIBLING_MARKER not in text
    assert CURATED_FOREIGN_MARKER not in text
    assert CTX_SIBLING_PROJECT["_id"] not in _all_returned_ids(payload)
    assert CTX_FOREIGN_ORG["_id"] not in _all_returned_ids(payload)


@pytest.mark.asyncio
async def test_a_curated_id_with_no_canonical_document_contributes_nothing():
    payload = await _call(PROJECT_A_ACTOR)

    assert CTX_UNRESOLVABLE_ID not in _all_returned_ids(payload)


@pytest.mark.asyncio
async def test_an_unrecognised_role_is_denied_rather_than_given_the_organisation():
    """Carried from G-A2: canonical `build_scope_query` denies unknown tiers.

    The fix for that, if the owner wants those seeded roles to read, belongs in
    `build_scope_query` — never in an endpoint-local organisation-wide fallback,
    which is exactly the union this phase removed.
    """
    payload = await _call(UNRECOGNISED_ROLE_ACTOR)

    assert payload["documents"] == []
    assert payload["suggested_documents"] == []
    assert payload["document_ids"] == []


# ---------------------------------------------------------------------------
# PUBLICATION AUTHORITY — the second, independent boundary.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_review_required_document_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_HUMAN_REVIEW["_id"] not in _all_returned_ids(payload)
    assert HUMAN_REVIEW_MARKER not in _payload_text(payload)


@pytest.mark.asyncio
async def test_a_quarantined_duplicate_document_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_DUPLICATE["_id"] not in _all_returned_ids(payload)
    assert DUPLICATE_MARKER not in _payload_text(payload)


@pytest.mark.asyncio
async def test_a_soft_deleted_document_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_DELETED["_id"] not in _all_returned_ids(payload)
    assert DELETED_MARKER not in _payload_text(payload)


@pytest.mark.asyncio
async def test_a_curated_human_review_document_is_not_disclosed():
    payload = await _call(PROJECT_A_ACTOR)

    assert CTX_HUMAN_REVIEW["_id"] not in _all_returned_ids(payload)
    assert CURATED_REVIEW_MARKER not in _payload_text(payload)


# ---------------------------------------------------------------------------
# Model-B last-known-good, and the positives. A containment fix that empties
# the endpoint is not a fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operational_processing_failure_does_not_retract_last_known_good():
    """`failed` is OPERATIONAL — the pipeline broke without reaching a verdict.

    Only an outcome ABOUT THE CONTENT may retract a previous publication. Do
    not "harden" this into a denial; that is the T07 fixture mistake.
    """
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_OPERATIONAL_FAILURE["_id"] in _ids(payload["suggested_documents"])
    assert OPERATIONAL_FAILURE_MARKER in _payload_text(payload)


@pytest.mark.asyncio
async def test_the_in_scope_consumable_document_is_still_suggested():
    payload = await _call(PROJECT_A_ACTOR)

    assert DOC_SAFE["_id"] in _ids(payload["suggested_documents"])
    assert SAFE_MARKER in _payload_text(payload)


@pytest.mark.asyncio
async def test_the_in_scope_curated_document_is_still_returned():
    payload = await _call(PROJECT_A_ACTOR)

    assert CTX_SAFE["_id"] in _ids(payload["documents"])
    assert CTX_SAFE["_id"] in [str(v) for v in payload["document_ids"]]
    assert CURATED_SAFE_MARKER in _payload_text(payload)


@pytest.mark.asyncio
async def test_an_organisation_tier_actor_is_still_bounded_by_the_letters_project():
    """Entitlement is org-wide; the letter's own project narrows inside it."""
    payload = await _call(ORG_A_ADMIN)
    text = _payload_text(payload)

    assert DOC_SAFE["_id"] in _ids(payload["suggested_documents"])
    assert SIBLING_PROJECT_MARKER not in text
    assert FOREIGN_ORG_MARKER not in text


@pytest.mark.asyncio
async def test_a_global_actor_is_bounded_by_the_letter_not_denied():
    """Global semantics preserved through canonical scope, not a role check."""
    payload = await _call(SUPERADMIN)
    text = _payload_text(payload)

    assert DOC_SAFE["_id"] in _ids(payload["suggested_documents"])
    assert CTX_SAFE["_id"] in _ids(payload["documents"])
    assert SIBLING_PROJECT_MARKER not in text
    assert FOREIGN_ORG_MARKER not in text


# ---------------------------------------------------------------------------
# Whole-payload and structural pins.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [PROJECT_A_ACTOR, ORG_A_ADMIN, SUPERADMIN],
    ids=["project-tier", "organisation-tier", "global"],
)
async def test_no_response_field_carries_an_unauthorised_marker(actor: CurrentUser):
    """Field stripping is not the fix — nothing about the row may survive."""
    text = _payload_text(await _call(actor))

    for marker in FORBIDDEN_MARKERS:
        assert marker not in text, f"unauthorised marker disclosed: {marker}"


@pytest.mark.asyncio
async def test_a_letter_without_a_letter_number_suggests_nothing():
    row = dict(LETTER_ROW)
    row["letter_no"] = None
    payload = await _call(PROJECT_A_ACTOR, letter_row=row)

    assert payload["suggested_documents"] == []


def test_the_letter_number_lookup_cannot_be_called_without_an_actor():
    """Fail closed by construction, not by remembering to pass a filter.

    `list_documents_by_letter_no` is a generic `documents` reader over a GLOBAL
    string key. A future caller must be unable to obtain rows without supplying
    the identity they are to be bounded by, so the actor parameter carries no
    default.
    """
    signature = inspect.signature(DocumentService.list_documents_by_letter_no)
    assert "current_user" in signature.parameters
    assert signature.parameters["current_user"].default is inspect.Parameter.empty


def test_context_document_disclosure_cannot_be_requested_without_an_actor():
    signature = inspect.signature(LetterService.get_context_documents)
    assert "current_user" in signature.parameters
    assert signature.parameters["current_user"].default is inspect.Parameter.empty
