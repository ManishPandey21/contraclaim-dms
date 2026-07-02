from __future__ import annotations

from rbac_backend.services.contracts_ingest import (
    ClauseExtractor,
    ClauseInfo,
    ContractTextPreprocessor,
    ContractIngestor,
    IngestionConfig,
    ParsedDocument,
    ParsedPage,
)


def test_clause_extractor_preserves_numbered_heading_hierarchy():
    text = """1 General Conditions
The contract applies to the works.

1.1 Notices
Notices must be issued in writing.

1.1.1 Method of Service
Service may be by registered post.

2 Payment
Payment shall follow the certified IPC."""

    clauses = ClauseExtractor().extract_clauses(text)

    assert [clause.clause_number for clause in clauses] == ["1", "1.1", "1.1.1", "2"]
    assert [clause.clause_title for clause in clauses] == [
        "General Conditions",
        "Notices",
        "Method of Service",
        "Payment",
    ]
    assert clauses[0].level == 1
    assert clauses[1].level == 2
    assert clauses[1].parent_number == "1"
    assert clauses[2].level == 3
    assert clauses[2].parent_number == "1.1"
    assert clauses[3].parent_number is None


def test_clause_extractor_preserves_explicit_clause_type_and_title():
    clauses = ClauseExtractor().extract_clauses(
        """CLAUSE 8.4 - Extension of Time
The Contractor shall be entitled to an extension of time where delay is caused by the Employer."""
    )

    assert len(clauses) == 1
    assert clauses[0].clause_type == "clause"
    assert clauses[0].clause_number == "8.4"
    assert clauses[0].clause_title == "Extension of Time"


def test_contract_ingestor_builds_searchable_grounded_clause_payload():
    text = (
        "Preamble text.\n"
        "CLAUSE 8.4 - Extension of Time\n"
        "GCC 8.4 requires notice and substantiation for extension of time.\n"
        "SCC 8.4 amends the Engineer's review period."
    )
    clause_start = text.index("CLAUSE 8.4")
    clause = ClauseInfo(
        clause_number="8.4",
        clause_title="Extension of Time",
        clause_text=text[clause_start:],
        clause_type="clause",
        start_position=clause_start,
        end_position=len(text),
        level=2,
        parent_number="8",
        clause_id="gcc-8.4",
        toc_path=["GCC", "Time for Completion", "Extension of Time"],
    )
    parsed = ParsedDocument(
        text=text,
        pages=[
            ParsedPage(number=4, text=text[:60], start=0, end=60),
            ParsedPage(number=5, text=text[60:], start=60, end=len(text)),
        ],
        file_path="contract.pdf",
    )
    ingestor = ContractIngestor.__new__(ContractIngestor)
    ingestor.config = IngestionConfig(CHUNK_SIZE=4000)
    ingestor.clause_extractor = ClauseExtractor()

    payloads = ingestor._build_clause_payloads(
        parsed_doc=parsed,
        clauses=[clause],
        final_tags=["gcc", "eot"],
        upload_id="upload-1",
        document_id="doc-1",
        organization_id="org-A",
        project_id="proj-A",
        filename="gcc.pdf",
        source_path="/tmp/gcc.pdf",
    )

    assert len(payloads) == 1
    metadata = payloads[0]["metadata"]
    assert metadata["uploadType"] == "contract"
    assert metadata["document_type"] == "contract"
    assert metadata["organization_id"] == "org-A"
    assert metadata["project_id"] == "proj-A"
    assert metadata["document_id"] == "doc-1"
    assert metadata["clause_id"] == "gcc-8.4"
    assert metadata["clause_number"] == "8.4"
    assert metadata["clause_no"] == "8.4"
    assert metadata["clause_title"] == "Extension of Time"
    assert metadata["contract_id"] == "doc-1"
    assert metadata["chunk_type"] == "clause"
    assert metadata["section_title"] == "Extension of Time"
    assert metadata["clause_level"] == 2
    assert metadata["parent_clause_number"] == "8"
    assert metadata["toc_path"] == ["GCC", "Time for Completion", "Extension of Time"]
    assert metadata["page_numbers"] == [4, 5]
    assert metadata["page_start"] == 4
    assert metadata["page_end"] == 5
    assert metadata["page"] == 4
    assert metadata["source_pdf_page_link"] == "contract:doc-1#page=4"
    assert metadata["ai_chunked"] is False
    assert metadata["text_source"] == "cleaned_contract_text"
    assert metadata["clause_tags"] == ["CLAUSE 8.4", "GCC 8.4", "SCC 8.4"]
    assert metadata["checksum_sha256"] == payloads[0]["checksum"]
    assert "Hierarchy: GCC > Time for Completion > Extension of Time" in metadata["text_enriched"]
    assert "Pages: 4, 5" in metadata["text_enriched"]


def test_contract_text_preprocessor_removes_repeated_margins_but_preserves_clause_headings():
    parsed = ParsedDocument(
        text="",
        pages=[
            ParsedPage(
                number=1,
                start=0,
                end=0,
                text=(
                    "KNPCC-05 Contract Package\n"
                    "Page 1 of 3\n"
                    "1 General Conditions\n"
                    "This clause text must remain.\n"
                    "KNPCC-05 Contract Package"
                ),
            ),
            ParsedPage(
                number=2,
                start=0,
                end=0,
                text=(
                    "KNPCC-05 Contract Package\n"
                    "Page 2 of 3\n"
                    "8.4 Extension of Time\n"
                    "The Contractor shall give notice.\n"
                    "KNPCC-05 Contract Package"
                ),
            ),
            ParsedPage(
                number=3,
                start=0,
                end=0,
                text=(
                    "KNPCC-05 Contract Package\n"
                    "Page 3 of 3\n"
                    "20.1 Claims\n"
                    "Particulars shall be submitted.\n"
                    "KNPCC-05 Contract Package"
                ),
            ),
        ],
        file_path="contract.pdf",
    )

    cleaned, audit = ContractTextPreprocessor().clean_document(parsed)

    assert "KNPCC-05 Contract Package" not in cleaned.text
    assert "Page 1 of 3" not in cleaned.text
    assert "1 General Conditions" in cleaned.text
    assert "8.4 Extension of Time" in cleaned.text
    assert "20.1 Claims" in cleaned.text
    assert [item["page_number"] for item in audit] == [1, 2, 3]


def test_clause_extractor_does_not_treat_standalone_page_numbers_as_clauses():
    text = """149

8.4 Extension of Time
The Contractor shall be entitled to extension of time.

150"""

    clauses = ClauseExtractor().extract_clauses(text)

    assert [clause.clause_number for clause in clauses] == ["8.4"]


def test_ai_clause_spans_below_coverage_threshold_are_rejected():
    ingestor = ContractIngestor.__new__(ContractIngestor)
    ingestor.processing_config = type(
        "Cfg",
        (),
        {"contract_ai_chunking_min_confidence": 0.80},
    )()
    source_text = "8.4 Extension of Time\nFull clause text.\n20.1 Claims\nClaim procedure text."
    spans = [
        type(
            "Span",
            (),
            {
                "heading": "8.4 Extension of Time",
                "path": ["8.4 Extension of Time"],
                "clause_id": "8.4",
                "start_offset": 0,
                "end_offset": 20,
            },
        )()
    ]

    accepted, confidence = ingestor._validate_ai_clause_spans(spans, source_text)

    assert accepted == []
    assert confidence is not None
    assert confidence < 0.80
