# Phase 7 - Collaboration Safety and Auditability

Date: 03 May 2026

## Implemented

- Added optional optimistic concurrency for document update/delete using:
  - `If-Match: "<revision>"`
  - `X-Document-Revision: <revision>`
- Added `ETag` and `X-Document-Revision` headers on document read/update responses.
- Added internal `_revision` counter on document records without changing response bodies.
- Added HTTP 409 conflict response when a stale client submits an old revision.
- Added document audit history endpoint:
  - `GET /api/documents/{id}/audit-events`
- Added comment audit events through `document.comment_added`.
- Expanded audit indexes for actor and resource history lookup.

## Client Policy

Existing clients can keep using the current API without sending revision headers. New collaborative clients should:

1. Read a document.
2. Store `ETag` or `X-Document-Revision` from the response headers.
3. Send that revision in `If-Match` or `X-Document-Revision` for update/delete.
4. If the API returns 409, refresh the document and show a conflict resolution UI.

## Audit Events

The audit trail now covers:

- `document.created`
- `document.updated`
- `document.deleted`
- `document.downloaded`
- `document.enclosure_added`
- `document.comment_added`

Audit records are append-only at the application layer and stored in `document_audit_events`.

## Remaining Work

- Add explicit field-level merge support for high-conflict metadata fields.
- Add document edit locks for long-running edits if product workflow requires them.
- Expose version history UI and audit history UI in the frontend.
- Add more audit events for reference add/remove and enclosure remove.
- Add end-to-end concurrent browser workflow tests.
