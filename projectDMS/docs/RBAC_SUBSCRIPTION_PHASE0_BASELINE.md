# RBAC + Subscription Phase 0 Baseline

This document is the Phase 0 baseline for hardening RBAC, subscription
entitlements, tenant isolation, usage limits, and auditability.

Phase 0 does not fix the underlying authorization gaps. It makes the intended
security contract explicit, adds an executable backend route inventory, and pins
known gaps with regression tests so later remediation phases can close them
deliberately.

## Canonical authorization contract

Every protected backend action must be evaluated using this decision order:

1. Authenticated user/session is valid.
2. User has the required permission through role assignment.
3. User is in scope for the organisation, project, document, subscription, or
   package being accessed.
4. The effective organisation/project subscription includes the requested
   feature or mode.
5. Any relevant usage quota has available capacity.
6. The allow/deny decision is logged with actor, permission, scope, feature,
   quota, result, and reason.

Frontend route guards are UX-only. They must never be treated as authorization.

## Single sources of truth

| Concern | Canonical component | Phase 0 expectation |
| --- | --- | --- |
| Permission truth | `PermissionService.user_has_permission(...)` | Roles grant permissions; no direct `db.roles` authorization in routers. |
| Scope truth | `ScopeService.is_client_scope_allowed(...)` | Must deny mismatched org/project pairs in Phase 1. |
| Subscription truth | `EntitlementService.check_permission_entitlement(...)` | Must deny when a feature is not included or subscription state is inactive. |
| Quota truth | `UsageMeteringService.check_and_record(...)` | Wired into metered operations through `PolicyService.authorize(...)` in Phase 4. |
| Orchestrating gate | `PolicyService.authorize(...)` | Default backend gate for protected feature APIs. |
| Audit truth | `AuditEventService.emit(...)` | Authorization and entitlement decisions must be auditable. |

## Permission and entitlement matrix

| Feature group | Example permissions | Required subscription feature or mode | Required quota keys | Current Phase 0 status |
| --- | --- | --- | --- | --- |
| DMS document read/download | `dms.document.view`, `dms.document.download`, `dms.document.bulk_download` | `feature.dms.enabled`; archive/offboarding may allow read/export only | Optional export/download quota if product decides to meter it | Partially enforced through `PolicyService` on hardened routes. |
| DMS document upload/edit/delete | `dms.document.upload`, `dms.document.edit_metadata`, `dms.document.delete` | `feature.dms.enabled`; blocked in archive/offboarding | `limit.document_uploads_month`, `limit.storage_gb`, `limit.documents` | Phase 4 wires document-upload count quota for standard, bulk, multipart contract, and chunk-merge upload completion paths; storage capacity remains a deeper storage-service hardening item. |
| OCR | upload/process paths with OCR enabled | `feature.dms.ocr` | `limit.ocr_pages_month` | Phase 4 wires contract-ingestion OCR page quota before OCR batches run; legacy/general document OCR page metering should be completed if that processing path remains active. |
| Advanced search/RAG/vector | retrieval/search/vector operations | `feature.dms.advanced_search` and/or explicit vector feature | `limit.advanced_searches_month`, vector operation quota | Phase 4 wires advanced-search quota into AI assistant letter search and document vector search. |
| Evidence graph/FalkorDB | `dms.evidence_graph.view`, `dms.evidence_graph.manage`, `dms.evidence_graph.verify` | `feature.dms.enabled` plus `feature.dms.evidence_graph` | graph operation quota if metered | Phase 3 enforces the module feature key through `EntitlementService`. |
| Contract and claim modules | `dms.claim.*`, `dms.contract.*`, `dms.arbitration.*`, chronology/key-date/IPC/BG/insurance permissions | `feature.dms.enabled` plus module-specific keys such as `feature.dms.claims`, `feature.dms.contract_appraisal`, `feature.dms.arbitration` | module workflow/export quota if metered | Phase 3 enforces module feature keys through `EntitlementService`; quotas not fully wired. |
| Drafting and AI letter workflows | `drafting.request.*`, `drafting.draft.*`, `drafting.review.*` | `feature.drafting.enabled` plus sub-feature keys such as `feature.drafting.ai_drafts`, `feature.drafting.review` | `limit.drafted_letters_month`, `limit.ai_reviews_month`, AI token quota | Phase 2 moved legacy AI assistant routes to policy; Phase 3 enforces drafting sub-feature keys; Phase 4 wires drafted-letter and AI-review usage quotas into AI assistant generation paths. |
| Billing/subscription management | `subscription.*`, `billing.*` | Role permission; lifecycle state controls target subscription | N/A except billing review limits if added | Many routes require step-up; subscription-id routes need resolved-scope authorization in later phases. |
| User/project/storage administration | `users:*`, `projects:*`, storage settings permissions | Role permission plus active organisation/project subscription where capacity is being changed | `limit.users`, `limit.projects`, `limit.storage_gb` | Capacity limits not fully enforced. |
| System/platform administration | `platform.*`, `system:admin` aliases | Platform role; subscription bypass only if explicit break-glass policy permits it | N/A | Superadmin bypass is current behavior and must remain audited. |

## Subscription state baseline

| Subscription state | Allowed behavior |
| --- | --- |
| `trial`, `pilot`, `active` | Allow subscribed features if permission and scope pass, subject to quota. |
| `archive` | Allow read/download/export only; block writes/admin/drafting. |
| `offboarding` | Allow offboarding export and limited document download/bulk download only. |
| `cancelled`, `suspended`, `expired`, missing subscription | Deny paid/subscription-scoped features. |
| No subscription records | Production must fail closed; migration fail-open requires explicit non-production override. |

## Organisation/project inheritance baseline

| Case | Expected behavior |
| --- | --- |
| Organisation subscription only | Authorised projects inherit organisation entitlements. |
| Project subscription override | Project-specific subscription takes precedence for that project only. |
| Project add-on | Add-on affects only that project. |
| Mismatched organisation and project identifiers | Deny, even if the organisation alone is in scope. |
| Cross-organisation direct API request | Deny and audit. |

## Known Phase 0 gaps pinned by tests

These were intentionally unresolved in Phase 0 and were covered by strict
`xfail` tests in
`backend/rbac_backend/tests/test_rbac_subscription_phase0_baseline.py` until
each remediation phase fixed the related gap and removed the marker.

Resolved in Phase 1:

- `ScopeService.is_client_scope_allowed(...)` now verifies that a supplied
  `project_id` belongs to the supplied `organization_id`.

Resolved in Phase 2:

- Legacy AI assistant letter search, draft generation, LangGraph draft,
  strategy-plan, and LangGraph run retrieval paths now authorize through
  `PolicyService.authorize(...)` so permission, subscription entitlement, tenant
  scope, and audit handling use the canonical backend gate.
- AI assistant downstream reads now carry the resolved scope into Mongo queries
  for similar-letter search, source-document loading, and LangGraph run
  retrieval, preventing authorized requests from reusing or loading data outside
  the approved organisation/project scope.

Resolved in Phase 3:

- `EntitlementService.check_permission_entitlement(...)` now evaluates
  granular permission-to-feature requirements beyond broad
  `feature.dms.enabled` and `feature.drafting.enabled` flags.
- Active add-on feature flags are merged into backend entitlement decisions
  before subscription overrides are applied, so add-ons can grant features and
  explicit overrides can still deny them.
- Built-in default-plan feature fallbacks provide granular module keys for
  bundled plans even when older seeded plan documents do not yet contain the new
  keys.

Resolved in Phase 4:

- `PolicyService.authorize(...)` accepts metering parameters and calls
  `UsageMeteringService.check_and_record(...)` only after role, scope, and
  entitlement checks pass.
- Metered authorization denies quota exhaustion or missing metering scope and
  emits the decision through the existing policy audit event.
- AI assistant draft, LangGraph draft/background, strategy-plan, AI letter
  search, document vector search, standard document upload, bulk per-file
  upload, multipart contract upload, and chunk-merge contract upload paths now
  use canonical metered usage keys.
- Contract OCR ingestion now records and enforces `limit.ocr_pages_month`
  against the exact pages selected for OCR before any OCR batch is executed.

Resolved in Phase 5:

- Subscription lifecycle and billing mutation routes that accept only
  `subscription_id` now load the subscription first and authorize with the
  stored `organization_id` / `project_id` before mutation.
- Scoped billing/subscription permissions now require tenant scope when a
  target organisation or project is present, while global plan/catalog admin
  actions can still authorize without a tenant scope.
- Subscription history, invoice preview, trial conversion, update, upgrade,
  downgrade, cancellation, reactivation, billing-period change, and add-on
  mutation paths use the resolved subscription scope.

Remaining unresolved gaps:

- No strict Phase 0 expected-failure gaps remain in the baseline test suite.

## Backend route inventory

The executable route inventory is implemented in
`scripts/rbac_phase0_route_inventory.py`.

Run:

```powershell
python scripts/rbac_phase0_route_inventory.py --format markdown
```

Useful variants:

```powershell
python scripts/rbac_phase0_route_inventory.py --format json
python scripts/rbac_phase0_route_inventory.py --format summary
```

The script classifies every mounted `/api` route into public/external,
`PolicyService`, permission-only, legacy permission-only, scope-only,
auth-only, step-up-only, system-admin, or no-visible-guard buckets.

## Safety controls

- `RBAC_ENTITLEMENT_FAIL_OPEN` defaults to `false`.
- Production validation rejects `RBAC_ENTITLEMENT_FAIL_OPEN=true`.
- Production validation rejects `ALLOW_DEV_HEADERS=true`.
- Production validation rejects insecure cookies, development CORS origins, and
  other unsafe production settings.
- Phase 0 tests pin these settings so fail-open behavior cannot silently return.

## Completion criteria for Phase 0

- This baseline document exists and is kept current.
- The backend route inventory script can classify all mounted `/api` routes.
- Strict expected-failure tests capture the known P0/P1 audit gaps.
- Production config rejects entitlement fail-open mode.
- No production deployment or data mutation is required for Phase 0.
