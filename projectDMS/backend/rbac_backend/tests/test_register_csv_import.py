from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.services.register_csv_import import (
    validate_csv_import_scope,
    import_bank_guarantees_csv,
    import_key_dates_csv,
    preview_bank_guarantees_csv,
    preview_key_dates_csv,
    template_csv,
    KEY_DATE_SAMPLE_ROW,
    KEY_DATE_TEMPLATE_HEADERS,
    BG_SAMPLE_ROW,
    BG_TEMPLATE_HEADERS,
)


START = datetime(2026, 1, 5)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self, seed=None):
        self.docs = {doc["_id"]: dict(doc) for doc in (seed or [])}

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one(self, query):
        if "_id" in query and not isinstance(query["_id"], dict):
            doc = self.docs.get(query["_id"])
            return dict(doc) if doc else None
        for doc in self.docs.values():
            if self._match(doc, query):
                return dict(doc)
        return None

    async def update_one(self, query, update):
        doc = self.docs.get(query.get("_id"))
        if doc:
            doc.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if doc else 0)

    async def find_one_and_update(self, query, update, return_document=True):
        doc = self.docs.get(query.get("_id"))
        if not doc:
            return None
        doc.update(update.get("$set", {}))
        return dict(doc)

    def find(self, query=None):
        query = query or {}
        return _Cursor([dict(doc) for doc in self.docs.values() if self._match(doc, query)])

    @staticmethod
    def _match(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
            elif actual != expected:
                return False
        return True


class _DB:
    def __init__(self):
        self.key_date_milestones = _Coll()
        self.key_date_eot_applications = _Coll()
        self.key_date_extension_history = _Coll()
        self.key_date_achievements = _Coll()
        self.key_date_notifications = _Coll()
        self.projects = _Coll()
        self.contract_master = _Coll()
        self.bank_guarantees = _Coll()
        self.bg_extension_history = _Coll()
        self.bg_notifications = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(
        id="u1",
        roles=["orgadmin"],
        organization_id=org,
        organizations=[org],
        projects=["proj-A"],
        account_type="client_user",
    )


def _csv(text: str) -> bytes:
    return text.strip().encode("utf-8")


def test_sample_templates_include_required_headers():
    key_date_template = template_csv(KEY_DATE_TEMPLATE_HEADERS, KEY_DATE_SAMPLE_ROW)
    bg_template = template_csv(BG_TEMPLATE_HEADERS, BG_SAMPLE_ROW)

    assert key_date_template.splitlines()[0] == (
        "title,contractual_week_number,project_start_date,milestone_ref,description,"
        "responsible_party_id,original_planned_key_date,remarks"
    )
    assert bg_template.splitlines()[0] == (
        "contract_id,bg_number,bg_type,issuing_bank,branch,bg_amount,currency,conversion_rate,"
        "submission_date,contractual_required_up_to,bg_expiry_date,claim_expiry_date,bg_status,remarks"
    )
    assert "project_id" not in key_date_template.splitlines()[0]
    assert "project_id" not in bg_template.splitlines()[0]


@pytest.mark.asyncio
async def test_import_scope_requires_matching_project_organization():
    db = _DB()
    await db.projects.insert_one({"_id": "proj-A", "organization_id": "org-A"})

    scope = await validate_csv_import_scope(
        db,
        organization_id="org-A",
        project_id="proj-A",
    )
    assert scope == ("org-A", "proj-A")

    with pytest.raises(ValueError, match="does not belong"):
        await validate_csv_import_scope(
            db,
            organization_id="org-B",
            project_id="proj-A",
        )


@pytest.mark.asyncio
async def test_preview_rejects_legacy_scope_columns_in_csv():
    content = _csv(
        """
        title,project_id,contractual_week_number,project_start_date
        Basement Structure Complete,proj-A,5,2026-01-05
        """
    )

    with pytest.raises(ValueError, match="Select the Organisation and Project"):
        await preview_key_dates_csv(
            _DB(),
            content,
            _user(),
            organization_id="org-A",
            project_id="proj-A",
        )


@pytest.mark.asyncio
async def test_bank_guarantee_preview_rejects_legacy_scope_columns_in_csv():
    content = _csv(
        """
        project_id,contract_id,bg_number,bg_type
        proj-A,primary,BG-2026-001,performance
        """
    )

    with pytest.raises(ValueError, match="Select the Organisation and Project"):
        await preview_bank_guarantees_csv(
            _DB(),
            content,
            _user(),
            organization_id="org-A",
            project_id="proj-A",
        )


@pytest.mark.asyncio
async def test_key_date_preview_reports_row_errors_and_csv_duplicates():
    content = _csv(
        """
        title,contractual_week_number,project_start_date
        Basement Structure Complete,5,2026-01-05
        Basement Structure Complete,5,2026-01-05
        Missing Week,,2026-01-05
        """
    )

    preview = (
        await preview_key_dates_csv(
            _DB(), content, _user(), organization_id="org-A", project_id="proj-A"
        )
    ).response

    assert preview.total_rows == 3
    assert preview.valid_rows == 1
    assert preview.invalid_rows == 2
    assert preview.can_import is False
    assert preview.rows[1].duplicate is True
    assert "duplicate key date appears more than once in this CSV" in preview.rows[1].errors
    assert "contractual_week_number is required" in preview.rows[2].errors


@pytest.mark.asyncio
async def test_key_date_import_creates_existing_milestone_records():
    db = _DB()
    content = _csv(
        """
        title,contractual_week_number,project_start_date,milestone_ref
        Basement Structure Complete,5,2026-01-05,M-001
        Tower Crane Dismantled,8,2026-01-05,M-002
        """
    )

    result = await import_key_dates_csv(
        db, content, _user(), organization_id="org-A", project_id="proj-A"
    )

    assert result.can_import is True
    assert result.imported_count == 2
    assert len(db.key_date_milestones.docs) == 2
    stored = list(db.key_date_milestones.docs.values())
    assert {doc["milestone_ref"] for doc in stored} == {"M-001", "M-002"}
    assert all(doc["organization_id"] == "org-A" for doc in stored)
    assert all(doc["project_id"] == "proj-A" for doc in stored)


@pytest.mark.asyncio
async def test_bank_guarantee_preview_reports_existing_duplicate_and_invalid_rows():
    db = _DB()
    await db.bank_guarantees.insert_one(
        {
            "_id": "bg-existing",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "bg_number": "BG-EXIST",
        }
    )
    content = _csv(
        """
        bg_number,bg_type,bg_amount
        BG-EXIST,performance,100
        BG-BAD,wrong,abc
        """
    )

    preview = (
        await preview_bank_guarantees_csv(
            db, content, _user(), organization_id="org-A", project_id="proj-A"
        )
    ).response

    assert preview.total_rows == 2
    assert preview.valid_rows == 0
    assert preview.invalid_rows == 2
    assert preview.can_import is False
    assert preview.rows[0].duplicate is True
    assert "duplicate bank guarantee number already exists for this project" in preview.rows[0].errors
    assert any("bg_type must be one of" in error for error in preview.rows[1].errors)
    assert "bg_amount must be a number" in preview.rows[1].errors


@pytest.mark.asyncio
async def test_bank_guarantee_import_creates_existing_bg_records():
    db = _DB()
    content = _csv(
        """
        contract_id,bg_number,bg_type,issuing_bank,bg_amount,currency,conversion_rate,contractual_required_up_to,bg_expiry_date,bg_status
        primary,BG-2026-001,performance,Sample Bank,1000000,INR,1,2026-12-31,2026-11-30,valid
        """
    )

    result = await import_bank_guarantees_csv(
        db, content, _user(), organization_id="org-A", project_id="proj-A"
    )

    assert result.can_import is True
    assert result.imported_count == 1
    stored = list(db.bank_guarantees.docs.values())
    assert len(stored) == 1
    assert stored[0]["bg_number"] == "BG-2026-001"
    assert stored[0]["organization_id"] == "org-A"
    assert stored[0]["project_id"] == "proj-A"
