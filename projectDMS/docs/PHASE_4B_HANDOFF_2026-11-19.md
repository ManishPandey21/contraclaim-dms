# Phase 4b handoff (2026-11-19)

A compact resume point for a new session. The canonical records are still
[`PHASE_4B_TICKET_REGISTER.md`](PHASE_4B_TICKET_REGISTER.md),
[`SECURITY_REMEDIATION_2026-11-19.md`](SECURITY_REMEDIATION_2026-11-19.md), the plan
`docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md`,
[`PLEADING_VALIDATION_POLICY.md`](PLEADING_VALIDATION_POLICY.md) and `projectDMS/CLAUDE.md`.
Where this file and those disagree, they win.

## Where the work is

- **Repository and branch:** repository `ManishPandey21/contraclaim-dms`, branch `claude/zen-ptolemy-695liz`.
- **Pull request:** #114, which stays a draft.
- **Verified state:** after compaction, HEAD `fa2ab5c` matched the remote and the working tree was clean.
- **Next commit:** this file, the 4B-05 audit events and the 4B-30 register reconciliation are committed on top of `fa2ab5c`.
- **Reading CI:** use `gh api repos/ManishPandey21/contraclaim-dms/actions/runs?head_sha=<sha>`.
- **Before every push:** run the mypy gate with `pre-commit run mypy --all-files`.

## Four stages, never merged into one

1. Implemented: the code exists and its proof test ran.
2. CI-verified: the proof ran in a recorded CI run.
3. Production evidence: P-readings were taken.
4. Owner acceptance.

Rules for applying them:

- A reading that was not taken is **Pending**, never zero.
- Implementation is not production acceptance.
- Legal review (JG-5(f)) and security approval are separate from all four stages.

## Standing restrictions (unchanged)

The following must not happen:

- a merge;
- a production deployment;
- a production migration;
- a permission migration;
- a rollout;
- a silent permission change.

The following settings and states stay as they are:

- `CLAIM_BENCH_ROLLOUT_MODE=off`;
- the arbitration rollout settings are unchanged;
- skill packs stay unpublished;
- PR #114 stays a draft;
- Phase 5 stays blocked until the owner explicitly accepts.

Data, gates and scope:

- **Data:** no stored production key dates may be recalculated without project-specific approval. Historical `Other` claims and earlier notice decisions stay unchanged until P-03 has been read.
- **Gates:** do not weaken the vulnerability gate, and do not change scanning exceptions without approval.
- **JG-7(c):** custom-role row visibility is opt-in on the claims list only.

## Status of the Phase 4b tickets (from the register)

- **Implemented, and CI-verified at least for the built part:**
  - 4B-01 to 4B-03;
  - 4B-05 (complete; audit events CI-verified in run 44493207076);
  - 4B-06 to 4B-41;
  - 4B-43, 4B-45 to 4B-47 and 4B-49.

  Most of these are "not released" or wait on a P-reading.
- **Partial:**

  | Ticket | Built | Waiting on |
  |---|---|---|
  | 4B-04 | — | M-01 migration, which waits for P-02 |
  | 4B-42 | Format, rules, synthetic sets and reviewer workflow | Experts, legal approver and owner consent |
  | 4B-44 | Journey test, staging spec and runbook | **Not run on staging** |

- **Not started:** 4B-48, the defence-pleadings bundle. It is off the critical path and excluded from the exit criteria.
- **Owner acceptance:** 4B-13, 4B-14 and 4B-46 are accepted as implemented. 4B-25 and 4B-26 are provisionally accepted. None is accepted for production.

## Security remediation (SEC-1 to SEC-4, JG-7(c)), 2026-11-19

Commits: `0a62ba8` and earlier, then `1ce6730`, `f6a9156`, `14bc466` and `fa2ab5c`. CI run 44483094374 on `14bc466` passed:

- 8,410 backend unit tests;
- 1,006 real-MongoDB tests, with the Redis suite running on CI's disposable Redis;
- frontend, dependency scan and Docker.

The secret scan was red on its two known findings.

Mutation testing: 30 of 32 mutants were killed; the other 2 are equivalent.

The independent re-review found no remaining Critical or High issue. **This is not security approval.**

Fixed:

| Group | Ids |
|---|---|
| Defects | DEF-1 (project boundary), DEF-2 (open WebSocket after disable), DEF-3 (dropped `organizations`), DEF-4 (non-delegable via aliases) |
| Review findings | R-1 (built-in by name; my own regression), R-2 (alias fan-out), R-3 (project role organisation-wide), R-4/RR-1 (unscoped gates), RR-2 and L-1 to L-3 |

Open:

- **DEF-5:** string user ids get 422 on `/api/users/me`, 401 on `/api/me` and refresh. This is data-dependent and P-05 counts the users. Regression tests and design added in the third pass; fix after P-05.
- **DEF-6:** fixed in the third pass (the service rebinds on a new database handle; also covers production reconnects).
- **DEF-4 root cause (owner decision):** should `organizations:read`, `orgs:view` and `reports:view` keep satisfying the billing and subscription checks? Assessment and separation proposal in the third pass.
- **Fixed in the third pass:** `POST /permissions/check` information disclosure, and the advertised permission list including the stored `permissions` field.
- **Legacy built-in roles:** ones stored under an ObjectId without `is_system` now count as custom. P-05 counts them; audit before deployment.
- **R-3 effect:** a user whose only access is a custom project role must select the project to list claims.

## Third pass (final code hardening, 2026-11-19)

- **Done (implemented, tested, CI-verified in run 44523611467 on `a43eb6a`; secret scan red as before):**
  - 4B-05 deprecation in one transaction;
  - `POST /permissions/check` restricted to self-checks, or scoped administrators with an audit trail;
  - the advertised permission list reconciled with both gate kinds;
  - DEF-6 notification service rebinding.
- **Assessment only:**
  - DEF-4: aliases unchanged; proposal to separate billing visibility, which needs P-05 and approval;
  - DEF-5: tests and design, behind a setting, after P-05.
- **Independent review:** 7 findings fixed, 4 documented.
- **New tickets:**
  - T-1: `check_user_access` and membership rows;
  - T-2: client `useRBAC` fallback;
  - T-3: scheduler email binding.
- **Owner decisions needed:**
  - DEF-4 separation;
  - the Super Admin `["*"]` advertisement.
- **Found and pinned:** today, ordinary organisation and project members read their organisation's billing records and subscription history through the `organizations:read` alias.

## Fourth pass (T-1 to T-4, 2026-11-19)

- **Done (implemented, tested, CI-verified in run 44570828567 on `14b695b`; secret scan red as before):**
  - T-2: client permissions fail closed, including the fix for a Critical remount loop found by review;
  - T-1: user read and user list count memberships;
  - T-3: digests resolve the email service at run time;
  - T-4: skill-version index gate.
- **Design only:** DEF-4 billing separation (`docs/DEF4_BILLING_PERMISSION_SEPARATION.md`), with decisions open on project-level subscription visibility and `/plan-settings/effective-services`.
- **Open, Low:**
  - Super Admin role checks outside `can()`;
  - sign-in form on the first `/me` failure;
  - profile cache race;
  - `skill_bindings` and `skills` index gates;
  - unused client alias helpers.

## Fifth pass (DEF-4 implementation, 2026-11-19)

- **Done (implemented and tested; CI pending at the time of writing):**
  - `BILLING_EXPLICIT_PERMISSIONS`, default off; with it off, authorisation is unchanged;
  - the explicit billing policy, status and financial separation, scoped effective services, and no role-name checkout;
  - P-05 `billing_permission_impact`;
  - the measured matrix and the grant-before-revoke plan (`docs/DEF4_BILLING_PERMISSION_SEPARATION.md`).
- **Not done, by instruction:** policy not activated, no aliases removed, no roles granted, no data migrated.
- **Owner decisions:**
  - M1: per-organisation built-in roles;
  - M4: whether a tenant billing admin may edit the global plan and add-on catalogue;
  - L3: validating the new scope on `PUT /subscriptions/{id}`;
  - whether operational `features` should omit `entitlement_overrides`.

## Blockers before production authorisation

1. P-01 to P-16 production readings with authorised read-only access (AQ-11). This includes:
   - P-05 access impact, then authorised scoped re-grants;
   - P-03 for legacy `Other` claims.
2. Authorised security review, including the two historical Gitleaks findings and the picomatch R5 assessment, which stay with the security owner.
3. JG-5(f) independent legal sign-off.
4. Real expert-labelled golden sets, then the skill publication gates.
5. Two consecutive deployed staging end-to-end passes (4B-44).
6. Owner acceptance per ticket and for Phase 4b, before Phase 5.

## Next dependency-ready work (no owner decision needed)

- Done: CI on `fa2ab5c` (run 44488223050) and on `187a51d` with the 4B-05 audit events (run 44493207076) green except the secret scan.
- Everything else left in Phase 4b waits on one of these:
  - a production reading: 4B-04 M-01 (P-02) and the P-05 re-grants;
  - staging: 4B-44;
  - experts or the owner: 4B-42, and the DEF-4 decision;
  - DEF-5 after P-05; DEF-4 separation after P-05 and approval; T-1 to T-4 done (fourth pass); remaining Low items above.

  None of these should be started without that input. The security fixes would change behaviour, so they need explicit approval.
