"""G29 against a real FalkorDB: invalidate one document, keep everyone else's.

Mocks cannot close this gap. The whole difficulty is FalkorDB's own MERGE
semantics: letter nodes are merged on ``normCode`` ALONE
(`falkor_graph_service.py:178`), with no ``document_id`` property anywhere on
the node. Two documents that cite the same letter code collapse onto one node,
and `ON MATCH SET` lets whichever wrote last overwrite ``subject``/``code``/
``direction``.

So "delete document A's graph contribution" has no direct expression in the
graph: a `DETACH DELETE` by normCode would take document B's knowledge with it,
across organisations, because the node is global.

Provenance therefore lives in Mongo, keyed by normCode - which is how the
existing letter-deletion cascade already decides safety
(`letter_service.py:726-747`, "still owned by another live record"). This suite
extends that rule with publication state: a graph fact is consumable only while
at least one CONSUMABLE document supports its code.

Run with a local FalkorDB:

    FALKOR_TEST_URL=localhost:6380 pytest backend/rbac_backend/tests/integration/test_graph_invalidation_falkor.py
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_port,
    falkor_unreachable,
)

# Resolved through the shared seam, not from os.environ: under
# CONTRACLAIM_STAGING_GATE the localhost default is refused outright, so a
# staging run cannot silently measure the development FalkorDB.
FALKOR_HOST = falkor_host()
FALKOR_PORT = falkor_port()

pytestmark = pytest.mark.integration


def _client():
    redis = pytest.importorskip("redis")
    try:
        client = redis.Redis(host=FALKOR_HOST, port=FALKOR_PORT, decode_responses=True)
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        falkor_unreachable(exc)
    return client


@pytest.fixture()
def graph():
    """An isolated graph per test, dropped afterwards."""
    client = _client()
    # G-A21 / GRAPH-GATES U9. This suite is REQUIRED evidence for the G29
    # umbrella, and it used to mint its own `g29_*` namespace and tear down with a
    # swallowed `GRAPH.DELETE`. That is exactly the pattern that left
    # `g31_letterdraft_d0fdb2ac7c` on a shared instance: a failed delete was
    # silent, so a crashed run looked like a clean one. It now mints through the
    # sanctioned helper, which names the graph after THIS run, refuses to delete
    # anything it did not create, verifies the deletion against `GRAPH.LIST`, and
    # raises rather than leaving residue. No assertion in this file changed.
    name = disposable_graph_name("invalidation")

    def query(cypher: str, params: Optional[Dict[str, Any]] = None):
        if params:
            prefix = " ".join(
                f"CYPHER {key}={_literal(value)}" for key, value in params.items()
            )
            cypher = f"{prefix} {cypher}"
        return client.execute_command("GRAPH.QUERY", name, cypher)

    yield query

    drop_disposable_graph(client, name)


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "\\'")
    return f"'{escaped}'"


def _codes(result) -> List[str]:
    rows = result[1] if len(result) > 1 else []
    return sorted(row[0] for row in rows if row and row[0])


# --- The MERGE semantics that make blind deletion unsafe -----------------------


def test_two_documents_collapse_onto_one_node(graph) -> None:
    """The premise of G29, demonstrated against the real engine."""
    graph("MERGE (l:Letter {normCode: 'ABC-1'}) SET l.subject = 'From doc A'")
    graph("MERGE (l:Letter {normCode: 'ABC-1'}) SET l.subject = 'From doc B'")

    result = graph("MATCH (l:Letter) RETURN l.normCode")

    assert _codes(result) == ["ABC-1"], "expected a single shared node"

    subject = graph("MATCH (l:Letter {normCode:'ABC-1'}) RETURN l.subject")[1][0][0]
    assert subject == "From doc B", (
        "ON MATCH SET lets the later writer overwrite the earlier document's "
        "contribution, so the node carries no per-document attribution"
    )


def test_the_node_carries_no_document_provenance(graph) -> None:
    """Why attribution cannot come from the graph itself."""
    graph(
        "MERGE (l:Letter {normCode: 'ABC-2'}) "
        "SET l.subject='S', l.organization_id='org-1', l.project_id='proj-1'"
    )

    keys = graph("MATCH (l:Letter {normCode:'ABC-2'}) RETURN keys(l)")[1][0][0]

    assert "document_id" not in keys, (
        "if this ever gains a document_id the provenance rule below should be "
        "revisited - today attribution must come from Mongo"
    )
    assert "organization_id" in keys


def test_blind_deletion_destroys_the_other_documents_knowledge(graph) -> None:
    """The exact failure a naive invalidate-on-block would cause."""
    graph("MERGE (a:Letter {normCode: 'SHARED-1'}) SET a.subject='shared'")
    graph("MERGE (b:Letter {normCode: 'OTHER-1'}) SET b.subject='other'")
    graph(
        "MATCH (a:Letter {normCode:'SHARED-1'}), (b:Letter {normCode:'OTHER-1'}) "
        "MERGE (a)-[:REPLIES_TO]->(b)"
    )

    # Document A is blocked. A DETACH DELETE keyed on normCode alone:
    graph("MATCH (l:Letter {normCode:'SHARED-1'}) DETACH DELETE l")

    remaining = _codes(graph("MATCH (l:Letter) RETURN l.normCode"))
    edges = graph("MATCH ()-[e:REPLIES_TO]->() RETURN count(e)")[1][0][0]

    assert remaining == ["OTHER-1"]
    assert int(edges) == 0, (
        "document B's REPLIES_TO edge was destroyed by document A's "
        "invalidation - this is why deletion must be conditional"
    )


# --- The rule: delete only when no consumable document still supports the code -


def _supporters(mongo_documents: List[Dict[str, Any]], norm_code: str) -> List[Dict]:
    from rbac_backend.services.publication_policy import is_consumable

    return [
        doc
        for doc in mongo_documents
        if doc.get("letterNoNormalized") == norm_code and is_consumable(doc)
    ]


def test_a_shared_node_survives_when_another_document_still_supports_it(
    graph,
) -> None:
    """Test 2: two documents, one blocked. B's knowledge must remain."""
    from rbac_backend.models.processing_state import ProcessingState

    graph("MERGE (l:Letter {normCode: 'SHARED-2'}) SET l.subject='shared'")

    mongo = [
        {
            "_id": "docA",
            "letterNoNormalized": "SHARED-2",
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        },
        {
            "_id": "docB",
            "letterNoNormalized": "SHARED-2",
            "processing_status": ProcessingState.COMPLETED.value,
        },
    ]

    still_supported = _supporters(mongo, "SHARED-2")
    assert still_supported, "document B still supports this code"

    # Because a consumable supporter remains, the node is NOT deleted.
    assert _codes(graph("MATCH (l:Letter) RETURN l.normCode")) == ["SHARED-2"]


def test_the_node_is_deletable_when_its_last_supporter_is_blocked(graph) -> None:
    """Test 3: the only supporter is blocked, so nothing may consume it."""
    from rbac_backend.models.processing_state import ProcessingState

    graph("MERGE (l:Letter {normCode: 'SOLO-1'}) SET l.subject='solo'")

    mongo = [
        {
            "_id": "docA",
            "letterNoNormalized": "SOLO-1",
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        }
    ]

    assert _supporters(mongo, "SOLO-1") == [], "no consumable supporter remains"

    graph("MATCH (l:Letter {normCode:'SOLO-1'}) DETACH DELETE l")
    assert _codes(graph("MATCH (l:Letter) RETURN l.normCode")) == []


def test_a_blocked_supporter_in_another_tenant_does_not_protect_the_node(
    graph,
) -> None:
    """Phase 6: the code is global, so the rule must be about consumability.

    A blocked document cannot be consumed by anyone, so it must not keep a node
    alive on another tenant's behalf either.
    """
    from rbac_backend.models.processing_state import ProcessingState

    mongo = [
        {
            "_id": "org1doc",
            "letterNoNormalized": "X-1",
            "organization_id": "org-1",
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        },
        {
            "_id": "org2doc",
            "letterNoNormalized": "X-1",
            "organization_id": "org-2",
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        },
    ]

    assert _supporters(mongo, "X-1") == []


def test_another_tenants_consumable_document_protects_the_node(graph) -> None:
    """The mirror: org-2's valid knowledge must survive org-1's invalidation."""
    from rbac_backend.models.processing_state import ProcessingState

    graph("MERGE (l:Letter {normCode: 'X-2'}) SET l.subject='cross tenant'")

    mongo = [
        {
            "_id": "org1doc",
            "letterNoNormalized": "X-2",
            "organization_id": "org-1",
            "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        },
        {
            "_id": "org2doc",
            "letterNoNormalized": "X-2",
            "organization_id": "org-2",
            "processing_status": ProcessingState.COMPLETED.value,
        },
    ]

    assert len(_supporters(mongo, "X-2")) == 1
    assert _codes(graph("MATCH (l:Letter) RETURN l.normCode")) == ["X-2"]


# --- Run versioning -------------------------------------------------------------


def test_run1_pass_run2_blocked_run3_pass(graph) -> None:
    """Phase 7: a blocked run must not permanently poison the document."""
    from rbac_backend.models.processing_state import ProcessingState

    graph("MERGE (l:Letter {normCode: 'RUN-1'}) SET l.subject='run 1'")
    doc = {
        "_id": "docA",
        "letterNoNormalized": "RUN-1",
        "processing_status": ProcessingState.COMPLETED.value,
    }
    assert _supporters([doc], "RUN-1")

    doc["processing_status"] = ProcessingState.HUMAN_REVIEW_REQUIRED.value
    assert _supporters([doc], "RUN-1") == []

    doc["processing_status"] = ProcessingState.COMPLETED.value
    assert _supporters([doc], "RUN-1"), "a later clean run must republish"


# --- Idempotency ----------------------------------------------------------------


def test_deleting_an_absent_node_is_safe(graph) -> None:
    graph("MATCH (l:Letter {normCode:'GHOST'}) DETACH DELETE l")
    graph("MATCH (l:Letter {normCode:'GHOST'}) DETACH DELETE l")

    assert _codes(graph("MATCH (l:Letter) RETURN l.normCode")) == []


# --- G32: a kept node still carries the blocked supporter's properties --------


def test_a_kept_shared_node_retains_the_blocked_supporters_properties(graph) -> None:
    """G32, reproduced against the real engine.

    The keep/delete decision is binary, but MERGE ... ON MATCH SET means the
    LAST writer's mutable properties persist regardless of who supports the
    node. So keeping a node because document A still supports it does not
    restore A's values when blocked document B wrote last.

    Note `organization_id` in particular: the node ends up carrying the wrong
    tenant's identifier, because the merge key is global and the property is
    not.
    """
    graph(
        "MERGE (l:Letter {normCode:'PROP-1'}) ON CREATE SET "
        "l.subject='Subject from A', l.organization_id='org-A', "
        "l.direction='incoming'"
    )
    graph(
        "MERGE (l:Letter {normCode:'PROP-1'}) ON MATCH SET "
        "l.subject='Subject from B', l.organization_id='org-B', "
        "l.direction='outgoing'"
    )

    row = graph(
        "MATCH (l:Letter {normCode:'PROP-1'}) "
        "RETURN l.subject, l.organization_id, l.direction"
    )[1][0]

    # Document B is now blocked; document A still supports the code, so the
    # node is correctly KEPT - and still exposes B's values.
    assert row[0] == "Subject from B", (
        "G32: the shared node still exposes the blocked supporter's subject"
    )
    assert row[1] == "org-B", (
        "G32: the shared node carries the wrong tenant's organization_id"
    )
    assert row[2] == "outgoing"


def test_property_ownership_is_not_recoverable_from_the_node_alone(graph) -> None:
    """Why G32 cannot be fixed by inspecting the graph.

    There is no record on the node of which document supplied which property,
    so 'recompute from a remaining consumable supporter' has to read the values
    back out of Mongo. The graph cannot answer it.
    """
    graph("MERGE (l:Letter {normCode:'PROP-2'}) SET l.subject='S', l.date='2026-01-01'")

    keys = graph("MATCH (l:Letter {normCode:'PROP-2'}) RETURN keys(l)")[1][0][0]

    assert not any(
        key in keys for key in ("subject_document_id", "source_document_id", "supporters")
    ), "no per-property provenance exists on the node"
