# CL-3B — Programme Milestone + Chronology canonical linking (2026-09-23)

Branch `feat/cl3b-programme-chronology-links` (worktree `C:\SaaS-cl3b`), stacked on CL-3A
`feat/cl3a-hindrance-scope-integration` @ `11473f7` (PR contraclaim-dms#27, itself on CL-2 #26 and
CL-1 #23). Nothing in #22/#23/#26/#27 was rewritten.

## 1. Provenance (re-fetched from both remotes, 2026-09-23)

`origin` and `contraclaim` both resolve `feat/cl3a-hindrance-scope-integration` to
`11473f75995096fa609f1e1287c71169488017b4`. PR #27: OPEN, DRAFT, unmerged, 5/5 checks SUCCESS
(secret scan, backend, frontend, dependency scan, docker build/scan).

## 2. Audit matrix (Phase 1)

### Programme Milestone

| Aspect | Finding |
|---|---|
| Model / collection | `ProgrammeMilestone` (`models/evidence_registers.py`), `programme_milestones` |
| Router / service | `routers/evidence_registers.py` (`/api/programme-milestones` list/create/get/patch), `EvidenceRegisterService` |
| Permissions | `dms.evidence_graph.view` / `dms.evidence_graph.manage` |
| Scope | `organization_id` (defaulted from the actor) + required `project_id` on the row |
| Delete / archive | **none** (no route); `status` has `superseded` - a programme status, not an archive |
| Graph | create emits a `project_event` + `event_link` (evidence graph) |
| Frontend | **no page existed**; only Hindrance's "affected activities" picker read the list; the Hindrance link route for a milestone was `None` |
| Canonical type name | `programme_milestone` (already `EvidenceEntityType.PROGRAMME_MILESTONE`, `HindranceLinkTargetType`) |

### Chronology

| Aspect | Finding |
|---|---|
| Model / collections | `MatterChronology` (`matter_chronologies`, soft delete `deleted_at`, `status` incl. `archived`); `MatterChronologyEvent` (`matter_chronology_events`, no delete) |
| Router / service | `routers/chronology.py`, `services/chronology.py` |
| Permissions | `dms.chronology.view/create/edit/verify/export/admin` |
| Scope | the chronology row; events are anchored to the parent's org/project by `_apply_chronology_authority_scope` (older events may carry none) |
| Evidence owner | the **event** (`source_document_id`, `related_document_ids` live there); `chronology_event` is already its name in `publication_policy` |
| Frontend | `ChronologyBuilderPage` with its own org/project pickers; `/chronology/:chronologyId` routes existed but the page ignored the param |

### Existing Document references

| Field | Classification | Decision |
|---|---|---|
| `programme_milestones.linked_document_ids` | LEGACY, G31-certified write path (`_authorized_document_links`, served through `_current_authority_projection`) | READ-ONLY COMPATIBILITY on the canonical side (read through as `supporting_document`); write path **kept** (same status as `/api/delay-events`, see §8) |
| `matter_chronology_events.related_document_ids` | LEGACY; written unvalidated by PATCH, `/link`, create; UI never wrote it | ADD PATH REMOVED (409); an unchanged echo or a removal (subset) passes and lands on the event revision trail, so a stale/foreign legacy id can still be cleaned up; `null` 422; READ-ONLY COMPATIBILITY as `supporting_document` |
| `matter_chronology_events.source_document_id` | CANONICAL provenance (one Document; governs publication of the event text) | Stays the field of record; surfaced read-only as `source_document`; never a link row |
| `matter_chronologies.selected_source_ids` | extraction input selection, not a relationship | untouched |
| `event_link_ids`, `project_event_id` | evidence-graph links, not Document relationships | untouched |
| `delay_event_links` | Hindrance → Programme/Key Date/EOT | unchanged; remains Hindrance-only |
| MIGRATION REQUIRED | none for correctness: legacy read-through serves history, canonical rows serve new links | a backfill is optional (§5) |

## 3. Adapters

* `ProgrammeMilestoneEntityAdapter` - scope from the row; view/manage `dms.evidence_graph.view/manage`;
  write guard fenced on org/project; `delete` refuses (the register has none, so `delete_target`
  answers 409); label `PM-110 · Pier P4 piling`; route `/programme-milestones/{id}`;
  `active_scope_enforced`.
* `ChronologyEventEntityAdapter` - scope from the **parent chronology**; an event whose own
  org/project contradicts it, or whose chronology is deleted, does not load; an archived chronology
  is read-only both ways (guard refuses writes and removals, never offered); view/manage/delete
  `dms.chronology.view/edit/admin`; label `date · title` from the publication-safe projection (a
  title lifted from a no-longer-consumable Document is never a label, and search matches the loaded
  entity, not the raw row); route `/chronology/{chronologyId}?event_id={eventId}`;
  `active_scope_enforced`.

## 4. Relationship roles (Phase 6)

| Target | Roles | Derivation |
|---|---|---|
| Programme Milestone | `programme_record`, `progress_evidence`, `correspondence`, `supporting_document` | planned/forecast dates ← programme record; actual date/status ← progress evidence; no approval/instruction concept in the model, so neither exists |
| Chronology event | `correspondence`, `supporting_document` (+ read-only `source_document`) | related documents were generic; `evidence` is not a distinct concept from supporting document; the source is provenance (Phase 13) |

## 5. Legacy chronology references (Phase 5)

Legacy read-through + canonical writes. No historical field is rewritten. A future certified
backfill would classify each `related_document_ids` / `source_document_id` value as: valid
same-project Document; missing Document; foreign project; foreign organisation; duplicate (already a
canonical row, or source repeated in related - the read-through already shows it once, as the
source); ambiguous (a related id that is also the source keeps the source meaning). The read path
already enforces every class: `_document` resolves each id and serves it only when it exists, is
consumable and is in the event's structural scope; the reverse lookup additionally drops any
candidate whose structural scope is not the Document's. No inventory/dry-run/apply script was added:
nothing needs rewriting for correctness, and a backfill without an owner decision on "source vs
supporting" for duplicates would invent meaning.

## 6. Source-document semantics (Phase 13)

`source_document_id` is one Document per event, tied to its span text and publication authority.
It is NOT flattened into `supporting_document`: it reads through as `source_document`, is absent from
the linkable roles (422 if requested), and stays visible beside a canonical link of the same Document
(`legacy_superseded_by_link` is false for it), so one source never becomes many and a link never hides
provenance. PATCH may still change it, now only to a Document in the chronology's own project that
is currently authoritative (403 / 409).

## 7. Selected project scope (Phase 7)

Same mechanism as CL-3A (`core/tenant_context.py`, `X-Org-Id` / `X-Proj-Id`,
`active_scope_enforced`). Programme-milestone and chronology routes follow it; the relationship
routes hold both targets to it.

| Case | Programme | Chronology | Relationship routes |
|---|---|---|---|
| member A+B, selected A, record A | 200 | 200 | 200/201 |
| member A+B, selected B, record A | 403 `context_forbidden` | 403 `context_forbidden` | 403 |
| non-member of A | 403 | 403 | 403 |
| no selection, list | 200 bounded | 200 bounded | reverse: bounded |
| no selection, record / write | 400 `selection_required` | 400 `selection_required` | 400 |
| superadmin, selected B, record A | 403 | 403 | 403 |

Deliberately selection-blind: chronology **exports** (opened as plain `<a href>` downloads, which
cannot carry headers) and the two `/arbitration/drafts/...` chronology routes (they authorize for the
arbitration register) - debt #24.

## 8. Decisions not changed (Phases 15-18)

* **Legacy-array helper (Phase 15): not extracted.** The four routers differ materially: IPC update
  replaces through `replace_legacy_document_ids` (claim-only, so 409), BG/Key Date refuse any
  non-empty write, Variation compares an echo with the *presented* ids and keeps scope-less rows. The
  common part is already one call (`reject_ambiguous_legacy_write`). A helper would have to encode
  four behaviours behind flags. CL-3B does not add a fifth copy: Programme keeps its G31 path and
  Chronology's refusal is local to its router.
* `/api/delay-events` raw `linked_document_ids`: untouched (G31). `/api/programme-milestones` raw
  `linked_document_ids` is under the same G31 authority suite and is likewise kept - pinned by a
  real-Mongo test.
* Project-less legacy Variations: CL-3A exception untouched.
* No system-wide active-scope rollout (IPC, BG, Insurance, Claims, Key Dates, Contract Documents,
  Documents, dashboards, reports remain on #24).

## 9. Link to Record

* The offered types are the adapters with `link_to_record` (the hand-kept
  `LINK_TO_RECORD_TARGET_TYPES` list is now derived from the registry): the nine CL-2/CL-3A types plus
  `programme_milestone` and `chronology_event`.
* **>200 search (Phase 9).** Each adapter supplies a DB predicate (`link_target_search`) applied
  before the 200-row scan budget: row label fields by default, the parent Bank Guarantee's number/type
  for BG events, the parent Key Date's reference/title for achievements, and no narrowing when the
  search is part of a fixed label suffix. Proven red on `11473f7` (BG event and Key Date achievement
  searches behind 250 rows returned `[]`) and green here for four types.
* **Permission-aware button (Phase 10).** `GET /documents/{id}/link-target-types` answers, from the
  caller's manage permissions on the Document's scope (one PolicyService decision per permission, no
  register row read), which types they could link; selection-bound types only while the selection is
  the Document's project. The panel shows the button only when that list is non-empty, the dialog
  offers exactly it, and an error fails closed. `link-targets` itself also returns `[]` without
  reading records when the caller holds none of a type's manage permissions.

## 10. Delete / archive (Phase 14)

* Chronology delete (`DELETE /chronologies/{id}`, `dms.chronology.admin`) soft-deletes the
  chronology and, in the same transaction, soft-removes every active canonical link of its events
  (`DocumentRelationshipService.retire_target_links`), each audited once with reason "Chronology
  deleted". Events and Documents survive; the events stop loading as targets (404).
* Archived chronology: read-only, not offered. Programme Milestone: no delete or archive exists;
  `delete_target` refuses; superseded milestones keep taking evidence.
* A foreign link id cannot be removed: the link's own target is loaded and authorized.

## 11. Independent reviews and dispositions

Four independent reviewers (RBAC/tenant scope; relationship data integrity; legacy
compatibility/migration; frontend/spec fidelity): **0 BLOCKER, 0 HIGH** in all four.

| Finding (severity, review) | Disposition |
|---|---|
| BG event / Key Date achievement predicate dropped label-tail searches ("Extension 2", "KD-7 · Ach") the old scan matched (MEDIUM, integrity) | **Fixed**: no narrowing for a search spanning `·` or ending in a count; parent `_id` searchable; unit-pinned (`test_link_target_search_predicates.py`) |
| Chronology reverse lookup scanned all tenants' events, unindexed (MEDIUM legacy / LOW RBAC / LOW integrity) | **Fixed**: candidates limited to the Document's own live chronologies; indexes `(source_document_id, chronology_id)`, `(related_document_ids, chronology_id)` |
| Stale legacy `related_document_ids` could no longer be removed (MEDIUM, legacy) | **Fixed**: a subset (removal) passes, recorded by the event revision trail; adding stays 409; `null` 422 |
| Withheld legacy references were silent (MEDIUM, legacy) | **Fixed**: counted and logged per target by `list_for_target` |
| Pleading-context failure wiped the event list (MEDIUM, frontend) | **Fixed**: separate failure, events stay; stale-response guard added |
| A refused chronology deep link silently opened another chronology (MEDIUM, frontend) | **Fixed**: explicit alert, nothing substituted; Vitest + Playwright pin it |
| Programme raw `linked_document_ids` is a second, unaudited write path (MEDIUM, integrity) | **Kept - debt.** It is certified by the G31 document-authority suite (`test_evidence_register_document_authority_red.py`), exactly like `/api/delay-events`; reopening it needs the owner's G31 decision |
| Arbitration drafting context reads raw `related_document_ids`, not canonical links (MEDIUM, legacy) | **Debt**: the arbitration LangGraph engine is intentionally not primary; its context builder must move to `list_for_target` when arbitration work resumes |
| `source_document_id` checked for scope, not for the writer's `DOCUMENT_VIEW` (LOW, RBAC) | **Tried and reverted - debt**: the G31 chronology suite pins the exact authorization sequence of event creation |
| Archived chronology is read-only on the relationship API only; register routes can still un-archive / extract (LOW, RBAC) | Debt: same permission, no privilege gained; a register-level archive policy is a product decision |
| Archived check on unlink not fenced in the transaction (LOW, RBAC) | Debt: race window between load and commit; same-permission actor |
| `letter_no` searchable but not withheld (LOW, RBAC) | **Fixed**: dropped from the searchable fields |
| Fake DBs without `matched_count` treated as matched (LOW, integrity) | **Fixed**: fail-closed default |
| Button/fail-closed tests could pass on the pending state; unknown type could open an empty dialog; `aria-controls` dangling; back link assumed Hindrance access; no in-place switch in e2e (LOW, frontend) | **Fixed** (settled-state assertions, client-side type filter, conditional `aria-controls`, `navigate(-1)`, in-place switches) |
| Events Documents search used the event's own (possibly absent) scope (LOW, legacy) | **Fixed**: the chronology's scope |
| Chronology delete now needs transactions (standalone dev Mongo) (LOW, legacy) | Accepted: every canonical relationship write already does; production is a replica set |

## 12. Remaining debt

See the PR description for evidence counts.

* Chronology exports and arbitration chronology routes are selection-blind (§7).
* `/api/programme-milestones` and `/api/delay-events` accept raw `linked_document_ids` (G31).
* No Programme Milestone register/list page exists; the detail page is reached by deep link (Linked
  Records, Hindrance affected activities). A register page is product work, not linking.
* Link-to-Record still loads each candidate through its adapter (a few queries per row, bounded by
  the 200-row budget and the scoped rate limiter).
* The Playwright workflow is mock-backed; staging plan §15 owes the real-stack run (gated by R-A9I and
  the owner window).
