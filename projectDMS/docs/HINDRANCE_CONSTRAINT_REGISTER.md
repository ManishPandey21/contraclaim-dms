# Hindrance & Constraint Register

Owner decision (2026-09-21): the register extends the existing `delay_events`
collection. There is no second hindrance entity.

## Shape

| Layer | Where |
|---|---|
| Collection | `delay_events` (canonical); `delay_event_reference_counters`; `delay_event_links` |
| Model | `models/evidence_registers.py` — `DelayEvent`, `HindranceCreate`, `HindranceUpdate`, link models |
| Service | `services/hindrance_register_service.py` — `HindranceRegisterService` owns every write |
| Canonical API | `routers/hindrances.py` → `/api/hindrances…` |
| Compatibility API | `routers/evidence_registers.py` → `/api/delay-events…` (same service) |
| Document evidence | `entity_document_links` via `DelayEventEntityAdapter` (target type `delay_event`) |
| Timeline | derived `project_events` row, one per entry, keyed by `(source_entity_type="delay_event", source_entity_id)` |
| UI | `/hindrances` (register), `/hindrances/:id` (detail); sidebar "Hindrance & Constraint Register" |

`event_type` is `hindrance | constraint | delay_event`. Only `delay_event` presumes
delay: its timeline link is `causes_delay`; the other two use `affects` and project
as `ProjectEventType.HINDRANCE` / `CONSTRAINT`.

## Rules an edit must keep

- **Reference**: `hindrance_ref` is server-generated (`HIN-`, `CNS-`, `DLY-` + 4+
  digits), per organisation+project+type, from an atomic counter. A partial unique
  index (`uq_delay_events_project_reference`) is the backstop; a collision realigns
  the counter and retries. Clients can never set or change it (`extra="forbid"`).
- **Scope**: the organisation is the project's owner (`resolve_project_organization`),
  never the caller's guess; authorization runs first so a refused caller learns
  nothing about project existence. Updates cannot move a record.
- **PATCH**: omitted = untouched, explicit `null` = cleared. Fields without a
  cleared state (title, type, start date, responsibility, status, …) refuse null
  with 422. The same rule now applies to drawing references and programme milestones.
- **Status**: `status` is the operational lifecycle (`open`, `under_review`,
  `resolved`, `closed`). Claim progress is `claim_status`. The legacy claim states
  `claimed/assessed/rejected` stay valid in storage and on `/api/delay-events`;
  `/api/hindrances` refuses them as a status.
- **Archive, never delete**: `POST …/archive` / `…/restore` with a reason. Archived
  entries are read-only (edits, links, evidence → 409), excluded from default lists
  (`include_archived=true` shows them), still retrievable by id, audited.
- **Timeline is derived and fail-visible**: a projection failure never fails the
  write; it sets `timeline_sync_status=failed` + `timeline_sync_error` (exception
  class only), emits `delay_events.timeline_sync_failed`, logs, and is repaired
  idempotently by `POST …/timeline-sync`. Edits refresh the existing event in place.
- **Relationships**: activities (`programme_milestones`), key dates
  (`key_date_milestones`) and EOT submissions live in `delay_event_links` with soft
  removal and a unique active identity. A link requires edit on the entry, view on
  the target, and the same organisation AND project. Linking an EOT submission is
  support only; it never creates or assesses an EOT claim.

## Permissions

`dms.hindrance.view|create|edit|archive`, domain `client_dms`, entitlement
`feature.dms.evidence_graph` (so no plan's scope changes). Default roles:
superadmin, orgadmin, contractmgr_org, projectadmin hold all four; orguser,
projectuser, doccontroller, reporter, limited_user hold none. `/api/delay-events`
moved from `dms.evidence_graph.*` to these permissions, so graph access alone no
longer reaches the register.

| Role | view | create | edit | archive | Before this change (delay events via `dms.evidence_graph.*`) |
|---|---|---|---|---|---|
| superadmin | ✓ | ✓ | ✓ | ✓ | full (bypass) |
| orgadmin | ✓ | ✓ | ✓ | ✓ | view + manage |
| contractmgr_org | ✓ | ✓ | ✓ | ✓ | view + manage |
| projectadmin | ✓ | ✓ | ✓ | ✓ | view + manage |
| orguser, projectuser | — | — | — | — | none |
| doccontroller, reporter, limited_user, settings_manager | — | — | — | — | none |
| custom role holding only `dms.evidence_graph.*` | — | — | — | — | view + manage (**loses access**) |

Linking an activity additionally needs `dms.evidence_graph.view` (activities are the
`programme_milestones` register); linking a key date or EOT submission needs
`dms.keydate.view`.

**Production role documents are not refreshed by the seeder** (only `superadmin`
is). Until `python -m rbac_backend.scripts.align_role_contract` is applied,
production `orgadmin`/`projectadmin` do not hold the new permissions, and
`contractmgr_org` is outside that operation's scope entirely.

### Mandatory cutover step (owner decision, 2026-09-21)

The table above is the seeded default. Production role documents lag it, so the
compatibility API would otherwise lose callers that reached it through
`dms.evidence_graph.*`. One authorization model is kept; the gap is closed at
cutover, and staging certification depends on it:

1. `python -m rbac_backend.scripts.align_role_contract` (inspect, then apply) so
   `orgadmin` and `projectadmin` hold the four `dms.hindrance.*` permissions.
2. An explicit, owner-approved grant of the same four to `contractmgr_org`, which
   the alignment operation does not cover.
3. Before either, list the custom roles holding `dms.evidence_graph.*`; each loses
   `/api/delay-events` and needs its own decision.

Step 2 is `role_contract_alignment.grant()` (`OWNER_APPROVED_GRANTS`), run by the
same command. Step 3 was measured on production on 2026-09-21: no custom role holds
those permissions ([staging plan](HINDRANCE_STAGING_CERTIFICATION_PLAN.md) §4).

## Active project scope

Owner decision 2026-09-22: the navbar selection is an authorization boundary for the
register (`core/tenant_context.py`). The browser sends `X-Org-Id` / `X-Proj-Id` on every
API request. With a project selected, a record, create body, link target or
reverse-lookup target in another project is 403 `context_forbidden`, for superadmin too.
With none selected, record-level and mutating routes are 400 `selection_required`, and
the list stays bounded by `build_scope_query`. The same rule covers `/api/delay-events`,
the `delay_event` document-link routes and the Hindrance rows of
`/api/documents/{id}/entity-links`. Other modules are not yet bound by the selection;
that is recorded as cross-module debt.

## Compatibility and migration

No data migration is required and none is written:

| Legacy state | Read behaviour |
|---|---|
| no `event_type` | served as `delay_event` (the only schema that could have written it); the `event_type=delay_event` filter matches it |
| no `category` | served `null`, shown "Uncategorised" |
| no `hindrance_ref` | served `null`; UI shows `delay_ref`. Not back-filled: assigning references to history is an owner decision |
| `programme_activity` free text | kept and editable; structured links are additive |
| `linked_document_ids` | served through the canonical relationship read-through (`source=legacy_read_through`, role `supporting_document`). The canonical API (`/api/hindrances`) writes evidence only to `entity_document_links` and rejects the field. The compatibility API (`/api/delay-events`) still accepts it (CL-3A debt): that write is gated by `dms.hindrance.edit` and the selected project, keeps only in-scope authorized Documents, refuses an archived entry, and is audited as `delay_events.updated` - but it carries no relationship role and emits no `document_relationship.linked` audit, and such a row can only be cleared through the same API |
| `claimed/assessed/rejected` status | preserved; shown "(legacy)" |

`ensure_indexes` adds the new indexes at startup. The unique reference index is
partial, so legacy rows without a reference cannot violate it.

## Deprecation of `/api/delay-events`

Kept with its exact surface and `_id` serialisation. New UI code uses only
`/api/hindrances` (enforced by `hindrance-api.test.ts`). Remove the four
compatibility routes once the route inventory and access logs show no caller.
