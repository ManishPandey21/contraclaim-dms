# RBAC Audit Report

Date: 2026-05-18

Scope:
- Documents page draft request flow.
- Letter Workflow Management tab visibility.
- Client User access expectations.
- Final draft approval notification by email.
- Backend RBAC and workflow transition enforcement.

## Executive Summary

The current RBAC implementation is not yet production-safe for the client approval workflow. The UI allows the expected draft request and letter workflow navigation in many cases, but the underlying role model and backend workflow transition checks are too broad or incomplete.

Client User access should be supported, but it should be permission-scoped rather than granted by showing all workflow tabs and actions. A Client User should be able to request drafts, view their scoped draft requests, review final approval items, and see completed letters. They should not be able to draft, edit strategy, perform internal review, or approve outside their assigned scope.

## Findings

### 1. Client User Is Not a First-Class Frontend Role

Active frontend route roles are:
- `superadmin`
- `orgadmin`
- `orguser`
- `projectadmin`
- `projectuser`

The active route RBAC config does not define a separate `clientuser` role. Backend `CurrentUser.account_type` defaults to `client_user`, but that is not equivalent to a role and is not used by frontend route guards.

Relevant file:
- `client/src/config/rolePermissions.ts`

Risk:
- A user considered "Client User" by business terminology may not receive consistent UI access unless mapped to `orguser` or `projectuser`.
- If a future `clientuser` role is added only in backend, `/letters` may still be blocked by frontend route rules.

### 2. DocumentsPage Allows Draft Request Flow, But Permission Is Too Broad

`DocumentsPage.tsx` has a `Request Draft` action that calls:

```text
POST /documents/{id}/request-draft
```

The frontend optimistically marks the document as `Under Process` and navigates to:

```text
/letters?requestDraftForDocumentId={docId}
```

Relevant files:
- `client/src/pages/DocumentsPage.tsx`
- `client/src/services/enhanced-api.ts`
- `backend/rbac_backend/routers/documents.py`

Backend endpoint currently requires only:

```text
documents:read
```

Risk:
- A read-only user can reserve a document for drafting.
- The document is marked `Under Process` before a durable draft request or letter is guaranteed.
- If the user closes the initiation dialog or creation fails, the document can remain blocked as `Under Process`.

### 3. No Explicit `Draft Requested` Status in Letter Workflow

Frontend `LetterStatus` currently includes:

```text
Draft, Input, Strategy, Review, Approval, Completed, Rejected
```

There is no explicit `Draft Requested` status. Backend notification logic maps draft request events to `DRAFT_REQUESTED`, but the UI workflow does not expose that as a first-class tab or lifecycle state.

Relevant files:
- `client/src/hooks/useLetterWorkflow.ts`
- `backend/rbac_backend/services/letter_service.py`

Risk:
- Draft request tracking is mixed with `Draft` or document `Under Process` status.
- Client users cannot clearly see "Draft Requested" as a queue/state.

### 4. Letter Workflow Tabs Are Static, Not Permission-Based

The tabs are hard-coded:

```text
All Letters, Input, Strategy, Draft, Review, Approval, Completed
```

Relevant file:
- `client/src/components/letter-workflow/LetterWorkflowTabs.tsx`

Risk:
- All users with `/letters` route access see the same workflow stages.
- Visibility and actions are not separated. A user may see workflow areas they should not operate in.

### 5. Backend Status Transition Endpoints Need Stronger Authorization

The following endpoints authenticate the user but do not perform enough workflow permission checks before status change:

```text
POST /letters/{letter_id}/submit
POST /letters/{letter_id}/approve
POST /letters/{letter_id}/complete
```

Relevant file:
- `backend/rbac_backend/routers/letters.py`

Specific issue:
- `approve` and `complete` pass `current_user` as the positional `comment` argument to `change_status`, instead of passing `user_id`.

Risk:
- Status transitions may be possible for users who should only have read access.
- Status history actor tracking is unreliable for approval/completion.

### 6. Approval Email Is Not Sent for Approval Assignment

Notification events support `APPROVAL_ASSIGNED`, `APPROVAL_COMPLETED`, and `APPROVAL_REJECTED`.

However, immediate email events currently include:

```text
DRAFT_APPROVED
DRAFT_REJECTED
NEW_UPLOAD
BULK_UPLOAD_COMPLETED
```

`APPROVAL_ASSIGNED` is not included, so final draft approval may appear as in-app notification but not be sent by email.

Relevant files:
- `backend/rbac_backend/utils/notification_service.py`
- `backend/rbac_backend/services/email_service.py`

Risk:
- Final draft may not reach the approver by email.
- Client approver can miss approval tasks unless actively using the app.

## Recommendation on Client User Tab Access

Client User should be allowed access to the following tabs, but only within organization/project scope:

- `All Letters`
- `Draft Requested`
- `Approval`
- `Completed`

Client User should not automatically receive access to:

- `Input`
- `Strategy`
- `Draft`
- `Review`

Those should be granted only if the user also has explicit internal workflow permissions.

Recommended model:

| Area | Client User Default | Notes |
| --- | --- | --- |
| All Letters | Yes, read-only scoped | Shows only permitted org/project letters. |
| Draft Requested | Yes | Shows draft requests created by or visible to client. |
| Approval | Yes | View final drafts pending approval. Approve only with permission. |
| Completed | Yes | Read-only completed correspondence. |
| Input | No by default | Only if client input is requested. |
| Strategy | No | Internal drafting strategy should remain internal. |
| Draft | No by default | Internal drafter workspace. |
| Review | No by default | Internal review workspace. |

## Proposed Permission Model

Add or standardize these permissions:

```text
drafting.request.create
drafting.request.view
drafting.letter.view_scoped
drafting.approval.view
drafting.approval.approve
drafting.completed.view
drafting.draft.edit
drafting.review.perform
drafting.strategy.manage
```

Suggested assignment:

| Permission | Client User | Drafter | Reviewer | Admin |
| --- | --- | --- | --- | --- |
| `drafting.request.create` | Yes | Optional | Optional | Yes |
| `drafting.request.view` | Yes | Yes | Yes | Yes |
| `drafting.letter.view_scoped` | Yes | Yes | Yes | Yes |
| `drafting.approval.view` | Yes | Yes | Yes | Yes |
| `drafting.approval.approve` | Optional approver only | No | Optional | Yes |
| `drafting.completed.view` | Yes | Yes | Yes | Yes |
| `drafting.draft.edit` | No | Yes | No | Yes |
| `drafting.review.perform` | No | No | Yes | Yes |
| `drafting.strategy.manage` | No | Optional | Optional | Yes |

## Implementation Plan

### Phase 1: Role and Permission Cleanup

1. Decide whether to create explicit `clientuser` or map Client User to `orguser/projectuser`.
2. If explicit `clientuser` is used:
   - Add it to backend role aliases.
   - Add it to default roles.
   - Add it to frontend `Roles`.
   - Add route allowance for `/letters`.
3. Add the drafting permissions listed above.
4. Migrate seeded roles and existing users.

### Phase 2: Draft Request Lifecycle

1. Replace the current two-step reserve flow with an atomic backend operation:
   - Create draft request or letter with status `Draft Requested`.
   - Link source document.
   - Mark source document `Under Process` only after successful creation.
2. Change `/documents/{id}/request-draft` to require `drafting.request.create`.
3. Add rollback/release endpoint if a draft request is cancelled.
4. Add tests for duplicate draft request prevention.

### Phase 3: Workflow Status and Tabs

1. Add `Draft Requested` to frontend and backend status handling.
2. Add a `Draft Requested` tab or request queue.
3. Make tab rendering permission-driven:
   - Client User: `All Letters`, `Draft Requested`, `Approval`, `Completed`.
   - Drafter: `Draft`, `Review`, `Completed`.
   - Reviewer: `Review`, `Approval`, `Completed`.
   - Admin: all tabs.
4. Keep tab visibility separate from action permissions.

### Phase 4: Backend Transition Authorization

1. Add authorization checks before every status transition:
   - Submit for review: `drafting.draft.submit_for_review`.
   - Move to approval: `drafting.review.approve`.
   - Complete/approve final: `drafting.approval.approve`.
2. Call `check_letter_access` for the target letter before status changes.
3. Fix `change_status` calls:

```python
await controller.letter_service.change_status(
    letter_id,
    "Approval",
    user_id=getattr(current_user, "id", None),
)
```

4. Add audit events for draft request, approval assignment, approval complete, and rejection.

### Phase 5: Final Draft Approval Email

1. Add `APPROVAL_ASSIGNED` to immediate email events.
2. Resolve recipients from:
   - assigned final approver,
   - project/org client approver role,
   - explicit approval chain configuration.
3. Include in the email:
   - letter title,
   - subject,
   - project,
   - approval link,
   - final draft preview or export link.
4. Add delivery log checks and tests.

### Phase 6: Test Coverage

Add tests for:

1. Client User can request draft only with `drafting.request.create`.
2. Client User can see scoped `All Letters`, `Draft Requested`, `Approval`, `Completed`.
3. Client User cannot edit `Strategy`, `Draft`, or `Review`.
4. Client approver can approve only scoped approval-stage letters.
5. Unauthorized status transitions return `403`.
6. Approval assignment emits in-app and email notification.
7. Draft request failure does not leave document stuck as `Under Process`.

## Final Recommendation

Grant Client User access to `All Letters`, `Draft Requested`, `Approval`, and `Completed`, but make this read-only and scope-bound unless explicit approval permission is assigned.

Do not grant Client User broad access to `Draft`, `Strategy`, or `Review`. These are internal preparation stages and should remain available only to assigned drafting/review roles.
