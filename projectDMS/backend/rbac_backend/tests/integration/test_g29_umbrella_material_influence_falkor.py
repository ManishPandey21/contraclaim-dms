"""G29 UMBRELLA: the four gates composed, against a real engine, end to end.

G29 is not a fourth independent gate. Its own source says so: "The three
individual gaps are closed elsewhere: G32 ... G31 ... G30 .... This suite is the
end-to-end statement of the umbrella." What no existing suite states is the
COMPOSITION - each predecessor is proven on its own seam, with hand-seeded state
standing in for the neighbour.

This file removes those stand-ins. Every scenario below runs at least two
subsystems for real, in sequence, and terminates at a FINAL MATERIAL OBJECT (a
`ReportPreview`, its `metrics`, the exported CSV bytes, or a `SourceEvidence`),
never at a helper and never at a graph assertion alone:

  A  real G31 writer -> real G30 consumer -> canonical authority withdrawn ->
     consumer again -> retraction -> writer retry
  B  legacy state -> real G32 migration -> real retraction -> real consumer
  C  invalid / ambiguous ownership -> migration -> retraction -> consumer
  D  legacy raw property poisoning -> consumer -> migration -> consumer
  E  contaminant capacity, with the graph built by the REAL writer
  F  the positive path, all the way through, and its retraction

WHY "ZERO MATERIAL INFLUENCE" IS STRONGER THAN "NOT DISPLAYED".
GRAPH-GATES lists disclosure, source selection, SOURCE SUPPRESSION, ranking,
count, ordering, evidence availability, prompt composition, report content,
export and downstream graph rewrite. A contaminant that discloses nothing and
still evicts a legitimate letter from the page has influenced the answer. Every
scenario therefore asserts a positive as well as a negative: the valid source is
present, the count is right, and the marker is absent.

PRESENCE IS NOT PERMISSION TO SERVE. Scenario A deliberately leaves the stale
artefact physically in the graph across an authority change and asserts it is
still there afterwards. Deleting it would prove a different, weaker thing.

WHAT THIS FILE DOES NOT PROVE. The graph here is a run-owned disposable
namespace. GRAPH-GATES field 12 names a green run against a fresh graph as
INVALID closure evidence for deployed state, and nothing here is offered as
anything else: the production graphs are never opened, never migrated and
structurally refused by the migration's own target guard.

Run with the authorised relay:

    G29_UMBRELLA_CERTIFICATION=1 FALKOR_TEST_HOST=127.0.0.1 FALKOR_TEST_PORT=6390 \
      pytest backend/rbac_backend/tests/integration/test_g29_umbrella_material_influence_falkor.py
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.report import ReportRequest
from rbac_backend.services import graph_letter_state_migration as migration
from rbac_backend.services import report_service as report_service_module
from rbac_backend.services.report_service import ReportService
from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_password,
    falkor_port,
    falkor_unreachable,
)

pytestmark = pytest.mark.integration

ORG = "org-A"
PROJECT = "proj-A"

ROOT_CODE = "LTR-ROOT-G29"
ROOT_NORM = "ltr-root-g29"

VALID_CODE = "LTR-VALID-G29"
VALID_NORM = "ltr-valid-g29"
PEER_CODE = "LTR-PEER-G29"
PEER_NORM = "ltr-peer-g29"

#: Only ever written onto graph state, never into canonical Mongo. Its presence
#: in any final object is disclosure; the valid source's ABSENCE is suppression.
POISON_MARKER = "G29_UMBRELLA_POISON_MARKER_66601"
CANONICAL_MARKER = "G29_UMBRELLA_CANONICAL_MARKER"


# ---------------------------------------------------------------------------
# Canonical Mongo. The scope and authority queries are APPLIED: a collection
# that returned everything would make every assertion below vacuous.
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if not any(_same(actual, candidate) for candidate in expected["$in"]):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, *_a: Any, **_k: Any) -> "_Cursor":
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield deepcopy(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def count_documents(self, query: Optional[Dict[str, Any]] = None) -> int:
        return len([row for row in self.rows if _matches(row, query)])

    def aggregate(self, _pipeline: Any) -> _Cursor:  # pragma: no cover - unused
        return _Cursor([])


class _Database:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._collections: Dict[str, _Collection] = {"documents": _Collection(documents)}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _document(
    *,
    document_id: str,
    letter_no: str,
    norm: str,
    references: Optional[List[Dict[str, Any]]] = None,
    organization_id: str = ORG,
    project_id: str = PROJECT,
    blocked: bool = False,
) -> Dict[str, Any]:
    return {
        "_id": document_id,
        "letterNo": letter_no,
        "letter_no": letter_no,
        "letterNoNormalized": norm,
        "organization_id": organization_id,
        "project_id": project_id,
        "subject": f"{CANONICAL_MARKER} SUBJECT {norm}",
        "summary": f"{CANONICAL_MARKER} SUMMARY {norm}",
        "date": "2026-09-01",
        "references": references or [],
        "processing_status": "human_review_required" if blocked else "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }


def _blocked(documents: List[Dict[str, Any]], *document_ids: str) -> List[Dict[str, Any]]:
    """A canonical snapshot with authority withdrawn from named documents."""
    out = deepcopy(documents)
    for row in out:
        if str(row["_id"]) in document_ids:
            row["processing_status"] = "human_review_required"
    return out


# ---------------------------------------------------------------------------
# Real Falkor, run-owned namespace.
# ---------------------------------------------------------------------------


def _falkor(graph_name: str, *, cleanup: bool = False):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    service = FalkorGraphService(
        FalkorGraphConfig(
            host=falkor_host(),
            port=falkor_port(),
            graph_name=graph_name,
            password=falkor_password(),
            enabled=True,
            cleanup=cleanup,
        )
    )
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        falkor_unreachable(exc)
    return service


@pytest.fixture()
def graph():
    """A disposable graph owned by this run, dropped loudly on the way out."""
    name = disposable_graph_name("g29umbrella")
    service = _falkor(name, cleanup=True)
    try:
        yield service
    finally:
        drop_disposable_graph(service._get_client(), name)


def _writer(service: Any):
    """The REAL G31 writer, not a hand-seeded final graph."""
    from rbac_backend.graph.graph_ingestion_service import GraphIngestionService

    return GraphIngestionService(falkor=service)


def _sync(service: Any, document: Dict[str, Any]) -> None:
    _writer(service).sync_document_to_falkor(
        str(document["_id"]), document, None, "outgoing"
    )


def _edge_keys(service: Any) -> List[tuple]:
    state = migration.FalkorStateClient(service._get_client(), service.config.graph_name)
    return sorted(
        (edge.src_norm, edge.dst_norm, edge.rel_type, edge.owner_document_id)
        for edge in migration.read_authority_edges(state)
    )


def _node_norms(service: Any) -> List[str]:
    state = migration.FalkorStateClient(service._get_client(), service.config.graph_name)
    return sorted(node.norm_code for node in migration.read_letter_nodes(state))


def _state(service: Any) -> Any:
    return migration.FalkorStateClient(service._get_client(), service.config.graph_name)


# ---------------------------------------------------------------------------
# The final material consumer: the linked-chain report and its export.
# ---------------------------------------------------------------------------


def _user() -> CurrentUser:
    return CurrentUser(
        id="user-g29",
        username="reporter",
        email="reporter@example.com",
        roles=["orguser"],
        organization_id=ORG,
        organizations=[ORG],
        projects=[PROJECT],
    )


def _request(**overrides: Any) -> ReportRequest:
    payload: Dict[str, Any] = {
        "report_id": "linked-letter-chain",
        "letter_no": ROOT_CODE,
        "chain_direction": "up",
        "include_self": False,
        "organization_id": ORG,
        "limit": 5,
    }
    payload.update(overrides)
    return ReportRequest(**payload)


def _async_value(value: Any):
    async def _coro():
        return value

    return _coro()


def _wire(monkeypatch: pytest.MonkeyPatch, service: Any, documents: List[Dict[str, Any]]):
    """Pin BOTH boundaries: real Falkor on the disposable graph, applied Mongo."""
    db = _Database(documents)
    monkeypatch.setattr(report_service_module, "get_database", lambda: _async_value(db))
    import rbac_backend.services.falkor_graph_service as falkor_module

    monkeypatch.setattr(falkor_module, "FalkorGraphService", lambda: service)
    return ReportService(db), db


def _preview(report: ReportService, request: Optional[ReportRequest] = None):
    return asyncio.run(report.generate_preview(request or _request(), _user()))


def _download(report: ReportService, request: Optional[ReportRequest] = None):
    return asyncio.run(report.generate_download(request or _request(), _user()))


def _codes(preview: Any) -> List[str]:
    return [row["letter_no"] for row in preview.rows]


def _blob(preview: Any) -> str:
    return repr(preview.rows) + repr(preview.metrics)


async def _drafting_evidence(service: Any, db: Any, code: str):
    """The other final consumer: drafting `SourceEvidence`."""
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    builder = DraftContextBuilder(
        document_service=None,
        conversation_service=None,
        contract_service=SimpleNamespace(),
        graph_service=service,
        db=db,
    )
    letter = SimpleNamespace(letter_no=code, organization_id=ORG, project_id=PROJECT)
    request = SimpleNamespace(include_letter_codes=[], exclude_letter_codes=[])
    return await builder._graph_sources(letter, request, ORG, PROJECT, [])


# ===========================================================================
# SCENARIO A - stale source after authority withdrawal
# ===========================================================================
#
# The full ten-step sequence in one test, because splitting it would let each
# half be satisfied by a different graph. The writer, the consumer, the
# retraction and the retry all run for real, in order, over one graph.


def test_scenario_a_a_withdrawn_source_loses_all_influence_and_is_never_recreated(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = [
        _document(
            document_id="doc-valid",
            letter_no=VALID_CODE,
            norm=VALID_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(
            document_id="doc-peer",
            letter_no=PEER_CODE,
            norm=PEER_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM),
    ]

    # 1 + 2. Canonical source is consumable; the REAL writer creates support.
    for document in documents:
        _sync(graph, document)
    assert (VALID_NORM, ROOT_NORM, "CITES", "doc-valid") in _edge_keys(graph)

    # 3. The final consumer sees it.
    report, _db = _wire(monkeypatch, graph, documents)
    first = _preview(report)
    assert set(_codes(first)) == {VALID_NORM, PEER_NORM}
    assert first.metrics["total_linked"] == 2

    # 4 + 5. Canonical authority is withdrawn. The graph is NOT touched.
    withdrawn = _blocked(documents, "doc-valid")
    assert (VALID_NORM, ROOT_NORM, "CITES", "doc-valid") in _edge_keys(graph), (
        "the stale artefact must remain PHYSICALLY present - deleting it here "
        "would prove a weaker property than the one G29 asserts"
    )

    # 6 + 7. Called again, the withdrawn source has zero material influence:
    # not disclosed, not counted, and its slot goes to the legitimate peer
    # rather than being lost.
    report, _db = _wire(monkeypatch, graph, withdrawn)
    second = _preview(report)
    assert _codes(second) == [PEER_NORM]
    assert second.metrics["total_linked"] == 1
    assert VALID_NORM not in _blob(second)

    # The export inherits the same answer - `generate_download` runs the same
    # path, so a divergence here would be a second, unpinned consumer.
    report, _db = _wire(monkeypatch, graph, withdrawn)
    payload, _filename = _download(report)
    text = payload.decode("utf-8")
    assert PEER_NORM in text and VALID_NORM not in text

    # 8. Retraction executes for real.
    _writer(graph).remove_document_from_falkor("doc-valid", withdrawn[0])
    assert (VALID_NORM, ROOT_NORM, "CITES", "doc-valid") not in _edge_keys(graph)
    assert (PEER_NORM, ROOT_NORM, "CITES", "doc-peer") in _edge_keys(graph), (
        "retraction of one owner's support destroyed a peer owner's"
    )

    # 9 + 10. The writer retries against the SAME withdrawn snapshot and the
    # support is not recreated - the failure mode G31 exists for.
    _sync(graph, withdrawn[0])
    assert (VALID_NORM, ROOT_NORM, "CITES", "doc-valid") not in _edge_keys(graph)

    report, _db = _wire(monkeypatch, graph, withdrawn)
    third = _preview(report)
    assert _codes(third) == [PEER_NORM]
    assert third.metrics["total_linked"] == 1


# ===========================================================================
# SCENARIO B - migrated legacy ownership composed with retraction and reading
# ===========================================================================


def _seed_legacy_ownership(service: Any) -> None:
    """Legacy shape: an ownerless resolvable edge, peer A/B, an unresolved NULL."""

    def q(cypher: str) -> Any:
        return service._execute(cypher)

    for norm in (ROOT_NORM, VALID_NORM, PEER_NORM, "ltr-orphan-g29"):
        q(
            f"CREATE (:Letter {{normCode: '{norm}', createdAt: '2026-02-11T00:00:00', "
            "lastUpdated: '2026-02-11T00:00:00'})"
        )

    def edge(src: str, dst: str, owner: Optional[str] = None, source: str = "parser") -> None:
        props = [f'source: "{source}"']
        if owner is not None:
            props.append(f'owner_document_id: "{owner}"')
        q(
            f"MATCH (s:Letter {{normCode: '{src}'}}), (d:Letter {{normCode: '{dst}'}}) "
            f"CREATE (s)-[:CITES {{{', '.join(props)}}}]->(d)"
        )

    # uniquely resolvable from canonical evidence, but written without an owner
    edge(VALID_NORM, ROOT_NORM)
    # peer owners on one shared key - both legitimate, neither may take the other
    edge(PEER_NORM, ROOT_NORM, owner="doc-peer-a")
    edge(PEER_NORM, ROOT_NORM, owner="doc-peer-b")
    # no canonical evidence at all: stays NULL, non-authoritative (R11-A2)
    edge("ltr-orphan-g29", ROOT_NORM)


def _legacy_documents() -> List[Dict[str, Any]]:
    return [
        _document(
            document_id="doc-valid",
            letter_no=VALID_CODE,
            norm=VALID_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(
            document_id="doc-peer-a",
            letter_no=PEER_CODE,
            norm=PEER_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(
            document_id="doc-peer-b",
            letter_no=PEER_CODE,
            norm=PEER_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM),
    ]


def _migrate(service: Any, documents: List[Dict[str, Any]]) -> Any:
    index = migration.CanonicalReferenceIndex.from_mongo_documents(documents)
    return migration.run_migration(client=_state(service), index=index, dry_run=False)


def test_scenario_b_migration_makes_a_legacy_edge_retractable_without_touching_its_peer(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed legacy -> G32 migration -> G31 retraction -> G30 consumer.

    Before the migration the ownerless edge could never be retracted by any
    cleanup path. After it, its owner - and ONLY its owner - can, and the final
    report reflects current canonical sources either way.
    """
    _seed_legacy_ownership(graph)
    documents = _legacy_documents()

    receipt = _migrate(graph, documents)
    assert receipt.dry_run is False
    owners = dict(
        ((src, dst, rel), owner) for src, dst, rel, owner in _edge_keys(graph)
    )
    assert owners[(VALID_NORM, ROOT_NORM, "CITES")] == "doc-valid", "R11-A1"
    assert owners[("ltr-orphan-g29", ROOT_NORM, "CITES")] is None, "R11-A2"

    # Owner A retracts. B must survive - the shared node is global, so this is
    # the whole reason ownership exists.
    graph.upsert_letter_with_refs(
        {"code": PEER_CODE, "normCode": PEER_NORM}, [], cleanup=True, owner_document_id="doc-peer-a"
    )
    peer_owners = sorted(
        owner for src, dst, rel, owner in _edge_keys(graph) if src == PEER_NORM
    )
    assert peer_owners == ["doc-peer-b"], "owner A's retraction took owner B's support"

    # The final consumer's answer is the CURRENT canonical set, not the graph's
    # history: the peer code still has a consumable supporter (doc-peer-b), and
    # the unresolved orphan resolves to nothing and stays out.
    report, _db = _wire(monkeypatch, graph, documents)
    preview = _preview(report)
    assert set(_codes(preview)) == {VALID_NORM, PEER_NORM}
    assert "ltr-orphan-g29" not in _blob(preview)
    assert preview.metrics["total_linked"] == 2


def test_scenario_b_an_unresolved_legacy_edge_is_not_retraction_authority_for_anyone(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R11-A2 stated as behaviour: NULL ownership is inert in BOTH directions.

    It cannot be used to retract, and it cannot admit its source to the answer.
    """
    _seed_legacy_ownership(graph)
    documents = _legacy_documents()
    _migrate(graph, documents)

    # Nobody's retraction removes it.
    for owner in ("doc-valid", "doc-peer-a", "doc-peer-b"):
        graph.upsert_letter_with_refs(
            {"code": "LTR-ORPHAN-G29", "normCode": "ltr-orphan-g29"},
            [],
            cleanup=True,
            owner_document_id=owner,
        )
    orphan = [row for row in _edge_keys(graph) if row[0] == "ltr-orphan-g29"]
    assert orphan == [("ltr-orphan-g29", ROOT_NORM, "CITES", None)]

    # And it still contributes nothing to the final object.
    report, _db = _wire(monkeypatch, graph, documents)
    assert "ltr-orphan-g29" not in _blob(_preview(report))


# ===========================================================================
# SCENARIO C - invalid / ambiguous ownership
# ===========================================================================


def test_scenario_c_ambiguous_and_malformed_ownership_never_become_authoritative(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two owners could own it, or none exists: neither is guessed, and neither
    lets the source influence the final report."""
    graph._execute(
        "CREATE (:Letter {normCode: $root, createdAt: 'x', lastUpdated: 'x'})",
        {"root": ROOT_NORM},
    )
    for norm in ("ltr-ambig-g29", "ltr-ghost-g29"):
        graph._execute(
            "CREATE (:Letter {normCode: $n, createdAt: 'x', lastUpdated: 'x'})", {"n": norm}
        )
    graph._execute(
        "MATCH (s:Letter {normCode:'ltr-ambig-g29'}), (d:Letter {normCode:$root}) "
        "CREATE (s)-[:CITES {source:'parser'}]->(d)",
        {"root": ROOT_NORM},
    )
    graph._execute(
        "MATCH (s:Letter {normCode:'ltr-ghost-g29'}), (d:Letter {normCode:$root}) "
        "CREATE (s)-[:CITES {source:'parser', owner_document_id:'doc-that-does-not-exist'}]->(d)",
        {"root": ROOT_NORM},
    )

    # Two canonical documents assert the same reference: ownership is not
    # uniquely derivable, so R11-A4 forbids picking either.
    documents = [
        _document(
            document_id="doc-ambig-1",
            letter_no="LTR-AMBIG-G29",
            norm="ltr-ambig-g29",
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(
            document_id="doc-ambig-2",
            letter_no="LTR-AMBIG-G29",
            norm="ltr-ambig-g29",
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM),
    ]

    receipt = _migrate(graph, documents)
    owners = {(src, dst, rel): owner for src, dst, rel, owner in _edge_keys(graph)}
    assert owners[("ltr-ambig-g29", ROOT_NORM, "CITES")] is None, "first-match assignment"
    assert (
        owners[("ltr-ghost-g29", ROOT_NORM, "CITES")] == "doc-that-does-not-exist"
    ), "an INVALID owner is reported, never silently repaired into a different one"
    assert receipt.ambiguous == 1
    assert receipt.owned_invalid == 1

    # No retraction may treat either as some other owner's support.
    for owner in ("doc-ambig-1", "doc-ambig-2"):
        graph.upsert_letter_with_refs(
            {"code": "LTR-AMBIG-G29", "normCode": "ltr-ambig-g29"},
            [],
            cleanup=True,
            owner_document_id=owner,
        )
    assert ("ltr-ambig-g29", ROOT_NORM, "CITES", None) in _edge_keys(graph)

    # And the ghost source has no consumable supporter, so it stays out of the
    # final object while the ambiguous one - which DOES have consumable
    # supporters in Mongo - is admitted on that canonical evidence, not on the
    # strength of an owner nobody could derive.
    report, _db = _wire(monkeypatch, graph, documents)
    preview = _preview(report)
    assert "ltr-ghost-g29" not in _blob(preview)
    assert _codes(preview) == ["ltr-ambig-g29"]


# ===========================================================================
# SCENARIO D - legacy raw property poisoning
# ===========================================================================


def _poison(service: Any, norm: str) -> None:
    service._execute(
        "MATCH (n:Letter {normCode:$norm}) "
        "SET n.subject=$m, n.summary=$m, n.text_content=$m, n.code=$m, "
        "n.direction=$m, n.date=$m, n.organization_id=$m, n.project_id=$m",
        {"norm": norm, "m": POISON_MARKER},
    )


def _forbidden_properties(service: Any, norm: str) -> tuple:
    nodes = {node.norm_code: node for node in migration.read_letter_nodes(_state(service))}
    return nodes[norm].forbidden_properties


def test_scenario_d_legacy_node_properties_are_inert_before_migration_and_gone_after(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader must not need the migration to be safe, and the migration must
    still physically strip what the reader refuses to read."""
    documents = [
        _document(
            document_id="doc-valid",
            letter_no=VALID_CODE,
            norm=VALID_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM),
    ]
    for document in documents:
        _sync(graph, document)
    _poison(graph, VALID_NORM)
    assert _forbidden_properties(graph, VALID_NORM), "the seed must actually be poisoned"

    # BEFORE: the poison is physically present and materially inert, in both
    # the report and the drafting evidence.
    report, db = _wire(monkeypatch, graph, documents)
    before = _preview(report)
    assert _codes(before) == [VALID_NORM]
    assert POISON_MARKER not in _blob(before)
    assert CANONICAL_MARKER in _blob(before), "content must come from canonical Mongo"

    codes, sources = asyncio.run(_drafting_evidence(graph, db, ROOT_CODE))
    assert VALID_NORM in codes
    assert POISON_MARKER not in repr(sources)

    # AFTER: the migration removes them from the engine, and the answer is
    # unchanged - which is the point. A migration that altered the answer would
    # mean the properties had been load-bearing all along.
    _migrate(graph, documents)
    assert _forbidden_properties(graph, VALID_NORM) == ()

    report, db = _wire(monkeypatch, graph, documents)
    after = _preview(report)
    assert _codes(after) == _codes(before)
    assert after.metrics["total_linked"] == before.metrics["total_linked"]
    assert POISON_MARKER not in _blob(after)
    assert CANONICAL_MARKER in _blob(after)


# ===========================================================================
# SCENARIO E - capacity, with the graph built by the REAL writer
# ===========================================================================


def test_scenario_e_contaminants_cannot_spend_the_window_that_holds_a_valid_letter(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The G-A17 class in umbrella form.

    The contaminants are not hand-seeded: they are written by the real writer
    while they are still consumable, then withdrawn, foreign or deleted - which
    is how residue actually arises. `VALID_NORM` sorts AFTER every contaminant,
    so an implementation that merely happened to order by identity cannot pass.
    """
    contaminants = [(f"LTR-AAA-{index:03d}", f"ltr-aaa-{index:03d}") for index in range(8)]
    documents = [
        _document(
            document_id=f"doc-{norm}",
            letter_no=raw,
            norm=norm,
            references=[{"letterNo": ROOT_CODE}],
        )
        for raw, norm in contaminants
    ]
    documents.append(
        _document(
            document_id="doc-valid",
            letter_no=VALID_CODE,
            norm=VALID_NORM,
            references=[{"letterNo": ROOT_CODE}],
        )
    )
    documents.append(_document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM))
    for document in documents:
        _sync(graph, document)

    # Three different reasons to be inadmissible, all at once, and all of them
    # still physically present in the graph.
    withdrawn = _blocked(documents, *[f"doc-{norm}" for _raw, norm in contaminants[:3]])
    for row in withdrawn:  # foreign tenant
        if row["_id"] in {f"doc-{norm}" for _raw, norm in contaminants[3:6]}:
            row["organization_id"] = "org-B"
            row["project_id"] = "proj-B"
    withdrawn = [  # unresolvable: the canonical document is gone entirely
        row
        for row in withdrawn
        if row["_id"] not in {f"doc-{norm}" for _raw, norm in contaminants[6:]}
    ]

    report, _db = _wire(monkeypatch, graph, withdrawn)
    preview = _preview(report, _request(limit=1))

    assert _codes(preview) == [VALID_NORM], (
        "eight inadmissible graph candidates consumed the single available slot "
        "and the one authorised letter never reached the report"
    )
    assert preview.metrics["total_linked"] == 1
    for _raw, norm in contaminants:
        assert norm not in _blob(preview)


# ===========================================================================
# SCENARIO F - the positive path, end to end
# ===========================================================================


def test_scenario_f_a_clean_source_publishes_reads_exports_and_then_retracts(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A system that returns empty is not a security success.

    Writer -> valid state -> reader -> report -> CSV -> drafting evidence, and
    only then the retraction when authority changes.
    """
    documents = [
        _document(
            document_id="doc-valid",
            letter_no=VALID_CODE,
            norm=VALID_NORM,
            references=[{"letterNo": ROOT_CODE}],
        ),
        _document(document_id="doc-root", letter_no=ROOT_CODE, norm=ROOT_NORM),
    ]
    for document in documents:
        _sync(graph, document)

    # State is valid on the migration's own terms: identity-only nodes, every
    # authority-controlled edge owned.
    findings = migration.run_migration(
        client=_state(graph),
        index=migration.CanonicalReferenceIndex.from_mongo_documents(documents),
        dry_run=True,
    )
    assert findings.nodes_with_forbidden_properties == 0
    assert findings.unresolved == 0 and findings.owned_invalid == 0
    assert findings.owned_valid == 1

    report, db = _wire(monkeypatch, graph, documents)
    preview = _preview(report)
    assert _codes(preview) == [VALID_NORM]
    assert preview.metrics["total_linked"] == 1
    assert CANONICAL_MARKER in _blob(preview)

    report, db = _wire(monkeypatch, graph, documents)
    payload, filename = _download(report)
    assert filename.startswith("linked-letter-chain-")
    assert VALID_NORM in payload.decode("utf-8")

    codes, sources = asyncio.run(_drafting_evidence(graph, db, ROOT_CODE))
    assert VALID_NORM in codes
    assert all(source.label == "Graph-linked letter" for source in sources)

    # Authority changes, and the same clean path now yields nothing for it -
    # while the node itself is still there.
    withdrawn = _blocked(documents, "doc-valid")
    report, _db = _wire(monkeypatch, graph, withdrawn)
    after = _preview(report)
    assert _codes(after) == []
    assert after.metrics["total_linked"] == 0
    assert VALID_NORM in _node_norms(graph)


# ===========================================================================
# The production graphs are never a target of anything above.
# ===========================================================================


def test_the_umbrella_cannot_reach_a_business_graph_on_this_engine(graph: Any) -> None:
    """Asserted against the live engine, not against a constant.

    Every migration call in this file goes through `run_migration`, whose first
    statement is the target guard. This proves the guard is real here rather
    than trusting that no test typed the wrong name.
    """
    client = graph._get_client()
    live = {str(name) for name in client.execute_command("GRAPH.LIST")}
    for business in ("G", "contraclaim"):
        if business not in live:
            continue
        with pytest.raises(migration.MigrationTargetRefused):
            migration.run_migration(
                client=migration.FalkorStateClient(client, business),
                index=migration.CanonicalReferenceIndex(),
                dry_run=True,
            )
