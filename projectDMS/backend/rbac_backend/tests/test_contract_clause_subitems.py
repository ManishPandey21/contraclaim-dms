"""Tests for (a)/(i) sub-item hierarchy: numbering path helpers, the sub-item
detector's letter/roman classification, and the agent producing parent + child
clause records with a correct parent-child hierarchy.
"""

from __future__ import annotations

from rbac_backend.services.contract_clause import (
    ClauseChunkingAgent,
    ClauseStorageService,
    DocumentScope,
    detect_subitems,
)


# --------------------------------------------------------------------------- #
# Numbering path helpers understand paren sub-items
# --------------------------------------------------------------------------- #
def test_clause_path_for_lettered_subitem():
    assert ClauseStorageService.clause_path("8.4(a)") == ["8", "8.4", "8.4(a)"]
    assert ClauseStorageService.parent_clause_no("8.4(a)") == "8.4"
    assert ClauseStorageService.level("8.4(a)") == 3


def test_clause_path_for_nested_roman_subitem():
    assert ClauseStorageService.clause_path("8.4(b)(i)") == ["8", "8.4", "8.4(b)", "8.4(b)(i)"]
    assert ClauseStorageService.parent_clause_no("8.4(b)(i)") == "8.4(b)"
    assert ClauseStorageService.level("8.4(b)(i)") == 4


def test_plain_and_structural_numbers_unchanged():
    assert ClauseStorageService.clause_path("8.4") == ["8", "8.4"]
    assert ClauseStorageService.clause_path("Appendix A") == ["Appendix A"]


# --------------------------------------------------------------------------- #
# Sub-item detector: letters, romans, ambiguity, nesting
# --------------------------------------------------------------------------- #
def test_detects_lettered_items():
    text = "The Contractor shall:\n(a) do X;\n(b) do Y;\n(c) do Z."
    items = detect_subitems(text, "8.4")
    assert [i.clause_no for i in items] == ["8.4(a)", "8.4(b)", "8.4(c)"]
    assert all(i.kind == "letter" and i.parent_no == "8.4" for i in items)


def test_romans_nest_under_current_letter():
    text = "(a) first;\n(b) second, provided that:\n(i) notice given;\n(ii) records kept."
    items = detect_subitems(text, "8.4")
    by_no = {i.clause_no: i for i in items}
    assert set(by_no) == {"8.4(a)", "8.4(b)", "8.4(b)(i)", "8.4(b)(ii)"}
    assert by_no["8.4(b)(i)"].kind == "roman"
    assert by_no["8.4(b)(i)"].parent_no == "8.4(b)"
    assert by_no["8.4(b)(ii)"].parent_no == "8.4(b)"


def test_ambiguous_i_continues_letter_run():
    # …(g),(h),(i) is a letter run, not roman.
    text = "(g) g;\n(h) h;\n(i) i;\n(j) j."
    items = detect_subitems(text, "5")
    kinds = {i.clause_no: i.kind for i in items}
    assert kinds["5(i)"] == "letter"
    assert [i.clause_no for i in items] == ["5(g)", "5(h)", "5(i)", "5(j)"]


def test_single_marker_is_not_a_list():
    assert detect_subitems("(a) only one marker here", "8.4") == []


# --------------------------------------------------------------------------- #
# Agent produces parent + child records with a real hierarchy
# --------------------------------------------------------------------------- #
def test_agent_builds_parent_and_child_records():
    agent = ClauseChunkingAgent(db=None)
    text = (
        "8.4 Extension of Time\n"
        "The Contractor may claim an extension if:\n"
        "(a) the delay is caused by a Variation;\n"
        "(b) the delay results from adverse weather, provided that:\n"
        "(i) notice is given within 14 days; and\n"
        "(ii) records are maintained."
    )
    cleaned, index = agent._assemble_pages([{"page_no": 1, "cleaned_text": text}])
    detected = agent._detect_clauses(cleaned, index)

    nos = [d.clause_no for d in detected]
    assert "8.4" in nos
    assert "8.4(a)" in nos and "8.4(b)" in nos
    assert "8.4(b)(i)" in nos and "8.4(b)(ii)" in nos

    scope = DocumentScope(
        org_id="o", project_id="p", contract_id="c", document_id="d", document_type="GCC"
    )
    records, _ = agent.build_records(scope, detected)
    by_no = {r.clause_no: r for r in records}
    assert by_no["8.4(a)"].parent_clause_no == "8.4"
    assert by_no["8.4(a)"].level == 3
    assert by_no["8.4(b)(i)"].parent_clause_no == "8.4(b)"
    assert by_no["8.4(b)(i)"].level == 4
    # Distinct records / ids for parent and children.
    assert len({r.clause_uid for r in records}) == len(records)
    # Sub-item source spans populated for viewer jumps.
    assert by_no["8.4(a)"].char_start is not None
