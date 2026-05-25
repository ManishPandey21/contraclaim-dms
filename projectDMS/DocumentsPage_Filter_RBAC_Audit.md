# DocumentsPage Filter and RBAC Audit

## Current Implementation

Primary routed page: `client/src/pages/DocumentsPage.tsx`.

Related files reviewed:
- `client/src/routes.tsx`
- `client/src/pages/DocumentsPage.tsx`
- `client/DocumentsPage.tsx`
- `client/src/hooks/useHasPermission.ts`
- `client/src/services/enhanced-api.ts`
- `backend/rbac_backend/routers/documents.py`
- `backend/rbac_backend/services/document_service.py`
- `backend/rbac_backend/services/authorization_service.py`
- `backend/rbac_backend/services/permission_service.py`
- `backend/rbac_backend/core/security.py`
- `backend/rbac_backend/models/document.py`
- `backend/rbac_backend/services/common.py`

`client/src/routes.tsx:13` lazy-loads `client/src/pages/DocumentsPage.tsx`, and `client/src/routes.tsx:76-78` maps both `/documents` and `/documents/legacy` to that component. The root-level `client/DocumentsPage.tsx` is an older duplicate, not the routed page.

Available filters in `DocumentsPage`:

| Filter | UI location | State | API param | Backend support | Client-side filtering |
| --- | --- | --- | --- | --- | --- |
| Search | `client/src/pages/DocumentsPage.tsx:1245-1251` | `searchTerm`, line `179` | `search`, line `307` | Not supported by list endpoint | Yes, current page only, lines `799-805` |
| Direction | `client/src/pages/DocumentsPage.tsx:1257-1269` | `directionFilter`, line `182` | `uploadType=incoming/outgoing`, lines `319-323` | Yes, exact query pass-through | Yes, lines `823-824` |
| Project | `client/src/pages/DocumentsPage.tsx:1271-1283` | `projectFilter`, line `183` | `project_id`, lines `309-310` and again `325-326` | Yes, exact query pass-through | No explicit project match in `filteredDocuments` |
| Status | `client/src/pages/DocumentsPage.tsx:1285-1342` | `statusFilter`, line `180` | `status`, line `313` | Yes, exact query pass-through | Yes, line `806` |
| Tag | `client/src/pages/DocumentsPage.tsx:1344-1356` | `tagFilter`, line `181` | Not sent for listing; commented out at lines `315-318` | Backend can support `tags`, but listing does not use it from this page | Yes, current page only, lines `808-821` |

Data flow:

1. `fetchDocuments` builds `/documents` query params at `client/src/pages/DocumentsPage.tsx:305-331`.
2. The backend list endpoint accepts `organization_id`, `project_id`, `tags`, `subTags`, `uploadType`, `status`, `letterNo`, `subject`, `skip`, and `limit` at `backend/rbac_backend/routers/documents.py:2262-2277`.
3. The list endpoint does not accept `search`; the frontend sends it anyway at `client/src/pages/DocumentsPage.tsx:306-308`.
4. Backend query construction passes through `organization_id`, `project_id`, `uploadType`, and `status`, plus `letterNo`, `subject`, `tags`, and `subTags` when provided, in `backend/rbac_backend/services/authorization_service.py:456-493`.
5. Documents are fetched through `DocumentService.list_documents`, which applies Mongo filtering and backend pagination at `backend/rbac_backend/services/document_service.py:577-590`.
6. `fetch_paginated` applies no default sort unless a sort is supplied; document listing supplies none. See `backend/rbac_backend/services/common.py:39-55`.
7. The frontend transforms backend documents at `client/src/pages/DocumentsPage.tsx:373-441`.
8. The table sorts and filters the already-returned backend page at `client/src/pages/DocumentsPage.tsx:767-834`.
9. Pagination uses backend `total` from `client/src/pages/DocumentsPage.tsx:349-351` and `Math.ceil(totalDocuments / documentsPerPage)` at line `829`, even when client-only filters reduce the visible rows.

Filtered data display:

- Rows are rendered from `paginatedDocuments.map` at `client/src/pages/DocumentsPage.tsx:1486`.
- Displayed fields include date, letter number, direction, from, to, subject, tag, sub-tag, status, project, upload date, and actions at `client/src/pages/DocumentsPage.tsx:1496-1705`.
- No-results state is rendered at `client/src/pages/DocumentsPage.tsx:1376-1405`.
- Loading state is rendered at `client/src/pages/DocumentsPage.tsx:1360-1367`.
- Error state is rendered at `client/src/pages/DocumentsPage.tsx:1368-1375`.

## Expected Behaviour

Filters should be applied consistently on the backend before pagination. The backend response should contain only documents matching all selected filters, and `total` should represent the filtered total. Frontend filtering should either be removed or limited to defensive validation that does not affect pagination counts.

Expected filter semantics:

- Search should match document name/filename, subject, letter number, and preferably parties/from/to. It should be a backend query so it searches across the entire result set, not just the current page.
- Direction should map `Incoming` to `uploadType=incoming` and `Outgoing` to `uploadType=outgoing`.
- Project should filter by `project_id` once and should reset pagination to page 1 on change.
- Status should filter by exact supported statuses or a documented normalized status enum.
- Tag should filter on backend using the same representation as documents store in `tags`: either ObjectId strings or names, but not both ambiguously.
- Multiple filters should combine with AND semantics: a document must satisfy search, project, status, direction, and tag filters together.
- Pagination should reset to page 1 when any filter or search changes.
- The URL should optionally reflect filter state if users are expected to share/bookmark filtered views.
- A clear/reset action should clear all filters and search, reset to page 1, and refetch unfiltered results.
- Date range filtering should either be implemented as backend `date_from`/`date_to` or omitted from requirements. Presently there is no date range filter.

RBAC expectation:

- Frontend route visibility can improve UX but must not be trusted for access control.
- Backend must enforce `documents:read` before listing and enforce organization/project scope in the Mongo query.
- Direct document access, download, references, links, update, and delete must enforce per-resource access.
- Dev-header authentication must never grant elevated roles in production-like environments.

## Issues Found

### 1. Search is sent to the API but ignored by backend list endpoint

Location:
- Frontend sends `search`: `client/src/pages/DocumentsPage.tsx:306-308`.
- Backend list endpoint lacks `search`: `backend/rbac_backend/routers/documents.py:2264-2277`.
- Backend query builder ignores `search`: `backend/rbac_backend/services/authorization_service.py:456-493`.

Present behaviour:
- API returns a normal paginated document page, not global search results.
- Frontend then searches only the already-returned page at `client/src/pages/DocumentsPage.tsx:799-805`.

Impact:
- Search misses matching documents on other pages.
- Backend `total` and pagination remain unrelated to the search result set.
- Export also accepts `search` at `backend/rbac_backend/routers/documents.py:1843`, but `build_document_query` ignores it, so exported data is not search-filtered.

Risk level: High functional risk.

### 2. Tag filter is frontend-only for listing but pagination remains backend-wide

Location:
- Listing tag param is commented out: `client/src/pages/DocumentsPage.tsx:315-318`.
- Tag filter is applied on the current page: `client/src/pages/DocumentsPage.tsx:808-821`.
- Backend can accept `tags`: `backend/rbac_backend/routers/documents.py:2267`, `backend/rbac_backend/services/authorization_service.py:485-488`.

Present behaviour:
- Selecting a tag does not ask backend for matching documents.
- Only the current backend page is filtered.
- `totalPages` still uses unfiltered backend `total` at `client/src/pages/DocumentsPage.tsx:829`.

Impact:
- Matching tag results on other pages are hidden until the user manually pages through.
- The count says "of N documents" where N is not the tag-filtered count.
- A page can show no rows even when matching rows exist elsewhere.

Risk level: High functional risk.

### 3. Search, status, direction, and tag are applied twice or inconsistently

Location:
- Backend params are built at `client/src/pages/DocumentsPage.tsx:305-331`.
- Client filtering repeats search/status/tag/direction at `client/src/pages/DocumentsPage.tsx:799-827`.

Present behaviour:
- Status and direction are filtered by backend and then filtered again by frontend.
- Search is not filtered by backend, only frontend.
- Tag is not filtered by backend for listing, only frontend.
- Project is filtered by backend only.

Impact:
- Combined filters do not have one clear source of truth.
- Backend `total` matches only backend-applied filters, not final displayed rows.
- UI behaviour varies by filter type.

Risk level: High functional risk.

### 4. Project filter is appended twice

Location:
- First append: `client/src/pages/DocumentsPage.tsx:309-310`.
- Duplicate append: `client/src/pages/DocumentsPage.tsx:325-326`.

Present behaviour:
- URL contains duplicate `project_id` values when project is selected.
- FastAPI receives an optional string, not a list, so this likely resolves to one value, but it is still incorrect and fragile.

Risk level: Low to Medium functional risk.

### 5. Filter changes do not reset pagination to page 1

Location:
- Filter setters are direct: `client/src/pages/DocumentsPage.tsx:1249-1250`, `1257-1259`, `1271`, `1285`, `1344`.
- Pagination is calculated from `currentPage`: `client/src/pages/DocumentsPage.tsx:328-330`.
- Page changes only happen in pagination controls: `client/src/pages/DocumentsPage.tsx:1720-1757`.

Present behaviour:
- If the user is on page 5 and changes a filter that only has one page of results, the frontend still requests `skip=100`.

Impact:
- Users can see "No Documents Found" even though matching documents exist on page 1.

Risk level: High functional risk.

### 6. No clear/reset action exists

Location:
- No reset handler found for `searchTerm`, `statusFilter`, `tagFilter`, `directionFilter`, `projectFilter`, and `currentPage`.
- "All" options exist for individual selects at `client/src/pages/DocumentsPage.tsx:1265`, `1276`, `1290`, and `1349`.

Present behaviour:
- Users must manually clear search and reset each dropdown.
- Current page is not reset when clearing individual filters.

Risk level: Medium UX risk.

### 7. No-results messaging ignores project filter

Location:
- No-results conditional checks search/status/tag/direction only: `client/src/pages/DocumentsPage.tsx:1385-1395`.

Present behaviour:
- If only `projectFilter` is active and no rows return, the UI can show the "You haven't uploaded any documents yet" empty-library message and upload button instead of a filtered-empty message.

Risk level: Medium UX risk.

### 8. Backend listing has no deterministic sort

Location:
- `DocumentService.list_documents` calls `fetch_paginated` without a sort at `backend/rbac_backend/services/document_service.py:584-590`.
- `fetch_paginated` only sorts if a sort argument is supplied at `backend/rbac_backend/services/common.py:52-55`.
- Frontend sorts only the returned page at `client/src/pages/DocumentsPage.tsx:767-797`.

Present behaviour:
- Backend page boundaries can be nondeterministic or insertion-order dependent.
- Frontend sorting is per page, not global.

Impact:
- A document's position across pages may be unstable.
- Sorting by date/status/tag does not sort the full dataset.

Risk level: Medium functional risk.

### 9. Export filters do not match listing filters

Location:
- Export sends search/status/direction/tag: `client/src/pages/DocumentsPage.tsx:889-903`.
- Export backend accepts `search`: `backend/rbac_backend/routers/documents.py:1836-1847`.
- Shared query builder ignores `search`: `backend/rbac_backend/services/authorization_service.py:456-493`.

Present behaviour:
- Search is ignored for export.
- Listing tag filter compares selected tag id and selected tag name against displayed `doc.tag`; export sends only `tags=tagFilter`.

Impact:
- Exported Excel can include documents not visible in the table or exclude documents visible after client-side tag-name matching.

Risk level: Medium to High functional risk.

### 10. Date range filtering is absent

Location:
- No date filter state or date range query params exist in `client/src/pages/DocumentsPage.tsx`.
- Backend list endpoint has no date range params at `backend/rbac_backend/routers/documents.py:2264-2277`.

Present behaviour:
- Date fields are displayed and sortable, but not filterable by range.

Risk level: Requirement gap.

### 11. Invalid filter values are not consistently validated

Location:
- `status` is an unconstrained query string: `backend/rbac_backend/routers/documents.py:2270`.
- `uploadType` is an unconstrained query string in the list endpoint: `backend/rbac_backend/routers/documents.py:2269`, though the model constrains stored documents at `backend/rbac_backend/models/document.py:74`.

Present behaviour:
- Invalid status/uploadType values likely return zero results rather than a validation error.
- Frontend select options prevent normal UI invalid values, but direct API calls are not constrained.

Risk level: Low to Medium API quality risk.

### 12. Tag and sub-tag display logic is noisy and ambiguous

Location:
- Tag display maps through `availableTags`, then `availableSubtags`, then raw value at `client/src/pages/DocumentsPage.tsx:1516-1535`.
- Sub-tag display maps through `availableTags`, then `availableSubtags`, then raw value at `client/src/pages/DocumentsPage.tsx:1538-1550`.
- Debug logs run during row render at `client/src/pages/DocumentsPage.tsx:1525-1533` and `1546-1548`.

Present behaviour:
- Tag display may resolve a tag id through sub-tags if IDs collide or data is inconsistent.
- Render-time console logs will spam the console for every row render.

Risk level: Low UX/maintainability risk.

## RBAC Findings

### Backend enforcement exists for list access

Location:
- List endpoint requires authenticated current user and `documents:read`: `backend/rbac_backend/routers/documents.py:2275-2277`.
- `require_permission` enforces permissions and returns 403 on failure: `backend/rbac_backend/core/security.py:224-263`.
- Permission aliases include `documents:read`, `docs:view`, and `letters:view`: `backend/rbac_backend/services/permission_service.py:121-142`.

Finding:
- Users without `documents:read` or an accepted alias should not be able to list documents.

Risk level: Low.

### Backend list query applies organization/project scope

Location:
- `controller_list_documents` calls `build_document_query`: `backend/rbac_backend/routers/documents.py:1198-1212`.
- Scope is applied in `AuthorizationService.build_document_query`: `backend/rbac_backend/services/authorization_service.py:495-526`.

Finding:
- Non-superadmin users are constrained to allowed organizations.
- Project-scoped roles are constrained to `current_user.projects` when no `project_id` is supplied.
- If a project-scoped user supplies a disallowed `project_id`, authorization raises an error at `backend/rbac_backend/services/authorization_service.py:512-518`.
- `orgadmin` and `orguser` are allowed across projects in their organization by design at `backend/rbac_backend/services/authorization_service.py:511-524`.

Risk level: Medium, depending on intended orguser scope. If `orguser` should not see all projects in the organization, this is overbroad.

### Backend direct document access is stronger than list access

Location:
- `_ensure_document_access` calls `PermissionService.check_resource_access`: `backend/rbac_backend/routers/documents.py:78-89`.
- `GET /documents/{id}` calls `_ensure_document_access` and `controller_get_document`: `backend/rbac_backend/routers/documents.py:2141-2154`.
- Download also checks `_ensure_document_access` and organization/project access: `backend/rbac_backend/routers/documents.py:2181-2206`.
- Resource membership checks document owner, organization, or project membership: `backend/rbac_backend/services/permission_service.py:791-810`.

Finding:
- Direct document fetch and download have resource-level checks in addition to permission checks.
- Listing relies on scoped Mongo query rather than per-row resource checks.

Risk level: Low to Medium. Query-level filtering is acceptable if scope rules are correct, but per-row checks would be more defensive.

### Frontend RBAC is limited to delete action hiding

Location:
- Delete permission hook: `client/src/pages/DocumentsPage.tsx:839`.
- Delete is hidden in the dropdown if permission is false: `client/src/pages/DocumentsPage.tsx:1691-1699`.
- Hook implementation calls `/permissions/check`: `client/src/hooks/useHasPermission.ts:8-37`, `client/src/services/enhanced-api.ts:614-625`.
- `ProtectedRoute` only checks authentication, not document permission: `client/src/components/auth/ProtectedRoute.tsx:6-15`.

Finding:
- The frontend does not hide the Documents page based on `documents:read`.
- View/download buttons are always shown, but backend should enforce access.
- Delete hiding is UX only; backend delete still enforces permission and resource access via routes around `backend/rbac_backend/routers/documents.py:2328-2337`.

Risk level: Low security risk, Medium UX risk.

### Dev-header fallback can become a serious security issue if enabled outside development

Location:
- `buildAuthHeaders` defaults roles to `superadmin` and user id to `demo`: `client/src/pages/DocumentsPage.tsx:269-287`.
- Backend accepts dev headers only when `ALLOW_DEV_HEADERS` is true: `backend/rbac_backend/core/security.py:134-189`.

Finding:
- If no token exists and dev headers are allowed, the frontend can send `X-User-Role: superadmin`.
- Backend explicitly disables dev headers unless `settings.ALLOW_DEV_HEADERS` is true, which limits the issue in production if configured correctly.

Risk level: Critical if `ALLOW_DEV_HEADERS=True` in shared/staging/production; Low if it is strictly local-only.

### Related document APIs also enforce access

Location:
- References list checks read access: `backend/rbac_backend/routers/documents.py:1541-1552`.
- Add reference checks source update and target read: `backend/rbac_backend/routers/documents.py:1563-1594`.
- Linked document list checks read access: `backend/rbac_backend/routers/documents.py:1767-1781`.
- Link documents checks source update and target read: `backend/rbac_backend/routers/documents.py:1725-1751`.

Finding:
- The related reference/link endpoints used from the document experience generally enforce backend access.

Risk level: Low.

## Risk Level

Overall risk: High.

Primary reason: the listing UI mixes backend pagination with frontend-only filtering. Search and tag results are incomplete, counts are wrong, and export does not consistently match the visible table.

Security risk: Medium overall, with one configuration-dependent Critical risk if dev headers are enabled outside local development. Backend RBAC is present and generally stronger than frontend hiding, but org/project scope should be confirmed against the intended business policy.

## Implementation Update

Implemented on 2026-05-07.

Completed phases:

1. Backend search and tag filtering
   - Added `search` support to `GET /documents` in `backend/rbac_backend/routers/documents.py:list_documents`.
   - Added backend search query construction in `AuthorizationService.build_document_query`, matching `filename`, `subject`, `letterNo`, `from`, `from_`, and `to`.
   - Added tag/sub-tag value expansion in `AuthorizationService._expand_lookup_filter_values` so filters can match both stored IDs and display names.
   - Updated `DocumentsPage.fetchDocuments` to send `tags` for listing instead of filtering tags only on the current frontend page.

2. Reset pagination on filter change
   - Added `updateDocumentFilters` in `client/src/pages/DocumentsPage.tsx`.
   - Search, direction, project, status, and tag changes now reset `currentPage` to `1`.

3. Removed duplicate `project_id`
   - Removed the second `project_id` append in `DocumentsPage.fetchDocuments`.

4. Backend default sorting
   - Added deterministic default sort to `DocumentService.list_documents`: `createdAt DESC`, `date DESC`, `_id DESC`.

5. Clear/reset all filters button
   - Added `resetDocumentFilters` and a visible Reset button when any filter/search is active.
   - Empty filtered results also show a Reset Filters action.

6. No-results messaging
   - Replaced the incomplete filter check with `hasActiveFilters`, which includes project filtering.

7. Removed frontend superadmin dev-header fallback
   - `buildAuthHeaders` no longer sends default `X-User-Id: demo` or default `X-User-Role: superadmin`.
   - Dev headers are now only sent when values are explicitly present in `localStorage`.

8. URL-driven filters
   - Added `useSearchParams` support in `client/src/pages/DocumentsPage.tsx`.
   - Filter state initializes from URL params and writes changes back to URL params:
     `search`, `project_id`, `status`, `tags`, `uploadType`, and `page`.
   - Pagination controls update the `page` URL param.

Additional alignment:
- Export now includes `project_id` and uses trimmed `search`, so export filters are closer to listing filters.
- Frontend no longer applies search/tag/status/direction filtering to only the current backend page; backend filtering is the source of truth before pagination.

## Recommended Fixes

1. Implement backend search for `/documents`.
   - Add `search: Optional[str] = Query(None)` to `backend/rbac_backend/routers/documents.py:list_documents`.
   - Extend `AuthorizationService.build_document_query` to add a Mongo `$or` over `filename`, `subject`, `letterNo`, `from`, and `to`.
   - Escape user input with `re.escape` unless intentionally supporting regex search.

2. Move tag filtering to backend for listing.
   - Decide whether `Document.tags` stores tag IDs or tag names.
   - Send `tags=tagFilter` from `client/src/pages/DocumentsPage.tsx`.
   - Remove the client-side tag fallback once storage is normalized, or use it only as non-paginating display fallback.

3. Make one source of truth for filters.
   - Prefer backend filtering before pagination for search, project, status, direction, tags, subTags, and date ranges.
   - Remove `filteredDocuments` filtering for backend-supported filters or make it a defensive assertion only.

4. Reset pagination on filter/search changes.
   - Add a `useEffect` or wrapped handlers that call `setCurrentPage(1)` when `searchTerm`, `statusFilter`, `tagFilter`, `directionFilter`, or `projectFilter` changes.

5. Add a clear/reset filters action.
   - Clear `searchTerm`.
   - Set all filters to `"all"`.
   - Reset `currentPage` to `1`.
   - Refetch.

6. Fix project query duplication.
   - Remove the duplicate `project_id` append at `client/src/pages/DocumentsPage.tsx:325-326`.

7. Fix no-results messaging.
   - Include `projectFilter !== "all"` in the filtered-empty condition at `client/src/pages/DocumentsPage.tsx:1385-1395`.

8. Add deterministic backend sorting.
   - Add default sort such as `createdAt DESC` or `date DESC, createdAt DESC` to `DocumentService.list_documents`.
   - If sortable table columns must be global, pass sort params to backend rather than sorting only the current page.

9. Align export with visible listing.
   - Reuse the same backend query builder and supported params for list and export.
   - Ensure `search` and tag filtering behave identically.

10. Add optional date range support if required.
   - Frontend: add `dateFrom` and `dateTo`.
   - Backend: add `date_from` and `date_to`, validate them, and query `date` with `$gte`/`$lte`.
   - Reset pagination when range changes.

11. Validate filter values at API boundary.
   - Use enums or constrained literals for `uploadType`.
   - Validate status against the allowed workflow statuses or centralize status definitions.

12. Remove dev-header defaults from production-facing frontend code.
   - Do not default missing roles to `superadmin`.
   - Only send `X-User-*` headers behind an explicit local development flag.
   - Verify `ALLOW_DEV_HEADERS=False` in all non-local deployments.

13. Strengthen tests.
   - Add backend tests for list search, combined filters, tag filtering, invalid filter values, filtered totals, and RBAC scope boundaries.
   - Add frontend tests for page reset after filter changes, project-only no-results message, tag filter pagination, and export filter parity.

## Issue Status Report

Updated on 2026-05-07 after implementation.

| # | Audit issue/finding | Status | Notes |
| --- | --- | --- | --- |
| 1 | Search is sent to the API but ignored by backend list endpoint | Fixed | `GET /documents` now accepts `search`, and `AuthorizationService.build_document_query` applies backend `$or` search across filename, subject, letter number, from, from_, and to. Export also benefits because it uses the same query builder. |
| 2 | Tag filter is frontend-only for listing but pagination remains backend-wide | Fixed | `DocumentsPage.fetchDocuments` now sends `tags`; backend expands tag IDs/names and filters before pagination. |
| 3 | Search, status, direction, and tag are applied twice or inconsistently | Fixed | Frontend no longer filters the current page for search/status/direction/tag. Backend filtering is now the source of truth before pagination. |
| 4 | Project filter is appended twice | Fixed | Duplicate `project_id` append was removed from `DocumentsPage.fetchDocuments`. |
| 5 | Filter changes do not reset pagination to page 1 | Fixed | Filter/search changes now go through `updateDocumentFilters(..., { resetPage: true })`. |
| 6 | No clear/reset action exists | Fixed | Added Reset button and `resetDocumentFilters`; filtered empty state also offers Reset Filters. |
| 7 | No-results messaging ignores project filter | Fixed | Empty-state logic now uses `hasActiveFilters`, which includes project filtering. |
| 8 | Backend listing has no deterministic sort | Fixed | `DocumentService.list_documents` now sorts by `createdAt DESC`, `date DESC`, and `_id DESC`. |
| 9 | Export filters do not match listing filters | Mostly fixed | Export now sends trimmed `search`, `project_id`, `status`, `uploadType`, and `tags`, and backend query builder handles search/tag filtering. Remaining gap: export/list parity should be covered by automated tests. |
| 10 | Date range filtering is absent | Open | No date range UI or backend `date_from`/`date_to` params were added because it was outside the requested priority list. |
| 11 | Invalid filter values are not consistently validated | Open | API still accepts free-form `status` and `uploadType` strings on the list endpoint. Add enum/literal validation in a later phase. |
| 12 | Tag and sub-tag display logic is noisy and ambiguous | Open | Render-time tag/sub-tag console logs and ambiguous display fallback remain. Functional backend tag filtering was fixed, but display cleanup was not part of this pass. |

RBAC/security finding status:

| Finding | Status | Notes |
| --- | --- | --- |
| Backend enforcement exists for list access | Verified unchanged | `GET /documents` still requires authenticated user and `documents:read`. |
| Backend list query applies organization/project scope | Verified unchanged | Scope enforcement remains in `AuthorizationService.build_document_query`. Business decision still needed if `orguser` should not see all organization projects. |
| Backend direct document access is stronger than list access | Verified unchanged | Direct fetch/download/reference/link endpoints retain resource-level checks. |
| Frontend RBAC is limited to delete action hiding | Open by design | This pass did not add route-level `documents:read` UI hiding. Backend remains authoritative. |
| Dev-header fallback can become a serious security issue if enabled outside development | Frontend fixed, deployment check still required | `DocumentsPage.buildAuthHeaders` no longer defaults to `demo/superadmin`. Backend still supports dev headers when `ALLOW_DEV_HEADERS=True`; verify it is false outside local development. |

Recommended-fix status:

| Recommended fix | Status |
| --- | --- |
| Backend search for `/documents` | Fixed |
| Move tag filtering to backend for listing | Fixed |
| Make one source of truth for filters | Fixed for current filters; date range remains open |
| Reset pagination on filter/search changes | Fixed |
| Add clear/reset filters action | Fixed |
| Fix project query duplication | Fixed |
| Fix no-results messaging | Fixed |
| Add deterministic backend sorting | Fixed |
| Align export with visible listing | Mostly fixed; needs automated parity tests |
| Add optional date range support | Open |
| Validate filter values at API boundary | Open |
| Remove dev-header defaults from production-facing frontend code | Fixed in `DocumentsPage`; backend deployment config must still be verified |
| Strengthen tests | Open |

Validation status:
- Backend syntax check passed: `python -m py_compile backend\rbac_backend\services\authorization_service.py backend\rbac_backend\routers\documents.py backend\rbac_backend\services\document_service.py`.
- Frontend production build passed: `npm run build` from `client`.
- No automated backend/frontend regression tests were added in this phase.

## Follow-up Implementation and TagsPage Audit

Updated on 2026-05-07 after follow-up issue review.

### DocumentsPage Follow-up Status

| Issue | Status | File/function references | Notes |
| --- | --- | --- | --- |
| Add date range UI and backend `date_from`/`date_to` params | Fixed | `client/src/pages/DocumentsPage.tsx:171-172`, `client/src/pages/DocumentsPage.tsx:202-226`, `client/src/pages/DocumentsPage.tsx:1463-1489`, `backend/rbac_backend/routers/documents.py:1844-1866`, `backend/rbac_backend/routers/documents.py:2276-2296`, `backend/rbac_backend/services/authorization_service.py:502-563` | Listing and export now accept `date_from` and `date_to`; the frontend stores them in URL params, sends them to API/export, includes them in reset/active-filter state, and resets pagination on change. Backend applies the range to the document `date` field with `$gte`/`$lte`. |
| Correct tag and sub-tag display logic | Fixed | `client/src/pages/DocumentsPage.tsx:337-360`, `client/src/pages/DocumentsPage.tsx:1792-1803` | Removed render-time console logging and ambiguous cross-lookup. Tags now resolve only from `availableTags`; sub-tags resolve only from `availableSubtags`; empty values render as `--`. |
| Filter/export parity after date range | Fixed for implemented filters | `client/src/pages/DocumentsPage.tsx:527-531`, `client/src/pages/DocumentsPage.tsx:1086-1090` | List and export both send `date_from` and `date_to`. Automated parity tests are still not present. |

### TagsPage Current Implementation

Frontend:
- `client/src/pages/TagsPage.tsx:62-88` loads tags through `listTags()` and initializes frontend-only expansion/editing state.
- `client/src/pages/TagsPage.tsx:91-140` creates tags/sub-tags and refreshes the affected list.
- `client/src/pages/TagsPage.tsx:144-201` updates tag/sub-tag names and patches local state.
- `client/src/pages/TagsPage.tsx:210-251` deletes tags/sub-tags and removes them from local state.
- `client/src/pages/TagsPage.tsx:272-330` lazy-loads sub-tags for an expanded tag through `listSubTags(tagId)`.
- `client/src/services/tags-api.ts:69-114` wraps `/tags`, `/tags/{tag_id}/subtags`, and `/subtags/{subtag_id}` APIs and normalizes `_id`/`id`.

Backend:
- `backend/rbac_backend/routers/tags.py:100-132` lists tags after `tags:read` permission and `AuthorizationService.build_tag_query`.
- `backend/rbac_backend/routers/tags.py:394-431` lists sub-tags only after loading the parent tag and calling `check_tag_access`.
- `backend/rbac_backend/routers/tags.py:55-88`, `199-241`, `269-314`, `337-381`, `462-508`, and `536-581` enforce permissions for create/update/delete flows.
- `backend/rbac_backend/services/authorization_service.py:1127-1238` builds tag visibility scope for global, organization, and project tags.
- `backend/rbac_backend/services/tag_service.py:133-249` performs tag pagination/enrichment.
- `backend/rbac_backend/services/tag_service.py:316-404` performs sub-tag pagination/enrichment.

### TagsPage Issues Found

| Issue | Risk | File/function references | Details |
| --- | --- | --- | --- |
| TagsPage does not expose backend search/pagination | Medium | `client/src/services/tags-api.ts:69-72`, `client/src/pages/TagsPage.tsx:62-88`, `backend/rbac_backend/routers/tags.py:738-753` | Backend supports `skip`, `limit`, and `search`, but the frontend always calls `/tags` without params and has no search, next/previous, or total-count UI. Large tag sets will silently show only the backend default first page. |
| Non-superadmin tag search can be lost by authorization query construction | Medium | `backend/rbac_backend/services/authorization_service.py:1150-1221`, `backend/rbac_backend/routers/tags.py:115-121`, `backend/rbac_backend/services/tag_service.py:146-164` | `build_tag_query` may place search in `$or`, then replace `$or` with visibility conditions. The controller passes only the authorized query into `get_tags_paginated`, so the original `search` value is not available for the service to combine with visibility. |
| Backend tag search uses raw regex text | Medium | `backend/rbac_backend/services/authorization_service.py:1150-1157`, `backend/rbac_backend/services/tag_service.py:149-160` | Search text is not escaped with `re.escape`, so user input is interpreted as a regex. This can produce unexpected matches or expensive regex behavior. |
| Usage counts may not match document storage fields | Medium | `backend/rbac_backend/services/tag_service.py:216-217`, `backend/rbac_backend/services/tag_service.py:380`, `backend/rbac_backend/services/tag_service.py:433-435` | Tag usage counts only check `documents.tags == tag.id`; sub-tag usage checks lowercase `subtags`, while documents elsewhere use camelCase `subTags`. If documents store names or camelCase, counts and delete protection can be wrong. |
| No empty state for zero tags | Low | `client/src/pages/TagsPage.tsx:563-567` | When the tag list is empty and not loading, the UI renders an empty bordered panel with no explanatory message or create guidance. |
| Sub-tag operations do not have per-action loading guards | Low | `client/src/pages/TagsPage.tsx:56-60`, `client/src/pages/TagsPage.tsx:116-140`, `client/src/pages/TagsPage.tsx:164-201`, `client/src/pages/TagsPage.tsx:228-251` | `operationLoading` tracks only tag add/delete/update. Sub-tag create/update/delete can be double-clicked while requests are in flight. |
| Delete operations have no confirmation prompt | Low | `client/src/pages/TagsPage.tsx:210-251` | Backend blocks deletion when usage is detected, but accidental deletion of unused tags/sub-tags is possible from the UI. |

### TagsPage RBAC Findings

| Finding | Status | Notes |
| --- | --- | --- |
| Backend permission checks exist for tag CRUD | Verified | Router/controller calls `tags:read`, `tags:create`, `tags:update`, and `tags:delete` before operations. |
| Parent-tag access is checked before listing sub-tags | Verified | `TagController.get_subtags` loads the parent tag and calls `check_tag_access(current_user, parent_tag, "read")`. |
| Backend visibility scope is role-aware | Mostly verified | `build_tag_query` includes global/org/project visibility rules, but its `$or` handling can conflict with search filters and should be refactored to preserve both visibility and search. |
| Frontend hiding is not authoritative | Open by design | TagsPage does not appear to hide actions by permission. Backend enforcement is present, but UX would improve if unauthorized actions were hidden/disabled after permission checks. |

### TagsPage Risk Level

Overall TagsPage risk: Medium.

Primary risk is data correctness, not immediate document disclosure: tag search/pagination are not fully exposed in the UI, search can be dropped for scoped users, and tag/sub-tag usage counts may be inaccurate because document field names are inconsistent. Backend RBAC checks are present for tag visibility and CRUD, but search/visibility query composition should be tightened.

### TagsPage Recommended Fixes

1. Add search, pagination, and total-count handling to `listTags`/`TagsPage`.
2. Refactor `AuthorizationService.build_tag_query` to combine visibility and search with `$and`, preserving both conditions.
3. Escape tag search text with `re.escape` unless regex search is an intentional feature.
4. Normalize document tag storage and update usage-count queries to cover the canonical fields, especially `subTags`.
5. Add empty-state messaging for zero tags.
6. Add per-sub-tag operation loading state and disable duplicate actions while requests are in flight.
7. Add confirmation before deleting unused tags or sub-tags.

### Updated Issue Status Report

| # | Audit issue/finding | Status after follow-up | Notes |
| --- | --- | --- | --- |
| 1 | Search is sent to API but ignored by backend list endpoint | Fixed | No change from prior status. |
| 2 | Tag filter is frontend-only for listing | Fixed | No change from prior status. |
| 3 | Search/status/direction/tag filtering inconsistent | Fixed | No change from prior status. |
| 4 | Project filter appended twice | Fixed | No change from prior status. |
| 5 | Filter changes do not reset pagination | Fixed | Date range changes also reset pagination. |
| 6 | Clear/reset action missing | Fixed | Reset now clears date range as well. |
| 7 | No-results messaging ignores project filter | Fixed | No change from prior status. |
| 8 | Backend listing has no deterministic sort | Fixed | No change from prior status. |
| 9 | Export filters do not match listing filters | Fixed for implemented filters | Export now includes date range in addition to search/project/status/uploadType/tags. Automated parity tests remain open. |
| 10 | Date range filtering is absent | Fixed | Added URL-driven UI and backend `date_from`/`date_to` list/export filtering. |
| 11 | Invalid filter values are not consistently validated | Open | `status` and `uploadType` remain free-form on the list endpoint. |
| 12 | Tag and sub-tag display logic is noisy and ambiguous | Fixed | Display lookup now uses dedicated tag/sub-tag resolvers and no console logging. |
| 13 | TagsPage backend/frontend audit | Completed | Findings and recommendations added above; no TagsPage code changes were made in this follow-up. |

Follow-up validation:
- Backend syntax check passed: `python -m py_compile backend\rbac_backend\services\authorization_service.py backend\rbac_backend\routers\documents.py`.
- Frontend production build passed: `npm run build` from `client`.
- Build warnings remain unrelated to this change: outdated Browserslist data, `pdfjs-dist` eval warning, mixed static/dynamic `LoginPage` import, and large chunk warnings.

## TagsPage Fix Implementation Update

Updated on 2026-05-07 after TagsPage issue correction.

| # | TagsPage issue | Status | File/function references | Notes |
| --- | --- | --- | --- | --- |
| 1 | Add search, pagination, and total-count handling to `listTags`/`TagsPage` | Fixed | `client/src/services/tags-api.ts:29-47`, `client/src/services/tags-api.ts:89-124`, `client/src/pages/TagsPage.tsx:48-126`, `client/src/pages/TagsPage.tsx:693-783` | `listTags` now accepts `search`, `page`, and `limit`, returns total/page metadata, and `TagsPage` shows search, result count, and previous/next pagination. |
| 2 | Preserve tag visibility and search together | Fixed | `backend/rbac_backend/services/authorization_service.py:1127-1236` | `build_tag_query` now combines visibility scope and search with `$and`, so scoped users do not lose search filtering. |
| 3 | Escape tag search text | Fixed | `backend/rbac_backend/services/authorization_service.py:1160-1169`, `backend/rbac_backend/services/tag_service.py:149-163` | Tag search now uses `re.escape` before building regex queries. |
| 4 | Normalize usage-count queries for document tag/sub-tag fields | Fixed | `backend/rbac_backend/services/tag_service.py:222`, `backend/rbac_backend/services/tag_service.py:278-283`, `backend/rbac_backend/services/tag_service.py:399`, `backend/rbac_backend/services/tag_service.py:453-459`, `backend/rbac_backend/services/tag_service.py:486-526` | Usage counts now check tag/sub-tag IDs, names, string IDs, ObjectIds, canonical `subTags`, and legacy lower/singular variants. |
| 5 | Add empty-state messaging for zero tags | Fixed | `client/src/pages/TagsPage.tsx:739-756` | Empty list and empty search states now show clear messaging. |
| 6 | Add per-sub-tag operation loading and prevent duplicate actions | Fixed | `client/src/pages/TagsPage.tsx:67-86`, `client/src/pages/TagsPage.tsx:161-208`, `client/src/pages/TagsPage.tsx:237-288`, `client/src/pages/TagsPage.tsx:326-358`, `client/src/pages/TagsPage.tsx:440-466`, `client/src/pages/TagsPage.tsx:613-666` | Sub-tag add/update/delete actions now have keyed loading state and disabled buttons while requests are in flight. |
| 7 | Confirm deletion before deleting unused tags/sub-tags | Fixed | `client/src/pages/TagsPage.tsx:300-329`, `client/src/pages/TagsPage.tsx:333-358` | UI prompts for confirmation before tag or sub-tag delete requests. Backend still blocks deletion when usage exists. |
| 8 | Check and correct issue in adding subtags | Fixed | `client/src/pages/TagsPage.tsx:161-208`, `backend/rbac_backend/services/tag_service.py:307-319` | Frontend trims/validates names, prevents duplicate submit clicks, checks loaded duplicates, reloads the sub-tag list after create, and backend duplicate lookup is now case-insensitive within the parent tag. |

TagsPage validation:
- Backend syntax check passed: `python -m py_compile backend\rbac_backend\services\authorization_service.py backend\rbac_backend\services\tag_service.py backend\rbac_backend\routers\tags.py`.
- Frontend production build passed: `npm run build` from `client`.
- Existing unrelated build warnings remain: outdated Browserslist data, `pdfjs-dist` eval warning, mixed static/dynamic `LoginPage` import, and large chunk warnings.

## TagsPage Subtag Typing Follow-up

Updated on 2026-05-07 after auditing the "Enter subtag name" field.

Expected behavior:
- Expanding a tag should keep the subtag input mounted.
- Typing in `Enter subtag name` should update only `newSubTagNames[tagId]` and preserve focus on every keystroke.
- Clicking `Add Subtag` should trim the current value, validate it, prevent duplicate submits, create the subtag, reload the parent tag's subtags, clear the input, and keep the tag expanded.

Present behavior before this follow-up:
- The add path in `client/src/pages/TagsPage.tsx:addSubTag` was functionally correct, but the row renderer was not stable.
- `TagActions`, `SubTagActions`, and `TagItem` were declared as React component types inside `TagsPage`.
- Every keystroke in `newSubTagNames` re-rendered `TagsPage`, recreated those component functions, and could cause React to remount the expanded tag row. That remount can drop focus from the controlled subtag input, making typing appear broken or interrupted.

Fix applied:
- Replaced nested component usage with render functions so the expanded row and input are not remounted due to changing component type identity.
- References: `client/src/pages/TagsPage.tsx:466-658`, `client/src/pages/TagsPage.tsx:754`.

Validation:
- Frontend production build passed: `npm run build` from `client`.
- Existing unrelated build warnings remain: outdated Browserslist data, `pdfjs-dist` eval warning, mixed static/dynamic `LoginPage` import, and large chunk warnings.
