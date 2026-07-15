from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from rbac_backend.models.legal_word import (
    LegalWordCreate,
    LegalWordPublicationType,
    LegalWordSource,
    LegalWordStatus,
    LegalWordSuggestionEligibility,
    LegalWordUpdate,
)
from rbac_backend.services.legal_word_service import (
    DAILY_WORD_COUNT,
    LegalWordService,
    LegalWordServiceError,
    run_legal_word_daily_publish,
)


class _InsertResult:
    def __init__(self, inserted_id: str) -> None:
        self.inserted_id = inserted_id


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, field, direction):
        reverse = direction < 0
        self.docs.sort(key=lambda doc: doc.get(field), reverse=reverse)
        return self

    def skip(self, amount):
        self.docs = self.docs[amount:]
        return self

    def limit(self, amount):
        self.docs = self.docs[:amount]
        return self

    async def to_list(self, length):
        return list(self.docs[:length])


class _Collection:
    def __init__(self, unique_field: str | None = None) -> None:
        self.docs = {}
        self.unique_field = unique_field
        self.indexes = []
        self._next_id = 1

    async def create_index(self, field, unique=False):
        self.indexes.append((field, unique))

    async def insert_one(self, payload):
        if self.unique_field:
            value = payload.get(self.unique_field)
            if value is not None:
                for doc in self.docs.values():
                    if doc.get(self.unique_field) == value:
                        raise DuplicateKeyError("duplicate")
        doc = dict(payload)
        inserted_id = str(self._next_id)
        self._next_id += 1
        doc["_id"] = inserted_id
        self.docs[inserted_id] = doc
        return _InsertResult(inserted_id)

    async def find_one(self, query):
        for doc in self.docs.values():
            if self._matches(doc, query):
                return dict(doc)
        return None

    def find(self, query):
        return _Cursor([dict(doc) for doc in self.docs.values() if self._matches(doc, query)])

    async def count_documents(self, query):
        return len([doc for doc in self.docs.values() if self._matches(doc, query)])

    async def update_one(self, query, update):
        for key, doc in self.docs.items():
            if self._matches(doc, query):
                fields = update.get("$set", {})
                self.docs[key] = {**doc, **fields}
                return _UpdateResult(1)
        return _UpdateResult(0)

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            if key == "$or":
                if not any(self._matches(doc, clause) for clause in expected):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


class _Db:
    def __init__(self) -> None:
        self.legal_words = _Collection("normalized_word")
        self.legal_word_daily_sets = _Collection("published_date")


@pytest.fixture
def fake_db(monkeypatch):
    db = _Db()

    async def _get_database():
        return db

    monkeypatch.setattr(
        "rbac_backend.services.legal_word_service.get_database",
        _get_database,
    )
    return db


async def _create_approved_word(service: LegalWordService, raw_word: str):
    return await service.create_word(
        LegalWordCreate(
            word=raw_word,
            meaning=f"{raw_word} meaning.",
            synonyms=[raw_word.lower()],
            example_sentence=f"{raw_word} example.",
            status=LegalWordStatus.APPROVED,
        )
    )


@pytest.mark.asyncio
async def test_create_word_normalizes_and_dedupes_synonyms(fake_db):
    service = LegalWordService()
    current_user = SimpleNamespace(id="admin-1")

    word = await service.create_word(
        LegalWordCreate(
            word="  Pertinent  ",
            meaning=" Relevant to the matter. ",
            synonyms=["relevant", "Relevant", " material "],
            example_sentence=" It is pertinent to note the delay. ",
        ),
        current_user,
    )

    assert word.word == "Pertinent"
    assert word.normalized_word == "pertinent"
    assert word.synonyms == ["relevant", "material"]
    assert word.created_by == "admin-1"
    assert ("normalized_word", True) in fake_db.legal_words.indexes


@pytest.mark.asyncio
async def test_duplicate_word_raises_conflict(fake_db):
    service = LegalWordService()
    payload = LegalWordCreate(
        word="Pertinent",
        meaning="Relevant.",
        synonyms=["relevant"],
        example_sentence="It is pertinent to note the delay.",
    )

    await service.create_word(payload)

    with pytest.raises(LegalWordServiceError) as exc:
        await service.create_word(payload)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_ai_suggestions_flag_new_recent_and_republish_eligible(fake_db, monkeypatch):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    recent = await service.create_word(
        LegalWordCreate(
            word="Pertinent",
            meaning="Relevant.",
            synonyms=["relevant"],
            example_sentence="It is pertinent to note the delay.",
            status=LegalWordStatus.PUBLISHED,
            published_date=date(2026, 6, 1),
        ),
        admin,
    )
    old = await service.create_word(
        LegalWordCreate(
            word="Waiver",
            meaning="Intentional relinquishment of a contractual right.",
            synonyms=["relinquishment"],
            example_sentence="No waiver of rights is intended by this correspondence.",
            status=LegalWordStatus.PUBLISHED,
            published_date=date(2025, 12, 1),
        ),
        admin,
    )

    async def _fake_candidates(preferred_words=None):
        assert preferred_words == []
        return [
            {
                "word": "Condition Precedent",
                "meaning": "A requirement that must be satisfied before an obligation arises.",
                "synonyms": ["precondition"],
                "example_sentence": "Payment is subject to the condition precedent stated in the Contract.",
            },
            {
                "word": "Pertinent",
                "meaning": "Relevant to the contractual issue.",
                "synonyms": ["relevant"],
                "example_sentence": "The enclosed records are pertinent to the delay event.",
            },
            {
                "word": "Waiver",
                "meaning": "Relinquishment of a contractual right.",
                "synonyms": ["relinquishment"],
                "example_sentence": "This letter is issued without waiver of contractual rights.",
            },
            {
                "word": "Estoppel",
                "meaning": "A bar against taking an inconsistent position.",
                "synonyms": ["preclusion"],
                "example_sentence": "The Employer is estopped from denying the instructed change.",
            },
            {
                "word": "Indemnity",
                "meaning": "An obligation to compensate another party for specified losses.",
                "synonyms": ["compensation undertaking"],
                "example_sentence": "The indemnity shall apply to claims arising from the instruction.",
            },
        ]

    monkeypatch.setattr(service, "_generate_ai_word_candidates", _fake_candidates)

    response = await service.suggest_words_with_ai(admin, today=date(2026, 7, 7))
    by_word = {suggestion.word: suggestion for suggestion in response.suggestions}

    new_word = by_word["Condition Precedent"]
    assert new_word.eligibility_status == LegalWordSuggestionEligibility.NEW_WORD
    assert new_word.word_record.source == LegalWordSource.AI_SUGGESTED
    assert new_word.is_eligible_for_republish is True

    recent_word = by_word["Pertinent"]
    assert recent_word.existing_word_id == recent.id
    assert recent_word.eligibility_status == LegalWordSuggestionEligibility.BLOCKED_RECENTLY_PUBLISHED
    assert recent_word.is_eligible_for_republish is False
    assert fake_db.legal_words.docs[recent.id]["is_eligible_for_republish"] is False

    old_word = by_word["Waiver"]
    assert old_word.existing_word_id == old.id
    assert old_word.eligibility_status == LegalWordSuggestionEligibility.ELIGIBLE_FOR_REPUBLISH
    assert old_word.is_eligible_for_republish is True
    assert fake_db.legal_words.docs[old.id]["first_suggested_date"] == "2026-07-07"


@pytest.mark.asyncio
async def test_ai_suggestions_prioritize_and_enrich_user_requested_words(fake_db, monkeypatch):
    service = LegalWordService()
    user = SimpleNamespace(id="user-1")
    admin = SimpleNamespace(id="admin-1")

    await service.search_or_request_word("  Delay Damages  ", user)
    requested_id = next(iter(fake_db.legal_words.docs))

    async def _fake_candidates(preferred_words=None):
        assert preferred_words == ["Delay Damages"]
        return [
            {
                "word": "Condition Precedent",
                "meaning": "A requirement that must be satisfied before an obligation arises.",
                "synonyms": ["precondition"],
                "example_sentence": "Payment is subject to the condition precedent stated in the Contract.",
            },
            {
                "word": "Delay Damages",
                "meaning": "Damages recoverable for delay caused by a contractual default.",
                "synonyms": ["delay compensation", "liquidated damages"],
                "example_sentence": "The Employer's claim for delay damages is denied for the reasons stated below.",
            },
        ]

    monkeypatch.setattr(service, "_generate_ai_word_candidates", _fake_candidates)

    response = await service.suggest_words_with_ai(admin, today=date(2026, 7, 7))

    first = response.suggestions[0]
    assert first.word == "Delay Damages"
    assert first.existing_word_id == requested_id
    assert first.eligibility_status == LegalWordSuggestionEligibility.NEW_WORD
    assert first.eligibility_label == "User Requested Word"
    assert first.word_record.source == LegalWordSource.USER_REQUESTED
    stored = fake_db.legal_words.docs[requested_id]
    assert stored["meaning"] == "Damages recoverable for delay caused by a contractual default."
    assert stored["synonyms"] == ["delay compensation", "liquidated damages"]
    assert stored["example_sentence"] == (
        "The Employer's claim for delay damages is denied for the reasons stated below."
    )
    assert stored["first_suggested_date"] == "2026-07-07"


@pytest.mark.asyncio
async def test_search_missing_word_creates_pending_user_request_once(fake_db):
    service = LegalWordService()
    current_user = SimpleNamespace(id="user-1")

    first = await service.search_or_request_word("  Quantum Meruit  ", current_user)
    second = await service.search_or_request_word("quantum   meruit", current_user)

    assert first.found is False
    assert first.requested is True
    assert first.word is None
    assert second.word is None
    assert len(fake_db.legal_words.docs) == 1
    requested = next(iter(fake_db.legal_words.docs.values()))
    assert requested["source"] == "user_requested"
    assert requested["status"] == LegalWordStatus.PENDING_REVIEW.value
    assert requested["requested_by_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_user_requested_list_only_returns_pending_requests_by_default(fake_db):
    service = LegalWordService()
    user = SimpleNamespace(id="user-1")
    admin = SimpleNamespace(id="admin-1")

    await service.search_or_request_word("Quantum Meruit", user)
    requested_id = next(iter(fake_db.legal_words.docs))
    await service.update_word(
        requested_id,
        LegalWordUpdate(
            word="Quantum Meruit",
            meaning="Reasonable payment for work performed where valuation is disputed.",
            synonyms=["reasonable remuneration"],
            example_sentence="The Contractor claims payment on a quantum meruit basis.",
        ),
        admin,
    )
    await service.approve_word(requested_id, admin)

    pending_user_requests, total = await service.list_words(source=LegalWordSource.USER_REQUESTED.value)
    approved_user_requests, approved_total = await service.list_words(
        source=LegalWordSource.USER_REQUESTED.value,
        status=LegalWordStatus.APPROVED.value,
    )

    assert pending_user_requests == []
    assert total == 0
    assert [word.id for word in approved_user_requests] == [requested_id]
    assert approved_total == 1


@pytest.mark.asyncio
async def test_daily_set_returns_words_in_saved_order(fake_db):
    service = LegalWordService()
    word_ids = []
    for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]:
        word = await service.create_word(
            LegalWordCreate(
                word=raw_word,
                meaning=f"{raw_word} meaning.",
                synonyms=[raw_word.lower()],
                example_sentence=f"{raw_word} example.",
                status=LegalWordStatus.PUBLISHED,
            )
        )
        word_ids.append(word.id)

    await service.create_daily_set(date(2026, 7, 6), list(reversed(word_ids)))
    response = await service.get_today_words(date(2026, 7, 6))

    assert response.count == DAILY_WORD_COUNT
    assert [word.id for word in response.words] == list(reversed(word_ids))


@pytest.mark.asyncio
async def test_admin_actions_transition_word_statuses(fake_db):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    word = await service.create_word(
        LegalWordCreate(
            word="Pertinent",
            meaning="Relevant.",
            synonyms=["relevant"],
            example_sentence="It is pertinent to note the delay.",
        )
    )

    approved = await service.approve_word(word.id, admin)
    assert approved.status == LegalWordStatus.APPROVED
    assert approved.approved_by_admin_id == "admin-1"
    assert approved.approved_at is not None

    scheduled = await service.schedule_word(word.id, date(2026, 7, 7), admin)
    assert scheduled.status == LegalWordStatus.SCHEDULED
    assert scheduled.scheduled_date == date(2026, 7, 7)

    rejected = await service.reject_word(word.id, admin)
    assert rejected.status == LegalWordStatus.REJECTED

    inactive = await service.deactivate_word(word.id, admin)
    assert inactive.status == LegalWordStatus.INACTIVE


@pytest.mark.asyncio
async def test_approve_requires_complete_word(fake_db):
    service = LegalWordService()
    current_user = SimpleNamespace(id="user-1")
    response = await service.search_or_request_word("Quantum Meruit", current_user)
    assert response.requested is True
    word_id = next(iter(fake_db.legal_words.docs))

    with pytest.raises(LegalWordServiceError) as exc:
        await service.approve_word(word_id, SimpleNamespace(id="admin-1"))

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_publish_daily_words_prioritizes_scheduled_and_is_idempotent(fake_db):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    publish_date = date(2026, 7, 8)

    scheduled = await _create_approved_word(service, "Estoppel")
    await service.schedule_word(scheduled.id, publish_date, admin)
    fill_words = [
        await _create_approved_word(service, raw_word)
        for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite"]
    ]

    response = await service.publish_daily_words(publish_date, admin)
    again = await service.publish_daily_words(publish_date, admin)

    assert response.count == DAILY_WORD_COUNT
    assert [word.id for word in response.words] == [
        scheduled.id,
        *[word.id for word in fill_words],
    ]
    assert [word.id for word in again.words] == [word.id for word in response.words]
    assert len(fake_db.legal_word_daily_sets.docs) == 1
    for word in response.words:
        stored = fake_db.legal_words.docs[word.id]
        assert stored["status"] == LegalWordStatus.PUBLISHED.value
        assert stored["published_date"] == publish_date.isoformat()
        assert stored["last_published_date"] == publish_date.isoformat()
        assert stored["published_count"] == 1
        assert stored["publication_history"] == [publish_date.isoformat()]
        assert stored["is_eligible_for_republish"] is False


@pytest.mark.asyncio
async def test_publish_daily_words_does_not_repeat_published_words(fake_db):
    service = LegalWordService()
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in [
            "Pertinent",
            "Notwithstanding",
            "Requisite",
            "Material",
            "Expedite",
            "Covenant",
            "Estoppel",
            "Indemnity",
            "Prejudice",
            "Waiver",
            "Mitigation",
            "Set-off",
        ]
    ]

    first = await service.publish_daily_words(date(2026, 7, 8))
    second = await service.publish_daily_words(date(2026, 7, 9))

    assert first.count == DAILY_WORD_COUNT
    assert second.count == DAILY_WORD_COUNT
    assert {word.id for word in first.words}.isdisjoint({word.id for word in second.words})
    assert [word.id for word in first.words] == [word.id for word in words[:DAILY_WORD_COUNT]]
    assert [word.id for word in second.words] == [word.id for word in words[DAILY_WORD_COUNT:]]


@pytest.mark.asyncio
async def test_publish_daily_words_requires_six_available_words(fake_db):
    service = LegalWordService()
    for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite"]:
        await _create_approved_word(service, raw_word)

    with pytest.raises(LegalWordServiceError) as exc:
        await service.publish_daily_words(date(2026, 7, 8))

    assert exc.value.status_code == 409
    assert fake_db.legal_word_daily_sets.docs == {}


@pytest.mark.asyncio
async def test_publish_word_immediately_includes_selected_word_in_daily_set(fake_db):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    publish_date = date(2026, 7, 10)
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]
    ]

    response = await service.publish_word_immediately(words[2].id, admin, for_date=publish_date)

    assert response.count == DAILY_WORD_COUNT
    assert response.words[0].id == words[2].id
    daily_set = next(iter(fake_db.legal_word_daily_sets.docs.values()))
    assert daily_set["word_ids"][0] == words[2].id
    stored = fake_db.legal_words.docs[words[2].id]
    assert stored["last_published_date"] == publish_date.isoformat()
    assert stored["published_count"] == 1


@pytest.mark.asyncio
async def test_list_published_words_returns_newest_events_and_today_new_badges(fake_db):
    service = LegalWordService()
    publish_date = date(2026, 7, 16)
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]
    ]

    await service.publish_daily_words(publish_date)
    items, total = await service.list_published_words(limit=6, today=publish_date)

    assert total == DAILY_WORD_COUNT
    assert [item.id for item in items] == [word.id for word in words]
    assert {item.display_publication_date for item in items} == {publish_date}
    assert {item.publication_type for item in items} == {LegalWordPublicationType.NEW}
    assert {item.publication_badge for item in items} == {"New"}


@pytest.mark.asyncio
async def test_list_published_words_marks_republications_only_on_republication_date(fake_db):
    service = LegalWordService()
    old_date = date(2025, 12, 1)
    repeat_date = date(2026, 7, 16)
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]
    ]
    await service.publish_daily_words(old_date)

    await service.publish_word_immediately(words[0].id, for_date=repeat_date)
    today_items, _ = await service.list_published_words(limit=6, today=repeat_date)
    old_items, _ = await service.list_published_words(
        limit=6,
        today=repeat_date,
        published_date=old_date,
    )

    assert {item.display_publication_date for item in today_items} == {repeat_date}
    assert {item.publication_type for item in today_items} == {LegalWordPublicationType.REPEATED}
    assert {item.publication_badge for item in today_items} == {"Repeated"}
    assert {item.display_publication_date for item in old_items} == {old_date}
    assert {item.publication_badge for item in old_items} == {None}


@pytest.mark.asyncio
async def test_unpublish_word_removes_public_event_without_deleting_record(fake_db):
    service = LegalWordService()
    publish_date = date(2026, 7, 16)
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]
    ]
    await service.publish_daily_words(publish_date)

    updated = await service.unpublish_word(words[0].id, for_date=publish_date)
    items, total = await service.list_published_words(limit=6, today=publish_date)

    assert updated.status == LegalWordStatus.APPROVED
    assert updated.published_count == 0
    assert updated.publication_history == []
    assert total == DAILY_WORD_COUNT - 1
    assert words[0].id not in {item.id for item in items}


@pytest.mark.asyncio
async def test_publish_word_immediately_blocks_recently_published_word(fake_db):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    recent = await service.create_word(
        LegalWordCreate(
            word="Pertinent",
            meaning="Relevant.",
            synonyms=["relevant"],
            example_sentence="It is pertinent to note the delay.",
            status=LegalWordStatus.PUBLISHED,
            published_date=date(2026, 6, 1),
        ),
        admin,
    )

    with pytest.raises(LegalWordServiceError) as exc:
        await service.publish_word_immediately(recent.id, admin, for_date=date(2026, 7, 10))

    assert exc.value.status_code == 409
    assert fake_db.legal_word_daily_sets.docs == {}


@pytest.mark.asyncio
async def test_schedule_word_limits_one_date_to_six_words(fake_db):
    service = LegalWordService()
    admin = SimpleNamespace(id="admin-1")
    publish_date = date(2026, 7, 8)
    words = [
        await _create_approved_word(service, raw_word)
        for raw_word in [
            "Pertinent",
            "Notwithstanding",
            "Requisite",
            "Material",
            "Expedite",
            "Covenant",
            "Indemnity",
        ]
    ]

    for word in words[:DAILY_WORD_COUNT]:
        await service.schedule_word(word.id, publish_date, admin)

    with pytest.raises(LegalWordServiceError) as exc:
        await service.schedule_word(words[-1].id, publish_date, admin)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_scheduler_entrypoint_publishes_today(fake_db):
    service = LegalWordService()
    for raw_word in ["Pertinent", "Notwithstanding", "Requisite", "Material", "Expedite", "Covenant"]:
        await _create_approved_word(service, raw_word)

    result = await run_legal_word_daily_publish()

    assert result["published"] is True
    assert result["count"] == DAILY_WORD_COUNT
    daily_set = next(iter(fake_db.legal_word_daily_sets.docs.values()))
    assert len(daily_set["word_ids"]) == DAILY_WORD_COUNT
