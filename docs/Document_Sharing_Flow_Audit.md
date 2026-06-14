# Document Sharing Flow Audit

Date: 2026-05-07

## Scope

Audited the share workflow centered on:

- `client/src/pages/ShareDocumentPage.tsx`
- `client/src/services/email-service.ts`
- `backend/rbac_backend/routers/email_share.py`
- `backend/rbac_backend/services/email_service.py`
- Email delivery configuration examples

## Executive Summary

The previous sharing flow could send an email, but the primary document link pointed to the authenticated application viewer. External recipients without a ContraClaim login could not reliably download the shared document. The UI also exposed "Include letter link" as a single checkbox instead of explicit delivery choices, and the HTML email template was assembled in the frontend with brittle backend string replacement.

The improved flow now supports:

- Secure public document download through opaque share tokens.
- Explicit delivery options: "Share via link" and "Attach file to email".
- Both delivery methods in the same share.
- Backend-rendered branded HTML email with CTA buttons and mobile responsive layout.
- Attachment size control and no-login download endpoint.
- Better recipient validation and safer email group handling.

## Ratings

| Area | Before | After |
| --- | ---: | ---: |
| Public recipient usability | 3/10 | 8/10 |
| Delivery option clarity | 4/10 | 9/10 |
| Security model | 5/10 | 8/10 |
| Email presentation | 5/10 | 8/10 |
| Frontend/backend separation | 4/10 | 8/10 |
| Overall process score | 4/10 | 8/10 |

## Key Findings Before Improvement

1. Public download was not actually public.
   The generated link used `/documentviewer/:id`, which depends on frontend app access and authenticated API calls.

2. Delivery options were ambiguous.
   The UI had "Include letter link" but no separate "attach file" option.

3. HTML template ownership was inverted.
   `ShareDocumentPage.tsx` embedded a full email document, while the backend attempted to replace a hard-coded placeholder block.

4. Attachments were counted but not sent.
   The backend response implied attachment counts based on links/references, while the SMTP service had no attachment support.

5. Email group handling could re-add group recipients to `To`.
   The frontend already expands selected groups into To/CC/BCC, but also sent `group_ids`, causing backend group members to be appended to `To`.

6. Invalid pending recipient input could be silently dropped.
   The submit handler filtered invalid pending input before validation.

## Implemented Changes

### Frontend

- Added explicit delivery options:
  - `shareViaLink` defaults to true.
  - `attachFileToEmail` defaults to false.
  - Validation prevents submitting with both disabled.
- Updated API payload to send:
  - `share_via_link`
  - `attach_file_to_email`
- Improved pending recipient validation before dedupe.
- Stopped sending `group_ids` from the page after group recipients are expanded locally, preventing accidental To-list mutation.
- Updated success messaging to reflect link and attachment delivery.
- Kept HTML as editable message content while moving the full branded email shell to the backend.

### Backend

- Added `share_via_link` and `attach_file_to_email` to `DocumentShareRequest`.
- Added a secure token store in `document_share_tokens`.
  - Only SHA-256 token hashes are stored.
  - Tokens expire via `DOCUMENT_SHARE_TOKEN_TTL_DAYS`.
  - Access count and last access timestamp are tracked.
- Added unauthenticated endpoint:
  - `GET /api/email/public-share/{token}/download`
- Public endpoint validates:
  - token shape
  - token hash lookup
  - expiry
  - revocation status
  - file availability
- Added attachment support to `EmailService.send_share_email`.
- Added configurable attachment limit:
  - `SHARE_EMAIL_ATTACHMENT_MAX_MB`
- Added backend-rendered modern HTML email with:
  - ContraClaim branding
  - responsive layout
  - primary "Download document" CTA
  - secondary "Open in DMS" CTA
  - metadata section
  - plain text fallback
- Updated sender configuration to honor `SMTP_FROM_EMAIL`.
- Added public API configuration examples:
  - `PUBLIC_API_URL`
  - `DOCUMENT_SHARE_TOKEN_TTL_DAYS`
  - `SHARE_EMAIL_ATTACHMENT_MAX_MB`

## Public Download Endpoint

The new endpoint is intentionally unauthenticated:

```text
GET /api/email/public-share/{token}/download
```

Security controls:

- Uses high entropy opaque tokens.
- Stores only token hashes in MongoDB.
- Requires non-expired, non-revoked token records.
- Does not expose document IDs in the URL.
- Serves with `Content-Disposition: attachment`.
- Uses `Cache-Control: private, no-store`.
- Validates local file paths through existing storage root checks.

Recommended follow-up:

- Add an admin revoke endpoint for individual share tokens.
- Add a TTL index on `document_share_tokens.expires_at`.
- Add rate limiting by IP/token prefix for public download attempts.

## Phase-Wise Improvement Plan

### Phase 1 - Completed

- Confirmed authenticated viewer links were not suitable for public recipients.
- Added token-based public download.
- Added explicit delivery options.
- Added attachment support.
- Improved email template ownership and rendering.
- Improved recipient validation and group expansion behavior.

### Phase 2 - Recommended Next

- Add database indexes:
  - unique index on `document_share_tokens.token_hash`
  - TTL index on `document_share_tokens.expires_at`
- Add public download audit events.
- Add share-token revocation UI/API.
- Add tests for:
  - link-only share
  - attachment-only share
  - link plus attachment share
  - expired token download
  - invalid token download
  - oversized attachment rejection

### Phase 3 - Recommended Later

- Convert reference and enclosure links to public token links when they are included in external emails.
- Add optional recipient passcode or per-recipient token binding for sensitive shares.
- Add organization-level policy controls:
  - disable public links
  - set max expiry
  - force attachment-only or link-only
  - require download watermarking
- Add email delivery logs for share emails, not only notification emails.

## Backend Requirements

Environment:

```text
APP_URL=http://localhost:5173
PUBLIC_API_URL=http://localhost:8000/api
DOCUMENT_SHARE_TOKEN_TTL_DAYS=30
SHARE_EMAIL_ATTACHMENT_MAX_MB=15
SMTP_FROM_EMAIL=noreply@example.com
```

Database:

```javascript
db.document_share_tokens.createIndex({ token_hash: 1 }, { unique: true })
db.document_share_tokens.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
db.document_share_tokens.createIndex({ document_id: 1, created_at: -1 })
```

Operational:

- `PUBLIC_API_URL` must be the externally reachable backend API base, including `/api`.
- SMTP provider must allow the configured attachment size.
- Public API route must be reachable from email recipients.
- If deployed behind a reverse proxy, preserve the `/api/email/public-share/.../download` route.

## Residual Risks

- Public links are bearer tokens. Anyone with the email/link can download until expiry or revocation.
- Reference and enclosure links currently remain app-view links unless separately enhanced.
- Public download responses currently materialize file bytes before responding; large files should be streamed in a later pass.
- No automated tests were added in this pass.

## Verification

- Python compile check passed for updated backend files.
- `npm run build` passed for the frontend.
