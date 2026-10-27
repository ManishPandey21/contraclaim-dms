from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set

from bson import ObjectId

from ..core.database import get_database
from ..models.letter import Letter, ConversationTree
from .letter_service import LetterService, LetterServiceError
from ..utils.error_handler import LetterError

#: One answer for every reason a reparent's dependent letters block it, so the
#: response never reveals whether a letter the caller cannot see exists.
DEPENDENT_LETTER_BLOCKS_MOVE = "A dependent letter blocks this move"


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

    async def get_authorized_conversation_chain(
        self, letter_id: str, current_user: Any
    ) -> List[Letter]:
        """The conversation chain, bounded by the ACTOR's canonical row visibility.

        `get_conversation_chain` accepts `current_user` and never reads it, and
        the family it assembles is built from association alone - a shared
        `conversation_id` or a `previous_letter_id` edge. Association is not
        authority: a reply chain may cross projects and organisations, and every
        member's `content` and `subject` flow into whatever consumes the chain.

        This is a SEPARATE method rather than a change to the generic one,
        which has other callers (the letters router, the LangGraph letter
        pipeline and the drafting context builder) whose semantics must not
        change implicitly.

        The boundary is the canonical `build_scope_query`, asked of Mongo so the
        one row-visibility primitive decides. The ANCHOR letter's own
        organisation and project narrow INSIDE that entitlement - a value
        outside it denies every row - and are never the authority source. No
        role name is read here.
        """
        ordered = await self.get_conversation_chain(letter_id, current_user)
        if not ordered or current_user is None:
            return []

        # Private to `core.security` by name only; it is the canonical id
        # expansion that `build_scope_query` itself uses, and reimplementing it
        # here would let the two drift.
        from ..core.security import build_scope_query, _expand_object_ids

        anchor = next(
            (letter for letter in ordered if str(letter.id) == str(letter_id)), None
        )
        organization_id = (
            str(anchor.organization_id)
            if anchor is not None and anchor.organization_id
            else None
        )
        project_id = (
            str(anchor.project_id)
            if anchor is not None and anchor.project_id
            else None
        )

        scope = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
        )

        identifiers = [str(letter.id) for letter in ordered if letter.id]
        if not identifiers:
            return []

        id_clause: Dict[str, Any] = {"_id": {"$in": _expand_object_ids(identifiers)}}
        query = {"$and": [id_clause, scope]} if scope else id_clause

        db = await self._get_db()
        rows = await db.letters.find(query, {"_id": 1}).to_list(length=None)
        allowed = {str(row.get("_id")) for row in rows}

        return [letter for letter in ordered if str(letter.id) in allowed]

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

    async def build_authorized_conversation_tree(
        self,
        letter_id: str,
        current_user: Any,
        include: Optional[Callable[[Letter], Awaitable[bool]]] = None,
    ) -> Optional[ConversationTree]:
        """The conversation tree, bounded by the ACTOR's canonical row visibility.

        Same boundary as `get_authorized_conversation_chain`: every letter is read
        through `build_scope_query`, narrowed to the ANCHOR letter's organisation
        and project. Association (`conversation_id`, `previous_letter_id`,
        `ancestors`) only nominates candidates; a letter outside that scope is
        never loaded. A hidden letter is pruned and its visible descendants are
        re-attached to their nearest visible ancestor.

        Returns None when the anchor itself is not visible to the actor. The
        caller authorises the anchor separately (`check_letter_access`).
        `include`, when given, applies the per-letter rules row scope cannot
        express (drafter assignment) to every other member.
        """
        if current_user is None:
            return None
        letter_service = await self._get_letter_service()
        seed = await letter_service.get_letter(letter_id)
        if not seed or not seed.id:
            return None

        from ..core.security import build_scope_query, _expand_object_ids

        scope = build_scope_query(
            current_user,
            organization_id=str(seed.organization_id) if seed.organization_id else None,
            project_id=str(seed.project_id) if seed.project_id else None,
        )
        db = await self._get_db()

        async def visible(query: Dict[str, Any]) -> List[Letter]:
            bounded = {"$and": [query, scope]} if scope else query
            letters: List[Letter] = []
            for row in await db.letters.find(bounded).to_list(length=None):
                try:
                    letters.append(Letter(**letter_service._augment_letter_document(row)))
                except Exception:
                    continue
            return letters

        family: Dict[str, Letter] = {}
        queue: deque[str] = deque()

        def enqueue(letter: Letter) -> None:
            if letter.id and letter.id not in family:
                family[letter.id] = letter
                queue.append(letter.id)

        for letter in await visible({"_id": {"$in": _expand_object_ids([seed.id])}}):
            enqueue(letter)
        if seed.id not in family:
            return None
        if seed.conversation_id:
            for letter in await visible({"conversation_id": seed.conversation_id}):
                enqueue(letter)

        while queue:
            current = family[queue.popleft()]
            related = [str(value) for value in [current.previous_letter_id, *(current.ancestors or [])] if value]
            if related:
                for letter in await visible({"_id": {"$in": _expand_object_ids(related)}}):
                    enqueue(letter)
            for letter in await visible({"previous_letter_id": current.id}):
                enqueue(letter)

        if include is not None:
            # Per-letter rules beyond row scope (for example drafter assignment).
            family = {key: member for key, member in family.items() if key == seed.id or await include(member)}
        family = await self.with_visible_threading(family)
        return self._pruned_tree(family[seed.id], family)

    async def with_visible_threading(self, letters: Dict[str, Letter]) -> Dict[str, Letter]:
        """Copies of ``letters`` whose threading fields name only letters in ``letters``.

        `previous_letter_id` becomes the nearest returned ancestor (or None),
        `ancestors` keeps only returned letters, and a `conversation_id` that is
        the id of a letter not returned is cleared. An opaque conversation key
        that names no letter is kept.
        """
        from ..core.security import _expand_object_ids

        visible = set(letters)
        unseen = {
            str(letter.conversation_id)
            for letter in letters.values()
            if letter.conversation_id and str(letter.conversation_id) not in visible
        }
        hidden_conversations: Set[str] = set()
        if unseen:
            db = await self._get_db()
            rows = await db.letters.find({"_id": {"$in": _expand_object_ids(sorted(unseen))}}, {"_id": 1}).to_list(length=None)
            hidden_conversations = {str(row.get("_id")) for row in rows}

        redacted: Dict[str, Letter] = {}
        for key, letter in letters.items():
            candidates = [letter.previous_letter_id, *reversed(letter.ancestors or [])]
            parent = next(
                (str(candidate) for candidate in candidates if candidate and str(candidate) in visible and str(candidate) != key),
                None,
            )
            update: Dict[str, Any] = {
                "previous_letter_id": parent,
                "ancestors": [str(ancestor) for ancestor in (letter.ancestors or []) if str(ancestor) in visible],
            }
            if letter.conversation_id and str(letter.conversation_id) in hidden_conversations:
                update["conversation_id"] = None
            redacted[key] = letter.model_copy(update=update)
        return redacted

    @staticmethod
    def _pruned_tree(seed: Letter, family: Dict[str, Letter]) -> ConversationTree:
        def visible_parent(letter: Letter) -> Optional[str]:
            candidates = [letter.previous_letter_id, *reversed(letter.ancestors or [])]
            for candidate in candidates:
                if candidate and str(candidate) in family and str(candidate) != letter.id:
                    return str(candidate)
            return None

        parent_of = {letter_id: visible_parent(letter) for letter_id, letter in family.items()}

        root_id = seed.id
        seen: Set[str] = {root_id}
        while parent_of.get(root_id) and parent_of[root_id] not in seen:
            root_id = parent_of[root_id]
            seen.add(root_id)

        children_map: Dict[str, List[Letter]] = defaultdict(list)
        for letter_id, parent_id in parent_of.items():
            if parent_id:
                children_map[parent_id].append(family[letter_id])

        built: Set[str] = set()

        def build_node(node: Letter) -> ConversationTree:
            built.add(node.id)
            ordered_children = sorted(
                (child for child in children_map.get(node.id, []) if child.id not in built),
                key=lambda item: (
                    getattr(item, "depth", 0) if getattr(item, "depth", None) is not None else 0,
                    getattr(item, "created_at", None) or getattr(item, "createdAt", None) or datetime.min,
                ),
            )
            return ConversationTree(letter=node, children=[build_node(child) for child in ordered_children])

        return build_node(family[root_id])

    async def get_descendants(self, letter_id: str) -> List[Letter]:
        """Every letter whose stored ancestry includes ``letter_id`` (what a reparent rewrites)."""
        from ..core.security import _expand_object_ids

        letter_service = await self._get_letter_service()
        db = await self._get_db()
        descendants: List[Letter] = []
        query = {"ancestors": {"$in": _expand_object_ids([str(letter_id)])}}
        for row in await db.letters.find(query).to_list(length=None):
            try:
                descendants.append(Letter(**letter_service._augment_letter_document(row)))
            except Exception as exc:
                # A descendant that cannot be read can be neither authorised nor
                # rewritten; skipping it would leave its threading stale.
                raise LetterError(DEPENDENT_LETTER_BLOCKS_MOVE, 409) from exc
        return descendants

    async def reparent_letter_atomic(
        self,
        letter_id: str,
        new_parent_id: str,
        current_user: Any,
        descendant_ids: Optional[List[str]] = None,
    ) -> None:
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

        await self._recompute_descendants(letter.id, new_ancestors, descendant_ids=descendant_ids)

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

    async def _recompute_descendants(
        self, root_id: str, base_ancestors: List[str], descendant_ids: Optional[List[str]] = None
    ) -> None:
        from ..core.security import _expand_object_ids

        db = await self._get_db()
        # Ancestry may be stored as strings or ObjectIds. When the caller
        # authorised a descendant set, rewrite exactly that set.
        query: Dict[str, Any] = {"ancestors": {"$in": _expand_object_ids([root_id])}}
        if descendant_ids is not None:
            query["_id"] = {"$in": _expand_object_ids(descendant_ids)}
        cursor = db.letters.find(query)
        descendants = await cursor.to_list(length=None)

        for doc in descendants:
            # Read the stored ancestry directly: parsing the raw row as `Letter`
            # fails on its ObjectId `_id`, which silently skipped every descendant.
            ancestors = [str(a) for a in (doc.get("ancestors") or [])]
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
