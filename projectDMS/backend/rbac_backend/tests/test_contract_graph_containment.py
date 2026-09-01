"""Graph containment, stable clause identity, and the priority inertness guard.

A-16 - no ordering by graph priority for contract evidence.
C08-06 - stale graph type/priority cannot gain authority.

Why A-16 has to be a **structural** guard rather than a behavioural one: today
`priority` and `section_type` have no ordering, filtering or scoring path into
contract evidence, so an assertion of the form "results are not ordered by
priority" passes for free and would keep passing after someone added the
ordering back in a place the fixture does not exercise. The guard therefore
reads the retrieval sources and fails when either field acquires such a path.

That guard is deliberately precise about what it forbids. `section_type` is
currently read once, by `retrieval/service.py`, purely to populate the display
field `section_heading`. That is a passthrough, not authority, and banning every
mention of the string would either fail on day one or force the guard to be
weakened later - both worse than naming the real hazard: **ordering, filtering
and scoring**.

The graph executor here is a bounded evaluator of the two query shapes the
production reader actually emits. It applies the WHERE conjuncts and only then
truncates to `LIMIT`, which is the ordering under test. Its value is established
by the mutations: moving the eligible predicate out of the query, or applying it
after the limit in Python, both turn the capacity test red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from rbac_backend.services.contract_graph_containment import (
    GraphContainmentResult,
    GraphEligibilityUnresolved,
    compose_graph_document_scope,
    contained_related_clauses,
    stable_clause_node_id,
)
from rbac_backend.services.contract_graph_service import (
    ClauseGraphPayload,
    ContractGraphService,
    DocumentGraphPayload,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# a bounded evaluator of the production query shapes
# --------------------------------------------------------------------------- #


class FakeFalkor:
    """Evaluates the reader's WHERE conjuncts, then applies LIMIT.

    Only the conjunct forms the production reader emits are understood. An
    unrecognised conjunct raises rather than being ignored, so a production
    change that this evaluator cannot model fails loudly instead of silently
    passing.
    """

    def __init__(self, clauses: List[Dict[str, Any]]) -> None:
        self.clauses = clauses
        self.queries: List[str] = []
        self.enabled = True

    # -- transport surface used by ContractGraphService ---------------------- #

    def is_enabled(self) -> bool:
        return self.enabled

    def _parse_rows(self, rows: Any) -> List[Dict[str, Any]]:
        return list(rows)

    def _execute(self, cypher: str, params: Dict[str, Any], read_only: bool = False):
        self.queries.append(cypher)
        rows = [
            row
            for row in self.clauses
            if row.get("organization_id") == params.get("organization_id")
            and row.get("project_id") == params.get("project_id")
        ]

        seeds = {
            row["clause_number"]
            for row in rows
            if row["clause_number"] in (params.get("seed_clause_numbers") or [])
        }

        if "section_sibling" in cypher:
            seed_docs = {
                row["doc_id"]
                for row in rows
                if row["clause_number"] in seeds
                and (
                    not params.get("has_seed_document_ids")
                    or row["doc_id"] in (params.get("seed_document_ids") or [])
                )
            }
            seed_sections = {
                row["section_id"]
                for row in rows
                if row["clause_number"] in seeds and row["doc_id"] in seed_docs
            }
            rows = [row for row in rows if row["section_id"] in seed_sections]
        else:
            rows = [row for row in rows if row["clause_number"] in seeds]
            if params.get("has_seed_document_ids"):
                rows = [
                    row
                    for row in rows
                    if row["doc_id"] not in (params.get("seed_document_ids") or [])
                ]

        rows = [row for row in rows if row.get("is_active") is True]
        rows = self._apply_eligible_conjunct(cypher, params, rows)

        # Ordering is whatever the store returns; the query has no ORDER BY.
        # LIMIT is applied last, which is exactly the capacity the eligible
        # predicate has to be protected from.
        limited = rows[: params["limit"]]
        return [
            {
                "document_id": row["doc_id"],
                "clause_id": row.get("clause_id"),
                "clause_number": row["clause_number"],
                "clause_title": row.get("title"),
                "text": row.get("text_content"),
                "page_number": row.get("page_number"),
                "section_type": row.get("section_type"),
                "priority": row.get("priority"),
                "clause_node_id": row.get("clause_node_id"),
                "graph_relation": "section_sibling"
                if "section_sibling" in cypher
                else "same_clause_number",
            }
            for row in limited
        ]

    @staticmethod
    def _apply_eligible_conjunct(cypher, params, rows):
        """Honour an eligible-document conjunct only if the query carries one."""
        if "$eligible_document_ids" not in cypher:
            return rows
        if not params.get("has_eligible_document_ids"):
            return rows
        allowed = set(params.get("eligible_document_ids") or [])
        return [row for row in rows if row["doc_id"] in allowed]


def _clause(doc_id, clause_number, *, section_id, is_active=True, **extra):
    row = {
        "doc_id": doc_id,
        "clause_number": clause_number,
        "clause_id": f"{doc_id}-{clause_number}",
        "section_id": section_id,
        "organization_id": "org-1",
        "project_id": "project-1",
        "is_active": is_active,
        "title": None,
        "text_content": f"text for {doc_id} {clause_number}",
        "page_number": 1,
        "clause_node_id": f"{doc_id}:{clause_number}",
    }
    row.update(extra)
    return row


def _service(clauses):
    falkor = FakeFalkor(clauses)
    return ContractGraphService(falkor=falkor), falkor


# --------------------------------------------------------------------------- #
# A-16 structural guard
# --------------------------------------------------------------------------- #

#: Sources that can put a graph field on the contract evidence path.
_RETRIEVAL_SOURCES = (
    "services/contract_graph_service.py",
    "services/contract_service.py",
    "retrieval/service.py",
    "services/contract_graph_containment.py",
)

_ORDERING_PATTERNS = (
    re.compile(r"ORDER\s+BY[^\n]*\b(priority|section_type)\b", re.IGNORECASE),
    re.compile(r"\bsort\s*\([^)]*\b(priority|section_type)\b", re.IGNORECASE),
    re.compile(r"sorted\s*\([^)]*\b(priority|section_type)\b", re.IGNORECASE),
    re.compile(r"key\s*=\s*lambda[^\n:]*:[^\n]*\b(priority|section_type)\b"),
    re.compile(r"\bscore\b[^\n]*\b(priority|section_type)\b", re.IGNORECASE),
    re.compile(r"\b(priority|section_type)\b[^\n]*\bscore\b", re.IGNORECASE),
)


def _read(relative: str) -> str:
    return (BACKEND_ROOT / relative).read_text(encoding="utf-8")


def test_graph_priority_has_no_ordering_or_scoring_path_into_contract_evidence():
    """A-16. Structural, because inertness makes a behavioural check vacuous."""
    offenders = []
    for relative in _RETRIEVAL_SOURCES:
        source = _read(relative)
        for pattern in _ORDERING_PATTERNS:
            for match in pattern.finditer(source):
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{relative}:{line}: {match.group(0).strip()}")
    assert offenders == [], (
        "graph priority/section_type acquired an ordering, sorting or scoring "
        "path into contract evidence: " + "; ".join(offenders)
    )


def test_graph_candidate_builder_still_discards_priority_and_section_type():
    """The candidate dict is the last place a graph field could sneak through."""
    source = _read("services/contract_service.py")
    tree = ast.parse(source)
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_graph_candidates"
    )
    emitted = {
        node.value
        for node in ast.walk(builder)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "priority" not in emitted
    assert "section_type" not in emitted


def test_contract_evidence_rows_never_carry_a_priority_key():
    """Final state, not source shape: whatever the graph returns, priority dies."""
    clauses = [
        _clause("doc-a", "10.1", section_id="s1", section_type="SCC", priority=1),
        _clause("doc-b", "10.1", section_id="s2", section_type="GCC", priority=2),
    ]
    service, _ = _service(clauses)

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-a", "doc-b"],
        limit=10,
    )

    assert result.matches
    for match in result.matches:
        assert "priority" not in match
        assert "section_type" not in match


# --------------------------------------------------------------------------- #
# the writer stops producing filename-derived classification
# --------------------------------------------------------------------------- #


class RecordingFalkor:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.enabled = True

    def is_enabled(self) -> bool:
        return self.enabled

    def _parse_rows(self, rows):
        return list(rows or [])

    def _execute(self, cypher, params=None, read_only=False):
        self.calls.append({"cypher": cypher, "params": dict(params or {})})
        return []


def _upsert_once(section_type: Optional[str], priority: int):
    falkor = RecordingFalkor()
    service = ContractGraphService(falkor=falkor)
    document = DocumentGraphPayload(
        doc_id="doc-a",
        title="Agreement",
        version="1",
        organization_id="org-1",
        project_id="project-1",
        section_type=section_type,
        priority=priority,
    )
    clause = ClauseGraphPayload(
        clause_id="c-1",
        clause_number="10.1",
        title="Variations",
        text_content="body",
        page_number=3,
        section_type=section_type,
        priority=priority,
        is_active=True,
    )
    service.upsert_contract_graph(document=document, clauses=[clause])
    return falkor


def test_new_projections_stop_writing_filename_derived_section_type_and_priority():
    falkor = _upsert_once("SCC", 1)

    for call in falkor.calls:
        assert "priority" not in call["params"], call["cypher"]
        assert "section_type" not in call["params"], call["cypher"]
        assert "c.priority" not in call["cypher"]
        assert "c.section_type" not in call["cypher"]
        assert "s.priority" not in call["cypher"]


def test_clause_node_identity_does_not_encode_the_classification():
    """DEBT-03. The identity must survive a reclassification."""
    scc = _upsert_once("SCC", 1)
    gcc = _upsert_once("GCC", 2)

    def clause_ids(falkor):
        return {
            call["params"]["clause_node_id"]
            for call in falkor.calls
            if "clause_node_id" in call["params"]
        }

    assert clause_ids(scc) == clause_ids(gcc)
    assert clause_ids(scc)


def test_stable_identity_helper_rejects_a_classification_component():
    import inspect

    parameters = set(inspect.signature(stable_clause_node_id).parameters)
    assert parameters == {"doc_id", "clause_id"}

    identity = stable_clause_node_id(doc_id="doc-a", clause_id="c-1")
    assert "SCC" not in identity
    assert "GCC" not in identity


def test_a_reclassified_document_does_not_fork_into_a_second_clause_identity():
    first = stable_clause_node_id(doc_id="doc-a", clause_id="c-1")
    second = stable_clause_node_id(doc_id="doc-a", clause_id="c-1")
    assert first == second


# --------------------------------------------------------------------------- #
# containment before the limit
# --------------------------------------------------------------------------- #


def test_foreign_graph_candidates_cannot_consume_the_limit():
    """Four well-connected but ineligible clauses, one applicable, LIMIT 3."""
    clauses = [_clause(f"doc-foreign-{index}", "10.1", section_id="s1") for index in range(4)]
    clauses.append(_clause("doc-applicable", "10.1", section_id="s1"))
    service, falkor = _service(clauses)

    # Baseline: uncontained expansion spends the whole limit on foreign rows.
    uncontained = service.find_related_clauses(
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        limit=3,
    )
    assert len(uncontained) == 3
    assert "doc-applicable" not in {row["document_id"] for row in uncontained}

    contained = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-applicable"],
        limit=3,
    )
    assert {match["document_id"] for match in contained.matches} == {"doc-applicable"}


def test_eligible_predicate_travels_in_the_query_not_in_python():
    clauses = [_clause("doc-a", "10.1", section_id="s1")]
    service, falkor = _service(clauses)

    contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-a"],
        limit=5,
    )

    assert falkor.queries
    for cypher in falkor.queries:
        assert "$eligible_document_ids" in cypher
        # Before the limit, or it protects results and not candidates.
        assert cypher.index("$eligible_document_ids") < cypher.index("LIMIT")


def test_eligible_set_is_never_truncated_the_way_seed_lists_are():
    """Silently dropping eligible ids would widen the answer, not narrow it."""
    eligible = [f"doc-{index}" for index in range(50)]
    clauses = [_clause("doc-49", "10.1", section_id="s1")]
    service, falkor = _service(clauses)

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=eligible,
        limit=5,
    )
    assert {match["document_id"] for match in result.matches} == {"doc-49"}


# --------------------------------------------------------------------------- #
# C08-06: stale graph state cannot authorise itself
# --------------------------------------------------------------------------- #


def test_stale_graph_type_and_priority_cannot_gain_authority():
    """C08-06. A node claiming SCC/priority 1 is still excluded when ineligible."""
    clauses = [
        _clause(
            "doc-stale",
            "10.1",
            section_id="s1",
            section_type="SCC",
            priority=1,
            contract_id="contract-1",
        ),
        _clause("doc-live", "10.1", section_id="s1", section_type=None, priority=5),
    ]
    service, _ = _service(clauses)

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-live"],
        limit=10,
    )

    assert {match["document_id"] for match in result.matches} == {"doc-live"}


def test_graph_contract_id_property_cannot_admit_an_ineligible_document():
    clauses = [
        _clause("doc-not-eligible", "10.1", section_id="s1", contract_id="contract-1"),
    ]
    service, _ = _service(clauses)

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-eligible"],
        limit=10,
    )
    assert result.matches == []


# --------------------------------------------------------------------------- #
# empty, none, intersection
# --------------------------------------------------------------------------- #


def test_empty_eligible_set_issues_no_graph_query_and_is_valid_empty():
    service, falkor = _service([_clause("doc-a", "10.1", section_id="s1")])

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=[],
        limit=5,
    )

    assert falkor.queries == []
    assert result.matches == []
    assert result.valid_empty is True
    assert result.degraded is False


def test_none_eligibility_raises_rather_than_traversing_the_graph():
    service, falkor = _service([_clause("doc-a", "10.1", section_id="s1")])

    with pytest.raises(GraphEligibilityUnresolved):
        contained_related_clauses(
            service,
            organization_id="org-1",
            project_id="project-1",
            seed_clause_numbers=["10.1"],
            eligible_document_ids=None,
            limit=5,
        )

    assert falkor.queries == []


def test_missing_tenant_scope_raises_rather_than_traversing_the_graph():
    service, falkor = _service([_clause("doc-a", "10.1", section_id="s1")])

    with pytest.raises(GraphEligibilityUnresolved):
        contained_related_clauses(
            service,
            organization_id=None,
            project_id="project-1",
            seed_clause_numbers=["10.1"],
            eligible_document_ids=["doc-a"],
            limit=5,
        )

    assert falkor.queries == []


def test_caller_document_narrowing_is_intersected_not_replaced():
    assert compose_graph_document_scope(["D1"], ["D1", "D2"]) == ["D1"]
    assert compose_graph_document_scope(["D9"], ["D1", "D2"]) == []
    assert sorted(compose_graph_document_scope(None, ["D1", "D2"])) == ["D1", "D2"]


def test_caller_asking_for_an_ineligible_document_issues_no_query():
    service, falkor = _service([_clause("doc-a", "10.1", section_id="s1")])

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-a"],
        caller_document_ids=["doc-z"],
        limit=5,
    )

    assert falkor.queries == []
    assert result.valid_empty is True


# --------------------------------------------------------------------------- #
# failure semantics
# --------------------------------------------------------------------------- #


class ExplodingFalkor(FakeFalkor):
    def _execute(self, cypher, params=None, read_only=False):
        raise RuntimeError("falkor unavailable")


def test_graph_failure_is_degraded_and_never_retried_unfenced():
    service = ContractGraphService(falkor=ExplodingFalkor([]))

    result = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-a"],
        limit=5,
    )

    assert result.matches == []
    assert result.degraded is True
    assert result.valid_empty is False
    assert result.failure_reason is not None


def test_valid_empty_graph_failure_and_authority_failure_stay_distinct():
    service, _ = _service([])
    empty = contained_related_clauses(
        service,
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=[],
        limit=5,
    )
    failed = contained_related_clauses(
        ContractGraphService(falkor=ExplodingFalkor([])),
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        eligible_document_ids=["doc-a"],
        limit=5,
    )

    assert (empty.valid_empty, empty.degraded) == (True, False)
    assert (failed.valid_empty, failed.degraded) == (False, True)
    assert isinstance(empty, GraphContainmentResult)


def test_graph_unavailability_cannot_veto_legal_promotion():
    """Classification authority must not import the graph at all."""
    import inspect

    from rbac_backend.services import contract_classification_service

    source = inspect.getsource(contract_classification_service)
    assert "graph" not in source.lower()
    assert "falkor" not in source.lower()


# --------------------------------------------------------------------------- #
# no cutover, no cross-ticket bleed
# --------------------------------------------------------------------------- #


def test_the_graph_capability_is_reachable_only_from_evidence_mode():
    """Ticket 12 switched evidence mode on; nothing else may have moved.

    Replaces the absence assertion this ticket carried before the cutover. The
    graph fence is now live for contract evidence and still absent from the
    generic search path and from the separate retrieval stack.
    """
    import inspect

    from rbac_backend.services import contract_service

    generic = inspect.getsource(contract_service.ContractService.search_contracts)
    assert "contained_related_clauses" not in generic

    evidence = inspect.getsource(contract_service.ContractService._evidence_candidates)
    assert "contained_related_clauses" in evidence

    retrieval = _read("retrieval/service.py")
    assert "contract_graph_containment" not in retrieval


def test_legacy_graph_callers_are_unchanged_when_no_eligible_set_is_supplied():
    """The reader keeps its current behaviour until ticket 12 switches it."""
    clauses = [_clause("doc-a", "10.1", section_id="s1"), _clause("doc-b", "10.1", section_id="s1")]
    service, _ = _service(clauses)

    rows = service.find_related_clauses(
        organization_id="org-1",
        project_id="project-1",
        seed_clause_numbers=["10.1"],
        limit=10,
    )
    assert {row["document_id"] for row in rows} == {"doc-a", "doc-b"}


def test_lexical_and_vector_capabilities_are_untouched_by_graph_work():
    for module_name in ("contract_lexical_containment", "contract_vector_containment"):
        source = _read(f"services/{module_name}.py")
        assert "graph" not in source.lower()
        assert "falkor" not in source.lower()


def test_embedding_generation_change_was_not_absorbed():
    """Ticket 13 owns the embedding input; T11 must not have touched it."""
    from rbac_backend.services import contract_reprojection_worker

    worker = contract_reprojection_worker.ContractReprojectionWorker
    assert not [name for name in dir(worker) if "embed" in name.lower()]
    assert sorted(contract_reprojection_worker.__all__) == [
        "ContractReprojectionWorker",
        "ProjectionClaim",
        "REPROJECTION_CLAIMS_COLLECTION",
        "StaleWorkerGeneration",
    ]
