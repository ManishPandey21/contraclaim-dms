"""G28 against a real Qdrant: purge works, and denial holds when it does not.

The point of this suite is the second half. Physical deletion is easy to test
and easy to believe in; the property that actually matters is that a stale point
which survives deletion is still unusable, because containment must not depend
on a remote delete succeeding. A Qdrant outage at exactly the wrong moment must
not reopen the hole.

Run with a local Qdrant:

    QDRANT_TEST_URL=http://localhost:6333 pytest backend/rbac_backend/tests/integration/test_qdrant_containment_live.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

import pytest

# 127.0.0.1, not localhost. On Windows `localhost` resolves to ::1 first and
# the IPv6 hop to a Docker Desktop port binding can stall past a short timeout -
# which is what made this suite skip intermittently while the container was
# healthy and serving the same request in ~50ms.
QDRANT_URL = os.environ.get("QDRANT_TEST_URL", "http://127.0.0.1:6333")

#: Collection creation on a loaded host has been measured at 1.9-2.7s, so the
#: probe timeout is generous enough not to mistake slowness for absence.
_TIMEOUT = int(os.environ.get("QDRANT_TEST_TIMEOUT", "30"))


def _api_key() -> str:
    """Local dev key, from the environment or the gitignored secrets file."""
    key = os.environ.get("QDRANT_API_KEY") or os.environ.get("QDRANT_TEST_API_KEY")
    if key:
        return key
    from pathlib import Path

    for candidate in (
        Path(__file__).resolve().parents[4] / "config" / "secrets" / "qdrant_api_key",
        Path(__file__).resolve().parents[3] / "config" / "secrets" / "qdrant_api_key",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


_HEADERS = {"api-key": _api_key()} if _api_key() else {}

pytestmark = pytest.mark.integration


def _client():
    requests = pytest.importorskip("requests")
    try:
        response = requests.get(
            f"{QDRANT_URL}/collections", timeout=_TIMEOUT, headers=_HEADERS
        )
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Qdrant not reachable at {QDRANT_URL}: {exc}")
    return requests


@pytest.fixture()
def collection():
    requests = _client()
    name = f"g28_{uuid.uuid4().hex[:10]}"
    requests.put(
        f"{QDRANT_URL}/collections/{name}",
        json={"vectors": {"size": 4, "distance": "Cosine"}},
        timeout=_TIMEOUT,
        headers=_HEADERS,
    ).raise_for_status()

    yield name, requests

    try:
        requests.delete(f"{QDRANT_URL}/collections/{name}", timeout=_TIMEOUT, headers=_HEADERS)
    except Exception:
        pass


def _upsert(requests, name: str, points: List[Dict[str, Any]]) -> None:
    requests.put(
        f"{QDRANT_URL}/collections/{name}/points?wait=true",
        json={"points": points},
        timeout=_TIMEOUT,
        headers=_HEADERS,
    ).raise_for_status()


def _search_document_ids(requests, name: str) -> List[str]:
    response = requests.post(
        f"{QDRANT_URL}/collections/{name}/points/search",
        json={"vector": [0.1, 0.1, 0.1, 0.1], "limit": 10, "with_payload": True},
        timeout=_TIMEOUT,
        headers=_HEADERS,
    )
    response.raise_for_status()
    return [
        hit["payload"]["document_id"] for hit in response.json()["result"]
    ]


def test_a_published_document_is_retrievable(collection) -> None:
    name, requests = collection
    _upsert(
        requests,
        name,
        [{"id": 1, "vector": [0.1, 0.1, 0.1, 0.1], "payload": {"document_id": "docA"}}],
    )

    assert _search_document_ids(requests, name) == ["docA"]


def test_purging_by_document_id_removes_only_that_document(collection) -> None:
    """Physical cleanup, and it must not take the peer document with it."""
    name, requests = collection
    _upsert(
        requests,
        name,
        [
            {"id": 1, "vector": [0.1, 0.1, 0.1, 0.1], "payload": {"document_id": "docA"}},
            {"id": 2, "vector": [0.1, 0.1, 0.1, 0.2], "payload": {"document_id": "docB"}},
        ],
    )

    requests.post(
        f"{QDRANT_URL}/collections/{name}/points/delete?wait=true",
        json={
            "filter": {
                "must": [{"key": "document_id", "match": {"value": "docA"}}]
            }
        },
        timeout=_TIMEOUT,
        headers=_HEADERS,
    ).raise_for_status()

    assert _search_document_ids(requests, name) == ["docB"]


def test_a_surviving_stale_point_is_still_denied(collection) -> None:
    """The defence-in-depth property, proved with a real surviving point.

    The purge is deliberately NOT run here - this simulates it failing or not
    having happened yet. The document is blocked, so the eligibility filter must
    refuse the hit regardless of the vector still being there.
    """
    from rbac_backend.models.processing_state import ProcessingState
    from rbac_backend.services.publication_policy import is_consumable

    name, requests = collection
    _upsert(
        requests,
        name,
        [{"id": 1, "vector": [0.1, 0.1, 0.1, 0.1], "payload": {"document_id": "docA"}}],
    )

    # The point is genuinely still in Qdrant.
    assert _search_document_ids(requests, name) == ["docA"]

    blocked = {
        "_id": "docA",
        "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
    }
    hits = _search_document_ids(requests, name)
    usable = [doc_id for doc_id in hits if is_consumable(blocked)]

    assert usable == [], (
        "a stale vector survived the purge and the eligibility filter did not "
        "refuse it - containment would depend on the delete succeeding"
    )


def test_a_clean_document_is_not_denied(collection) -> None:
    from rbac_backend.models.processing_state import ProcessingState
    from rbac_backend.services.publication_policy import is_consumable

    name, requests = collection
    _upsert(
        requests,
        name,
        [{"id": 1, "vector": [0.1, 0.1, 0.1, 0.1], "payload": {"document_id": "docA"}}],
    )

    clean = {"_id": "docA", "processing_status": ProcessingState.COMPLETED.value}
    usable = [d for d in _search_document_ids(requests, name) if is_consumable(clean)]

    assert usable == ["docA"]


def test_purging_an_absent_document_is_idempotent(collection) -> None:
    name, requests = collection

    for _ in range(2):
        response = requests.post(
            f"{QDRANT_URL}/collections/{name}/points/delete?wait=true",
            json={
                "filter": {
                    "must": [{"key": "document_id", "match": {"value": "ghost"}}]
                }
            },
            timeout=_TIMEOUT,
            headers=_HEADERS,
        )
        response.raise_for_status()
