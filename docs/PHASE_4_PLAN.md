# Phase 4 — Claims-Market Layer: Implementation Plan

> The construction-claims domain layer that turns a strong DMS into a defensible
> **contract-intelligence platform**. Every module is an *extension* of existing
> primitives (tenant model, document/letter schema, citation-enforced RAG, the
> letter draft-approval engine), not a rewrite. Both backend and frontend are
> specified per module.

Companion docs: [ARCHITECTURE.md](ARCHITECTURE.md) · [AUTHZ.md](AUTHZ.md) ·
[EXECUTION_PLAN.md](EXECUTION_PLAN.md).

---

## 0. Foundations reused (do not rebuild)

| Concern | Reuse | Where |
|---|---|---|
| Tenant scoping | `PolicyService.authorize` + `build_scope_query` | `core/security.py`, `services/policy_service.py` |
| Audit | `AuditEventService.emit` (+ export via `GET /api/audit/export`) | `services/audit_event_service.py`, `services/audit_export.py` |
| Clause-grounded AI | `RetrievalService.contract_iterative_qa` (HyDE/RRF, enforced citations, SCC>GCC) | `retrieval/service.py` |
| Approval engine | letter draft runs/assignments/approvals | `services/letter_drafting/`, `letter_draft_runs`/`_assignments`/`_comments` |
| Documents/correspondence | upload→scan→OCR→metadata→file_object | `routers/documents.py`, `services/file_object_service.py` |
| Notifications | in-app + WebSocket + digests | `services/notifications.py`, `routers/ws.py` |
| Export | XLSX/CSV builders | `services/export_service.py`, `services/audit_export.py` |
| Frontend patterns | typed API service + page + dialog + link picker + comments | `services/tasks-api.ts`, `pages/TasksPage.tsx`, document link picker |

**Cross-cutting rules for every module:** new collections carry
`organization_id`/`project_id`; every list/read/mutation goes through
`PolicyService`/`build_scope_query`; every mutation emits an audit event; add the
compound indexes in `core/database.py::ensure_indexes`; add a new permission
family to `core/permissions.py` (`claims.*`) and map it in the role matrix; ship
model + scope + endpoint tests mirroring `test_tasks.py`/`test_http_isolation.py`.

---

## 1. Claim Register  *(foundation — build first)*

The spine everything else links to.

**Backend**
- **Model** `models/claim.py`: `ClaimType` (eot, variation, payment_ipc, loss_expense, defect, other), `ClaimStatus` (draft, notified, submitted, under_review, agreed, rejected, disputed, closed); `ClaimBase` fields: `claim_ref`, `type`, `title`, `description`, `event_date`, `notice_date`, `submission_date`, `response_due_date`, `amount_claimed`, `amount_agreed`, `currency`, `eot_days_claimed`, `eot_days_granted`, `responsible_party_id`, `contract_clauses: list[str]`, `linked_document_ids: list[str]`, `linked_letter_ids: list[str]`, `organization_id`, `project_id`.
- **Router** `routers/claims.py`: CRUD + `GET /claims` (scoped list w/ filters: type/status/party/date), `POST /claims/{id}/status` (transition), `POST /claims/{id}/links` / `DELETE` (attach/detach correspondence + letters). Gate with new `claims.view/create/edit/delete/manage` permissions; audit each mutation; default org/project from `current_user`.
- **Service** `services/claim_service.py`: persistence + the status state-machine + link integrity (validate linked docs are in-scope).
- **Indexes** (`database.py`): `(organization_id, project_id, status, created_at)`, `(organization_id, project_id, type)`, `claim_ref` (unique per org/project), `response_due_date`.

**Frontend**
- `services/claims-api.ts` (typed CRUD + links + status).
- `pages/ClaimsRegisterPage.tsx` — filterable table (type/status/party/value/due) + create/edit dialog (reuse the TasksPage form pattern).
- `pages/ClaimDetailPage.tsx` — claim header, **linked-correspondence panel** (reuse the document link picker), value/EOT summary, status timeline, comments (reuse the per-task comments component), audit trail.
- `routes.tsx`: `/claims`, `/claims/:id`.

**Acceptance:** Org A never sees Org B's claims (HTTP-isolation test); status transitions audited; correspondence links resolve. **Effort: M.**

---

## 2. Correspondence SLA & Time-bar Tracker

The "don't miss a deadline" feature — high pilot value, deadline-heavy domain.

**Backend**
- Add `response_due_date` + `time_bar_date` + `sla_state` to letters/claims (or a derived `sla_items` aggregation across `letters`/`claims`/`documents`).
- **Background job** (extend `services/background_jobs.py` / the APScheduler in `main.py`): scan for deadlines within `N` days (approaching) and past-due (breached) → emit notifications (`services/notifications.py`) + escalation to managers.
- **Endpoints** (`routers/sla.py` or extend `dashboard.py`): `GET /sla/upcoming?days=`, `GET /sla/breached`, both tenant-scoped.
- **Time-bar config:** per-claim-type notice windows (e.g., 28-day) configurable per org (a small `sla_rules` collection or org settings).

**Frontend**
- `pages/SLATrackerPage.tsx` — dashboard of upcoming/breached deadlines grouped by claim/correspondence, with escalation actions; `services/sla-api.ts`.
- SLA badges on `ClaimDetailPage`/`DocumentsPage`; integrate into the existing `NotificationCenterPage`.

**Acceptance:** a seeded near-due claim shows in `/sla/upcoming`; breach emits a notification. **Effort: M.**

---

## 3. EOT / Delay, Variation (VO/CE), Payment / IPC modules

Specialised claim sub-types with **clause-grounded AI assessment**.

**Backend**
- Type-specific metadata on the claim (delay events + windows for EOT; CE/VO refs + valuation for variation; IPC line items + certified/paid for payment).
- **`POST /claims/{id}/assess`** — runs `RetrievalService.contract_iterative_qa` over the project's contract scoped to the claim, returning a **cited** draft assessment (relevant clauses, SCC>GCC precedence, gaps). Reuses the existing citation-enforced engine — no new AI infra.
- Gate with `claims.assess`; persist the assessment + its citations + `rag_run` trace for traceability.

**Frontend**
- Type-specific sub-forms on `ClaimDetailPage`; an **"AI assessment" panel** that renders the cited clauses (reuse the Contract-QA citation UI from `ContractQAPage`), with a "regenerate" action and a visible source ledger.

**Acceptance:** assessment returns citations; no-evidence → "Information not found". **Effort: M–L.**

---

## 4. Generalised Approval Workflows

Reuse the letter draft-approval engine for claims (and any approvable entity).

**Backend**
- Generalise the `letter_drafting` approval pattern (`letter_draft_assignments`/`_runs`/approvals) into an `approvals` capability bound to a `(resource_type, resource_id)` — e.g., a claim. States: draft → assigned → in_review → approved/returned. Reuse `expert_allocations`/role checks for who can approve.
- **Endpoints** on the resource: `POST /claims/{id}/assign`, `/submit-for-review`, `/approve`, `/return`. Step-up + `PolicyService` gated; audited.

**Frontend**
- Approval action bar on `ClaimDetailPage` (assign reviewer, submit, approve, return with comment) — reuse `letter-workflow` approval components.

**Acceptance:** role matrix enforced (drafter can't self-approve); transitions audited. **Effort: M.**

---

## 5. Evidence Bundle / Data-room Export

The concrete arbitration buying-reason.

**Backend**
- **`GET /claims/{id}/evidence-bundle`** — assemble a **ZIP**: the claim record (PDF/JSON), all linked correspondence (fetched via `file_object_service`), the claim's **audit trail CSV** (reuse `audit_export`), and a manifest. Stream the ZIP (`StreamingResponse`). `dms.audit.view`/`claims.export` gated; scoped; the export action itself is audited.

**Frontend**
- "Export evidence bundle" button on `ClaimDetailPage` → downloads the ZIP (reuse the blob-download pattern from `audit-api.ts`).

**Acceptance:** bundle contains only in-scope documents + a complete, ordered audit trail. **Effort: M.**

---

## 6. Email / Mailbox Ingestion

Capture inbound correspondence automatically.

**Backend**
- Per-org mailbox config (IMAP or Microsoft Graph). A **background poller** ingests new mail as **incoming** correspondence documents through the existing pipeline (MIME validation, ClamAV scan, OCR, metadata, `file_object`), attaching sender/subject/date metadata and auto-linking by letter-reference where possible.
- **Security:** encrypted mailbox credentials (reuse the SMTP-settings encryption), allow-listed senders/domains, size/type caps, SSRF-safe fetching.

**Frontend**
- Mailbox config in settings (`SettingsPage`); ingested emails appear in `DocumentsPage` as incoming correspondence with a provenance badge.

**Acceptance:** a test mailbox message lands as a scanned, metadata-tagged incoming document in the right org/project. **Effort: L** (external integration; needs a test mailbox).

---

## 7. e-Signature

Sign issued letters / claim submissions.

**Backend**
- Pluggable signature provider behind an interface (mirror the `PaymentGatewayInterface` pattern) — DocuSign / Adobe Sign adapter + a webhook (`POST /api/esign/webhooks/{provider}`, signature-verified + idempotent, like billing webhooks) that records completion + the signed artifact.
- `POST /letters/{id}/send-for-signature`; status tracked on the letter/claim.

**Frontend**
- "Send for signature" + signature-status badge on the letter/claim.

**Acceptance:** signed-document webhook updates status idempotently. **Effort: L** (needs provider keys).

---

## 8. Sequencing, effort & decisions

**Recommended order** (each builds on the prior; 1–4 are the pilot-critical core):
1. **Claim Register** (foundation) — M
2. **SLA / Time-bar Tracker** — M
3. **Approval Workflows** (reuse engine) — M
4. **Evidence Bundle Export** (reuses M7) — M
5. **EOT/Variation/Payment + AI assessment** — M–L
6. **Email ingestion** — L · **e-Signature** — L (external, last; need creds)

**Rough effort:** core (1–4) ≈ 4–6 weeks for 1–2 engineers; full Phase 4 ≈ 10–12 weeks.

**Decisions needed before starting:**
- Claim taxonomy/status set — confirm against the target contract forms (FIDIC / NEC / bespoke).
- Permission model — new `claims.*` family vs. extending `dms.*` (recommend new family for clean role matrices).
- e-Sign provider (DocuSign vs Adobe Sign) and mailbox protocol (IMAP vs Microsoft Graph) — drives modules 6–7.

**Risks/mitigations:** scope creep on claim fields → start minimal, iterate with a pilot; AI assessment accuracy → keep citations enforced + a visible source ledger + measure no-answer rate; external integrations (mail/e-sign) → behind interfaces + signature-verified webhooks, verified at deploy.

**Definition of done (per module):** tenant-isolation + RBAC-matrix tests green; audit on every mutation; indexes added; frontend typechecks + lints; documented in OPERATIONS/ARCHITECTURE.

**Market impact:** core (claim register + SLA tracker) is what moves market-fit from ~7 to **8/10** once a lighthouse pilot validates it (per EXECUTION_PLAN.md).
