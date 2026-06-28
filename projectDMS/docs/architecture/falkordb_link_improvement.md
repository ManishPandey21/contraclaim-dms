# FalkorDB Integration Improvement Plan

This plan addresses the high- and medium-priority risks identified in the FalkorDB Reference-Link Audit. All findings have been validated against the codebase.

## Validation Summary

- ✅ **FalkorDB schema index**: Confirmed missing `Letter.normCode` index in `falkor_graph_service.py`.
- ✅ **Reference resolution scope**: Confirmed `_resolve_target_document` does not scope by `organization_id` or `project_id`.
- ✅ **Manual reference replacement**: Confirmed `sync_bidirectional(source="manual")` replaces all existing manual links with the single new link in `document_service.py`.
- ✅ **Graph sync on mutation**: Confirmed manual add/remove, `update_document`, and soft deletes do not automatically resync FalkorDB.
- ✅ **Parser cleanup**: Confirmed `cleanup` in `upsert_letter_with_refs` only runs if `ref_list` is non-empty.

## Design Decisions & Open Questions

> [!IMPORTANT]
> - **Soft Deletion**: We need to decide if soft-deleted documents should be completely removed from FalkorDB (`DELETE`), or if they should be marked with a property (e.g., `is_deleted=true`) to preserve graph structure but filter them out during traversal. By default, we will mark them as deleted so the edge history remains but traverses can ignore them.
> - **Scope Enforcement**: Enforcing `organization_id` and `project_id` on reference resolution will restrict documents from incorrectly linking to duplicate reference codes from other organizations. This is highly recommended.
> - **Graph Ingestion Service**: To trigger FalkorDB updates from `document_service.py` mutations, we will import `GraphIngestionService` and invoke `sync_document_to_falkor` where appropriate.

## Proposed Changes

---

### 1. Schema Updates

#### `backend/rbac_backend/services/falkor_graph_service.py`
- In `ensure_schema`, add `"CREATE INDEX FOR (n:Letter) ON (n.normCode)"` to the `index_queries` list to prevent performance degradation on upserts and reads.

### 2. Synchronization Consistency & Edge Cleanup

#### `backend/rbac_backend/services/falkor_graph_service.py`
- In `upsert_letter_with_refs`, modify the cleanup condition to run even if `ref_list` is empty. This ensures stale edges are removed if all parser references are deleted.
  ```python
  # Change:
  # if (cleanup if cleanup is not None else self.cleanup_enabled) and ref_list:
  # To:
  if (cleanup if cleanup is not None else self.cleanup_enabled):
  ```

### 3. Tenant & Project Scoping for Reference Resolution

#### `backend/rbac_backend/services/reference_sync_service.py`
- Update `_resolve_target_document` to accept `organization_id` and `project_id` arguments.
- Include these tenant scoping arguments in the MongoDB `documents.find_one` queries within `_resolve_target_document` to prevent cross-tenant reference leakage.
- Update `sync_bidirectional` to pass `organization_id` and `project_id` from the `source_doc` to `_resolve_target_document`.

### 4. Preserving Manual References

#### `backend/rbac_backend/services/document_service.py`
- **`add_reference`**: Instead of passing only the new reference payload to `sync_bidirectional`, fetch existing `manual` references from the document. Append the new reference (if it's not a duplicate), and pass the combined list. This prevents `sync_bidirectional` from overwriting all prior manual links.

### 5. Keeping FalkorDB Up-to-Date on Mutations

#### `backend/rbac_backend/services/document_service.py`
- **`add_reference` / `remove_reference`**: After successfully adding or removing a reference in MongoDB, trigger `GraphIngestionService.sync_document_to_falkor` to ensure FalkorDB correctly reflects the user's manual link changes.
- **`update_document`**: Compare the pre-update document state with the post-update document state. If critical fields are modified (`letterNo`, `reference`, `references`, `previous_letter_id`, `organization_id`, `project_id`), trigger a FalkorDB resync.
- **`delete_document`**: When a document is soft-deleted, call `FalkorGraphService` to either remove the node entirely or set `is_deleted=true` to exclude it from active graph transversals.

## Verification & Testing Plan

### Automated Tests
- Run existing unit tests for `reference_sync_service.py` to ensure bidirectional linking functions properly with tenant scoping.
- Write/update tests for `DocumentService` to verify that `add_reference` merges manual references instead of replacing them.

### Manual Verification
1. **Scoping**: Upload two documents with the identical `letterNo` but in different organizations. Verify they do not automatically link to each other.
2. **Manual Links**: Add a manual reference to a document, then add a second manual reference. Verify both manual references are preserved in Mongo and FalkorDB.
3. **Graph Sync**: Edit a document's `letterNo` and verify the `normCode` updates immediately in FalkorDB without requiring full reprocessing.
4. **Soft Delete**: Soft delete a document and ensure it no longer appears in FalkorDB linked-chain reports or traverses.
5. **Edge Cleanup**: Remove all parsed references from a document and ensure its outgoing `CITES` edges are properly cleaned up in FalkorDB.
