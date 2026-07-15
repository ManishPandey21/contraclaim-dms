from __future__ import annotations

import calendar
import json
from datetime import date, datetime
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from ..core.database import get_database
from ..models.legal_word import (
    LegalWord,
    LegalWordAISuggestion,
    LegalWordAISuggestionResponse,
    LegalWordCreate,
    LegalWordDailySet,
    LegalWordSearchResponse,
    LegalWordSource,
    LegalWordSuggestionEligibility,
    LegalWordStatus,
    LegalWordPublicationType,
    LegalWordPublishedItem,
    LegalWordUpdate,
    TodayLegalWordsResponse,
)
from .audit_event_service import AuditEventService


try:  # pragma: no cover - optional production dependency path
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - tests and offline environments
    AsyncOpenAI = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
DAILY_WORD_COUNT = 6
SYSTEM_PUBLISHER_ID = "system:legal-word-daily-publisher"
REPUBLISH_MONTHS = 6
AI_SUGGESTION_COUNT = 6
PUBLIC_WORD_STATUSES = {
    LegalWordStatus.APPROVED.value,
    LegalWordStatus.SCHEDULED.value,
    LegalWordStatus.PUBLISHED.value,
}

AI_FALLBACK_WORDS: List[Dict[str, Any]] = [
    {
        "word": "Condition Precedent",
        "meaning": "A contractual requirement that must be satisfied before a right, obligation, or payment becomes enforceable.",
        "synonyms": ["precondition", "prior requirement", "trigger condition"],
        "example_sentence": "Release of the retention amount is subject to fulfilment of the condition precedent stated in the Contract.",
    },
    {
        "word": "Notwithstanding",
        "meaning": "A term used to show that a provision applies despite another clause or conflicting position.",
        "synonyms": ["despite", "regardless of", "in spite of"],
        "example_sentence": "Notwithstanding the pending measurement dispute, the Contractor reserves all rights under the Contract.",
    },
    {
        "word": "Without Prejudice",
        "meaning": "A reservation stating that a communication or proposal does not waive existing contractual or legal rights.",
        "synonyms": ["rights reserved", "no waiver", "without waiver"],
        "example_sentence": "This submission is made without prejudice to our entitlement to claim additional time and cost.",
    },
    {
        "word": "Quantum Meruit",
        "meaning": "A basis for claiming reasonable payment for work performed where the contract rate or valuation is disputed.",
        "synonyms": ["reasonable remuneration", "fair value", "earned value"],
        "example_sentence": "The Contractor is entitled to payment on a quantum meruit basis for the instructed additional work.",
    },
    {
        "word": "Indemnity",
        "meaning": "An obligation to compensate another party for specified losses, liabilities, or claims.",
        "synonyms": ["compensation undertaking", "hold harmless", "loss protection"],
        "example_sentence": "The Employer is requested to confirm the indemnity position for third-party claims arising from the instructed change.",
    },
    {
        "word": "Time at Large",
        "meaning": "A position where the contractual completion date is no longer enforceable and completion is required within a reasonable time.",
        "synonyms": ["reasonable time", "unenforceable completion date", "open completion period"],
        "example_sentence": "The Contractor submits that the prevention event has placed time at large unless a fair extension of time is granted.",
    },
]


class LegalWordServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class LegalWordService:
    """Persistence service for contractual/legal word learning records."""

    def __init__(self) -> None:
        self._db = None
        self._words = None
        self._daily_sets = None
        self._indexes_ready = False

    async def _get_handles(self):
        if self._words is None or self._daily_sets is None:
            db = await get_database()
            self._db = db
            self._words = db.legal_words
            self._daily_sets = db.legal_word_daily_sets
        if not self._indexes_ready:
            await self.ensure_indexes()
        return self._words, self._daily_sets

    async def ensure_indexes(self) -> None:
        words = self._words
        daily_sets = self._daily_sets
        if words is None or daily_sets is None:
            db = await get_database()
            self._db = db
            words = self._words = db.legal_words
            daily_sets = self._daily_sets = db.legal_word_daily_sets

        await words.create_index("normalized_word", unique=True)
        await words.create_index("category")
        await words.create_index("status")
        await words.create_index("source")
        await words.create_index("scheduled_date")
        await words.create_index("published_date")
        await words.create_index("first_suggested_date")
        await words.create_index("last_published_date")
        await words.create_index("is_eligible_for_republish")
        await daily_sets.create_index("published_date", unique=True)
        self._indexes_ready = True

    @staticmethod
    def normalize_word(value: str) -> str:
        return " ".join(str(value or "").strip().casefold().split())

    async def create_word(
        self, data: LegalWordCreate, current_user: Optional[Any] = None
    ) -> LegalWord:
        words, _ = await self._get_handles()
        now = datetime.utcnow()
        payload = data.model_dump(exclude_none=True, mode="json")
        payload["normalized_word"] = self.normalize_word(data.word)
        payload["created_at"] = now
        payload["updated_at"] = now
        payload["created_by"] = self._user_id(current_user)
        payload["updated_by"] = self._user_id(current_user)
        if data.source == LegalWordSource.AI_SUGGESTED and not payload.get("first_suggested_date"):
            payload["first_suggested_date"] = date.today().isoformat()
        payload.setdefault("published_count", 0)
        payload.setdefault("publication_history", [])
        if payload.get("published_date") and not payload.get("last_published_date"):
            payload["last_published_date"] = payload["published_date"]
        if payload.get("last_published_date") and not payload.get("publication_history"):
            payload["publication_history"] = [payload["last_published_date"]]
            payload["published_count"] = max(int(payload.get("published_count") or 0), 1)
            payload["is_eligible_for_republish"] = self._is_republish_eligible(
                self._coerce_date(payload["last_published_date"]),
                date.today(),
            )
        payload.setdefault("is_eligible_for_republish", True)

        try:
            result = await words.insert_one(payload)
        except DuplicateKeyError as exc:
            raise LegalWordServiceError("Word already exists", 409) from exc

        payload["_id"] = str(result.inserted_id)
        created = self._to_word(payload)
        await self._emit_audit(
            "legal_word.created",
            current_user,
            str(created.id or result.inserted_id),
            after=created.model_dump(mode="json"),
        )
        return created

    async def get_word_by_id(self, word_id: str) -> Optional[LegalWord]:
        words, _ = await self._get_handles()
        doc = await words.find_one({"_id": self._to_query_id(word_id)})
        return self._to_word(doc) if doc else None

    async def find_word_by_text(
        self, word: str, *, public_only: bool = False
    ) -> Optional[LegalWord]:
        words, _ = await self._get_handles()
        query: Dict[str, Any] = {"normalized_word": self.normalize_word(word)}
        if public_only:
            query["status"] = {"$in": sorted(PUBLIC_WORD_STATUSES)}
        doc = await words.find_one(query)
        return self._to_word(doc) if doc else None

    async def list_words(
        self,
        *,
        status: Optional[str] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        published_date: Optional[date] = None,
        publication_type: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[LegalWord], int]:
        words, _ = await self._get_handles()
        query: Dict[str, Any] = {}
        if status:
            query["status"] = status
        if source:
            query["source"] = source
            if source == LegalWordSource.USER_REQUESTED.value and not status:
                query["status"] = LegalWordStatus.PENDING_REVIEW.value
        if category:
            pattern = re.escape(category.strip())
            if pattern:
                query["category"] = {"$regex": f"^{pattern}$", "$options": "i"}
        if published_date:
            query["$or"] = [
                {"published_date": published_date.isoformat()},
                {"last_published_date": published_date.isoformat()},
                {"publication_history": published_date.isoformat()},
            ]
        if search:
            pattern = re.escape(search.strip())
            if pattern:
                search_clause = [
                    {"word": {"$regex": pattern, "$options": "i"}},
                    {"meaning": {"$regex": pattern, "$options": "i"}},
                    {"synonyms": {"$regex": pattern, "$options": "i"}},
                ]
                if "$or" in query:
                    query["$and"] = [{"$or": query.pop("$or")}, {"$or": search_clause}]
                else:
                    query["$or"] = search_clause

        page_size = max(1, min(int(limit or 50), 200))
        offset = max(0, int(skip or 0))
        if publication_type:
            docs = await words.find(query).sort("updated_at", -1).to_list(length=2000)
            docs = [
                doc
                for doc in docs
                if self._admin_publication_type(doc, date.today()).value == publication_type
            ]
            total = len(docs)
            docs = docs[offset : offset + page_size]
        else:
            total = await words.count_documents(query)
            docs = (
                await words.find(query)
                .sort("updated_at", -1)
                .skip(offset)
                .limit(page_size)
                .to_list(length=page_size)
            )
        return [self._to_word(doc) for doc in docs], total

    async def update_word(
        self,
        word_id: str,
        update: LegalWordUpdate,
        current_user: Optional[Any] = None,
    ) -> LegalWord:
        words, _ = await self._get_handles()
        update_fields = update.model_dump(
            exclude_unset=True, exclude_none=True, mode="json"
        )
        existing_doc = await words.find_one({"_id": self._to_query_id(word_id)})
        if "word" in update_fields:
            update_fields["normalized_word"] = self.normalize_word(update_fields["word"])
        publication_date_value = update_fields.get("last_published_date") or update_fields.get("published_date")
        if publication_date_value:
            parsed_publication_date = self._coerce_date(publication_date_value)
            if parsed_publication_date:
                history = self._publication_history(existing_doc or {})
                already_recorded = parsed_publication_date in history
                if not already_recorded:
                    history.append(parsed_publication_date)
                update_fields.setdefault("published_date", parsed_publication_date.isoformat())
                update_fields["last_published_date"] = parsed_publication_date.isoformat()
                update_fields["publication_history"] = [
                    item.isoformat() for item in sorted(history)
                ]
                if "published_count" not in update_fields:
                    update_fields["published_count"] = int(
                        (existing_doc or {}).get("published_count") or 0
                    ) + (0 if already_recorded else 1)
                update_fields["is_eligible_for_republish"] = self._is_republish_eligible(
                    parsed_publication_date,
                    date.today(),
                )
        if not update_fields:
            existing = await self.get_word_by_id(word_id)
            if not existing:
                raise LegalWordServiceError("Word not found", 404)
            return existing

        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = self._user_id(current_user)
        result = await words.update_one(
            {"_id": self._to_query_id(word_id)},
            {"$set": update_fields},
        )
        if result.matched_count == 0:
            raise LegalWordServiceError("Word not found", 404)

        updated = await words.find_one({"_id": self._to_query_id(word_id)})
        updated_word = self._to_word(updated)
        await self._emit_audit(
            "legal_word.updated",
            current_user,
            word_id,
            before=self._json_safe_doc(existing_doc),
            after=updated_word.model_dump(mode="json"),
        )
        return updated_word

    async def search_or_request_word(
        self, query: str, current_user: Optional[Any] = None
    ) -> LegalWordSearchResponse:
        normalized = self.normalize_word(query)
        if not normalized:
            raise LegalWordServiceError("Search query is required", 400)

        existing_public = await self.find_word_by_text(query, public_only=True)
        if existing_public:
            return LegalWordSearchResponse(found=True, word=existing_public)

        requested = await self._get_or_create_user_request(query, current_user)
        _ = requested
        return LegalWordSearchResponse(
            found=False,
            requested=True,
            message=(
                "This word is not available in the database yet. "
                "It has been submitted for admin review."
            ),
        )

    async def suggest_words_with_ai(
        self,
        current_user: Optional[Any] = None,
        *,
        today: Optional[date] = None,
    ) -> LegalWordAISuggestionResponse:
        """Generate AI-assisted legal word suggestions and evaluate publish eligibility."""
        target_date = today or date.today()
        requested_docs = await self._pending_user_requested_docs(AI_SUGGESTION_COUNT)
        requested_words = [str(doc.get("word") or "") for doc in requested_docs if doc.get("word")]
        raw_candidates = await self._generate_ai_word_candidates(
            preferred_words=requested_words
        )
        candidates = self._prepare_ai_candidates(
            raw_candidates,
            preferred_docs=requested_docs,
        )
        suggestions: List[LegalWordAISuggestion] = []
        for candidate in candidates[:AI_SUGGESTION_COUNT]:
            suggestions.append(
                await self._evaluate_ai_candidate(candidate, target_date, current_user)
            )
        return LegalWordAISuggestionResponse(suggestions=suggestions)

    async def approve_word(
        self, word_id: str, current_user: Optional[Any] = None
    ) -> LegalWord:
        existing = await self.get_word_by_id(word_id)
        if not existing:
            raise LegalWordServiceError("Word not found", 404)
        self._ensure_complete_word(existing)
        updated = await self._set_word_fields(
            word_id,
            {
                "status": LegalWordStatus.APPROVED.value,
                "approved_by_admin_id": self._user_id(current_user),
                "approved_at": datetime.utcnow(),
            },
            current_user,
        )
        await self._emit_audit(
            "legal_word.approved",
            current_user,
            word_id,
            before=existing.model_dump(mode="json"),
            after=updated.model_dump(mode="json"),
        )
        return updated

    async def reject_word(
        self, word_id: str, current_user: Optional[Any] = None
    ) -> LegalWord:
        existing = await self.get_word_by_id(word_id)
        updated = await self._set_word_fields(
            word_id,
            {"status": LegalWordStatus.REJECTED.value},
            current_user,
        )
        await self._emit_audit(
            "legal_word.rejected",
            current_user,
            word_id,
            before=existing.model_dump(mode="json") if existing else None,
            after=updated.model_dump(mode="json"),
        )
        return updated

    async def deactivate_word(
        self, word_id: str, current_user: Optional[Any] = None
    ) -> LegalWord:
        existing = await self.get_word_by_id(word_id)
        updated = await self._set_word_fields(
            word_id,
            {"status": LegalWordStatus.INACTIVE.value},
            current_user,
        )
        await self._emit_audit(
            "legal_word.deactivated",
            current_user,
            word_id,
            before=existing.model_dump(mode="json") if existing else None,
            after=updated.model_dump(mode="json"),
        )
        return updated

    async def schedule_word(
        self,
        word_id: str,
        scheduled_date: date,
        current_user: Optional[Any] = None,
    ) -> LegalWord:
        existing = await self.get_word_by_id(word_id)
        if not existing:
            raise LegalWordServiceError("Word not found", 404)
        self._ensure_complete_word(existing)
        self._ensure_republish_eligible(existing, scheduled_date)
        if existing.status not in {
            LegalWordStatus.APPROVED,
            LegalWordStatus.SCHEDULED,
            LegalWordStatus.PUBLISHED,
        }:
            raise LegalWordServiceError(
                "Only approved or eligible published words can be scheduled",
                400,
            )
        await self._ensure_schedule_slot_available(word_id, scheduled_date)
        updated = await self._set_word_fields(
            word_id,
            {
                "status": LegalWordStatus.SCHEDULED.value,
                "scheduled_date": scheduled_date.isoformat(),
            },
            current_user,
        )
        await self._emit_audit(
            "legal_word.scheduled",
            current_user,
            word_id,
            before=existing.model_dump(mode="json"),
            after=updated.model_dump(mode="json"),
            metadata={"scheduled_date": scheduled_date.isoformat()},
        )
        return updated

    async def publish_daily_words(
        self,
        for_date: Optional[date] = None,
        current_user: Optional[Any] = None,
    ) -> TodayLegalWordsResponse:
        """Publish the platform-wide set of contractual/legal words for a date.

        The selection is intentionally deterministic: manually scheduled words
        for the date are used first, then approved unscheduled words fill the
        remaining slots in approval/creation order. The daily set is keyed by
        date, so repeated scheduler runs return the already-published set.
        """
        target_date = for_date or date.today()
        existing = await self.get_daily_set(target_date)
        if existing:
            return await self.get_today_words(target_date)

        selected_docs = await self._select_daily_word_docs(target_date)
        if len(selected_docs) < DAILY_WORD_COUNT:
            raise LegalWordServiceError(
                (
                    "Not enough approved unpublished legal words to publish "
                    f"{DAILY_WORD_COUNT} words for {target_date.isoformat()}."
                ),
                409,
            )

        word_ids = [str(doc.get("_id")) for doc in selected_docs]
        try:
            await self.create_daily_set(target_date, word_ids, current_user)
        except LegalWordServiceError as exc:
            if exc.status_code == 409:
                return await self.get_today_words(target_date)
            raise

        await self._mark_words_published(word_ids, target_date, current_user)
        return await self.get_today_words(target_date)

    async def publish_word_immediately(
        self,
        word_id: str,
        current_user: Optional[Any] = None,
        *,
        for_date: Optional[date] = None,
    ) -> TodayLegalWordsResponse:
        """Publish one approved eligible word as part of the shared daily set."""
        target_date = for_date or date.today()
        existing = await self.get_word_by_id(word_id)
        if not existing:
            raise LegalWordServiceError("Word not found", 404)
        self._ensure_complete_word(existing)
        self._ensure_republish_eligible(existing, target_date)
        if existing.status not in {
            LegalWordStatus.APPROVED,
            LegalWordStatus.SCHEDULED,
            LegalWordStatus.PUBLISHED,
        }:
            raise LegalWordServiceError("Approve the word before publishing it", 400)

        words, daily_sets = await self._get_handles()
        target_id = str(existing.id)
        current_set = await self.get_daily_set(target_date)
        if current_set:
            if target_id in current_set.word_ids:
                await self._mark_words_published([target_id], target_date, current_user)
                return await self.get_today_words(target_date)
            if len(current_set.word_ids) >= DAILY_WORD_COUNT:
                raise LegalWordServiceError(
                    f"{DAILY_WORD_COUNT} words are already published for {target_date.isoformat()}",
                    409,
                )
            next_ids = [*current_set.word_ids, target_id]
            await daily_sets.update_one(
                {"published_date": target_date.isoformat()},
                {
                    "$set": {
                        "word_ids": next_ids,
                        "updated_at": datetime.utcnow(),
                        "updated_by": self._user_id(current_user),
                    }
                },
            )
            await self._mark_words_published([target_id], target_date, current_user)
            return await self.get_today_words(target_date)

        target_doc = await words.find_one({"_id": self._to_query_id(target_id)})
        if not target_doc:
            raise LegalWordServiceError("Word not found", 404)
        selected_docs = [target_doc]
        selected_ids = {target_id}
        for doc in await self._select_daily_word_docs(target_date):
            doc_id = str(doc.get("_id"))
            if doc_id in selected_ids:
                continue
            selected_docs.append(doc)
            selected_ids.add(doc_id)
            if len(selected_docs) == DAILY_WORD_COUNT:
                break

        if len(selected_docs) < DAILY_WORD_COUNT:
            raise LegalWordServiceError(
                (
                    "Not enough approved eligible legal words to publish "
                    f"{DAILY_WORD_COUNT} words for {target_date.isoformat()}."
                ),
                409,
            )

        word_ids = [str(doc.get("_id")) for doc in selected_docs]
        await self.create_daily_set(target_date, word_ids, current_user)
        await self._mark_words_published(word_ids, target_date, current_user)
        return await self.get_today_words(target_date)

    async def get_today_words(self, for_date: Optional[date] = None) -> TodayLegalWordsResponse:
        target_date = for_date or date.today()
        daily_set = await self.get_daily_set(target_date)
        if not daily_set:
            return TodayLegalWordsResponse(
                published_date=target_date,
                words=[],
                count=0,
                message="No daily contractual/legal words have been published for this date yet.",
            )

        words, _ = await self._get_handles()
        word_docs = await words.find(
            {"_id": {"$in": [self._to_query_id(word_id) for word_id in daily_set.word_ids]}}
        ).to_list(length=max(len(daily_set.word_ids), 1))
        word_map = {str(doc.get("_id")): self._to_word(doc) for doc in word_docs}
        ordered = [word_map[word_id] for word_id in daily_set.word_ids if word_id in word_map]
        return TodayLegalWordsResponse(
            published_date=target_date,
            words=ordered,
            count=len(ordered),
        )

    async def list_published_words(
        self,
        *,
        search: Optional[str] = None,
        category: Optional[str] = None,
        published_date: Optional[date] = None,
        publication_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 6,
        today: Optional[date] = None,
    ) -> Tuple[List[LegalWordPublishedItem], int]:
        """Return publication events, newest first, for the learning page."""
        words, daily_sets = await self._get_handles()
        page_size = max(1, min(int(limit or 6), 200))
        offset = max(0, int(skip or 0))
        current_date = today or date.today()

        daily_docs = await daily_sets.find({}).sort("published_date", -1).to_list(length=2000)
        events: List[Tuple[date, int, str]] = []
        seen: set[Tuple[str, date]] = set()
        for daily_doc in daily_docs:
            event_date = self._coerce_date(daily_doc.get("published_date"))
            if not event_date:
                continue
            for position, raw_word_id in enumerate(daily_doc.get("word_ids") or []):
                word_id = str(raw_word_id)
                key = (word_id, event_date)
                if key in seen:
                    continue
                seen.add(key)
                events.append((event_date, position, word_id))

        word_ids = sorted({word_id for _, _, word_id in events})
        word_docs = (
            await words.find({"_id": {"$in": [self._to_query_id(word_id) for word_id in word_ids]}})
            .to_list(length=max(len(word_ids), 1))
            if word_ids
            else []
        )
        docs_by_id = {str(doc.get("_id")): doc for doc in word_docs}

        legacy_docs = await words.find(
            {"status": LegalWordStatus.PUBLISHED.value}
        ).to_list(length=2000)
        for doc in legacy_docs:
            word_id = str(doc.get("_id"))
            docs_by_id.setdefault(word_id, doc)
            for event_date in self._publication_history(doc):
                key = (word_id, event_date)
                if key in seen:
                    continue
                seen.add(key)
                events.append((event_date, DAILY_WORD_COUNT, word_id))

        events.sort(key=lambda item: (item[0].isoformat(), -item[1], item[2]), reverse=True)

        items: List[LegalWordPublishedItem] = []
        for event_date, _, word_id in events:
            doc = docs_by_id.get(word_id)
            if not doc:
                continue
            if doc.get("status") in {
                LegalWordStatus.INACTIVE.value,
                LegalWordStatus.REJECTED.value,
            }:
                continue
            if published_date and event_date != published_date:
                continue
            publication_kind = self._publication_type_for_date(doc, event_date)
            if publication_type and publication_kind.value != publication_type:
                continue
            if category and str(doc.get("category") or "").casefold() != category.casefold():
                continue
            if search and not self._word_doc_matches_search(doc, search):
                continue
            items.append(self._published_item_from_doc(doc, event_date, current_date))

        total = len(items)
        return items[offset : offset + page_size], total

    async def get_daily_set(self, for_date: date) -> Optional[LegalWordDailySet]:
        _, daily_sets = await self._get_handles()
        doc = await daily_sets.find_one({"published_date": for_date.isoformat()})
        return self._to_daily_set(doc) if doc else None

    async def create_daily_set(
        self,
        for_date: date,
        word_ids: List[str],
        current_user: Optional[Any] = None,
    ) -> LegalWordDailySet:
        if len(word_ids) != DAILY_WORD_COUNT:
            raise LegalWordServiceError(
                f"Daily set must contain exactly {DAILY_WORD_COUNT} words",
                400,
            )
        _, daily_sets = await self._get_handles()
        now = datetime.utcnow()
        payload = {
            "published_date": for_date.isoformat(),
            "word_ids": [str(word_id) for word_id in word_ids],
            "created_at": now,
            "updated_at": now,
            "created_by": self._user_id(current_user),
            "updated_by": self._user_id(current_user),
        }
        try:
            result = await daily_sets.insert_one(payload)
        except DuplicateKeyError as exc:
            raise LegalWordServiceError("Daily set already exists", 409) from exc
        payload["_id"] = str(result.inserted_id)
        return self._to_daily_set(payload)

    async def unpublish_word(
        self,
        word_id: str,
        current_user: Optional[Any] = None,
        *,
        for_date: Optional[date] = None,
    ) -> LegalWord:
        """Remove a word from a publication date and keep its remaining history."""
        words, daily_sets = await self._get_handles()
        existing_doc = await words.find_one({"_id": self._to_query_id(word_id)})
        if not existing_doc:
            raise LegalWordServiceError("Word not found", 404)

        target_date = (
            for_date
            or self._last_published_from_doc(existing_doc)
            or date.today()
        )
        daily_doc = await daily_sets.find_one({"published_date": target_date.isoformat()})
        if daily_doc:
            next_ids = [
                str(item)
                for item in daily_doc.get("word_ids") or []
                if str(item) != str(word_id)
            ]
            await daily_sets.update_one(
                {"published_date": target_date.isoformat()},
                {
                    "$set": {
                        "word_ids": next_ids,
                        "updated_at": datetime.utcnow(),
                        "updated_by": self._user_id(current_user),
                    }
                },
            )

        history = [
            item
            for item in self._publication_history(existing_doc)
            if item != target_date
        ]
        last_published = history[-1] if history else None
        update_fields = {
            "status": (
                LegalWordStatus.PUBLISHED.value
                if last_published
                else LegalWordStatus.APPROVED.value
            ),
            "published_date": last_published.isoformat() if last_published else None,
            "last_published_date": last_published.isoformat() if last_published else None,
            "published_count": len(history),
            "publication_history": [item.isoformat() for item in history],
            "is_eligible_for_republish": self._is_republish_eligible(
                last_published,
                date.today(),
            ),
            "updated_at": datetime.utcnow(),
            "updated_by": self._user_id(current_user),
        }
        await words.update_one(
            {"_id": self._to_query_id(word_id)},
            {"$set": update_fields},
        )
        updated_doc = await words.find_one({"_id": self._to_query_id(word_id)})
        updated = self._to_word(updated_doc)
        await self._emit_audit(
            "legal_word.unpublished",
            current_user,
            word_id,
            before=self._json_safe_doc(existing_doc),
            after=updated.model_dump(mode="json"),
            metadata={"published_date": target_date.isoformat()},
        )
        return updated

    async def delete_word(
        self,
        word_id: str,
        current_user: Optional[Any] = None,
    ) -> LegalWord:
        """Delete a word and remove it from any daily publication set."""
        words, daily_sets = await self._get_handles()
        existing_doc = await words.find_one({"_id": self._to_query_id(word_id)})
        if not existing_doc:
            raise LegalWordServiceError("Word not found", 404)

        daily_docs = await daily_sets.find({}).to_list(length=2000)
        for daily_doc in daily_docs:
            word_ids = [str(item) for item in daily_doc.get("word_ids") or []]
            if str(word_id) not in word_ids:
                continue
            next_ids = [item for item in word_ids if item != str(word_id)]
            await daily_sets.update_one(
                {"_id": daily_doc.get("_id")},
                {
                    "$set": {
                        "word_ids": next_ids,
                        "updated_at": datetime.utcnow(),
                        "updated_by": self._user_id(current_user),
                    }
                },
            )

        delete_one = getattr(words, "delete_one", None)
        if delete_one is None:
            raise LegalWordServiceError("Delete operation is unavailable", 500)
        result = await delete_one({"_id": self._to_query_id(word_id)})
        deleted_count = int(getattr(result, "deleted_count", 0) or 0)
        if deleted_count == 0:
            raise LegalWordServiceError("Word not found", 404)
        deleted = self._to_word(existing_doc)
        await self._emit_audit(
            "legal_word.deleted",
            current_user,
            word_id,
            before=self._json_safe_doc(existing_doc),
        )
        return deleted

    async def _generate_ai_word_candidates(
        self,
        preferred_words: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key or AsyncOpenAI is None:
            return list(AI_FALLBACK_WORDS)

        preferred = [
            " ".join(str(word or "").strip().split())
            for word in preferred_words or []
            if str(word or "").strip()
        ]
        preference_text = ""
        if preferred:
            preference_text = (
                "Prioritize these user-requested words first and provide complete "
                f"entries for them: {', '.join(preferred[:AI_SUGGESTION_COUNT])}. "
            )
        prompt = (
            f"Suggest exactly {AI_SUGGESTION_COUNT} contractual/legal words useful for construction "
            "contract correspondence. "
            f"{preference_text}"
            "Avoid generic words and return only JSON "
            "with this shape: {\"words\":[{\"word\":\"...\",\"meaning\":\"...\","
            "\"synonyms\":[\"...\"],\"example_sentence\":\"...\"}]}. "
            "Examples must sound like contractual letters and must not cite any "
            "specific project or party."
        )
        try:
            client = AsyncOpenAI(api_key=api_key, timeout=30)
            response = await client.chat.completions.create(
                model=os.getenv("LEGAL_WORD_AI_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You generate concise contractual/legal vocabulary for "
                            "contract administrators. Return valid JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=1400,
            )
            content = response.choices[0].message.content if response.choices else ""
            parsed = self._parse_ai_candidate_payload(self._normalize_ai_content(content))
            return parsed or list(AI_FALLBACK_WORDS)
        except Exception as exc:  # pragma: no cover - depends on external AI service
            logger.warning("AI legal word suggestion failed; using fallback words: %s", exc)
            return list(AI_FALLBACK_WORDS)

    async def _evaluate_ai_candidate(
        self,
        candidate: Dict[str, Any],
        today: date,
        current_user: Optional[Any],
    ) -> LegalWordAISuggestion:
        words, _ = await self._get_handles()
        normalized = self.normalize_word(candidate["word"])
        existing_doc = await words.find_one({"normalized_word": normalized})
        if not existing_doc:
            word_record = await self.create_word(
                LegalWordCreate(
                    word=candidate["word"],
                    meaning=candidate["meaning"],
                    synonyms=candidate["synonyms"],
                    example_sentence=candidate["example_sentence"],
                    source=LegalWordSource.AI_SUGGESTED,
                    status=LegalWordStatus.PENDING_REVIEW,
                    first_suggested_date=today,
                    is_eligible_for_republish=True,
                ),
                current_user,
            )
            return LegalWordAISuggestion(
                **candidate,
                existing_word_id=word_record.id,
                word_record=word_record,
                previous_publication_status="Never published",
                eligibility_status=LegalWordSuggestionEligibility.NEW_WORD,
                eligibility_label="New Word",
                is_eligible_for_republish=True,
            )

        last_published = self._last_published_from_doc(existing_doc)
        eligible = self._is_republish_eligible(last_published, today)
        first_suggested = existing_doc.get("first_suggested_date") or today.isoformat()
        update_fields: Dict[str, Any] = {
            "first_suggested_date": first_suggested,
            "is_eligible_for_republish": eligible,
            "updated_at": datetime.utcnow(),
            "updated_by": self._user_id(current_user),
        }
        update_fields.update(self._completion_fields_for_suggestion(existing_doc, candidate))
        await words.update_one(
            {"_id": existing_doc.get("_id")},
            {"$set": update_fields},
        )
        updated_doc = await words.find_one({"_id": existing_doc.get("_id")}) or existing_doc
        word_record = self._to_word(updated_doc)
        if (
            existing_doc.get("source") == LegalWordSource.USER_REQUESTED.value
            and existing_doc.get("status") == LegalWordStatus.PENDING_REVIEW.value
            and not last_published
        ):
            label = "User Requested Word"
            previous_status = "User requested; never published"
            status_value = LegalWordSuggestionEligibility.NEW_WORD
            blocked_reason = None
        elif eligible:
            label = (
                "Eligible for Republishing - Last Published More Than 6 Months Ago"
                if last_published
                else "Eligible for Republishing - Never Published"
            )
            previous_status = (
                f"Last published on {last_published.isoformat()}"
                if last_published
                else "Never published"
            )
            status_value = LegalWordSuggestionEligibility.ELIGIBLE_FOR_REPUBLISH
            blocked_reason = None
        else:
            label = "Already Published Within 6 Months - Not Allowed"
            previous_status = f"Last published on {last_published.isoformat()}"
            status_value = LegalWordSuggestionEligibility.BLOCKED_RECENTLY_PUBLISHED
            blocked_reason = (
                "This word was published within the last 6 months and cannot be "
                "published or scheduled again yet."
            )

        return LegalWordAISuggestion(
            **candidate,
            existing_word_id=word_record.id,
            word_record=word_record,
            previous_publication_status=previous_status,
            last_published_date=last_published,
            eligibility_status=status_value,
            eligibility_label=label,
            is_eligible_for_republish=eligible,
            blocked_reason=blocked_reason,
        )

    async def _pending_user_requested_docs(
        self,
        limit: int,
    ) -> List[Dict[str, Any]]:
        words, _ = await self._get_handles()
        docs = await words.find(
            {
                "source": LegalWordSource.USER_REQUESTED.value,
                "status": LegalWordStatus.PENDING_REVIEW.value,
            }
        ).to_list(length=max(1, limit))
        return sorted(
            docs,
            key=lambda doc: (
                self._sort_value(doc.get("requested_at")),
                self._sort_value(doc.get("created_at")),
                self._id_sort_value(doc.get("_id")),
            ),
        )[:limit]

    def _prepare_ai_candidates(
        self,
        raw_candidates: List[Dict[str, Any]],
        *,
        preferred_docs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen: set[str] = set()
        raw_by_key: Dict[str, Dict[str, Any]] = {}
        for raw in raw_candidates or []:
            candidate = self._candidate_from_raw(raw)
            if not candidate:
                continue
            raw_by_key.setdefault(self.normalize_word(candidate["word"]), candidate)

        for doc in preferred_docs or []:
            word = doc.get("word") or ""
            key = self.normalize_word(str(word))
            if not key or key in seen:
                continue
            candidate = raw_by_key.get(key) or self._candidate_from_existing_doc(doc)
            if not candidate:
                continue
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= AI_SUGGESTION_COUNT:
                return candidates

        for raw in [*(raw_candidates or []), *AI_FALLBACK_WORDS]:
            candidate = self._candidate_from_raw(raw)
            if not candidate:
                continue
            key = self.normalize_word(candidate["word"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= AI_SUGGESTION_COUNT:
                break
        return candidates

    def _candidate_from_existing_doc(self, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        word = " ".join(str(doc.get("word") or "").strip().split())
        if not word:
            return None
        return {
            "word": word,
            "meaning": doc.get("meaning") or "",
            "synonyms": list(doc.get("synonyms") or []),
            "example_sentence": doc.get("example_sentence") or "",
        }

    def _completion_fields_for_suggestion(
        self,
        existing_doc: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        if existing_doc.get("source") != LegalWordSource.USER_REQUESTED.value:
            return {}

        fields: Dict[str, Any] = {}
        if not existing_doc.get("meaning") and candidate.get("meaning"):
            fields["meaning"] = candidate["meaning"]
        if not existing_doc.get("example_sentence") and candidate.get("example_sentence"):
            fields["example_sentence"] = candidate["example_sentence"]
        if not existing_doc.get("synonyms") and candidate.get("synonyms"):
            fields["synonyms"] = list(candidate.get("synonyms") or [])
        return fields

    def _candidate_from_raw(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        synonyms = (
            raw.get("synonyms")
            or raw.get("useful_synonyms")
            or raw.get("Useful Synonyms")
            or []
        )
        if isinstance(synonyms, str):
            synonyms = [part.strip() for part in synonyms.split(",")]
        try:
            payload = LegalWordCreate(
                word=raw.get("word") or raw.get("Word") or "",
                meaning=raw.get("meaning") or raw.get("Meaning") or raw.get("definition") or "",
                synonyms=list(synonyms or []),
                example_sentence=(
                    raw.get("example_sentence")
                    or raw.get("example")
                    or raw.get("Example in Contractual Letter")
                    or ""
                ),
                source=LegalWordSource.AI_SUGGESTED,
            )
        except Exception:
            return None
        return {
            "word": payload.word,
            "meaning": payload.meaning,
            "synonyms": payload.synonyms,
            "example_sentence": payload.example_sentence,
        }

    def _parse_ai_candidate_payload(self, content: str) -> List[Dict[str, Any]]:
        text = (content or "").strip()
        if not text:
            return []
        payload: Any = None
        for candidate_text in self._json_candidates(text):
            try:
                payload = json.loads(candidate_text)
                break
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            words = payload.get("words") or payload.get("suggestions") or payload.get("items")
            return words if isinstance(words, list) else []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _json_candidates(text: str) -> List[str]:
        candidates = [text]
        if "[" in text and "]" in text:
            candidates.append(text[text.find("[") : text.rfind("]") + 1])
        if "{" in text and "}" in text:
            candidates.append(text[text.find("{") : text.rfind("}") + 1])
        return candidates

    @staticmethod
    def _normalize_ai_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    text = getattr(item, "text", None) or getattr(item, "content", None)
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(content or "")

    async def _ensure_schedule_slot_available(
        self,
        word_id: str,
        scheduled_date: date,
    ) -> None:
        words, _ = await self._get_handles()
        docs = await words.find(
            {
                "status": LegalWordStatus.SCHEDULED.value,
                "scheduled_date": scheduled_date.isoformat(),
            }
        ).to_list(length=DAILY_WORD_COUNT + 1)
        existing_ids = {str(doc.get("_id")) for doc in docs if str(doc.get("_id")) != str(word_id)}
        if len(existing_ids) >= DAILY_WORD_COUNT:
            raise LegalWordServiceError(
                f"{DAILY_WORD_COUNT} words are already scheduled for {scheduled_date.isoformat()}",
                409,
            )

    async def _select_daily_word_docs(self, target_date: date) -> List[Dict[str, Any]]:
        words, _ = await self._get_handles()
        scheduled_docs = await words.find(
            {
                "status": LegalWordStatus.SCHEDULED.value,
                "scheduled_date": target_date.isoformat(),
            }
        ).to_list(length=DAILY_WORD_COUNT + 1)
        scheduled_docs = self._sort_word_docs(scheduled_docs, scheduled=True)
        if len(scheduled_docs) > DAILY_WORD_COUNT:
            raise LegalWordServiceError(
                f"More than {DAILY_WORD_COUNT} words are scheduled for {target_date.isoformat()}",
                409,
            )

        selected: List[Dict[str, Any]] = []
        selected_ids: set[str] = set()
        for doc in scheduled_docs:
            word = self._to_word(doc)
            self._ensure_complete_word(word)
            self._ensure_republish_eligible(word, target_date)
            selected.append(doc)
            selected_ids.add(str(doc.get("_id")))

        remaining = DAILY_WORD_COUNT - len(selected)
        if remaining <= 0:
            return selected

        approved_docs = await words.find(
            {"status": {"$in": [LegalWordStatus.APPROVED.value, LegalWordStatus.PUBLISHED.value]}}
        ).to_list(length=1000)
        approved_docs = self._sort_word_docs(approved_docs, scheduled=False)
        for doc in approved_docs:
            doc_id = str(doc.get("_id"))
            if doc_id in selected_ids:
                continue
            if doc.get("scheduled_date"):
                continue
            word = self._to_word(doc)
            try:
                self._ensure_complete_word(word)
                self._ensure_republish_eligible(word, target_date)
            except LegalWordServiceError:
                continue
            selected.append(doc)
            selected_ids.add(doc_id)
            if len(selected) == DAILY_WORD_COUNT:
                break

        return selected

    async def _mark_words_published(
        self,
        word_ids: List[str],
        published_date: date,
        current_user: Optional[Any],
    ) -> None:
        words, _ = await self._get_handles()
        now = datetime.utcnow()
        for word_id in word_ids:
            doc = await words.find_one({"_id": self._to_query_id(word_id)})
            if not doc:
                continue
            history = self._publication_history(doc)
            already_recorded = published_date in history
            if not already_recorded:
                history.append(published_date)
            update_fields = {
                "status": LegalWordStatus.PUBLISHED.value,
                "published_date": published_date.isoformat(),
                "last_published_date": published_date.isoformat(),
                "published_count": int(doc.get("published_count") or 0)
                + (0 if already_recorded else 1),
                "publication_history": [item.isoformat() for item in history],
                "is_eligible_for_republish": False,
                "updated_at": now,
                "updated_by": self._user_id(current_user),
            }
            await words.update_one(
                {"_id": self._to_query_id(word_id)},
                {"$set": update_fields},
            )
            await self._emit_audit(
                "legal_word.published",
                current_user,
                word_id,
                before=self._json_safe_doc(doc),
                after=self._json_safe_doc({**doc, **update_fields}),
                metadata={
                    "published_date": published_date.isoformat(),
                    "publication_type": self._publication_type_for_date(
                        doc,
                        published_date,
                    ).value,
                },
            )

    async def _get_or_create_user_request(
        self, word: str, current_user: Optional[Any]
    ) -> LegalWord:
        words, _ = await self._get_handles()
        normalized = self.normalize_word(word)
        existing = await words.find_one({"normalized_word": normalized})
        if existing:
            return self._to_word(existing)

        now = datetime.utcnow()
        payload = {
            "word": " ".join(word.strip().split()),
            "normalized_word": normalized,
            "meaning": None,
            "synonyms": [],
            "example_sentence": None,
            "source": LegalWordSource.USER_REQUESTED.value,
            "status": LegalWordStatus.PENDING_REVIEW.value,
            "requested_by_user_id": self._user_id(current_user),
            "requested_at": now,
            "created_at": now,
            "updated_at": now,
            "created_by": self._user_id(current_user),
            "updated_by": self._user_id(current_user),
        }
        try:
            result = await words.insert_one(payload)
        except DuplicateKeyError:
            existing = await words.find_one({"normalized_word": normalized})
            if existing:
                return self._to_word(existing)
            raise
        payload["_id"] = str(result.inserted_id)
        return self._to_word(payload)

    async def _set_word_fields(
        self,
        word_id: str,
        fields: Dict[str, Any],
        current_user: Optional[Any],
    ) -> LegalWord:
        words, _ = await self._get_handles()
        update_fields = dict(fields)
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = self._user_id(current_user)
        result = await words.update_one(
            {"_id": self._to_query_id(word_id)},
            {"$set": update_fields},
        )
        if result.matched_count == 0:
            raise LegalWordServiceError("Word not found", 404)
        updated = await words.find_one({"_id": self._to_query_id(word_id)})
        return self._to_word(updated)

    def _published_item_from_doc(
        self,
        doc: Dict[str, Any],
        event_date: date,
        today: date,
    ) -> LegalWordPublishedItem:
        word = self._to_word(doc)
        publication_type = self._publication_type_for_date(doc, event_date)
        badge: Optional[str] = None
        if event_date == today:
            badge = "Repeated" if publication_type == LegalWordPublicationType.REPEATED else "New"
        payload = word.model_dump(by_alias=True)
        payload["display_publication_date"] = event_date
        payload["publication_type"] = publication_type
        payload["publication_badge"] = badge
        return LegalWordPublishedItem(**payload)

    def _publication_type_for_date(
        self,
        doc: Dict[str, Any],
        event_date: date,
    ) -> LegalWordPublicationType:
        prior_history = [
            item for item in self._publication_history(doc) if item < event_date
        ]
        return (
            LegalWordPublicationType.REPEATED
            if prior_history
            else LegalWordPublicationType.NEW
        )

    def _admin_publication_type(
        self,
        doc: Dict[str, Any],
        today: date,
    ) -> LegalWordPublicationType:
        history = self._publication_history(doc)
        if not history or int(doc.get("published_count") or 0) <= 0:
            return LegalWordPublicationType.UNPUBLISHED
        if self._is_republish_eligible(history[-1], today):
            return LegalWordPublicationType.ELIGIBLE_FOR_REPUBLICATION
        if len(history) > 1:
            return LegalWordPublicationType.REPEATED
        return LegalWordPublicationType.PREVIOUSLY_PUBLISHED

    def _word_doc_matches_search(self, doc: Dict[str, Any], search: str) -> bool:
        needle = search.strip().casefold()
        if not needle:
            return True
        haystacks = [
            str(doc.get("word") or ""),
            str(doc.get("meaning") or ""),
            str(doc.get("example_sentence") or ""),
            str(doc.get("category") or ""),
            " ".join(str(item) for item in doc.get("synonyms") or []),
        ]
        return any(needle in value.casefold() for value in haystacks)

    def _json_safe_doc(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if doc is None:
            return None
        safe: Dict[str, Any] = {}
        for key, value in doc.items():
            if isinstance(value, (date, datetime)):
                safe[key] = value.isoformat()
            elif isinstance(value, ObjectId):
                safe[key] = str(value)
            elif isinstance(value, list):
                safe[key] = [
                    item.isoformat() if isinstance(item, (date, datetime)) else str(item) if isinstance(item, ObjectId) else item
                    for item in value
                ]
            else:
                safe[key] = value
        return safe

    async def _emit_audit(
        self,
        action: str,
        current_user: Optional[Any],
        word_id: str,
        *,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            await AuditEventService(self._db).emit(
                action=action,
                actor_id=self._user_id(current_user),
                resource_type="legal_word",
                resource_id=str(word_id),
                before=before,
                after=after,
                metadata=metadata,
            )
        except Exception:  # pragma: no cover - audit must not block admin actions
            logger.warning("Failed to write legal word audit event %s", action, exc_info=True)

    def _ensure_complete_word(self, word: LegalWord) -> None:
        if not word.meaning or not word.example_sentence:
            raise LegalWordServiceError(
                "Word must have meaning and example sentence before approval or scheduling",
                400,
            )

    def _ensure_republish_eligible(self, word: LegalWord, target_date: date) -> None:
        last_published = word.last_published_date or word.published_date
        if self._is_republish_eligible(last_published, target_date):
            return
        raise LegalWordServiceError(
            (
                "Word was published within the last "
                f"{REPUBLISH_MONTHS} months and is not eligible for republishing"
            ),
            409,
        )

    def _is_republish_eligible(
        self,
        last_published_date: Optional[date],
        today: date,
    ) -> bool:
        if last_published_date is None:
            return True
        return last_published_date < self._six_month_cutoff(today)

    @staticmethod
    def _six_month_cutoff(today: date) -> date:
        month = today.month - REPUBLISH_MONTHS
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _last_published_from_doc(self, doc: Dict[str, Any]) -> Optional[date]:
        return self._coerce_date(doc.get("last_published_date") or doc.get("published_date"))

    def _publication_history(self, doc: Dict[str, Any]) -> List[date]:
        seen: set[date] = set()
        history: List[date] = []
        for value in doc.get("publication_history") or []:
            parsed = self._coerce_date(value)
            if parsed is None or parsed in seen:
                continue
            seen.add(parsed)
            history.append(parsed)
        published = self._last_published_from_doc(doc)
        if published and published not in seen:
            history.append(published)
        return sorted(history)

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    def _sort_word_docs(
        self,
        docs: List[Dict[str, Any]],
        *,
        scheduled: bool,
    ) -> List[Dict[str, Any]]:
        def key(doc: Dict[str, Any]) -> Tuple[str, str, str, str]:
            primary = doc.get("updated_at") if scheduled else doc.get("approved_at")
            return (
                self._sort_value(primary),
                self._sort_value(doc.get("created_at")),
                self._id_sort_value(doc.get("_id")),
                str(doc.get("normalized_word") or self.normalize_word(doc.get("word", ""))),
            )

        return sorted(list(docs or []), key=key)

    @staticmethod
    def _sort_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _id_sort_value(value: Any) -> str:
        text = str(value or "")
        return f"{int(text):020d}" if text.isdigit() else text

    def _to_word(self, doc: Optional[Dict[str, Any]]) -> LegalWord:
        if not doc:
            raise LegalWordServiceError("Word payload missing", 500)
        payload = dict(doc)
        if payload.get("_id") is not None:
            payload["_id"] = str(payload["_id"])
        payload["normalized_word"] = payload.get("normalized_word") or self.normalize_word(
            payload.get("word", "")
        )
        return LegalWord(**payload)

    def _to_daily_set(self, doc: Optional[Dict[str, Any]]) -> LegalWordDailySet:
        if not doc:
            raise LegalWordServiceError("Daily word set payload missing", 500)
        payload = dict(doc)
        if payload.get("_id") is not None:
            payload["_id"] = str(payload["_id"])
        return LegalWordDailySet(**payload)

    def _to_query_id(self, value: str) -> Any:
        try:
            return ObjectId(str(value))
        except Exception:
            return value

    def _user_id(self, current_user: Optional[Any]) -> Optional[str]:
        if current_user is None:
            return None
        value = getattr(current_user, "id", None) or getattr(current_user, "email", None)
        return str(value) if value is not None else None


async def run_legal_word_daily_publish() -> Dict[str, Any]:
    """Scheduler entry point for the daily contractual/legal word publication."""
    publisher = type("SystemPublisher", (), {"id": SYSTEM_PUBLISHER_ID})()
    try:
        response = await LegalWordService().publish_daily_words(current_user=publisher)
        return {
            "published_date": response.published_date.isoformat(),
            "count": response.count,
            "published": response.count == DAILY_WORD_COUNT,
        }
    except LegalWordServiceError as exc:
        logger.warning("Legal word daily publication skipped: %s", exc)
        return {
            "published_date": date.today().isoformat(),
            "count": 0,
            "published": False,
            "error": str(exc),
        }


__all__ = [
    "DAILY_WORD_COUNT",
    "LegalWordService",
    "LegalWordServiceError",
    "PUBLIC_WORD_STATUSES",
    "run_legal_word_daily_publish",
]
