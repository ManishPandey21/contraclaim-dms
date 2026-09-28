#!/usr/bin/env python3
"""Read-only census of the correspondence Qdrant collection (DI-B1 backfill).

Before DI-B1 was fixed, correspondence vectors were written in LangChain's
``{page_content, metadata: {...}}`` envelope. Tenant-scoped retrieval filters
the flat ``org_id``/``project_id`` fields, so those points exist but are never
returned. This census classifies every point by writer contract (see
``classify_vector_payload``) and reports, per organisation, how many legacy
points and distinct legacy documents need a reindex.

It only scrolls: no upsert, no delete, no payload write. Run it inside the
backend container (the host interpreter lacks the app's dependencies):

    python scripts/correspondence_vector_census.py [--collection document_vectors]
        [--batch-size 256] [--list-document-ids]

``QDRANT_URL`` / ``QDRANT_API_KEY`` come from the environment, as for the app.
There is deliberately no resume: totals from a resumed scroll would describe
part of the collection while reading like all of it. Re-run from the start.

Exit status: 0 when no legacy or unscoped point remains, 3 when any does (the
backfill is not finished), 2 on a configuration or connection error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from rbac_backend.retrieval.correspondence_payload import (  # noqa: E402
    POINT_LEGACY_ENVELOPE,
    POINT_UNSCOPED,
    classify_vector_payload,
)


def _org_of(payload: Dict[str, Any]) -> str:
    nested = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(
        payload.get("org_id")
        or payload.get("organization_id")
        or nested.get("organization_id")
        or nested.get("org_id")
        or "<none>"
    )


def _document_of(payload: Dict[str, Any]) -> Optional[str]:
    nested = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    value = payload.get("document_id") or nested.get("document_id")
    return str(value) if value else None


def census(client: Any, collection: str, batch_size: int) -> Dict[str, Any]:
    offset: Any = None
    totals: Counter = Counter()
    by_org: Dict[str, Counter] = defaultdict(Counter)
    legacy_documents: Dict[str, Set[str]] = defaultdict(set)
    advisory_points = 0
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = dict(point.payload or {})
            kind = classify_vector_payload(payload)
            org = _org_of(payload)
            totals[kind] += 1
            by_org[org][kind] += 1
            if kind in (POINT_LEGACY_ENVELOPE, POINT_UNSCOPED):
                document_id = _document_of(payload)
                if document_id:
                    legacy_documents[org].add(document_id)
                nested = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if nested.get("key_reply_points") or payload.get("key_reply_points"):
                    advisory_points += 1
        if offset is None:
            break
    return {
        "collection": collection,
        "totals": dict(totals),
        "by_org": {org: dict(counts) for org, counts in sorted(by_org.items())},
        "legacy_document_counts": {org: len(ids) for org, ids in sorted(legacy_documents.items())},
        "legacy_points_with_reply_advice": advisory_points,
        "_legacy_document_ids": {org: sorted(ids) for org, ids in legacy_documents.items()},
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", "document_vectors"))
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--list-document-ids", action="store_true")
    args = parser.parse_args(argv)
    url = os.getenv("QDRANT_URL")
    if not url:
        print("QDRANT_URL is not set", file=sys.stderr)
        return 2
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None, timeout=60)
        report = census(client, args.collection, max(1, args.batch_size))
    except Exception as exc:
        print(f"census failed: {exc}", file=sys.stderr)
        return 2
    ids = report.pop("_legacy_document_ids")
    if args.list_document_ids:
        report["legacy_document_ids"] = ids
    print(json.dumps(report, indent=2, sort_keys=True))
    remaining = report["totals"].get(POINT_LEGACY_ENVELOPE, 0) + report["totals"].get(POINT_UNSCOPED, 0)
    return 3 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
