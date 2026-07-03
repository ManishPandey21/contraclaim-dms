"""Tests for BOQ (Bill of Quantities) table structuring: header detection,
row parsing, whole-table detection (with/without header), and the agent
producing a structured ``table`` clause record with parsed rows linked to the
nearest clause.
"""

from __future__ import annotations

from rbac_backend.services.contract_clause import (
    ClauseChunkingAgent,
    DetectedClause,
    DocumentScope,
    detect_boq_tables,
    is_boq_header,
    parse_boq_row,
)


# --------------------------------------------------------------------------- #
# Header + row parsing
# --------------------------------------------------------------------------- #
def test_header_detection():
    assert is_boq_header("Item  Description  Unit  Qty  Rate  Amount")
    assert is_boq_header("Sl. No.   Particulars   Unit   Quantity   Rate   Amount")
    assert not is_boq_header("The Contractor shall complete the works.")


def test_parse_full_boq_row():
    row = parse_boq_row("1.1  Excavation in ordinary soil  cum  1250.5  350.00  437,675.00")
    assert row is not None
    assert row.item_no == "1.1"
    assert row.description == "Excavation in ordinary soil"
    assert row.unit.lower() == "cum"
    assert row.quantity == 1250.5
    assert row.rate == 350.0
    assert row.amount == 437675.0


def test_parse_row_without_item_code():
    row = parse_boq_row("Providing and laying PCC  m3  10  4500  45000")
    assert row is not None
    assert row.item_no is None
    assert row.unit.lower() == "m3"
    assert row.amount == 45000.0


def test_non_row_returns_none():
    assert parse_boq_row("This is a normal sentence with no columns.") is None
    assert parse_boq_row("(a) a lettered sub-item, not a BOQ row") is None


# --------------------------------------------------------------------------- #
# Whole-table detection
# --------------------------------------------------------------------------- #
def test_detect_boq_table_with_header():
    text = (
        "Bill of Quantities\n"
        "Item  Description  Unit  Qty  Rate  Amount\n"
        "1  Excavation  cum  100  350  35000\n"
        "2  PCC 1:4:8  m3  20  4500  90000\n"
        "3  Reinforcement  kg  5000  65  325000\n"
        "Total carried forward\n"
    )
    tables = detect_boq_tables([{"page_no": 120, "cleaned_text": text}])
    assert len(tables) == 1
    t = tables[0]
    assert t.page_no == 120
    assert len(t.rows) == 3
    assert t.columns  # header columns captured
    assert t.rows[0].description == "Excavation" and t.rows[0].amount == 35000.0


def test_detect_headerless_boq_run():
    # Table continued onto a new page (no header), still a run of rows.
    text = (
        "4  Shuttering  sqm  800  250  200000\n"
        "5  Plastering  sqm  1200  180  216000\n"
        "6  Painting  sqm  1200  90  108000\n"
    )
    tables = detect_boq_tables([{"page_no": 121, "cleaned_text": text}])
    assert len(tables) == 1
    assert len(tables[0].rows) == 3
    assert tables[0].header is None


def test_single_row_is_not_a_table():
    tables = detect_boq_tables([{"page_no": 1, "cleaned_text": "1  Excavation  cum  100  350  35000\n"}])
    assert tables == []


# --------------------------------------------------------------------------- #
# Agent stores structured BOQ table linked to nearest clause
# --------------------------------------------------------------------------- #
def test_agent_builds_structured_boq_table_record():
    agent = ClauseChunkingAgent(db=None)
    pages = [
        {
            "page_no": 44,
            "cleaned_text": (
                "Item  Description  Unit  Qty  Rate  Amount\n"
                "1  Excavation  cum  100  350  35000\n"
                "2  PCC  m3  20  4500  90000\n"
            ),
        }
    ]
    boq_tables = ClauseChunkingAgent.detect_boq_tables(pages)
    assert len(boq_tables) == 1 and boq_tables[0].table_type == "boq"

    scope = DocumentScope(
        org_id="o", project_id="p", contract_id="c", document_id="d", document_type="BOQ"
    )
    clauses = [DetectedClause(clause_no="5.1", clause_title="Measurement", text="x", page_start=43)]
    records, summary = agent.build_records(scope, clauses, boq_tables)

    table_recs = [r for r in records if r.chunk_type == "table"]
    assert len(table_recs) == 1
    rec = table_recs[0]
    assert rec.table_type == "boq"
    assert len(rec.table_rows) == 2
    assert rec.table_rows[0]["description"] == "Excavation"
    assert rec.table_rows[0]["amount"] == 35000.0
    assert rec.linked_clause_no == "5.1"       # linked to nearest preceding clause
    assert summary.tables_detected == 1
