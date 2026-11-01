# CL-4A — System-wide active project scope for the core DMS modules (2026-09-23)

Branch `feat/cl4a-core-active-project-scope` (worktree `C:\SaaS-cl4a`), stacked on CL-3B
`feat/cl3b-programme-chronology-links` @ `eac8fdb` (PR contraclaim-dms#29, itself on CL-3A #27,
CL-2 #26 and CL-1 #23). Nothing in #23/#26/#27/#29 was rewritten.

## 1. Provenance (re-fetched from both remotes, 2026-09-23)

`origin` and `contraclaim` both resolve `feat/cl3b-programme-chronology-links` to
`eac8fdbe7956d559165fbed87207ff62a2f897d1`. PR #29: OPEN, DRAFT, unmerged, base
`feat/cl3a-hindrance-scope-integration`, 5/5 exact-head checks SUCCESS (secret scan, backend lint
and tests, frontend lint/tests/build, dependency scan, docker build and image scan).

## 2. Phase 1 — scope audit matrix (before any code change)

Audited at `eac8fdb`: every `@router.*` decorator in `documents.py`, `document_relationships.py`,
`search.py`, `contracts.py`, `contract_master.py`, `contract_master_api.py` (+ `contract_clauses.py`,
`contract_appraisal.py` briefly), `claims.py`, `ipc_bills.py`, `insurance.py`,
`bank_guarantees.py`, `key_dates.py`, `dashboard.py`, plus the count/summary routes elsewhere.

### 2.0 Facts common to every audited route (stated once)

* **Selected-project aware: no** — except `document_relationships.py` (bound target types only).
  No route in the other routers depends on `core/tenant_context.py`, so `X-Org-Id` / `X-Proj-Id`
  were ignored and "no selection" behaved exactly like any selection.
* **Superadmin:** `PolicyService.authorize` allows before any scope check
  (`services/policy_service.py:138`); `build_scope_query` keeps only the filters passed
  (`core/security.py:616`). A superadmin list with no filter returns every tenant.
* **Direct id:** every register's `_load` fetched by `_id` alone, then
  `authorize_document(user, perm, record)` on the record's own org/project — a *membership* test.
  A member of A and B could read and write A while B was selected.
* **Lists:** optional `?organization_id=` / `?project_id=` filters chosen by the client, passed to
  `authorize` and `build_scope_query`. With no filter an org role sees every project of the org.
* **Transport:** the axios `api` client (`createHttpClient`) sent the selection headers;
  `authenticatedFetch` and the global `fetch` wrapper (`client/src/services/http.ts`) did **not** —
  and most Documents / Dashboard / Document Viewer calls use them. Enforcing a route whose caller
  uses them would answer 400 to a correct UI. (Fixed first, §3.1.)
* **Frontend cache:** `MainLayout` keys the routed page by `org:project`, so a navbar switch
  remounts the page and refetches. Stale rows survived because the refetch was not bounded by the
  selection (register pages kept their own `projectFilter`, default "all"), not because of a cache.

Legend: **N** = NEEDS ACTIVE-SCOPE ENFORCEMENT, **C** = ALREADY CORRECT, **G** = INTENTIONALLY
GLOBAL, **A** = AMBIGUOUS — OWNER DECISION (the CL-4A default applied is stated in §3.2).
"Hold" = `require_selection()` before the load, `require_record()` after it (400 / 403).
"Narrow" = list filters pinned to the selection; a filter may narrow, never leave it.

### 2.1 Documents — `routers/documents.py`

A Document carries `organization_id` (required) and `project_id` (`""` = organisation-level).
`EDA` = `_ensure_document_access` (raw load + `authorize_document`), `GDB` =
`document_service.get_document_by_id` (hides deleted).

| # | Route | Method | Line | Current check | Caller (client) | Deep link | Class | CL-4A |
|---|---|---|---|---|---|---|---|---|
| D1 | /documents/vector-search | GET | 2257 | `authorize(view)` + per-hit `authorize_document` | none | — | N | narrow |
| D2 | /documents/export | GET | 2280 | `authorize(download)` + `build_document_query` | DocumentsPage (authenticatedFetch) | — | N | narrow |
| D3 | /documents | POST | 2540 | `authorize(upload, form org/project)` | UploadPage (authenticatedFetch) | — | N | body project must be the selection |
| D4 | /documents/{id} | GET | 2596 | EDA view + GDB | DocumentViewer, ContractViewer, Share, Letter*, Reference* | `/documentviewer/:id`, `/documents/summary/:id`, `/reference/:id`, `/share/:id` | N | hold (A: org-level) |
| D5 | /documents/{id}/process | POST | 2612 | EDA edit_metadata | DocumentViewerPage | viewer | N | hold |
| D6 | /internal/documents/{id}/process | POST | 2624 | LangGraph service token | backend only | — | G | — |
| D7 | /documents/download-all | GET | 2635 | project archive `authorize(bulk_download)` | FolderStructurePage (axios) | — | N | `require_project(project_id)`; route is shadowed by D4 (debt) |
| D8 | /documents/{id}/download | GET | 2679 | GDB + `authorize_document(download)` + EDA | DocumentHeader, DocumentViewer (authenticatedFetch), Folder (axios) | viewer | N | hold |
| D9 | /document-search | GET | 2801 | `authorize(view)` + `build_document_query`, linkable only | EntityDocumentLinks, LinkedDocumentsPicker, Insurance, Chronology, Tasks (axios) | — | N | narrow |
| D10 | /documents | GET | 2802 | same handler | DocumentsPage (authenticatedFetch, URL `?project_id`) | `/documents` | N | narrow |
| D11 | /documents/{id} | PUT | 2852 | GDB + `authorize_document(edit_metadata)` + EDA | MetadataEditor | viewer | N | hold |
| D12 | /documents/{id}/summary-metadata | PATCH | 2881 | same | LetterSummaryPage | `/documents/summary/:id` | N | hold |
| D13 | /documents/{id} | DELETE | 2920 | step-up + `authorize_document(delete)` | DocumentsPage | — | N | hold |
| D14 | /documents/{id}/audit-events | GET | 2946 | EDA view | none | — | N | hold |
| D15 | /documents/{id}/enclosures | GET | 2968 | EDA view | EnclosuresPanel, Share | viewer | N | hold |
| D16 | /documents/{id}/enclosures | POST | 2980 | EDA edit_metadata | EnclosuresPanel | viewer | N | hold |
| D17 | /documents/{id}/enclosures/{eid} | DELETE | 2993 | EDA edit_metadata | EnclosuresPanel | viewer | N | hold |
| D18 | /documents/{id}/references | GET | 3006 | EDA view | Viewer, ReferencePage, Share | `/reference/:id` | N | hold |
| D19 | /documents/{id}/references | POST | 3018 | EDA link_reference + target view | ReferencesPanel, ReferencePage | viewer | N | hold source |
| D20 | /documents/{id}/references/{rid} | DELETE | 3031 | EDA link_reference | ReferencesPanel | viewer | N | hold |
| D21 | /documents/{id}/sync-references | POST | 3044 | EDA link_reference | ReferencePage | `/reference/:id` | N | hold |
| D22 | /documents/link | POST | 3056 | EDA source + EDA target | none | — | A | hold source **and** target |
| D23 | /documents/{id}/linked | GET | 3069 | EDA view | ReferencesPanel | viewer | N | hold |
| D24 | /documents/{id}/comments | GET | 3083 | GDB + EDA view | LetterSummaryPage | summary | N | hold |
| D25 | /documents/{id}/comments | POST | 3102 | GDB + EDA comment.add | LetterSummaryPage | summary | N | hold |
| D26 | /documents/bulk-upload | POST | 3131 | `authorize(upload, form)` | UploadPage | — | N | body project must be the selection |
| D27 | /documents/bulk-upload/{job_id}/status | GET | 3191 | caller's own scope only; job not bound to caller | UploadPage | — | A | unchanged; recorded debt (job ownership) |
| D28 | /documents/bulk-upload/template | GET | 3209 | static CSV | UploadPage | — | G | — |
| D29 | /documents/{id}/request-draft | POST | 3236 | EDA view + `drafting.request.create` | DocumentsPage, LetterWorkflowPage | — | N | hold |

### 2.2 Relationship routes — `routers/document_relationships.py`

All routes already depend on `requested_scope`; before CL-4A only `variation`, `delay_event`,
`programme_milestone`, `chronology_event` were bound (`active_scope_enforced`).

| # | Route | Method | Line | Class before | CL-4A |
|---|---|---|---|---|---|
| R1 | /entities/{t}/{id}/document-links:batch | POST | 114 | C (bound types) | binds every core target via the adapter flag |
| R2 | /entities/{t}/{id}/document-links | GET | 139 | C | same |
| R3 | …/document-links:freeze | POST | 156 | C | same |
| R4 | /document-links/{id}:remove | POST | 177 | C | same |
| R5 | /document-links/{id} | GET | 199 | C | same |
| R6 | /document-links/{id}/history | GET | 215 | C | same |
| R7 | /documents/{id}/entity-links (reverse) | GET | 244 | A (unbound rows never filtered) | every core target's rows filtered by the selection |
| R8 | /documents/{id}/link-targets (Link to Record) | GET | 260 | A | every core target needs a selection, offers nothing outside it |
| R9 | /documents/{id}/link-target-types | GET | 295 | A | core types offered only while the selection is the Document's project |
| R11 | /documents/{id}/link-dependencies | GET | 326 | A | as R7 |

### 2.3 Search — `routers/search.py`

| # | Route | Method | Line | Current check | Caller | Class | CL-4A |
|---|---|---|---|---|---|---|---|
| S1 | /search/documents | GET | 19 | `authorize(view)` + ScopeService org/project `$in` | EnhancedDocumentsPage (axios) | N | narrow |
| S2 | /search/suggestions | GET | 258 | same role scoping | EnhancedDocumentsPage | N | narrow |
| S3 | /search/popular | GET | 321 | login; static | none | G | — |
| S4 | /search/track | POST | 352 | login; telemetry | EnhancedDocumentsPage | G | — |
| S5 | /search/analytics | GET | 379 | superadmin only | none | G | — |
| S6 | /search/semantic | POST | 465 | delegates to S1 by direct call | RetrievalConsolePage | N | narrow (via S1) |

### 2.4 Contract Documents

`contracts.py` / `contract_clauses.py` use the `documents` collection (`uploadType=contract`,
`project_id` may be `""`); `contract_master.py` is the `contract_master` collection;
`contract_master_api.py` is the v1 `contract_documents` collection (`scope_level` +
`scope_project_id` in the model / CL-1 adapter, but promotion writes `project_id`).

| Route | Method | Line | Current check | Caller | Class | CL-4A |
|---|---|---|---|---|---|---|
| /contracts/upload-session | POST | 276 | `_authorize_contract_scope(upload)` + `_resolve_scope` | ContractsUploadPage | N/A (org-level upload) | body project must be the selection when one is selected |
| /contracts/upload-multipart | POST | 300 | session + upload | ContractsPage, ContractsUploadPage | N | same |
| /contracts/upload-chunk | POST | 482 | session + upload | same | N | same |
| /contracts/status | GET | 731 | job `_authorize_scope` | polling | N | hold job |
| /contracts/{id}/ocr/retry | POST | 752 | `get_contract_document` + edit_metadata | none | N | hold |
| /contracts/{id}/reindex | POST | 824 | same | ContractViewerPage | N | hold |
| /contracts/list | GET | 901 | `authorize(view)` + `build_scope_query` | Appraisal, Viewer, Upload, Search, QA | N | narrow |
| /contracts/search | POST | 924 | body org/project | ContractsPage, ContractsSearchPage | N | narrow |
| /contracts/{id}/download | GET | 944 | `authorize_document(download)` | Upload, Viewer (authenticatedFetch) | N | hold |
| /contracts/master | GET | 41 | `authorize(view)` + `build_scope_query` | ContractMasterPage, BG/IPC/Variation forms | N | narrow |
| /contracts/master | POST | 58 | `authorize(manage, body)` | ContractMasterPage | N | body project must be the selection |
| /contracts/master/{id} | GET/PUT | 73/83 | `_load` + `authorize_document` | ContractMasterPage | N | hold |
| /contracts/master/{id}/revise-completion | POST | 96 | `_load(manage)` | ContractMasterPage | N | hold |
| /contracts/master/{id}/bg-required-dates | GET | 118 | `_load(view)` | ContractMasterPage | N | hold |
| /contracts/{id}/clauses/index, /contracts/{id}/clauses | POST/GET | 68/104 | `get_contract_document` + clause perms | ClauseIndexTab | N | hold the contract document |
| /contracts/clauses/{uid}… (patch/regenerate/split/merge) | * | 121–171 | clause row + PolicyService | ClauseIndexTab | N | recorded CL-4B (clause-uid load path) |
| /contract-master/* (v1, 13 routes) | * | 284–880 | `get_policy()` imports the non-existent `core.policy` → every route 500s | ContractMasterWorkspacePage (hard-coded ids, raw fetch) | A | **not changed**; defect handed off (separate task), recorded CL-4B |
| contract_appraisal.py (≈25 routes) | * | 55–463 | `authorize` / `authorize_document` | ContractAppraisalPage (own `projectId`) | A | recorded CL-4B (report subsystem) |

The CL-1 `contract_document` adapter reads `scope_project_id` only when `scope_level=="project"`
(`entity_adapter_registry.py:1692`); promotion writes `project_id`. Recorded, not changed.

### 2.5 Claims — `routers/claims.py` (`claims`: `organization_id`, **optional** `project_id`)

| Route | Method | Line | Current check | Caller | Deep link | Class | CL-4A |
|---|---|---|---|---|---|---|---|
| /claims | GET | 68 | `authorize(view, org=query)` — no own-org fallback | ClaimsRegisterPage | `/claims` | N | narrow + own-org gate fallback |
| /claims | POST | 102 | `authorize(create, body)` | ClaimsRegisterPage, AppraisalRegisters | — | A (optional project) | body project must be the selection |
| /claims/{id} | GET | 142 | `_load_authorized(view)` | ClaimDetailPage, Tasks, Timeline, LinkedRecordsPanel | `/claims/:id` | N | hold (`allow_unscoped`) |
| /claims/{id} | PUT | 154 | `_load_authorized(edit)` | register | — | N | hold |
| /claims/{id}/status | POST | 180 | edit | register | — | N | hold |
| /claims/{id} | DELETE | 195 | delete + `delete_target` | register | — | N | hold |
| /claims/{id}/assess | POST | 233 | assess | ClaimAssessmentDialog | — | N | hold |
| /claims/{id}/assessments | GET | 253 | view | ClaimAssessmentDialog | — | N | hold |
| /claims/{id}/evidence-bundle | GET | 265 | view + audit.view | detail/register (axios blob) | — | N | hold |
| /claims/{id}/approval | GET | 326 | view (get_or_create) | ClaimApprovalDialog | — | N | hold |
| /claims/{id}/assign, /submit-for-review, /approve, /return | POST | 337–390 | manage/edit | ClaimApprovalDialog | — | N | hold |

### 2.6 IPC / Billing — `routers/ipc_bills.py` (`ipc_bills`: org + required project)

| Route | Method | Line | Current check | Caller | Deep link | Class | CL-4A |
|---|---|---|---|---|---|---|---|
| /ipc-bills | GET | 78 | `authorize(view)` + `build_scope_query` | IPCBillRegisterPage | `/ipc-bills` | N | narrow |
| /ipc-bills/summary | GET | 108 | same | register | — | N | narrow |
| /ipc-bills/export | GET | 129 | `authorize(export)`; `ipc_id` → `_load` | register (axios blob) | — | N | narrow / hold |
| /ipc-bills | POST | 161 | `authorize(create, body)` | register | — | N | body project must be the selection |
| /ipc-bills/{id} | GET | 187 | `_load(view)` | register deep-link fallback | `/ipc-bills?ipc_id=` | N | hold |
| /ipc-bills/{id} | PUT | 201 | `_load(edit/approve)` | register | — | N | hold |
| /ipc-bills/{id} | DELETE | 227 | `_load(delete)` | register | — | N | hold |

### 2.7 Insurance — `routers/insurance.py` (`insurance_policies`: org + required project)

| Route | Method | Line | Current check | Caller | Class | CL-4A |
|---|---|---|---|---|---|---|
| /insurance, /insurance/summary, /insurance/alerts | GET | 120/151/168 | `authorize(view)` + scope | InsuranceRegisterPage | N | narrow |
| /insurance/export | GET | 187 | `authorize(export)` | register (axios blob) | N | narrow |
| /insurance/types (GET/POST/PUT/DELETE) | * | 212–263 | org master data | register | G | — |
| /insurance | POST | 285 | `authorize(create, body)` + link_batch | register | N | body project must be the selection |
| /insurance/upload | POST | 335 | retired (410) | none | G | — |
| /insurance/{id}/documents/upload | POST | 360 | `_load(edit)` | register | N | hold |
| /insurance/{id} | GET | 471 | `_load(view)` | deep-link fallback `?insurance_id=` | N | hold |
| /insurance/{id}/file | GET | 481 | `_load(view)` | `insuranceFileUrl()` plain URL — no caller | A | hold (a header-less link answers 400; §4 exports) |
| /insurance/{id} | PUT/DELETE | 533/575 | `_load(edit/delete)` | register | N | hold |
| /insurance/{id}/replace-file | POST | 555 | retired (410) after `_load` | none | G | hold (load path shared) |
| /insurance/legacy-evidence/inventory, /canonicalize | POST | 651/685 | DMS_ADMIN + step-up, scope named in body | operator | G | — |

### 2.8 Bank Guarantees — `routers/bank_guarantees.py` (BG + events: org + project)

| Route | Method | Line | Current check | Caller | Deep link | Class | CL-4A |
|---|---|---|---|---|---|---|---|
| /bank-guarantees, /summary, /alerts | GET | 98/123/140 | `authorize(view)` + scope | BankGuaranteeRegisterPage | `?bg_id=&event_id=` | N | narrow |
| /bank-guarantees/export | GET | 159 | `authorize(export)` | register (axios blob) | — | N | narrow |
| /bank-guarantees/import/template | GET | 186 | static | register | — | G | — |
| /bank-guarantees/import/preview, /import | POST | 205/243 | `validate_csv_import_scope` + create | CsvImportDialog (navbar scope) | — | N | form project must be the selection |
| /bank-guarantees | POST | 280 | `authorize(create, body)` | register | — | N | body project must be the selection |
| /bank-guarantees/{id} (GET/PUT/DELETE) | * | 309/324/352 | `_load` + `authorize_document` | register | — | N | hold |
| /{id}/status, /extend, /release | POST | 367/385/411 | `_load(edit/extend/release)` | register | — | N | hold |
| /{id}/history, /{id}/events | GET | 427/438 | `_load(view)` | register evidence panel | `event_id` | N | hold |

### 2.9 Key Dates / Achievements / EOT — `routers/key_dates.py`

Key date: own org/project. Achievement: `"{milestone}:ach"`, scope copied from the parent, only
written through `POST /key-dates/{id}/achievement`. EOT submission / determination: own required
`project_id`, project-level (not per key date). Workflow routes use
`_authorize_workflow_scope(q.project)`.

| Route | Method | Line | Entity | Class | CL-4A |
|---|---|---|---|---|---|
| /key-dates, /key-dates/dashboard, /key-dates/export | GET | 162/187/204 | KD | N | narrow |
| /key-dates/import/template | GET | 248 | — | G | — |
| /key-dates/import/preview, /import | POST | 267/303 | KD | N | form project must be the selection |
| /key-dates | POST | 338 | KD | N | body project must be the selection |
| /key-dates/recalculate | POST | 369 | KD (bulk) | N | query project must be the selection |
| /key-dates/workflow, /key-dates/revisions | GET | 397 | baseline + EOT | N | query project must be the selection |
| /key-dates/baseline/freeze | POST | 416 | baseline | N | body project must be the selection |
| /key-dates/eot-submissions | POST | 437 | EOT submission | N | body project must be the selection |
| /key-dates/eot-submissions/{id} (PUT, lock, supersede, template, import/preview, import, export) | * | 467–613, 833 | EOT submission | N | hold |
| /key-dates/eot-determinations | POST | 632 | EOT determination | N | body project must be the selection |
| /key-dates/eot-determinations/{id} (PUT, freeze, template, import/preview, import, export) | * | 664–784, 857 | EOT determination | N | hold |
| /key-dates/baseline/export, /key-dates/history/export | GET | 803/885 | workflow | N | query project must be the selection |
| /key-dates/{id} (GET/PUT/DELETE) | * | 922/932/960 | KD | N | hold |
| /key-dates/{id}/eot, /eots, /eot/{eid}/review, /history (deprecated legacy EOT) | * | 978–1069 | legacy EOT | A | hold (they share `_load`; retirement is a separate decision) |
| /key-dates/{id}/achievement | POST | 1092 | achievement | N | hold the parent |

### 2.10 Dashboards / counts

| Route | Line | Class | CL-4A |
|---|---|---|---|
| GET /dashboard/stats | dashboard.py:226 | N | narrow when a project is selected (every total incl. letters); authenticatedFetch now sends the headers |
| Register summaries (BG/IPC/Insurance/KD dashboard) | above | N | narrow (same list rule) |
| /variations/summary, /hindrances | variations.py:135, hindrances.py:91 | C | — |
| /letters, /tasks/board, /sla/*, /reports/*, /letter-drafting/metrics/dashboard, /arbitration/cases/{id}/dashboard | letters.py:1939 etc. | N / A | recorded CL-4B (correspondence, tasks, reports, arbitration are out of CL-4A scope) |
| /organizations/{id}/stats, /projects, /users, /notifications/unread-count, platform analytics | — | G | — |

## 3. Implementation

### 3.1 One shared mechanism (Phase 2)

`core/tenant_context.py` stays the only parser of `X-Org-Id` / `X-Proj-Id`. CL-4A adds one
method, `ActiveScope.list_filters(organization_id, project_id)`: a list filter may narrow
the selection, never leave it. Variation's router-local `_list_scope` now delegates to it,
so every scoped list shares one rule. Record-level routes use the CL-3A pattern:
`require_selection()` before the load (400 `selection_required` first, whether or not the
id exists), then `require_record(record, allow_unscoped=True)` (403 `context_forbidden`).
Create, import and workflow routes use `require_project(body/form/query project)`, which
refuses a mismatch and never rewrites it. A legacy row with no project is held to the
selected organisation only (Variation CL-2 precedent).

On the relationship routes, `active_scope_enforced = True` now covers `claim`, `ipc_bill`,
`insurance`, `bank_guarantee_event`, `bank_guarantee`, `key_date_achievement`,
`eot_submission`, `eot_determination` and `contract_document`, so all 13 registered adapters
are bound. The forward holds, reverse-lookup filtering and Link-to-Record gating from
CL-3A/CL-3B apply to them unchanged.

### 3.2 Owner-default decisions applied (the AMBIGUOUS rows)

| Question | CL-4A default |
|---|---|
| Organisation-level Documents (`project_id ""`) — record | held to the selected organisation; a project must still be selected (record-level) |
| Organisation-level Documents — `/documents` list | selected project **plus** the selected organisation's organisation-level Documents, for organisation roles and superadmin only (they listed them before); project-tier users never see them (they did not before). `/document-search` (link picker), export and search stay project-only |
| Project-less legacy Claim/IPC/Insurance/BG/Key Date rows | readable/removable by id under a selection of their organisation; not listed under a project selection |
| Claim create with no project | refused (body project must be the selection; never rewritten) |
| D22 `/documents/link`, D19/D20 references | hold source **and** target; reference listings (`/references`, `/linked`) drop targets outside the selection |
| Organisation-level contract upload with nothing selected | still works (held to the selected organisation if any); `/contracts/status` polls it the same way |
| Deprecated legacy EOT routes | held (they share `_load`); retirement is a separate decision |
| Dashboard Organizations/Projects tabs | narrowed with the totals (Phase 11: counts reflect the selected project only) |
| Contract Master v1 (`contract_master_api.py`) | **not changed** — every route already 500s (`core.policy` does not exist); handed off as a separate task |

### 3.3 Module results

Documents (29 routes), Contracts (9) + Contract Master (6) + clause index/list (2), Claims
(14), IPC (7), Insurance (13 scoped + 5 intentionally global), Bank Guarantees (16 incl. 1
global template), Key Dates / achievements / EOT (45 incl. 1 global template), search (3
scoped, 3 global) and the dashboard all follow §2's CL-4A column. Two fixes outside the pure
scope change are disclosed:

* `/search/semantic` returned 500 on every call. It called `search_documents` with four
  arguments, so the other `Query(...)` defaults leaked in. It now passes every argument plus
  the selection. This is pinned by a test.
* `GET /claims` authorized with `organization_id=None` when no filter was given, which
  failed the subscription gate for tenant users. It now falls back to the caller's
  organisation, as IPC, Insurance, BG and Key Dates already did.

### 3.4 Frontend (Phase 15)

* `services/http.ts`: the global fetch wrapper and `authenticatedFetch` now add the
  selection headers to API requests. Before this change only the axios client did, and the
  Documents register, Document Viewer, Letter pages and Dashboard use fetch. Presigned
  storage and third-party URLs never get the headers.
* `fetchApiFileBlob`: `/documents/{id}/download` and `/contracts/{id}/download` answer an
  S3-stored file with a 307 to a presigned URL. A browser follows a redirect with the
  request's own headers, so the selection would reach S3 and turn the presigned GET into a
  CORS preflight. The routes gained an opt-in `?redirect=false`, which returns `{"url": ...}`
  (the default 307 is unchanged). The five download callers now fetch the storage URL with
  no API headers and no credentials.
* `hooks/useRegisterProjectScope.ts`: the Claims, IPC, Insurance, BG, Key Dates, Documents
  and Upload pages pin their project filter and create-form default to the navbar
  project, and disable the page picker while it is pinned. `usePinnedPageScope` does the
  same for the five Contracts pages, which seed their picker from storage.
* Scope refusals are shown as one sentence (`scopeRefusalMessage`). The generic
  "Permission denied" toast is suppressed for them, and `enhanced-api` no longer renders
  a structured 403 as "[object Object]".
* Cache: `MainLayout` already remounts the routed page on a switch. The pages now wait for
  the tenant to load before the first fetch, and every list is bounded by the server.

## 4. Export inventory (Phase 22 — no export rewrite)

| Export / download | Transport | Carries `X-Proj-Id` | Classification |
|---|---|---|---|
| IPC / Insurance / BG / Key Date register exports, EOT baseline/submission/determination/history exports, Claim evidence bundle | axios blob | yes | safe: narrowed/held on the server |
| `/documents/export`, Documents list | `authenticatedFetch` | yes (CL-4A) | safe |
| `/documents/{id}/download`, `/contracts/{id}/download` | `fetchApiFileBlob` | yes; storage leg carries none | safe (presigned leg is object-id permission-checked) |
| `/insurance/{id}/file` | `insuranceFileUrl()` plain URL — no caller | no | requires signed token / query context → CL-4B (a plain link would answer 400) |
| Chronology exports (`<a href>`) | plain link | no | out of CL-4A; CL-4B |
| Arbitration exports | — | — | out of scope (CL-4B) |
| `/reports/*` | body `project_id` from storage | n/a | reporting subsystem, out of scope (CL-4B) |
| `/documents/download-all` | axios blob | yes | held, but **unreachable** (shadowed by `GET /documents/{id}`) — recorded |

## 5. G31 disposition (Phase 21)

`/api/delay-events` and Programme raw `linked_document_ids` are **not touched**, and neither
router file is in the diff. The G31 document-authority suites pass unchanged in the full run.

## 6. Reviews (Phase 25)

Four independent read-only reviews. Their findings and dispositions:

| Review | BLOCKER | HIGH | Disposition |
|---|---|---|---|
| Authorization / scope | 0 | 0 | M1 (D19 target), M2 (reference metadata) **fixed**; M3 clause-uid routes → CL-4B |
| Regression / business | 0 | 3 → 0 | H1 org-level Documents vanished from the register → **fixed** (§3.2); project-less Claim create stays refused (recorded owner decision). H2 consolidated dashboard → by design (Phase 11) and recorded. H3 Upload/Contracts pickers conflicting with the navbar → **fixed** (pinned). M1 claims own-org fallback → kept, disclosed §3.3. M2 cross-project deep links refuse → by design. M3 out-of-scope pages inherit the Document hold → by design (backend must enforce). M4 CL-1 harness refusal source → the unit suite still pins the policy refusal with a pinned selection. M5 clause-uid → CL-4B |
| Frontend cache / navigation | 0 | 1 → 0 | H1 S3 redirect carrying the selection → **fixed** (`fetchApiFileBlob`). M2 duplicate toast → **fixed**. M3 form pickers → **fixed** (pinned). M1 Playwright switches via reload → recorded; the red check (§7) proves the header path. M4 Key Dates EOT panel on a foreign `?project_id` → recorded LOW-impact (server refuses) |
| Relationship / data leak | 0 | 0 | M1 references → **fixed**. M2 → as regression M4. L1 reverse lookup under an organisation-only selection → CL-4B (unreachable in the UI, which always selects a project) |

## 7. Evidence

See the PR description for the exact counts of each run. Playwright red check: with the
pre-CL-4A `http.ts` the cross-module spec fails (an A1 Document stays listed under A2);
with CL-4A it passes (1 worker, 0 retries, mock-backed).

## 8. Remaining CL-4B debt

* Arbitration drafting / arbitration chronology routes, chronology `<a href>` exports,
  `/reports/*`, letters / correspondence, tasks, SLA and letter-drafting metrics — not yet
  selection-bound.
* Contract Master v1 routes (broken import, cross-organisation reconciliation load), plus
  clause-uid mutations (`PATCH /contracts/clauses/{uid}`, split, merge, regenerate).
* The `scope_project_id` vs `project_id` field split for promoted contract instruments.
* `/insurance/{id}/file`: signed-URL or query context for plain links.
* D27: bulk-upload job status is not bound to its caller or project.
* `/documents/download-all` is shadowed by `GET /documents/{id}`.
* Reverse lookup under an organisation-only selection (relationship L1).
* No "All projects" navbar state, so consolidated organisation views need an explicit
  owner decision (regression H2).
* Raw `linked_document_ids` compatibility writes (G31: `/api/delay-events`, Programme).
* Mock-backed Playwright only; the real-stack staging run is still owed.
