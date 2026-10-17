"""Turn raw Mongo values into JSON-safe presentation values.

A Mongo row read by Motor can carry BSON types - ``ObjectId`` above all, since
``documents._id`` is ObjectId-keyed - that neither Pydantic nor ``json`` can
serialize. Embedding such a row in a response model validates happily (a
``Dict[str, Any]`` field accepts anything) and then fails at response
serialization, *after* the handler returned and any transaction committed. The
caller sees a 500 for a write that succeeded.

:func:`present_bson` is total by construction: every input maps to something
JSON-safe, and nothing it is given is mutated - containers are rebuilt, never
edited in place, so the canonical row a caller still holds keeps its real types.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from bson import ObjectId
from bson.decimal128 import Decimal128


def present_bson(value: Any) -> Any:
    """Return a JSON-safe copy of ``value``.

    * ``ObjectId`` / ``UUID`` -> their canonical string form;
    * ``Decimal128`` / ``Decimal`` -> exact decimal string (never a lossy float);
    * binary payloads (``bytes``, ``bson.Binary``) -> ``None``: raw bytes are
      never part of a presentation;
    * ``datetime`` / ``date`` are kept - Pydantic serializes them natively;
    * mappings and sequences are rebuilt recursively (mapping keys become str);
    * anything else unrecognised -> ``str(value)``, so an unexpected BSON type
      degrades to text instead of failing the response.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, (ObjectId, uuid.UUID)):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if isinstance(value, Enum):
        return present_bson(value.value)
    if isinstance(value, Mapping):
        return {str(key): present_bson(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [present_bson(item) for item in value]
    return str(value)
