"""Physical Qdrant point ids for generic chunk vectors.

A generic chunk's logical id is ``<document>-<digest>``
(``ingestion.chunk_ids.deterministic_chunk_id``). It names the chunk in
``chunks`` rows and in the point payload, but it is not a valid Qdrant point
id - Qdrant accepts only unsigned integers and UUIDs, and rejects it (400) on
upsert and on delete. The ingestion pipeline, the vector reconciler and
storage-sync's chunk repair therefore write and delete their points under
``generic_chunk_point_id(chunk_id)``: a UUID5 that is the same for the same
logical id, every time, in every process.

Physical and logical ids stay distinct: the payload keeps ``chunk_id``
unchanged, and everything that lists or compares chunks reads that. Only the
writer and the delete pass through this mapping, and they pass the same
function (``VectorClient.upsert`` / ``delete`` ``point_id_for=``). Contract
writers do not use it: their ids are their own.
"""

from __future__ import annotations

import uuid

#: The namespace the correspondence point ids already use
#: (``correspondence_point_id``), with a ``generic:`` prefix so a generic id can
#: never equal a correspondence one.
GENERIC_POINT_ID_NAMESPACE = uuid.NAMESPACE_URL
_PREFIX = "contraclaim:qdrant:generic:"


def generic_chunk_point_id(chunk_id: str) -> str:
    """The deterministic Qdrant point id of a generic chunk's logical id."""
    return str(uuid.uuid5(GENERIC_POINT_ID_NAMESPACE, f"{_PREFIX}{chunk_id}"))
