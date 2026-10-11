# Release certification: OCR + Canonical Evidence

**Status:** CERTIFIED FOR STAGE A (code deploy, unified_v1 globally OFF), subject to the owner
approval points in §18. **NOT READY for the production canary** until P1-1 (§17) is fixed.
**NOT READY for global unified_v1 enablement** (never in scope for this release).

Prepared 2026-10-11. Production was not changed. Every production read in this document
was read-only.

---

## 1. Release scope

One vertical capability: **OCR + Canonical Evidence**. Page-wise mixed-content extraction
(`unified_v1`), its quality gates and status truthfulness, and a complete, checksummed,
page-mapped canonical text per document that does not depend on Qdrant, an embedding
provider or an LLM.

The release also carries every security and dependency fix merged onto
`release/contraclaim-rc1` since production's `1e89bf3`. Those are **not optional riders**:
they are already on the release branch, several close tenant-isolation defects that
production still has, and removing them would mean cutting a different release.

## 2. Release identity

| Item | Value |
|---|---|
| Release branch | `release/contraclaim-rc1` on remote `contraclaim` (`ManishPandey21/contraclaim-dms`) |
| RELEASE SHA | `2533b19d443fa972186e04a7e67113597d54041d` |
| RELEASE TREE | `e3804d6dd9c2c65fd34e358d3e5f7e2a4efac49b` |
| Remote `origin` release branch | `1e89bf3` (deliberately behind since #38; not synced as a side effect) |
| Worktree | `C:\SaaS-release-rc1`, clean at the release SHA before this document was added |
| CI on the exact tree | run [38120404911](https://github.com/ManishPandey21/contraclaim-dms/actions/runs/38120404911): PR #20 merge commit `d592896`, whose tree **is** `e3804d6` (main is an ancestor of the release, so the merge tree equals the release tree; verified with `git log -1 --format=%T`) |
| Open PRs targeting the release | contraclaim-dms#44 (variation link modal layout), contraclaim-dms#45 (explicit All-scope, draft). **Neither is required; both excluded.** |
| Unmerged changes required for this release | **None** for Stage A. P1-1 (§17) needs one compose-only change before the canary. |

### Production as read on 2026-10-11 (read-only)

| Item | Value |
|---|---|
| Checkout | git root `/opt/contraclaim-dms` (app dir `projectDMS/`), branch `release/contraclaim-rc1`, remote `origin` = `contraclaim-dms`, no upstream set |
| PRODUCTION SHA | `1e89bf3036692e2cfd127a29102d6c45338ceacf` |
| PRODUCTION TREE | `c133968f8478578cb560f08a0698776871559612` |
| Compose file set (container label) | `docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml` (gateway also carries `docker-compose.clamav-r-a8z.yml` from its last recreate) |
| backend / contract-worker / document-worker image | `sha256:a82a6b31336b16a4f2eb02a124bbb36e2a47c736a82a4e0094cd7f1b2b49b35c` |
| client image | `sha256:cd95b7e12cd21f7b8de4445b0779a0825a32a21dc2120d76acd54e63f2cb4d6b` |
| Approved manifest | `/opt/contraclaim-dms/release-manifests/current.json` = release `1e89bf3`, scope FULL, same four image ids |
| Rollback tags present | `contraclaim-final-backend:1e89bf3…` and `contraclaim-final-client:1e89bf3…` (same ids as running). The `contraclaim-rollback-*:fe728b2-pre1e89bf3` tags recorded on 2026-10-04 are **no longer present**. |
| Routing | `document-worker`: `UNIFIED_EXTRACTION_ENABLED=false`, `DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0`. `document-worker-canary`: not running (replicas 0). `backend`: receives no `UNIFIED_*` variable at all (see P1-1). |
| Migrations | all catalogue migrations `applied`, including `20260814_0001 document_extraction_indexes`; `20260730_0001` and `20260806_0001` read `applied_not_in_catalogue` |
| Page-evidence indexes | `document_ocr_pages` unique `(document_id, extraction_run_id, page_number)`; `document_extraction_heads` unique `document_id` |
| Page evidence | 0 page rows, 0 heads (unified has never run in production) |
| Jobs | 155 `document_processing_jobs`: 152 with no `pipeline_version`, 3 `legacy_v0`; 151 completed, 4 dead-lettered |
| Backups | **Corrected 2026-10-11 (window):** a nightly backup exists (`/etc/cron.d/contraclaim-backup`, 01:30 IST, local + offsite, root `/var/backups/contractdms`). The earlier "no backups" reading used the wrong root. `backup_status.py` = ok. Every nightly run since 2026-10-04 reports the FalkorDB flush **FAILED** (see P0-F1). |
| Host | 8 vCPU, 22 GB RAM (16 GB available), `/` 62 GB free |

## 3. Included PRs and features

Twenty PRs merged between `1e89bf3` and `2533b19` (`git log --first-parent 1e89bf3..2533b19`):

| PR | Merge | Area | Capability (requested list item) |
|---|---|---|---|
| #38 | `05ab61e` | Security | Tenant isolation for `GET /api/letters` (16) |
| #39 | `89c49e4` | Security | Full authentication for WebSocket connections (16) |
| #40 | `55f6bb8` | Security | Tenant scope query builders fail closed (16) |
| #41 | `db70b5c` | Security | Tenant isolation for deep-planning letter context (16) |
| #42 | `d04ace6` | Security | Deep-planning `target_letter_id` scoped to caller's tenant (16) |
| #43 | `6a287fc` | Security | Project scope for deep-planning document context (16) |
| #47 | `160f18f` | Dependency | Temporary PyMongo advisory exception (superseded by #52) (16) |
| #48 | `7f30471` | Dependency | source-map-js 1.2.2 (16) |
| #50 | `9041825` | Dependency | braces R3 exception renewed to **2026-11-05** (16) |
| #52 | `f2165b8` | Dependency | PyMongo 4.18.2 + checkpoint-mongodb 0.5.1; R4 retired (16) |
| #53 | `602f4e5` | Dependency | pydantic-ai 1.107.7 (2026-10-08 advisories) (16) |
| #51 | `6dd7f93` | Extraction | Page-wise mixed-content OCR, `unified_v1`, native/OCR routing, canary worker split (1, 2) |
| #54 | `b28760b` | Extraction | Numeric integrity across native text and OCR (3) |
| #55 | `ab59511` | Extraction | Table-header discovery, truthful quality gates (4) |
| #57 | `cef90e9` | Client | Terminal-status polling (7) |
| #56 | `596d194` | Extraction | Status and review-page truthfulness (5, 6) |
| #58 | `c4f6706` | Extraction | Canonical Evidence Store: full text, page map, revision, checksum, raw vs corrected, scoped read, deleted-document protection, same-document publish guard (8–15) |
| #59 | `98fb4e2` | Docs | Chronology foundation design (documentation only) |
| #60 | `70ef9e4` | Security | Chronology verification authority (17) |
| #61 | `2533b19` | Chronology | A0b event-date truthfulness (18) |

## 4. Explicit exclusions

Not implemented, not enabled, not part of this release: A0c / D5 / D6; new
`chronology_assertions` / `chronology_events`; chronology AI and review UI; Matter Context
Builder; SOC/SOD/Rejoinder changes; Project Evidence Graph redesign; enclosure extraction;
contract page-store unification; R4 side-by-side table reconstruction; partial vector
publication; G15; G16; historical production re-extraction; global `unified_v1`
enablement. Open PRs #44 and #45.

## 5. Known limitations (non-blocking)

| Id | Limitation | Evidence in this certification |
|---|---|---|
| KL-1 | G15: job-less processing path does not go through the page store | not exercised (normal queued route only) |
| KL-2 | G16: scanned image regions inside a typed page are not OCR'd | unchanged |
| KL-3 | R4: side-by-side table reading order | claim page 5 still fails the table gate → review (expected) |
| KL-4 | One review page withholds embeddings for the whole document | claim: `vector_sync_status=deferred`, review page 5 |
| KL-5 | Enclosure extraction absent | — |
| KL-6 | Contract documents keep a separate page store | — |
| KL-7 | Chronology A0c and the chronology foundation are not implemented | — |
| KL-8 | Existing production documents read canonical evidence `not_built` | staging legacy document → `not_built`, legacy text offered, no error |
| KL-9 | Legacy path loses scanned pages (legacy_v0 = one document-level text-layer decision) | staging legacy GLM: 150 chars, 0/8 facts; legacy claim: 5,228 chars, page-1 facts missing. This is current production behaviour, unchanged by this release. |
| KL-10 | Document viewer never sees job status (P2-1) | see §17 |
| KL-11 | Viewer polls a non-terminal status every 4 s for as long as the page is open (no cap); bulk upload tracker caps at ~10 min | code read, `DocumentViewerPage.tsx` / `UploadPage.tsx` |
| KL-12 | braces R3 exception expires **2026-11-05**; PyPI `PYSEC-2026-3740` (NLTK, no fix) accepted for this release | CI dependency scan |

## 6. Certification matrix

PASS = the expected result was observed. Evidence paths under `H:` are in the staging
harness `C:\SaaS-release-cert-2533b19\evidence\` (outside every repository).

| Area | Test | Expected | Actual | Evidence | Result | Blocker |
|---|---|---|---|---|---|---|
| A Backend | Full suite, exact tree, CI (Py 3.12.15) | 0 failed | 6803 passed, 667 skipped, 4 xfailed, 0 failed | CI job 114413771871 | PASS | No |
| A Backend | Full suite **inside the certified backend image** | 0 failed except environment | 6748 passed; 14 failed, all `FileNotFoundError: 'git'` (no git in runtime image; pass in CI on the same tree) | `H:full-suite.log`, `H:full-suite.xml` | PASS (environment-only failures, mechanism proven) | No |
| A Backend | pre-commit `--all-files` (9 hooks incl. mypy gate, forbid-print, forbid-legacy-authz) | all Passed | all 9 Passed | CI job 114413771871 | PASS | No |
| A Backend | `compileall` | ok | ok (CI and in-image) | CI; `H:suite-in-image.log` | PASS | No |
| B Frontend | `eslint --max-warnings=0` | 0 | 0 | CI job 114413771807 | PASS | No |
| B Frontend | vitest | 0 failed | 651 passed, 3 skipped (80 files + 3 skipped) | CI job 114413771807 | PASS | No |
| B Frontend | Playwright e2e (CI set) | 0 failed | 42 passed, 37 skipped | CI job 114413771807 | PASS | No |
| B Frontend | Production build | succeeds | CI ok; certified client image built `npm ci` + `npm run build` from the exact archive | `H:build-client.log` | PASS | No |
| C Real Mongo | 26 CI must-run real-Mongo modules (replica set) | 0 skip, every module ran | 567 passed, "all 26 module(s) ran" | CI job 114413771871 | PASS | No |
| C Real Mongo | Staging replica set end-to-end (upload → worker → page rows → head) | heads + rows written transactionally | 4 unified documents published (plus 4 clones for destructive probes); unique indexes enforced after migration | `H:state.json`, `H:phase5-canonical-with-indexes.json` | PASS | No |
| D Redis | Password-protected Redis for sessions/runtime/queue | login, sessions, permission cache work | all logins/step-up succeeded through Redis-backed sessions | `H:log-backend.txt` | PASS | No |
| E Qdrant | Canonical read with Qdrant **stopped** | full text served | 30/30 checks passed with `qdrant` container exited | `H:phase5-canonical.json` | PASS | No |
| F Worker queues | Disjoint claims: legacy worker vs canary worker vs contract worker | each job claimed only by its pipeline's worker | 7/7 jobs: legacy→`document-worker`, unified→`document-worker-canary`, contract-worker claimed 0 | `H:jobs.txt`, `H:log-*.txt` | PASS | No |
| F Worker queues | Retries / dead letters | 0 unexpected | 0 dead-lettered, 0 jobs with attempts > 1 | Mongo count | PASS | No |
| G OCR binaries | ocrmypdf / tesseract / ghostscript / qpdf in image | present | `ocrmypdf=16.10.4, tesseract=/usr/bin/tesseract, gs=/usr/bin/gs, qpdf=/usr/bin/qpdf` | `H:log-backend.txt` | PASS | No |
| H Storage | Shared upload volume between API and workers | workers read API-stored files | every upload processed by a worker from `/app/uploads/org…` | `H:log-*.txt` | PASS | No |
| I ClamAV | Clean upload accepted; EICAR refused; clamd down → refused (`ANTIVIRUS_ENABLED=true`, `CLAMAV_FAIL_OPEN=false`) | accept / refuse / refuse, no record on refusal | 7 clean accepted; EICAR → 400 "Antivirus scan rejected this file: Eicar-Test-Signature"; clamd stopped → 400 "Antivirus offline"; 0 documents created for either refusal | `H:log-backend.txt`, Mongo count | PASS (status code: P2-5) | No |
| J RBAC | Exact-tree suites (authz, scope, route inventory gate) | 0 failed | included in 6803 passed | CI | PASS | No |
| J RBAC | Real principals from real login tokens, real `PolicyService` | see §9 | 10/10 refusal + positive-control checks | `H:phase5-canonical.json` | PASS | No |
| K Canonical | §8–§9 acceptance | 13 properties | 30/30 checks, twice (before and after the prod-equivalent unique indexes) | `H:phase5-*.json` | PASS | No |
| L Status | Server-side status for success / review / indexing failure / settled | §10 | all observed values truthful | `H:state.json` | PASS | No |
| L Status | Viewer reflects job status | job status visible | **route missing; viewer falls back to `not_queued`** | live 404 + code read | FAIL (P2-1, pre-existing) | No |
| M Rollback | Clear API allowlist → new uploads `legacy_v0`; earlier unified jobs keep `unified_v1` | as stated | as stated | `H:state.json` p11 | PASS | No |
| M Rollback | Production canary allowlist actually routes | canary org → `unified_v1` | **production compose wiring cannot route** (API never receives the allowlist) | `H:state.json` p7 + compose read | FAIL (P1-1) | Canary only |
| N Security | Secret scan (gitleaks), exact tree | no leaks | "no leaks found" | CI job 114413771665 | PASS | No |
| N Security | pip-audit, exact tree | 0 (1 accepted ignore) | "No known vulnerabilities found, 1 ignored" (PYSEC-2026-3740) | CI job 114413771803 | PASS | No |
| N Security | npm audit gate | 0 HIGH/CRITICAL outside accepted exception | gate passed; raw audit 22 (8 moderate, 14 high) — all highs are the owner-accepted braces GHSA-vfj7-8cjw-p6xm (expires 2026-11-05) | CI job 114413771803 | PASS | No |
| N Security | Trivy, CI images (`--ignore-unfixed --severity CRITICAL,HIGH --exit-code 1`) | 0 fixable HIGH/CRITICAL | backend, client, langgraph, graphiti, docling all passed | CI job docker-build-and-scan | PASS | No |
| N Security | Trivy, **certified** images (exact image ids) | 0 fixable HIGH/CRITICAL | 0 / 0; freshness gate GO | `H:trivy/` | PASS | No |

## 7. Test results (exact counts)

| Gate | Where | Result |
|---|---|---|
| Backend full suite | CI exact tree | **6803 passed, 667 skipped, 4 xfailed, 0 failed** (287.7 s) |
| Real-Mongo must-run suites | CI exact tree | **567 passed, 0 skipped; all 26 modules ran** |
| pre-commit (all files) | CI exact tree | **9/9 hooks Passed** |
| Frontend lint | CI exact tree | **0 problems** (`--max-warnings=0`) |
| Frontend unit | CI exact tree | **651 passed, 3 skipped** |
| Frontend e2e | CI exact tree | **42 passed, 37 skipped** |
| Frontend build | CI + certified client image | **OK** |
| Secret scan | CI exact tree | **no leaks found** |
| Python deps | CI exact tree | **0 vulnerabilities, 1 accepted ignore** |
| npm deps | CI exact tree | **gate passed** (accepted braces exception only) |
| Image scan | CI exact tree | **5/5 images 0 fixable HIGH/CRITICAL** |
| Backend suite in certified image | local, Py 3.12 / exact pins, archive mounted | **6748 passed, 14 failed, 708 skipped, 4 xfailed**. All 14 failures have one cause, `FileNotFoundError: 'git'`: the runtime image has no git binary and these tests shell out to `git ls-files` / `git cat-file` (13 in `test_ci_static_gates.py`, 1 in `test_legacy_pipeline_equivalence.py`). The same 14 pass in CI on the identical tree. Environment artefact of running tests in a runtime image, not a release defect. |
| Real-Mongo in certified image | local, `mongo:8.0` rs0 | **567 passed, 0 skipped; all 26 modules ran** (1625.6 s) |
| Trivy on certified images | local `aquasec/trivy:0.70.0`, CI policy flags | **backend 0, client 0 fixable HIGH/CRITICAL**; `scripts/check_image_scan_freshness.py` = **GO** for both exact ids (reports created 2026-10-11 10:44/10:45 UTC, valid 24 h) |

No failure was classified as baseline. The only failures seen (14, in-image) have a proven environmental mechanism and pass on the same tree in CI.

During certification the local Docker Desktop engine crashed once (4 GB VM, staging stack and test suite running together); it was restarted, the certified image ids were unchanged, and the suites were re-run with nothing else running.

## 8. Real-PDF acceptance

Both audit PDFs were used read-only from the operator's Downloads folder, never copied into
any repository, uploaded through the normal queued route (`POST /api/documents`,
`ocrEnabled=1`) as `orgadmin` in `org1/proj1`, with the API allowlist set to `org1` (the
P1-1 wiring). Customer text never reached evidence: only lengths, page numbers, checksums
and named-fact booleans.

### GLM-SAM-KNPCC-05-UPMRC-OL-2022-2326.pdf

| Field | Value |
|---|---|
| PIPELINE VERSION | `unified_v1` (job and document) |
| PAGE METHODS | 1 OCR, 2 OCR, 3 native, 4 native, 5 OCR |
| REVIEW PAGES | none |
| FAILED PAGES | none |
| CANONICAL CHECKSUM | `1410b434443ab7dcebde53ac0055c4e090ccc5ef521f0e2254b32574bbabd0a4` (re-derived from the served text) |
| CANONICAL PAGE COUNT | 5 (page map 5, head `page_numbers` = expected = [1..5]) |
| CANONICAL REVISION | 1 |
| FACTS RECOVERED | **8/8** (reference, subject, D-wall, clause 8.4.1, clause 17.1, attempt table, 23.11.2022, 01.12.2022) |
| Duplicate page rows | 0 |
| Source checksum | head `source_sha256` equals the document's `sha256` |
| Status | `partially_processed` — indexing failed because staging has no embedding provider (truthful; expected) |
| Duration | 90.4 s on the local Docker VM (4 vCPU-class, 3.8 GB) |
| Legacy control (same PDF, `legacy_v0`) | 150 chars, 0/8 facts — the scanned pages are lost on the legacy path (KL-9) |

### c21f238d-ed25-4a95-a8ca-f43f7b194eca.pdf (claim)

| Field | Value |
|---|---|
| PIPELINE VERSION | `unified_v1` |
| PAGE METHODS | 1 OCR; 2–6 native |
| REVIEW PAGES | **[5]** — message "1 page(s) need review (failed the extraction quality check: page(s) 5)"; `quality_review_pages: [5]` |
| FAILED PAGES | none |
| Quality verdicts | p1–p4 `not_checkable` (page 4 truthful: not claimed checkable), p5 `fail`, p6 `pass` (arithmetic) |
| Numeric integrity | `19,292,171` (pages 3, 5), `689,012` (pages 3, 5), `950,000` (page 6) — all whole |
| CANONICAL CHECKSUM | `bbe498d1f92b8dba2fdf43d5ef25285c6485147261c3c754a0727dbfc5912870` |
| CANONICAL PAGE COUNT | 6 |
| Raw vs corrected | deterministic repairs on pages 2, 5, 6; `original_text` retained and differs from the canonical page text |
| FACTS RECOVERED | **6/6** named facts + 3/3 numerics |
| Duplicate page rows | 0 |
| Status | `human_review_required`, vectors `deferred` (KL-4) |
| Duration | 28.4 s (second copy 19.4 s) |
| Legacy control (org2, `legacy_v0`) | 5,228 chars; page-1 reference, claim amount and referenced letter missing (KL-9) |

## 9. Security / RBAC acceptance (canonical evidence)

Run inside the certified backend image against the staging replica set, with real
`PolicyService`/`PermissionService`, principals resolved by `principal_from_access_token`
from real `/api/login` tokens, **Qdrant stopped**, and every outbound HTTP(S) request
blackholed (`HTTP(S)_PROXY=127.0.0.1:9`). Destructive probes ran on clones only.
Script: `C:\SaaS-release-cert-2533b19\cert_canonical.py`. **30/30 passed**, run twice.

| # | Property | Result |
|---|---|---|
| 1 | Full canonical text without Qdrant | PASS (Qdrant container exited) |
| 2 | Without embedding provider | PASS (none configured; outbound blackholed) |
| 3 | Without LLM | PASS (same) |
| 4 | Raw and corrected values auditable | PASS (claim pages 2, 5, 6) |
| 5 | Page spans map to source pages | PASS (`text[start:end] == page_text(n)`, contiguous, every span has its row) |
| 6 | Canonical checksum verifies | PASS (sha256 of served text == head == manifest) |
| 7 | Scoped selection mandatory | PASS (no selection → 400) |
| 8 | Foreign project denied | PASS (403; and 403 when it selects its own project) |
| 9 | Foreign organisation denied | PASS (403) |
| — | Super Admin bounded by explicit selection | PASS (403 with org2 selection; positive control with org1) |
| 10 | Deleted document → 404 | PASS (project member and Super Admin) |
| 11 | Retained evidence inaccessible after soft delete | PASS (rows and head retained, read refused) |
| 12 | Stale same-document publisher cannot overwrite | PASS (`CanonicalHeadConflictError`; newer revision 2 kept) |
| 13 | Inconsistent head/rows fail closed | PASS (tampered row, missing row, head scope ≠ document scope → `CanonicalEvidenceInconsistentError`) |
| — | Legacy document | PASS (`not_built`, legacy text offered, no error) |

## 10. Status / UI acceptance

| Case | Server state observed | Client predicate (`utils/processingStatus.ts`) | Result |
|---|---|---|---|
| SUCCESS | `completed` (not produced in staging: no embedding provider) | terminal | PASS (predicate; CI vitest) |
| HUMAN REVIEW | `human_review_required`, pages `[5]` in message and error | terminal | PASS |
| INDEXING FAILURE | `partially_processed`, `terminal: true`, stages embeddings/vector_sync | terminal (stage ≠ `awaiting_page_resume`) | PASS |
| STORED ONLY | not produced | terminal | PASS (predicate) |
| FAILED | not produced | terminal | PASS (predicate) |
| RETRYING | not produced | non-terminal → keeps polling | PASS (predicate) |
| PROCESSING / `metadata_extracted` | observed transiently | non-terminal → keeps polling | PASS |
| PARTIALLY_PROCESSED awaiting page resume | not produced | non-terminal | PASS (predicate) |
| PARTIALLY_PROCESSED settled | observed | terminal | PASS |
| No endless polling | — | bulk tracker capped (~10 min); viewer stops on terminal | PASS with KL-11 |
| Viewer shows job status | — | **`GET /api/documents/{id}/processing-status` does not exist** (no route at release or in history); the viewer catches the 404 and sets `not_queued`, which wins over `document.processing_status` | **P2-1** |

The UI render itself was not exercised in a browser: the staging seed user must accept the
Security Terms first, and that acceptance was not given. Rows marked "predicate" are proven
by the shipped predicate and its CI unit tests, not by a rendered page.

## 11. Staging topology

Local, disposable, isolated (production host was not used: its same-host model requires
production to be stopped).

| Item | Value |
|---|---|
| Harness | `C:\SaaS-release-cert-2533b19` (`docker-compose.cert.yml`, `cert.sh`, `runner.py`, `cert_canonical.py`, `gateway.mjs`) |
| Compose project | `ccert-2533b19`, network `ccert-2533b19_default` |
| Containers | backend, contract-worker, document-worker (`legacy_v0`), document-worker-canary (`unified_v1`), client, gateway, mongo (rs0), redis (password), clamav, qdrant |
| Volumes | `ccert-2533b19_mongo_data`, `ccert-2533b19_qdrant_data`, `ccert-2533b19_uploads` |
| Ports (all loopback) | API `127.0.0.1:18095`, client `127.0.0.1:18096`, gateway `127.0.0.1:18097`, Mongo `127.0.0.1:27037` |
| Isolation proof | `H:isolation.txt`: 0 production identifiers in any container environment; every published port on 127.0.0.1; Mongo URI host `mongo` only |
| Secrets | per-run, generated into `.env.cert` (mode 600), never printed; dummy AWS/OpenAI/SMTP |
| Tenants | `org1/proj1` (canary), `org1/proj1b` (foreign project), `org2/proj2` (non-canary), `dms_enterprise` subscriptions |
| Code source | `git archive 2533b19`; content-identical to the release (0 file diffs against the commit's index; the raw tree hash differs only in exec bits lost on Windows) |
| Test Mongo | separate `ccert-tests-mongo` (standalone) and `ccert-tests-mongors` (rs0) on network `ccert-tests` |

Deviations from production, stated: `ENVIRONMENT=development` (production's startup config
gate needs real secrets); no FalkorDB (`FALKORDB_ENABLED=false`, as in CI); no httpd (a
path-only node gateway); no S3 (local uploads volume); no embedding provider; Docker VM of
3.8 GB RAM, so timings are not production baselines.

## 12. Immutable image digests

Built on 2026-10-11 from the exact archive with `docker build --pull --no-cache
--build-arg RELEASE_SHA=2533b19d443fa972186e04a7e67113597d54041d`. Revision label =
release SHA on both.

| Service | Image id (`docker image inspect .Id`) |
|---|---|
| BACKEND | `sha256:4b7b4aae06a0c66e88cc0b70c638c018eccccad0bb5d4f24d8a20aa6c9bb35f9` |
| CONTRACT WORKER | same image as backend |
| DOCUMENT WORKER | same image as backend |
| CANARY DOCUMENT WORKER | same image as backend |
| FRONTEND | `sha256:11c904e97663d4421dc7437e1e35d10a6472ebf7515e61120eaf02af72c373ee` |

These are the images staging certified. To deploy **these** bytes, move them with
`docker save | ssh contraclaim docker load` (the id survives a save/load) and retag; do not
rebuild on the host.

**Owner decision (OD-2):** `docs/IMAGE_FRESHNESS_POLICY.md` (owner decision, 2026-09-13)
requires every release image to be rebuilt `--pull --no-cache` and re-scanned before each
window, with Trivy evidence ≤ 24 h old about the exact image id. That conflicts with "never
rebuild after certification" unless the window starts within 24 h of this build. Recommended:
deploy these images and re-scan the **same ids** within 24 h of the window; if a new fixable
HIGH/CRITICAL appears, the window is NO-GO and a rebuild + re-certification follows.

## 13. Deployment procedure — Stage A (prepared, NOT executed)

Code/images only. `unified_v1` globally OFF, allowlist empty, canary worker 0 replicas.
Compose set: `-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml`, project
`contraclaim`. `$DC` below means that command.

1. **Backup (hard gate).** `backup_status.py` is currently failed. Take a full backup
   (`scripts/production_backup.sh`), validate it with `scripts/validate_backup_archive.py`,
   and confirm `backup_status.py` passes. No backup, no window.
2. **Record before-state:** `git rev-parse HEAD HEAD^{tree}` (expect `1e89bf3` / `c133968`),
   `docker inspect -f '{{.Image}}'` of every app container (expect §2 ids), copy of
   `current.json`, and the counts in §2 (jobs by status/version, page rows 0, heads 0).
3. **Rollback images:** tag the running ids explicitly:
   `docker tag sha256:a82a6b31… contraclaim-rollback-backend:1e89bf3` and
   `docker tag sha256:cd95b7e1… contraclaim-rollback-client:1e89bf3`; verify with
   `docker image inspect`.
4. **Ship the certified images:** `docker save contraclaim-cert-backend:2533b19
   contraclaim-cert-client:2533b19 | ssh contraclaim docker load`; verify the two ids equal
   §12; re-scan them (OD-2); `release_manifest.py certify` with those reports.
5. **Code:** the host checkout is `/opt/contraclaim-dms` (git root), remote `origin` =
   `ManishPandey21/contraclaim-dms`, branch `release/contraclaim-rc1` with **no upstream configured**
   (its `origin/release/contraclaim-rc1` was last fetched at `1e89bf3`). Run `git fetch origin` then
   `git merge --ff-only origin/release/contraclaim-rc1`; confirm `2533b19` / `e3804d6`; `PYTHONDONTWRITEBYTECODE=1`
   and clear `__pycache__` under `backend/` before any host script.
6. **Maintenance:** announce; expected outage is the backend/worker recreate (minutes).
   Run `scripts/release_preflight.sh` and `check_maintenance_time_budget.py` as the runbook
   requires.
7. **Retag** the certified ids as the compose image names (`contraclaim-backend:latest`,
   `contraclaim-contract-worker:latest`, `contraclaim-document-worker:latest`,
   `contraclaim-document-worker-canary:latest`, `contraclaim-client:latest`).
8. **Migrations:** none new in this release (catalogue unchanged since `1e89bf3`). Still run
   the dry-run inside the new backend container:
   `$DC exec -T backend python -m rbac_backend.scripts.migrate_database --fail-on-warning`.
9. **Stop/start order:** `$DC up -d --no-deps --no-build backend` → health → `contract-worker`
   → `document-worker` → `client`. Never a plain `up` (it would start stale services through
   `depends_on`). Leave `document-worker-canary` at 0 replicas.
10. **API health:** `/health` and `/health/ready` 200; release identity = `2533b19` in
    `RELEASE_SHA` env and image label.
11. **Worker health:** exactly one `START_DOCUMENT_EXTRACTION_WORKERS=true` (document-worker),
    `DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0`, contract-worker the only
    `RUN_SCHEDULER=true`; restart counts stable.
12. **Queue health:** no job stuck `queued` > 5 min; dead letters unchanged (4).
13. **Frontend:** fetch the hashed asset from `contraclaim.com` and confirm a new chunk name.
14. **Legacy smoke (§14).**
15. **Canonical dormant:** `unified_v1` job count 0; page rows 0; heads 0.
16. **Data parity:** counts from step 2 unchanged except the smoke upload.
17. `scripts/post_deploy_verify.sh` (with `release_manifest.py verify`), then `promote`.

## 14. Production smoke tests (Stage A)

- Log in as an owner-designated real user; `GET /api/me` 200.
- Upload one owner-approved, non-sensitive PDF through the UI in an owner-chosen project:
  job `legacy_v0`, claimed by `document-worker`, terminal status within the normal time.
- Open it in the document viewer; download/preview the file.
- Search for a term from it where search is currently expected to work.
- Open an existing contract and an existing correspondence record; both render.
- Confirm no `unified_v1` job, no page row, no head was created.
- Confirm a project-tier user still sees 200 bounded to assignments (CL-4A behaviour) and a
  foreign-org record still 403/404 (security hotfixes #38–#43 now live).

## 15. Canary procedure (after P1-1 is fixed and Stage A is healthy)

1. Owner names one organisation (and project). Record its id.
2. Set `UNIFIED_EXTRACTION_CANARY_ORG_IDS=<org>` for **backend** and both workers;
   `DOCUMENT_WORKER_CANARY_REPLICAS=1`; `DOCUMENT_WORKER_PIPELINE_VERSIONS=legacy_v0`.
3. Retag the certified backend id as `contraclaim-document-worker-canary:latest`; then
   `$DC up -d --no-deps --no-build backend document-worker document-worker-canary`.
4. Verify `printenv UNIFIED_EXTRACTION_CANARY_ORG_IDS` in the **backend** container.
5. Upload controlled representative documents through the normal UI route.
6. Verify: canary org jobs `unified_v1`; another org's upload `legacy_v0`; canary worker
   claims only `unified_v1`; page rows and heads exist only for the canary org; each head
   verifies through `get_document_canonical_evidence`.
7. Rollback drill (§16 Level 1) once, then re-enable.

## 16. Observability thresholds and rollback

### Rollout gates (canary)

No production baseline for unified exists. Staging numbers are from a 3.8 GB Docker VM and
are ceilings for comparison, not targets.

| Signal | Gate | Basis |
|---|---|---|
| Upload success rate | 100 % of canary uploads accepted (excluding true duplicates / AV) | staging 7/7 (+2 correct duplicate refusals) |
| Extraction success (reaches a truthful terminal state) | 100 %; any `completed` with an unextracted page = immediate rollback | staging 7/7 |
| Human-review rate | record, do not gate; investigate if > 50 % of representative clean letters | staging 2/4 unified documents (both claim copies, page 5, KL-3) |
| OCR seconds/page | investigate > 60 s/OCR page; rollback > 120 s | staging ≈ 29 s/OCR page (GLM 90 s / 3 OCR pages) |
| Document duration | investigate > 5 min for ≤ 10 pages | staging 19–90 s |
| CPU / RAM (canary worker) | no OOM kill; host available RAM stays > 4 GB | staging worker idle ~300 MB; peak not measured |
| Retry rate | any job with attempts > 1 is investigated | staging 0 |
| Dead-letter rate | 0 new dead letters from the canary org | staging 0 |
| Canonical publish failures | 0 (`CanonicalEvidenceInconsistentError`, `CanonicalHeadConflictError` in logs) | staging 0 |
| Status inconsistencies | 0 documents whose status contradicts the job | staging 0 |
| Embedding failures / vector sync failures | equal to the legacy baseline (both pipelines share the indexer) | not measurable in staging (no provider) |
| Queue depth / age | no `unified_v1` job queued > 10 min | staging 0 |

### Level 1 — feature rollback

Trigger: any gate above breached, or owner request. Clear the allowlist on **backend and
workers**, recreate backend first (no new unified jobs), then scale the canary worker to 0
only after in-flight unified jobs settle (or decide to leave them for the unrestricted
worker). No image change. New uploads are `legacy_v0`; existing unified jobs keep their
recorded version. Proven in staging (p11) on the API side.

### Level 2 — release rollback

Trigger: Stage A health failure, a regression in legacy behaviour, a security regression, or
a Level 1 rollback that does not restore service. Retag `contraclaim-rollback-backend:1e89bf3`
/ `contraclaim-rollback-client:1e89bf3` to the compose names, `git checkout 1e89bf3` on the
host, recreate backend → workers → client with `--no-deps --no-build`, re-promote the
previous manifest, run `post_deploy_verify.sh`.

Data compatibility: no migration in this release, so nothing to downgrade. Page rows, heads
and `pipeline_version` on jobs are additive; `1e89bf3` ignores page rows/heads, and its claim
filter treats `legacy_v0` and missing versions as legacy. A `unified_v1` job still queued at
rollback is never claimed by `1e89bf3`'s legacy-restricted worker — settle or explicitly
re-queue such jobs before Level 2. Canonical evidence written during the canary is retained
and stays inaccessible except through the authorised read path; **never delete evidence to
roll back code.**

## 17. Release-blocker list

| Id | Class | Finding | Failure scenario | Recommendation |
|---|---|---|---|---|
| P1-1 | **P1 (blocks canary only)** | `docker-compose.prod.yml` passes `UNIFIED_EXTRACTION_ENABLED` / `UNIFIED_EXTRACTION_CANARY_ORG_IDS` to `document-worker` and `document-worker-canary` only. `pipeline_version` is decided in the **API** process (`routers/documents.py` → `DocumentService.queue_document_processing` → `resolve_pipeline_version`). | Operator follows the runbook, allowlists the canary org → every canary upload is still `legacy_v0`; the canary silently tests nothing. Reproduced in staging (p7: org1 allowlisted on workers, job `legacy_v0`). Rollback steps that edit `.env` likewise have no effect on routing. | Add both variables to the `backend` service environment (compose-only change; certified images unaffected because `backend/` is the build context), plus a static test that the API service receives every routing variable. New release SHA for the compose file; images stay `4b7b4aae…` / `11c904e9…`. |
| P1-2 | ~~P1~~ **Withdrawn** | Wrong backup root was checked; nightly backups exist. Fresh window backup `20261011-211316` taken: `backup_status.py` ok, 6/6 checksums OK, offsite synced, Mongo restore drill in an isolated container = exact parity (8 key collections, 175 collections). | — | — |
| P0-F1 | **P0 (pre-existing, production)** | Two FalkorDB engines run at once: `contraclaim-falkordb-1` (compose service, preserved original, de249ee3df20) and `contraclaim-falkordb-cutover` (bd67a8ba439c). Both mount `contraclaim_falkordb_data` at `/data`, both carry the `falkordb` alias, and the backend's `falkordb` hostname resolves to both IPs. Both have run since 2026-10-04 11:48Z (the 1e89bf3 deploy started the original through `depends_on`). | Each graph write (a Letter node on correspondence ingest, including a Stage A smoke upload) lands on one engine at random; each engine persists its own in-memory state to the same `dump.rdb`/AOF, so a restart loads whichever wrote last, and graph data can silently disappear. Counts match (156 nodes / 220 relationships / same labels, 0 unsaved changes), but **content does not**: a full-content fingerprint (2026-10-11, later the same day) shows 2 Letter nodes differ. Internal ids 151/152 are full Letters created 2026-08-24 on the original, and stubs created 2026-09-25 and 2026-10-05 on the cutover. The original holds no node or relationship newer than 2026-08-24, so it is a stale pre-cutover snapshot; every later write is on the cutover. Reads that DNS sends to the original (most connections) have returned stale graph state since 2026-10-04. The earlier "no divergence" statement compared counts only and was wrong. Backup flush cannot be proven (exit 3). | Owner decision: stop (do not remove) `contraclaim-falkordb-1` so only the cutover engine answers, re-run the backup until the flush is proven, then continue the window. |
| P2-1 | P2, pre-existing | `GET /api/documents/{id}/processing-status` does not exist; the viewer's fallback `not_queued` overrides the document's real status. | A document in `human_review_required` / `partially_processed` is shown in the viewer as not queued; the review page list never appears there. Same in production today. | Add the route (or read `document.processing_status` + job stage) in a follow-up release. |
| P2-2 | P2 | Viewer polls a non-terminal status every 4 s with no cap. | A stuck job keeps an open viewer polling indefinitely. | Cap or back off in a follow-up. |
| P2-3 | P2, ops | Production Redis password was printed into this session's transcript by a read-only env listing whose redaction matched variable names, not URL userinfo. | Credential exposure to the session log. | Owner decides on rotation. |
| P2-4 | P2, ops | 2026-10-04 rollback tags `contraclaim-rollback-*:fe728b2-pre1e89bf3` no longer exist on the host. | Rollback beyond `1e89bf3` would need a rebuild. | Accept; Stage A step 3 creates the `1e89bf3` rollback tags. |
| P2-5 | P2 | An antivirus **outage** is answered `400 "Antivirus scan rejected this file: Antivirus offline"`. | The user is told their file was rejected when the scanner is down; a 400 is not retryable to clients. Fail-closed itself holds. | Answer 503 with an outage message in a follow-up. |
| — | KNOWN LIMITATION | KL-1 … KL-12 (§5) | — | No action for this release. |
| — | OUT OF SCOPE | §4 | — | — |

**P0 blockers: none. P1 blockers: P1-1 (canary), P1-2 (Stage A, operational).**

## 18. Owner approval points

| # | Decision |
|---|---|
| OD-1 | Approve Stage A (code deploy, unified OFF) for a named window. |
| OD-2 | Image freshness: deploy the certified ids with a same-id re-scan ≤ 24 h before the window (recommended), or rebuild + re-certify. |
| OD-3 | Approve the P1-1 compose fix (one small PR to `release/contraclaim-rc1`) before any canary. |
| OD-4 | Name the canary organisation/project and the representative documents. |
| OD-5 | Redis credential rotation (P2-3). |
| OD-6 | Whether this certification document is merged into the release branch (it changes the release SHA but not the images) or kept on its own branch until after deployment. |
| OD-7 | Each rollout expansion after the observation window (§19). |

## 19. Observation window

After the canary passes: at least 24 hours **and** at least 20 representative canary
uploads (if volume is lower, wait for the document count, not the clock), covering native,
scanned and mixed PDFs. No automatic expansion; each expansion is a separate owner approval.

## 20. Post-deploy release record (template)

```text
RELEASE SHA            = 2533b19d443fa972186e04a7e67113597d54041d
RELEASE TREE           = e3804d6dd9c2c65fd34e358d3e5f7e2a4efac49b
CERTIFIED IMAGE DIGESTS= backend/workers sha256:4b7b4aae06a0c66e88cc0b70c638c018eccccad0bb5d4f24d8a20aa6c9bb35f9
                         client          sha256:11c904e97663d4421dc7437e1e35d10a6472ebf7515e61120eaf02af72c373ee
PREVIOUS PRODUCTION SHA= 1e89bf3036692e2cfd127a29102d6c45338ceacf
PREVIOUS IMAGE DIGESTS = backend/workers sha256:a82a6b31336b16a4f2eb02a124bbb36e2a47c736a82a4e0094cd7f1b2b49b35c
                         client          sha256:cd95b7e12cd21f7b8de4445b0779a0825a32a21dc2120d76acd54e63f2cb4d6b
DEPLOYMENT DATE/TIME   =
DEPLOYED BY            =
BACKUP (path, sha256, validated) =
GLOBAL UNIFIED ENABLED = NO
CANARY ORG/PROJECT     =
CANARY ENABLED AT      =
SMOKE TEST             =
LEGACY TEST            =
CANARY TEST            =
ROLLBACK TEST          =
ROLLBACK REQUIRED      =
OPEN ISSUES            = P1-1 (until fixed), P2-1, P2-2, P2-3, P2-4, P2-5, KL-1..KL-12
PRODUCTION HEALTH      =
NEXT EXPANSION DECISION=
```
