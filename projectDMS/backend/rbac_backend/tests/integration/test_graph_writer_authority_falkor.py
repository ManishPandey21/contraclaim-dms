"""G31 against a real FalkorDB: sync must not (re-)write blocked support.

`GraphIngestionService.sync_document_to_falkor` had no authority axis at all -
`grep` for `is_consumable|publication_policy|resolve_document_authority` in
`graph/graph_ingestion_service.py` returned nothing. So containment could remove
a blocked document's graph contribution and the very next sync would re-MERGE
it: the physical purge was undone by the writer.

The document dict is already in hand at the call site, so authority resolves
synchronously through the certified predicate - no second policy, no async hack.

Run with a local FalkorDB:
    FALKOR_TEST_HOST=localhost FALKOR_TEST_PORT=6380 \
      pytest backend/rbac_backend/tests/integration/test_graph_writer_authority_falkor.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.authority_band_graph import (  # noqa: E402
    disposable_graph_name,
    drop_disposable_graph,
)

FALKOR_HOST = os.environ.get("FALKOR_TEST_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKOR_TEST_PORT", "6380"))

pytestmark = pytest.mark.integration


def _falkor(graph_name: str):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    service = FalkorGraphService(
        FalkorGraphConfig(
            host=FALKOR_HOST, port=FALKOR_PORT, graph_name=graph_name,
            password=None, enabled=True, cleanup=False,
        )
    )
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {FALKOR_HOST}:{FALKOR_PORT}: {exc}")
    return service


@pytest.fixture()
def ingestion():
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService

    name = disposable_graph_name("writer")
    falkor = _falkor(name)
    yield GraphIngestionService(falkor=falkor), falkor
    # Teardown failure is raised, not swallowed: a shared instance that keeps
    # a graph nobody is tracking is how residue accumulated before.
    drop_disposable_graph(falkor._get_client(), name)


def _document(status: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    doc = {
        "_id": "doc-g31",
        "letterNo": "LTR-G31",
        "subject": "Delay notice",
        "organization_id": "org-A",
        "project_id": "proj-A",
    }
    if status is not None:
        doc["processing_status"] = status
    doc.update(extra)
    return doc


def _letter_count(falkor, code: str = "LTR-G31") -> int:
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    result = falkor._execute(
        "MATCH (l:Letter {normCode: $n}) RETURN count(l)",
        {"n": normalize_letter_code(code)},
    )
    rows = result[1] if len(result) > 1 else []
    if not rows:
        return 0
    return int(rows[0][0][1])


def _sync(ingestion, document) -> None:
    service, _ = ingestion
    service.sync_document_to_falkor(str(document["_id"]), document, None, "letter")


# --- a clean document still syncs ---------------------------------------------


def test_a_clean_document_creates_its_letter_node(ingestion) -> None:
    _, falkor = ingestion
    _sync(ingestion, _document("metadata_extracted"))

    assert _letter_count(falkor) == 1


def test_an_operationally_failed_document_still_syncs(ingestion) -> None:
    """Model B: a worker crash is not a verdict on the content."""
    _, falkor = ingestion
    _sync(ingestion, _document("failed"))

    assert _letter_count(falkor) == 1


# --- the defect: blocked content must not be written or re-MERGEd -------------


def test_a_blocked_document_is_not_written_to_the_graph(ingestion) -> None:
    _, falkor = ingestion
    _sync(ingestion, _document("human_review_required"))

    assert _letter_count(falkor) == 0, (
        "graph sync wrote support for a document under an adverse quality verdict"
    )


def test_a_quarantined_document_is_not_written_to_the_graph(ingestion) -> None:
    _, falkor = ingestion
    _sync(ingestion, _document("metadata_extracted", duplicate_status="duplicate"))

    assert _letter_count(falkor) == 0


def test_resync_after_blocking_does_not_reintroduce_support(ingestion) -> None:
    """The G31 core: containment must not be undone by the next sync."""
    _, falkor = ingestion
    document = _document("metadata_extracted")
    _sync(ingestion, document)
    assert _letter_count(falkor) == 1

    # Containment removes the contribution.
    from rbac_backend.services.falkor_graph_service import normalize_letter_code
    falkor._execute(
        "MATCH (l:Letter {normCode: $n}) DETACH DELETE l",
        {"n": normalize_letter_code("LTR-G31")},
    )
    assert _letter_count(falkor) == 0

    # The document is now blocked, and sync runs again.
    document["processing_status"] = "human_review_required"
    _sync(ingestion, document)

    assert _letter_count(falkor) == 0, (
        "a later sync re-MERGEd the blocked document's support, undoing containment"
    )
