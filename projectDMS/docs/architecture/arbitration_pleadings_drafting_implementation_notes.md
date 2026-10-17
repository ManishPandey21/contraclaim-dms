# Arbitration Pleadings Drafting Implementation Notes

## Implemented Slice

This implementation adds the first production-ready foundation for Arbitration Drafting:

- Mongo-backed draft, version, selected reference, claim head, paragraph response, and generation run models.
- Backend API under `/api/arbitration`.
- Dedicated RBAC permissions under `dms.arbitration.*`.
- Source-ledger based deterministic pleading generation with `[Evidence required]` safeguards.
- SoC paragraph import for Statement of Defence and SoD paragraph import for Rejoinder.
- DOCX/PDF export through existing `python-docx` and `reportlab` infrastructure.
- Frontend route/sidebar integration and a usable saved-drafts/create/detail workflow.

The generator is intentionally deterministic in this slice. A later LLM layer can replace section body generation while preserving the same source ledger, validator, versioning, export, and RBAC contracts.

## API Surface

- `GET /api/arbitration/drafts`
- `POST /api/arbitration/drafts`
- `GET /api/arbitration/drafts/{draft_id}`
- `PATCH /api/arbitration/drafts/{draft_id}`
- `DELETE /api/arbitration/drafts/{draft_id}`
- `POST /api/arbitration/drafts/{draft_id}/evidence/search`
- `POST /api/arbitration/drafts/{draft_id}/evidence/refresh`
- `GET /api/arbitration/drafts/{draft_id}/source-ledger`
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/import-soc`
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/import-defence`
- `POST /api/arbitration/drafts/{draft_id}/paragraph-responses/generate`
- `POST /api/arbitration/drafts/{draft_id}/generate`
- `POST /api/arbitration/drafts/{draft_id}/sections/{section_key}/regenerate`
- `POST /api/arbitration/drafts/{draft_id}/versions`
- `GET /api/arbitration/drafts/{draft_id}/versions`
- `GET /api/arbitration/drafts/{draft_id}/versions/{version}`
- `POST /api/arbitration/drafts/{draft_id}/approve`
- `POST /api/arbitration/drafts/{draft_id}/return-for-revision`
- `GET /api/arbitration/drafts/{draft_id}/runs/{run_id}`
- `GET /api/arbitration/drafts/{draft_id}/audit`
- `GET /api/arbitration/drafts/{draft_id}/export/docx`
- `GET /api/arbitration/drafts/{draft_id}/export/pdf`

## Permissions

- `dms.arbitration.view`
- `dms.arbitration.create`
- `dms.arbitration.edit`
- `dms.arbitration.generate`
- `dms.arbitration.export`
- `dms.arbitration.approve`
- `dms.arbitration.audit`
- `dms.arbitration.admin`

Default DMS admin roles receive these through `CLIENT_DMS_PERMISSIONS`.

## Database Migration Notes

The repo uses idempotent Mongo index setup in `backend/rbac_backend/core/database.py`. New indexes:

- `arbitration_drafts`: `(organization_id, project_id, contract_id, draft_type, status)`
- `arbitration_drafts`: `(organization_id, project_id, created_at)`
- `arbitration_draft_versions`: `(draft_id, version)` unique
- `arbitration_selected_references`: `(draft_id, source_type, source_id)`
- `arbitration_claim_heads`: `(draft_id, head_type)`
- `arbitration_paragraph_responses`: `(draft_id, source_paragraph_number)`
- `arbitration_generation_runs`: `(draft_id, created_at)`

No destructive migration is required.

## Validation

Backend:

- `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py backend/rbac_backend/tests/test_permission_catalog.py backend/rbac_backend/tests/test_route_inventory.py`
- `python -m py_compile` over new arbitration backend modules.

Frontend:

- `npm test -- --run src/config/__tests__/routeInventory.test.ts src/config/__tests__/rolePermissions.sidebar.test.ts`

Full frontend `tsc` still fails on unrelated pre-existing issues in legacy files. No remaining TypeScript errors are reported for `ArbitrationDraftingPage.tsx`.
