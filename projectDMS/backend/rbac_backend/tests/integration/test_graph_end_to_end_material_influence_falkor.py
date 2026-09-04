"""G29/G30 end-to-end: real contaminated FalkorDB state must reach a FINAL
material object without influencing it — in either direction.

Two halves, and neither existed before this file (GRAPH-GATES.md, U6):

* `test_graph_stale_containment_falkor.py` reaches a **real** graph but stops at
  `graph_codes_denied(...)` against an in-file fake — a helper, which G30 field
  12 names as invalid closure evidence.
* `test_graph_consumer_authority.py` reaches a **final** `SourceEvidence` object
  but its graph is a dict-returning fake, so no real node is ever traversed.

So nothing joined *real engine state* to *a final evidence object*. Both halves
below do, against a run-owned disposable graph.

WHAT THIS FILE PINS THAT IS NEW — CAPACITY.
------------------------------------------
G29's assertion is that a stale artifact has **ZERO material influence**.
Withholding a contaminated node's *content* is not zero influence if its mere
presence deletes a legitimate letter from the answer.

`_generate_linked_chain_report` bounded its Falkor traversal with `LIMIT $limit`
and resolved authority afterwards, so inadmissible nodes — blocked, foreign
tenant, or resolving to no Mongo document at all — consumed the candidate window
before `build_scope_query`, `is_consumable` or `graph_codes_denied` were ever
consulted. A report over a real graph then asserted *zero linked letters* while
one genuinely existed, and `metrics["total_linked"]` fell from 1 to 0. The
exported CSV inherits it, because `generate_download` calls `generate_preview`.

The same file already carries the reasoning at the Python cut ("Slicing before
containment lets a denied or out-of-scope code consume a report slot and
silently push a legitimate linked letter out of the CSV — influence without
disclosure"). It was applied to the Python slice and not to the Cypher `LIMIT`
one statement upstream.

ORDER IS NOT A DEFENCE, AND IT IS A KNOWN CLASS HERE.
`publication_policy.graph_codes_denied` documents the identical defect one layer
down: "`find_one` returns an arbitrary document in Mongo natural order, so 'is
any supporter consumable?' became order-dependent". The remedy there was to stop
letting engine order be load-bearing. These tests therefore assert the same
authorised answer under BOTH insertion orders — a fix that merely re-orders
Falkor's traversal would pass one and fail the other.

REAL FALKOR IS REQUIRED. The bug is engine candidate-window semantics; a fake
graph that returns a Python list cannot reproduce it.

Run with a local FalkorDB:
    FALKOR_TEST_HOST=localhost FALKOR_TEST_PORT=6380 \
      pytest backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.report import ReportRequest
from rbac_backend.services import report_service as report_service_module
from rbac_backend.services.report_service import ReportService
from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_port,
)

# Resolved through the shared seam, not from os.environ: under
# CONTRACLAIM_STAGING_GATE the localhost default is refused outright, so a
# staging run cannot silently measure the development FalkorDB.
FALKOR_HOST = falkor_host()
FALKOR_PORT = falkor_port()

pytestmark = pytest.mark.integration

ROOT_CODE = "LTR-ROOT-G-A17"
ROOT_NORM = "ltr-root-g-a17"

#: Sorts AFTER every contaminant code below, so a fix that merely happens to
#: order candidates by identity cannot pass by accident.
VALID_CODE = "LTR-VALID-ZZZ-777"
VALID_NORM = "ltr-valid-zzz-777"
SECOND_VALID_CODE = "LTR-VALID-ZZZ-888"
SECOND_VALID_NORM = "ltr-valid-zzz-888"

IN_SCOPE_MARKER = "ORG_A_LEGITIMATE_LINKED_MARKER_G_A17"
FOREIGN_MARKER = "ORG_B_FOREIGN_MARKER_G_A17"
POISON_MARKER = "POISONED_RAW_GRAPH_VALUE_G_A17_31337"


# ---------------------------------------------------------------------------
# Mongo boundary. The scope/authority queries ARE under test, so they are
# applied rather than ignored — an in-memory collection that returns everything
# would make every assertion below vacuous.
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


# ---------------------------------------------------------------------------
# Real Falkor, run-owned namespace
# ---------------------------------------------------------------------------


def _falkor(graph_name: str):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    service = FalkorGraphService(
        FalkorGraphConfig(
            host=FALKOR_HOST,
            port=FALKOR_PORT,
            graph_name=graph_name,
            password=None,
            enabled=True,
            cleanup=False,
        )
    )
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {FALKOR_HOST}:{FALKOR_PORT}: {exc}")
    return service


@pytest.fixture()
def graph():
    """A disposable graph owned by this run, dropped loudly on the way out."""
    name = disposable_graph_name("materialinfluence")
    service = _falkor(name)
    try:
        yield service
    finally:
        drop_disposable_graph(service._get_client(), name)


def _seed_chain(service: Any, cited_norms: List[str], *, root: str = ROOT_NORM) -> None:
    """`(:Letter {normCode})-[:CITES]->(root)` for each code, IN THE GIVEN ORDER.

    Insertion order is the variable the order-independence tests move, so it is
    controlled here rather than left to whatever the engine happens to do.
    """
    service._execute("CREATE (:Letter {normCode:$norm})", {"norm": root})
    for norm in cited_norms:
        service._execute(
            "MATCH (r:Letter {normCode:$root}) "
            "CREATE (n:Letter {normCode:$norm})-[:CITES]->(r)",
            {"root": root, "norm": norm},
        )


def _poison_node(service: Any, norm: str) -> None:
    """Write the pre-G32 legacy properties an OLDER writer would have left.

    Not a test of the current writer (which no longer writes them) — a test of
    what a reader does when it meets one that is already there.
    """
    service._execute(
        "MATCH (n:Letter {normCode:$norm}) "
        "SET n.subject=$marker, n.summary=$marker, n.text_content=$marker, "
        "n.code=$marker, n.direction=$marker, n.date=$marker, "
        "n.organization_id=$marker, n.project_id=$marker",
        {"norm": norm, "marker": POISON_MARKER},
    )


# ---------------------------------------------------------------------------
# Canonical Mongo documents
# ---------------------------------------------------------------------------


def _document(
    *,
    letter_no: str,
    norm: str,
    organization_id: str = "org-A",
    project_id: str = "proj-A",
    marker: str = IN_SCOPE_MARKER,
    blocked: bool = False,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "_id": f"doc-{norm}",
        "letter_no": letter_no,
        "letterNo": letter_no,
        "letterNoNormalized": norm,
        "organization_id": organization_id,
        "project_id": project_id,
        "subject": f"{marker} SUBJECT {norm}",
        "summary": f"{marker} SUMMARY {norm}",
        "date": "2026-09-01",
        "processing_status": "human_review_required" if blocked else "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    return doc


def _contaminant_codes(count: int, prefix: str) -> List[Tuple[str, str]]:
    """(raw, normalized) pairs that all sort BEFORE the valid codes."""
    return [(f"LTR-{prefix}-{index:03d}", f"ltr-{prefix.lower()}-{index:03d}") for index in range(count)]


def _user(
    roles: Optional[List[str]] = None,
    *,
    organization_id: Optional[str] = "org-A",
    projects: Optional[List[str]] = None,
) -> CurrentUser:
    return CurrentUser(
        id="user-g-a17",
        username="reporter",
        email="reporter@example.com",
        roles=roles or ["orguser"],
        organization_id=organization_id,
        organizations=[organization_id] if organization_id else [],
        projects=projects if projects is not None else ["proj-A"],
    )


def _request(**overrides: Any) -> ReportRequest:
    payload: Dict[str, Any] = {
        "report_id": "linked-letter-chain",
        "letter_no": ROOT_CODE,
        "chain_direction": "up",
        "include_self": False,
        "organization_id": "org-A",
        "limit": 3,
    }
    payload.update(overrides)
    return ReportRequest(**payload)


def _async_value(value: Any):
    async def _coro():
        return value

    return _coro()


def _wire(monkeypatch: pytest.MonkeyPatch, service: Any, db: _Database) -> ReportService:
    """Pin BOTH boundaries: real Falkor on the disposable graph, applied Mongo."""
    monkeypatch.setattr(report_service_module, "get_database", lambda: _async_value(db))
    import rbac_backend.services.falkor_graph_service as falkor_module

    monkeypatch.setattr(falkor_module, "FalkorGraphService", lambda: service)
    return ReportService(db)


def _codes(preview: Any) -> List[str]:
    return [row["letter_no"] for row in preview.rows]


def _serialized(preview: Any) -> str:
    return repr(preview.rows) + repr(preview.metrics)


# ===========================================================================
# CAPACITY — an inadmissible node may not consume a report slot
# ===========================================================================


@pytest.mark.parametrize("valid_position", ["first", "last"])
def test_blocked_graph_candidates_cannot_starve_a_valid_linked_letter(
    graph: Any, monkeypatch: pytest.MonkeyPatch, valid_position: str
) -> None:
    """Ten blocked supporters ahead of the limit, one legitimate letter behind it.

    The blocked documents are real supporters — they resolve, they are simply
    not publishable. Their content is correctly withheld either way; what must
    also be true is that their PRESENCE costs the valid letter nothing.
    """
    contaminants = _contaminant_codes(10, "BLOCKED")
    valid = (VALID_CODE, VALID_NORM)
    ordered = [valid] + contaminants if valid_position == "first" else contaminants + [valid]
    _seed_chain(graph, [norm for _raw, norm in ordered])

    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=3), _user())

    assert _codes(preview) == [VALID_NORM], (
        "blocked graph candidates consumed the bounded candidate window before "
        "any authority was resolved, so a legitimate linked letter vanished "
        f"from the report (valid cited {valid_position})"
    )
    assert preview.metrics["total_linked"] == 1
    assert FOREIGN_MARKER not in _serialized(preview)


@pytest.mark.parametrize("valid_position", ["first", "last"])
def test_foreign_tenant_graph_candidates_cannot_starve_a_valid_linked_letter(
    graph: Any, monkeypatch: pytest.MonkeyPatch, valid_position: str
) -> None:
    """The shared node is keyed on `normCode` alone and is GLOBAL.

    So every tenant's citations join the neighbourhood that consumes one
    tenant's candidate window. The foreign documents here are deliberately
    CLEAN and consumable — only scope can contain them, and scope is resolved
    after the graph cut.
    """
    contaminants = _contaminant_codes(10, "FOREIGN")
    valid = (VALID_CODE, VALID_NORM)
    ordered = [valid] + contaminants if valid_position == "first" else contaminants + [valid]
    _seed_chain(graph, [norm for _raw, norm in ordered])

    documents = [
        _document(
            letter_no=raw,
            norm=norm,
            organization_id="org-B",
            project_id="proj-B",
            marker=FOREIGN_MARKER,
        )
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=3), _user())

    assert _codes(preview) == [VALID_NORM], (
        "another tenant's letters consumed this tenant's candidate window "
        f"(valid cited {valid_position})"
    )
    assert preview.metrics["total_linked"] == 1
    assert FOREIGN_MARKER not in _serialized(preview)


@pytest.mark.parametrize("valid_position", ["first", "last"])
def test_unresolvable_graph_candidates_cannot_starve_a_valid_linked_letter(
    graph: Any, monkeypatch: pytest.MonkeyPatch, valid_position: str
) -> None:
    """Nodes with NO canonical Mongo source at all — pure graph residue.

    This is the class G29 exists for: physical presence in FalkorDB that nothing
    in Mongo supports. It must fail closed, and failing closed must not cost a
    legitimate letter its slot.
    """
    contaminants = _contaminant_codes(10, "ORPHAN")
    valid = (VALID_CODE, VALID_NORM)
    ordered = [valid] + contaminants if valid_position == "first" else contaminants + [valid]
    _seed_chain(graph, [norm for _raw, norm in ordered])

    service = _wire(
        monkeypatch, graph, _Database([_document(letter_no=VALID_CODE, norm=VALID_NORM)])
    )
    preview = _run(service, _request(limit=3), _user())

    assert _codes(preview) == [VALID_NORM], (
        f"unresolvable graph residue starved a legitimate letter (valid cited {valid_position})"
    )
    assert preview.metrics["total_linked"] == 1


def test_a_contaminant_set_larger_than_any_fixed_overfetch_still_admits_the_valid_letter(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multiplier is not a boundary.

    `graph_limit = limit * k` closes nothing unless the number of inadmissible
    preceding candidates has a proven upper bound, and it does not: the shared
    node is global, so the pool is instance-wide. Sixty contaminants against
    `limit=3` exceeds any small multiplier anybody would pick.
    """
    contaminants = _contaminant_codes(60, "BULK")
    # Valid cited FIRST: the engine's observed return order is reverse
    # insertion, so this is the adverse arrangement rather than the lucky one.
    _seed_chain(graph, [VALID_NORM] + [norm for _raw, norm in contaminants])

    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=3), _user())

    assert _codes(preview) == [VALID_NORM]
    assert preview.metrics["total_linked"] == 1


@pytest.mark.parametrize("valid_position", ["first", "last"])
def test_limit_one_admits_the_valid_letter_whatever_the_contaminants(
    graph: Any, monkeypatch: pytest.MonkeyPatch, valid_position: str
) -> None:
    """The sharpest form: one slot, six inadmissible candidates, one valid.

    A correct implementation filters before it cuts, so the single admissible
    candidate occupies the single slot regardless of the order FalkorDB returns
    neighbours in. Before the fix these two parametrisations DISAGREED, which is
    the whole point — the report's correctness depended on engine order.
    """
    contaminants = _contaminant_codes(6, "ADVERSARIAL")
    valid = (VALID_CODE, VALID_NORM)
    ordered = [valid] + contaminants if valid_position == "first" else contaminants + [valid]
    _seed_chain(graph, [norm for _raw, norm in ordered])

    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=1), _user())

    assert _codes(preview) == [VALID_NORM]
    assert preview.metrics["total_linked"] == 1


def test_the_authorised_report_is_identical_under_reversed_insertion_order(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same logical graph, two insertion orders, one answer.

    Load-bearing: a "fix" that re-orders the traversal so the valid node happens
    to survive is not a fix. Correctness may not depend on the order the engine
    returns neighbours in — the identical defect, and the identical remedy, are
    already documented one layer down in `graph_codes_denied`.
    """
    contaminants = _contaminant_codes(8, "ORDER")
    valid = [(VALID_CODE, VALID_NORM), (SECOND_VALID_CODE, SECOND_VALID_NORM)]
    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents += [_document(letter_no=raw, norm=norm) for raw, norm in valid]

    forward = valid + contaminants
    reverse = list(reversed(forward))

    previews = []
    for ordering, root in ((forward, ROOT_NORM), (reverse, ROOT_NORM + "-b")):
        _seed_chain(graph, [norm for _raw, norm in ordering], root=root)
        service = _wire(monkeypatch, graph, _Database(documents))
        previews.append(
            _run(service, _request(limit=2, letter_no=root.upper()), _user())
        )

    assert _codes(previews[0]) == _codes(previews[1]) == [VALID_NORM, SECOND_VALID_NORM]
    assert previews[0].metrics["total_linked"] == previews[1].metrics["total_linked"] == 2


def test_total_linked_counts_authorised_rows_not_raw_graph_candidates(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metric asserts how many linked letters SUPPORT this chain.

    Counting raw candidates, or candidates limited before authority, makes it a
    statement about graph residue instead — and under-reporting an evidence
    chain is material in a product whose output is contractual claims.
    """
    contaminants = _contaminant_codes(12, "COUNT")
    _seed_chain(
        graph,
        [VALID_NORM, SECOND_VALID_NORM] + [norm for _raw, norm in contaminants],
    )
    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents += [
        _document(letter_no=VALID_CODE, norm=VALID_NORM),
        _document(letter_no=SECOND_VALID_CODE, norm=SECOND_VALID_NORM),
    ]

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=5), _user())

    assert preview.metrics["total_linked"] == 2
    assert preview.total_rows == 2
    assert sorted(_codes(preview)) == [VALID_NORM, SECOND_VALID_NORM]


def test_duplicate_graph_edges_to_one_code_consume_one_slot(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several `CITES` edges to the same code are one candidate, not several.

    Paging a candidate set makes duplicate suppression load-bearing: a code seen
    on two pages must not be admitted twice, and must not spend the window twice
    either.
    """
    contaminants = _contaminant_codes(4, "DUPE")
    _seed_chain(graph, [VALID_NORM] + [norm for _raw, norm in contaminants])
    # A second and third citation of the SAME valid code.
    for _ in range(2):
        graph._execute(
            "MATCH (r:Letter {normCode:$root}), (n:Letter {normCode:$norm}) "
            "CREATE (n)-[:REPLIES_TO]->(r)",
            {"root": ROOT_NORM, "norm": VALID_NORM},
        )

    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=2), _user())

    assert _codes(preview) == [VALID_NORM]
    assert preview.metrics["total_linked"] == 1


# ===========================================================================
# TERMINATION AND POSITIVE CONTROL
# ===========================================================================


def test_every_candidate_inadmissible_terminates_with_an_empty_chain(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhaustion, not a loop, and not a widened scope.

    The failure mode a paging fix invites is scanning forever looking for an
    admissible row that does not exist. Twenty-five inadmissible candidates and
    no valid one must terminate on graph exhaustion with an honest empty answer.
    """
    contaminants = _contaminant_codes(25, "ALLBAD")
    _seed_chain(graph, [norm for _raw, norm in contaminants])
    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=5), _user())

    assert preview.rows == []
    assert preview.metrics["total_linked"] == 0
    assert FOREIGN_MARKER not in _serialized(preview)


def test_fewer_authorised_candidates_than_requested_is_not_an_error(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for five and finding two returns two."""
    _seed_chain(graph, [VALID_NORM, SECOND_VALID_NORM])
    documents = [
        _document(letter_no=VALID_CODE, norm=VALID_NORM),
        _document(letter_no=SECOND_VALID_CODE, norm=SECOND_VALID_NORM),
    ]

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=5), _user())

    assert sorted(_codes(preview)) == [VALID_NORM, SECOND_VALID_NORM]
    assert preview.metrics["total_linked"] == 2


def test_a_clean_graph_neighbourhood_still_enriches_the_report(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control. Starvation must not be "fixed" by serving nothing.

    Enrichment still comes from the canonical Mongo document, and the row still
    carries its subject and summary.
    """
    _seed_chain(graph, [VALID_NORM, SECOND_VALID_NORM])
    documents = [
        _document(letter_no=VALID_CODE, norm=VALID_NORM),
        _document(letter_no=SECOND_VALID_CODE, norm=SECOND_VALID_NORM),
    ]

    service = _wire(monkeypatch, graph, _Database(documents))
    preview = _run(service, _request(limit=3), _user())

    assert preview.metrics["total_linked"] == 2
    assert all(IN_SCOPE_MARKER in row["subject"] for row in preview.rows)
    assert all(IN_SCOPE_MARKER in row["summary"] for row in preview.rows)
    assert all(row["organization_id"] == "org-A" for row in preview.rows)


def test_a_stale_node_left_in_place_stops_counting_when_authority_changes(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G29's own assertion, at a final object, with the artefact still present.

    The graph is not touched between the two reports. Only Mongo authority
    changes. The retracted letter must disappear AND the slot it vacated must go
    to the next legitimate letter rather than to the residue that outnumbers it.
    """
    contaminants = _contaminant_codes(6, "STALE")
    _seed_chain(
        graph,
        [VALID_NORM, SECOND_VALID_NORM] + [norm for _raw, norm in contaminants],
    )
    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents += [
        _document(letter_no=VALID_CODE, norm=VALID_NORM),
        _document(letter_no=SECOND_VALID_CODE, norm=SECOND_VALID_NORM),
    ]

    service = _wire(monkeypatch, graph, _Database(documents))
    first = _run(service, _request(limit=1), _user())
    assert _codes(first) == [VALID_NORM]

    # Canonical authority changes; the graph artefact is deliberately left alone.
    blocked = deepcopy(documents)
    for row in blocked:
        if row["letterNoNormalized"] == VALID_NORM:
            row["processing_status"] = "human_review_required"

    service = _wire(monkeypatch, graph, _Database(blocked))
    second = _run(service, _request(limit=1), _user())

    assert _codes(second) == [SECOND_VALID_NORM], (
        "the retracted letter's slot went to graph residue instead of to the "
        "next legitimate letter"
    )
    assert second.metrics["total_linked"] == 1


def test_the_exported_csv_carries_the_valid_letter_and_no_contaminant(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`generate_download` calls `generate_preview`, so the starved row set was
    what the CSV contained. Pinned at the exported bytes, not just the preview."""
    contaminants = _contaminant_codes(8, "CSV")
    _seed_chain(graph, [VALID_NORM] + [norm for _raw, norm in contaminants])
    documents = [
        _document(letter_no=raw, norm=norm, marker=FOREIGN_MARKER, blocked=True)
        for raw, norm in contaminants
    ]
    documents.append(_document(letter_no=VALID_CODE, norm=VALID_NORM))

    service = _wire(monkeypatch, graph, _Database(documents))
    payload, filename = _run_download(service, _request(limit=2), _user())
    text = payload.decode("utf-8")

    assert filename.startswith("linked-letter-chain-")
    assert VALID_NORM in text
    assert FOREIGN_MARKER not in text
    for _raw, norm in contaminants:
        assert norm not in text


# ===========================================================================
# G30 half — a real contaminated node cannot become drafting evidence
# ===========================================================================
#
# The other side of U6: `test_graph_consumer_authority.py` asserts on final
# `SourceEvidence` objects but never traverses a real node. These four do both.


async def _drafting_evidence(service: Any, db: Any, code: str):
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    builder = DraftContextBuilder(
        document_service=None,
        conversation_service=None,
        contract_service=SimpleNamespace(),
        graph_service=service,
        db=db,
    )
    letter = SimpleNamespace(letter_no=code, organization_id="org-A", project_id="proj-A")
    request = SimpleNamespace(include_letter_codes=[], exclude_letter_codes=[])
    return await builder._graph_sources(letter, request, "org-A", "proj-A", [])


async def test_a_blocked_real_node_never_becomes_a_source_evidence(graph: Any) -> None:
    _seed_chain(graph, [VALID_NORM, "ltr-blocked-drafting-001"])
    db = _Database(
        [
            _document(letter_no=VALID_CODE, norm=VALID_NORM),
            _document(
                letter_no="LTR-BLOCKED-DRAFTING-001",
                norm="ltr-blocked-drafting-001",
                marker=FOREIGN_MARKER,
                blocked=True,
            ),
        ]
    )
    codes, sources = await _drafting_evidence(graph, db, ROOT_CODE)

    assert "ltr-blocked-drafting-001" not in codes
    assert VALID_NORM in codes, "the graph leg must genuinely run, not return nothing"
    assert FOREIGN_MARKER not in repr(sources)


async def test_an_unresolvable_real_node_fails_closed_in_drafting(graph: Any) -> None:
    _seed_chain(graph, [VALID_NORM, "ltr-orphan-drafting-001"])
    db = _Database([_document(letter_no=VALID_CODE, norm=VALID_NORM)])
    codes, _sources = await _drafting_evidence(graph, db, ROOT_CODE)

    assert "ltr-orphan-drafting-001" not in codes
    assert VALID_NORM in codes


async def test_legacy_properties_on_a_real_node_cannot_become_content(graph: Any) -> None:
    """G32 failure mode (b) against a real node: a stale `subject` left by an
    older writer is contamination owned by whichever document wrote last."""
    _seed_chain(graph, [VALID_NORM])
    _poison_node(graph, VALID_NORM)
    db = _Database([_document(letter_no=VALID_CODE, norm=VALID_NORM)])
    codes, sources = await _drafting_evidence(graph, db, ROOT_CODE)

    assert VALID_NORM in codes
    assert POISON_MARKER not in repr(sources), (
        "a legacy property still physically present on the shared node reached "
        "a final SourceEvidence object"
    )
    for source in sources:
        assert source.snippet is None
        assert set(source.metadata or {}) <= {"normCode"}


async def test_a_clean_real_node_still_becomes_drafting_evidence(graph: Any) -> None:
    """Positive control: denial must not over-fire into serving nothing."""
    _seed_chain(graph, [VALID_NORM, SECOND_VALID_NORM])
    db = _Database(
        [
            _document(letter_no=VALID_CODE, norm=VALID_NORM),
            _document(letter_no=SECOND_VALID_CODE, norm=SECOND_VALID_NORM),
        ]
    )
    codes, sources = await _drafting_evidence(graph, db, ROOT_CODE)

    assert VALID_NORM in codes and SECOND_VALID_NORM in codes
    assert all(source.label == "Graph-linked letter" for source in sources)


# ---------------------------------------------------------------------------
# The report generator is async; these tests are sync because their fixture
# owns a real Falkor connection. `conftest.pytest_pyfunc_call` gives each
# coroutine test its own loop, so a sync test drives its own.
# ---------------------------------------------------------------------------


def _run(service: ReportService, request: ReportRequest, user: CurrentUser):
    import asyncio

    return asyncio.run(service.generate_preview(request, user))


def _run_download(service: ReportService, request: ReportRequest, user: CurrentUser):
    import asyncio

    return asyncio.run(service.generate_download(request, user))
