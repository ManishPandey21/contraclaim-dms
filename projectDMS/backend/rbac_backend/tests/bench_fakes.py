"""In-memory Mongo stand-in for skill-registry and Letter Bench tests.

Supports what those services use: equality, ``$in``, dotted keys, ``$set`` /
``$setOnInsert`` / ``$push`` / ``$inc``, upsert and sort. A ``session`` keyword
is accepted and ignored (no concurrency to serialise). Not a general Mongo
emulator.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from bson import ObjectId


class _Result:
    def __init__(self, matched: int = 0, modified: int = 0, inserted_id: Any = None) -> None:
        self.matched_count = matched
        self.modified_count = modified
        self.inserted_id = inserted_id


def _get(doc: Dict[str, Any], dotted: str) -> Any:
    """Value at a dotted path. A path through an array yields every element's value,
    as Mongo does (``chain.skill_key`` over a list of links)."""
    value: Any = doc
    for part in dotted.split("."):
        if isinstance(value, list):
            value = [item.get(part) for item in value if isinstance(item, dict)]
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _equals(actual: Any, expected: Any) -> bool:
    # An array field matches a scalar when any element does.
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = _get(doc, key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            if "$in" in expected and not any(_equals(actual, option) for option in expected["$in"]):
                return False
            if "$ne" in expected and _equals(actual, expected["$ne"]):
                return False
        elif not _equals(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: Any, direction: int = 1) -> "_Cursor":
        if isinstance(key, list):
            key, direction = key[0]
        self._docs.sort(key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction < 0)
        return self

    def limit(self, count: int) -> "_Cursor":
        self._docs = self._docs[:count]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return [copy.deepcopy(doc) for doc in self._docs[:length]]

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield copy.deepcopy(doc)

        return _gen()


class FakeCollection:
    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.docs: List[Dict[str, Any]] = docs or []

    async def insert_one(self, doc: Dict[str, Any], **_: Any) -> _Result:
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return _Result(inserted_id=stored["_id"])

    async def find_one(self, query: Dict[str, Any], projection: Any = None, sort: Any = None, **_: Any):
        matches = [doc for doc in self.docs if _match(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction < 0)
        return copy.deepcopy(matches[0]) if matches else None

    def find(self, query: Optional[Dict[str, Any]] = None, projection: Any = None, **_: Any) -> _Cursor:
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    @staticmethod
    def _apply(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
        for key, value in update.get("$set", {}).items():
            # A dotted key sets a nested field, as Mongo does.
            target = doc
            *parents, leaf = key.split(".")
            for part in parents:
                if not isinstance(target.get(part), dict):
                    target[part] = {}
                target = target[part]
            target[leaf] = copy.deepcopy(value)
        for operator in ("$push", "$addToSet"):
            for key, value in update.get(operator, {}).items():
                # Dotted paths and {"$each": [...]}, as Mongo applies them.
                target = doc
                *parents, leaf = key.split(".")
                for part in parents:
                    if not isinstance(target.get(part), dict):
                        target[part] = {}
                    target = target[part]
                items = value["$each"] if isinstance(value, dict) and "$each" in value else [value]
                current = target.setdefault(leaf, [])
                for item in items:
                    if operator == "$addToSet" and item in current:
                        continue
                    current.append(copy.deepcopy(item))
        for key, value in update.get("$inc", {}).items():
            doc[key] = (doc.get(key) or 0) + value

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False, **options: Any) -> _Result:
        if options.get("array_filters"):
            # Not modelled: a fake that ignored them would set a literal "$[name]" key and pass.
            raise NotImplementedError("array_filters: test this write against a real MongoDB")
        for doc in self.docs:
            if _match(doc, query):
                self._apply(doc, update)
                return _Result(1, 1)
        if upsert:
            stored = {k: v for k, v in query.items() if not isinstance(v, dict)}
            stored.update(copy.deepcopy(update.get("$setOnInsert", {})))
            self._apply(stored, update)
            stored.setdefault("_id", ObjectId())
            self.docs.append(stored)
            return _Result(0, 1)
        return _Result(0, 0)

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any], **_: Any) -> _Result:
        count = 0
        for doc in self.docs:
            if _match(doc, query):
                self._apply(doc, update)
                count += 1
        return _Result(count, count)

    async def delete_one(self, query: Dict[str, Any]) -> _Result:
        for index, doc in enumerate(self.docs):
            if _match(doc, query):
                del self.docs[index]
                return _Result(1, 1)
        return _Result(0, 0)

    async def delete_many(self, query: Dict[str, Any]) -> _Result:
        before = len(self.docs)
        self.docs[:] = [doc for doc in self.docs if not _match(doc, query)]
        return _Result(before - len(self.docs), before - len(self.docs))

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        return kwargs.get("name", "index")

    async def count_documents(self, query: Dict[str, Any]) -> int:
        return sum(1 for doc in self.docs if _match(doc, query))


class FakeDB:
    def __init__(self) -> None:
        self._collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]
