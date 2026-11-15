# CL-3A — Hindrance + active-scope integration (2026-09-22)

Integration phase. It reconciles the Hindrance & Constraint Register (PR #22) with the canonical
relationship framework of CL-1/CL-2, and closes the CL-2 Variation active-scope debt. Programme
Milestone / Chronology Document adapters (CL-3B) are **not** started.

## 1. Source lines (re-fetched from both remotes)

| Line | Branch | HEAD | Tree |
|---|---|---|---|
| Release base | `release/contraclaim-rc1` | `99ab522` | `fdd8034` |
| CL-1 (PR #23) | `feat/cl1-relationship-hardening` | `19775a2` | `5ebca8d` |
| CL-2 (PR #26), **CL-3A base** | `feat/cl2-variation-correspondence-links` | `e819884` | `3f2f079` |
| PR #22 | `feat/hindrance-constraint-register` | `5bfd0dd` | `3416da4` |

Both remotes agree on every SHA. `merge-base(CL-2, PR #22) = 99ab522`: PR #22 is 7 commits on
release; CL-2 is 10.

## 2. Three-way overlap (release -> CL-2, release -> PR #22)

CL-2 touched 47 paths, PR #22 57. Six overlap; everything else is one-sided.

| Category | Paths | Decision |
|---|---|---|
| 1. PR #22-only Hindrance feature | `routers/hindrances.py`, `services/hindrance_register_service.py`, `models/evidence_registers.py`, `models/evidence_graph.py`, `routers/evidence_registers.py`, `services/evidence_register_service.py`, `services/evidence_graph_backfill_service.py`, client `pages/Hindrance*`, `components/hindrances/*`, `services/hindrance-api.ts`, `lib/hindrance-labels.ts`, `ContractTimelinePage.tsx` | KEEP PR22 (transplanted by merge) |
| 2. CL-1/CL-2-only canonical relationship | `models/document_relationship.py`, `utils/bson_presentation.py`, `routers/documents.py`, `routers/variations.py`, `services/variation_service.py`, backfill/census, `EntityDocumentLinks.tsx`, `LinkToRecordDialog.tsx`, `lib/relationship-roles.ts` | KEEP CL2 |
| 3. Both changed | `routers/document_relationships.py` | MANUAL: CL-2 routes/bodies kept, PR #22 selection hold kept, then generalised (§4) |
| | `services/document_relationship_service.py` | MANUAL (auto-merged, inspected): PR #22 `link_target` + CL-2 service |
| | `services/entity_adapter_registry.py` | MANUAL: both adapters registered; `DelayEventEntityAdapter` given CL-2's adapter contract |
| | `LinkedRecordsPanel.tsx` | KEEP CL2 (shared label helpers); PR #22 label table superseded |
| | `document-relationships-api.ts` | MANUAL: union of role types and role lists |
| | `playwright.config.ts` | MANUAL: both mocked suites |
| 4. Tenant context | `core/tenant_context.py`, `services/active-scope.ts`, `services/http.ts`, `contexts/TenantContext.tsx`, `main.py` (CORS headers) | KEEP PR22, docstrings generalised |
| 5. Shared relationship UI | `LinkedRecordsPanel`, `LinkToRecordDialog`, `EntityDocumentLinks` | KEEP CL2 + Hindrance target/labels |
| 6. Permissions / roles | `core/permissions.py`, `initial_data/default_permissions.py`, `scripts/align_role_contract.py`, `services/role_contract_alignment.py` | KEEP PR22 |
| 7. Route inventory | `scripts/gate3_route_inventory.py`, `docs/GATE_3_ROUTE_INVENTORY.md`, `tests/test_route_inventory.py` | KEEP PR22 (no CL-2 overlap); API inventory = exact union (640) |
| 8. CI | `.github/workflows/ci.yml` | KEEP CL2 + CL-3A suite added to the replica-set step |
| 9. Tests | CL-1/CL-2 suites, PR #22 suites | KEEP both; CL-2 harnesses now send `X-Proj-Id`; pins updated where a shared label changed |
| 10. Docs | CL-1/CL-2 records; `HINDRANCE_*` | KEEP both; staging plan §14 added |

## 3. Transplant strategy

PR #22 was **merged**, not rebased or cherry-picked (`53f0a1b`): its seven commits keep their SHAs,
so PR #22 stays the preserved source checkpoint and nothing was reimplemented. Five textual conflicts
were resolved by hand (listed in the merge message). The merge tree passed CL-1 + CL-2 real-Mongo,
CL-1/CL-2 unit and all PR #22 backend suites (332 passed, 0 skipped) before any reconciliation.

## 4. Shared relationship reconciliation

* `EntityAdapter.active_scope_enforced` is the single opt-in. `VariationEntityAdapter` and
  `DelayEventEntityAdapter` set it; every other target stays selection-blind (pinned for Claim).
* The router hold is generic (loads through the registry) and replaces PR #22's
  `SCOPED_TARGET_TYPE = "delay_event"` special case. It now also covers CL-2's
  `GET /document-links/{id}` and `/documents/{id}/link-dependencies`, which PR #22 predates.
* Reverse lookups hide selection-bound rows outside the selection. With **no** selection they stay
  bounded by membership (PR #22's list rule, kept); an invalid selection hides them (fail closed).
* Link to Record offers `delay_event`; a selection-bound type needs a selection and offers nothing
  for a Document outside it; archived Hindrances are never offered (`link_target_query`).
* CL-1/CL-2 guarantees are untouched: all CL-1 and CL-2 suites pass unchanged except for sending the
  selection header, which the browser always sends.

## 5. Hindrance Document adapter

`DelayEventEntityAdapter` (target `delay_event`, collection `delay_events`, roles notice /
correspondence / instruction / site_record / photograph / programme_record / supporting_document,
legacy `linked_document_ids` read through as `supporting_document`, write guard fenced on
org/project/`archived_at: None`, no freeze, no delete). Proven on real Mongo: link, forward,
reverse with route `/hindrances/{id}`, correspondence 422 on a contract Document, exactly-once audit,
independent unlink. `delay_event_links` holds only programme milestone / key date / EOT links.

## 6. Active scope

`core/tenant_context.py` (PR #22 port of the codex design) is the one mechanism; the browser sends
`X-Org-Id` / `X-Proj-Id` from `TenantContext` through the single `createHttpClient` interceptor.

| Case | Hindrance | Variation | Relationship routes (both) |
|---|---|---|---|
| member A+B, selected A, record A | 200 | 200 | 200/201 |
| member A+B, selected B, record A | 403 `context_forbidden` | 403 `context_forbidden` | 403 |
| non-member of A | 403 | 403 | 403 |
| no selection, list | 200 bounded | 200 bounded | reverse: bounded |
| no selection, record / write | 400 `selection_required` | 400 `selection_required` | 400 |
| superadmin, selected B, record A | 403 | 403 | 403 |
| superadmin, no selection | list broad, record 400 | list broad, record 400 | 400 |

Variation create refuses a body project other than the selection (never rewrites it). A legacy
Variation with **no** project (kept readable/deletable by CL-2) is held to the selected
organisation only.

**Defect found by the real-seed suite and fixed:** with no project selected, `GET /api/hindrances`
and `/api/delay-events` authorized a project-tier member with `organization_id=None`, so the real
subscription gate answered 403 "No active subscription" instead of the bounded list. PR #22's
fake-Mongo tests stubbed entitlement and could not see it.

## 7. Compatibility `/api/delay-events`

Requires `dms.hindrance.*` and the active scope (PR #22). Proven on real seeds: a custom role with
`dms.evidence_graph.manage` but no `dms.hindrance.*` cannot create or patch; A+B selected B cannot
GET/PATCH DelayEvent A.

**Kept, recorded as debt:** `/api/delay-events` still accepts raw `linked_document_ids` writes. The
G31 document-authority suite (31 tests) certifies that path; refusing it (as CL-2 did for Variation)
was tried and reverted as out of scope. The canonical `/api/hindrances` accepts no raw ids.

## 8. Role contract

Unchanged from PR #22: `dms.hindrance.view/create/edit/archive` reach orgadmin and contractmgr_org
(organisation-tier `CLIENT_DMS_PERMISSIONS`) and projectadmin; the live `contractmgr_org` gets
exactly those four through the bounded `OWNER_APPROVED_GRANTS`; no custom role is granted anything.

## 9. SYSTEM-WIDE ACTIVE-SCOPE CONSISTENCY DEBT (contraclaim-dms#24)

Not moved in CL-3A, selection-blind as before: IPC/Billing, Bank Guarantee, Insurance, Claims,
Key Dates (incl. achievements / EOT), Contract Documents / Contract Master, Documents, dashboards,
reports, correspondence/letters. The global headers change nothing for them: the server reads
`X-Org-Id` / `X-Proj-Id` only in `core/tenant_context.py` consumers and in the `ALLOW_DEV_HEADERS`
path (which also needs `X-User-Id`, never sent by the browser). Several other pages still read
`proj_id` from localStorage (ContractsPage, ContractViewerPage, LetterWorkflowPage, EmailGroupsPage,
settings panels) - out of scope here.

Other debt: projectadmin with several projects cannot switch in the navbar (`canSwitchProject`);
compat raw `linked_document_ids` (§7); `LINK_TO_RECORD_TARGET_TYPES` is still a list beside the
registry; the Playwright workflow is mock-backed - the real-stack staging run is owed (staging plan
§14, gated by R-A9I and the owner window).

## 10. Evidence

See the PR description for the exact counts of each run (CI env, disposable replica set,
`--workers=1 --retries=0`).
