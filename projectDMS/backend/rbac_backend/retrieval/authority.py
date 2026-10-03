"""The line between the retrieval AUTHORITY filter and the caller's USER filter.

The authority filter - organisation and project - comes only from the scope the
route authorised (`SearchFilters.org_id` / `project_id`, checked by
`PolicyService`). `SearchFilters.metadata` is caller-controlled narrowing and must
never be able to replace or widen it.

Every store builds its predicate by merging the two, and a merge is where the
override happened: `{"org_id": authorised, **metadata}` lets `metadata.org_id`
win, `project_id: None` drops the project condition (the vector filter builders
skip `None`), and a list value turns an equality into `MatchAny`. So the user
side is stripped of every authority-owned key BEFORE any merge, and callers put
the authority fields last as well. Either defence alone would close the
override; together a future merge written in the wrong order still cannot
reopen it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: Field names that carry tenancy in any retrieval store. `org_id`/`project_id`
#: are the vector payload and `chunks` fields; `organization_id` is the Mongo
#: document/`document_vectors` field; `tenant_id`/`workspace_id` are listed so an
#: alias of the same identity cannot be introduced as "just metadata".
AUTHORITY_FILTER_KEYS = frozenset(
    {"org_id", "organization_id", "project_id", "tenant_id", "workspace_id"}
)


def is_authority_key(key: Any) -> bool:
    """True for a key the caller may not filter on.

    Matched on the normalised LEAF of a dotted path, so `metadata.org_id`,
    `payload.project_id`, `ORG_ID` and `org-id` are all refused. Mongo operator
    keys (`$or`, `$where`, ...) are refused too: metadata is a map of field
    conditions, and a top-level operator can carry an authority field inside it.
    """
    normalised = str(key).strip().lower().replace("-", "_")
    if normalised.startswith("$"):
        return True
    return normalised.rsplit(".", 1)[-1] in AUTHORITY_FILTER_KEYS


def user_metadata_filters(metadata: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The caller's metadata with every authority-owned key removed.

    Dropped rather than rejected: a client that echoes its own org id in
    metadata keeps working, and a foreign one has no effect because the
    authority filter is applied separately. The drop is logged (keys only, never
    values) so a client probing for other tenants is visible.
    """
    if not metadata:
        return {}
    kept: Dict[str, Any] = {}
    dropped = []
    for key, value in metadata.items():
        if is_authority_key(key):
            dropped.append(str(key))
            continue
        kept[key] = value
    if dropped:
        logger.warning(
            "Dropped authority-owned keys from caller retrieval metadata: %s",
            sorted(dropped),
        )
    return kept
