# Chronology Foundation — Audit and Design Proposal

Status: **APPROVED DESIGN (owner decisions recorded 2026-10-11, §28). Nothing in this document is implemented.**
Date: 2026-10-11
Base: `contraclaim/release/contraclaim-rc1` = `c4f6706802465b18280e470c005d26badd6d801f`,
tree `e635e7ed25922c9646e978cd086c7146dae31007` (PR #58, Canonical Evidence Store, merged).
Implementation order is fixed by §26: A0 (existing-defect fixes) first; the new layer only after A0.

All `file:line` references are relative to `backend/rbac_backend/` unless prefixed with `client/`
or `docs/`. They were read at the base SHA above. "Inferred" marks a conclusion drawn from code
rather than observed behaviour. No test was run for this design; defects listed in §5 are code
reads, not red tests.

---

## 1. Executive summary

ContraClaim already has **three overlapping event representations**, none of which consumes the
Canonical Evidence Store:

| Store | Scope | How populated | Provenance | Review |
|---|---|---|---|---|
| `matter_chronology_events` (Chronology Builder; CL-3B made it a canonical link target) | one row per **matter chronology** | user-triggered rule extractor, one event per document, or manual entry | `source_document_id` + first page + first ~900 chars of body | `verification_status` + revision trail |
| `project_events` + `event_links` (Evidence Graph / Contract Timeline) | project | automatically, one "document event" per processed upload; register hooks; backfill; chronology verify | regex char offsets into an unrecorded text field, no page | link-level `ai_suggested → user_verified/approved/rejected` |
| `arbitration_chronology_matrix` | arbitration case | copied from `matter_chronology_events` by a deterministic adapter | inherits from the event | inherits |

The existing Chronology Builder is a working **matter-level, pleading-aware curation workspace**
with real review, revision, RBAC, export and arbitration wiring. What it is not is an
evidence-backed event store:

- one event per document;
- no fact/position split;
- no typed dates; the date falls back to the upload timestamp labelled `exact`;
- no canonical revision pin;
- events are copied per matter, so the same fact is verified separately in every chronology.

**Recommendation (Option B′, §24–25).**

- Add a **project-scoped, evidence-backed event layer**:
  - `chronology_assertions`: what one document says, pinned to a canonical span and revision;
  - `chronology_events`: the deduplicated event, supported by many assertions;
  - `chronology_extraction_runs`: idempotent processing status per `(document, canonical_revision, extractor)`.
- **Evolve** the Chronology Builder (`matter_chronologies` / `matter_chronology_events`) into the
  **matter view** over that layer. A matter row references a project event and keeps only
  pleading-specific curation fields: perspective, pleading use, annexure number, legal relevance.
- Keep `project_events` / `event_links` as the **derived** timeline and graph projection.
  The 2026-06 plan already declares them derived: `docs/architecture/chronology_builder_integration_plan.md:23-27`.
- Reuse `event_links` for event↔event and event→register relations.

This gives one source of truth for events (`chronology_events`), one for evidence (the canonical
store) and one for matter curation (`matter_chronology_events`), with no new parallel chronology UI.

**Readiness.** Implementation can start after the owner decisions in §28. The first PR is
schema, provenance and idempotency only, with no extraction (§26). Production is unaffected
throughout:

- every production document is `legacy_v0` and reads as canonical `not_built`;
- extraction ships behind a default-off flag.

---

## 2. Current chronology implementation

### 2.1 Chronology Builder (`matter_chronologies`)

**Models** (`models/chronology.py`)
- `MatterChronology` (:134-180): `organization_id`, `project_id` (required), `contract_id`, `matter_id`, `claim_id`, `title`, `chronology_type`, `party_perspective`, `selected_source_ids`, `status`, `summary_counts`, soft-delete fields.
- `MatterChronologyEventBase` (:183-225):
  - Scope: `chronology_id` (required).
  - Dates: `event_date`, `event_end_date`, `date_text`, `date_type`.
  - Content: `title`, `description`.
  - Source: `source_document_id`, `source_document_name`, `source_page`, `source_paragraph`, `source_spans[] {page, paragraph, start, end, text}`.
  - Correspondence: `letter_no`, `from_party`, `to_party` (free strings).
  - Tags: `contract_clauses[]` (free strings), `issue_tags[]`, `claim_heads[]`.
  - Assessment: `responsible_party`, `supports_party`, `event_classification`, `impact_type`, `impact_days`, `impact_amount`, `confidence_score`, `verification_status`.
  - Notes and use: `manual_notes`, `legal_relevance`, `pleading_use`.
  - Links: `related_event_ids`, `related_document_ids`, `project_event_id`, `event_link_ids`, `ai_extraction_id`, `annexure_no`, `duplicate_of_event_id`, `metadata`.
- `MatterChronologyEventRevision` (:282-294): append-only, unique `(chronology_id, event_id, revision)`.

**Enums (verbatim, reuse candidates)**
- `ChronologyType`: general_dispute, eot_delay, variation, payment, termination, force_majeure, defect_dlp, bank_guarantee_retention, counterclaim, other.
- `ChronologyDateType`: exact, approximate, inferred, range, undated. This is date *precision*, not date *meaning*.
- `ChronologyEventClassification`: admitted_fact, disputed_fact, notice, breach, mitigation, delay, payment, variation, contractual_trigger, evidence_only, other.
- `ChronologyVerificationStatus`: ai_suggested, verified, edited_verified, rejected, duplicate, needs_review.
- `ChronologySupportsParty`, `ChronologyImpactType`, `ChronologyPleadingUse`, `ChronologyRevisionAction`, `ChronologyExportType`.

**API** (`routers/chronology.py`, mounted at `/api`)
- CRUD on `/chronologies` and `/chronologies/{id}/events`.
- Event actions: `/extract`, `/verify`, `/reject`, `/mark-duplicate`, `/link`, `/revisions`, `/pleading-context`.
- `/arbitration/drafts/{id}/attach-chronology` and `/chronology-context`.
- Exports: docx, xlsx (actually CSV), pdf, evidence-index (same CSV).
- Auth: `active_scope` selection → `require_record` → `PolicyService.authorize_document(..., resource_type="matter_chronology")` (:71-86).
- Permissions: `dms.chronology.view/create/edit/verify/export/admin`, gated by entitlement `feature.dms.chronology` (`services/entitlement_service.py:116-121`).

**Extraction** (`services/chronology.py:479-684`)
- Runs synchronously inside the request.
- Deterministic regex rules; no LLM; `model="deterministic-rule-extractor"`.
- Produces **one event per document**.
- Fixed confidence: 0.72 when a date was found, 0.45 otherwise.
- Idempotency key: `(chronology_id, source_document_id, metadata.content_hash)`, with a non-unique index.
- Text comes from `select_body_text` via the publication policy, **not** from canonical evidence.

**Review**
- `verify_event` / `reject_event` require VERIFY.
- `verify_event` also syncs a new `project_events` row plus `user_verified` `event_links` (`_sync_verified_event`, :838-932).

**UI** (`client/src/pages/ChronologyBuilderPage.tsx`, 760 lines)
- List, create, extract, status filter, verify/reject, attach to a draft by typed id, export links.
- `EntityDocumentLinks` panel per event (CL-3B).
- There is no event editing, no manual event entry, no table view and no import.

### 2.2 Evidence Graph / Contract Timeline (`project_events`, `event_links`, `ai_extractions`)

**Models** (`models/evidence_graph.py`)
- `ProjectEventType`: letter, instruction, delay, payment, drawing, milestone, meeting, claim, variation, bank_guarantee, key_date, hindrance, constraint, other.
- `EventRelationType`: refers_to, replies_to, supersedes, supports_claim, rebuts_claim, issues_instruction, records_default, seeks_information, issued_through, causes_delay, causes_variation, clarifies_scope, supports_measurement, affects, governed_by, relates_to.
- `EventLinkStatus`: ai_suggested, user_verified, rejected, approved.
- Links are append-only revisions per `link_group_id`.

**Automatic population**
- `EvidenceGraphService.ingest_document_metadata` (:568) is called from `DocumentService._publish_graph_and_evidence_from_current` (`services/document_service.py:256`) on every processed, publishable upload.
- It creates one "document event" per document, keyed on `(source_entity_type="document", source_entity_id)`, and never updates it.
- If the document has no date, `event_date` falls back to `utcnow()` (:782-784).
- Link suggestions are regex and reference-number based, all `ai_suggested`.

**Exposure**
- `GET /api/contracts/timeline` (`routers/evidence_graph.py:370`, `dms.contract.timeline.view`) → `ContractTimelinePage.tsx`.
- Register hooks also write here:
  - Hindrance (`hindrance_register_service.py:602-660`): idempotent and updates on edit.
  - Drawing and Programme Milestone (`evidence_register_service.py:355-418`): no idempotency, no update sync.
- An admin backfill (`evidence_graph_backfill_service.py`) also writes here.
- FalkorDB: **nothing** from the evidence graph is written to Falkor.

### 2.3 Arbitration consumption

- `chronology-builder-adapter` (`services/arbitration_drafting/agents/deterministic.py:220-258`) copies verified `matter_chronology_events` into `arbitration_chronology_matrix`.
  - It selects by **case org/project**, not by chronology (:1035, :1272), with a limit of 100.
- The context builder (`services/arbitration_drafting/context.py:1101-1203`) turns matrix rows into ledger rows `[S{n}: citation]`, with a page only if the event has `source_page`. There is no span.
- Every pleading type requires the chronology matrix (`workflow_domain.py:119-133`).
- No LLM prompt anywhere generates a chronology.
  - The closest is the per-letter `SUMMARY_EXTRACTION_INSTRUCTION` (`models/document_metadata.py:30-37`), which lists events within one letter as narrative.
  - Item 19 `linked_event_suggested` is free text, deliberately excluded from vectors.

### 2.4 Other "timelines" (not chronology)

These are not chronology and are out of scope:
- `StrategyContextTimelineEntry`: the conversation chain for drafting (`strategy_context_service.py:100-118`).
- The arbitration workflow-step timeline.
- The document activity timeline in `DocumentViewerPage`.

---

## 3. CL-3B audit

"CL-3B" was the 2026-09-23 correspondence-linking PR (`docs/architecture/correspondence_linking_cl3b_programme_chronology_2026-09-23.md`). It is merged into this release (commits `ac612666`, `2e28d9e1`). It did not create the chronology register. It bound the existing Chronology Builder **event** into canonical Document linking.

| Question | Answer |
|---|---|
| Purpose | Make `chronology_event` (and `programme_milestone`) a canonical `entity_document_links` target under the navbar selection |
| Schema | Unchanged `matter_chronology_events`, plus `document_relationship_revision` (`$inc` at `entity_adapter_registry.py:2170-2177`) |
| Provenance rule | `source_document_id` is the event's single extraction provenance. It is read-only role `source_document`, never a link row, and stays visible next to links |
| Linkable roles | `correspondence`, `supporting_document` (`entity_adapter_registry.py:158-166`) |
| Legacy field | `related_document_ids` add path removed (409); subset or echo allowed; read through as `supporting_document` |
| Scope | Event scope = parent chronology; an archived parent is read-only; contradicting org/project → does not load |
| Deletion | `legacy_blocks_document_deletion = False`: chronology references never block Document deletion; canonical link rows still do |
| Manual vs automated | Both: manual create/patch, and the rule extractor on request |
| Persistence | Mongo, authoritative. The evidence graph is a derived copy on verify |
| Export | docx / CSV / pdf, selection-blind (recorded debt #24) |
| Downstream | Arbitration adapter + attach-to-draft; Contract Timeline via the verify sync |
| Authoritative or report? | **Authoritative for matter curation** (review states, revisions, VERIFY permission), but **not evidence-grade**: one event per document, no canonical pin, no fact/position split |

**Conclusion.** CL-3B's adapter, roles, provenance rule and deletion semantics are sound and must be preserved. The weakness is in the underlying event model and extractor, not in CL-3B.

---

## 4. Current data flow

```
                    upload ──► document_processing_jobs ──► process_document_async
                                                                   │
            ┌──────────────────────────────────────────────────────┼─────────────────────────────┐
            ▼                                                      ▼                             ▼
  document_extraction_heads / document_ocr_pages      documents (LLM metadata:      Falkor :Letter{normCode}
  (CANONICAL EVIDENCE; unified path only;             letterNo, date, from, to,     CITES edges (global key)
   OFF in production → legacy_v0 = not_built)          reference[], tags …)
            │  (no consumer yet)                               │
            ✕                                                  ├─► ReferenceSyncService ─► documents.references /
                                                               │                           referencedBy (resolved)
                                                               └─► EvidenceGraphService.ingest_document_metadata
                                                                     ─► project_events (1/doc) + event_links (ai_suggested)
                                                                                     ▲
  user: POST /chronologies/{id}/extract ─► rule extractor (body text, 1 event/doc) ─► matter_chronology_events
                                                    │ verify                                  │
                                                    └─► new project_events + event_links ─────┘
                                                                                              │
  arbitration chronology-builder-adapter ◄──── verified matter_chronology_events (by org/project)
        └─► arbitration_chronology_matrix ─► context ledger [S#] ─► SOC / SOD / Rejoinder drafts
```

---

## 5. Gaps and defects found by the audit

Gaps (G) block the objective. Defects (D) are existing bugs observed by code read. None is fixed by this design session.

| # | Finding | Evidence |
|---|---|---|
| G1 | No chronology consumer reads canonical evidence. `get_document_canonical_evidence` has no non-test callers | `canonical_evidence_service.py:149`, grep |
| G2 | One event per document; no multiple events or dates per letter | `chronology.py:603-684` |
| G3 | No typed date semantics. `date_type` is precision only. Documents have only `date` (no received or sent date) | `models/document.py:77` |
| G4 | No fact vs party-position distinction (`classification` mixes `admitted_fact`/`disputed_fact` with topic) | `models/chronology.py:58-69` |
| G5 | Spans carry no offsets into canonical text and no revision/sha pin | `chronology.py:652-668`; `evidence_graph_service.py:756-765` |
| G6 | The same fact is copied per matter chronology; no event↔mention model; no cross-document dedup | `models/chronology.py:184` |
| G7 | No reply-to link between Documents. `previous_letter_id` is set only by unmounted legacy code. Falkor `REPLIES_TO` is keyed on a document ObjectId, so it can never meet a letter node | `routers/documents.py:2769-2827`; `graph/graph_ingestion_service.py:550-558` |
| G8 | Reference resolution ignores the referenced letter's date; first match by `updatedAt` wins on number collisions | `reference_sync_service.py:507-595` |
| G9 | Sender and recipient are free strings; parties have no Contractor/Employer/Engineer role | `models/document.py:79-80`; `models/party.py:47` |
| G10 | Clause references are unresolved strings; nothing ties them to Contract Master `ApplicableInstrument` | `models/chronology.py:204` |
| G11 | No publication hook after canonical publish; no change streams | `document_processor.py:516-539` |
| G12 | `get_document_canonical_evidence` requires a request user + selection, so a background job has no scoped read path | `canonical_evidence_service.py:149-179` |
| G13 | No LLM token/cost accounting anywhere | grep; `pydantic_ai_service.py:272-281` |
| G14 | No issue entity. Arbitration issues live in matrix rows keyed `issue_key`; chronology `issue_tags` is free | `deterministic.py:595-655` |
| G15 | Enclosures are not Documents (no text, no metadata) — out of scope here, noted for evidence completeness | `models/document.py:41-50` |
| D1 | `_event_date` falls back to upload `created_at` and labels it `exact`. The evidence-graph doc event and backfill fall back to `utcnow()` | `chronology.py:733-737`; `evidence_graph_service.py:782-784`; `evidence_graph_backfill_service.py:212-216` |
| D2 | Create and PATCH of an event accept any `verification_status` with only EDIT, bypassing VERIFY and the graph sync | `chronology.py:290-338`; `models/chronology.py:214,259` |
| D3 | Editing a verified event does not re-sync its `project_event` (the early return when `project_event_id` is set) | `chronology.py:839-840` |
| D4 | Duplicate `project_events` per document: ingest doc event + one per chronology verify, same `(document, id)` key | `chronology.py:855-856`; `evidence_graph_service.py:777` |
| D5 | `list_links` filters `status` before collapsing to the latest revision, so superseded `ai_suggested` revisions resurface; 5000 cap | `evidence_graph_service.py:347-364` |
| D6 | The arbitration adapter scopes by org/project, not chronology, and includes events of soft-deleted chronologies (inferred). `impact`/`issue_link` are always null because of field-name mismatches | `deterministic.py:1035-1051, 233-250` |
| D7 | Ambiguous-date checker regex ignores dot separators (`dd.mm.yyyy` is common in Indian correspondence) | `services/extraction/quality/date_checks.py` |
| D8 | `ChronologyExtractRequest.include_existing_project_events` and `matter_chronology_exports` are dead | `models/chronology.py:299`; `core/database.py:564` |
| D9 | CLAUDE.md says drafting LLM calls pass `strict=True` to `LLMGenerator.generate`; on this release that signature has no `strict` and swallows errors into a fallback string | `retrieval/generator.py:39-60`; `letter_drafting/section_editor.py:60` |
| D10 | Falkor `normCode` is global (no tenant in key); 307/310 legacy edges unowned | `falkor_graph_service.py:50-58`; `graph_letter_state_migration.py:6-10` |

---

## 6. Proposed event model

Design principle: **a document asserts; an event is what the assertions are about.** Smallest
durable schema = three collections plus revisions. Relations reuse `event_links`.

### 6.1 `chronology_assertions` (evidence mention / candidate)

One row = one thing one document says, at one canonical span. It is immutable apart from review fields and the re-anchor outcome.

| Field | Purpose |
|---|---|
| `_id` | deterministic idempotency key (§20.2) |
| `organization_id`, `project_id` | copied from the source Document at extraction; never from the caller |
| `document_id` | source Document |
| `canonical_revision`, `canonical_sha256` | provenance pin (§9) |
| `page_number`, `start`, `end` | span in **canonical document text**; must lie within one page span from `page_map` |
| `page_content_sha256` | from `page_map`; enables cheap re-anchoring |
| `quote_sha256`, `quote_len` | integrity pointer for `text[start:end]` (no duplicate source text stored) |
| `kind` | `record` \| `position` \| `joint_record` (§8) |
| `event_type` | controlled taxonomy (§7.3) |
| `dates[]` | typed date mentions (§7) |
| `asserted_by` | `{party_role, party_label, party_id?}`: who is speaking (normally the document author) |
| `actor`, `affected_party` | `{party_role, party_label}`: who did it, who was affected |
| `stance` | for `position`: `alleges` \| `admits` \| `denies` \| `disputes` \| `reserves` \| `requests` \| `undertakes` |
| `proposition` | short normalised statement (≤ 300 chars) generated from the span; never presented as source text |
| `references[]` | explicit document references found in the span: `{letter_no_raw, date_raw, resolved_document_id?}` |
| `amounts[]` | `{value, currency, quantity?, unit?, start, end}` |
| `clause_mentions[]` | `{text_as_written, start, end}`, **unresolved** (§17) |
| `issue_codes[]` | controlled issue vocabulary (§15) |
| `extraction` | `{method: deterministic\|llm\|manual, extractor_id, extractor_version, model?, prompt_version?, run_id}` |
| `confidence` | 0..1 computed per rule/model, never a constant |
| `status` | `candidate` \| `needs_review` \| `auto_confirmed` \| `approved` \| `rejected` \| `stale` \| `superseded` |
| `review_flags[]` | e.g. `page_needs_review`, `ambiguous_date`, `legacy_metadata_only`, `low_confidence` |
| `event_id` | the `chronology_events` row this supports, set on attach |
| `reviewed_by`, `reviewed_at`, `review_note` | |
| `superseded_by_assertion_id` | set on re-extraction or re-anchor (§12) |
| `created_at`, `updated_at` | |

### 6.2 `chronology_events` (authoritative event)

One row = one real-world occurrence or one party position, project-scoped and deduplicated.

| Field | Purpose |
|---|---|
| `_id` | uuid4; stability comes from assertions, not the event id |
| `organization_id`, `project_id` | |
| `event_type`, `title`, `description` | the description is user-approved text, not source text |
| `event_kind` | `occurrence` (something happened) \| `position` (a party asserted something) |
| `primary_date` | `{value, precision (reuse ChronologyDateType), date_role}`; nullable when unknown |
| `period_start`, `period_end` | for durations such as access delays or suspensions |
| `parties` | `{actor, affected[], position_holder?}` |
| `evidence_status` | `recorded` \| `alleged` \| `admitted` \| `disputed` \| `confirmed` \| `contradicted` \| `stale_evidence` (derived from supporting assertions, overridable on review; §8) |
| `review_status` | `candidate` \| `needs_review` \| `auto_confirmed` \| `approved` \| `rejected` \| `superseded` \| `merged` |
| `supporting_assertion_ids[]` | many mentions → one event |
| `primary_assertion_id` | the assertion whose span is the default citation |
| `issue_codes[]`, `location`, `work_package` | filter dimensions (§15) |
| `clause_links[]` | resolved clause citations only (§17) |
| `merged_into_event_id`, `supersedes_event_id` | |
| `revision` | `$inc` on every change; full snapshot in `chronology_event_revisions` |
| `created_by`, `created_at`, `updated_by`, `updated_at`, `approved_by`, `approved_at` | |

Register links (claim, EOT submission/determination, variation, delay event, key date, programme
milestone) and event↔event relations are **not** embedded arrays. They are `event_links` rows
with `source_type="chronology_event"`, which reuses the existing revisioned, reviewable link
model (§10, §16).

### 6.3 `chronology_extraction_runs`

| Field | Purpose |
|---|---|
| `_id` | `sha256(document_id \| canonical_revision \| extractor_id@version)` |
| `organization_id`, `project_id`, `document_id`, `canonical_revision`, `canonical_sha256` | |
| `extractor_id`, `extractor_version` | |
| `status` | `queued` \| `processing` \| `completed` \| `partial` \| `needs_review` \| `failed` \| `blocked` \| `superseded` |
| `blocked_reason` | `not_built` \| `not_consumable` \| `deleted` \| `duplicate_pending` |
| `attempts`, `lease_owner`, `lease_until`, `error_code` | same lease/fence pattern as Contract Master reprojection |
| `counts` | `{pages_eligible, pages_skipped_review, assertions_created, assertions_reused, …}` |
| `llm_usage` | `{model, input_tokens, output_tokens, est_cost_usd, calls, fallbacks}` (§21) |

### 6.4 `chronology_event_revisions`

Same shape as `matter_chronology_event_revisions`: unique `(event_id, revision)`, plus `before`, `after`, `action`, `actor` and `note`.

### 6.5 What is deliberately *not* in the event

- Pleading use, perspective, annexure number and legal relevance stay on the matter row (§14).
- Source text stays in the canonical store, not copied into the event.
- Free-text `from_party`/`to_party` are replaced by role-typed parties.

---

## 7. Date semantics

### 7.1 Date mention

Each entry in `assertion.dates[]` has this shape:

```
{ role, value_start, value_end?, precision, raw_text, start, end,
  explicit: true|false, confidence, flags: [ambiguous_dm, two_digit_year, …] }
```

### 7.2 `date_role` vocabulary

| Group | Roles |
|---|---|
| Document dates | `DOCUMENT_DATE`, `SENT_DATE`, `RECEIVED_DATE` |
| Reference date | `REFERENCE_DOCUMENT_DATE` |
| Occurrence | `EVENT_DATE`, `PERIOD_START`, `PERIOD_END` |
| Acts | `INSTRUCTION_DATE`, `NOTICE_DATE`, `SUBMISSION_DATE`, `APPROVAL_DATE`, `REJECTION_DATE` |
| Site and works | `ACCESS_DATE`, `COMMENCEMENT_DATE`, `COMPLETION_DATE`, `MEETING_DATE` |
| Money | `PAYMENT_DATE`, `CERTIFICATION_DATE` |
| Time limits | `DEADLINE` |
| Fallback | `UNSPECIFIED`: a date is present but its meaning is uncertain |

### 7.3 Date rules

1. **No collapse.** An assertion keeps all its dates. `chronology_events.primary_date` is picked by rule from the event type, for example:
   - `ACCESS_DELAYED` → `EVENT_DATE` or `PERIOD_START`;
   - `CORRESPONDENCE_ISSUED` → `DOCUMENT_DATE`.

   If no date of the required role exists, `primary_date` is null and the event is `needs_review`. It is never filled from another role.
2. **Never use system timestamps.** `created_at`, `uploadedAt` and `utcnow()` never become chronology dates. This fixes D1 for the new layer.
3. **Ambiguity is explicit.** `03/04/2024` with no project convention → `flags: [ambiguous_dm]`, `confidence ≤ 0.5`, status `needs_review`. A project-level `date_convention` (day-first default for Indian contracts; owner decision §28 OD4) resolves it deterministically. Dot separators are included, which fixes D7 for this layer.
4. **Relative dates** ("within 7 days of the above letter") are `explicit=false` and stay `UNSPECIFIED`. No arithmetic is done in extraction; computing deadlines is later work.
5. **Document date.** Canonical text is authoritative. The LLM metadata `documents.date` is a hint only. A `DOCUMENT_DATE` is `explicit` only when it is found in canonical page-1 text.

### 7.4 Worked example

A letter dated 25 Mar says: "with reference to your letter dated 15 Mar, access was not provided on 10 Mar".

| Role | Value | Attaches to |
|---|---|---|
| `DOCUMENT_DATE` | 25 Mar | record assertion "letter issued" |
| `REFERENCE_DOCUMENT_DATE` | 15 Mar | inside `references[]`; also used to disambiguate reference resolution (fixes G8 for this layer) |
| `EVENT_DATE` | 10 Mar | position assertion "access not provided" |

These are three dates in two assertions. Nothing is collapsed.

---

## 8. Fact vs party-position model

`assertion.kind`:

| Kind | Meaning | Example |
|---|---|---|
| `record` | the document's own existence or act is the fact | "Contractor issued letter ABC-123 dated 25 Mar to the Engineer" — true by the document itself |
| `position` | the author asserts something about the world | "Access was delayed by the Employer for 42 days": `asserted_by=Contractor`, `stance=alleges` |
| `joint_record` | a document signed or issued in a way that binds more than one party | jointly signed MoM, Engineer's certificate, measurement record — treated as `record` for each signatory |

`event.evidence_status` is derived from the supporting assertions. The rules, in order:

1. Only `record` support → `recorded`.
2. Only `position` support from one side → `alleged`.
3. A `position` by the party *against* whom it is made with `stance=admits` → `admitted`.
4. Opposing positions (`alleges` vs `denies`/`disputes`) → `disputed`.
5. Allegation plus independent `record`/`joint_record` support (e.g. Engineer's site record) → `confirmed`, which needs reviewer approval and is never automatic.
6. Record support inconsistent with the position → `contradicted`, set by the reviewer only.

A reviewer may override the derived status. The override is recorded in a revision with a reason.

**The rule that matters.** An extractor may never create an `occurrence` event with `evidence_status ∈ {confirmed, admitted}` from a single party's letter. "Access was delayed by the Employer for 42 days" produces:

- record: *Contractor issued letter ABC-123 asserting access delay.*
- position: *Contractor alleges Employer-caused access delay of 42 days from 10 Mar*, with `event_kind=position` and `evidence_status=alleged`.

Party roles. `party_role` uses the vocabulary `employer`, `engineer`, `general_contractor`, `contractor`, `jv_member`, `consultant`, `subcontractor`, `authority`, `other`, `unknown`. Roles resolve from the sender string through a **project party-role map** (§28 OD5). Unknown senders resolve to `unknown` plus a review flag. Roles are never guessed from the document's `uploadType`.

**Project party-role map (approved, §28 OD5).** Contract Master stays the source for contractual instruments and party names; the map does not replace those identities. The map is a project-scoped, admin-maintained, auditable collection (`project_party_roles`):

| Field | Purpose |
|---|---|
| `organization_id`, `project_id` | scope |
| `party_ref` | `{contract_master_party?, party_id?, label}`: points at the Contract Master identity where one exists |
| `role` | from the vocabulary above |
| `member_of` | for JV/consortium members: the JV entry they belong to |
| `aliases[]` | sender strings and letterhead variants that resolve to this entry |
| `effective_from`, `effective_to` | role changes over time (e.g. a new Engineer appointed) |
| `revision`, `created_by/at`, `updated_by/at` | every change is a revision; history is never overwritten |

Resolution is by alias and by the document date within the effective period. An ambiguous or unmatched sender stays `unknown` and flagged.

---

## 9. Evidence / provenance model

The acceptance criterion from `docs/CANONICAL_EVIDENCE.md:149-153` is adopted unchanged. A citation is:

```
document_id + canonical_revision + canonical_sha256 + page_number + [start, end) in canonical text
            + page_content_sha256 + quote_sha256
```

Validation runs at assertion creation in the extraction run (EVIDENCE SPAN VALIDATION stage):

1. `CanonicalEvidence.status` is built, the manifest `canonical_sha256` matches the run's, and `publication_consumable` is true. Otherwise there is no assertion and the run is `blocked`.
2. `start < end`, and `page_at(start) == page_at(end-1) == page_number`. A span never crosses a page; multi-page facts produce one assertion per page.
3. The page is not `unresolved` and not `text_withheld`.
4. If the page is `needs_review`, the assertion gets the flag `page_needs_review`, its status is forced to `needs_review`, and it can never be `auto_confirmed`.
5. `quote_sha256 = sha256(text[start:end])` is stored. On read, a consumer recomputes it from the current canonical text. A mismatch means the citation is invalid: it fails visibly and is never served as a quote.

Excerpts are always served by slicing canonical text at read time through the scoped reader. Nothing in the chronology collections stores customer text except reviewer-written `title`, `description` and `proposition`.

Legacy (`legacy_v0`, canonical `not_built`) documents get no span assertions. Whether they can contribute metadata-only, non-citable candidates was owner decision §28 OD3: **no** automatic evidence-grade assertions; they appear in a "not yet chronology-eligible" count instead, and existing Builder rows stay visible labelled legacy / not evidence-pinned.

Paragraph and block offsets can be added later inside a page span without changing this contract.

---

### 9.1 Full-document context (extraction input)

- The extractor reads **complete canonical text**, never vector chunks.
- Windowing for large documents:
  - the window = one page plus the tail of the previous page and the head of the next (bounded characters), so qualifications that straddle a page break are seen;
  - assertions are still pinned to one page.
- A **document header context** is prepended to every window: letter no, date, from/to, subject, the reference list, and section headings detected on earlier pages. This keeps "with reference to para 3 above" interpretable.
- Tables: the canonical page `tables` structure is passed as structured rows and is not flattened.
- Footnotes and enclosure lists are kept in the page text as published.
- Reconciliation pass per document: merge duplicate candidates from overlapping windows (identical span or same type+date+actor) before persistence.
- There is no truncation. If a page exceeds the window budget, it is split at paragraph boundaries with overlap, and the run records `pages_split`.
- Enclosures are not Documents today (G15), so they are out of reach until enclosure extraction exists. This is recorded as a limitation, not worked around.

---

## 10. Candidate vs approved lifecycle

```
                 extraction run (doc, rev)
                          │
                ┌─────────▼─────────┐
                │ assertion:        │──reject──► rejected (kept)
                │ candidate /       │
                │ needs_review      │──approve──► approved ─┐
                └─────────┬─────────┘                       │
           auto-confirm   │ (only §13.2 class)              │ attach (new or existing event)
                          ▼                                 ▼
                    auto_confirmed ───────────────► chronology_event
                                                    candidate → approved / rejected
                                                    approved → merged | superseded
   new canonical revision ──► re-anchor (§12) ──► carried forward | stale | superseded
```

- An event becomes `approved` only when a VERIFY holder approves it, or it is created by the auto-confirm class.
- An approved event needs at least one supporting assertion in `approved` or `auto_confirmed`.
- **Consumers (Matter view, drafting, graph projection) read only `approved` or `auto_confirmed` events by default.** Candidates are visible only in the review queue. This matches the existing `pleading_context` rule (`chronology.py:520-522`).

---

## 11. Deduplication model

Dedup is about attaching assertions to events. It is not about deleting assertions. Every mention survives as evidence.

1. **Same-document key (deterministic, auto).** Two runs of the same `(document_id, canonical_revision, extractor_version)` produce identical assertion ids, so they are a no-op (§20).
2. **Event-match proposal (deterministic scoring, never auto-merged).** For a new assertion, candidate events in the same project are scored on:
   - same `event_type` family;
   - date overlap within tolerance (exact for `record`; ±3 days or period overlap for occurrences);
   - same `issue_codes` ∩ location/work package;
   - same actor/affected roles;
   - **explicit reference**: the assertion's document references the document of an existing supporting assertion.

   Score ≥ threshold gives `proposed_event_id` on the assertion. A reviewer accepts with *attach* or rejects with *new event*.
3. **Never auto-merge across documents** in the initial release. The one exception: a `record` assertion whose `document_id` already backs a `record` event of the same type (a re-extraction carried forward).
4. **Merge** of two approved events is a reviewer action. It sets `merged_into_event_id`, moves the supporting assertions and writes a revision on both. It is reversible by an "unmerge" revision.
5. Positions are never merged into occurrences. "Contractor alleges delay" and "Engineer denies delay" are two `position` events linked to one `occurrence` event by `event_links` (`relation_type` `rebuts_claim` or a new `disputes`), which drives `evidence_status=disputed`.

The existing model cannot support this, because `matter_chronology_events` is per chronology with one `duplicate_of_event_id`. That is the core reason a project-level layer is needed (§24).

---

## 12. Document-reference linking

- **Primary signal.** Explicit references in canonical text, found deterministically: letter number patterns plus "your letter dated …".
- Resolution reuses the existing `ReferenceSyncService` resolver order: `documentId` → `letterNoNormalized` → regex. **Additionally, the referenced date must match `REFERENCE_DOCUMENT_DATE` when one is present.** On a number collision with no date match, the reference stays unresolved and is flagged. It never falls back to first-match (G8).
- Output is an assertion `references[]` entry. When resolved, an `event_links` suggestion is made from this document's `record` event to the referenced document's `record` event:
  - `refers_to` by default;
  - `replies_to` only when the text says so explicitly ("in reply to", "in response to").

  Both start as `ai_suggested`, except `refers_to` from an exact letter-number **and** date match, which may be `auto_confirmed` (§13.2).
- Candidate events derived from B may then propose links to events derived from A (`responds_to`, `contradicts`, `confirms`). These are always suggestions.
- This layer does **not** write `documents.references`. That stays owned by `ReferenceSyncService.sync_bidirectional` (CLAUDE.md decision). It also does not fix the Falkor `REPLIES_TO` defect (G7); that is the graph PR's concern.

---

## 13. Canonical revision handling

`canonical_revision` increments on **every** publish, including a retry with identical content (`extraction_adapters/document_page_store.py:484`). So:

On a new revision N+1 of document D:

1. **Identical content** (`canonical_sha256` unchanged): re-pin every assertion's revision and sha with no review impact. Write a revision note.
2. **Changed content.** For each assertion pinned to N:
   - (a) `page_content_sha256` unchanged for its page **and** `text[start:end]` hashes to `quote_sha256` at the new page offset delta → **carried forward**. The assertion is re-pinned, keeps its status, and the trail records old and new pins.
   - (b) Otherwise, a unique exact match of the quote within the same page → **re-anchored**. The assertion is re-pinned and flagged `re_anchored`, but because the surrounding page content changed, any approved event it supports returns to `needs_review`. Approval is never silently preserved against changed evidence.
   - (c) Otherwise → `stale`. If it backed an approved event:
     - if the event still has another approved supporting assertion, it stays approved with a warning;
     - if not, it becomes `evidence_status=stale_evidence, review_status=needs_review`, and consumers exclude it or show `[Evidence changed — review required]`.
3. A new extraction run for N+1 produces fresh assertions. Those identical to carried-forward ones dedupe by id. Others are new candidates.
4. Old pins are never silently left pointing at invalid spans: the read path's `quote_sha256` check (§9.5) is the backstop.

Owner decision (§28 OD7): **only identical evidence (case 1, or case 2a with an unchanged page) keeps approval. Anything that cannot be deterministically re-pinned to identical evidence is `stale`; an approved event whose only authoritative support goes stale returns to review.**

---

## 14. Human review

### 14.1 Review queue (conceptual UI, built in PR-F)

A row per assertion or event shows:
- type and kind (fact or position), and typed dates;
- asserted-by, actor and affected party roles;
- issue codes;
- source document (letter no, date) with **page N** and the **canonical excerpt**, sliced live with ± context;
- confidence and review flags.

Actions: **Approve**, **Edit then approve** (edits recorded), **Reject** (reason), **Attach to event / New event**, **Merge**, **Mark disputed / admitted**.

### 14.2 Auto-confirm class (deliberately narrow)

An assertion may be `auto_confirmed` only if **all** of these hold:
- `kind=record`;
- `event_type=CORRESPONDENCE_ISSUED`;
- `method=deterministic`;
- the letter number and `DOCUMENT_DATE` are both found verbatim on canonical page 1;
- page 1 is not `needs_review`;
- no `ambiguous_dm` flag;
- `publication_consumable`.

Plus explicit `refers_to` links matched on number **and** date.

Everything semantic (positions, occurrences, instructions inferred from wording, clause links, issue tags from AI) requires a human (§28 OD2).

### 14.3 Permissions (approved capability model, §28 OD6)

Explicit capability permissions, following the existing `dms.*` naming. Each is organisation/project scoped through the normal selection + policy + row-scope path; none is implied by a role name.

| Permission | Allows |
|---|---|
| `dms.chronology.view` (exists) | read events, assertions, matter views, excerpts (excerpts also need `dms.document.view` on the source) |
| `dms.chronology.create_candidate` (new) | create manual candidate assertions/events; trigger an authorised per-document extraction run |
| `dms.chronology.edit` (exists) | edit non-review fields of candidates and matter rows |
| `dms.chronology.verify` (exists) | approve, edit-then-approve, mark admitted/disputed/confirmed |
| `dms.chronology.reject_merge` (new) | reject, attach to an existing event, merge/unmerge |

**Edit never confers verify.** Review state (`review_status`, `status`, `evidence_status` overrides) is writable only through review endpoints that check `dms.chronology.verify` (or `reject_merge` for reject/merge). Edit endpoints refuse any payload carrying a review-state field. The same rule is enforced in the service layer, not only in the router. The existing Builder's D2 defect is fixed first, in A0a.

---

## 15. CL-3B / Chronology Builder integration

### 15.1 Recommendation: B — the Chronology Builder becomes the matter view over the project event layer

**Why not A** (extend `matter_chronology_events` into the event store):
- Its rows are owned by a matter (`chronology_id` required). One fact used in three matters would be three rows verified three times.
- Making `chronology_id` optional changes the meaning of a collection that these all depend on:
  - CL-3B's adapter, whose scope comes from the parent chronology;
  - the publication policy (`chronology_event`);
  - the arbitration adapter;
  - five test suites (`test_chronology_*`, `integration/test_cl3b_programme_chronology_mongo.py`).
- There is no place for the mention/position split without rewriting it anyway.

**Why not C** (keep it separate): a matter chronology *is* a chronology. Keeping both would be the parallel source of truth the brief forbids.

### 15.2 How B works

- `matter_chronology_events` gains `chronology_event_id` (nullable) and becomes a **curation row**:
  - it holds `pleading_use`, `supports_party` (perspective), `annexure_no`, `legal_relevance`, `manual_notes`, ordering, and an inclusion flag;
  - event content (dates, parties, evidence, status) is **read through** from `chronology_events` and is not editable on the matter row.
- Existing rows with no `chronology_event_id` remain **legacy matter events**, served as today and labelled "not evidence-pinned". There is no forced migration. An optional assisted "promote to project event" action is offered.
- The `POST /chronologies/{id}/extract` endpoint changes meaning: *select approved project events matching the chronology's filters (§16)* instead of running the rule extractor. The old extractor is retired behind a flag after PR-F.
- The CL-3B adapter, roles, `source_document` provenance and deletion semantics are unchanged. For anchored rows, `source_document` resolves from the primary assertion.
- Project-specific business columns live on the matter row `metadata`. No custom fields on `chronology_events` (§28 OD8).

---

## 16. Issue / Claim / EOT / Variation relationships

- **Issue codes.** A controlled list. Seed it from the existing `EXTRACTED_TAG_OPTIONS` / `EXTRACTED_SUBTAG_OPTIONS` (`models/document_metadata.py:56-117`) and `HindranceCategory`, rather than inventing a vocabulary. AI-proposed codes are candidates; reviewers can add codes.
- **Register links** are `event_links` rows from `chronology_event` to the register record. They reuse existing relation types:
  - `supports_claim` / `rebuts_claim` → `claim`;
  - `causes_delay` / `affects` → `delay_event`, `key_date`, `programme_milestone`;
  - `causes_variation` → `variation`;
  - new targets `eot_submission`, `eot_determination` (existing entity names in `entity_adapter_registry.py`).
- **No manual per-claim rebuild.** "Chronology for claim C" is the union of:
  - (a) events linked to C by approved `event_links`;
  - (b) events whose supporting assertions cite a Document that is linked to C in `entity_document_links`;
  - (c) events sharing C's issue codes within C's period.

  (b) and (c) are shown as **suggested inclusions**, not silently included. The same pattern applies to EOT, variation, delay event, party, location, work package, date range and clause. Every filter is a query over `chronology_events`, indexed on `(org, project, review_status, primary_date)`, `(org, project, issue_codes)` and `(org, project, event_type)`.

---

## 17. Contract links

- Assertions record `clause_mentions[]` as written ("GCC 8.4", "Clause 2.1"). These are **unresolved and non-authoritative**.
- An event `clause_links[]` entry exists only after resolution through the Contract Master path:
  - `authorize_contract_scope` → `ContractScopeResolver.resolve(...)` → `ApplicableInstrument`;
  - the entry is stored as `{contract_document_id, document_version_id, classification_revision, applicability_event_id, clause_row_id, clause_no, resolved_by (user|deterministic), resolved_at, status}`.
- Deterministic resolution is allowed only for an exact `clause_no` match within exactly one CURRENT applicable instrument. Anything else is a reviewer pick. AI never resolves a clause link.
- A later classification-revision change marks `clause_links` `stale` (same mechanism as §13). A clause row id alone is never sufficient (`contract_master_v1_03` rule).
- No contract graph redesign in this work.

---

## 18. Project Evidence Graph strategy

- Mongo `chronology_events` / `chronology_assertions` are authoritative. Graph structures are **projections**.
- **Phase 1 (PR-G), Mongo projection.**
  - Approved events project to `project_events` with `source_entity_type="chronology_event"`, idempotent and updated on change (the hindrance sync pattern, `hindrance_register_service.py:602-660`).
  - This replaces the per-verify `_sync_verified_event` that causes D3/D4.
  - The ingest "document event" stays until `CORRESPONDENCE_ISSUED` events cover the same documents; then it is retired behind a flag.
- **Phase 2, FalkorDB projection** (Project Evidence Graph, separate from the Contract Rules Graph):

  ```
  (Document)-[:CONTAINS_ASSERTION]->(Assertion)-[:SUPPORTS]->(Event)
  (Event)-[:RELATES_TO_ISSUE]->(Issue)
  (Event)-[:REFERENCES|RESPONDS_TO|CONTRADICTS|CONFIRMS|SUPERSEDES]->(Event)
  (Event)-[:RELATES_TO_CLAUSE]->(ContractClause)      // only resolved clause_links
  (Event)-[:SUPPORTS_CLAIM|AFFECTS_EOT|RELATES_TO_VARIATION]->(Register)
  ```

  New labels must key nodes by `org|project|id`. The global `normCode` key pattern (D10) must not be repeated. Only approved and auto-confirmed items are projected. Rebuild from Mongo must be possible at any time.

---

## 19. RBAC / tenant model

- **Reads.** Every list and get goes through `active_scope` (`require_selection` / `require_record`), then `PolicyService.authorize(..., dms.chronology.view)`, then `build_scope_query`. This is the hindrance router pattern (`routers/hindrances.py:91-197`).
  - A project-tier user with no project selected gets 200 bounded to their assignments, not 403.
  - The gate answers membership; the query answers rows (CLAUDE.md rule).
- **Writes.** `authorize_document` on the loaded row. Org and project are always taken from the source Document (assertions) or the loaded event, never from the payload. Attaching an assertion to an event in another project is refused, as is merging across projects.
- **Excerpt reads.** Serving an excerpt also requires `dms.document.view` on the source Document. This closes the CL-3B debt "no DOCUMENT_VIEW on source" for the new layer.
- **Super Admin.** Bounded by the navbar selection like everyone else (`tenant_context.py:31-33`). The explicit ALL selection is **not on this release** (`feat/explicit-all-scope-selection` is unmerged). Chronology must not add its own ALL path; it adopts that one when merged.
- **Background extraction.**
  - No global unscoped service.
  - The run carries `organization_id` / `project_id` copied from the Document at enqueue, as `document_processing_jobs` does (`document_service.py:963-1003`).
  - A new **internal scoped reader** `read_canonical_evidence_for_job(db, document_id, *, organization_id, project_id)` refuses if the Document's scope differs and applies the same deleted/consumable checks. This addresses G12. It must not be importable by routers; a test pins that.
- **Permissions.** The capability set in §14.3. Default roles today give chronology only to superadmin, orgadmin, contractmgr_org and projectadmin; orguser and projectuser have none. Default-role mapping for the two new permissions is set when they are introduced (PR-A/E), and each grant stays organisation/project scoped.
- **Exports.** Must take the selection, unlike today (debt #24). Use a fetch/blob download rather than `<a href>`.
- **Rate limiters.** Any new limiter (e.g. a manual "re-extract document" action) passes `scope=`.
- **Route inventory.** Regenerate with `scripts/rbac_phase0_route_inventory.py` per PR.

---

## 20. Trigger, idempotency and failure model

### 20.1 Trigger (recommended initial model)

- Canonical publish → enqueue a run keyed `(document_id, canonical_revision, extractor_version)`.
  - G11: add one explicit enqueue call after a successful head publish (`document_processor.py:516-539`). Do not use polling.
- Behind `CHRONOLOGY_EXTRACTION_ENABLED` (default **off**), plus an authorised per-document "run now" action for staging and benchmarking.
- Eligibility:
  - built canonical evidence;
  - `publication_consumable`;
  - not `duplicate_status=pending`;
  - not deleted.
- Otherwise the run is recorded as `blocked` with a reason, so it stays visible and is never silently skipped.
- `needs_review` pages are processed, but their assertions are forced to `needs_review` (§9).
- `unresolved` / `withheld` pages are skipped and counted.
- **A document's own review requirement is never cleared by chronology.** The run never writes to `documents` or `document_extraction_heads`.
- Worker: the Contract Master reprojection pattern. The run row is the queue, with a lease plus owner-token fence. A stale completion is discarded. It runs in the worker process (`START_CHRONOLOGY_WORKERS`), not in a request.

### 20.2 Idempotency

- Run `_id = sha256("run.v1|document_id|canonical_revision|extractor_id@version")`, unique.
- Assertion `_id = sha256("asr.v1|document_id|canonical_revision|extractor_id@version|kind|event_type|page|start|end|primary_date_role|primary_date_iso")`, unique. The insert is upsert-on-insert (`$setOnInsert`), so a re-run never overwrites review state.
- Events use uuid ids. Their idempotency comes from assertion attachment, and the same-document `record` rule (§11.3) prevents duplicate issued-events.
- LLM extractors must be deterministic in their *keys*: spans are validated and snapped to canonical offsets, and dates are normalised before hashing. Non-deterministic model wording changes only `proposition`, which is not in the key.

### 20.3 Failure semantics

- Run status values: `queued`, `processing`, `completed`, `partial` (some pages failed), `needs_review` (completed but every assertion needs review), `failed`, `blocked`, `superseded` (a newer revision arrived).
- Retry: up to 3 attempts, backoff `min(300, 30·attempts)` s, then `failed` (dead-letter visible in the status endpoint).
- **Chronology failure never:**
  - changes `documents.processing_status`;
  - touches canonical evidence;
  - deletes or downgrades existing approved events.

  Approved events are only changed by §13 revision handling or by humans.
- LLM failure → deterministic assertions still persist. The run is `partial` with `llm_usage.fallbacks += 1`. LLM output that fails span validation is dropped and counted, never stored unvalidated.

---

## 21. Observability and cost

**Metrics.** These use `ObservabilityRegistry` counters/gauges (`services/observability.py:29-53`) rendered at `/metrics`. Labels are org and project ids only, never text.

- Run metrics: `chronology_runs_total{status}`, `chronology_documents_eligible`, `chronology_documents_blocked{reason}`.
- Assertion metrics: `chronology_assertions_created_total{method,kind}`, `chronology_assertions_reused_total`, `chronology_assertions_rejected_total`, `chronology_assertions_stale_total`.
- Event metrics: `chronology_events{review_status}`, `chronology_review_queue_depth`, `chronology_dedup_proposals_total{accepted|rejected}`.
- LLM metrics: `chronology_llm_tokens_total{model,direction}`, `chronology_llm_est_cost_usd_total{model}`, `chronology_llm_fallback_total`, `chronology_llm_escalation_total`, `chronology_span_validation_dropped_total`.

**Status.** A status endpoint per project gives eligible / processed / blocked / failed counts, plus the average assertions per document.

**Logging.** IDs, counts and error codes only. No document text, quotes or propositions in logs (house rule; avoids the evidence-leak class seen in release evidence).

**Cost.** `llm_usage` is recorded per run from provider usage fields from the first LLM PR. Price is a configurable per-model table, not hard-coded.

---

### 21.1 Model and cost strategy

Mechanical first:

| Step | Method | Notes |
|---|---|---|
| Letter number, document date, references and their dates, amounts with currency, clause mentions, "your letter dated" patterns, period phrases ("from X to Y") | deterministic (PR-B) | |
| Semantic event candidates per page window | lower-cost model (PR-D) | structured JSON schema output: `{event_type, kind, stance, span_quote, dates[{role, raw}], actor_role, affected_role}`; the server snaps quotes to canonical offsets and rejects non-matching quotes |
| Ambiguous cases only: stance/actor uncertainty, low confidence, conflicting dates, issue classification | stronger model, escalation | counted in `escalation_total` |

**No model is hard-coded.**
- Use env-configured `CHRONOLOGY_EXTRACT_MODEL` and `CHRONOLOGY_ESCALATE_MODEL`, following existing `*_MODEL` env patterns (`core/config.py:275-278`).
- Every LLM path has a deterministic fallback and marks the run `partial`/degraded.

Because `LLMGenerator.generate` has no `strict` mode on this release (D9), the chronology extractor uses its own client wrapper. That wrapper raises on failure and records usage. It must not use the error-swallowing generator.

**Prompt versioning.** Use a `CHRONOLOGY_EXTRACT_PROMPT_VERSION` constant, pinned by tests, and bump it on any prompt change (house rule for arbitration prompts applies here).

---

## 22. Benchmark / gold-set plan

- **Synthetic gold set in the repo** (`backend/rbac_backend/tests/fixtures/chronology_gold/`):
  - correspondence chains written for the benchmark, including the §23 chain plus variants (number collisions, dot-dates, ambiguous dd/mm, reply chains, repeated allegations across 3 letters, admission, denial, partial grant, multi-page qualification, table-borne dates);
  - each chain has hand-written expected assertions and events as JSON;
  - **no customer documents in the repo.**
- **Real-document set outside the repo.** Authorised project documents, e.g. the two Downloads fixtures named in the handoff, used read-only. Expected events are approved by a domain reviewer and stored outside git, with a manifest of document sha256 values only.
- **Harness.** A script runs the extractor over a set and scores it against expected results:

| Metric | Definition |
|---|---|
| Event precision / recall | assertion-level match on (document, type, kind, primary date, page) |
| Date-role accuracy | correct `role` for matched dates |
| Party attribution accuracy | `asserted_by` / `actor` / `affected` roles |
| Reference-link accuracy | resolved reference = expected document |
| Duplicate-merge error | false-merge rate and missed-merge rate of proposals |
| Provenance accuracy | span within ±0 characters of the expected quote after snapping; page correct |
| Fact-vs-position accuracy | `kind` + `stance` correct |

- **Gate.** The harness runs in CI on the synthetic set (deterministic extractor: exact expected output). Real-set runs are manual, and the reports are stored outside the repo. Automated extraction may be enabled for a tenant only after the real-set report meets owner-approved thresholds (§28 OD9). Approved initial thresholds:
  - provenance accuracy ≥ 0.99;
  - fact-vs-position ≥ 0.95;
  - precision ≥ 0.85;
  - false merges = 0 (merges are reviewer-only anyway).

---

## 23. Example event chain (synthetic)

**Project.** Synthetic Metro Viaduct, Package P2. Contractor "AlphaBuild", Engineer "Delta Consult", Employer "City Metro Corp". Day-first dates.

| # | Document | Content (abridged) |
|---|---|---|
| L1 | AB/P2/101, 02-03-2025, Contractor → Engineer | "We request access to Pier P14 area by 05-03-2025 to commence piling." |
| L2 | DC/P2/055, 04-03-2025, Engineer → Contractor | "Ref AB/P2/101 dated 02-03-2025. Access to P14 will be provided by 10-03-2025." |
| L3 | AB/P2/118, 25-03-2025, Contractor → Engineer | "With reference to your letter DC/P2/055 dated 04-03-2025, access was not provided on 10-03-2025. Access remains unavailable. This is a notice under Clause 8.4." |
| L4 | AB/P2/131, 22-04-2025 | "Access to P14 was delayed by the Employer for 42 days (10-03-2025 to 21-04-2025). Delay notice under Clause 8.4." |
| L5 | CMC/P2/020, 05-05-2025, Employer | "Ref AB/P2/131. We deny responsibility; the access delay arose from the Contractor's failure to complete utility survey." |
| L6 | AB/P2/150, 20-05-2025 | "EOT submission for 42 days under Clause 8.4 for the P14 access delay." |
| L7 | DC/P2/090, 30-06-2025 | "Ref AB/P2/150. EOT of 20 days is granted; balance rejected." |

**Assertions.** Abridged; every row also carries `document_id`, `canonical_revision`, `canonical_sha256`, page, span and `quote_sha256`.

| Id | Doc | Kind | Type | Stance | Dates (role) | Status |
|---|---|---|---|---|---|---|
| a1 | L1 | record | CORRESPONDENCE_ISSUED | — | 02-03 DOCUMENT_DATE | auto_confirmed |
| a2 | L1 | position | REQUEST | requests (Contractor) | 05-03 DEADLINE | candidate |
| a3 | L2 | record | CORRESPONDENCE_ISSUED | — | 04-03 DOCUMENT_DATE; 02-03 REFERENCE_DOCUMENT_DATE | auto_confirmed |
| a4 | L2 | position | ACCESS_PROMISED (RESPONSE) | undertakes (Engineer) | 10-03 DEADLINE | candidate |
| a5 | L3 | record | CORRESPONDENCE_ISSUED / NOTICE | — | 25-03 DOCUMENT_DATE; 04-03 REFERENCE_DOCUMENT_DATE | auto_confirmed (issued) |
| a6 | L3 | position | ACCESS_DELAYED | alleges (Contractor) | 10-03 EVENT_DATE | candidate |
| a7 | L4 | position | ACCESS_DELAYED | alleges (Contractor); actor=Employer | 10-03 PERIOD_START; 21-04 PERIOD_END | candidate |
| a8 | L4 | record | DELAY_NOTICE | — | 22-04 NOTICE_DATE | candidate (semantic type) |
| a9 | L5 | position | ACCESS_DELAYED | denies (Employer); actor=Contractor | — | candidate |
| a10 | L6 | record | EOT_SUBMISSION | — | 20-05 SUBMISSION_DATE | candidate |
| a11 | L7 | record | EOT_DETERMINATION | — | 30-06 DOCUMENT_DATE (determination) | candidate |

The clause "8.4" in a5/a8/a10 is held as `clause_mentions` only, unresolved until a reviewer resolves it via the Contract Master.

**Events after review.**

| Event | Kind | Supported by | evidence_status | Notes |
|---|---|---|---|---|
| E1 Contractor requests access to P14 by 05-03 | position | a2 (+a1) | recorded (request is a record of asking) | |
| E2 Engineer undertakes access by 10-03 | position | a4 | admitted-relevant: an Engineer undertaking | reviewer marks it as Engineer position |
| **E3 Access to P14 not provided 10-03 → 21-04 (42 d)** | occurrence | a6, a7 (Contractor), a9 (Employer, denies cause) | **disputed** (as to cause); the period is alleged by the Contractor only | the reviewer may mark the *occurrence* `confirmed` only if an independent record (e.g. a joint site record) is added later |
| E4 Contractor delay notice under 8.4 | record | a5, a8 | recorded | `issue_codes: [Site Access, Hindrances, Notice Compliance]` |
| E5 Employer denies responsibility | position | a9 | alleged (Employer position) | `event_links`: E5 → E3 `rebuts_claim` |
| E6 EOT submission 42 d | record | a10 | recorded | `event_links` → `eot_submission` (`supports_claim`) |
| E7 EOT determination: 20 d granted | record | a11 | recorded | `event_links` → `eot_determination`; E7 `responds_to` E6 |

**Relations** (`event_links`, `source_type=chronology_event`):
- E2 `responds_to` E1;
- E4 `refers_to` E2 (from L3's explicit reference, number + date match → auto_confirmed);
- E3 `causes_delay` → delay_event HIN-0007 (reviewer);
- E5 `rebuts_claim` E3;
- E7 `responds_to` E6.

**Issue chronology "P14 Access".** Issue code Site Access plus the location filter returns E1–E7. The claim / EOT view for submission E6 returns E3, E4, E6 and E7 through links, with E1, E2 and E5 as suggested inclusions.

---

## 24. Options considered

| Criterion | A: extend `matter_chronology_events` | **B′: project event + assertion layer; Builder = matter view; `project_events` = projection** | C: graph-first, Mongo projections | D: evolve `project_events` into the store |
|---|---|---|---|---|
| Reuse | High on paper; low in practice (model must change shape) | High: reuses `event_links`, the review/revision patterns, CL-3B adapter, RBAC, the worker pattern, and the Builder UI | Low: Falkor has no tenant-keyed evidence nodes today | Medium: its enums and links are reused, but every row is "one per document" and is used by the timeline, backfill and hindrance sync |
| Auditability | Per-matter only | Per assertion, per event, per matter | Weak (Falkor has no revision history) | Weak (no event revisions) |
| Human review | Exists | Exists (reused) + assertion-level | Must be built | Links only |
| Dedup | Impossible across matters | Native (assertions → event) | Native but unaudited | Same-key collisions already (D4) |
| Filtering | Per chronology | Project-wide indexes | Cypher; tenant-key risk | Partial (post-pagination filtering defect) |
| Claim/EOT reuse | Copies per matter | Links once, reused by every matter | Yes | Yes |
| SOC/SOD/Rejoinder | Exists (adapter) | Same adapter, now evidence-pinned | Rewrite | Rewrite |
| Graph compatibility | Indirect | Clean projection | Native | Native in Mongo |
| Migration burden | High (semantic change of a live collection + 5 suites) | Low: additive collections; one nullable field on matter rows | High | High (timeline, backfill, hindrance sync, arbitration ledger all read it) |
| RBAC | Parent-chronology scope | Project scope + matter scope (existing) | New ACL in graph | Existing |
| Performance | Fine | Fine (indexed Mongo) | Falkor for traversal only | Fine |
| Ops complexity | Low | Medium: one worker type + 3 collections | High | Medium |

## 25. Recommended architecture: **B′**

- **Evidence source of truth:** the Canonical Evidence Store (`document_extraction_heads` + `document_ocr_pages`).
- **Event source of truth:** `chronology_events`, supported by `chronology_assertions`, revisioned in `chronology_event_revisions`, processed via `chronology_extraction_runs`.
- **Matter curation:** `matter_chronologies` / `matter_chronology_events` (the existing Chronology Builder), referencing project events.
- **Relations:** `event_links` (fix D5 first).
- **Derived:** `project_events` (Contract Timeline), Falkor Project Evidence Graph, `arbitration_chronology_matrix`.

## 26. Bounded PR roadmap

Each PR runs the **full** backend suite with `backend/.venv` and the CI-aligned docker recipe, regenerates the route inventory when routes change, and is red-tests-first. Nothing deploys without owner sign-off.

| PR | Scope | Out |
|---|---|---|
| **A0** (approved first, §28 OD11) | Fix existing defects in the current Builder: D2 (status bypass), D1 (`created_at`/`utcnow` as EXACT date), D5 (link status filter after latest-revision collapse), D6 (adapter by chronology + exclude deleted). Red tests first | no new model |
| **A** | Collections, indexes and migration (assertions, events, event revisions, runs); Pydantic models; repository; provenance pin + `quote_sha256` validator; deterministic id helpers; scoped job reader for canonical evidence + router-import guard test; read APIs (list/get, status) under `dms.chronology.view` | no extraction, no UI |
| **B** | Deterministic extractor (issued record, typed dates with day-first and ambiguity flags, references with date-aware resolution, amounts, clause mentions); run worker with lease; enqueue hook after canonical publish; `CHRONOLOGY_EXTRACTION_ENABLED` flag (off); revision re-anchoring (§13); auto-confirm class; metrics | no LLM |
| **C** | Benchmark harness + synthetic gold set + CI job on the synthetic set; real-set runner (data outside repo) | |
| **D** | LLM semantic candidates (positions/occurrences) with schema output, span snapping, escalation, usage/cost capture, prompt-version constant, deterministic fallback | no auto-confirm for LLM output |
| **E** | Review and dedup APIs: approve/edit/reject, attach/new event, merge/unmerge, derived evidence_status, `event_links` suggestions (refers_to/replies_to/responds_to/rebuts), register links | |
| **F** | UI: review queue; Chronology Builder becomes the matter view (select project events, legacy rows labelled); selection-aware exports; retire the old extractor behind a flag | |
| **G** | Projection to `project_events` (idempotent, updated), retirement plan for ingest doc events and `_sync_verified_event`; Falkor Project Evidence Graph with tenant-keyed nodes | |
| **H** | Matter Context Pack / Claim / EOT / SOC / SOD / Rejoinder consumption (acceptance criteria in §16 and below) | |

**Acceptance criteria recorded for PR-H consumers.**

- **Claim:** approved events linked to or suggested for the claim, ordered by primary date. Each has its evidence citation (doc, page, span), plus notices, deadlines, instructions, submissions, responses and determinations by event type.
- **SOC:** claimant-perspective selection of approved events with citations. Positions are labelled as the claimant's case, never as fact.
- **SOD:** for each SOC event or position, show the respondent's position events (`rebuts_claim`/`disputes`), plus supporting and contrary evidence assertions.
- **Rejoinder:** SOC position → SOD response → still-disputed status → rebuttal evidence assertions.
- **All consumers:**
  - exclude `stale_evidence`, unapproved and non-consumable sources by default;
  - when a cited source becomes non-consumable, the excerpt is withheld at read time (same rule as `safe_event_records`);
  - a `quote_sha256` mismatch fails visibly.

## 27. Risks and limitations

1. **Production has no canonical evidence.** All documents are `legacy_v0`, so this layer yields nothing in production until unified extraction is enabled and documents are re-extracted. That is deliberate, not a defect, but it means business value lags the code.
2. **Review load.** Semantic extraction produces many candidates. Without good dedup proposals and the narrow auto-confirm class, reviewers drown. The benchmark must measure candidates per document.
3. **Party roles** depend on a project party-role map that does not exist yet (G9). Until it does, roles default to `unknown` and every position needs review.
4. **Reference resolution** remains only as good as letter-number normalisation. Collisions without dates stay unresolved by design.
5. **Enclosures** are invisible (G15). Events evidenced only in enclosures will be missed.
6. **Two transitional event projections** (ingest doc events and chronology projections) coexist on the Contract Timeline until PR-G retires one.
7. **The LLM wrapper is new.** `LLMGenerator` swallows errors (D9), so a separate raising client is required. Another LLM entry point is a surface to keep consistent.
8. **Cost** is unmeasured until PR-D. There is no historical baseline for token usage.
9. **The ALL selection** for Super Admin depends on an unmerged branch.
10. **Two clause id schemes** exist (sha1 `clause_uid`, sha256 reprojection `_id`). Clause links store the Contract Master instrument identity plus `clause_no` for that reason.

## 28. Owner decisions (approved 2026-10-11)

| # | Decision |
|---|---|
| OD1 | **Architecture:** Option B′ approved. Canonical Evidence Store is the evidence source of truth; `chronology_assertions` = document-specific mentions/positions; `chronology_events` = deduplicated project-level events; the Chronology Builder becomes the matter-level curated view, not a parallel authoritative source. CL-3B linking behaviour is preserved. |
| OD2 | **Authoritative use:** semantic/AI events need human approval before Claims, EOT, SOC, SOD, Rejoinder or other formal drafting treats them as authoritative. Only two deterministic classes may auto-confirm initially: (1) letter/document issued; (2) an exact document-reference relationship where reference number and date both match deterministically. No causation, responsibility, entitlement, delay, admission, denial or contractual interpretation auto-confirms because a model is confident. |
| OD3 | **Legacy documents:** no automatic evidence-grade assertions from documents without canonical evidence. Existing Builder events stay visible, identifiable as legacy / not evidence-pinned. Manual chronology remains possible. Re-extraction/backfill is a separate owner-approved future task. |
| OD4 | **Dates:** DMY default for current Indian projects, configurable per project. Ambiguous dates stay ambiguous unless source context resolves them. System timestamps are never substituted for missing event dates; a missing event date stays missing. Date type stays explicit. |
| OD5 | **Parties:** Contract Master remains the source for contractual instruments and party names. A project-level, admin-maintained, auditable role map (§8) covers practical roles (Employer, Engineer, GC, Contractor, JV/consortium members) and role/effective-period changes, without silently replacing Contract Master identities. |
| OD6 | **Permissions:** explicit capability permissions (§14.3): view, create_candidate, edit, verify, reject_merge, all organisation/project scoped. Edit never confers verify. |
| OD7 | **Canonical revision:** if evidence changes materially and a citation cannot be deterministically re-pinned to identical evidence, the assertion is stale; if it is an approved event's only authoritative support, the event returns to review. Approval is never silently preserved against changed evidence (§13). |
| OD8 | **Custom fields:** the core assertion/event schema stays generic and stable. Matter/register/project-specific fields live in the Builder/view/extension layer. |
| OD9 | **Benchmark gates before enabling automatic extraction (initial, may tighten later):** provenance accuracy ≥ 0.99; fact-vs-position accuracy ≥ 0.95; event precision ≥ 0.85; zero false automatic event merges. |
| OD10 | **Retention:** rejected and superseded candidates are retained as audit records under the future retention / legal-hold / permanent-purge policy. No "retain forever" is hard-coded. |
| OD11 | **Sequencing:** A0 defect fixes (existing Builder) come before any new-layer code. |

### Appendix — controlled event taxonomy (§6 of the brief)

These values map to `ProjectEventType` for projection and reuse existing names where they exist.

| Group | Types | Projects to `ProjectEventType` |
|---|---|---|
| Correspondence | `CORRESPONDENCE_ISSUED`, `REQUEST`, `RESPONSE` | letter |
| Instructions | `INSTRUCTION`, `VARIATION_INSTRUCTION`, `DRAWING_ISSUED`, `DRAWING_REVISED`, `DESIGN_CHANGE` | instruction / variation / drawing |
| Notices | `NOTICE`, `DELAY_NOTICE`, `CLAIM_NOTICE`, `EOT_NOTICE`, `DISPUTE_NOTICE` | letter / claim / delay |
| Submissions and decisions | `SUBMISSION`, `APPROVAL`, `REJECTION`, `VARIATION_SUBMISSION`, `VARIATION_DETERMINATION`, `EOT_SUBMISSION`, `EOT_DETERMINATION`, `CLAIM_SUBMISSION`, `CLAIM_RESPONSE` | claim / variation / key_date |
| Site and works | `ACCESS_GRANTED`, `ACCESS_DELAYED`, `WORK_COMMENCED`, `WORK_STOPPED`, `WORK_RESUMED`, `HINDRANCE`, `CONSTRAINT`, `DELAY_EVENT`, `TEST_INSPECTION`, `HANDOVER`, `COMMISSIONING` | hindrance / constraint / delay / milestone |
| Money | `PAYMENT_APPLICATION`, `PAYMENT_CERTIFICATION`, `PAYMENT` | payment |
| Meetings | `MEETING` (MoM; `joint_record` when signed by both sides) | meeting |
| Dispute resolution | `CONCILIATION`, `ARBITRATION` | other |
| Fallback | `OTHER` | other |

Hindrance subcategories reuse `HindranceCategory` (site_access, land_handover, design_information, …) as issue codes rather than as new event types.
