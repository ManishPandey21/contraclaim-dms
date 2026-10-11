# Page-wise OCR, progress-photo assets, child enclosures, and archive passthrough — design

**Date:** 2026-08-14
**Status:** design approved; no code changed.
**Companions:**
[current_document_enclosure_contract_ingestion_flow_2026-08-13.md](../../architecture/current_document_enclosure_contract_ingestion_flow_2026-08-13.md)
(the implementation record this design amends) ·
[mixed_pdf_ingestion_and_summary_plan_2026-08-13.md](../../architecture/mixed_pdf_ingestion_and_summary_plan_2026-08-13.md)
(the measured evidence for the page-level defect).

---

## 1. Improvement outcome

One page-aware extraction engine serves every intake surface — general single upload,
general bulk upload, enclosures, and contracts — running in a dedicated worker process
rather than the FastAPI request-serving container. Every PDF page is classified and routed
individually, so a scanned page inside an otherwise-textual document is OCR'd instead of
silently indexed as empty. Every page is then assessed by a deterministic quality gate, and
pages that fail, come back incomplete, or fall below threshold are routed to a bounded
LLM/Vision fallback ladder whose output is re-checked by that same gate — terminating in
`HUMAN_REVIEW_REQUIRED` rather than in a fabricated or silently-accepted result. Substantive
site/progress photographs are extracted from PDFs as
independent, page-anchored image assets. Enclosures become first-class child documents with
their own extraction results. Archives are stored intact and never processed.

Nothing in this design changes the contract path's observable behaviour. The contract path
is the reference implementation being generalised, not a target for modification.

---

## 2. Expected behaviour

| Input | Today | After |
|---|---|---|
| 9-page PDF, pages 1–2 scanned, pages 3–9 textual, general upload | Any text in first 5 pages ⇒ OCR skipped for the whole file; pages 1–2 indexed as empty; document marked `completed` | Pages 1–2 routed to OCR as one contiguous batch; pages 3–9 keep native text; per-page evidence recorded |
| PNG/JPEG general upload with OCR enabled | Admitted by MIME allowlist, then handed to `process_pdf()` which is not an image path | Routed to a single-page image OCR path; stored as-is, never wrapped in a PDF |
| Enclosure upload | Stored only — no version, job, OCR, metadata, vectors, or graph | First-class child document with `parent_document_id`, its own conversion, OCR, chunks, vectors, and assets |
| `.zip` / `.rar` upload | `415` — the MIME sniffer has no archive branch | Stored intact after AV + SHA-256 + duplicate checks; no job, no extraction; letter number not required |
| PDF containing site photographs | Nothing extracted | Photographs become independent assets, page- and region-anchored, viewable with a deep link to the source page |
| OCR page budget exceeded | n/a (no budget today) | Resumable batching; document enters `PARTIALLY_PROCESSED`, never silently truncated |
| A page's OCR fails or returns garbage | Silently accepted as that page's text, or the whole job fails | Page routed to the LLM/Vision fallback ladder; the document does not fail because of one page |
| Extracted text fails an integrity check (split digits, broken reading order, mangled table) | Not detected — no quality gate exists | Page escalated through the ladder; unresolved pages end at `HUMAN_REVIEW_REQUIRED`, never `completed` |
| An LLM reconstructs a page | n/a | Output re-run through the **same** deterministic checks; model confidence alone never accepts it; every intervention retains page/region provenance |

### Ambiguities resolved by the requester

- **"All image assets as independent files"** means substantive site/progress photographs
  **only**. Signatures, stamps, seals, logos, letterheads, QR/barcodes, icons, and
  decorative graphics are explicitly *not* extracted as standalone assets.
- **Only `ACCEPTED` photographs** become visible and searchable. `REVIEW_REQUIRED`
  candidates remain internal and unindexed until a human resolves them.
- **Enclosure results** live on the child record, not merged into the parent.
- **Archives** keep antivirus, SHA-256, and duplicate checks; only extraction is skipped.

---

## 3. Current-state findings

All evidence read from the working tree at `C:\SaaS\projectDMS` on 2026-08-14.

### 3.1 The general path decides OCR once, for the whole document

`OCRService.is_pdf_textual(pdf_path, max_pages=5)` returns `True` if **any** of the first
five pages yields text ([ocr_service.py:64](../../../backend/rbac_backend/services/ocr_service.py)).
`process_pdf` then branches on that single boolean
([ocr_service.py:139-155](../../../backend/rbac_backend/services/ocr_service.py)): text found
⇒ copy the original and extract a sidecar; no text ⇒ OCRmyPDF the entire file.

Measured against a real 9-page contractor claim (companion document §2.1): the decision was
`SKIP OCR`, and 2 of 9 pages yielded zero text. Those two pages are the covering letter —
letter number, date, sender, recipient, subject, signature — precisely the contractual
entities the DMS indexes and drafts from.

### 3.2 The contract path already implements the target behaviour

`_extract_pdf_pages_with_ocr_batches`
([contracts_ingest.py:1322-1466](../../../backend/rbac_backend/services/contracts_ingest.py))
compares each page's trimmed length against `contract_ocr_min_text_chars_per_page`, groups
contiguous low-text pages into batches, meters them, runs OCRmyPDF with `--pages`, and
records per-page state. On the same file it correctly routed pages 1–2 and only pages 1–2.

It is, however, welded to contract concerns: `upload_id`, `db_service.upsert_ocr_batch`,
`db_service.upsert_ocr_pages`, `upsert_job_status`, and `UsageEventType.OCR_PAGE` are all
called from inside the extraction loop.

### 3.3 The page-index mapping rests on an unverified assumption

`_extract_selected_pages_from_pdf`
([contracts_ingest.py:1565-1586](../../../backend/rbac_backend/services/contracts_ingest.py))
chooses between two ways of locating a page in OCRmyPDF's output:

```
if output_count >= max(ordered):  source_index = page_number - 1   # absolute
else:                             source_index = index             # positional
```

Which branch runs depends on whether `ocrmypdf --pages 1-2` emits a 2-page PDF or the full
document with only those pages OCR'd. **This is not verified.** OCRmyPDF's `--pages`
documents which pages are *OCR'd*, not the output's page count. If the output retains all
pages — the reading the code's own primary branch assumes — then `output_count` always equals
the full count, the condition is always true, and the positional branch is **unreachable dead
code**. If the output is trimmed to the batch, then the absolute branch fires whenever
`max(ordered)` happens to be ≤ the batch length, and page text is silently attributed to the
wrong page number.

Either way the construct is a hazard: one branch is dead, or the selection between them is
numeric coincidence. A misfire does not raise — it stores page 8's text as page 1's, which is
invisible until someone cites the wrong page in a claim. It is survivable serving one caller
with one tool; it must be replaced with explicit source→output mapping before it serves four.

**Phase 0 resolves the ambiguity by running `ocrmypdf --pages` inside the backend container
and recording the actual output page count.** This host cannot: it has no tesseract
(companion document §9).

### 3.4 The MIME sniffer has no archive branch

`sniff_mime_from_bytes`
([file_validation.py:12-53](../../../backend/rbac_backend/utils/file_validation.py))
recognises PDF, DOCX (via filename hint), PNG, JPEG, and a text heuristic. A `.zip` returns
`application/octet-stream` and is rejected with `415`. `.rar` likewise.

### 3.5 The client picker and the backend allowlist disagree

`UploadPage.tsx:809` sets `accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.gif"`.
`ALLOWED_DOCUMENT_MIMES` defaults to `{application/pdf, image/png, image/jpeg, text/plain}`
([config.py:159-166](../../../backend/rbac_backend/core/config.py)). A user can select a
`.doc`, `.docx`, or `.gif` and receive a `415` after upload.

### 3.6 Enclosures are storage-only

`controller_add_enclosure`
([documents.py:1647-1752](../../../backend/rbac_backend/routers/documents.py))
authorizes, spools, validates, scans, stores a `FileObject`, emits an audit event, and
appends a subdocument to `documents.enclosures`. No `DocumentVersion`, no processing job, no
OCR, no metadata, no vectors, no references, no graph.

### 3.7 `START_BACKGROUND_SERVICES` is a bundle, not a switch

`start_background_services()` starts **four** tasks
([background_jobs.py:416-422](../../../backend/rbac_backend/services/background_jobs.py)):
`periodic_cleanup`, `periodic_assignment_alerts`, `periodic_subscription_lifecycle`, and
`periodic_document_processing_jobs`. Setting `START_BACKGROUND_SERVICES=false` on the web
tier to move OCR off it would also stop assignment alerts and subscription lifecycle.

`contract-worker` additionally owns `RUN_SCHEDULER=true` and is documented as
single-replica ([docker-compose.prod.yml:216-245](../../../docker-compose.prod.yml)). A new
worker must not inherit that flag.

### 3.8 Every page of a real claim carries images

Companion document §3.9: pages 3–9 of the sample — all text-bearing — carry 3–6 embedded
images each (stamps, signatures, logos, scanned insets). `images > 0` is therefore **not** a
scan indicator, and "extract every image" would produce roughly 30 junk assets from one
9-page letter. This is the single most important constraint on the photo extractor.

---

## 4. Architecture

### 4.1 Module and seam map

| Module | Interface | Hidden behind it | Seam |
|---|---|---|---|
| `PageExtractionEngine` | `extract(source, kind, policy, retry_pages, meter) -> PageExtractionResult` | Page classification, OCR routing, contiguous batching, OCRmyPDF invocation, page-index mapping, native/OCR merge, resumability | `PageStore` adapter |
| `ContractPageStore` | `PageStore` | `contract_ocr_pages`, `contract_ocr_batches`, contract job status | Mongo |
| `DocumentPageStore` | `PageStore` | `document_ocr_pages`, `document_ocr_batches`, document job stage | Mongo |
| `SourceKindRouter` | `route(mime, filename) -> SourceKind` | Archive / PDF / image / text / unsupported classification | Called by both intake and extraction |
| `PageClassifier` | `classify(page) -> PageClassification` | Char density, image coverage, table count, vector-drawing count, dimensions, rotation, blankness | Internal to the engine |
| `ExtractionQualityGate` | `assess(page, extraction) -> QualityVerdict` | Column-role mapping, in-table declared formulas, arithmetic identities, split-digit detection, date-convention checking, reading-order coherence, tolerance handling | Runs unconditionally on every page from every source |
| `ExtractionFallbackLadder` | `resolve(page, verdict, evidence) -> ResolvedPage` | Tier selection, minimal-evidence assembly, model invocation, re-verification, escalation, terminal `HUMAN_REVIEW_REQUIRED` | `ReconstructionModel` adapter per tier |
| `PageRasterizer` | `render(page, dpi)`, `crop(page, bbox, dpi)` | pypdfium2 rendering via pdfplumber, Pillow cropping, resolution policy | Shared by the ladder and the asset extractor |
| `InterventionLedger` | `record(intervention)` | Immutable per-intervention provenance rows | Mongo `page_extraction_interventions` |
| `ImageAssetExtractor` | `extract_photographs(pdf, pages) -> list[AssetCandidate]` | XObject enumeration, boilerplate deduplication, hard rejects, deterministic scoring, uncertainty banding | `VisionAdjudicator` adapter |
| `VisionAdjudicator` | `adjudicate(candidates) -> list[Verdict]` | Strict LLM vision call, prompt version, budget cap, deterministic fallback | Model provider |
| `ArchiveIntakePolicy` | `is_archive(mime)`, `requires_letter_number(kind)` | Archive MIME set and the intake exemptions it implies | Called by document + enclosure routers |
| `UploadPolicyService` | `get_policy() -> UploadPolicy` | Canonical allowed MIMEs, extensions, size caps, per-surface differences | `GET /api/config/upload-policy` |

**Depth check.** `PageExtractionEngine` presents one method. Behind it sit the page decision,
batching, subprocess invocation, index mapping, merge, and resume logic. Deleting it makes
that complexity reappear in four callers. It earns its keep.

**Seam check.** `PageStore` has two adapters on day one (contract and document), so it is a
real seam, not a hypothetical one. `VisionAdjudicator` has two: the model-backed one and a
`NullAdjudicator` that leaves everything `REVIEW_REQUIRED`.

**What must NOT move into the engine.** `upload_id`, `contract_ocr_*` collections, contract
job-status upserts, and `UsageEventType.OCR_PAGE` accounting stay with their callers. The
engine receives a `meter` callback and *returns* page records; it never writes them.

### 4.2 Interface

```
PageExtractionPolicy:
    ocr_enabled: bool
    min_text_chars_per_page: int          # existing contract_ocr_min_text_chars_per_page
    batch_size: int                       # existing contract_ocr_batch_size
    max_ocr_pages_per_attempt: int        # resumability boundary, NOT a silent cap
    ocr_language: str

PageExtractionResult:
    pages: list[ExtractedPage]
    combined_text: str
    ocr_pages_total: int
    ocr_failed_pages: list[int]
    ocr_deferred_pages: list[int]         # not attempted this pass; drives resume
    completeness: COMPLETE | PARTIAL
    engine_version: str

ExtractedPage:
    number, text, char_count
    source: TEXT_LAYER | OCR | EMPTY
    status: text_layer | ocr_completed | ocr_empty | ocr_failed | ocr_disabled | ocr_deferred
    classification: PageClassification
    width, height, rotation
    batch_id, error
```

### 4.3 Page classification

The minimum-character threshold stays as the OCR trigger — companion §3.9 established it as
the correct discriminator — but classification is recorded alongside it for routing,
diagnostics, and the asset extractor:

| Class | Signals |
|---|---|
| `TEXT_NATIVE` | char count ≥ threshold, low image coverage |
| `SCANNED_IMAGE` | char count < threshold, one image covering ≥85% of the page |
| `MIXED_CONTENT` | char count ≥ threshold with significant image coverage |
| `TABLE_HEAVY` | ≥1 table from `find_tables()` (may co-occur with the above) |
| `BLANK` | no text, no images, no vector drawings |
| `UNRENDERABLE` | page could not be parsed |

Classification is descriptive metadata, not a second gate. Only `SCANNED_IMAGE` and `BLANK`
change behaviour: the former suppresses whole-page-raster candidates in the asset extractor,
the latter suppresses a pointless OCR call.

### 4.4 Resumable batching and fail-visible states

No silent page cap. `max_ocr_pages_per_attempt` bounds the work in **one attempt**, and the
remainder is recorded as `ocr_deferred` and re-claimed on the next pass.

New document processing states:

- **`PARTIALLY_PROCESSED`** — some pages extracted, some deferred or failed; the document is
  usable but explicitly incomplete, and resumption is scheduled. Downstream publication
  (vectors, references, graph) proceeds only for what was extracted, and the document carries
  the deferred page list.
- **`HUMAN_REVIEW_REQUIRED`** — resumption is exhausted or a page failed terminally. No
  automatic retry; surfaced to an operator.

Neither state may be reported as `completed`. This follows the house rule from `CLAUDE.md`:
never mark success on a skipped step.

### 4.5 Worker topology (Approach A+)

Heavy extraction must not add sustained CPU to the request-serving process. Because
`START_BACKGROUND_SERVICES` bundles four unrelated tasks (§3.7), the extraction loop gets its
own flag rather than reusing the bundle:

```
START_DOCUMENT_EXTRACTION_WORKERS   default false
```

- `periodic_document_processing_jobs()` moves behind the new flag; the other three tasks stay
  under `START_BACKGROUND_SERVICES`.
- `backend`: `START_BACKGROUND_SERVICES=true`, `START_DOCUMENT_EXTRACTION_WORKERS=false`.
  Cleanup, alerts, and subscription lifecycle are unaffected.
- New `document-worker` service, same image, `python -m rbac_backend.worker`:
  `START_BACKGROUND_SERVICES=false`, `START_DOCUMENT_EXTRACTION_WORKERS=true`,
  `START_CONTRACT_QUEUE_WORKERS=false`, **`RUN_SCHEDULER=false`** — `contract-worker` remains
  the single scheduler owner.
- Must mount `backend_uploads` (the shared volume the contract worker already mounts) and
  join `service-net`, `data-net`, `egress-net`.

Job claiming already uses heartbeat, stale recovery, and bounded attempts
([document_service.py:757-964](../../../backend/rbac_backend/services/document_service.py)).
**Phase 0 must verify the claim is a single atomic `findOneAndUpdate`** before a second
process claims from the same collection; if it is not, that is a prerequisite fix, not an
optional one.

### 4.6 Progress-photo extraction

Three stages. Cost rises at each, so each stage must dispose of as much as it can.

**Stage 1 — candidate filtering (deterministic, free).** Enumerate image XObjects per page
with their placement rectangles. Hard-reject:

- **XObject SHA-256 repeating across ≥2 pages** — boilerplate: letterhead, logo, stamp
  template. Cheapest and strongest signal available.
- Bilevel encoding (`/ImageMask`, `BitsPerComponent 1`, `CCITTFaxDecode`, `JBIG2Decode`) —
  signature, stamp, fax scan.
- ≤16 unique colours — logo, icon, flat graphic.
- Pixel area below a floor, or placed area below a fraction of the page.
- Aspect ratio outside a plausible photographic range — letterhead and signature strips.
- Covering ≥85% of a `SCANNED_IMAGE` page — that is a page scan, not an asset.

**Stage 2 — deterministic classification (free).** Score survivors on encoding
(`DCTDecode`/`JPXDecode`), colour space (DeviceRGB / ICCBased 3-channel, 8bpc), byte
entropy, pixel dimensions, placed-area fraction, and vertical position (demote the letterhead
band at the top and the signature band at the bottom). Two thresholds produce three outcomes:
`ACCEPTED`, `REJECTED`, and an uncertainty band → `REVIEW_REQUIRED`.

**Stage 3 — selective Vision (paid, uncertainty band only).** Only `REVIEW_REQUIRED`
candidates are sent, and only when the adjudicator is enabled and within budget. The call
answers one question — *is this a substantive site or progress photograph, or is it a
signature, stamp, seal, logo, letterhead, barcode, icon, or decorative graphic?* — and
returns a label plus confidence.

Per `CLAUDE.md`: `strict=True`, a pinned prompt-version constant, and a deterministic
fallback. **The fallback is to remain `REVIEW_REQUIRED`, never to auto-accept.** A failed,
disabled, over-budget, or low-confidence adjudication leaves the candidate internal and
unindexed. Budget is capped per document and per organisation, and every call is metered.

**Visibility rule.** Only `ACCEPTED` assets are stored as independent `FileObject`s, exposed
in the UI, and made searchable. `REVIEW_REQUIRED` candidates persist as metadata rows —
page, bbox, signals, and the reason they were uncertain — and are excluded from every
listing, index, export, and retrieval path until a human resolves them. Whether their bytes
are also retained is deferred to open question 1 (§8); until it is answered, implement
metadata-only, because a reviewer can open the source page from the recorded bbox and adding
retention later is cheaper than removing it.

**Asset record.** Each accepted asset carries `document_id`, `page_number`, `bbox` in PDF
user-space, pixel dimensions, source XObject SHA-256, classification decision, decision
reason, confidence, and adjudication method (`deterministic` | `vision` | `human`). The UI
renders it with a deep link that opens the source PDF at that page.

**Scope boundary.** Phase 9 covers **embedded PDF image XObjects only**. Photographs inside a
*flattened or fully scanned* page are not separate XObjects — the whole page is one raster,
and there is nothing to enumerate. Recovering those requires page-region detection over the
rendered raster and is recorded here as **P2**, explicitly out of Phase 9 scope. It must not
be claimed as covered.

### 4.7 Enclosures as child documents

An enclosure becomes a real `documents` row:

```
uploadType            : "enclosure"
parent_document_id    : ObjectId
relationship_type     : "enclosure" | "annexure" | "attachment"
source_page_range     : {start, end} | null    # when carved from a parent PDF
parent_provenance     : {letter_no, subject, document_date, organization_id, project_id}
```

It gets its own `DocumentVersion`, its own `document_processing_jobs` entry, and therefore
its own conversion, page OCR, chunks, vectors, and image assets — reusing the existing
pipeline rather than duplicating it. Scope fields are inherited from the parent at creation
so `build_scope_query` and `PolicyService` work unchanged.

**Listing rule.** Ordinary top-level Document Library listings filter to
`parent_document_id: null` by default. Retrieval — search, vectors, drafting context — is
**not** filtered: a child document may surface on its own merit, carrying its parent
provenance so the result is attributable.

`documents.enclosures[]` remains populated as a denormalised pointer list for one release so
no current consumer breaks, then is superseded. This mirrors the `summary` →
`short_summary` supersession pattern in the companion plan.

### 4.8 Archives

`sniff_mime_from_bytes` gains two magic-byte branches: `PK\x03\x04` → `application/zip`
(guarded so the existing DOCX filename-hint branch still wins for `.docx`) and
`Rar!\x1a\x07` → `application/vnd.rar`. Both MIMEs join `ALLOWED_DOCUMENT_MIMES` and
`ALLOWED_ENCLOSURE_MIMES`.

`ArchiveIntakePolicy.is_archive(mime)` gates two things in the intake path:

1. **Letter number becomes optional** for archive uploads. The general route currently
   requires one before storage; archives are exempt.
2. **No processing job is created**, regardless of the `ocrEnabled` flag.

Antivirus, SHA-256 storage deduplication, and duplicate precheck are all unchanged and still
run. No unpacking, no listing of contents, no downstream processing, and therefore **no new
Python dependency** — `rarfile`/`unrar` are not required because we never read inside.

ClamAV's RAR handling depends on how the deployed image was built; Phase 0 verifies it in
the running container rather than assuming it.

### 4.9 Extraction quality gate and the LLM/Vision fallback ladder

Where OCR, native text extraction, or layout extraction fails, returns incomplete or
low-confidence output, or misses a configured quality threshold, the affected **page or
region** is routed to an LLM/Vision fallback rather than being silently accepted, silently
skipped, or failing the whole document.

#### 4.9.1 The gate is deterministic, unconditional, and must ship first

**This is the load-bearing constraint, and it is the one the evidence is emphatic about.**

Companion document §3.1 measured **9 split-digit corruptions in the *native* text layer** of a
correct PDF — `1 ,900,000` for `1,900,000`. Parsed naively that reads as `1`, and the claim
subtotal lands ₹2.5 crore light. So "native text ⇒ trustworthy" is false, and the gate must
run on **every** page from every source: native, OCR, and LLM-reconstructed alike.

Companion §3.3 measured the opposite failure just as sharply: a naive quality rule
("last three numeric columns must satisfy qty × rate = amount") produced **12 false
mismatches on a document that was correct**, because a separate `Nos` column applied, because
two tables declared their own formulas in their headers (`Area A=[h*l]`), and because an `S/N`
serial was read as a quantity.

Those twelve false positives are twelve paid escalations per document, on a document that
needed none. Therefore:

> **The ladder must not be enabled until the quality gate is column-role aware, honours
> in-table declared formulas, applies a rounding tolerance, and treats "roles not confidently
> identified" as _not checkable_ rather than _failed_.** Shipping the ladder on a naive gate
> converts a cost-control feature into an unbounded spend.

The gate emits one of four verdicts, and only the third escalates:

| Verdict | Meaning | Action |
|---|---|---|
| `PASS` | Checks ran and agreed | Accept |
| `NOT_CHECKABLE` | Roles/structure not confidently identified; nothing to verify against | Accept, mark unverified — **do not escalate** |
| `FAIL` | A check ran and disagreed, or extraction failed/returned implausible output | Escalate to the ladder |
| `INDETERMINATE` | Checks conflict | Escalate to the ladder |

Escalation triggers include: OCR returned empty or failed for a page; text density
implausible against the page's classification (`MIXED_CONTENT` page yielding near-zero
characters); reading order incoherent (companion §3.5 — narrative shredded by an adjacent
table's header cells); a table detected structurally but unparsed; arithmetic identity
violated beyond tolerance; split-digit pattern detected; mixed date conventions in one column
(companion §3.4).

#### 4.9.2 Minimum necessary evidence

The model receives the least material that can answer the question, assembled by
`PageRasterizer`:

- the **cropped region** when the failure is localised to one table or block; the **full page
  image** only when the failure is page-wide;
- the existing native text, OCR text, and detected table structures for that same region;
- the specific reason for escalation and the checks that failed.

It does **not** receive the whole document, neighbouring pages, or unrelated metadata. This is
a cost control, a data-minimisation control, and a precision control at once — a narrower
prompt reconstructs a table better than a wider one.

*Verified 2026-08-14:* `pdfplumber.Page.to_image(resolution=150)` renders via **pypdfium2
5.0.0**, already installed transitively, and Pillow crops the result — measured in
`backend/.venv` in this session. **No new dependency, and no AGPL exposure**, so the companion
document's rejection of PyMuPDF stands unchallenged.

#### 4.9.3 Value preservation and the anti-fabrication rule

The model reconstructs missing or unreliable text, tables, reading order, and related content,
and must preserve contractual values — dates, amounts, clause numbers, reference numbers,
quantities, percentages — **exactly as evidenced by the source**.

Enforcement is not a prompt instruction; prompts are not a control. It is three mechanisms:

1. **Re-verification.** The reconstruction is fed back through `ExtractionQualityGate` — the
   same checks, not a lighter set. A reconstructed cost table must satisfy the same column
   sums and qty × rate identities as a natively extracted one.
2. **Corroboration classing.** Every contractual value in the output is classed
   `corroborated` (matches a token in the native/OCR evidence, or satisfies an independent
   arithmetic identity) or `uncorroborated` (read from the image alone, nothing to check it
   against). Uncorroborated values are persisted as **unverified**, are never promoted to an
   authoritative chronology or claim figure, and render as uncertain — consistent with the
   companion's rule for ambiguous dates.
3. **Confidence is never sufficient.** A model's own confidence score may *route*, but may
   never *accept*. An output that no check corroborates and no check contradicts is
   `NOT_CHECKABLE`, not `PASS`.

Repair of a detected corruption follows the companion's dual-confirmation rule unchanged:
accept a numeric repair only when **two independent structural checks agree**; record
before / after / reason / method / confidence / page; never repair on a single check.

#### 4.9.4 The ladder

```
Tier 0  deterministic extraction (native text-layer / OCR)
          │ gate: FAIL or INDETERMINATE
Tier 1  configured vision-capable model, page or region
          │ re-gate: still FAIL or INDETERMINATE
Tier 2  configured higher-capability model
          │ re-gate: still unresolved, or budget/availability exhausted
        HUMAN_REVIEW_REQUIRED   (terminal — never `completed`)
```

Per `CLAUDE.md`, every tier is a `strict=True` call with a pinned prompt-version constant.
The deterministic fallback for a *drafting* call is degraded prose; here there is no
deterministic way to read a page that OCR could not, so **the fallback is to stop and mark the
page `HUMAN_REVIEW_REQUIRED`** — never to emit invented content, and never to silently retain
the failed Tier-0 output as though it had passed. Same principle, correct application.

Failure is contained at the page: an unresolved page yields `PARTIALLY_PROCESSED` at the
document level (§4.4), not a failed document. A document fails only when **no** page produced
usable content and the ladder resolved none of them.

Budgets are enforced per page, per document, and per organisation, and every tier invocation
is metered. Tier 2 is off by default and enabled per deployment.

#### 4.9.5 Intervention provenance

Every LLM intervention writes an immutable `page_extraction_interventions` row:

```
document_id, page_number, region_bbox | null
trigger          : the gate verdict and the specific checks that failed
tier             : 1 | 2
model, model_version, prompt_version
evidence_sent    : region|page, dpi, which text sources were included
confidence       : model-reported, recorded but never sufficient alone
corrections      : [{before, after, reason, method, corroboration}]
post_check       : the re-gate verdict on the reconstruction
status           : resolved | escalated | human_review_required
tokens, cost, latency, actor (system), timestamp
```

The ledger is append-only and scoped to the document, so it inherits document RBAC. It is the
audit answer to "why does this page read differently from the PDF?" — a question this system
must be able to answer in an arbitration context.

Under no circumstances does the system fabricate missing content, or mark an unresolved
extraction as successfully completed.

### 4.10 Canonical upload policy

The backend becomes the single source of truth. `UploadPolicyService` derives, from settings,
the allowed MIME set, the corresponding extension list, and the size cap **per surface**
(document, enclosure, contract, archive), exposed as `GET /api/config/upload-policy`.

The client fetches it and builds the picker's `accept` attribute and its client-side
validation from the response, with the current static list retained only as an offline
fallback. A test asserts the fallback is a subset of the served policy, so the two cannot
drift silently the way they have (§3.5).

A runtime endpoint is chosen over build-time codegen deliberately: these allowlists are
env-overridable, so a generated constant would reflect defaults rather than deployed
configuration.

---

## 5. Risks and safeguards

| Risk | Severity | Safeguard |
|---|---|---|
| OCR volume rises sharply once the 5-page heuristic stops suppressing it | High | Dedicated `document-worker`; Phase 0 samples production to size the increase; Phase 11 benchmarks CPU, memory, wall time, and OCR pages before rollout |
| **A naive quality gate escalates good pages, turning cost control into unbounded LLM spend** | **High** | Companion §3.3 measured 12 false positives on a correct document. Phase 6 is a hard prerequisite for Phase 7, with both the 12-false-positive and 9-corruption counts as numeric gates |
| An LLM reconstruction fabricates a contractual value | High | Re-verification through the same deterministic gate; corroborated/uncorroborated classing; uncorroborated values persist as unverified and never become authoritative; confidence may route but never accept |
| Tier-2 escalation loops or runs away on a pathological document | Medium | Bounded tiers (no retry beyond Tier 2), per-page/document/org budgets, Tier 2 off by default, terminal `HUMAN_REVIEW_REQUIRED` |
| Page images sent to a model leak more than needed | Medium | Region-preferred evidence assembly; full page only when the failure is page-wide; ledger records exactly what was sent |
| Two processes claiming the same Mongo job | High | Verify atomic `findOneAndUpdate` claim in Phase 0; it is a prerequisite fix if absent |
| Page-index mapping bug propagates to four callers | High | §3.3 hardened and covered by fixtures **before** the engine is shared (Phase 1) |
| Enclosure child rows leak into document listings | Medium | Default `parent_document_id: null` filter; explicit tests on listing, count, and export paths |
| Photo classifier accepts stamps/logos | Medium | Boilerplate hash-repeat reject plus bilevel and colour-count rejects; fixtures assert zero accepts on the sample claim's ~30 embedded images |
| Photo classifier rejects genuine site photos | Medium | Uncertainty band routes to Vision rather than to `REJECTED`; `REVIEW_REQUIRED` is resolvable by a human |
| Vision spend grows unbounded | Medium | Band-only invocation, per-document and per-org caps, metering, `strict=True` with a fallback that declines rather than accepts |
| Archives become a malware vector | Medium | AV retained and mandatory; no unpacking anywhere in the system |
| Contract path regresses while being generalised | High | Phase 1 is behaviour-preserving refactor only, gated on the existing contract test suite plus new golden page-routing fixtures |
| `RUN_SCHEDULER` accidentally duplicated on the new worker | Medium | Explicit `false`; leader lock is the existing safety net |

---

## 6. Phased plan

Each phase is independently reviewable and verifiable. Phases 1–2 change no observable
behaviour.

### Phase 0 — Evidence, fixtures, and prerequisites
**Outcome:** every later phase has a pass/fail gate and no assumption is load-bearing.
- Turn the 9-page sample claim into fixture #1 with golden page routing (pages 1–2 to OCR,
  3–9 native) and a golden asset expectation (**zero** accepted photographs among its ~30
  embedded images).
- Add fixtures for: a PDF with genuine site photographs; a scanned-only PDF; a landscape
  page; a `.zip`; a `.rar`; a standalone PNG and JPEG.
- Sample production: count documents whose page count exceeds their pages-with-text count.
  This sizes both the OCR increase and any backfill.
- **Verify** the document job claim is atomic; **verify** `build_scope_query` and the library
  listing behave with child rows present; **verify** ClamAV RAR support in the container;
  **verify** the output page count of `ocrmypdf --pages` in the container (§3.3), since the
  correct mapping in Phase 1 depends on the answer.
- Baseline benchmark: CPU, memory, wall time, OCR pages per document on the current path.
**Verification:** fixtures committed and running in CI; three verification questions answered
in writing with evidence; baseline numbers recorded.

### Phase 1 — Harden page mapping, extract `PageExtractionEngine`
**Outcome:** the contract path runs on the shared engine with byte-identical behaviour.
- Replace the `output_count >= max(ordered)` heuristic (§3.3) with explicit source→output
  page mapping derived from the requested range, using the Phase 0 container measurement.
  Assert the mapping rather than infer it: if a batch's output does not have the expected
  shape, fail the batch visibly instead of falling back to positional indexing.
- Extract the engine; introduce `PageStore`; implement `ContractPageStore` carrying all
  existing contract writes and metering.
**Verification:** existing contract suite green; golden page-routing fixtures pass; a
differential test shows identical page records before and after on fixture #1.

### Phase 2 — Worker topology
**Outcome:** extraction can run off the web tier.
- Add `START_DOCUMENT_EXTRACTION_WORKERS`; move `periodic_document_processing_jobs()` behind
  it; leave the other three background tasks alone.
- Add the `document-worker` compose service with the flag matrix from §4.5.
**Verification:** with the flag off on `backend`, assignment alerts and subscription
lifecycle still run (asserted); document jobs are claimed only by the new worker; scheduler
ownership remains solely with `contract-worker`.

### Phase 3 — General path on the shared engine
**Outcome:** the §3.1 defect is fixed.
- Implement `DocumentPageStore` (`document_ocr_pages`, `document_ocr_batches`).
- Route the general path through the engine; add `PageClassifier`; add resumable batching and
  the `PARTIALLY_PROCESSED` / `HUMAN_REVIEW_REQUIRED` states.
- Retire `is_pdf_textual`'s document-level decision from the general path.
**Verification:** fixture #1 routes pages 1–2 to OCR and only those; a forced mid-document
failure yields `PARTIALLY_PROCESSED` with a correct deferred list and resumes cleanly; no
document reaches `completed` with unextracted pages.

### Phase 4 — Source-kind routing
**Outcome:** images and text stop being fed to a PDF path.
- `SourceKindRouter`; single-page image OCR; images stored as-is, never wrapped in a PDF;
  text passthrough.
**Verification:** PNG and JPEG fixtures produce OCR text and remain image `FileObject`s;
`process_pdf` is not invoked for non-PDF input.

### Phase 5 — Archives and canonical upload policy
**Outcome:** archives upload cleanly; the picker matches the backend.
- ZIP/RAR sniffing; allowlist additions; `ArchiveIntakePolicy`; letter-number exemption;
  no-job gate.
- `UploadPolicyService` + `GET /api/config/upload-policy`; client picker driven by it.
**Verification:** `.zip`/`.rar` upload succeeds with AV and duplicate checks recorded and no
job created; an EICAR-in-zip fixture is rejected; a `.gif` is refused consistently by both
picker and backend.

### Phase 6 — Deterministic extraction quality gate
**Outcome:** every page from every source is assessed, and escalation becomes affordable.
**This phase gates Phase 7 and must not be skipped or thinned.**
- Column-role mapping (`S/N`, `Nos`, `Qty`, `Unit`, `Rate`, `Amount`) before any numeric
  check; in-table declared-formula support; rounding tolerance; split-digit detection with
  dual-confirmation repair; a dedicated date-convention checker with an `uncertain` state;
  reading-order coherence checks.
- Four verdicts (§4.9.1). `NOT_CHECKABLE` must never escalate.
- Runs unconditionally, native pages included.
**Verification:** on fixture #1, the gate produces **zero** `FAIL` verdicts from the
12 known false-positive patterns (companion §3.3), and detects **all 9** known split-digit
corruptions (companion §3.1) with correct repaired values. Both numbers are hard gates: the
ladder is not enabled until they are met.

### Phase 7 — LLM/Vision extraction fallback ladder
**Outcome:** failed and low-confidence extraction is recovered or explicitly escalated, never
silently accepted and never fatal to the document.
- `PageRasterizer` (pypdfium2 via pdfplumber + Pillow, verified available); minimal-evidence
  assembly (region-preferred, page only when page-wide).
- Tier 1 and Tier 2 `strict=True` calls with pinned prompt versions; re-verification through
  the Phase 6 gate; corroborated/uncorroborated value classing; per-page, per-document, and
  per-org budgets with metering.
- `InterventionLedger` with the full provenance record (§4.9.5).
- Page-level failure containment: an unresolved page yields `PARTIALLY_PROCESSED`, not a
  failed document.
**Verification:** a scanned-page fixture whose OCR is forced to fail is recovered at Tier 1
and its reconstruction passes the same checks; a deliberately unrecoverable page reaches
`HUMAN_REVIEW_REQUIRED` and the document is `PARTIALLY_PROCESSED`, never `completed`; model
confidence alone never accepts an output; **spend on fixture #1 is zero**; every intervention
has a ledger row with page, trigger, model, version, corrections, and post-check verdict.

### Phase 8 — Enclosures as child documents
*No dependency on Phases 6–7; may be resequenced earlier if enclosure delivery is more urgent
than extraction quality.*
**Outcome:** enclosures gain full extraction with preserved provenance.
- Child `documents` rows with `parent_document_id`, `relationship_type`,
  `source_page_range`, `parent_provenance`; own version and job.
- Default listing filter; retrieval left unfiltered; `documents.enclosures[]` maintained for
  one release; wire the client mutations that exist but are unreachable today.
**Verification:** a child document is OCR'd, chunked, and vectorised independently; it is
absent from library listings and present in search results with parent attribution;
cross-tenant isolation tests pass on the new rows.

### Phase 9 — Progress-photo extraction, deterministic stages
**Outcome:** embedded photographs become independent assets; junk does not.
- Stage 1 filtering and Stage 2 scoring; asset `FileObject`s with page and bbox; deep link to
  the source page.
**Verification:** fixture #1 yields **zero** accepted assets; the site-photo fixture yields
its expected set; every decision carries a recorded reason.

### Phase 10 — Selective Vision adjudication for photo candidates
*Distinct from Phase 7: a different question, prompt, and budget. Shares only `PageRasterizer`
and the metering infrastructure.*
**Outcome:** the uncertainty band is resolved without paying for the confident majority.
- `VisionAdjudicator` with `strict=True`, pinned prompt version, per-document and per-org
  budget caps, metering, and a decline-not-accept fallback.
**Verification:** only `REVIEW_REQUIRED` candidates trigger calls; disabled/over-budget/failed
adjudication leaves them `REVIEW_REQUIRED` and unindexed; spend on fixture #1 is zero.

### Phase 11 — Review UI, benchmark, and rollout gate
**Outcome:** the feature is operable and its cost is known before production.
- Asset viewer with page deep link; a review queue serving **both** `REVIEW_REQUIRED` photo
  candidates and `HUMAN_REVIEW_REQUIRED` pages, with the intervention ledger visible on the
  page-review path so a reviewer can see what the model changed and why.
- Benchmark CPU, memory, processing time, OCR-page volume, **and LLM tier-1/tier-2 call
  volume and spend** against the Phase 0 baseline.
**Verification:** measured deltas recorded; rollout proceeds only if within an agreed budget,
otherwise the gate thresholds, batching, and concurrency parameters are retuned first.

### P2 — Flattened and scanned-page photo extraction
Out of scope here. Page-region detection over rendered page rasters, for photographs that are
not separate XObjects. Recorded explicitly so its absence is not mistaken for coverage.

---

## 7. Review coverage and blind spots

**Traced end to end:** general single upload, general bulk upload, enclosure upload, contract
upload, the durable job loop, OCR decision points, MIME validation, and worker topology.

**Production lenses applied:** correctness, tenancy/RBAC, security (AV, archive handling),
failure recovery and resumability, cost, capacity, observability, configuration, deployment,
and test coverage.

**Not verified in this pass, deferred to Phase 0:**
- atomicity of the document job claim;
- listing and scope behaviour with child document rows present;
- ClamAV RAR support in the deployed image;
- current production OCR volume and the size of the increase;
- whether any consumer other than `ShareDocumentPage` reads `documents.enclosures[]`;
- which concrete models fill Tier 1 and Tier 2, and whether the deployment's configured
  provider exposes a vision-capable endpoint at both tiers;
- the real-world escalation rate once the Phase 6 gate exists — it determines whether the
  ladder's cost is a rounding error or a line item, and it cannot be estimated from the single
  fixture measured so far.

**Verified in this session, by execution:** page rasterization at 150 DPI and region cropping
via `pdfplumber` → `pypdfium2 5.0.0` → Pillow, run in `backend/.venv`. This is the only claim
here established by running code rather than reading it.

**Inference, flagged as such:** that pages 1–2 of the sample claim are the covering letter.
They contain no text layer, so this is inferred from position and document structure and can
only be confirmed by OCR-ing them inside the backend container.

---

## 8. Decision gate

Resolved by the requester and recorded here: Approach A+ with a dedicated worker;
adapter-preserved persistence; deterministic filtering plus selective Vision; `ACCEPTED`-only
visibility; embedded photos in Phase 9 with flattened pages as P2; enclosures as first-class
child records excluded from top-level listings but present in retrieval; archives stored
unchanged with AV and duplicate checks; letter number optional for archives; page mapping
hardened before sharing; resumable batching with fail-visible states; backend-canonical file
policy; classification beyond the character threshold; benchmarking before rollout.

Also recorded: LLM/Vision fallback for failed, incomplete, or below-threshold extraction, with
minimal-evidence prompting, mandatory re-verification through the same deterministic checks,
confidence-never-accepts, a bounded two-tier ladder terminating in `HUMAN_REVIEW_REQUIRED`,
full intervention provenance, page-level failure containment, and no fabrication.

Open, and needed before Phase 9 completes rather than before Phase 1 starts:

1. **`REVIEW_REQUIRED` retention.** Do we retain candidate bytes for human review, or only
   metadata and a page reference? Retention costs storage; metadata-only means the reviewer
   opens the source page instead.
2. **Who resolves the review queue** — the uploader, a project reviewer, or an org admin?
   This determines the permission the queue is gated on.
3. **Photograph privacy.** Site photographs may contain identifiable people. Assets inherit
   document scope, which is the safe default, but confirm no export path widens it.
4. **Tier 1 and Tier 2 model selection**, and whether Tier 2 is enabled in production at all.
   The design treats Tier 2 as off by default; turning it on is a cost decision.
5. **Where the quality thresholds sit.** The gate's tolerances (rounding tolerance, text-density
   implausibility, reading-order coherence) set the escalation rate directly. They should be
   tuned against the Phase 0 fixture corpus and then held as pinned constants, not left as
   free-floating configuration that drifts spend without review.
6. **Uncorroborated-value rendering.** A value the ladder read from an image that no check can
   corroborate is persisted as unverified — but the UI, search, drafting context, and
   arbitration bundles each need to decide whether to show it, hide it, or show it marked. The
   companion document argues for "marked, never silently smoothed"; confirm that holds across
   all four consumers.
