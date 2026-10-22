# Document Ingestion — Correspondence Metadata Matrix

Read-only audit, 2026-09-27, working tree on `integrate/key-date-eot` @ `a179ca9` (dirty). Paths relative to `backend/rbac_backend/` unless prefixed `client/src/`.
See also: [current state](document-ingestion-current-state.md) · [issues](document-ingestion-issues.md).

## 1. How extraction actually runs

- **Default = hybrid: LLM free text → regex.** `use_pydantic_ai` defaults False (`config/document_processing_config.py:150`). `DocumentProcessor._extract_and_persist` (`services/document_processor.py:783-938`) calls `OpenAIService.process_text` (chat completion over OCR text, `services/openai_service.py:136-182`) or `process_document` (Responses API over the uploaded PDF when there is no text, `:88-134`). Both use one numbered **27-field text prompt** `_expanded_extraction_prompt` (`openai_service.py:254-293`). Output is text, parsed by regex in `TextProcessingService.parse_extraction_report` (`services/text_processing_service.py:140-318`) into `ParsedDocumentMetadata` (`models/document_metadata.py:121-213`).
- **Optional structured path:** PydanticAI agent → `DocumentMetadataModel` (`services/pydantic_ai_service.py:85-139`, prompt `:240-287`), temperature 0, input truncated to 12 000 chars. When enabled, *both* LLM calls run.
- **Model / version provenance on `documents`:** only `metadata_source` label (`openai_text_legacy_regex`, `ocr_fallback_regex`, `pydantic_ai`, `legacy_regex`) and `processing_metadata.{processed_at, processing_time, chunks_created, partial_failures}` (`services/document_service.py:1815-1824`). **No model name, prompt version, schema version, per-field confidence.** `ai_extractions` rows carry `schema_version="evidence_graph.v2"`, `prompt_version="existing_document_metadata.v2"`, `model=<metadata_source label>`, a hard-coded `confidence=0.75`, `status=APPLIED` (`services/evidence_graph_service.py:587-600`).
- **LLM failure behaviour:**
  1. text call throws/timeouts (60 s) → deterministic regex report from OCR text, filename used as letterNo and subject (`document_processor.py:634-708, 823-838`); `partial_failures.ai_extraction` recorded, never shown in UI.
  2. PDF-upload call throws → whole attempt fails, job retries.
  3. regex parser throws → **empty metadata, silently, `success=True`** (`text_processing_service.py:315-318`). **Reproduced** (DI-B3): a single list item shaped like `letter no. X dated dd.mm.yyyy` under *Key Words*, *Contractual Clauses* or *Key Reply Points* is converted to a dict by `parse_legacy_reference_text` inside `_parse_list_block` (`:340-368`), fails `List[str]` validation, and every field is discarded.
  4. PydanticAI fails → regex, failure not recorded.
- **Reprocess overwrites human edits:** `document_service.py:1842-1856` sets subject/letterNo/from/to/date whenever the LLM returns a value; `metadata` replaced wholesale; `reference` set even when `[]`, then `sync_bidirectional(clear_existing=True)` removes parser links (manual links survive in their own `source="manual"` bucket). `manual_summary_metadata_override=True` is written on edit (`:2778-2787`) and **never read**.

## 2. The 39-field matrix

Legend: **P-OA** = `openai_service.py` prompt number; **LSP** = `client/src/pages/LetterSummaryPage.tsx`; **ME** = `client/src/components/document-viewer/MetadataEditor.tsx`; **RP** = `client/src/pages/ReferencePage.tsx`; **PUT** = `PUT /documents/{id}` (`routers/documents.py:2830`); **PSM** = `PATCH /documents/{id}/summary-metadata` (`routers/documents.py:2859`); **TXT** = Mongo wildcard `$text` index; **QP** = Qdrant chunk payload. "Human-verifiable" = the UI shows the value next to the source; the only affordance today is an "Open Document" button (LSP:645-650), so every row is **No** unless stated. **No field retains a per-fact page/span citation.**

| # | Field | Status | Schema field | DB field | Frontend display | Searchable | Editable | Human-verifiable | Source citation |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Document date | IMPLEMENTED | `date` (P-OA 1) | `documents.date` | LSP:661 | not TXT; date filter uses `createdAt` | PUT (ME:134) | No | No |
| 2 | Letter / reference no. | IMPLEMENTED | `letter_no` (2) | `letterNo`, `letterNoNormalized` (indexed org+project) | LSP:661 | TXT, QP | PUT | No | No |
| 3 | Sender / From | IMPLEMENTED (company string only) | `from_company` (3) | `from` | LSP:670 | TXT | PUT | No | No |
| 4 | Recipient / To | IMPLEMENTED (company string only) | `to_company` (4) | `to` | LSP:676 | TXT | PUT | No | No |
| 5 | CC | **ABSENT** | — | — | — | — | — | — | — |
| 6 | Subject | IMPLEMENTED | `subject` (5) | `subject` | LSP:658 | TXT, QP | PUT | No | No |
| 7 | Reference letters/documents | IMPLEMENTED (letterNo + date only) | `references[{letterNo,date,raw}]` (6) | `reference` (raw), `references`/`referencedBy` (resolved) | RP | TXT | manual add/remove links only | partial (RP shows link) | raw text kept |
| 8 | Contract / package / project | PARTIAL | not in prompt; upload-context `project_id` | `project_id` | — | filter | No | No | No |
| 9 | Organisation | PARTIAL | from/to strings only; no party entity | `from`, `to` | LSP | TXT | PUT | No | No |
| 10 | Document type | PARTIAL | not extracted; user `uploadType`; `reference_chain` (20) nearest | `uploadType`, `reference_chain` | LSP:554 | filter | PUT (`uploadType`) | No | No |
| 11 | Incoming/outgoing | PARTIAL | user `uploadType`, never cross-checked with from/to | `uploadType` | — | filter | PUT | No | No |
| 12 | Detailed summary | IMPLEMENTED (single 4–6 line summary) | `summary` (22) | `summary` | LSP:741-775 | TXT, QP | **No endpoint** | No | No |
| 13 | Short summary | **ABSENT** | — | — | — | — | — | — | — |
| 14 | Important points / key issues | PARTIAL | summary bullets; single `issue_nature` enum (14) | `summary`, `issue_nature` | LSP:550 | TXT, QP | PSM (`issue_nature`) | No | No |
| 15 | Points requiring response | PARTIAL | merged into `key_reply_points` | — | — | — | — | — | — |
| 16 | Points to address while replying | IMPLEMENTED (unstructured list) | `key_reply_points` (24) | `key_reply_points` (written only when non-empty, so stale values survive reprocess) | LSP:813-844 | TXT, QP | **No** | No | No |
| 17 | Commitments made | **ABSENT** | — | — | — | — | — | — | — |
| 18 | Requests / directions | ABSENT at ingestion | drafting-time regex `_action_requested` (`services/letter_drafting/incoming_analyzer.py:323-331`) | draft run only | drafting UI | No | confirm-analysis | — | — |
| 19 | Decisions | **ABSENT** | — | — | — | — | — | — | — |
| 20 | Notices | PARTIAL | `issue_nature` value "Contractual Notice", tag "Notice Compliance" | as left | LSP | TXT | PSM | No | No |
| 21 | Claims | PARTIAL | `claim_category` single enum (15) | `claim_category` | LSP:551 | TXT, QP | PSM | No | No |
| 22 | Variations | PARTIAL | `issue_nature` / tag / subtag | as left | LSP | TXT | PSM | No | No |
| 23 | Payment issues | PARTIAL | `issue_nature` / tag "Payment" | as left | LSP | TXT | PSM | No | No |
| 24 | Delay / hindrance | PARTIAL | `issue_nature` / tags / subtags | as left | LSP | TXT | PSM | No | No |
| 25 | EOT implications | PARTIAL | `claim_category` "EOT Claim", tag, prose; no EOT period | as left | LSP | TXT | PSM | No | No |
| 26 | Deadlines | ABSENT at ingestion | drafting-time `RESPONSE_DUE_PATTERN` (`incoming_analyzer.py:38-41`), run against the **summary** | draft run | drafting UI | No | — | — | — |
| 27 | Response due date | ABSENT at ingestion | `IncomingLetterAnalysis.response_deadline` (`models/letter_drafting.py:366`) | draft run | drafting UI | No | confirm-analysis | — | No |
| 28 | Contractual clauses mentioned | IMPLEMENTED | `contractual_clauses` (23) | `contractual_clauses`; graph Clause nodes (optional adapter) | LSP:778-811 | TXT, QP | **No** | No | No |
| 29 | Amounts / currencies | ABSENT at ingestion | drafting-time `AMOUNT_PATTERN`, first match only (`incoming_analyzer.py:19-22, 109`) | draft run | — | — | — | — | — |
| 30 | Quantities | **ABSENT** | — | — | — | — | — | — | — |
| 31 | Dates / events | PARTIAL | `linked_event_suggested` (19) single string; chronology rule extractor one event per doc (`services/chronology.py:568-631`) | `linked_event_suggested`; `matter_chronology_events` | LSP:555 | TXT | chronology verify/reject | chronology only | chronology `source_spans` (page lookup reads fields documents never set) |
| 32 | People / roles | **ABSENT** | companies only; `alleged_responsibility` party-type enum | — | — | — | — | — | — |
| 33 | Locations | IMPLEMENTED | `location`, `specific_area`, `chainage_from/to` (8, 9, 11, 12) | same | LSP:545-548 | TXT, QP | PSM (location only) | No | No |
| 34 | Attachments / enclosures | ABSENT (not extracted) | — | `documents.enclosures[]` uploaded manually, never processed/indexed | EnclosuresPanel | No | enclosure upload | — | — |
| 35 | Keywords | IMPLEMENTED | `keywords` (18), `additional_keywords` (21) — prompt labels both "Key Words" | same | LSP:572-577 | TXT, QP | PSM | No | No |
| 36 | Tags | IMPLEMENTED | `tags` → `extracted_tags` (26, allow-listed) | `extracted_tags` (separate from user `tags`) | LSP:580 | TXT, QP; search filter uses only user `tags` | PSM (**allow-list bypassed**, `routers/documents.py:174-191`) | No | No |
| 37 | Subtags | IMPLEMENTED | `sub_tags` → `extracted_subTags` (27) | `extracted_subTags` | LSP:585 | TXT, QP | PSM | No | No |
| 38 | Confidence score | **ABSENT** | none per field/doc; placeholders only (0.75 evidence graph; 0.85/0.65/0.25 drafting; 0.72/0.45 chronology) | — | — | — | — | — | — |
| 39 | Page/source citation per fact | **ABSENT** | — | — | — | — | — | — | — |

**Tally:** IMPLEMENTED 13 · PARTIAL 13 · ABSENT 13 (fields 5, 13, 17, 18, 19, 26, 27, 29, 30, 32, 34, 38, 39 absent at ingestion). Extra extracted fields not requested: `asset_type`, `work_type`, `priority`, `alleged_responsibility`, `reference_chain`, `specific_area` (partly non-editable).

Prompt defects: numbering skips #10; #18 and #21 both labelled "Key Words" (`openai_service.py:275-286`); the full letter is echoed as "Full Content" before fields 26/27 under a 4096-token output cap, so tags on long letters can be truncated (inferred). The echoed "Full Content" overwrites `documents.full_text` and becomes `authoritative_text` for drafting.

## 3. "Points to address while replying"

| Sub-item | Current derivation |
|---|---|
| allegations requiring rebuttal | merged into `key_reply_points` prose |
| requests requiring response | `key_reply_points`; drafting regex `action_requested` = first sentence with request/require/submit |
| contractual assertions | absent (only `contractual_clauses` list) |
| deadlines | absent at ingestion; drafting regex over summary |
| missing substantiation | absent |
| contradictions | absent |
| commitments | absent |
| unresolved issues | absent |
| questions | absent |
| payment claims | `claim_category` / `issue_nature` enum only |
| notices | enum/tag only |
| technical actions | absent |
| documents/evidence needed before reply | absent |

**Classification:** LLM-generated at ingestion as one flat `key_reply_points: List[str]` (prompt #24, "points that must be addressed in a future reply, claim defence, SoD, rejoinder…"), stored **on the document next to source facts with no advice/provenance flag**, not editable, no citation. On demand, the drafting workflow (`services/letter_drafting/incoming_analyzer.py:74-168`, `planning.py:99-134`) builds a reply matrix from `key_reply_points` with `[CONFIRM: …]` placeholders; that analysis is stored separately on the draft run and confirmed by the user (`services/letter_drafting/service.py:1335-1368`) but never flows back to the document.

**Verdict:** generated legal-advice-like content **is currently stored as if it were document metadata**. Recommended schema (for design, not implemented):

```json
{
  "reply_points_schema_version": "reply_points.v1",
  "extractor": {"model": "...", "prompt_version": "...", "run_id": "...", "created_at": "..."},
  "facts_from_letter": [
    {"id": "F1", "kind": "request|allegation|assertion|deadline|notice|payment_claim|commitment|question|decision",
     "text": "verbatim or near-verbatim", "page": 2, "span": [1043, 1188],
     "clause_refs": ["GCC 8.4"], "amount": {"value": 1250000, "currency": "INR"},
     "due_date": "2024-10-15", "confidence": 0.91}
  ],
  "issues_requiring_response": [
    {"id": "I1", "fact_ids": ["F1"], "type": "rebut|comply|clarify|reserve_rights|provide_evidence",
     "deadline": "2024-10-15", "priority": "high", "confidence": 0.84}
  ],
  "response_considerations": [
    {"id": "C1", "issue_ids": ["I1"], "text": "…", "nature": "generated_suggestion",
     "evidence_needed": ["hindrance register entry …"], "not_legal_advice": true}
  ],
  "review": {"state": "unreviewed|reviewed|approved", "by": null, "at": null, "edits": []}
}
```

Rules: `facts_from_letter` must carry page/span and be verifiable; `issues_requiring_response` reference facts; `response_considerations` are marked generated, never merged into facts, never embedded as source text, and excluded from `authoritative_text`.

## 4. References extraction & resolution

- **Extraction:** LLM prompt #6 only; no deterministic body scanner at ingestion. Post-processing regex in `services/reference_parser.py`: `DATE_MARKER_PATTERN` (`:11-14`, requires `dated|dtd|dt`) and `REFERENCE_LABEL_PATTERN` (`:16-18`, requires `no|number|#` after `LOA|letter|ltr|reference|ref`). Output `{letterNo, letter_no, date(DD-MM-YYYY), raw}`.
- **No patterns** for `Ref. [1]` numbered lists, drawings, BOQ items, variation orders, IPC/RA bills, EOT references or attachments. A `[1] …` item without a date marker keeps its whole text as `letterNo` and never resolves. `Ref: X dtd …` / `vide letter X dt …` keep the prefix (normalised code `ref-x…`) and never match.
- **Normalisation:** `normalize_letter_code` lowercases and replaces non-alphanumerics with `-`; empty → `"unknown"` (`services/falkor_graph_service.py:41-46`), stored as `letterNoNormalized`.
- **Resolution:** `ReferenceSyncService._resolve_target_document` (`services/reference_sync_service.py:507-595`): explicit `documentId` → `letterNoNormalized` equality → case-insensitive exact `letterNo`. Scoped by `_scope_filter` to source org + project (`:420-430`); **falls back to org-only when the source has no `project_id`** (default `""`); ignores reference date; no lifecycle/duplicate/deleted filter, no sort — a revision, duplicate or deleted doc can be linked. Bidirectional links in `references`/`referencedBy`, bucketed by source (parser/manual).
- **Unresolved:** queued in `reference_sync_queue` with org/project (`:247-300`); daily reaper 08:45 (`services/scheduler.py:64`), 200 per run, **expires after 30 days**; a newly arriving target does not trigger reverse lookup.
- **Graph:** `(:Letter {normCode})` MERGEd on `normCode` alone, shared across tenants (`falkor_graph_service.py:49-58, 225-259`); edges carry `owner_document_id`; built only from *resolved* references (`graph/graph_ingestion_service.py:507-548`). `delete_letter`'s preserve branch deletes **all** outgoing edges of the shared node (`falkor_graph_service.py:407-410`, re-verified). `get_thread` walks 6 hops; `graph_codes_denied` looks up `letterNoNormalized` with no org filter (`services/publication_policy.py:498`) — another tenant's codes can reach drafting context (codes only). `previous_letter_id` (ObjectId) is pushed as a letter code for REPLIES_TO (`graph_ingestion_service.py:550-558`). Optional adapter node ids `clause:{slug}`, `party:{slug}`, `letter-ref:{slug}` are unscoped (`:323-330`).

Fixture examples:
- `tests/test_reference_parser.py:9-28`: `"UPMRC/CPM-1/KANPUR/KNPCC-06/2024-25/Vol-2/1 17/104 dtd. 05.09.2024"` → letterNo `…/1 17/104`, date `05-09-2024`; `"Engineer's letter no. Kanpur-LET-JVTI-TBM-00371-E01 - dated 06.09.2024"` → `Kanpur-LET-JVTI-TBM-00371-E01`.
- `tests/test_metadata_critical.py:75-83,163-181`: `LOA no. 378/LMRC/CE-Contract/KNPCC-06/2021-22 dtd. 14-03-2022` → `378/LMRC/CE-Contract/KNPCC-06/2021-22`.
- `tests/test_reference_sync_service.py:213-260`: `DOC-002` resolves to the proj-1 target, not the org-2 one.

**Verdict: REFERENCE RESOLUTION = PARTIAL** — normalised text + document links (project-scoped when a project exists) + Falkor edges for resolved refs; unresolved retained only 30 days; category coverage limited to letter numbers.

## 5. Human validation — recommendation

Current state: no review/approve step, no confidence, no "verified" flag for correspondence metadata; `human_review_required` is an extraction-quality gate (unified path) that the UI does not display. Review workflows exist elsewhere and should be reused: chronology (`AI_SUGGESTED`/`NEEDS_REVIEW` → verify/reject, revisions), evidence-graph links (`AI_SUGGESTED` → `USER_VERIFIED`/`APPROVED`), drafting `confirmed_by_user`, contract clauses (`manually_edited` preserved on reprocess). Edit audit records field names only, not before/after.

| Field | Recommended policy | Auto-accept threshold (per-field confidence, once real confidence exists) |
|---|---|---|
| Letter number | HUMAN-REVIEWED | ≥0.95 **and** deterministic regex agreement with page-1 header; never from filename |
| Date | HUMAN-REVIEWED | ≥0.95 and parsed from the letter header, not "first date in text" |
| Sender / recipient | AUTO-ACCEPTED if matched to a known party entity; else REVIEWED | ≥0.90 with party match |
| Subject | AUTO-ACCEPTED | ≥0.85 |
| References | HUMAN-REVIEWED for unresolved or cross-project candidates; AUTO for exact same-project normalised match with date agreement | exact match only |
| Contract clauses | REVIEWED | ≥0.90 and clause exists in the project's `contract_clauses` |
| Response deadline | HUMAN-APPROVED | never auto; always show source span |
| Monetary amounts | HUMAN-APPROVED | never auto |
| Points to address while replying | HUMAN-APPROVED before use in drafting; stored as generated | never auto |
| Summary / keywords / tags | AUTO-ACCEPTED, editable | — |

Review UX: a per-document "Extraction review" panel showing each field with its page thumbnail + highlighted span, confidence chip, accept/edit/reject, bulk-accept for high-confidence fields, and a review queue filtered by project and by `human_review_required`/low-confidence state. Edits set per-field `source="human"` and are preserved on reprocess.
