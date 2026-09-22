# Hindrance & Constraint Register — staging certification plan

Status: **offline preparation. Nothing in this file has been executed against a deployment.**
Branch `feat/hindrance-constraint-register`, draft PR ManishPandey21/contraclaim-dms#22 (open,
draft, unmerged). Design: [HINDRANCE_CONSTRAINT_REGISTER.md](HINDRANCE_CONSTRAINT_REGISTER.md).

## 1. Owner decisions (recorded 2026-09-21)

| # | Decision |
|---|---|
| D1 | Staging = **same-host staging under an owner-authorized maintenance window** (option 1). No reduced stack beside production. No new staging host in this phase. |
| D1a | **Sequencing:** the hindrance outage does not start until **R-A9I Falkor compose normalization is complete** and production is back on a normal compose-managed Falkor. Until then: offline/read-only only — no production stop, no PR #22 deploy, no staging volumes. |
| D2 | `contractmgr_org` grant **APPROVED**: exactly `dms.hindrance.view`, `.create`, `.edit`, `.archive`. Nothing else. `dms.evidence_graph.*` is never a compatibility bypass. |
| D3 | `orgadmin` / `projectadmin` through `align_role_contract` only: inspect → dry-run → apply → re-apply (second apply NOOP). No user patched. |
| D4 | Custom roles: **no automatic re-grant.** Any custom role needing register access is presented individually. |
| D5 | `/api/delay-events` stays on `dms.hindrance.*`, with a permanent regression test (§5). |
| D7 | Fresh staging initialization uses the canonical release `DEFAULT_ROLES` unchanged. **contractmgr_org grant: OWNER APPROVED** — the seed is not altered to imitate old production documents. |
| D8 | Two-part role certification in the window: A = production-copy segment, B = fresh-initializer segment. |
| D15 | Recommended window **4 h**: 120-minute execution budget + 90-minute protected recovery reserve. The clock time is the owner's to set. |
| D16 | No merge, no production deploy of the register in this phase. |

## 2. `contractmgr_org` — exact grant

Mechanism: `role_contract_alignment.grant()` over `OWNER_APPROVED_GRANTS`, run by the same
command as the alignment (`python -m rbac_backend.scripts.align_role_contract [--apply]`).
It adds only the named permissions, removes nothing, never reads or writes users, never
creates or reactivates a role, refuses a grant outside the role's release definition, and a
second apply is a NOOP (`tests/test_role_contract_alignment.py`, 10 grant tests).

Measured on the live production document (read-only, 2026-09-21):

| | |
|---|---|
| Held before | 54 permissions, none `dms.hindrance.*`, none `dms.evidence_graph.*` |
| **Added** | `dms.hindrance.archive`, `dms.hindrance.create`, `dms.hindrance.edit`, `dms.hindrance.view` |
| Removed | none |
| Release-definition gap deliberately **not** granted | 66 permissions |
| Holders | 0 |

The 66-permission gap is why this role is granted, not aligned: aligning it would add 66
permissions the owner did not approve.

## 3. `orgadmin` / `projectadmin` — alignment plan

Candidate contract diffed against the live production documents (read-only, 2026-09-21):

| Role | Holders | Held | Additions | Removals | Extras preserved |
|---|---|---|---|---|---|
| orgadmin | 3 | 135 | the 4 `dms.hindrance.*` | none (`billing.plan.manage` already removed in R-A9G) | 6 |
| projectadmin | 2 | 118 | the 4 `dms.hindrance.*` | none | 4 |

Sequence on the production copy (segment A), JSON output kept as evidence:

1. `align_role_contract` (inspect): expect exactly the rows above, `unapproved_*_total` 0, exit 0.
2. `align_role_contract --apply`: applies, then runs a second apply in-process; `second_apply_is_noop: true`, exit 0.
3. `align_role_contract --apply` again (re-apply as a separate run): zero additions, zero writes, exit 0.

The same command covers the `contractmgr_org` grant (`grant_before` / `grant_apply` /
`grant_second_apply` in the report).

## 4. Custom-role and holder impact (live production, read-only, no PII)

Before the cutover the compat routes were gated by `dms.evidence_graph.*`, so a role holding
only those could reach `/api/delay-events`. After it, only `dms.hindrance.*` works. The
production permission catalogue holds 265 permissions and none of the four hindrance
permissions yet; the startup seeder (`ensure_permission_catalog_and_superadmin`) adds them,
and adds them to `superadmin` only.

| Role | Holders | Evidence-graph perms now | Hindrance access after cutover | Owner action required? |
|---|---|---|---|---|
| superadmin | 2 | view/verify/manage | full (name-based bypass + seeder) | no |
| orgadmin | 3 | view/verify/manage | full, via alignment (D3) | no (approved) |
| projectadmin | 2 | view/verify/manage | full, via alignment (D3) | no (approved) |
| contractmgr_org | 0 | none | full, via grant (D2) | no (approved) |
| projectuser | 2 | none | none | no |
| orguser, doccontroller, reporter, settings_manager, limited_user | 0 each | none | none | no |
| custom `…a0308e` | 0 | none (inactive, 0 perms) | none | no |
| custom `…c4a5f6` | 0 | none (inactive, 0 perms) | none | no |

12 roles, 9 users. **No custom role holds `dms.evidence_graph.view/manage`, so none loses
register access, and no owner decision is required under D4.** The two custom roles show only
their last six id characters.

## 5. Compatibility-route regression test

`tests/test_hindrance_register_http.py::test_evidence_graph_manage_is_not_a_write_bypass_on_the_compatibility_api`:
a principal holding `dms.hindrance.view` + `dms.evidence_graph.{view,verify,manage}`, without
`dms.hindrance.create/edit`, gets **403** on `POST /api/delay-events` and on
`PATCH /api/delay-events/{id}`. Positive controls: its list and detail reads are 200, and an
editor's PATCH with the same body is 200. So the 403 cannot come from a malformed request.
Mutation-checked: re-gating either route on `EVIDENCE_GRAPH_MANAGE` turns the test red
(`201 == 403` for create, `200 == 403` for update).

## 6. Staging seed disposition

- **Segment A (production copy):** restore a validated production backup copy into the
  staging Mongo. Measure role documents and holder counts read-only **before the staging
  backend starts** (the startup seeder rewrites the catalogue and `superadmin`; see
  the Gate 6 b3 lesson). Then run §3. No user mutation. Assert that nothing outside
  §2/§3 changed: diff every role document before and after.
- **Segment B (fresh initializer):** a fresh staging database seeded by the app's own
  initializer, with the database passed explicitly, using the release `DEFAULT_ROLES`
  unchanged (D7). This is the stack the browser and API specs run against.
- Superuser is dormant, and production has neither the role document nor any holders.
  **No `superuser` document is seeded to make a check pass.** "System User" is not a role in
  this release (`DEFAULT_ROLES` has no such key), so it is recorded as not applicable, not
  simulated. The owner can override both.

## 7. Accounts and expected access (segment B)

Two run-owned projects in the fixture organisation, `g3-<RUN_ID>-HIN-A` and `g3-<RUN_ID>-HIN-B`.
They are needed because an EOT submission needs a **frozen** key-date baseline and a freeze
cannot be undone. A second organisation holds the foreign fixtures.

| Env prefix | Role | Scope | Expected |
|---|---|---|---|
| `E2E_HIN_SUPERADMIN` | superadmin | global | all 200/201 |
| `E2E_HIN_ORGADMIN` | orgadmin | fixture org | all 200/201; seeds targets |
| `E2E_HIN_PA_AB` | projectadmin | projects A + B | all 200/201; browser workflow, navbar |
| `E2E_HIN_PA_B` | projectadmin | project B only | 403 on every A record (foreign project) |
| `E2E_HIN_CONTRACTMGR` | contractmgr_org | fixture org | all 200/201 (D2) |
| `E2E_HIN_ORGUSER` | orguser | fixture org | every operation 403 |
| `E2E_HIN_PROJECTUSER` | projectuser | project A | every operation 403, including direct URLs |
| `E2E_HIN_DOCCONTROLLER` | doccontroller | project A | every operation 403 |
| `E2E_HIN_REPORTER` | reporter | project A | every operation 403 |
| `E2E_HIN_LIMITED` | limited_user | project A | every operation 403 |
| `E2E_HIN_FOREIGN_ORGADMIN` | orgadmin | second org | 403 on everything in the fixture org |

Operations per persona: list, view, compat list, create, edit, document link, relationship
link, archive, restore. Refusals must be exactly 403: a 401 would be a masked 403, and a 422
would mean the request never reached authorization.

Foreign fixtures (`E2E_HIN_FOREIGN_{ORG,PROJECT,ENTRY,DOCUMENT,MILESTONE,KEY_DATE,EOT}_ID`)
are seeded by the runbook as the foreign org admin. The spec never writes outside
`E2E_STAGING_ORG_ID`.

**Navbar house policy (recorded, not assumed).** Project selection is client-side only: no
`X-Project-Id` exists, and a list is scoped by its `project_id` query. A direct detail fetch
is gated on **membership** of the record's project, not on the current selection. So for a
member of A and B with B selected, the old A detail URL opens the A record. A non-member of
A is refused with "You do not have access to this register entry." and API 403. The spec
asserts exactly this and writes both outcomes to evidence. If the owner wants
selection-bound detail access, that is a behaviour change, not a certification fix.

## 8. Executable artefacts

| Artefact | Path |
|---|---|
| Browser workflow (steps 1–18), foreign-project denial, navbar switch | `client/e2e/staging/hindrance-register.spec.ts` |
| RBAC matrix, direct URL, isolation, document links, relationship links, timeline, fault, archive, references | `client/e2e/staging/hindrance-certification-api.spec.ts` |
| Run-owned fixtures + residue policy | `client/e2e/staging/hindrance-fixtures.ts` |
| Role alignment + grant | `backend/rbac_backend/scripts/align_role_contract.py` |
| Teardown guard | `scripts/staging_teardown_guard.py` (allowlist of one: `contraclaim-stg`) |
| Maintenance time gate | `scripts/check_maintenance_time_budget.py --execution-budget-minutes 120` |
| Evidence scanner | `scripts/evidence_secret_scan.py` (dismiss `AUTH_COOKIE_DOMAIN` explicitly, never redact it) |

Both specs ran locally with no target: 12 skipped. With `CONTRACLAIM_STAGING_E2E=1` and no
target the run fails ("Set E2E_BASE_URL…"). tsc and eslint are clean.

Invocation (1 worker and 0 retries are forced by `playwright.config.ts` whenever
`E2E_BASE_URL` is set):

```bash
CONTRACLAIM_STAGING_E2E=1 E2E_RUN_ID=<run> npx playwright test e2e/staging/hindrance-certification-api.spec.ts e2e/staging/hindrance-register.spec.ts
```

**Timeline fault injection.** The code has no injection hook, so the fault is staging-only
and reversible:

1. Read and record the current `project_events` validator (`db.getCollectionInfos({name:"project_events"})`).
2. **Arm:** `collMod project_events` with a validator that rejects only `title` matching
   `g3-<RUN_ID>-TLFAIL`, `validationAction: "error"`. Then run the fault test with
   `E2E_HIN_TIMELINE_FAULT=armed`. Expect `timeline_sync_status=failed` and a
   `delay_events.timeline_sync_failed` audit row.
3. **Disarm:** `collMod` back to the validator recorded in step 1. Then run with
   `E2E_HIN_TIMELINE_FAULT=disarmed`. Expect the retry to report synced, a second retry to
   return the same `timeline_event_id`, and exactly one event for the pair
   `(delay_event, id)`.

This runs on the staging Mongo only (`-p contraclaim-stg`), never on production.

## 9. Residue policy

- Every run-owned object carries `g3-<RUN_ID>` (projects, documents, milestones, key dates,
  EOT submission references, register titles).
- Teardown (`archiveRunOwned`) soft-removes every active relationship link on a run-owned
  entry, then archives the entry with reason `certification teardown <RUN_ID>`. **Nothing
  is deleted.**
- The residue report (archived, already archived, links removed, failures, residual ids) is
  written to `E2E_EVIDENCE_DIR`. Any failure fails the run.
- Irreversible residue, recorded rather than removed: the frozen baselines of projects A and
  B, the EOT submissions, and the run-owned documents and targets.
- Segment A/B staging volumes go when the stack is torn down (`down -v` under the teardown
  guard). What survives is the evidence directory.

## 10. Images and execution budget

- The register changes backend and client source, so the R-A9E/R-A9G certified images do
  **not** cover it. Build `backend` (which also serves as `contract-worker` and
  `document-worker`) and `client` from the exact candidate tree on the host, inside the
  window's pre-stop phase. Record the digests and use them for both segments. Build a new
  image only; never retag a certified production image to point at it.
- Compose render: `docker compose -p contraclaim-stg -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.staging.yml --env-file .env.staging config`.
  The rendered output resolves secrets, so it must never be copied into evidence.
- Execution budget **120 min**: the newest superset measurement is R-A8M's full Gate
  2/3/7/8 rehearsal at 59 m 23 s, doubled. Recovery reserve 90 min. Window 4 h (D15).
- Recovery: after R-A9I, production Falkor is compose-managed, so the recovery is the
  standard ordered `up -d` of the production project. Before R-A9I it would not be:
  `depends_on falkordb` would start the stale original. That is why D1a exists.

## 11. Remaining before HINDRANCE STAGING PRE-OUTAGE READY

1. **R-A9I complete** (Falkor compose normalization; earliest 2026-09-22; not started — production
   still runs `contraclaim-falkordb-cutover`, original `contraclaim-falkordb-1` exited).
2. Candidate HEAD re-stated after this phase's commit, **CI green on that exact HEAD**.
3. Window scripts written and reviewed for this run: segment A restore/measure, segment B
   initializer + account/project/foreign-fixture seed, the fault arm/disarm pair, image build.
   The specs exist; the shell steps around them do not yet.
4. Staging `.env.staging` still carries the R-A9E-era values; the new `E2E_HIN_*` variables
   need declaring (names only in the repo, values in the window).
5. Staging checkout moved to the candidate SHA. **Superseded by §14:** the candidate is now the
   CL-3A integration branch `feat/cl3a-hindrance-scope-integration`, not PR #22 alone.
6. Owner grants the window (4 h, clock time set by the owner).


## 12. Review dispositions (standards + spec review, 2026-09-22)

Fixed:
- Every unsafe request in the specs now re-checks the target (`guardedSession` routes the
  CSRF header through `assertTargetIsNotProduction`). Register-entry bodies name
  `organization_id` explicitly (`entryBody`), and the teardown checks the target first.
- Document unlink sends `expected_revision` (without it the endpoint answers 422).
- A document from project B (same organisation) linked to a project-A entry must be refused
  with 403. Previously only a foreign-organisation document was tried.
- Timeline "update in place" is proved by the edit alone, with no manual re-sync.
- The fault flow now includes the UI: the browser spec's disarmed phase asserts "not synced",
  the **Retry timeline sync** button, and the repair. The API disarmed phase then proves the
  retry is idempotent (same `timeline_event_id`, exactly one event).
- The residue teardown also soft-removes active `entity_document_links` rows and reports them.
- A grant outside the release definition has status `refused` and makes the command exit 3,
  not 2. The duplicated lifecycle ladder is now one helper (`_lifecycle_status`).
- The API spec asserts `E2E_STAGING_PROJECT_ID == E2E_HIN_PROJECT_A_ID`; the seeder request
  context is disposed; the reverse-lookup assertion matches `hindrance.id` exactly.

Recorded, not changed:
- **Document-link audit**: no read API exposes `document_relationship.linked/unlinked`. The
  spec writes the link id to evidence, and the runbook reads `audit_events` on the staging
  Mongo for those ids (both actions expected).
- **Inspect = dry-run**: `align_role_contract` without `--apply` is `align(dry_run=True)`,
  so the "dry-run" step and the "inspect" step are the same run.
- **Regression test grants `dms.hindrance.view`**: this is deliberate. Without read access
  the positive controls (200 list and detail) could not prove that the 403 on write is an
  authorization answer.
- **Grant cache**: the grant is additions-only. A holder's cached refusal can outlive it for
  one cache TTL, which errs restrictive. There are 0 holders today.
- **Navbar direct URL — OWNER RULING NEEDED.** Spec 10 says the old A detail URL must be
  "refused according to current house policy". Current house policy (CLAUDE.md "the gate
  answers membership") means that for a member of A it opens. The spec records that
  outcome. If the owner wants detail access bound to the selected project, that is a new
  behaviour, not a certification fix.

## 13. Active project scope (owner decision 2026-09-22)

**Policy: ENFORCED on the register, its `/api/delay-events` compatibility routes, the
`delay_event` document-link routes and the document reverse lookup's Hindrance rows.**
`EffectiveScope = Entitlement ∩ Navbar Selection`.

- **Mechanism.** This is a minimal port of the codex `tenant_context` design into
  `backend/rbac_backend/core/tenant_context.py`: the `X-Org-Id` / `X-Proj-Id` headers, the
  `selection_required` (400) and `context_forbidden` (403) codes, and a
  `tenant.context.rejected` audit event on every refusal. A selected project must exist, be
  active, belong to the selected organisation, and pass
  `ScopeService.is_client_scope_allowed`, the membership test `PolicyService` uses. Ids are
  read from headers only; `project_id` on these routes stays a filter and can never widen
  the selection.
- **Client.** `services/active-scope.ts` holds the selection. `TenantContext` is its only
  writer and updates it synchronously on a switch, before the routed page remounts.
  `createHttpClient` (the common request layer) adds both headers to every API request.
- **No selection.** The list stays bounded by `build_scope_query`, never global. Every
  record-level or mutating route returns 400 `selection_required`, and the project is never
  inferred from the record or the body.
- **Superadmin.** An explicit selection binds superadmin too. With no selection, a broad
  list follows the existing rules and record-level operations are 400.
- **Refusal disclosure.** A record refusal echoes no ids. The audit event keeps the target.
- **Other targets are unchanged.** On the shared routes the selection is only validated
  when the target is `delay_event`, so a stale header never refuses another register's link.
  (CL-3A: generalised to every adapter with `active_scope_enforced` - Variation and
  Hindrance - see §14.)

**Staging consequences:**
- The navbar project selector is live only for superadmin, orgadmin and orguser
  (`TenantContext.canSwitchProject`). The browser workflow and the navbar test therefore
  run as `E2E_HIN_ORGADMIN`. `E2E_HIN_PA_AB` (projectadmin assigned to A and B) proves
  CASE A/B at the API.
- `contractmgr_org` has no org-wide project reach in `ScopeService` (only orgadmin and
  orguser do). The `E2E_HIN_CONTRACTMGR` account must therefore be **assigned to project
  A**, or its selection of A is refused.
- Every staging API call names its project (`inProject(session, projectId)`), exactly as
  the browser does.

**Recorded, not changed (for the owner):**
- A projectadmin assigned to several projects cannot switch between them in the navbar.
  That is existing behaviour. Under the new boundary it means such a user can only work in
  the project `TenantContext` picks for them.
- **CROSS-MODULE ACTIVE-SCOPE CONSISTENCY DEBT.** (CL-3A closed it for Variation, §14.) Variation, IPC/Billing, Insurance, Bank
  Guarantee, Key Dates, Documents, Claims and the other project-scoped modules enforce
  membership but not the selected project. The register is now stricter than they are.
  Follow-up ticket: ManishPandey21/contraclaim-dms#24. PR #22 does not change those
  modules.

## 14. CL-3A integration update (2026-09-22)

PR #22 is no longer the staging candidate on its own. It was merged (not rebased) into
`feat/cl3a-hindrance-scope-integration`, stacked on CL-2 (`feat/cl2-variation-correspondence-links`
@ `e819884`, itself on CL-1 @ `19775a2`). PR #22 @ `5bfd0dd` stays the preserved source checkpoint.
**The final integrated candidate SHA is the CL-3A PR HEAD at the moment its CI is green**; restate it
in the window record, never this document's text.

What the certification run must now also cover:

1. **CL-1/CL-2 relationship architecture.** Hindrance Document links go through
   `entity_document_links` via `DelayEventEntityAdapter` (target type `delay_event`), with the CL-1
   guarantees: ObjectId-keyed Documents presented as strings, orphan unlink, exactly-once
   `document_relationship.linked` / `.unlinked` audit, and the correspondence role refusing a
   non-incoming/outgoing Document with 422. `delay_event_links` remains for programme milestone,
   key date and EOT submission links only. The `/api/delay-events` raw `linked_document_ids` write
   is kept (G31-certified); record whether any staging caller still uses it.
2. **Variation scope test.** Variation is now bound by the same selection: as `E2E_HIN_PA_AB`
   (A+B), selected A -> Variation A 200; selected B -> Variation A 403 `context_forbidden` on
   GET/PUT/DELETE and on `/api/entities/variation/{A}/document-links`; no selection -> 400
   `selection_required`; the Variation list follows the selection. Seed one run-owned Variation in
   each of A and B (`g3-<RUN_ID>` in `variation_number`); teardown deletes nothing - record them as
   residue.
3. **Link-to-Record integration.** From a run-owned incoming letter in A: Link to Record ->
   "Hindrance / Constraint" -> the A entry is offered (archived entries are not) -> link as
   correspondence -> the letter's Linked Records shows both the Variation and the Hindrance ->
   under B neither is shown -> unlink each independently -> the letter remains.
4. **Unselected list gate.** With no project selected, `GET /api/hindrances` and
   `/api/delay-events` as a project-tier member must return 200 bounded to its projects (CL-3A
   fixed a 403 "No active subscription" here that only real seeds exposed).
5. The mocked browser workflow `client/e2e/cl3a-cross-register-scope.spec.ts` is development
   evidence only; the staging specs must repeat its steps against the real stack.

Gate D1a is unchanged: **no same-host staging until R-A9I Falkor normalization is complete and the
owner grants the maintenance window.** Nothing in CL-3A starts, schedules or approves it.

