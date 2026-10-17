# Release 1.2 — Grounded Letter Summary: audit and design

Status: design for PR A (summary authority / data model). Base: release
`eeeab0a9866b0d6abcea4f4466254cf47c91b92f`, tree `9f9264890500d06d370f22b48b7686a7cb2d1709`
(2026-10-16). Supersedes §5 of
[mixed_pdf_ingestion_and_summary_plan_2026-08-13.md](mixed_pdf_ingestion_and_summary_plan_2026-08-13.md)
where the two disagree.

## 0. Audit of the base (read from source, 2026-10-16)

| Question | Answer on `eeeab0a` |
|---|---|
| Current summary field | One `documents.summary: str`. `short_summary` and `detailed_summary` exist nowhere in the repository (backend, client, tests). |
| Current summary prompt | `SUMMARY_EXTRACTION_INSTRUCTION` (`models/document_metadata.py:30`), prompt version `existing_document_metadata.v4`. Asked of the model as Item 22 of the numbered report (`services/openai_service.py`) and as `summary_points` in the PydanticAI schema (`services/pydantic_ai_service.py:123`), rendered to `- point` lines (`:376-383`). The OCR-fallback report takes the first five long lines verbatim (`services/document_processor.py:943`). |
| Current summary storage | Written as a plain string by `DatabaseService` (`services/database_service.py` `updates["summary"]`) and `DocumentService.process_document_async` (`services/document_service.py` `update_fields["summary"]`). No version, no provenance, no binding to the evidence it was generated from. |
| Current summary frontend | `client/src/pages/LetterSummaryPage.tsx`: loads `GET /documents/{id}`, parses `doc.summary` into bullets (`:520`), tabs *Summary / Contractual Clauses / Key Reply Points* (`:748`). |
| Current key reply points | `documents.key_reply_points: List[str]`, model-generated from `KEY_REPLY_POINTS_EXTRACTION_INSTRUCTION` (`models/document_metadata.py:43`). Advisory; excluded from correspondence evidence and vector payloads (`retrieval.correspondence_payload.ADVISORY_FIELDS`). |
| Current canonical evidence authority | `document_extraction_heads` (manifest: `canonical_revision`, `canonical_sha256`, `extraction_run_id`, `page_map` with per-page `content_sha256`, `review_pages`, `unresolved_pages`, `withheld_pages`) over `document_ocr_pages` rows. Read only through `canonical_evidence_service.get_document_canonical_evidence` (authorised) / `_read_canonical_evidence` (after authorisation), which reassembles and re-verifies the checksum on every read. |
| Current page/span provenance | `CanonicalEvidence.pages[*].start/end` give each page's span in the canonical text; `page_at(offset)` maps an offset back to a page. Page rows carry `quality_verdict`, `quality_checks` (`name`/`verdict`/`candidate_value`), `applied_repairs`, `needs_review`, `text_withheld`. |
| Which documents have canonical evidence | Only the unified pipeline publishes it (`document_processor.py:516`). `legacy_v0` documents — most of the production corpus while the unified canary is off — have none (`EVIDENCE_NOT_BUILT`). |
| Current human review signals | `processing_status=human_review_required` (adverse, not consumable). Release 1.1.1 review (`services/extraction_review_service.py`): append-only `document_page_reviews`, `review_resolution` stamped on the head bound to `canonical_revision`+`canonical_sha256` (unset by any republish), `documents.extraction_review` keeps `machine_verdict`. The machine verdict on page rows is never rewritten. |
| Current metadata quality signals | `documents.metadata_quality` from `metadata_integrity.assess_metadata_quality`: `status`, `degraded`, `source_degraded`, `references_authoritative`, `missing_core_fields`, `warnings`. Human-edited fields protected by `protect_human_edited_fields`. |
| Consumability | `publication_policy.is_consumable`: denies `human_review_required`, confirmed duplicates, `lifecycle_state in {duplicate, deleted}`. `summary` is in `_DERIVED_FIELDS` and withheld with the body (`authoritative_summary`). |

Confirmed:

- `short_summary` does not exist on release; `detailed_summary` does not exist on release.
- The current `summary` is an AI-generated descriptive field (model output, or OCR-fallback lines).
- Source/canonical body text is the factual authority: `authoritative_fact_text` never returns the
  summary, and `test_correspondence_fact_vs_summary.py` pins every fact slot to the body.
- No summary outranks source text, and the new fields must not either (§9).

### 0.1 Consumers of `summary`

See §10 (filled from a full-repo inventory).

## 1. Authority model (replaces `generated_from_conversion_id`)

There is no `conversion_id` in the current architecture. The evidence identity is the canonical
head. A detailed summary is valid only for the exact evidence it was built from:

```
generated_from: {
    canonical_revision: int,        # head.canonical_revision — what citations pin
    canonical_sha256: str,          # head.canonical_sha256 — the bytes
    extraction_run_id: str | null,  # head.extraction_run_id — which run wrote the rows
    review_resolution: {            # present only when built after a human review
        canonical_revision, canonical_sha256, approved_pages, resolved_at
    } | null
}
```

`current` ⇔ `generated_from.canonical_revision == head.canonical_revision` **and**
`generated_from.canonical_sha256 == head.canonical_sha256`. Anything else is `stale` and is
never served as current. No new document authority is introduced: the summary is a derived
view of the head, exactly like a chronology citation.

Documents without canonical evidence (`legacy_v0`) get **no** grounded summary: nothing can carry
page provenance. Their status is `unavailable` (reason `no_canonical_evidence`) and the legacy
`summary` keeps working unchanged. Whether legacy documents should get a degraded, page-less
variant built from source `ocrText` is an owner decision (P1 in the release report).

## 2. Products

| Product | Question | Shape | Authority |
|---|---|---|---|
| `short_summary` | What is this letter about? | 1–2 sentences, ≤ 240 chars | composed only from Fact Pack facts |
| `detailed_summary` | What does this letter state / establish? | 11 sections of grounded items + facts + markdown | every item cites `fact_ids` |
| `key_reply_points` | What must a reply consider? | unchanged, advisory | unchanged; never merged into the detailed summary |

## 3. Data model (additive)

`documents.summary` is unchanged in meaning and writer. New fields:

- `documents.short_summary: str | null`
- `documents.detailed_summary: DetailedSummary | null` (schema in
  `models/grounded_summary.py`; `schema_version: "1"`)
- `documents.summary_state: {status, reason, generated_from, updated_at, error}`

Every generation is also appended to `document_grounded_summaries` (one row per
document + canonical revision + generation), so a superseded summary remains for audit while the
document points only at the current one.

`GroundedSummaryItem`: `text`, `role` (`source_fact` / `party_position` / `request` /
`instruction` / `reservation` / `claim` / `reference` / `uncertainty`), `party`, `fact_ids`,
`page_numbers`, `verification_status`, `source_spans` (`page_number`, `start_char`, `end_char`,
offsets into the canonical text).

`GroundedFact`: `id`, `type`, `label`, `value`, `unit`, `normalized_value`, `page_numbers`,
`source_spans`, `evidence_checks`, `verification_status`, `origin` (`canonical_text` /
`structured_metadata` / `human_edit` / `reference_sync`), `quote` (≤ 400 chars of the sentence that
states it; never whole page text).

`verification_status` vocabulary:

| Status | Meaning |
|---|---|
| `verified` | located in canonical text **and** independently corroborated (a passing deterministic quality check on that value, or a structured-metadata value found verbatim in the canonical text) |
| `source_stated` | located verbatim in canonical text; no independent check exists |
| `human_approved` | located on a page the machine gate failed and a reviewer approved for this exact revision |
| `uncertain` | not located in canonical text, ambiguous (e.g. a D/M vs M/D date), or on a page whose text is withheld/unresolved |
| `not_checkable` | the value is a human-entered metadata field the canonical text does not state |

## 4. Generation: two stages

**Stage A — Fact Pack (deterministic, no model).** Built from `CanonicalEvidence` plus the
document's structured metadata. Header facts (letter no, date, subject, from, to) come from the
stored fields and are *located* in the canonical text; location decides status. Clauses, amounts,
dates and stated references are read from the canonical text by deterministic patterns. Sentences
carrying a request, instruction, reservation, claim or stated position are taken verbatim, with
their span, and attributed to the letter's author — never restated as established fact. Page quality
(machine verdict, repairs, review approval) is carried on every fact. The LLM `summary` and
`key_reply_points` are never read.

**Stage B — Presentation.** Deterministic renderer always runs. An optional LLM pass
(`GROUNDED_SUMMARY_LLM_ENABLED`, default **off**) receives only the Fact Pack (ids, labels, values,
quotes), returns JSON whose every item cites `fact_ids`, and is validated: unknown ids, items with
no ids, and any number/date/clause token not present in the cited facts reject the whole output,
which falls back to the deterministic render (`generator.degraded=false`, `mode=deterministic`,
`llm_rejection` recorded). Prompt `grounded_letter_summary.v1` (pinned by test).

## 5. Human review, consumability, staleness

- Not consumable (human review required, duplicate, deleted) ⇒ no generation; status
  `blocked_for_review` / `withheld`; the GET route serves no summary text at all (the same rule
  `authoritative_summary` applies to `summary`).
- After resolution the review service regenerates from the same revision; approved pages' facts are
  `human_approved` and an `uncertainties` item records that page N failed automated checks
  (`check names`) and was approved by a reviewer — the machine failure is never erased.
- Republish ⇒ new revision ⇒ stored summary is `stale` and not displayed; new run regenerates.
- Summary failure never fails extraction: status `failed`, document stays consumable.

Status vocabulary: `not_generated`, `generating`, `current`, `stale`, `degraded`,
`blocked_for_review`, `withheld`, `unavailable`, `failed`.

## 6. API

`GET /api/documents/{id}/grounded-summary` — same gate order as `GET /documents/{id}` (selection,
then `authorize_document(dms.document.view)`), computes staleness against the live head, withholds
for non-consumable documents. `POST /api/documents/{id}/grounded-summary/generate` — on-demand
(lazy) generation for an existing document, `dms.document.edit_metadata`. A dedicated route because
staleness needs the head read and the payload is large; the existing document payload is not widened.

## 8. Measurements (local, 2026-10-16)

Real native extraction (`OCRService.process_pdf_pagewise`), the real `ExtractionQualityGate` and a real
`publish_canonical` into the in-memory Mongo double, then `GroundedSummaryService.generate`. This
workstation has no Tesseract/OCRmyPDF, so scanned pages stay unread here; OCR-dependent acceptance
(4299 cover pages, GLM mixed PDF) needs staging.

| Letter | Pages read | Canonical text | Fact Pack build | Whole generation | Model input (o200k) |
|---|---|---|---|---|---|
| 4299 (claim, tables) | 2–6 native; 1 unread (scan) | 5,234 chars / 2,152 tokens | 11–12 ms | 12–13 ms | 274 tokens (0.13x document) |
| 2327 (scanned) | 3–4 only (scan) | 70 chars | 1 ms | 1 ms | 4 tokens |

Deterministic mode (the default) costs nothing per document. With the model pass enabled the input
is the citable facts only; at the measured 274 input tokens and a ≤ 2,500-token output cap the cost is
bounded by the output, which must be priced against the configured `OPENAI_RESPONSES_MODEL` before the
flag is turned on. The first run of this measurement found the input at 4x the document (8,822 tokens)
because every fact carried its quote; fixed before review (see the acceptance-fix commit).

Defects found by the real document and fixed: table rows read as material dates and quantities; a
figure running into a date read as an amount ("INR 10" from `2,000,000 INR 10/30/2022`); table headers
read as amounts ("INR 1"); an unread page described as failing quality checks.

## 9. Owner decisions

1. **Legacy documents** (`legacy_v0`, no canonical evidence — most of production while the unified
   canary is off) get no grounded summary. Option: a page-less degraded variant from source `ocrText`.
2. **Model pass** stays off (`GROUNDED_SUMMARY_LLM_ENABLED=false`) until priced and reviewed on
   staging output.
3. **Backfill**: none in this release. `POST .../grounded-summary/generate` is per document; a
   corpus backfill is a separate approved operation.
4. **Consumer migration** of `summary` → `short_summary` (registers, search, cards, drafting selectors)
   is a later PR.

## 10. `summary` consumers and the compatibility decision

Full inventory, 2026-10-16 (backend root `backend/rbac_backend/`):

- **Writers:** `text_processing_service.py:446-528` (report Item 22), `pydantic_ai_service.py:376-383`,
  `document_processor.py:943` (OCR fallback), `database_service.py:378`, `document_service.py:2312`,
  `metadata_integrity.py:300-404` (degraded-overlay and rebuild), `graph/graph_ingestion_service.py:132`;
  the `metadata` snapshot (`extracted_metadata_updates`) copies it to `metadata.summary`.
- **Drafting / LLM context:** `agents/service.py:224`, `routers/deep_planning.py:425`,
  `services/ai_service.py:242-316`, `ai_workflows/langgraph/letter_pipeline.py` (8 sites),
  `letter_drafting/context.py`, `incoming_analyzer.py`, `letter_drafting/service.py:1237`,
  `strategy_context_service.py:152,260`.
- **Retrieval / graph:** `retrieval/correspondence_payload.py:62` (Qdrant payload),
  `graph/graph_adapter.py:135` (Graphiti body), `evidence_graph_service.py:613-673`.
- **Chronology / arbitration:** `chronology.py:835`, `arbitration_drafting/context.py:732,1066`,
  `service.py:966`, `workflow_domain.py:440`, `agents/deterministic.py:205,1473`, `agents/llm.py:561`.
- **Reports / API:** `report_service.py` (columns and `$ifNull` projections), `routers/documents.py:716`
  (vector-search results), `letter_service.py:605`, `Document.summary` on every document response.
- **Client:** `LetterSummaryPage.tsx:520`, `ReferencePage.tsx:477`,
  `letter-workflow/BackgroundSummary.tsx:125` (draft and strategic-plan pages), `ReportsAnalyticsPage.tsx:72`.

Twenty-plus consumers read `summary` as *detailed descriptive context*: drafting prompts, planning,
the vector payload, Graphiti bodies, reports. Replacing it with a ≤ 240-character string would silently
starve every one of them. **Decision: `summary` keeps its current writer and meaning for Release 1.2.**
`short_summary` and `detailed_summary` are new, read by the Letter Summary page only; consumers migrate
deliberately in later PRs. `GET /documents/{id}` returns `response_model=Document`, which whitelists
fields (`models/document.py:157`); the new fields are not added to it in PR A/B, so no existing payload
changes. A guard test pins that no production module outside the grounded-summary package and its
route reads `detailed_summary` / `short_summary` (no consumer may start treating them as fact).

## 7. Out of scope

Enclosure OCR, Vision, chronology A0c/D5/D6, PR #45, PR #19, reference-integrity, G15/G16/R4,
global unified_v1 rollout, corpus backfill, span highlighting in the PDF viewer, reply drafting.
