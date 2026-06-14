from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from bson import ObjectId

from ..core.database import get_database
from ..models.letter import Letter, ConversationTree
from .letter_service import LetterService, LetterServiceError


class ConversationService:
    """Conversation-aware utilities used by the letter router."""

    def __init__(self, letter_service: Optional[LetterService] = None) -> None:
        self._letter_service = letter_service
        self._db = None

    async def _get_db(self):
        if self._db is None:
            self._db = await get_database()
        return self._db

    async def _get_letter_service(self) -> LetterService:
        if self._letter_service is None:
            db = await self._get_db()
            self._letter_service = LetterService(db)
        return self._letter_service

    async def get_conversation_chain(self, letter_id: str, current_user: Any) -> List[Letter]:
        letter_service = await self._get_letter_service()
        letter = await letter_service.get_letter(letter_id)
        if not letter:
            return []

        family = await self._load_conversation_family(letter)
        ordered = sorted(
            family.values(),
            key=lambda item: (
                getattr(item, "depth", 0) if getattr(item, "depth", None) is not None else 0,
                getattr(item, "created_at", None) or getattr(item, "createdAt", None) or datetime.min,
            ),
        )
        return ordered

    async def build_conversation_tree(self, letter_id: Optional[str], current_user: Any) -> ConversationTree:
        if not letter_id:
            raise ValueError("letter_id is required")

        letter_service = await self._get_letter_service()
        letter = await letter_service.get_letter(letter_id)
        if not letter:
            raise LetterServiceError(f"Letter {letter_id} not found")

        family = await self._load_conversation_family(letter)
        root = self._select_tree_root(letter, family)

        children_map: Dict[str, List[Letter]] = defaultdict(list)
        for candidate in family.values():
            parent_id = getattr(candidate, "previous_letter_id", None)
            if parent_id and parent_id in family:
                children_map[parent_id].append(candidate)

        def build_node(node: Letter) -> ConversationTree:
            ordered_children = sorted(
                children_map.get(node.id, []),
                key=lambda item: (
                    getattr(item, "depth", 0) if getattr(item, "depth", None) is not None else 0,
                    getattr(item, "created_at", None) or getattr(item, "createdAt", None) or datetime.min,
                ),
            )
            return ConversationTree(
                letter=node,
                children=[build_node(child) for child in ordered_children],
            )

        return build_node(root)

    async def reparent_letter_atomic(self, letter_id: str, new_parent_id: str, current_user: Any) -> None:
        letter_service = await self._get_letter_service()
        db = await self._get_db()

        letter = await letter_service.get_letter(letter_id)
        parent = await letter_service.get_letter(new_parent_id)

        if not letter or not parent:
            raise LetterServiceError("Letter or parent not found")
        if letter.id == parent.id:
            raise LetterServiceError("Cannot reparent a letter to itself")

        parent_ancestors = [str(ancestor) for ancestor in (parent.ancestors or [])]
        if letter.id in parent_ancestors:
            raise LetterServiceError("Cannot reparent: new parent is a descendant of the letter")

        new_ancestors = parent_ancestors + [parent.id]
        new_depth = len(new_ancestors)

        await db.letters.update_one(
            {"_id": ObjectId(letter.id)},
            {
                "$set": {
                    "previous_letter_id": parent.id,
                    "ancestors": new_ancestors,
                    "depth": new_depth,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        await self._recompute_descendants(letter.id, new_ancestors)

    async def _load_conversation_family(self, seed: Letter) -> Dict[str, Letter]:
        letter_service = await self._get_letter_service()
        db = await self._get_db()

        family: Dict[str, Letter] = {}
        queue: deque[str] = deque()

        def enqueue(letter: Letter) -> None:
            if not letter or not letter.id:
                return
            if letter.id in family:
                return
            family[letter.id] = letter
            queue.append(letter.id)

        enqueue(seed)

        if seed.conversation_id:
            cursor = db.letters.find({"conversation_id": seed.conversation_id})
            for doc in await cursor.to_list(length=None):
                try:
                    enqueue(Letter(**doc))
                except Exception:
                    continue

        processed: Set[str] = set()
        while queue:
            current_id = queue.popleft()
            if current_id in processed:
                continue
            processed.add(current_id)

            letter = family[current_id]
            if letter.previous_letter_id:
                prev = await letter_service.get_letter(letter.previous_letter_id)
                if prev:
                    enqueue(prev)

            cursor = db.letters.find({"previous_letter_id": current_id})
            for doc in await cursor.to_list(length=None):
                try:
                    enqueue(Letter(**doc))
                except Exception:
                    continue

        return family

    @staticmethod
    def _select_tree_root(seed: Letter, family: Dict[str, Letter]) -> Letter:
        if seed.conversation_id and seed.conversation_id in family:
            return family[seed.conversation_id]

        for candidate in family.values():
            parent_id = getattr(candidate, "previous_letter_id", None)
            if not parent_id or parent_id not in family:
                return candidate
        return seed

    async def _recompute_descendants(self, root_id: str, base_ancestors: List[str]) -> None:
        db = await self._get_db()
        cursor = db.letters.find({"ancestors": root_id})
        descendants = await cursor.to_list(length=None)

        for doc in descendants:
            try:
                letter = Letter(**doc)
            except Exception:
                continue

            ancestors = [str(a) for a in (letter.ancestors or [])]
            if root_id in ancestors:
                tail = ancestors[ancestors.index(root_id) + 1 :]
            else:
                tail = []
            new_ancestors = base_ancestors + [root_id] + tail
            new_depth = len(new_ancestors)

            await db.letters.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "ancestors": new_ancestors,
                        "depth": new_depth,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )



