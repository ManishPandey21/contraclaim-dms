# Release certification: 1.1 — Unified OCR canary enablement

Certification only. Production was not changed: it runs Release 1.0 (`2533b19`), with
unified extraction globally off, an empty allowlist and no canary worker.

Evidence paths marked `H:` are in the staging harness
`C:\SaaS-release-cert-d7eef845\evidence\`, which is outside every repository.

## 1. Identity and scope

| Item | Value |
|---|---|
| Release SHA | `d7eef845e6e894f10ca1cdb9d9ce5ede0430df1e` |
| Release tree | `fbb006216f277b59219163a9a51b521443c29fe4` |
| Base (Release 1.0) | `2533b19d` / tree `e3804d6d` |
| #63 | head `45b7f1cb`, merge `65807a0e` (intermediate tree `9650c15a`) |
| #64 | original head `4abfc487`, updated head `daf00770` (normal merge of release, no rebase), merge `d7eef845` |
| Delta | 5 files: `docker-compose.prod.yml` + its compose test (#63); `routers/documents.py`, processing-status route tests, route inventory test (#64) |
| Open PRs on release | #44, #45 — unmerged, out of scope |

`daf00770^{tree}` equals the release tree, so the #64 CI ran on the exact Release 1.1 tree.

## 2. Test results (exact counts)

| Gate | Where | Result |
|---|---|---|
| Backend full suite | CI run 38169616890, exact SHA | **6813 passed, 667 skipped, 4 xfailed, 0 failed** |
| Real-Mongo must-run suites | same | **567 passed; all 26 modules ran** |
| pre-commit | same | **9/9 Passed** |
| compileall | same | ok |
| Frontend eslint | same | 0 problems (`--max-warnings=0`) |
| Frontend vitest | same | **651 passed, 3 skipped** |
| Frontend e2e | same | **42 passed, 37 skipped** |
| Frontend build | same, and the certified client image | ok |
| Secret scan (gitleaks) | same | no leaks found |
| pip-audit | same | 0 vulnerabilities, 1 accepted ignore |
| npm audit gate | same | passed (accepted braces exception only) |
| CI image scan | same | job success |
| Focused #63/#64 tests **in the certified backend image** | `H:focused-tests.log` | **48 passed** (status route 8, harness supplement 13, compose 22, route inventory 5) |
| Trivy on the certified images | `H:trivy/` | backend 0, client 0 fixable HIGH/CRITICAL; freshness gate **GO** |

The harness supplement (`C:\SaaS-release-cert-d7eef845\supplementary\test_r11_status_matrix.py`,
not committed) adds what the shipped tests leave implicit:
- every durable status is served exactly as stored, with its stage: completed, failed,
  dead_lettered, human_review_required, partially_processed (settled), partially_processed
  (`awaiting_page_resume`), retrying, processing, queued and stored_only;
- `not_queued` when no job exists;
- a foreign organisation's record answers exactly **403**, as `GET /documents/{id}` does;
- deleted and unknown documents answer exactly **404**.

## 3. #63 routing acceptance

Rendered `docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`:

| Service | `UNIFIED_EXTRACTION_ENABLED` | `UNIFIED_EXTRACTION_CANARY_ORG_IDS` | Pipeline versions | Replicas |
|---|---|---|---|---|
| backend | `false` | `""` | — (does not extract) | — |
| document-worker | `false` | `""` | `legacy_v0` | 1 |
| document-worker-canary | `false` | `""` | `unified_v1` | **0** |
| contract-worker | absent | absent | — | 1 |

Setting the allowlist in `.env` reaches all three routing services from one value. Worker
routing is unchanged from 2533b19.

## 4. #64 status endpoint

Access is identical to `GET /documents/{id}`: `_ensure_document_access`, then
`controller_get_document`, then `PolicyService.authorize_document`, in the same order. The
response is a whitelist of the client's `DocumentProcessingJobStatus` fields. Job internals
(file path, sha256, requester, tenant ids, pipeline version) are never returned.

**Error-text review (P2-A, pre-existing):** `error.message` and
`metadata.partial_failures.*.message` hold raw `str(exc)` from providers. Staging captured
`Incorrect API key provided: dummy-lo**********-key` (the provider masks the key) in
`partial_failures`. No stack traces are ever stored. Provider text can carry internal
hostnames, or upload paths from a `FileNotFoundError`.

`GET /documents/{id}` already returns the same text to the same principals, through
`Document.processing_error` and `processing_metadata` (`models/document.py:117-118`). So #64
adds no new exposure. Classified P2, not P1. Recommended later: sanitise provider messages
at the write sites (`document_processor.py`, `database_service.py`,
`document_service.py` unexpected-error handler).

## 5. Staging topology

Disposable local stack: compose project `ccert-d7eef845`, the exact certified images, own
Mongo (rs0), password Redis, Qdrant, ClamAV and uploads volume. Ports are loopback only.
Tenants are `org1/proj1` (canary) and `org2/proj2` (control). 19 migrations were applied
(dry-run first). Isolation proof `H:isolation.txt`: 0 production identifiers. Routing wiring
copies Release 1.1 production: one variable pair feeds backend + both workers, and canary
replicas come from `DOCUMENT_WORKER_CANARY_REPLICAS`.

Deviations from production: `ENVIRONMENT=development`, no FalkorDB, no S3, and dummy
OpenAI/AWS/SMTP. So there is **no LLM and no embedding provider**: every document ends
`partially_processed` (indexing failed) unless review applies. That outcome is truthful and
expected. The Docker VM has 4 GB and 8 vCPU.

## 6. Routing, claims, rollback

| Phase | Setup | Upload | Result |
|---|---|---|---|
| 7 Legacy control | global off, allowlist empty, canary worker **running** | org1 | `legacy_v0`; document-worker 16 log lines, canary 0, contract-worker 0 |
| 8 negative control (old runbook) | allowlist `org1` on **workers only**; backend not recreated | org1 | **`legacy_v0`** — the API never saw the allowlist |
| 8 Canary routing | backend recreated with `org1` (dry-run: only backend changed) | org1 GLM | **`unified_v1`** |
| 8 Control | same | org2 GLM | **`legacy_v0`** |
| 9 Claim isolation | both workers running | — | canary claimed only the unified doc (17 lines, 0 for legacy); legacy worker only the legacy doc (16 / 0) |
| 15 Rollback | clear allowlist → recreate backend + document-worker; 0 unified jobs in flight; canary replicas 0 | org1 | **`legacy_v0`**, legacy worker; all 3 earlier `unified_v1` jobs byte-identical (version, status, attempts, run id, head sha, revision, rows); evidence counts unchanged (35 page rows, 7 heads) |

Job records carry no claimer field, so claim proof is the persisted `pipeline_version` plus
worker logs (observability gap, P2-C).

## 7. GLM A/B (`GLM-SAM-KNPCC-05-UPMRC-OL-2022-2326.pdf`, sha256 `e727e5d5…`)

| Attribute | Legacy (org2) | Unified (org1) |
|---|---|---|
| Pipeline | `legacy_v0` | `unified_v1` |
| OCR pages | none | 1, 2, 5 |
| Native pages | (3–5 text layer, 150 chars) | 3, 4 |
| Text chars | 150 | **3555** (page sum 3547) |
| Known facts | 0/8 | **8/8** |
| Canonical evidence | not built | head rev 1, 5 rows, page map 5/5, checksum `1410b434443ab7dcebde53ac0055c4e090ccc5ef521f0e2254b32574bbabd0a4` |
| Review / failed pages | — | none / none |
| Status | `partially_processed` (indexing, no embedder) | same |
| Duration | 16.7 s | 115.2 s |

The unified checksum, characters and page methods equal the 2533b19 certification exactly.

## 8. Metadata A/B (staging = deterministic fallback parser only)

Staging has no LLM, so both runs used `ocr_fallback_regex`; production uses the OpenAI path.
This table therefore measures the text available to metadata plus the fallback parser, not
the production model.

| Field | In source text | Legacy | Unified (fallback parser) |
|---|---|---|---|
| Reference number | yes | MISSING | INCOMPLETE — in summary/keywords; `letter_no` field empty |
| Document date (24-12-2022) | yes | MISSING | INCOMPLETE — in summary; `date` field empty |
| Subject (soil collapses / D-walls) | yes | MISSING | INCOMPLETE — keywords only |
| Sender (Gulermak–SAM JV) | yes | MISSING | INCOMPLETE — keywords only; `from` empty |
| Recipient (Engineer's Representative, Typsa–Italferr JV) | yes | MISSING | INCOMPLETE — summary only; `to` empty |
| Referenced letters | no other letter number in the recovered text | — | — |
| Contract KNPCC-05 | yes | MISSING | INCOMPLETE — via reference/keywords |
| Clauses 8.4.1 / 17.1 | yes | MISSING | MISSING — `contractual_clauses` empty |
| Delay / soil collapse | yes | INCOMPLETE (photo captions only) | CORRECT (keywords) |
| Claim / rights reservation | yes | MISSING | MISSING |

**Did unified OCR provide the complete text metadata needs? YES.** Every field is present in
the unified text and absent from the legacy 150 characters. The remaining gaps are in the
deterministic fallback parser (P2-B, metadata layer), not OCR. The production canary must
measure the LLM path. Upload-form values (letterNo/date/subject) are stored separately and
were not counted as extraction.

## 9. Canonical evidence without Qdrant / embeddings / LLM

`cert_canonical.py` ran in the certified image against staging Mongo with **Qdrant stopped**
and all outbound HTTP(S) blackholed. Result: **30/30 passed** (`H:phase12-canonical.json`).
It covered the GLM and claim documents:
- reads, checksum, page map, span-to-page mapping and raw/corrected provenance;
- refusals: no selection → refused; foreign project and foreign organisation → 403; Super
  Admin bounded by selection, with a positive control;
- deleted document → 404 for member and Super Admin, with rows and head retained;
- a stale publisher is refused;
- tampered rows, a missing row and a scope mismatch all fail closed;
- the legacy document reads as `not_built`.

## 10. Status endpoint and UI contract

| Document | Endpoint result |
|---|---|
| legacy / unified (no embedder) | 200 `partially_processed`, stage `indexing_incomplete`, truthful indexing message |
| claim (review) | 200 `human_review_required`, stage `human_review_required` |

Review truthfulness: the claim PDF ends `human_review_required` with page 5 verdict `fail`.
`document.processing_error.message` reads "1 page(s) need review (failed the extraction
quality check: page(s) 5)", with `quality_review_pages: [5]`. There is no "0 pages" message.

The job stores `review_page_numbers: [5]` but `error: null`, and the endpoint whitelist does
not include `review_page_numbers`. So the endpoint alone shows the status without the pages.
The viewer reloads the document once on any terminal status (`DocumentViewerPage.tsx`
267-273) and shows `processingStatus.error || document.processing_error`, so the rendered
message is truthful. The #64 unit test models review pages on the job's `error`, which the
real pipeline does not write. Classified P2-D: the endpoint does not carry review pages.

Polling contract (`utils/processingStatus.ts`, CI vitest): these stop polling —
completed, failed, dead_lettered, human_review_required, stored_only, settled
partially_processed and not_queued. These keep polling — processing, retrying, queued and
partially_processed + `awaiting_page_resume`. The viewer passes the endpoint's `status` and
`stage` to the predicate.

Browser UI: **NOT RUN**. The seed user must accept the Security Terms first, and that was not
authorised.

## 11. Runbook correction (this branch)

`docs/operations/unified_extraction_canary_and_rollback.md` §4/§5 and
`unified_extraction_canary_execution_package.md` S3.3 and items 7/9 recreated only the
workers. **An allowlist change requires a `backend` recreate.** The API reads `settings` once
at import (`core/config.py:938`) and assigns `pipeline_version` at upload. Staging proved it:
the negative control in §6.

Second correction: clearing `DOCUMENT_WORKER_PIPELINE_VERSIONS` does **not** make the
default worker unrestricted. Compose passes `${DOCUMENT_WORKER_PIPELINE_VERSIONS:-legacy_v0}`,
so a `unified_v1` job still queued when the canary is scaled to 0 waits, intact, until a
canary worker runs again. The rollback therefore drains in-flight unified jobs before
scaling down.

## 12. Images

Built 2026-10-12 15:27–16:04Z from `git archive d7eef845` with
`docker build --pull --no-cache --build-arg RELEASE_SHA=d7eef845e6e894f10ca1cdb9d9ce5ede0430df1e`.
The revision label is the release SHA on both images.

| Service | Local id (Docker Desktop, OCI index) | Config digest (what a classic-store host reports after load) |
|---|---|---|
| backend, contract-worker, document-worker, document-worker-canary | `sha256:0da4f92d3036d550f3e616558b76f7b5dad23ae670eea668e155612220205d9a` | `sha256:cb4165d4849c7cfd641454bdb95afc6b47ec73641c59625be1bfd84c504ae005` |
| client | `sha256:c10a2b22b3c18286d363c217202f00b2a1a1102228e6fe099fec9eddbc747329` | `sha256:45638954a34198c92b8faad7033a4d8121ec5886ff5f6d3898f14b8af7516606` |

Trivy 0.70.0 (`--ignore-unfixed --severity CRITICAL,HIGH --exit-code 1`) reported 0 fixable
HIGH/CRITICAL on both images. The DB was refreshed at 16:47Z, and the reports were created
16:46Z (client) and 16:51Z (backend). `check_image_scan_freshness.py` = GO; **valid until
2026-10-13 16:46Z**. A client image is required because the release identity is baked into
it.

Resources (GLM unified): the canary worker peaked at 283 % CPU and 532 MiB. The job took
115.2 s (90.4 s on 2533b19, same host class). A separate OCR-stage duration was not captured.

## 13. Findings

| ID | Class | Finding |
|---|---|---|
| — | P0 | none |
| — | P1 | none |
| P2-A | P2 (pre-existing) | Raw provider exception text (masked key fragments, hostnames, paths possible) in `error` / `partial_failures`; already served by `GET /documents/{id}` |
| P2-B | P2 (metadata layer) | Deterministic fallback parser leaves date / letter_no / from / to / clauses empty although the unified text contains them |
| P2-C | P2 (observability) | Jobs record no claiming worker; claim isolation is provable only from logs |
| P2-D | P2 | Status endpoint does not expose review page numbers (job `review_page_numbers` not projected, job `error` null); viewer stays truthful via `document.processing_error` |
| P2-E | P2 (docs, fixed here) | Runbook worker-only allowlist restarts; "unrestricted default worker" claim |

Known limitations, unchanged: G15, G16, R4, whole-document embedding withholding, enclosures
not extracted, separate contract page store, SMTP EOF, summary "- null", legacy mixed-PDF OCR
loss, chronology A0c/D5/D6.

## 14. Verdict

STAGING CERTIFIED = **YES**.
READY FOR RELEASE 1.1 PRODUCTION DEPLOYMENT (canary off) = **YES**, subject to the owner
approving the Phase 12 gates.
READY FOR CONTROLLED CANARY = **YES after** Release 1.1 is deployed, the legacy smoke passes,
and the owner names the canary organisation/project.
READY FOR GLOBAL UNIFIED = **NO**.
