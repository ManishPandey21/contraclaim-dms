"""The vector namespaces a request may select.

The generic chunk writers take an optional namespace from their request: the
ingestion pipeline from ``IngestionOptions.vector_namespace`` (request body of
``POST /ingestion/jobs``), the vector reconciler from the superadmin
``POST /admin/vector/reconcile``. ``VectorClient.upsert`` creates a missing
collection, so an unchecked name let a caller create and fill any Qdrant
collection - Contract Master's internal ones included.

No product caller sets a namespace: the client, the services and the scripts
all leave it out. So the configured default collection is the only selectable
namespace, omitted or named exactly. Contract Master's namespaces
(``contract_clauses``, and ``document_vectors`` where it is not the configured
default) are written by their own writers and are never selectable here.
"""

from __future__ import annotations

from typing import Optional


class UnsupportedVectorNamespace(ValueError):
    """A request named a vector namespace it may not select."""


def selectable_vector_namespace(
    requested: Optional[str], default_collection: Optional[str]
) -> Optional[str]:
    """``None`` (the default) for an omitted namespace or the default named
    exactly; anything else raises ``UnsupportedVectorNamespace``.

    Exact match only - no trimming or case folding - so a near-name cannot
    reach a different collection. Without a known default, only omission is
    selectable.
    """
    if requested is None:
        return None
    if default_collection and requested == default_collection:
        return None
    raise UnsupportedVectorNamespace(
        "vector namespace is not selectable; omit it to use the default collection"
    )
