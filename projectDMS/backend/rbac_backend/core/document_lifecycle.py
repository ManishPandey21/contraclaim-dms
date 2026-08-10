"""Which document lifecycle states stay out of listings and counts.

A soft-deleted document, and one held pending duplicate review, are still rows in
``documents``. Every surface that reports documents to a user has to exclude the
same set, or a count disagrees with the list it claims to summarise -- the
Dashboard reported 59 documents against a Library showing 50 because it applied
no lifecycle predicate at all, while ``document_service`` and ``projects`` each
carried their own copy of the exclusion list.

Consume ``hidden_lifecycle_filter()`` rather than re-spelling the ``$nin``. The
list had already been duplicated once before the third surface was found to be
missing it entirely, which is the failure mode a single definition prevents.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Ordered for readability only; membership is what matters.
HIDDEN_LIFECYCLE_STATES: tuple[str, ...] = ("deleted", "duplicate_review", "duplicate")

LIFECYCLE_FIELD = "lifecycle_state"


def hidden_lifecycle_states() -> List[str]:
    """The excluded states as a list, which is what Mongo operators expect."""
    return list(HIDDEN_LIFECYCLE_STATES)


def hidden_lifecycle_filter() -> Dict[str, Any]:
    """The ``$nin`` predicate excluding every hidden state."""
    return {"$nin": hidden_lifecycle_states()}


def apply_hidden_lifecycle(query: Dict[str, Any]) -> Dict[str, Any]:
    """Add the exclusion to ``query`` unless the caller already set one.

    ``setdefault`` on purpose: a caller that deliberately asks for deleted rows
    (an audit view, a restore flow) must keep that ability.
    """
    query.setdefault(LIFECYCLE_FIELD, hidden_lifecycle_filter())
    return query
