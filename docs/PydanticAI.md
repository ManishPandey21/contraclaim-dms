# PydanticAI Integration Plan

## Goals
- Replace brittle regex-based metadata parsing with a Pydantic-driven LLM agent that returns validated ParsedDocumentMetadata instances.
- Centralize prompt + schema definitions so document ingestion and contract uploads share a single metadata extraction pipeline.
- Provide resilient fallbacks (legacy parser) and observability around LLM outputs for troubleshooting.

## Current State Assessment
- DocumentProcessor relies on OpenAIService.process_document to stream a textual report, then TextProcessingService.parse_extraction_report applies regex to map fields.
- Contract ingestion reuses regex parsing indirectly via metadata_processor_service; no structured enforcement occurs before persistence.
- Error handling focuses on transport failures; malformed or partial metadata silently produces empty fields.
- No abstraction exists for comparing raw LLM output vs persisted ParsedDocumentMetadata during QA.

## Target Architecture
- Introduce PydanticAIService wrapping a PydanticAI Agent configured with ParsedDocumentMetadata schema and tuned prompt instructions.
- Document processing flow:
  1. OCR / file upload as today.
  2. Retrieve canonical text content (OCR or OpenAI extraction).
  3. Invoke PydanticAIService.extract_metadata to obtain structured metadata + raw model diagnostics.
  4. Persist metadata via existing DatabaseService while archiving agent run metadata (for auditing optional).
- Provide graceful fallback to existing regex parser when the agent raises validation errors or model unavailable.

## Implementation Steps
1. **Dependencies**
   - Add pydantic-ai (slim build) to equirements.txt.
2. **Service Layer**
   - Create services/pydantic_ai_service.py exposing:
     - Schema definitions (adapting ParsedDocumentMetadata).
     - Agent instantiation using OpenAI chat model + config overrides.
     - Async extract_metadata(text, context) returning both ParsedDocumentMetadata and debug info.
3. **DocumentProcessor Integration**
   - Inject PydanticAIService in DocumentProcessor.
   - Replace direct parse_extraction_report call with agent extraction, falling back to legacy parser on PydanticAIError.
   - Surface agent diagnostics in logs / ProcessingResult for observability.
4. **Contract Ingestion Alignment**
   - Update contract ingestion / metadata processor utilities to leverage the new service when generating metadata summaries.
5. **Configuration & Settings**
   - Extend DocumentProcessingConfig with knobs (e.g., use_pydantic_ai, pydantic_ai_model) to toggle integration.
6. **Testing**
   - Expand 	est_embedding_fix.py or new test module to mock PydanticAI agent output, ensuring fallback logic and conversion to ParsedDocumentMetadata.
   - Add regression test for failure path (agent raises -> regex fallback).
7. **Documentation**
   - Document usage, toggles, and fallback logic in README or ops notes for future maintainers.

## Risks & Mitigations
- **Model drift / cost**: Keep config-driven toggles and allow per-env disablement.
- **Agent validation errors**: Always catch PydanticAI exceptions and fallback to deterministic parser.
- **Latency impact**: Cache agent client and reuse across documents; monitor response times.

## Validation Plan
- Unit tests covering agent success + fallback.
- Manual smoke test processing a representative PDF to verify metadata fields populate as expected.
- Compare existing regex output vs PydanticAI output for sample docs to ensure parity before wider rollout.
