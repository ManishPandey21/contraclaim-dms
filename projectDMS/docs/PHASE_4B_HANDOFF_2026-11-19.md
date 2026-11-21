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

- **Done (implemented, tested, CI-verified in run 44584757939 on `73c9b41`; secret scan red as before):**
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

## Sixth pass (owner acceptance and security follow-ups, 2026-11-20)

| Commit | Change |
|---|---|
| `8a45f3b` | M4 |
| `01cdb29`, `e633a0f` | L3 |
| `5ccd918`, `8517f27`, `d0eb1db` | Decision 4 and its review fixes |
| `10714db` | M1 audit, reading and tests |

- **Done (implemented and tested; CI on the exact head: see DEF-4 doc §11):**
  - M4: global pricing restricted to Super Admin or a platform pricing role;
  - L3: subscription scope preserved on `PUT`, with compare-and-set;
  - Decision 4: minimum disclosure for callers without organisation-level standing;
  - M1: audit, P-05 `built_in_role_scope_impact`, negative tests and the architecture plan.
- **Not done, by instruction:**
  - DEF-4 not activated, and the aliases are unchanged;
  - M1 not remediated, and no migration run.
- **Next M1 steps, each needing approval:**
  - read P-05;
  - owner decisions on the assignments needing one;
  - assignments (grant before revoke);
  - `BUILT_IN_ROLE_ASSIGNMENTS` shadow mode, then on.

## Seventh pass (DEF-4 security closure verification, 2026-11-20)

| Commit | Change |
|---|---|
| `c30f691` | Verification matrices and P-05 `flag_off_disclosure_impact` |
| `9a47109` | Contract-lease test defect |
| `a8393ce` | Review fixes F1, F2, F4; F5 tests |

**CI on `a98aa94`:** run 44718392602, green except Gitleaks.

**Verdicts:**

- N1 holds;
- platform pricing is consistent across all six evaluators.

**Open:**

- F3 (owner decision);
- M1 remediation;
- P-05 production reading;
- the authorised review.

**Next dependency-ready work (recommended):** the M1 remediation, step 1 of `docs/M1_BUILT_IN_ROLE_SCOPING.md` §4. This is the approved High-priority ticket.

- **Build:** per-assignment built-in roles behind `BUILT_IN_ROLE_ASSIGNMENTS`, default `off`, plus a `shadow` mode that only logs disagreements.
- **Out of scope:** writing any assignment, and any migration.

It is the main code-side blocker to DEF-4 activation, and it needs no production access. The M1-E1 to M1-E5 tests flip when the `on` mode is shown safe.

## Eighth pass (F3 package scope, 2026-11-20)

- **CI for the seventh pass:** run 44733660126 on `652d4e6`, green except Gitleaks. F1, F2, F4 and F5 are CI-verified.
- **F3 (`8eefa27`):** `package_id` is protected subscription scope, with compare-and-set on organisation, project and package, and other spellings refused. The writers are audited.
- **Package transfer:** designed only; not implemented.
- **Next dependency-ready work** is unchanged: the M1 shadow-mode evaluator, behind `BUILT_IN_ROLE_ASSIGNMENTS`, default `off`.

## Ninth pass (subscription security remediation, 2026-11-20)

- **F3 confirmed complete** on CI run 44765102151 (`f09d9be`): backend 8,435; real MongoDB 1,132 across 87 modules, none skipped; frontend, dependency scan, Docker green; Gitleaks red on the 2 known findings. Not security or production approval.
- **Gateway identity** (`da322ae`, `7cabfdf`, `f64ac9f`): only checkout binds; ordinary writes refused; webhook signature, body-hash idempotency, binding, ownership, order and activation checks; migration `20261124_0001` with Gate 6 fresh install 33/33. Re-binding designed only.
- **Entitlement resolution** (`5740b06` to `6fc0705`): status-aware tiers, latest activation governs, ambiguity fails closed, lifecycle changes only on the selected row with compare-and-set. Three independent reviews; introduced findings fixed; R6-5 to R6-8 pre-existing and open for owner decision.

| Item | Implemented | Locally tested | CI-verified | Security-approved | Production-accepted |
|---|---|---|---|---|---|
| F3 package scope (`8eefa27`, `f09d9be`) | Yes | Yes | Yes: run 44765102151 (Gitleaks red, known) | No | No |
| Gateway identity protection (`da322ae`, `7cabfdf`, `f64ac9f`) | Yes | Yes (26 real-Mongo) | Yes: run 44786462934 on `51c192e` (backend 8,436; real-Mongo 1,183 in 89 modules; Gitleaks red, known) | No | No |
| Migration `20261124_0001` | Yes, not run anywhere but empty DBs | Gate 6 fresh install 33/33 (sandbox-CA caveat) | Yes: run 44786462934 (gate-6 pin 33) | No | No |
| Entitlement resolution and lifecycle guard (`5740b06` to `6fc0705`) | Yes | Yes (38 real-Mongo) | Yes: run 44791457632 on `0cb99f0` (Gitleaks red, known) | No | No |
| P-05 `subscription_security_impact` | Yes (read-only) | Yes | Yes: run 44791457632 on `0cb99f0` (Gitleaks red, known) | No | Not run on production |
| Gateway re-binding | Design only | - | - | No | No |
| Package transfer | Design only | - | - | No | No |

**Verification evidence (ninth pass):**

- **CI run 44786462934 on `51c192e`** (gateway code, entitlement to `d37824e`, Gate 6 pins 33): backend 8,436 passed, 1,278 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1183 test case(s)", 1,183 passed; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; Gitleaks failed on the 2 known findings.
- **CI run 44789094291 on `b51de70`** (all remediation code): backend 8,436 passed, 1,290 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1195 test case(s)", 1,195 passed; frontend, dependency scan and Docker build/scan passed; Gitleaks failed on the 2 known findings.
- **Local, quiet worktree at `6fc0705`** (real Redis on 6390, replica set on 27018): real MongoDB 89 CI modules, 1,196 passed, 0 failed, 0 skipped; full backend suite 9,616 passed, 375 skipped, 4 xfailed, 0 failed. `pre-commit run mypy --all-files` passed; ruff unchanged from the previous head on every touched file.
- **Final code, CI run 44791457632 on `0cb99f0`** (same code as `6fc0705`; later commits are docs-only): backend 8,436 passed, 1,291 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 89 module(s) ran: 1196 test case(s)", 1,196 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on the 2 known findings.
- CI success is not security approval.

**Next dependency-ready work** is unchanged: the M1 shadow-mode evaluator behind `BUILT_IN_ROLE_ASSIGNMENTS`, default `off`. The open billing items above need owner decisions first.

## Tenth pass (subscription lifecycle closure R6-5 to R6-8, 2026-11-21)

- **R6-8, R6-5, R6-6, R6-7 fixed** in separately reviewable commits (each defect reproduced on real MongoDB before its fix), plus gateway/database consistency for lifecycle changes and a read-only P-05 payment-integrity reading.
- **Three further independent reviews** found and drove fixes for 4 pre-existing payment bypasses (1 Critical, 3 High), 2 more High pre-existing paths, and the defects the fixes introduced; the last review found none High.
- **Behaviour changes for tenants:** direct subscription creation, changes to unbilled plans, priced add-ons, trial extensions and no-charge conversions are Super Admin only; Razorpay-bound plans change by checkout (old plan keeps billing until cancelled - warning returned); one trial per scope.

| Item | Implemented | Locally verified | CI-verified (exact head) | Security-approved | Production-accepted |
|---|---|---|---|---|---|
| R6-8 PUT allowlist + direct create (F1) | Yes | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | No |
| R6-5 trial conversion (+F2-F4, R5, A3/A4, R9) | Yes | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | No |
| R6-6 reactivation (+R8-1, B1, C6, R9-7) | Yes | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | No |
| R6-7 durable cancellation (+R7, C1-C5) | Yes | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | No |
| Gateway/database consistency for lifecycle changes | Yes | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | No |
| P-05 `payment_integrity_impact` | Yes (read-only) | Yes | Yes: run 44822992420 on `918b41f` (final code; Gitleaks red, known) | No | Not run on production |
| Checkout replacement safeguard | Design only (§16.7) | - | - | No | No |
| Gate 6 fresh install, migration `20261124_0001` | Yes | 33/33 (sandbox-CA caveat; not production evidence) | Pin verified in CI on `0cb99f0` | No | No |

**Exact-head CI for this pass:** run 44822992420 on `918b41f` (the final code; later commits `897fe22`, `8fe6e4b` and this one are docs only): backend 8,437 passed, 1,365 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 99 module(s) ran: 1271 test case(s)", 1,271 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on its 2 known findings. **Local, quiet worktree at `918b41f`:** real MongoDB 1,271 passed after the local Redis (which had stopped mid-run; 8 Redis-backed tests failed with connection refused) was restarted and `test_security_redis_mongo.py` rerun (11/11); full backend suite 9,683 passed + the same 8 re-run; frontend lint clean, 1,017 passed (3 skipped), build passed; mypy staged gate passed; ruff unchanged on every touched file. CI success is not security approval.

**Next dependency-ready action (superseded by the eleventh pass):** run P-05 `payment_integrity_impact` (read-only, with authorised access); the checkout replacement and the M1 shadow evaluator were built in the eleventh pass.

## Eleventh pass (payment integrity closure and M1 shadow preparation, 2026-11-21)

Full record: DEF-4 §17, M1 §8, security report (eleventh pass). Baseline `6913a01`. No real provider was called; nothing was deployed, migrated, rebound, refunded or cancelled; no role was migrated or re-granted; every new flag is off by default.

| Item | Flag (default) | Implemented | Locally verified | CI-verified (exact head) | Security-approved | Production-accepted |
|---|---|---|---|---|---|---|
| WS4 provider reliability (timeouts, outcome_unknown, durable checkout, cycle-end reading) | always on | Yes (`8b6733e`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No |
| WS1 checkout replacement | `CHECKOUT_REPLACEMENT_ENABLED=false` | Yes, inactive (`f40a89f`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (activation needs owner approval; Razorpay scheduled start unverified) |
| WS2 payment amount mismatch policy | `PAYMENT_MISMATCH_POLICY=off` | Yes, inactive (`21ac55c`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (grace length and activation need owner approval) |
| WS3 P-05 legacy bindings and old cancellations | read-only | Yes (`9c64db2`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | Not run on production |
| WS5 M1 shadow evaluator | `BUILT_IN_ROLE_ASSIGNMENTS=off` (`on` refused) | Shadow only (`e661b92`, `71402c3`) | Yes | Yes: run 44895171828 on `1b77419` (final code; Gitleaks red, known) | No | No (M1 approval) |

**Reviews (agents, not the authorised security review):** review 10 (cold) found 1 High (latent, R10-1), 4 Medium and 12 Low/Info; all fixed in `71402c3` except R10-17 (recorded). Further reviews: review 10 (cold) and re-reviews 11 to 14 - every finding reproduced and fixed (`71402c3`, `43ef3e0`, `fdef504`, `7420456`, `1b77419`); re-review 14 found nothing Medium or higher in a UTC deployment (DEF-4 §17.7-17.11).

**CI:** exact-head run 44895171828 on `1b77419` (the final code; later commits are docs only): backend 8,485 passed, 1,461 skipped, 4 xfailed; exhibit confinement 20; real MongoDB "all 107 module(s) ran: 1367 test case(s)", 1,367 passed, none skipped; frontend (lint, unit, mocked Playwright, build), dependency scan and Docker build/scan passed; **Gitleaks failed** on its 2 known findings. **Local at `1b77419`:** backend unit suite 8,458 passed, 0 failed (135 skipped, 4 xfailed); payment, M1 and P-05 suites on real MongoDB 359 passed; mypy staged gate passed; ruff unchanged on every touched file (one pre-existing finding in `monetization_service.py`). CI success is not security approval.

**Next dependency-ready action:** with authorised read-only access, run P-05 (`payment_integrity_impact`, `legacy_binding_and_cancellation_reconciliation`, `built_in_role_scope_impact`) and the read-only cancellation reconciliation; then owner decisions on the flags, the grace length, the remediation sequence (DEF-4 §17.4) and the Razorpay SDK dependency (§17.6).

## Blockers before production authorisation

1. P-01 to P-16 production readings with authorised read-only access (AQ-11). This includes:
   - P-05 access impact (billing and the M1 built-in role scope), then authorised scoped re-grants;
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
