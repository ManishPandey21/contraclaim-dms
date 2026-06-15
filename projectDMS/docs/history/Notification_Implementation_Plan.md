# ContraClaim DMS Notification System – Present Status & Phase-wise Implementation Plan

## 1. Executive Summary

ContraClaim DMS currently has **partial notification support**. The backend already defines a persisted `Notification` model, notification categories and event types, a wired notification service, notification API routes, WebSocket delivery, basic email fan-out, daily/weekly digest jobs, document upload events, bulk upload summary events, and some letter workflow/comment events.

The current implementation is useful as a foundation, but it is not yet a complete notification system. It lacks granular user preferences, project-level subscriptions, role-based defaults, action buttons, rich email templates, delivery logs, retry handling, browser notification permission handling, full notification center search/filtering, document deletion/archive/version triggers, document comment notifications, scheduled pending-reply reminders, configurable escalations, and audit/security alert integration.

Recommended approach: **extend the existing FastAPI + MongoDB + React notification path instead of replacing it**. Phase 1 should harden and expand the current model/service/API/UI foundation, then add email delivery logs and preferences before adding reminders, escalations, browser notifications, and security alerts.

## 2. Present Codebase Status

| Area | Present Status | Existing Files / Components | Gap |
|---|---|---|---|
| Backend notification models | Partial support exists. `Notification`, `NotificationType`, `NotificationCategory`, and `NotificationContext` are defined for persisted in-app notifications. | `backend/rbac_backend/models/notification.py` (`Notification`, `NotificationType`, `NotificationCategory`, `NotificationContext`, `NotificationResponse`, `NotificationListResponse`) | Needs expanded event taxonomy, actions, delivery metadata, expiration, priority/severity, dedupe keys, channel status, and preference-aware fields. |
| API routes | Basic authenticated notification APIs exist for list, unread count, mark read, and mark all read. | `backend/rbac_backend/routers/notifications.py` (`list_notifications`, `get_unread_count`, `mark_notification_read`, `mark_all_notifications_read`) | Missing preference APIs, project settings APIs, bulk operations, search/filter endpoints, action endpoint, delivery-log/admin endpoints, and email test endpoint. |
| Email sending utilities | Partial. Immediate email fan-out and daily/weekly digest emails exist. SMTP is env-configured. | `backend/rbac_backend/services/email_service.py` (`EmailService.send_immediate_notification`, `send_digest`, `send_daily_digests`, `send_weekly_digests`, `send_share_email`); `backend/rbac_backend/utils/notification_service.py` (`_fanout_emails`) | Emails are plain text, broad preference only, no rich templates, no per-event/channel preferences, no retry queue, no delivery log, no bounce/failure management. |
| In-app notification support | Partial. Notifications persist in MongoDB and can be read/unread. | `backend/rbac_backend/utils/notification_service.py` (`NotificationService.emit`, `list_notifications`, `mark_as_read`, `mark_all_as_read`, `count_unread`) | Needs priority, actions, grouping, dedupe, retention, archive/delete, full history view, and advanced filtering/search. |
| WebSocket / SSE / polling support | Partial. WebSocket delivery exists and frontend also polls every 30 seconds. | `backend/rbac_backend/routers/ws.py` (`/ws/notifications`); `backend/rbac_backend/utils/notification_service.py` (`ConnectionManager`); `client/src/contexts/NotificationContext.tsx` (`useWebSocketNotifications`); `client/src/components/NotificationCenter.tsx` polling | WebSocket route includes a dev/testing `user_id` fallback that should be removed or gated. No SSE fallback. Browser Notification API is not used. |
| Scheduler / cron / Celery / background jobs | Partial. APScheduler is used for daily and weekly digests. Generic in-process background jobs exist, but not for reminders/escalations. | `backend/rbac_backend/main.py` (`AsyncIOScheduler`, daily/weekly digest jobs); `backend/rbac_backend/services/background_jobs.py` (`BackgroundJobProcessor`) | No Celery/RQ/worker-backed reminder jobs, no 10/20/30 day pending reply checks, no escalation matrix job, no scheduler execution log. |
| Document upload events | Partial. Single document upload emits `NEW_UPLOAD`; bulk upload emits `BULK_UPLOAD_COMPLETED` summary. | `backend/rbac_backend/services/document_service.py` (`DocumentService.create_document`, `_build_upload_notification_data`, `_resolve_upload_notification_context`); `backend/rbac_backend/routers/documents.py` (`_process_bulk_upload`, `_process_single_file`) | Needs recipient preference checks, project subscription checks, richer links, delivery logs, and better dedupe for repeated uploads. |
| Workflow / approval events | Partial. Letter status changes map to draft saved/approved/rejected events. Assigned user can be included. | `backend/rbac_backend/services/letter_service.py` (`change_status`, `update_letter`, `_status_to_event`, `_emit_letter_event`, `create_letter_with_chain`) | Status mapping is broad and not workflow-specific enough. Needs explicit approval assigned/completed/rejected task events, action links, submitter notifications, and workflow step metadata. |
| Comment or annotation events | Partial for letters only. Letter comments emit `COMMENT_ADDED`. Document comments exist but do not emit notifications. Mentions/replies/resolved threads are not detected. | `backend/rbac_backend/services/letter_service.py` (`add_comment`); `backend/rbac_backend/services/document_service.py` (`add_comment`) | Needs document comment notifications, mention parsing, reply notifications, resolved-thread notifications, annotation model/event support, and per-thread subscriptions. |
| User roles and permissions | Strong RBAC foundation exists. Notification recipients are resolved through RBAC role maps and scope filters. | `backend/rbac_backend/services/rbac_service.py` (`RBACService.get_notifiable_users`, `_EVENT_ROLE_MAP`, role aliases); `backend/rbac_backend/models/permission.py` (`DEFAULT_PERMISSIONS`) | No notification-specific permissions such as `notifications:manage`, `notification-settings:manage`, or admin notification report permissions. |
| Project-level access control | Partial. Recipient resolution and document/letter access use organization/project scope. | `backend/rbac_backend/services/rbac_service.py`; `backend/rbac_backend/routers/documents.py` (`_ensure_document_access`); project/user fields in `backend/rbac_backend/models/user.py` | No project-level notification subscription settings or project admin notification defaults. |
| Audit logs | Existing audit logger records many auth, permission, user, role, project, and email events. | `backend/rbac_backend/utils/audit_logger.py` (`AuditLogger`, `log_login_failed`, `log_login_successful`, `log_permission_check`, `log_email_sent`, `log_email_failed`); `backend/rbac_backend/core/security.py`; `backend/rbac_backend/routers/auth.py` | Audit/security events do not generate notifications. Needs explicit bridge for unusual login, permission changes, storage warnings, and security alerts. |
| Frontend notification UI | Partial. A topbar bell/dropdown shows unread count, categories, mark read, mark all read, and basic navigation. | `client/src/components/NotificationCenter.tsx`; `client/src/components/layout/Navbar.tsx`; `client/src/contexts/NotificationContext.tsx`; `client/src/services/enhanced-api.ts`; `client/src/types/api.ts` | Needs full notification center page, search, filters, bulk selection, actions, settings pages, digest/reminder settings, project settings, and better routing for letter/workflow resources. |
| Browser notification permission handling | Not present. | No implementation found in `client/src/contexts/NotificationContext.tsx` or `client/src/components/NotificationCenter.tsx` | Needs permission prompt component, user opt-in state, foreground-only native notifications, fallback behavior, and browser compatibility handling. |
| Existing reminder or escalation logic | Not present for notifications. Input requests have `due_date` and statuses, and letter pendency is calculated, but no notification job uses them. | `backend/rbac_backend/models/input_request.py` (`InputRequest.due_date`, `InputRequestStatus`); `backend/rbac_backend/services/input_request_service.py`; `backend/rbac_backend/services/letter_service.py` (`statusStartDate`, `pendencyDays`) | Needs reminder rules, escalation rules, scheduler worker, reminder state tracking, no-duplicate windows, and escalation recipient resolution. |

## 3. Notification Types to Support

| Notification Feature | Existing Support | Required Backend Changes | Required Frontend Changes | Priority | Complexity |
|---|---|---|---|---|---|
| 1. Document Creation / Upload | Partial. Single upload and bulk summary notifications exist. | Extend `NotificationType`; apply project subscriptions/preferences; add richer document links and delivery logs. | Improve navigation from notification to document/project; show upload metadata. | Must Have | Medium |
| 2. Email Notifications | Partial. Plain-text immediate emails and digests exist. | Add HTML/text templates, direct action/document links, delivery logs, retries, per-channel preferences. | Add email preference controls and admin test email UI. | Must Have | Medium |
| 3. Desktop Browser Notifications | Not present. | Include browser channel preference and payload suitable for native display. | Add `BrowserNotificationPermissionPrompt`; call Browser Notification API only when app is open and permission is granted. | Could Have | Medium |
| 4. Granular Subscription Settings | Not present. | Add `ProjectNotificationSubscription`; enforce during recipient resolution. | Add `ProjectNotificationSettings` page/panel. | Must Have | Medium |
| 5. Event-Type Toggle | Not present. | Add per-event/channel preferences and default fallback resolution. | Add event toggle UI in `NotificationPreferencePage`. | Must Have | Medium |
| 6. Role-Based Defaults | Not present. | Add default rules by role/org/project and apply on user creation or first preference read. | Add admin settings UI for defaults. | Should Have | Medium |
| 7. Actionable Notifications | Not present. | Add action metadata, secure action endpoint, RBAC re-check before action execution, action audit logs. | Add `NotificationActionButtons` for approve/view/assign. | Should Have | High |
| 8. Unified Notification Center | Partial dropdown only. | Add search/filter/bulk APIs and pagination enhancements. | Add `NotificationCenterPage`, filters, bulk mark read, resource links. | Must Have | Medium |
| 9. Digest Summaries | Partial daily/weekly unread digest. | Add `NotificationDigestSetting`, digest templates, custom frequency/window, delivery logs. | Add `DigestSettingsPanel`. | Could Have | Medium |
| 10. Scheduled Reminders | Not present. | Add `ReminderRule`, scheduler job for 10/20/30+ day pending replies, reminder dedupe state. | Show reminder preferences and reminder notification type. | Must Have | High |
| 11. Escalation Reminders | Not present. | Add `EscalationRule`, escalation matrix engine, escalation recipient resolver. | Add `ReminderEscalationSettingsPage`. | Should Have | High |
| 12. In-App Notifications | Partial. Persisted notification center exists as dropdown. | Expand model/actions/filtering/read states; add retention and dedupe. | Add full center page and better dropdown. | Must Have | Medium |
| 13. Document Deletion / Archival | Not present. | Emit trash/archive/permanent-delete events from document operations. | Route notifications to document list/archive/trash views. | Should Have | Medium |
| 14. Version Updates | Not present for documents. Letter draft versions exist but no notification trigger. | Add document version events and optional letter draft version events. | Show version update notifications and route to version history. | Should Have | Medium |
| 15. Approval / Workflow Tasks | Partial letter status events. | Add explicit task assignment/review/approval/signature events and assignee targeting. | Add task action buttons and route to workflow page. | Must Have | High |
| 16. Approval / Rejection Outcome | Partial via `DRAFT_APPROVED`/`DRAFT_REJECTED`. | Notify submitter with comments and outcome details; add clear event types. | Render outcome details and comments. | Must Have | Medium |
| 17. Comments & Annotations | Partial for letter comments only. | Add document comment events, mentions, replies, resolved threads, annotation events. | Add comment/mention notification routing and thread highlighting. | Should Have | High |
| 18. System & Security Alerts | Audit logs exist but no notification bridge. | Add alert event generator for storage warnings, unusual login, permission changes, audit events. | Add admin/security alert filters and severity display. | Later | High |

## 4. Recommended Notification Architecture

Use an event-driven architecture that extends the current `NotificationService` path and gradually consolidates notification behavior into a single service layer.

- **Notification event service:** Add `NotificationEventService` as the canonical entry point for domain events. Existing callers such as `DocumentService.create_document`, bulk upload, `LetterService.change_status`, and `LetterService.add_comment` should publish domain event payloads to this service instead of manually shaping notification records over time.
- **Notification database collection/table:** Continue using MongoDB `notifications`, but expand the schema with event type, channels, action metadata, priority/severity, dedupe key, delivery status summary, resource links, and expiry/retention fields.
- **Notification preferences:** Add per-user event/channel preferences. Use existing `User.preferences.emailNotifications` only as a backward-compatible default.
- **Project subscription settings:** Add project-level subscription documents so users can subscribe/unsubscribe from project events and project admins can configure defaults.
- **Role-based default rules:** Add role/org/project defaults for new users and for users without explicit preferences.
- **Email delivery service:** Keep `EmailService`, but add template rendering, link generation, delivery logging, retry handling, and channel preference checks.
- **In-app notification center:** Keep persisted in-app notifications as the source of truth. Add search, filters, pagination, bulk actions, and action dispatch.
- **Browser push/browser notification strategy:** Start with foreground Browser Notification API while the app is open. Use WebSocket events to trigger native browser notifications after permission is granted. Defer service-worker push until a later roadmap.
- **Scheduler/reminder worker:** Use APScheduler initially because it already exists in `backend/rbac_backend/main.py`, but isolate jobs in a worker-friendly service so Celery/RQ can replace it later if scale requires.
- **Escalation matrix engine:** Add rules that evaluate pending input requests, workflow tasks, and overdue replies, then resolve escalation recipients through RBAC and project/role defaults.
- **Audit/security alert integration:** Add a bridge from `AuditLogger` or audit-event consumers to notification events for security-sensitive events. Avoid notifying directly inside low-level audit writes unless failures are isolated from request flow.

## 5. Suggested Database Models

### Notification

Important fields:

- `_id`
- `event_type`
- `category`
- `resource_type`
- `resource_id`
- `organization_id`
- `project_id`
- `context`
- `actor_id`
- `recipients`
- `read_by`
- `archived_by`
- `priority`
- `severity`
- `title`
- `message`
- `data`
- `actions`
- `channels_requested`
- `delivery_summary`
- `dedupe_key`
- `created_at`
- `expires_at`

### NotificationPreference

Important fields:

- `_id`
- `user_id`
- `organization_id`
- `default_channels`
- `event_settings`: map of event type to `{enabled, channels, digest_only}`
- `quiet_hours`
- `digest_enabled`
- `browser_notifications_enabled`
- `email_notifications_enabled`
- `created_at`
- `updated_at`

### ProjectNotificationSubscription

Important fields:

- `_id`
- `project_id`
- `organization_id`
- `user_id`
- `subscribed`
- `event_settings`
- `role_default_source`
- `created_at`
- `updated_at`

### NotificationTemplate

Important fields:

- `_id`
- `event_type`
- `channel`
- `locale`
- `subject_template`
- `title_template`
- `body_template`
- `html_template`
- `required_variables`
- `active`
- `created_by`
- `updated_at`

### NotificationDigestSetting

Important fields:

- `_id`
- `user_id`
- `frequency`
- `time_of_day`
- `timezone`
- `included_event_types`
- `included_projects`
- `last_sent_at`
- `enabled`
- `created_at`
- `updated_at`

### ReminderRule

Important fields:

- `_id`
- `name`
- `resource_type`
- `event_type`
- `days_pending`
- `channels`
- `recipient_strategy`
- `enabled`
- `dedupe_window_hours`
- `created_by`
- `updated_at`

### EscalationRule

Important fields:

- `_id`
- `name`
- `resource_type`
- `condition`
- `threshold_days`
- `from_role`
- `to_roles`
- `to_users`
- `channels`
- `message_template_id`
- `enabled`
- `created_by`
- `updated_at`

### NotificationDeliveryLog

Important fields:

- `_id`
- `notification_id`
- `user_id`
- `channel`
- `status`
- `provider`
- `provider_message_id`
- `attempt_count`
- `last_attempt_at`
- `next_retry_at`
- `error_code`
- `error_message`
- `created_at`
- `updated_at`

## 6. Suggested Backend APIs

| Endpoint | Purpose | Request / Response | Required RBAC |
|---|---|---|---|
| `GET /api/notifications` | List authenticated user's notifications with filters. | Query: `unread_only`, `category`, `event_type`, `project_id`, `search`, `limit`, `skip`. Response: paginated notifications. | Authenticated user; return only notifications where user is recipient. |
| `GET /api/notifications/unread-count` | Return unread count for current user. | Response: `{ "unread_count": number }`. | Authenticated user. |
| `PATCH /api/notifications/{id}/read` | Mark one notification as read. | Response: updated notification or success flag. | Authenticated recipient only. |
| `PATCH /api/notifications/mark-all-read` | Mark all current user's notifications as read, optionally filtered by project/category. | Body: optional filters. Response: modified count. | Authenticated user. |
| `POST /api/notifications/preferences` | Create/update current user's notification preferences. | Body: event/channel settings, digest settings, quiet hours. Response: saved preferences. | Authenticated user for own preferences; admin permission for managing another user's defaults. |
| `GET /api/projects/{project_id}/notification-settings` | Read project notification subscription/default settings. | Response: project settings plus current user's subscription state. | Project member can read own subscription; project/org admin can read defaults. |
| `PATCH /api/projects/{project_id}/notification-settings` | Update project subscription/default rules. | Body: subscription and event settings. Response: saved settings. | Project member can update own subscription; project/org admin required for project defaults. |
| `POST /api/notifications/{id}/action` | Execute an action such as approve, view, assign to me, or mark done. | Body: `{ "action": "approve", "payload": {} }`. Response: action result and updated notification state. | Notification recipient plus fresh RBAC check for the underlying resource/action. |
| `POST /api/notification-test/email` | Send test email to verify SMTP/template setup. | Body: target user/email, template/event type. Response: delivery log status. | Admin or `emails:send_notifications`/notification admin permission. |

Additional admin APIs should be added later for templates, delivery logs, reminder rules, escalation rules, and notification reports.

## 7. Suggested Frontend Components

| Component / Page | Purpose | Likely Integration Points |
|---|---|---|
| `NotificationBell` | Small bell with unread count and WebSocket/polling status. | Replace or extract from `client/src/components/NotificationCenter.tsx`; mount in `client/src/components/layout/Navbar.tsx`. |
| `NotificationDropdown` | Lightweight recent notifications menu with category filters and quick actions. | Extract current dropdown behavior from `NotificationCenter.tsx`. |
| `NotificationCenterPage` | Full-page searchable notification inbox with filters, bulk mark-read, pagination, and resource navigation. | Add route in `client/src/routes.tsx`, link from dropdown and sidebar/topbar. |
| `NotificationFilters` | Filter by unread, category, project, event type, date, priority, and channel. | Used by `NotificationCenterPage`. |
| `NotificationPreferencePage` | User-level event/channel toggles, digest settings, quiet hours, browser notifications. | Add under existing settings route/page, likely near `client/src/pages/SettingsPage.tsx`. |
| `ProjectNotificationSettings` | Project-level subscribe/unsubscribe and default rules. | Add to project settings area; integrate with project access control. |
| `BrowserNotificationPermissionPrompt` | Ask for native browser notification permission only when user opts in. | Mount from preferences page or dropdown, not globally on first load. |
| `NotificationActionButtons` | Render actions such as Approve, View Document, Assign to me, Mark done. | Used inside dropdown and full notification center. |
| `DigestSettingsPanel` | Configure daily/weekly/custom digest. | Used inside `NotificationPreferencePage`. |
| `ReminderEscalationSettingsPage` | Admin UI for 10/20/30 day reminders and escalation matrix. | Add under admin/settings route; require admin RBAC. |

Existing frontend points to preserve:

- `client/src/contexts/NotificationContext.tsx` should remain the store/WebSocket integration point, but expand it for preferences, action execution, full-page filters, and browser notification dispatch.
- `client/src/services/enhanced-api.ts` should add new notification API methods.
- `client/src/types/api.ts` should add new notification preference, action, delivery, and settings types.
- `client/src/components/layout/Navbar.tsx` should continue to host the topbar notification entry point.

## 8. Event Trigger Matrix

| Event | Trigger Source | Recipients | Channels | Action Required |
|---|---|---|---|---|
| Document uploaded | `DocumentService.create_document`; `documents.py` single upload route | Project subscribers, users with document view/upload awareness roles, assigned project users, excluding actor | In-app, WebSocket, email if enabled, browser if opted in | View Document |
| Document metadata updated | `DocumentService.update_document` | Project subscribers and document watchers; optionally document owner/assigned users | In-app, WebSocket, optional digest/email | View Document |
| Document deleted | `DocumentService.delete_document` | Project/org admins, document owner, affected watchers/subscribers | In-app, email for permanent delete | View Project/Trash |
| Document archived | Future archive endpoint/service | Project subscribers, document owner, project admins | In-app, optional email | View Archive |
| New document version uploaded | Future document version service | Watchers, project subscribers, assigned users | In-app, WebSocket, optional email | View Version |
| Version rollback | Future version rollback service | Watchers, project admins, document owner | In-app, email for high-risk rollback | View Version History |
| Draft requested | Letter creation/input request workflow | Assigned drafter/reviewer and project workflow participants | In-app, WebSocket, email if enabled | Start Draft |
| Approval assigned | Letter workflow status transition or task assignment | Assigned approver(s), backup approver role, project workflow admins | In-app, WebSocket, email | Approve / View Letter |
| Approval completed | Letter workflow approval completion | Submitter/requester, project subscribers, assigned workflow participants | In-app, WebSocket, optional email | View Outcome |
| Approval rejected | Letter workflow rejection | Submitter/requester, assigned drafter, workflow admins | In-app, WebSocket, email | Revise / View Comments |
| Comment added | `LetterService.add_comment`; future document comment service | Resource owner, assigned users, thread participants, mentioned users | In-app, WebSocket, optional email | View Comment |
| Mention added | Future mention parser in comment/annotation service | Mentioned users only, with RBAC access check | In-app, WebSocket, email if enabled | Reply / View Mention |
| Reply pending for 10 days | Scheduler reminder job over `InputRequest.due_date`, status, or letter pendency | Requested user, requester, assigned owner | In-app, optional email/digest | Reply |
| Reply pending for 20 days | Scheduler reminder job | Requested user, requester, project lead/manager by rule | In-app, email | Reply / Escalate |
| Reply pending beyond 30 days | Scheduler escalation job | Requested user, requester, configured escalation roles/admins | In-app, email, admin report | Resolve / Escalate |
| Permission changed | User/role/permission router/service or audit event bridge | Affected user, org admin/security admin | In-app, email for sensitive changes | Review Permissions |
| Unusual login | Auth/audit event bridge from failed/suspicious login detection | Affected user, security/admin users depending severity | Email, in-app for admins | Review Account |
| Storage threshold crossed | Storage monitoring job/service | Org/project admins, storage admins | In-app, email | Review Storage |

## 9. Phase-wise Implementation Plan

### Phase 1 – Foundation

| Item | Plan |
|---|---|
| Objective | Strengthen the existing notification foundation without replacing current APIs/UI. |
| Backend tasks | Expand event enum/category taxonomy; add `NotificationEventService`; keep compatibility with `NotificationService.emit`; add dedupe key, priority, action metadata, resource links, and retention fields; add document upload notification tests. |
| Frontend tasks | Split current `NotificationCenter` into bell/dropdown-friendly pieces only if needed; add full notification page route; improve notification navigation for documents and letters. |
| Database changes | Extend `notifications` schema; add indexes on recipient/unread/project/event/created_at/dedupe key. |
| Testing requirements | Unit tests for event creation, recipient resolution, read/unread, dedupe, upload notification payloads; API tests for list/count/read. |
| Acceptance criteria | Uploading a document creates one in-app notification for correct recipients; unread count updates; WebSocket refresh works; full notification page lists current user's notifications only. |

### Phase 2 – Email Notifications

| Item | Plan |
|---|---|
| Objective | Make email notifications reliable, traceable, and useful. |
| Backend tasks | Add `NotificationTemplate`; render HTML/text emails; add direct app links; add `NotificationDeliveryLog`; implement retry metadata and failure capture; add admin test email endpoint. |
| Frontend tasks | Add email preference visibility and admin test email UI. |
| Database changes | Add `notification_templates` and `notification_delivery_logs`; index by notification/user/channel/status. |
| Testing requirements | Unit tests for template rendering; SMTP-disabled behavior; delivery log success/failure; API test for email test endpoint. |
| Acceptance criteria | Immediate email events produce delivery logs, contain direct document/workflow links, and do not send when disabled. |

### Phase 3 – Preferences & Subscriptions

| Item | Plan |
|---|---|
| Objective | Allow users and project admins to control notification volume. |
| Backend tasks | Add `NotificationPreference`; add `ProjectNotificationSubscription`; enforce event/channel toggles during recipient/channel resolution; add project settings APIs; add role-based default rule support. |
| Frontend tasks | Add `NotificationPreferencePage`, `ProjectNotificationSettings`, event toggles, project subscribe/unsubscribe controls. |
| Database changes | Add `notification_preferences` and `project_notification_subscriptions`; index by user/project/org. |
| Testing requirements | RBAC tests for project settings; preference resolution tests; API tests for own preferences vs admin defaults. |
| Acceptance criteria | Users can disable a specific event/channel; project subscription changes affect future notifications; admins can set project/role defaults. |

### Phase 4 – Workflow & Actionable Notifications

| Item | Plan |
|---|---|
| Objective | Notify the right users for workflow tasks and allow safe inline actions. |
| Backend tasks | Add explicit events for approval assigned/completed/rejected, draft requested, assign-to-me; add `POST /api/notifications/{id}/action`; re-check RBAC before every action; audit action execution. |
| Frontend tasks | Add `NotificationActionButtons`; route task notifications to `LetterWorkflowPage` or relevant workflow page; display approval comments/outcomes. |
| Database changes | Add action metadata and action state to notifications. |
| Testing requirements | Unit/API tests for action authorization, invalid action rejection, stale notification handling, and approval outcome recipients. |
| Acceptance criteria | Assignees receive workflow notifications; submitters receive approval/rejection outcome; inline actions cannot bypass RBAC. |

### Phase 5 – Reminders & Escalations

| Item | Plan |
|---|---|
| Objective | Add pending reply reminders and configurable escalation matrix. |
| Backend tasks | Add `ReminderRule` and `EscalationRule`; implement scheduler service for 10/20/30+ day checks; evaluate `InputRequest` and workflow pendency; prevent duplicate reminders within a dedupe window. |
| Frontend tasks | Add `ReminderEscalationSettingsPage`; show reminder/escalation notifications with required actions. |
| Database changes | Add `reminder_rules`, `escalation_rules`, and scheduler execution/dedupe tracking fields. |
| Testing requirements | Scheduler tests with frozen time; dedupe tests; escalation recipient tests; RBAC tests for admin-only rule editing. |
| Acceptance criteria | Pending replies at 10, 20, and 30+ days generate the configured reminders/escalations exactly once per window. |

### Phase 6 – Browser Notifications & Real-time Updates

| Item | Plan |
|---|---|
| Objective | Improve immediacy without depending only on the dropdown. |
| Backend tasks | Normalize WebSocket payloads for browser display; remove or gate `/ws/notifications` `user_id` fallback; add polling fallback endpoint behavior if WebSocket unavailable. |
| Frontend tasks | Add `BrowserNotificationPermissionPrompt`; trigger native notifications from WebSocket events when permission and preferences allow; keep polling fallback. |
| Database changes | Add browser channel preference fields if not already present. |
| Testing requirements | Frontend tests for permission states; WebSocket payload tests; manual cross-browser verification. |
| Acceptance criteria | Users who opt in receive native browser notifications while the web app is open; users who deny permission still receive in-app updates. |

### Phase 7 – Digest & Reporting

| Item | Plan |
|---|---|
| Objective | Provide digest summaries and admin visibility into notification delivery. |
| Backend tasks | Add `NotificationDigestSetting`; support daily/weekly/custom digest windows; add incoming/outgoing document digest grouping; add admin report APIs. |
| Frontend tasks | Add `DigestSettingsPanel`; add admin notification report page or settings section. |
| Database changes | Add digest settings and delivery report indexes. |
| Testing requirements | Digest window tests, grouping tests, delivery-log report API tests. |
| Acceptance criteria | Users can configure digest frequency; digest emails summarize incoming/outgoing documents and unread workflow items. |

### Phase 8 – Security & Audit Alerts

| Item | Plan |
|---|---|
| Objective | Surface important system/security events through the notification system. |
| Backend tasks | Add audit-event-to-notification bridge for permission changes, unusual login, account lock, storage threshold, and high-severity audit events; define severity and admin recipients. |
| Frontend tasks | Add security/system alert filters and severity indicators in notification center; route permission/storage alerts to admin pages. |
| Database changes | Add severity fields, security event metadata, and retention policies for security notifications. |
| Testing requirements | Audit bridge tests, security recipient tests, false-positive suppression tests, admin-only visibility tests. |
| Acceptance criteria | Security admins receive high-severity alerts without exposing sensitive details to unauthorized users. |

## 10. Implementation Priority

| Item | Priority |
|---|---|
| Extend existing notification model/service/API foundation | Must Have |
| Document upload notifications | Must Have |
| In-app notification center with read/unread history | Must Have |
| Unified notification center page with search/filter/bulk mark-read | Must Have |
| Email notifications with rich links | Must Have |
| Event-type toggles | Must Have |
| Project-level subscriptions | Must Have |
| Workflow task notifications | Must Have |
| Approval/rejection outcome notifications | Must Have |
| Scheduled 10/20/30 day reminders | Must Have |
| Delivery logs and email retry handling | Should Have |
| Role-based defaults | Should Have |
| Actionable notifications | Should Have |
| Document deletion/archive notifications | Should Have |
| Version update notifications | Should Have |
| Comment, reply, mention, and annotation notifications | Should Have |
| Escalation matrix | Should Have |
| Digest summaries | Could Have |
| Desktop browser notifications | Could Have |
| Admin notification reports | Could Have |
| Advanced unusual-login/security anomaly detection | Later |
| Service-worker push notifications | Later |

## 11. Risks and Controls

| Risk | Control |
|---|---|
| Notification spam | Add per-event toggles, project subscriptions, digest-only mode, quiet hours, dedupe keys, and rate limits for repetitive events. |
| Wrong recipient due to RBAC issue | Centralize recipient resolution in `NotificationEventService`; always use `RBACService.get_notifiable_users`; re-check resource access before listing/action execution; add RBAC tests. |
| Email delivery failure | Add `NotificationDeliveryLog`, retry metadata, SMTP-disabled graceful handling, admin test endpoint, and monitoring/reporting. |
| Duplicate notifications | Use event-level `dedupe_key`, scheduler dedupe windows, idempotent action handlers, and unique indexes where safe. |
| Security of action links | Use authenticated app routes, short-lived signed action tokens only if needed, and always re-run RBAC before mutating resources. |
| Browser notification permission limitations | Treat browser notifications as optional; never block in-app delivery; prompt only after user opt-in; document browser limitations. |
| Scheduler reliability | Persist scheduler execution logs, use dedupe windows, make jobs idempotent, and design for future migration to Celery/RQ. |
| Large volume notification performance | Add indexes by recipient/project/event/created_at/read state, paginate all lists, archive old notifications, batch delivery logs, and avoid unbounded fan-out. |

## 12. Testing Plan

| Test Area | Scenarios |
|---|---|
| Unit tests | Event-to-notification mapping, template rendering, dedupe key generation, preference resolution, project subscription resolution, reminder threshold logic, escalation recipient logic. |
| API tests | List notifications, unread count, mark read, mark all read, preferences save/read, project settings save/read, action endpoint authorization, email test endpoint. |
| RBAC tests | User sees only own notifications; project members only receive accessible project events; admin defaults require admin role; action links re-check underlying resource permission. |
| Email template tests | HTML/text rendering, missing variable handling, document/workflow link generation, SMTP-disabled behavior, delivery-log failure capture. |
| Scheduler tests | Daily/weekly digest windows, 10/20/30 day reminders, no duplicate reminder in dedupe window, escalation sequence, failed job retry behavior. |
| Frontend tests | Bell unread count, dropdown category filters, full center filters/search, mark read/all read, preference toggles, browser permission states, action buttons. |
| End-to-end notification flow tests | Upload document → notification persisted → WebSocket/unread count updated → email logged/sent if enabled → user opens notification → mark read and navigate to resource. |

## 13. Final Recommendations

Build the notification system incrementally by extending the current implementation:

1. Start with **Phase 1 Foundation** using existing `backend/rbac_backend/models/notification.py`, `backend/rbac_backend/utils/notification_service.py`, `backend/rbac_backend/routers/notifications.py`, `client/src/contexts/NotificationContext.tsx`, and `client/src/components/NotificationCenter.tsx`.
2. Add email delivery logs and templates before expanding to reminders. This gives visibility into delivery behavior and prevents silent failures.
3. Add preferences and project subscriptions before high-volume event types such as comments, versions, reminders, and escalations.
4. Implement actionable workflow notifications only after RBAC checks are enforced at the action endpoint.
5. Use APScheduler for the first reminder/escalation implementation because it already exists, but keep the job code isolated so it can move to Celery/RQ later.
6. Treat browser notifications and security anomaly alerts as later enhancements after the core in-app, email, preference, and reminder flows are stable.

Uncertainty to track during implementation: the repository has both the wired `backend/rbac_backend/utils/notification_service.py` and a separate placeholder-style `backend/rbac_backend/services/notifications.py`. Before coding, choose one canonical implementation path and either retire or clearly separate the unused service to avoid divergent notification behavior.
