# C-25 / C-26 / C-27 hotfix proposal: arbitration cases and SLA deadlines invisible to tenant users

Status: **PROPOSAL, NOT APPLIED, NOT DEPLOYED.** Prepared 2026-11-16 under owner decision 4
(Phase 4b); C-27 added on the owner's instruction the same day. Deploying it needs separate
owner approval. Whether production is actually affected is decided by reading P-13 and the
production `subscriptions` rows, neither of which has been taken (AQ-11).

Patch: [`docs/hotfix/C25_C26_C27_arbitration_and_sla.patch`](hotfix/C25_C26_C27_arbitration_and_sla.patch)
(`git format-patch`; SHA-256 `509e0e2ee94d4afc69cf65576d1068621cbd41e2ea2200cee50af5104bd0e6c2`).
It replaces the earlier C-25/C-26 patch. It is based on `34d664a`, the commit production was
last read to run (2026-11-15), and applies unchanged to the tip of `release/contraclaim-rc1`
(`6d65bc3` on 2026-11-16).

## The three defects

| ID | What happens | Cause | Evidence class |
|---|---|---|---|
| C-25 | A case created through `POST /api/arbitration/cases` answers 404 to `GET /api/arbitration/cases/{id}`, is absent from every list, and cannot be updated, readiness-approved or used to prepare a draft | `ArbitrationCase.deleted_at` defaults to `None` and is stored as `null`; the case queries asked for `{"deleted_at": {"$exists": False}}`, which a stored null does not match. The in-memory test double ignores `$exists`, so no unit test saw it | Repository-inferred; reproduced on a real MongoDB 8.0.4 replica set at `34d664a` and at `6d65bc3` |
| C-26 | `GET /api/arbitration/cases` and `GET /api/arbitration/drafts` answer **403 "No active subscription is configured for this project or organization"** to every tenant user (org admin, project admin) when the request names no `organization_id`. The arbitration pages call both with no parameters (`ArbitrationDraftingPage.tsx`, `ArbitrationCaseWorkspacePage.tsx`), and the Claim Bench escalation dialog sends only `project_id`. Super Admin is unaffected, because it skips the subscription check | The two list routes pass the query's `organization_id` (None) to `PolicyService.authorize`; the entitlement check finds no subscription without an organisation. Other register routes (for example `bank_guarantees`) fall back to `current_user.organization_id`. Production refuses to start with `RBAC_ENTITLEMENT_FAIL_OPEN` set, so the fail-open path cannot hide it there | Repository-inferred; reproduced through the production `app` over real HTTP, MongoDB and RBAC at `34d664a` and `6d65bc3`. Whether production users meet it depends on its `subscriptions` data, which has not been read |
| C-27 | `GET /api/sla/upcoming` and `GET /api/sla/breached` answer the same 403 to every tenant user. The SLA tracker page (`SLATrackerPage.tsx`) and the claims register's deadline strip (`ClaimsRegisterPage.tsx`) call them with no organisation. The organisation's own notice windows (`sla_rules`) are also never looked up, so the default windows apply | `routers/sla.py` passes the query's `organization_id` (None) both to `authorize` and to `SlaService.list_sla`, which reads `sla_rules` by it | Same as C-26 |

C-25 or C-26 alone would make arbitration look empty to tenant users; together they may explain
why production records zero arbitration cases and runs. C-27 would leave the SLA tracker and the
register's deadline strip empty, or refused, for tenant users. Only the readings can confirm any
of this.

## The change (8 lines of application code)

- `services/arbitration_drafting/case_workspace.py` (C-25): the four case queries (`list_cases`,
  `get_case`, `update_case`, readiness approval) use `{"deleted_at": None}`, which matches a
  missing **or** null field. A soft-deleted case (a date) is still excluded. Matrix rows and
  drafts have no `deleted_at` field in their models, so their `$exists` queries are right and
  are not changed.
- `routers/arbitration_drafting.py` (C-26): the case list and the drafts list pass
  `organization_id or current_user.organization_id` to `authorize`.
- `routers/sla.py` (C-27): both routes pass `organization_id or current_user.organization_id`
  to `authorize` and to `list_sla`.

The row scope filter (`build_scope_query`) is unchanged everywhere, so rows are still bounded to
what the user may see. The fallback is the user's own active organisation, so it widens nothing.

Not included: the Phase 4b coercion of text notice windows (a `sla_rules` value of `"28"` makes
the SLA routes raise). That is a separate change (4B-12). With C-27 applied, an organisation
whose `sla_rules` holds a text window would now reach that code path. Reading P-04 shows whether
any does; if so, the coercion should ship with this hotfix.

No migration, no data change, no new permission, no new dependency, no frontend change.

## Tests

A real-MongoDB module, `tests/integration/test_arbitration_case_lookup_hotfix_mongo.py`, runs
against the production `app` with no dependency override. It uses the existing
`RELATIONSHIP_CL2_MONGODB_URI` and the CI real-Mongo step; the patch adds it to the step list, so
the coverage rule is satisfied.

1. A case is created, found, listed (with and without a project filter) and renamed by its creator.
2. A soft-deleted case stays hidden and cannot be updated.
3. Stored rows with `deleted_at` absent, null and set are read correctly.
4. Another organisation and another project never see, list or change the case.
5. A draft is created and prepared from the case; another tenant cannot prepare it; after the case is soft-deleted, preparation answers 404.
6. The pages' unfiltered case and draft lists answer org and project admins, still refuse a user without the permission, and never show another tenant's rows.
7. (C-27) Both SLA routes answer org and project admins with no organisation in the request. The organisation's own 40-day window is applied, not the default. Another organisation sees nothing.

| Run | Result |
|---|---|
| Hotfix module on `34d664a` with the patch | 7 passed |
| The same with only the C-27 change removed | 1 failed (test 7), 6 passed |
| The same on `34d664a` without any application change | 6 failed, 1 passed (the isolation test passes either way: refusals were already refusals) |
| Hotfix module on `6d65bc3` with the patch, plus the CI coverage rule | 18 passed |
| The same on `6d65bc3` without the application change | 6 failed, 1 passed |
| `34d664a` with the patch, full backend suite | 7051 passed, 0 failed, 784 skipped (opt-in suites whose variables were not set; the CL2 real-Mongo suites ran), 4 xfailed |

The same fixes are on the Phase 4b branch: C-25 in `9fbeecc`, C-26 in `ebe20ff`, C-27 in
`5b2a508`. Their tests are in `test_arbitration_case_lookup_mongo.py` and
`test_claim_integrity_mongo.py`.

## Applying it, if approved

1. Read P-13, P-04 and the production `subscriptions` rows (read-only, section F of
   `scripts/post_cutover_readonly_checks.sh`), and record whether production is affected.
2. The owner approves the hotfix separately from Phase 4b.
3. Apply the patch to the branch production tracks (read `git branch --show-current` on the
   server first), run CI, and release through the normal certified-image path. Phase 4b is
   not part of this change, and nothing here enables the Claim Bench.

Rollback: revert the commit. There is no data change to undo.
