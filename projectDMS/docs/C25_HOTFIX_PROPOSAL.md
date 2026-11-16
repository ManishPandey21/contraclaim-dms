# C-25 / C-26 hotfix proposal: arbitration cases cannot be opened or listed

Status: **PROPOSAL, NOT APPLIED, NOT DEPLOYED.** Prepared 2026-11-16 under owner decision 4
(Phase 4b). Deploying it needs separate owner approval. Whether production is actually
affected is decided by reading P-13 (and the subscription state), which has not been taken
(AQ-11).

Patch: [`docs/hotfix/C25_C26_arbitration_case_lookup.patch`](hotfix/C25_C26_arbitration_case_lookup.patch)
(`git format-patch`; SHA-256 `c163b0ffc2115d5a8daab3e644594c0b87582927d60b5a630be30701fa084cbe`).
It is based on `34d664a`, the commit production was last read to run (2026-11-15). It also
applies unchanged to the tip of `release/contraclaim-rc1` (`6d65bc3` on 2026-11-16).

## The two defects

| ID | What happens | Cause | Evidence class |
|---|---|---|---|
| C-25 | A case created through `POST /api/arbitration/cases` answers 404 to `GET /api/arbitration/cases/{id}`, is absent from every list, and cannot be updated, readiness-approved or used to prepare a draft | `ArbitrationCase.deleted_at` defaults to `None` and is stored as `null`; the case queries asked for `{"deleted_at": {"$exists": False}}`, which a stored null does not match. The in-memory test double ignores `$exists`, so no unit test saw it | Repository-inferred; reproduced on a real MongoDB 8.0.4 replica set at `34d664a` and at `6d65bc3` |
| C-26 | `GET /api/arbitration/cases` and `GET /api/arbitration/drafts` answer **403 "No active subscription is configured for this project or organization"** to every tenant user (org admin, project admin) when the request names no `organization_id`. The arbitration pages call both with no parameters (`ArbitrationDraftingPage.tsx`, `ArbitrationCaseWorkspacePage.tsx`), and the Claim Bench escalation dialog sends only `project_id`. Super Admin is unaffected, because it skips the subscription check | The two list routes pass the query's `organization_id` (None) to `PolicyService.authorize`; the entitlement check returns no subscription without an organisation. Other register routes (for example `bank_guarantees`) fall back to `current_user.organization_id`. Production refuses to start with `RBAC_ENTITLEMENT_FAIL_OPEN` set, so the fail-open path cannot hide it there | Repository-inferred; reproduced through the production `app` over real HTTP, MongoDB and RBAC at `34d664a` and `6d65bc3`. Whether production users meet it depends on its `subscriptions` data, which has not been read |

Either defect alone would make arbitration look empty to tenant users. Together they may
explain why production records zero arbitration cases and runs; only reading P-13 and the
production subscription rows can confirm that.

## The change (6 lines of application code)

- `services/arbitration_drafting/case_workspace.py`: the four case queries (`list_cases`,
  `get_case`, `update_case`, readiness approval) use `{"deleted_at": None}`, which matches a
  missing **or** null field. A soft-deleted case (a date) is still excluded. Matrix rows and
  drafts have no `deleted_at` field in their models, so their `$exists` queries are right and
  are not changed.
- `routers/arbitration_drafting.py`: the case list and the drafts list pass
  `organization_id or current_user.organization_id` to `authorize`. The scope filter
  (`build_scope_query`) is unchanged, so rows are still bounded to what the user may see; the
  fallback is the user's own active organisation and widens nothing.

No migration, no data change, no new permission, no new dependency, no frontend change.
Existing stored rows are read correctly in all three shapes (absent, null, set).

## Tests

A new real-MongoDB module, `tests/integration/test_arbitration_case_lookup_hotfix_mongo.py`,
runs against the production `app` with no dependency override, under the existing
`RELATIONSHIP_CL2_MONGODB_URI` and the CI real-Mongo step (the patch adds it to the step list,
so the coverage rule is satisfied):

1. a case is created, found, listed (with and without a project filter) and renamed by its creator;
2. a soft-deleted case stays hidden and cannot be updated;
3. stored rows with `deleted_at` absent, null and set are read correctly;
4. another organisation and another project never see, list or change the case;
5. a draft is created and prepared from the case, another tenant cannot prepare it, and after the case is soft-deleted preparation answers 404;
6. the pages' unfiltered case and draft lists answer org and project admins, still refuse a user without the permission, and never show another tenant's rows.

| Run | Result |
|---|---|
| Hotfix module on `34d664a` with the patch | 6 passed |
| The same module on `34d664a` **without** the application change | 5 failed, 1 passed (the isolation test passes either way: refusals were already refusals) |
| Hotfix module on `6d65bc3` with the patch, plus the CI coverage rule | 17 passed |
| The same on `6d65bc3` without the application change | 5 failed, 1 passed |
| `34d664a` with the patch, existing arbitration, case-workspace, route-inventory, real-Mongo coverage and domain-error tests | 459 passed, 22 skipped (opt-in process-kill and load suites), 0 failed |
| `34d664a` with the patch, full backend suite | recorded in the Phase 4b report for this batch |

The same fix, with the same tests in `test_arbitration_case_lookup_mongo.py`, is on the
Phase 4b branch (C-25 in `9fbeecc`, C-26 in this batch).

## Applying it, if approved

1. Read P-13 and the production `subscriptions` rows (read-only, section F of
   `scripts/post_cutover_readonly_checks.sh`) and record whether production is affected.
2. The owner approves the hotfix separately from Phase 4b.
3. Apply the patch to the branch production tracks (read `git branch --show-current` on the
   server first), run CI, and release through the normal certified-image path. Phase 4b is
   not part of this change, and nothing here enables the Claim Bench.

Rollback: revert the commit. There is no data change to undo.
