"""G29 umbrella: a stale graph artifact may physically exist and must have
ZERO material influence, and sync must not reintroduce it.

The three individual gaps are closed elsewhere:
  G32 - the shared Letter node carries only `normCode` (+ write metadata), so it
        holds nothing document-, tenant-, or perspective-owned to leak.
  G31 - `sync_document_to_falkor` resolves authority before writing.
  G30 - the drafting consumer resolves each code's provenance to a canonical
        document and drops denied codes.

This suite is the end-to-end statement of the umbrella: leave the artifact in
the graph deliberately, then prove nothing consumes it and a resync does not
put it back.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

FALKOR_HOST = os.environ.get("FALKOR_TEST_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKOR_TEST_PORT", "6380"))

from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
)

pytestmark = pytest.mark.integration

STALE_MARKER = "STALE_GRAPH_SUPPORT_MARKER_4242"


def _falkor(graph_name: str):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    service = FalkorGraphService(
        FalkorGraphConfig(host=FALKOR_HOST, port=FALKOR_PORT, graph_name=graph_name,
                          password=None, enabled=True, cleanup=False)
    )
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {FALKOR_HOST}:{FALKOR_PORT}: {exc}")
    return service


@pytest.fixture()
def falkor():
    # G-A21 / GRAPH-GATES U9. This suite is REQUIRED evidence for the G29
    # umbrella, and it used to mint its own `g29_*` namespace and tear down with a
    # swallowed `GRAPH.DELETE`. That is exactly the pattern that left
    # `g31_letterdraft_d0fdb2ac7c` on a shared instance: a failed delete was
    # silent, so a crashed run looked like a clean one. It now mints through the
    # sanctioned helper, which names the graph after THIS run, refuses to delete
    # anything it did not create, verifies the deletion against `GRAPH.LIST`, and
    # raises rather than leaving residue. No assertion in this file changed.
    name = disposable_graph_name("stalecontainment")
    svc = _falkor(name)
    yield svc
    drop_disposable_graph(svc._get_client(), name)


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row
        return _gen()


class _Coll:
    """Supports `find()` iteration - the resolver examines ALL supporters, not
    an arbitrary first one, so the fake must not answer with `find_one`."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None):
        query = query or {}
        return _Cursor([
            row for row in self.rows
            if all(str(row.get(k)) == str(v) for k, v in query.items())
        ])

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        rows = [r for r in self.rows
                if all(str(r.get(kk)) == str(vv) for kk, vv in (query or {}).items())]
        return rows[0] if rows else None


class _DB:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._c = {"documents": _Coll(documents), "letters": _Coll([])}

    def __getitem__(self, name: str) -> _Coll:
        return self._c.setdefault(name, _Coll([]))

    def __getattr__(self, name: str) -> _Coll:
        return self[name]


def _letter_count(falkor, code: str) -> int:
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    result = falkor._execute(
        "MATCH (l:Letter {normCode: $n}) RETURN count(l)",
        {"n": normalize_letter_code(code)},
    )
    rows = result[1] if len(result) > 1 else []
    return int(rows[0][0][1]) if rows else 0


def test_a_stale_graph_artifact_has_zero_material_influence(falkor) -> None:
    """1) clean 2) graph populated 3) blocked 4) artifact LEFT IN PLACE
    5) every material consumer must ignore it."""
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
    from rbac_backend.services.publication_policy import graph_codes_denied

    service = GraphIngestionService(falkor=falkor)
    document = {
        "_id": "doc-g29", "letterNo": "LTR-G29", "subject": STALE_MARKER,
        "organization_id": "org-A", "project_id": "proj-A",
        "processing_status": "metadata_extracted",
    }

    # 1-2: clean document populates the graph.
    service.sync_document_to_falkor("doc-g29", document, None, "letter")
    assert _letter_count(falkor, "LTR-G29") == 1

    # 3: the source becomes blocked. 4: the artifact is deliberately LEFT.
    document["processing_status"] = "human_review_required"
    assert _letter_count(falkor, "LTR-G29") == 1, "stale artifact intentionally retained"

    # 5: every consumer resolves provenance and denies the code.
    db = _DB([{**document, "letterNoNormalized": "ltr-g29"}])
    denied = asyncio.run(graph_codes_denied(db, ["ltr-g29"]))
    assert "ltr-g29" in denied, "stale graph support was still consumable"


def test_a_resync_after_blocking_does_not_reintroduce_support(falkor) -> None:
    """The other half of the umbrella: sync cannot put denied support back."""
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    service = GraphIngestionService(falkor=falkor)
    document = {
        "_id": "doc-g29b", "letterNo": "LTR-G29B", "subject": STALE_MARKER,
        "organization_id": "org-A", "project_id": "proj-A",
        "processing_status": "metadata_extracted",
    }
    service.sync_document_to_falkor("doc-g29b", document, None, "letter")
    falkor._execute("MATCH (l:Letter {normCode: $n}) DETACH DELETE l",
                    {"n": normalize_letter_code("LTR-G29B")})

    document["processing_status"] = "human_review_required"
    service.sync_document_to_falkor("doc-g29b", document, None, "letter")

    assert _letter_count(falkor, "LTR-G29B") == 0


def test_blocking_one_supporter_does_not_damage_another(falkor) -> None:
    """Shared-node deletion safety: B's legitimate support must survive."""
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
    from rbac_backend.services.publication_policy import graph_codes_denied

    service = GraphIngestionService(falkor=falkor)
    shared = "LTR-SHARED29"
    doc_a = {"_id": "doc-a", "letterNo": shared, "organization_id": "org-A",
             "project_id": "proj-A", "processing_status": "metadata_extracted"}
    doc_b = {"_id": "doc-b", "letterNo": shared, "organization_id": "org-B",
             "project_id": "proj-B", "processing_status": "metadata_extracted"}
    service.sync_document_to_falkor("doc-a", doc_a, None, "letter")
    service.sync_document_to_falkor("doc-b", doc_b, None, "letter")
    assert _letter_count(falkor, shared) == 1  # one shared canonical node

    # A is blocked; B remains clean. The code still has a consumable supporter,
    # so the shared identity stays usable - A losing its support must not take
    # B's with it.
    db = _DB([
        {**doc_a, "letterNoNormalized": "ltr-shared29", "processing_status": "human_review_required"},
        {**doc_b, "letterNoNormalized": "ltr-shared29"},
    ])
    denied = asyncio.run(graph_codes_denied(db, ["ltr-shared29"]))

    assert "ltr-shared29" not in denied, "B's legitimate support was destroyed by A's block"
    assert _letter_count(falkor, shared) == 1, "canonical node preserved"


def test_when_every_supporter_is_blocked_the_code_is_denied(falkor) -> None:
    from rbac_backend.services.publication_policy import graph_codes_denied

    db = _DB([
        {"_id": "doc-a", "letterNoNormalized": "ltr-both", "processing_status": "human_review_required"},
    ])
    assert "ltr-both" in asyncio.run(graph_codes_denied(db, ["ltr-both"]))


def _edge_count(falkor, src: str, relationship: str) -> int:
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    result = falkor._execute(
        f"MATCH (:Letter {{normCode: $n}})-[e:{relationship}]->(:Letter) RETURN count(e)",
        {"n": normalize_letter_code(src)},
    )
    rows = result[1] if len(result) > 1 else []
    return int(rows[0][0][1]) if rows else 0


def test_a_system_source_edge_is_retractable(falkor) -> None:
    """G31/G29: retraction authority is ownership, not the `source` tag.

    `graph_ingestion_service` emits source='system' for `previous_letter_id`,
    and callers may pass any `ref["source"]` string at all. Cleanup used to
    delete only tags in an allow-list (`parser`/`manual`), so a system edge was
    written but could never be retracted - clearing `previous_letter_id` left a
    REPLIES_TO edge asserting a reply-chain that no longer exists, permanently.

    Asserts the FINAL graph state, not that cleanup was invoked.
    """
    src = f"ltr-sys-{uuid.uuid4().hex[:8]}"
    dst = f"ltr-prev-{uuid.uuid4().hex[:8]}"

    # 1. The document replies to `dst` - the writer tags this edge 'system'.
    falkor.upsert_letter_with_refs(
        {"code": src, "direction": "outgoing"},
        [{"code": dst, "type": "REPLIES_TO", "source": "system"}],
        cleanup=True,
        owner_document_id="doc-sys-1",
    )
    assert _edge_count(falkor, src, "REPLIES_TO") == 1, "precondition: edge written"

    # 2. `previous_letter_id` is cleared, so the owner's desired set is now empty.
    falkor.upsert_letter_with_refs(
        {"code": src, "direction": "outgoing"},
        [],
        cleanup=True,
        owner_document_id="doc-sys-1",
    )

    assert _edge_count(falkor, src, "REPLIES_TO") == 0, (
        "system-source edge survived retraction: the graph still asserts a "
        "reply-chain the source document no longer claims"
    )


def test_an_arbitrary_caller_source_tag_is_retractable(falkor) -> None:
    """The tag set is open; retraction must not depend on knowing every value."""
    src = f"ltr-tag-{uuid.uuid4().hex[:8]}"
    dst = f"ltr-tagdst-{uuid.uuid4().hex[:8]}"

    falkor.upsert_letter_with_refs(
        {"code": src, "direction": "outgoing"},
        [{"code": dst, "type": "CITES", "source": "some-future-extractor-v3"}],
        cleanup=True,
        owner_document_id="doc-tag-1",
    )
    assert _edge_count(falkor, src, "CITES") == 1

    falkor.upsert_letter_with_refs(
        {"code": src, "direction": "outgoing"},
        [],
        cleanup=True,
        owner_document_id="doc-tag-1",
    )
    assert _edge_count(falkor, src, "CITES") == 0, (
        "an unlisted source tag was written but is not retractable"
    )


def test_a_legacy_unattributed_edge_cannot_be_retracted_but_is_inert(falkor) -> None:
    """The real shape of the existing graph: edges written before ownership.

    Ownership-scoped cleanup cannot touch a NULL-owner edge (`NULL = $owner` is
    never true), and backfilling ownership is a migration that must NOT run
    here. So G31 provably cannot retract these; the containment claim is that
    G30 makes them inert regardless. This test states both halves so the pair
    is never silently broken - if a future change makes the consumer trust the
    edge, this fails even though the writer is untouched.
    """
    from rbac_backend.services.falkor_graph_service import normalize_letter_code
    from rbac_backend.services.publication_policy import graph_codes_denied

    src = f"ltr-legacy-{uuid.uuid4().hex[:8]}"
    dst = f"ltr-legacydst-{uuid.uuid4().hex[:8]}"
    n_src, n_dst = normalize_letter_code(src), normalize_letter_code(dst)

    # A pre-ownership edge: no owner_document_id property at all.
    falkor._execute(
        "MERGE (a:Letter {normCode: $s}) MERGE (b:Letter {normCode: $d}) "
        "MERGE (a)-[e:CITES]->(b) SET e.source = 'parser'",
        {"s": n_src, "d": n_dst},
    )
    assert _edge_count(falkor, src, "CITES") == 1

    # Half 1: the owner-scoped writer cannot retract it.
    falkor.upsert_letter_with_refs(
        {"code": src, "direction": "outgoing"}, [],
        cleanup=True, owner_document_id="doc-legacy-owner",
    )
    assert _edge_count(falkor, src, "CITES") == 1, (
        "unexpected: ownership cleanup deleted an edge it does not own"
    )

    # Half 2: with no consumable supporting document, the consumer denies the
    # code, so the surviving edge cannot reach or influence legal output.
    db = _DB([])
    assert n_dst in asyncio.run(graph_codes_denied(db, [n_dst])), (
        "a legacy edge that cannot be retracted is also not contained - it "
        "would reach output as an unattributed graph fact"
    )
