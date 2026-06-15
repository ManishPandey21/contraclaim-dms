# Health Page Improvement Todo

## Goal

Improve the superadmin Health page so it tracks database connectivity, storage/vector/graph health, document/file reconciliation, and repair actions with clear operational safety.

## Phase 1 - Planning and Scope

- [x] Record current Health page capabilities.
- [x] Record missing capabilities and target backend/frontend changes.
- [x] Keep all controls superadmin-only.
- [x] Preserve existing storage sync and LangGraph configuration behavior.

## Phase 2 - Backend Health Contract

- [x] Add explicit MongoDB database health data to `/api/storage-sync/status`.
- [x] Include collection counts for documents, chunks, vector sync status, and reconciliation run records.
- [x] Include database ping latency and availability.
- [x] Enforce superadmin access on health, repair, and reconciliation routes.

## Phase 3 - Backend Reconciliation

- [x] Extend vector reconciliation with `dry_run`.
- [x] Persist vector reconciliation run results in `storage_reconciliation_runs`.
- [x] Add file/document reconciliation endpoint for missing files, orphan storage references, no-chunk documents, orphan chunks, stuck processing documents, and missing workspace metadata.
- [x] Persist file reconciliation run results.

## Phase 4 - Frontend Health Page

- [x] Reorganize Health page into tabs: Overview, Database, Reconciliation, Stores, Repairs, AI Config.
- [x] Add Database Connections panel.
- [x] Add Vector Reconciliation controls and result table.
- [x] Add File/Document Reconciliation controls and result table.
- [x] Add confirmation before bulk repair and reconciliation actions.
- [x] Add auto-refresh interval control.
- [x] Keep LLM drafting controls available under AI Config.

## Phase 5 - Verification

- [x] Run backend compile/tests for storage sync and app import.
- [x] Run frontend production build.
- [x] Record implemented changes and any residual risks.

## Implementation Report

Completed.

### Backend Implemented

- Updated `backend/rbac_backend/routers/storage_sync.py`.
- Added superadmin-only dependency enforcement on:
  - `GET /api/storage-sync/status`
  - `POST /api/storage-sync/resync-doc`
  - `POST /api/storage-sync/resync-bulk`
  - `POST /api/storage-sync/reconcile`
  - `POST /api/storage-sync/reconcile-files`
- Added explicit MongoDB health block to `/storage-sync/status`:
  - availability
  - ping latency
  - database name
  - collection counts for documents, chunks, document vectors, vector sync status, and reconciliation runs
  - error reporting when ping/counts fail
- Extended `POST /storage-sync/reconcile` with `dry_run`.
  - Dry run reports `would_repair` rows without re-embedding/upserting.
  - Non-dry-run keeps existing repair behavior.
- Added reconciliation persistence in `storage_reconciliation_runs`.
- Added `POST /storage-sync/reconcile-files` for diagnostic file/document reconciliation.
  - Reports missing local files.
  - Reports missing storage references.
  - Reports storage location errors.
  - Reports documents without chunks.
  - Reports orphan chunks.
  - Reports documents stuck in processing statuses.
  - Reports missing organization/project metadata.
  - Reports duplicate file hash candidates where hash fields exist.
- Updated `backend/rbac_backend/core/database.py` with indexes for reconciliation run history.

### Frontend Implemented

- Rebuilt `client/src/pages/HealthPage.tsx` into tabbed superadmin operations:
  - Overview
  - Database
  - Reconciliation
  - Stores
  - Repairs
  - AI Config
- Added database connection panel.
- Added vector reconciliation UI with:
  - organization/project scope
  - limit
  - dry-run toggle
  - result summary
  - detail table
- Added file/document reconciliation UI with:
  - organization/project scope
  - limit
  - issue count summary
  - detail table
- Added bulk repair confirmation.
- Added repair/reconciliation scoped controls.
- Added auto-refresh selector.
- Kept existing LangGraph model/prompt controls under the AI Config tab.

### Verification Completed

- Backend compile check passed:
  - `python -m py_compile backend\rbac_backend\routers\storage_sync.py backend\rbac_backend\core\database.py`
- Frontend production build passed:
  - `npm run build` from `client`

### Residual Operational Notes

- File reconciliation is diagnostic only. It does not delete orphan chunks or repair storage records automatically.
- Local file existence checks only validate `filepath_local`; S3/object-store existence checks should be added later through storage provider clients.
- Reconciliation history is persisted, but no history browser UI was added in this phase.
- Confirmation currently uses `window.confirm`; a richer modal can be added later for better UX and audit detail.
