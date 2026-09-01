"""G32-STATE against a real FalkorDB: migrate legacy shared-Letter state safely.

G32-CODE fixed the writer and the readers. It cleaned nothing: the measured
deployed corpus is 215 shared `(:Letter)` nodes still carrying document- and
tenant-owned properties, and 307 of 310 `CITES` edges with no
`owner_document_id` - permanently unretractable, because retraction matches on
ownership.

This suite rehearses the migration over that shape against the real engine, on a
run-owned disposable graph. A fake cannot carry any of what it proves:

* **dry-run purity** is a statement about what the ENGINE observed, not about
  what the code intended;
* **idempotency** is a statement about `SET`/`REMOVE`/`DELETE` convergence in
  FalkorDB;
* **interruption recovery** is a statement about restarting against real partial
  state;
* **peer-owner isolation** is engine MERGE semantics on a globally shared node -
  the whole reason G32 exists.

The seed is built to look like the measured corpus rather than like a happy
path: a legacy node with every forbidden property, an already-clean node,
already-migrated valid ownership, an edge whose owner is uniquely derivable, an
ambiguous one, a malformed one, an empty-string one, a `source`-tag-only one,
peer A/B ownership on one shared key, a duplicate, and unrelated nodes.

**This proves readiness, not migration.** The graph here is disposable and by
construction contains none of the contaminated production nodes; GRAPH-GATES
field 12 names a green run against a fresh graph as invalid closure evidence for
G32. Nothing here certifies G32, G32-STATE, G31, G30 or G29.

Run with a local FalkorDB:

    G32_STATE_REHEARSAL=1 FALKOR_TEST_HOST=127.0.0.1 FALKOR_TEST_PORT=6390 \
      pytest backend/rbac_backend/tests/integration/test_graph_letter_state_migration_falkor.py
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from rbac_backend.services import graph_letter_state_migration as migration
from rbac_backend.services import graph_letter_state_validation as validation
from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_port,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def _redis_client():
    redis = pytest.importorskip("redis")
    client = redis.Redis(
        host=falkor_host(), port=falkor_port(), decode_responses=True, socket_timeout=5
    )
    try:
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {falkor_host()}:{falkor_port()}: {exc}")
    return client


# ---------------------------------------------------------------------------
# the canonical Mongo snapshot this rehearsal resolves ownership against
# ---------------------------------------------------------------------------
#
# Ownership evidence is EXACTLY what `GraphIngestionService._build_falkor_payload`
# derives: the document's own letter code is the edge source, its `references`
# are CITES/REPLIES_TO targets, and `previous_letter_id` is a REPLIES_TO target.
# Two documents share `L-PEER`, and two more share `L-AMBIG`, because a
# `(:Letter)` node is MERGEd on `normCode` alone and is therefore shared across
# documents and tenants - that collision is the normal case, not a pathology.

CANONICAL_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "_id": "docLegacy",
        "letterNo": "L-LEGACY",
        "references": [{"letterNo": "L-TARGET-A"}],
        "previous_letter_id": "L-TARGET-B",
    },
    {"_id": "docClean", "letterNo": "L-CLEAN", "references": [{"letterNo": "L-TARGET-B"}]},
    {"_id": "docPeerA", "letterNo": "L-PEER", "references": [{"letterNo": "L-TARGET-A"}]},
    {"_id": "docPeerB", "letterNo": "L-PEER", "references": [{"letterNo": "L-TARGET-A"}]},
    {"_id": "docAmbig1", "letterNo": "L-AMBIG", "references": [{"letterNo": "L-TARGET-A"}]},
    {"_id": "docAmbig2", "letterNo": "L-AMBIG", "references": [{"letterNo": "L-TARGET-A"}]},
    # Exists, but derives nothing. Its id appears only as an edge `source` tag,
    # which must never become ownership.
    {"_id": "docSourceTag", "letterNo": "L-OTHER", "references": []},
]

GHOST_OWNER = "doc-that-does-not-exist"

#: Every forbidden property the migration doc measured on the deployed corpus.
LEGACY_NODE_PROPERTIES = {
    "code": "L-LEGACY",
    "direction": "outgoing",
    "subject": "Extension of time - notice of delay",
    "date": "2026-02-11",
    "project": "KNPCC-11",
    "organization_id": "org-A",
    "project_id": "proj-A",
}


def _seed(client, graph: str) -> None:
    """Build a graph shaped like the measured corpus, not like a happy path."""

    def q(cypher: str) -> Any:
        return client.execute_command("GRAPH.QUERY", graph, cypher, "--compact")

    legacy_props = ", ".join(f'{k}: "{v}"' for k, v in LEGACY_NODE_PROPERTIES.items())
    q(
        "CREATE (:Letter {normCode: 'l-legacy', createdAt: '2026-02-11T00:00:00', "
        f"lastUpdated: '2026-02-11T00:00:00', {legacy_props}}})"
    )
    for norm in ("l-clean", "l-target-a", "l-target-b", "l-peer", "l-ambig", "l-ghost", "l-src"):
        q(
            f"CREATE (:Letter {{normCode: '{norm}', createdAt: '2026-02-11T00:00:00', "
            "lastUpdated: '2026-02-11T00:00:00'})"
        )
    # An unrelated label the Letter migration must not touch.
    q("CREATE (:Contract {contract_id: 'c-1', title: 'unrelated contract'})")

    def edge(src, dst, rel="CITES", owner=None, source="parser") -> None:
        props = [f'source: "{source}"']
        if owner is not None:
            props.append(f'owner_document_id: "{owner}"')
        q(
            f"MATCH (s:Letter {{normCode: '{src}'}}), (d:Letter {{normCode: '{dst}'}}) "
            f"CREATE (s)-[:{rel} {{{', '.join(props)}}}]->(d)"
        )

    # 1  uniquely resolvable, unowned                      -> SET_OWNER docLegacy
    edge("l-legacy", "l-target-a")
    # 2  already migrated, valid explicit owner            -> unchanged
    edge("l-clean", "l-target-b", owner="docClean")
    # 3+4 peer A / peer B on one shared key                -> unchanged, both survive
    edge("l-peer", "l-target-a", owner="docPeerA")
    edge("l-peer", "l-target-a", owner="docPeerB")
    # 5  no canonical evidence at all                      -> stays NULL (R11-A2)
    edge("l-peer", "l-target-b")
    # 6  two candidate owners                              -> stays NULL (R11-A4)
    edge("l-ambig", "l-target-a")
    # 7  malformed owner naming no document                -> reported, untouched
    edge("l-legacy", "l-ghost", owner=GHOST_OWNER)
    # 8  empty-string owner, resolvable                    -> SET_OWNER docLegacy
    edge("l-legacy", "l-target-b", rel="REPLIES_TO", owner="", source="system")
    # 9  empty-string owner, NOT resolvable                -> CLEAR_OWNER (R11-A3)
    edge("l-clean", "l-target-a", owner="")
    # 10 unowned duplicate of an already-owned assertion    -> DELETE_DUPLICATE
    edge("l-clean", "l-target-b")
    # 11 owner inferable ONLY from the source tag           -> stays NULL
    edge("l-src", "l-target-a", source="docSourceTag")


SEEDED_EDGE_COUNT = 11
SEEDED_LETTER_NODE_COUNT = 8


@pytest.fixture()
def graph_env():
    client = _redis_client()
    name = disposable_graph_name("g32state")
    _seed(client, name)
    state = migration.FalkorStateClient(client, name)
    index = migration.CanonicalReferenceIndex.from_mongo_documents(CANONICAL_DOCUMENTS)
    try:
        yield client, state, index
    finally:
        drop_disposable_graph(client, name)


def _comparable(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """A snapshot without engine-assigned edge ids, for cross-graph comparison."""
    return {
        "nodes": snapshot["nodes"],
        "edges": sorted(
            (tuple(edge[1:]) for edge in snapshot["edges"]),
            key=lambda row: tuple("" if value is None else str(value) for value in row),
        ),
    }


# ---------------------------------------------------------------------------
# the seed itself is the premise; assert it rather than assume it
# ---------------------------------------------------------------------------


def test_the_seed_reproduces_the_measured_legacy_shape(graph_env):
    _client, state, _index = graph_env
    nodes = migration.read_letter_nodes(state)
    edges = migration.read_authority_edges(state)

    assert len(nodes) == SEEDED_LETTER_NODE_COUNT
    assert len(edges) == SEEDED_EDGE_COUNT

    legacy = next(node for node in nodes if node.norm_code == "l-legacy")
    assert set(legacy.forbidden_properties) == set(LEGACY_NODE_PROPERTIES)

    owners = [edge.owner_document_id for edge in edges]
    assert owners.count(None) == 5
    assert owners.count("") == 2


def test_the_unmigrated_seed_fails_validation(graph_env):
    """A validator that passes the dirty state would prove nothing about the clean one."""
    _client, state, index = graph_env
    result = validation.validate_graph_state(state, index)
    assert not result.ok
    violated = set(result.rules_violated())
    assert "R1_no_forbidden_letter_property" in violated
    assert "R2_resolvable_edge_has_owner" in violated
    assert "R3_no_empty_string_owner" in violated
    assert "R4_no_owner_naming_no_canonical_document" in violated


# ---------------------------------------------------------------------------
# target safety, against the real engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("business_graph", ["G", "contraclaim"])
def test_the_migration_refuses_a_live_business_graph_on_this_engine(
    graph_env, business_graph
):
    """The graphs it must never touch are on the very instance this test uses."""
    client, _state, index = graph_env
    live = migration.FalkorStateClient(client, business_graph)
    with pytest.raises(migration.MigrationTargetRefused):
        migration.run_migration(client=live, index=index, dry_run=True)


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_a_dry_run_changes_nothing_in_the_engine(graph_env):
    _client, state, index = graph_env
    before = validation.semantic_snapshot(state)

    receipt = migration.run_migration(client=state, index=index, dry_run=True)

    after = validation.semantic_snapshot(state)
    assert after == before, "dry run mutated the graph"
    assert receipt.dry_run is True
    assert receipt.environment == migration.LOCAL_REHEARSAL_ENVIRONMENT


def test_the_dry_run_receipt_reports_every_ownership_class(graph_env):
    _client, state, index = graph_env
    receipt = migration.run_migration(client=state, index=index, dry_run=True)

    assert receipt.shared_letter_nodes == SEEDED_LETTER_NODE_COUNT
    assert receipt.authority_edges == SEEDED_EDGE_COUNT
    assert receipt.nodes_with_forbidden_properties == 1
    assert receipt.forbidden_property_instances == len(LEGACY_NODE_PROPERTIES)

    assert receipt.owned_valid == 3
    assert receipt.owned_invalid == 1
    assert receipt.owned_empty == 2
    assert receipt.resolvable == 2
    assert receipt.ambiguous == 1
    assert receipt.unresolved == 2
    assert (
        receipt.owned_valid
        + receipt.owned_invalid
        + receipt.owned_empty
        + receipt.resolvable
        + receipt.ambiguous
        + receipt.unresolved
        == SEEDED_EDGE_COUNT
    )

    # What an apply WOULD do, stated before it is allowed to do it.
    assert receipt.edges_owner_set == 2
    assert receipt.edges_owner_cleared == 1
    assert receipt.edges_duplicates_removed == 1


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def _apply(state, index, **kwargs):
    return migration.run_migration(client=state, index=index, dry_run=False, **kwargs)


def test_apply_strips_only_the_forbidden_shared_properties(graph_env):
    _client, state, index = graph_env
    _apply(state, index)

    nodes = {node.norm_code: node for node in migration.read_letter_nodes(state)}
    assert len(nodes) == SEEDED_LETTER_NODE_COUNT, "no node may be deleted"
    legacy = nodes["l-legacy"]
    assert legacy.forbidden_properties == ()
    assert "normCode" in legacy.properties
    assert "createdAt" in legacy.properties


def test_apply_leaves_an_unrelated_label_untouched(graph_env):
    """The Letter rules are not universal - a `(:Contract)` node is out of scope."""
    client, state, index = graph_env
    _apply(state, index)
    response = client.execute_command(
        "GRAPH.QUERY",
        state.graph_name,
        "MATCH (c:Contract) RETURN c.contract_id, c.title",
        "--compact",
    )
    rows = migration._parse_rows(response)
    assert rows == [{"c.contract_id": "c-1", "c.title": "unrelated contract"}]


def test_apply_backfills_only_uniquely_derivable_ownership(graph_env):
    _client, state, index = graph_env
    receipt = _apply(state, index)

    assert receipt.edges_owner_set == 2
    assert receipt.edges_owner_cleared == 1
    assert receipt.edges_duplicates_removed == 1
    assert receipt.nodes_changed == 1

    edges = migration.read_authority_edges(state)
    assert len(edges) == SEEDED_EDGE_COUNT - 1  # the duplicate is gone

    by_key: Dict[tuple, List[Any]] = {}
    for edge in edges:
        by_key.setdefault((edge.src_norm, edge.dst_norm, edge.rel_type), []).append(edge)

    # resolvable -> owned
    assert by_key[("l-legacy", "l-target-a", "CITES")][0].owner_document_id == "docLegacy"
    # empty string, resolvable -> real owner
    assert (
        by_key[("l-legacy", "l-target-b", "REPLIES_TO")][0].owner_document_id == "docLegacy"
    )
    # empty string, unresolvable -> NULL, never left as ''
    assert by_key[("l-clean", "l-target-a", "CITES")][0].owner_document_id is None
    # ambiguous -> untouched
    assert by_key[("l-ambig", "l-target-a", "CITES")][0].owner_document_id is None
    # no canonical evidence -> untouched
    assert by_key[("l-peer", "l-target-b", "CITES")][0].owner_document_id is None
    # source tag is not evidence
    assert by_key[("l-src", "l-target-a", "CITES")][0].owner_document_id is None
    # malformed owner -> reported, not guessed away
    assert by_key[("l-legacy", "l-ghost", "CITES")][0].owner_document_id == GHOST_OWNER

    assert all(edge.owner_document_id != "" for edge in edges), "R11-A3 violated"


def test_apply_preserves_peer_ownership_on_a_shared_key(graph_env):
    _client, state, index = graph_env
    _apply(state, index)
    owners = sorted(
        edge.owner_document_id
        for edge in migration.read_authority_edges(state)
        if (edge.src_norm, edge.dst_norm, edge.rel_type) == ("l-peer", "l-target-a", "CITES")
    )
    assert owners == ["docPeerA", "docPeerB"]


def test_apply_does_not_duplicate_support_that_already_existed(graph_env):
    _client, state, index = graph_env
    _apply(state, index)
    matching = [
        edge
        for edge in migration.read_authority_edges(state)
        if (edge.src_norm, edge.dst_norm, edge.rel_type)
        == ("l-clean", "l-target-b", "CITES")
    ]
    assert len(matching) == 1
    assert matching[0].owner_document_id == "docClean"


# ---------------------------------------------------------------------------
# validation after apply
# ---------------------------------------------------------------------------


def test_validation_after_apply_reports_the_malformed_owner_and_nothing_else(graph_env):
    """The one surviving finding is the one the migration must NOT have repaired."""
    _client, state, index = graph_env
    before_edges = migration.read_authority_edges(state)
    _apply(state, index)

    result = validation.validate_graph_state(state, index, before_edges=before_edges)
    assert result.rules_violated() == ["R4_no_owner_naming_no_canonical_document"]
    assert len(result.findings) == 1
    assert GHOST_OWNER in result.findings[0].detail
    assert result.unchecked_rules == []
    assert result.counts["unresolved_edges"] == 4


def test_validation_accepts_the_unresolved_remainder_under_r11_a2(graph_env):
    """Four edges stay NULL by design; that is the accepted amendment, not a failure."""
    _client, state, index = graph_env
    _apply(state, index)
    unresolved = [
        (edge.src_norm, edge.dst_norm, edge.rel_type)
        for edge in migration.read_authority_edges(state)
        if edge.owner_document_id is None
    ]
    assert sorted(unresolved) == [
        ("l-ambig", "l-target-a", "CITES"),
        ("l-clean", "l-target-a", "CITES"),
        ("l-peer", "l-target-b", "CITES"),
        ("l-src", "l-target-a", "CITES"),
    ]
    # l-src -> l-target-a keeps its source tag AND stays unowned.
    src_edge = next(
        edge
        for edge in migration.read_authority_edges(state)
        if edge.src_norm == "l-src"
    )
    assert src_edge.source == "docSourceTag"
    assert src_edge.owner_document_id is None


# ---------------------------------------------------------------------------
# idempotency and interruption recovery
# ---------------------------------------------------------------------------


def test_a_second_pass_changes_nothing(graph_env):
    _client, state, index = graph_env
    first = _apply(state, index)
    assert first.changed > 0

    snapshot = validation.semantic_snapshot(state)
    second = _apply(state, index)

    assert second.changed == 0
    assert validation.semantic_snapshot(state) == snapshot
    assert second.edges_unchanged == SEEDED_EDGE_COUNT - 1


def test_an_interrupted_run_converges_to_the_uninterrupted_result(graph_env):
    """Restart from the top: there is no cursor to lose, and this proves it."""
    client, state, index = graph_env
    _apply(state, index)
    uninterrupted = _comparable(validation.semantic_snapshot(state))

    other_name = disposable_graph_name("g32state_recovery")
    _seed(client, other_name)
    other = migration.FalkorStateClient(client, other_name)
    try:
        with pytest.raises(migration.MigrationInterrupted):
            migration.run_migration(
                client=other, index=index, dry_run=False, interrupt_after=2
            )
        partial = validation.semantic_snapshot(other)
        assert _comparable(partial) != uninterrupted, "the interruption did nothing"

        migration.run_migration(client=other, index=index, dry_run=False)
        assert _comparable(validation.semantic_snapshot(other)) == uninterrupted
    finally:
        drop_disposable_graph(client, other_name)


# ---------------------------------------------------------------------------
# the current writer and reader, against the migrated graph
# ---------------------------------------------------------------------------


def _falkor_service(graph_name: str, *, cleanup: bool):
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    return FalkorGraphService(
        FalkorGraphConfig(
            host=falkor_host(),
            port=falkor_port(),
            graph_name=graph_name,
            password=None,
            enabled=True,
            cleanup=cleanup,
        )
    )


def test_the_current_writer_does_not_recontaminate_the_migrated_node(graph_env):
    _client, state, index = graph_env
    _apply(state, index)

    service = _falkor_service(state.graph_name, cleanup=True)
    service.upsert_letter_with_refs(
        {
            "code": "L-LEGACY",
            "normCode": "l-legacy",
            "subject": "a subject the writer must not persist",
            "direction": "outgoing",
            "organization_id": "org-B",
            "project_id": "proj-B",
            "date": "2026-03-01",
        },
        [{"code": "L-TARGET-A", "type": "CITES", "source": "parser"}],
        cleanup=True,
        owner_document_id="docLegacy",
    )

    nodes = {node.norm_code: node for node in migration.read_letter_nodes(state)}
    assert nodes["l-legacy"].forbidden_properties == ()
    assert all(
        edge.owner_document_id != ""
        for edge in migration.read_authority_edges(state)
    )


def test_an_ownerless_write_is_still_refused_after_migration(graph_env):
    from rbac_backend.services.falkor_graph_service import FalkorGraphError

    _client, state, index = graph_env
    _apply(state, index)
    service = _falkor_service(state.graph_name, cleanup=True)
    with pytest.raises(FalkorGraphError):
        service.upsert_letter_with_refs(
            {"code": "L-LEGACY", "normCode": "l-legacy"},
            [{"code": "L-TARGET-A", "type": "CITES"}],
            cleanup=True,
            owner_document_id=None,
        )


def test_retraction_after_migration_removes_only_the_retracting_owners_support(graph_env):
    """G31's promise, restated against migrated state: A cannot remove B."""
    _client, state, index = graph_env
    _apply(state, index)

    service = _falkor_service(state.graph_name, cleanup=True)
    service.upsert_letter_with_refs(
        {"code": "L-PEER", "normCode": "l-peer"}, [], cleanup=True, owner_document_id="docPeerA"
    )

    remaining = migration.read_authority_edges(state)
    peer_owners = sorted(
        edge.owner_document_id
        for edge in remaining
        if (edge.src_norm, edge.dst_norm, edge.rel_type) == ("l-peer", "l-target-a", "CITES")
    )
    assert peer_owners == ["docPeerB"], "owner A's retraction took owner B's support"

    # The unresolved legacy edge on the same source node is untouched: R11-A2 says
    # it is not retraction authority for anyone, including the owner retracting.
    unresolved = [
        edge
        for edge in remaining
        if (edge.src_norm, edge.dst_norm, edge.rel_type) == ("l-peer", "l-target-b", "CITES")
    ]
    assert len(unresolved) == 1
    assert unresolved[0].owner_document_id is None


def test_a_backfilled_owner_can_now_retract_its_own_legacy_edge(graph_env):
    """The point of Stage 3: an unowned edge was permanently unretractable."""
    _client, state, index = graph_env
    _apply(state, index)

    service = _falkor_service(state.graph_name, cleanup=True)
    service.upsert_letter_with_refs(
        {"code": "L-LEGACY", "normCode": "l-legacy"},
        [],
        cleanup=True,
        owner_document_id="docLegacy",
    )

    keys = {
        (edge.src_norm, edge.dst_norm, edge.rel_type)
        for edge in migration.read_authority_edges(state)
    }
    assert ("l-legacy", "l-target-a", "CITES") not in keys
    assert ("l-legacy", "l-target-b", "REPLIES_TO") not in keys
    # The malformed-owner edge belongs to nobody, so nobody retracted it.
    assert ("l-legacy", "l-ghost", "CITES") in keys


def test_the_current_reader_sees_identity_only_after_migration(graph_env):
    """No stale property may become content - the G30 half, on migrated state."""
    _client, state, index = graph_env
    _apply(state, index)

    service = _falkor_service(state.graph_name, cleanup=False)
    rows = service.get_thread("l-legacy", depth=1)
    assert rows
    for row in rows:
        assert set(row) <= {"normCode", "createdAt"}
        assert "subject" not in row
        assert "organization_id" not in row
