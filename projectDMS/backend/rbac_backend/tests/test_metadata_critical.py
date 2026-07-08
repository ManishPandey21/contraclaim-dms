from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Optional

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.database_service import DatabaseService
from rbac_backend.services.reference_parser import parse_legacy_reference_text
from rbac_backend.services.text_processing_service import TextProcessingService


class FakeDocumentsCollection:
    def __init__(self) -> None:
        self._documents: list[Dict[str, Any]] = []

    async def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if "_id" in filter:
            for document in self._documents:
                if document.get("_id") == filter["_id"]:
                    return document
            return None

        if "filename" in filter:
            for document in self._documents:
                if document.get("filename") == filter["filename"]:
                    return document
            return None

        return None

    async def insert_one(self, document: Dict[str, Any]) -> SimpleNamespace:
        inserted = dict(document)
        inserted["_id"] = inserted.get("_id", f"doc-{len(self._documents) + 1}")
        self._documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, filter: Dict[str, Any], update: Dict[str, Any]) -> None:
        document = await self.find_one(filter)
        if document is None:
            raise AssertionError(f"Document not found for update: {filter}")
        document.update(update.get("$set", {}))


class FakeDatabase:
    def __init__(self) -> None:
        self.documents = FakeDocumentsCollection()


def _sample_report() -> str:
    return """
1) Date: 2025-02-14
2) Letter No.: ABC-123
3) From (Company): Alpha Corp
4) To (Company): Beta Ltd
5) Subject: Contract payment terms clarification
6) References (Ref.):
   - Ref A
   - Ref B
7) Summary:
   - Point one
   - Point two
8) Key Words:
   - escalation
   - liquidated damages
   - retention money
9) Contractual Clauses:
   1. Clause 14.2 - Payment schedule
   2) Clause 17 - LDs
10) Full content: Example full content here.
""".strip()


def _sample_reference_report() -> str:
    return """
1) Date: 2025-11-08
2) Letter No.: KNPCC-01634-E01
5) Subject: Reminder for submission of cost
6) References (Ref.):
   - letter no. AFC/PM/KNPCC-06/4905 dated 05.11.2025
   - LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dtd. 14-03-2022
10) Full content: Example full content here.
""".strip()


def _sample_expanded_report() -> str:
    return """
1) Date: 07.03.2025
2) Letter No.: CC/ABC/123
3) From (Company): Metro Contractor JV
4) To (Company): Metro Rail Corporation
5) Subject: Delay due to late access at Charbagh Station concourse
6) References:
   - letter no. MRC/ACCESS/100 dated 01.03.2025
7) Asset Type: Station
8) Location: Charbagh Station
9) Specific Area: concourse
11) Chainage From: CH 10+100
12) Chainage To: CH 10+450
13) Work Type: D-wall
14) Issue Nature: Access Constraint
15) Claim Category: EOT Claim
16) Alleged Responsibility: Employer
17) Priority: High
18) Key Words: delay, EOT, Charbagh, D-wall, access constraint
19) Linked Event Suggested: Late access at Charbagh Station concourse
20) Reference Chain: original notice
21) Key Words: station delay, eot claim, clause 8.4, authority access
22) Summary:
   - Contractor records delayed access to Charbagh Station concourse.
   - Employer responsibility is alleged due to pending handover.
   - Delay may affect EOT entitlement and prolongation cost reservation.
   - Engineer is requested to record the hindrance and issue instructions.
23) Contractual Clauses: GCC 8.4, SCC 2.1, Employer's Requirements access protocol
24) Key Reply Points - Points to be Addressed While Responding:
   - Address notice compliance and contemporaneous records.
   - Confirm critical path impact and mitigation steps.
25) Full Content: Cleaned full letter body here.
26) tags: Contractual, Delay
27) subTags: EOT Programme, Site Access
""".strip()


def _sample_extracted_tag_report() -> str:
    return _sample_expanded_report().replace(
        "26) tags:", "26) extracted_tags:"
    ).replace(
        "27) subTags:", "27) extracted_subTags:"
    )


def test_legacy_reference_parser_extracts_letter_no_date_and_raw() -> None:
    parsed = parse_legacy_reference_text(
        "LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dated 14.03.2022"
    )

    assert parsed == {
        "letterNo": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
        "letter_no": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
        "date": "14-03-2022",
        "raw": "LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dated 14.03.2022",
    }


def test_parse_extraction_report_extracts_critical_metadata() -> None:
    service = TextProcessingService(DocumentProcessingConfig())

    parsed = service.parse_extraction_report(_sample_report())

    assert parsed.date == "14-02-2025"
    assert parsed.letter_no == "ABC-123"
    assert parsed.subject == "Contract payment terms clarification"
    assert parsed.from_company == "Alpha Corp"
    assert parsed.to_company == "Beta Ltd"
    assert parsed.references == ["Ref A", "Ref B"]
    assert parsed.summary == "- Point one\n- Point two"
    assert parsed.keywords == ["escalation", "liquidated damages", "retention money"]
    assert parsed.contractual_clauses
    assert parsed.contractual_clauses[0] == "Clause 14.2 - Payment schedule"
    assert parsed.full_content == "Example full content here."


def test_parse_extraction_report_structures_legacy_letter_references() -> None:
    service = TextProcessingService(DocumentProcessingConfig())

    parsed = service.parse_extraction_report(_sample_reference_report())

    assert parsed.references == [
        {
            "letterNo": "AFC/PM/KNPCC-06/4905",
            "letter_no": "AFC/PM/KNPCC-06/4905",
            "date": "05-11-2025",
            "raw": "letter no. AFC/PM/KNPCC-06/4905 dated 05.11.2025",
        },
        {
            "letterNo": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
            "letter_no": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
            "date": "14-03-2022",
            "raw": "LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dtd. 14-03-2022",
        },
    ]


def test_parse_extraction_report_extracts_expanded_contract_metadata() -> None:
    service = TextProcessingService(DocumentProcessingConfig())

    parsed = service.parse_extraction_report(_sample_expanded_report())

    assert parsed.date == "07-03-2025"
    assert parsed.letter_no == "CC/ABC/123"
    assert parsed.from_company == "Metro Contractor JV"
    assert parsed.to_company == "Metro Rail Corporation"
    assert parsed.subject == "Delay due to late access at Charbagh Station concourse"
    assert parsed.references == [
        {
            "letterNo": "MRC/ACCESS/100",
            "letter_no": "MRC/ACCESS/100",
            "date": "01-03-2025",
            "raw": "letter no. MRC/ACCESS/100 dated 01.03.2025",
        }
    ]
    assert parsed.asset_type == "Station"
    assert parsed.location == "Charbagh Station"
    assert parsed.specific_area == "concourse"
    assert parsed.chainage_from == "CH 10+100"
    assert parsed.chainage_to == "CH 10+450"
    assert parsed.work_type == "D-wall"
    assert parsed.issue_nature == "Access Constraint"
    assert parsed.claim_category == "EOT Claim"
    assert parsed.alleged_responsibility == "Employer"
    assert parsed.priority == "High"
    assert parsed.keywords == ["delay", "EOT", "Charbagh", "D-wall", "access constraint"]
    assert parsed.linked_event_suggested == "Late access at Charbagh Station concourse"
    assert parsed.reference_chain == "original notice"
    assert parsed.additional_keywords == ["station delay", "eot claim", "clause 8.4", "authority access"]
    assert parsed.summary.startswith("- Contractor records delayed access")
    assert parsed.contractual_clauses == [
        "GCC 8.4",
        "SCC 2.1",
        "Employer's Requirements access protocol",
    ]
    assert parsed.key_reply_points == [
        "Address notice compliance and contemporaneous records",
        "Confirm critical path impact and mitigation steps",
    ]
    assert parsed.full_content == "Cleaned full letter body here."
    # Values are enforced against the controlled vocabularies
    # (EXTRACTED_TAG_OPTIONS / EXTRACTED_SUBTAG_OPTIONS) server-side.
    assert parsed.tags == ["Contractual", "Delay"]
    assert parsed.sub_tags == ["EOT Programme", "Site Access"]


def test_parse_extraction_report_accepts_extracted_tag_labels() -> None:
    service = TextProcessingService(DocumentProcessingConfig())

    parsed = service.parse_extraction_report(_sample_extracted_tag_report())

    assert parsed.tags == ["Contractual", "Delay"]
    assert parsed.sub_tags == ["EOT Programme", "Site Access"]


async def test_upsert_document_metadata_inserts_and_updates() -> None:
    config = DocumentProcessingConfig()
    text_service = TextProcessingService(config)
    database_service = DatabaseService(config)
    fake_db = FakeDatabase()

    parsed = text_service.parse_extraction_report(_sample_report())
    inserted = await database_service._upsert_document_metadata(
        fake_db,
        document_id=None,
        file_path="uploads/process_file/sample.pdf",
        parsed_metadata=parsed,
        full_text="OCRTEXT",
    )

    assert inserted["filename"] == "sample.pdf"
    assert inserted["filepath_local"] == "uploads/process_file/sample.pdf"
    assert inserted["subject"] == "Contract payment terms clarification"
    assert inserted["letterNo"] == "ABC-123"
    assert inserted["from"] == "Alpha Corp"
    assert inserted["to"] == "Beta Ltd"
    assert inserted["summary"] == "- Point one\n- Point two"
    assert inserted["keywords"] == ["escalation", "liquidated damages", "retention money"]
    assert inserted["contractual_clauses"]
    assert inserted["contractual_clauses"][0] == "Clause 14.2 - Payment schedule"
    assert inserted["reference"] == [{"letterNo": "Ref A"}, {"letterNo": "Ref B"}]
    assert inserted["ocrText"] == "OCRTEXT"
    assert isinstance(inserted["date"], datetime)
    assert "createdAt" in inserted
    assert "updatedAt" in inserted


async def test_upsert_document_metadata_preserves_structured_reference_rows() -> None:
    config = DocumentProcessingConfig()
    text_service = TextProcessingService(config)
    database_service = DatabaseService(config)
    fake_db = FakeDatabase()

    parsed = text_service.parse_extraction_report(_sample_reference_report())
    inserted = await database_service._upsert_document_metadata(
        fake_db,
        document_id=None,
        file_path="uploads/process_file/reference-sample.pdf",
        parsed_metadata=parsed,
        full_text="OCRTEXT",
    )

    assert inserted["reference"] == [
        {
            "letterNo": "AFC/PM/KNPCC-06/4905",
            "letter_no": "AFC/PM/KNPCC-06/4905",
            "date": "05-11-2025",
            "raw": "letter no. AFC/PM/KNPCC-06/4905 dated 05.11.2025",
        },
        {
            "letterNo": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
            "letter_no": "378/LMRC/CE-Contract/KNPCC-06/2021-22",
            "date": "14-03-2022",
            "raw": "LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dtd. 14-03-2022",
        },
    ]

    updated_metadata = parsed.model_copy(
        update={
            "keywords": ["variation order", "price adjustment"],
            "contractual_clauses": ["Clause 21 - Variations"],
        }
    )
    updated = await database_service._upsert_document_metadata(
        fake_db,
        document_id=str(inserted["_id"]),
        file_path="uploads/process_file/reference-sample.pdf",
        parsed_metadata=updated_metadata,
        full_text="OCRTEXT-V2",
    )

    assert updated["_id"] == inserted["_id"]
    assert updated["keywords"] == ["variation order", "price adjustment"]
    assert updated["contractual_clauses"] == ["Clause 21 - Variations"]
    assert updated["ocrText"] == "OCRTEXT-V2"


async def test_upsert_document_metadata_saves_expanded_fields_without_overwriting_tags() -> None:
    config = DocumentProcessingConfig()
    text_service = TextProcessingService(config)
    database_service = DatabaseService(config)
    fake_db = FakeDatabase()

    parsed = text_service.parse_extraction_report(_sample_expanded_report())
    inserted = await database_service._upsert_document_metadata(
        fake_db,
        document_id=None,
        file_path="uploads/process_file/expanded.pdf",
        parsed_metadata=parsed,
        full_text="OCRTEXT",
    )

    assert inserted["asset_type"] == "Station"
    assert inserted["location"] == "Charbagh Station"
    assert inserted["specific_area"] == "concourse"
    assert inserted["chainage_from"] == "CH 10+100"
    assert inserted["chainage_to"] == "CH 10+450"
    assert inserted["work_type"] == "D-wall"
    assert inserted["issue_nature"] == "Access Constraint"
    assert inserted["claim_category"] == "EOT Claim"
    assert inserted["alleged_responsibility"] == "Employer"
    assert inserted["priority"] == "High"
    assert inserted["linked_event_suggested"] == "Late access at Charbagh Station concourse"
    assert inserted["reference_chain"] == "original notice"
    assert inserted["additional_keywords"] == ["station delay", "eot claim", "clause 8.4", "authority access"]
    assert inserted["extracted_tags"] == ["Contractual", "Delay"]
    assert inserted["extracted_subTags"] == ["EOT Programme", "Site Access"]
    assert "tags" not in inserted
    assert "subTags" not in inserted
    assert inserted["metadata"]["asset_type"] == "Station"
    assert inserted["metadata"]["subTags"] == ["EOT Programme", "Site Access"]
