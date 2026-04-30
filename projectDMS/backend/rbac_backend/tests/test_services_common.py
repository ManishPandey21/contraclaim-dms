import asyncio
import pytest

from rbac_backend.services.common import (
    PaginationError,
    fetch_paginated,
    validate_pagination,
)


class StubCursor:
    def __init__(self, items):
        self._items = list(items)
        self._skip = 0
        self._limit = None

    def sort(self, _sort):  # pragma: no cover - behaviour not exercised in tests
        return self

    def skip(self, skip):
        self._skip = skip
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def __aiter__(self):
        self._index = self._skip
        self._end = (
            len(self._items)
            if self._limit is None
            else min(len(self._items), self._skip + self._limit)
        )
        return self

    async def __anext__(self):
        if self._index >= self._end:
            raise StopAsyncIteration
        value = self._items[self._index]
        self._index += 1
        return value


class StubCollection:
    def __init__(self, items):
        self._items = list(items)
        self.last_filter = None

    def find(self, filter=None, projection=None):
        self.last_filter = filter or {}
        return StubCursor(self._items)

    async def count_documents(self, filter):
        # ignore filter for simplicity
        return len(self._items)


def test_validate_pagination_normalises_values():
    skip, limit = validate_pagination(5, 25)
    assert skip == 5
    assert limit == 25


@pytest.mark.parametrize(
    "skip, limit",
    [(-1, 10), (0, 0), (0, 1001), ("x", 10), (0, "y")],
)
def test_validate_pagination_rejects_invalid_values(skip, limit):
    with pytest.raises(PaginationError):
        validate_pagination(skip, limit)


def test_fetch_paginated_returns_items_and_metadata():
    collection = StubCollection([{"_id": 1}, {"_id": 2}, {"_id": 3}])
    items, pagination = asyncio.run(
        fetch_paginated(
        collection,
        filter={"foo": "bar"},
        skip=1,
        limit=1,
        count_total=True,
        )
    )

    assert items == [{"_id": 2}]
    assert pagination.skip == 1
    assert pagination.limit == 1
    assert pagination.total == 3
    assert collection.last_filter == {"foo": "bar"}
