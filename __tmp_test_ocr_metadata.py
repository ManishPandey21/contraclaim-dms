import asyncio
import json
import os
import pdfminer
import pdfplumber
from pathlib import Path

from dotenv import load_dotenv

from backend.rbac_backend.config.document_processing_config import DocumentProcessingConfig
from backend.rbac_backend.services.ocr_service import OCRService
from backend.rbac_backend.services.pydantic_ai_service import (
    PydanticAIService,
    PydanticAIMetadataError,
)
from backend.rbac_backend.services.text_processing_service import TextProcessingService

try:
    import pdfplumber
    print("pdfplumber imported successfully")
except ImportError as e:
    print(f"Failed to import pdfplumber: {e}")

PDF_PATH = Path(r"C:\Users\santo\Downloads\Editable PDF's-20251018T171623Z-1-001\Editable PDF_s\Kanpur-LET-JVTI-CPM-01360-E01.pdf")

async def main() -> None:
    load_dotenv()

    if not PDF_PATH.exists():
        raise SystemExit(f"PDF file not found: {PDF_PATH}")

    config = DocumentProcessingConfig()
    ocr_service = OCRService(config)

    print("Running OCR pipeline...")
    processed_path, raw_text = await ocr_service.process_pdf(PDF_PATH)
    print(f"Processed PDF stored at: {processed_path}")
    print(f"OCR text characters: {len(raw_text or '')}")

    if not raw_text:
        print("No OCR text produced; attempting extraction from processed PDF directly.")
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"pdfplumber is required to extract text: {exc}")
        with pdfplumber.open(processed_path) as pdf:
            raw_text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        print(f"Extracted {len(raw_text or '')} characters using pdfplumber fallback.")

    extraction_text = raw_text or ""
    if not extraction_text.strip():
        raise SystemExit("Unable to obtain text content for metadata extraction.")

    metadata_payload = None

    pydantic_service = PydanticAIService(config)
    if pydantic_service.is_enabled:
        print("PydanticAI metadata extraction enabled; invoking agent...")
        try:
            result = await pydantic_service.extract_metadata(
                document_text=extraction_text,
                context={"filename": PDF_PATH.name, "upload_type": "incoming"},
            )
            if result:
                metadata_payload = result.metadata
                debug_info = result.debug
                print("PydanticAI extraction succeeded. Token usage summary:")
                print(json.dumps(debug_info.get("usage", {}), indent=2))
        except PydanticAIMetadataError as exc:
            print(f"PydanticAI extraction failed: {exc}")
    else:
        print("PydanticAI disabled; falling back to legacy parser.")

    if metadata_payload is None:
        print("Running legacy regex metadata parser...")
        text_service = TextProcessingService(config)
        metadata_payload = text_service.parse_extraction_report(extraction_text)

    if hasattr(metadata_payload, "model_dump"):
        metadata_dict = metadata_payload.model_dump()
    elif hasattr(metadata_payload, "dict"):
        metadata_dict = metadata_payload.dict()
    elif hasattr(metadata_payload, "__dict__"):
        metadata_dict = dict(metadata_payload.__dict__)
    else:
        metadata_dict = metadata_payload

    print("Metadata extraction result:")
    print(json.dumps(metadata_dict, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
