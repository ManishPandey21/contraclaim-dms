# Reference Linking Process Audit - 2026-07-01

## Scope

This audit validates the document/letter reference linking process against the intended UI shown in the screenshots for `/reference/{document_id}`:

- Header card with letter metadata, summary action, and `Sync References`.
- Three tabs: `Linked References`, `Parsed References`, and `Missing References`.
- Parsed references show extracted date + letter number and a link action.
- Linked references show resolved letter number, title, date, and open action.
- Missing references show extracted references that are not yet resolved/linked.

The audit reviewed frontend, backend APIs, reference sync service, persistence behavior, RBAC gates, indexes, and focused tests.

## Verdict

Partially ready.

The UI shape and main API flow exist and match the screenshot at a display level. Backend reference routes are mounted through the active PolicyService-gated router, and focused backend/frontend tests pass. However, there are material correctness gaps in the linking lifecycle:

- Automatic parser reference resolution is not scoped by organization/project, so a duplicate normalized letter number in another tenant/project can be linked incorrectly.
- Manual reference add uses replace-by-source semantics and can remove previous manual links when adding a new one.
- Manual add/remove does not consistently resync FalkorDB, so MongoDB and graph traversal can drift.
- The missing-reference tab is client-derived from parsed-minus-linked data and is not backed by `reference_sync_queue`, so it may not represent the backend reaper state.

## Intended Outcome Mapping

| Screenshot state | Current implementation | Status |
|---|---|---|
| Linked references tab with resolved rows | `ReferencePage` renders `letter.referencesLinked` from `GET /api/documents/{id}/references`. Backend enriches `document.references[]` with target letter number, title, and date. | Implemented |
| Parsed references tab with extracted date/letter number | `ReferencePage` merges `document.reference` from `GET /api/documents/{id}` with endpoint `parsed` data. Backend normalizes `Document.reference` metadata. | Implemented |
| Missing references tab with count | `ReferencePage` computes `parsed - linked` locally by letter number and missing `linkedId`. | Partially implemented |
| Link icon on parsed/missing row | Opens manual link dialog prefilled with letter number, searches `/documents?letterNo=...`, then posts to `/documents/{id}/references`. | Implemented with risks |
| Sync References button | Calls `POST /api/documents/{id}/sync-references`, then refreshes document + references. | Implemented |
| Backend deferred missing reference retry | `ReferenceSyncService.drain_reference_queue()` retries pending references and marks resolved/expired/orphaned. | Implemented but not surfaced in UI |

## Evidence

Frontend:

- Standalone page route: `client/src/routes.tsx:419`
- Route permission mapping: `client/src/config/rolePermissions.ts:76`
- Reference page data load from document: `client/src/pages/ReferencePage.tsx:198`
- Reference endpoint fetch/merge: `client/src/pages/ReferencePage.tsx:259`
- Client missing-reference calculation: `client/src/pages/ReferencePage.tsx:365`
- Sync References action: `client/src/pages/ReferencePage.tsx:422`
- Manual link action: `client/src/pages/ReferencePage.tsx:462`
- Three-tab UI: `client/src/pages/ReferencePage.tsx:605`
- Document viewer references panel add/delete API calls: `client/src/components/document-viewer/ReferencesPanel.tsx:253`, `client/src/components/document-viewer/ReferencesPanel.tsx:310`

Backend:

- Active router mounted: `backend/rbac_backend/main.py:181`
- Reference APIs: `backend/rbac_backend/routers/documents.py:2534`, `backend/rbac_backend/routers/documents.py:2546`, `backend/rbac_backend/routers/documents.py:2559`, `backend/rbac_backend/routers/documents.py:2574`
- Router PolicyService gates for view/link/sync: `backend/rbac_backend/routers/documents.py:2536`, `backend/rbac_backend/routers/documents.py:2548`, `backend/rbac_backend/routers/documents.py:2574`
- Controller target document authorization before manual link: `backend/rbac_backend/routers/documents.py:1625`
- DocumentService parsed/linked response: `backend/rbac_backend/services/document_service.py:1245`
- Manual add uses reference sync with `source="manual"`: `backend/rbac_backend/services/document_service.py:1324`
- Parser sync after document processing: `backend/rbac_backend/services/document_service.py:1194`
- Reference sync service: `backend/rbac_backend/services/reference_sync_service.py:32`
- Missing reference queue/reaper: `backend/rbac_backend/services/reference_sync_service.py:161`, `backend/rbac_backend/services/reference_sync_service.py:199`
- Target resolution by normalized letter number: `backend/rbac_backend/services/reference_sync_service.py:367`
- Compound document index exists for org/project/letter number: `backend/rbac_backend/core/database.py:172`

Tests run:

```powershell
python -m pytest backend/rbac_backend/tests/test_document_references.py backend/rbac_backend/tests/test_reference_sync_service.py backend/rbac_backend/tests/test_reference_sync_reaper.py -q
```

Result: `7 passed`.

```powershell
cd client
npx vitest run src/pages/__tests__/ReferencePage.parsed-references.test.tsx
```

Result: `1 passed, 3 tests passed`.

Note: running the same Vitest command from repo root failed because the client alias `@/...` was not loaded outside the client test scope.

## Findings

### High - Automatic reference resolution is not tenant/project scoped

`ReferenceSyncService._resolve_target_document()` resolves targets using:

- direct `documentId`, then
- `letterNoNormalized`, then
- case-insensitive `letterNo` regex.

The lookup does not constrain target documents to the source document's `organization_id` and `project_id`, even though the source document is loaded and a compound scoped index exists. This can link a source letter to an unrelated document in another organization/project if letter numbers collide.

Impact:

- Cross-tenant/project evidence contamination.
- Incorrect linked references and chronology/drafting context.
- Potential privacy issue if linked metadata becomes visible to users with access to the source but not the real intended target.

Recommended fix:

- Pass source scope into `_resolve_target_document()`.
- Add `organization_id` and `project_id` filters to normalized and regex lookups.
- For cross-project links, require an explicit manual workflow and target authorization.
- Add tests for same letter number in two projects/orgs.

### High - Adding a manual reference can remove previous manual references

`DocumentService.add_reference()` sends only the new manual reference into `ReferenceSyncService.sync_bidirectional(..., source="manual")`. The sync service stores references by replacing the current bucket for the same `source`. Therefore, adding a second manual link can remove the first manual link and remove the old backlink.

Impact:

- Multi-row linked references in the screenshot can be unstable for manually added links.
- User may link parsed rows one by one and unknowingly lose earlier manual links.
- The document viewer panel optimistically appends the new row, so the UI may temporarily hide the loss until refetch.

Recommended fix:

- Change manual add to merge with existing manual references and replay the full manual set plus the new link.
- Add a regression test: add manual target A, then target B, assert both remain and both backlinks remain.

### High - Manual add/remove does not reliably sync FalkorDB

Manual add/remove updates MongoDB `references` and `referencedBy`, but graph sync is only guaranteed in the explicit parser sync path. Existing architecture docs already note that manual mutations can leave FalkorDB stale.

Impact:

- Linked-letter reports, drafting context, and graph traversal can disagree with MongoDB.
- Users may see linked rows in UI but missing/stale graph context elsewhere.

Recommended fix:

- After successful manual add/remove/link, call graph ingestion sync for source and affected target documents.
- Add a reconciliation job comparing Mongo `references`/`referencedBy` with Falkor `CITES`/`REPLIES_TO`.

### Medium - Missing References UI is not backed by backend queue state

The screenshot's `Missing References (2)` is currently computed on the client from parsed references not present in linked rows. The backend also maintains `reference_sync_queue` with pending/resolved/expired/orphaned statuses, but the ReferencePage does not read or display that queue.

Impact:

- UI missing count can differ from backend queue state.
- Users cannot see whether a missing reference is pending retry, expired, orphaned, or resolved by the reaper.
- No retry or diagnostics from the UI.

Recommended fix:

- Add `GET /api/documents/{id}/reference-sync-status` or include queue status in `/references`.
- Return per-reference status: `linked`, `pending`, `expired`, `orphaned`, `ambiguous`.
- Let the UI display status and retry failed/missing entries.

### Medium - Ambiguous letter-number lookup chooses the first result

`ReferencePage.linkReference()` searches `/documents?letterNo=...` and takes the first result. For superadmin or broad org users, duplicate letter numbers can return multiple candidates.

Impact:

- Wrong document can be linked without a user confirmation step.

Recommended fix:

- If multiple candidates match, show a selection dialog with project, date, title, sender/recipient.
- Prefer source document project by default, but require explicit selection for non-exact or multi-match results.

### Medium - Share document reference picker is incompatible with the current response shape

`ShareDocumentPage` fetches `/documents/{documentId}/references` and calls `.map()` directly on the JSON response. The current backend returns `{ parsed, linked }`, not a raw array. The call is inside a catch-and-ignore block, so the picker silently fails to preload linked references.

Impact:

- Linked references may not appear as selectable related documents during sharing.

Recommended fix:

- Update the page to read `Array.isArray(refs) ? refs : refs.linked || []`.
- Add a regression test.

### Low - Frontend linking controls are not permission-aware

`/reference` is gated only by `dms.document.view`. The backend correctly enforces `dms.document.link_reference` for link/sync mutations, but the UI still displays link and sync controls to users who may only have view permission.

Impact:

- Users see actions they cannot perform and receive backend errors.

Recommended fix:

- Hide or disable `Sync References`, `Link New Reference`, row link buttons, and delete controls unless RBAC has `dms.document.link_reference`.

### Low - Test coverage validates display, but not the full screenshot lifecycle

Current tests cover:

- frontend parsed-reference display/merge/dedup,
- backend parsed/linked response,
- add/remove one reference,
- bidirectional parser sync,
- deferred queue reaper.

Missing tests:

- manual multi-link preservation,
- scoped target resolution,
- ambiguous target selection,
- sync button UI behavior,
- missing-reference queue status surfaced in UI,
- Falkor sync after manual mutations,
- permission-aware UI hiding.

## Production Recommendation

Do not treat the reference-linking process as production-complete until the high findings are resolved. The current implementation is usable for demos and controlled data where letter numbers are unique per accessible dataset, but it is not safe enough for production evidence workflows with multiple organizations/projects and repeated correspondence numbering.

## Priority Fix Plan

1. Scope automatic reference resolution by source `organization_id` and `project_id`.
2. Fix manual add to preserve all existing manual references.
3. Add regression tests for scoped resolution and manual multi-link preservation.
4. Sync FalkorDB after manual add/remove/link and add reconciliation reporting.
5. Add backend queue/status data to `/references` or a dedicated endpoint.
6. Add candidate selection UI for ambiguous letter number matches.
7. Make frontend controls permission-aware.
8. Fix `ShareDocumentPage` linked-reference response handling.
