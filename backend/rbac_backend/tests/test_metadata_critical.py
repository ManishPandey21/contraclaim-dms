from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Optional

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.database_service import DatabaseService
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

    updated_metadata = parsed.model_copy(
        update={
            "keywords": ["variation order", "price adjustment"],
            "contractual_clauses": ["Clause 21 - Variations"],
        }
    )
    updated = await database_service._upsert_document_metadata(
        fake_db,
        document_id=str(inserted["_id"]),
        file_path="uploads/process_file/sample.pdf",
        parsed_metadata=updated_metadata,
        full_text="OCRTEXT-V2",
    )

    assert updated["_id"] == inserted["_id"]
    assert updated["keywords"] == ["variation order", "price adjustment"]
    assert updated["contractual_clauses"] == ["Clause 21 - Variations"]
    assert updated["ocrText"] == "OCRTEXT-V2"
