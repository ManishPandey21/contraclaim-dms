# Claim Bench — staging acceptance runbook (4B-44)

Plan: `docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md`, ticket 4B-44, sections 6.7 and 9 (criteria 4 and 10).

**Status (2026-11-18): not run.** The deployed-stack suite exists. No staging stack, canary tenant or accounts have been made available to this work, so no staging run has been made and no staging criterion is met. Nothing in this document is evidence until section 6 holds two consecutive green runs.

## 1. What counts, and what does not

| Evidence | Counts for staging acceptance? |
|---|---|
| Mocked Playwright (`e2e/claim-journey.spec.ts`, `skills-review.spec.ts`, `arbitration-pleading-gates.spec.ts`, `letter-bench.spec.ts`) | **No.** They prove component behaviour. `playwright.config.ts` excludes them from any run with `E2E_BASE_URL` set. |
| In-process real-route test (`tests/integration/test_claim_journey_http_mongo.py`, CI real-Mongo step) | **No.** It is real routes, real RBAC and a real MongoDB replica set, in one process with a scripted model and seeded users and documents. It is the precondition: it proves the calls the staging suite makes are the right calls. |
| `client/e2e/staging/claim-journey-api.spec.ts` against a deployed stack, `CONTRACLAIM_STAGING_E2E=1`, **two consecutive green runs** on the same deployment, each with its build identifiers and evidence file | **Yes**, for the staging part of criterion 4. Criterion 10 also needs the journey to reach an approved pleading (section 5). |

A run with a skipped test, a retried test or a missing variable is not a green run: under `CONTRACLAIM_STAGING_E2E=1` a missing variable fails the run, and the deployed-stack configuration runs one worker with no retries.

## 2. Preconditions on the staging stack

Staging only. `assertTargetIsNotProduction` refuses any host in `E2E_PRODUCTION_HOSTS`, and fixtures address only `E2E_STAGING_HOST`. Nothing here is run against production, and no production data, configuration or rollout setting changes.

1. **Build.** Staging runs one recorded commit of `claude/zen-ptolemy-695liz` (or its successor), backend, workers and client built from it. Record the backend image's `org.opencontainers.image.revision` label: no API exposes it (finding JG-6), so pass it as `E2E_CJ_RELEASE_SHA`.
2. **Migrations.** `migrate_database --fail-on-warning` dry run, then apply, inside the backend container. The catalogue is 32 (Gate 6 fresh install `gate6-20261118t082257z-1668`). If staging is a restored production copy, Gate 6 bullet 3 and readings P-02/P-03 govern first; do not apply on a copy whose readings are not taken.
3. **Rollout, staging only.** `CLAIM_BENCH_ROLLOUT_MODE=canary` with the canary organisation alone in `CLAIM_BENCH_CANARY_TENANT_IDS`; `LETTER_BENCH_ROLLOUT_MODE=canary` with the same organisation in `LETTER_BENCH_CANARY_TENANT_IDS`. Production stays `off`.
4. **Tenant.** An internal canary organisation (`E2E_STAGING_ORG_ID`) with a `claims_commercial_desk` subscription; two projects used for nothing else (Contractor perspective, Engineer perspective), each with its contract ingested so the notice clause and clause 8.4 resolve in the clause index.
5. **Documents.** In the Contractor project, an incoming notice letter and an outgoing record letter, both ingested through the normal upload path.
6. **Skill.** The run's head test needs `indian-infra-claims` resolvable for drafting on staging. In Phase 4b no pack is published anywhere; on staging this needs the pack published **on staging only**, through the 4B-05 review workflow with a passing run on a real golden set (4B-42), which does not exist yet. Until then, step 2 of the journey cannot run against a real model, and this suite cannot pass. This is a blocker, not a setting to work around.
7. **Accounts** (section 3), each a real user in the tenant with exactly the grants named.

## 3. Accounts and variables

The spec header lists every variable; none is ever printed. Summary:

| Variable | Who |
|---|---|
| `E2E_BASE_URL`, `E2E_STAGING_HOST`, `E2E_PRODUCTION_HOSTS`, `E2E_STAGING_ORG_ID` | Target, allowlist, production refusal list, canary tenant |
| `E2E_CJ_CONTRACTOR_PROJECT_ID`, `E2E_CJ_ENGINEER_PROJECT_ID`, `E2E_CJ_FOREIGN_PROJECT_ID` | The two projects; the foreign user's own project |
| `E2E_CJ_NOTICE_DOCUMENT_ID`, `E2E_CJ_RECORD_DOCUMENT_ID` | The two letters (precondition 5) |
| `E2E_CJ_NOTICE_CLAUSE_NO`, `E2E_CJ_NOTICE_QUOTE`, `E2E_CJ_NOTICE_PERIOD_DAYS` | The notice clause, its wording, its period |
| `E2E_CJ_ORGADMIN_*` | Organisation admin: claims, bench, notice, escalation, drafts, reviewer grants |
| `E2E_CJ_VERIFIER_*` | A second person (claim manage, clause read) who verifies the notice period; also the pleading approver |
| `E2E_CJ_ENG_AUTHOR_*`, `E2E_CJ_ENG_REVIEWER_*` | Two people holding `dms.claim.determine` by name in the Engineer project (four-eyes) |
| `E2E_CJ_ENG_DRAFTER_*` | Client drafter in the Engineer project (`drafting.request.create`) |
| `E2E_CJ_STAFF_DRAFTER_*` | ContraClaim staff with an expert allocation for the Engineer project (reads the pre-filled Letter Bench; finding JG-2) |
| `E2E_CJ_LEGAL_REVIEWER_*`, `E2E_CJ_QUANTUM_REVIEWER_*` | Two people with `dms.arbitration.approve` in the Contractor project |
| `E2E_CJ_FOREIGN_*` | A user of another organisation |
| Optional: `CONTRACLAIM_STAGING_E2E`, `E2E_CJ_RELEASE_SHA`, `E2E_RUN_ID`, `E2E_EVIDENCE_DIR`, `E2E_IGNORE_HTTPS_ERRORS` | Evidence switch and records |

## 4. Running it

From `projectDMS/client`, on a machine that reaches staging, with the variables in the environment (never on the command line):

```bash
CONTRACLAIM_STAGING_E2E=1 npx playwright test e2e/staging/claim-journey-api.spec.ts
```

Then, without changing the deployment, run it a second time. Each run writes `claim-journey` evidence (run id, build identifiers, claim, case and draft ids) to `E2E_EVIDENCE_DIR`. Attach both evidence files and the two Playwright reports to section 6.

**Cost:** two head-test model calls, one determination proposal, one submission composition, one Letter Bench analysis per run.

**Residue:** the run archives its hindrance and deletes its Engineer claim and Statement of Claim draft. The escalated Contractor claim and its arbitration case remain: no route deletes a case, and escalation cannot be undone once a pleading version exists. Their ids are in the evidence file; remove them by hand on staging after both runs.

## 5. What the journey reaches today, and the gaps it records

The in-process test (CI) walks the same calls and asserts where each stops. The staging run will measure the same points. Findings from writing it (2026-11-18), numbered for the register:

| # | Finding | Severity | Effect on acceptance |
|---|---|---|---|
| JG-1 | No three-question start (`POST /claims/start`, 4B-20 not built); the journey starts with `POST /api/claims` | Plan gap | Step 1 differs from the plan |
| JG-2 | A client drafter can open a Letter Bench pre-filled from a determination (`drafting.request.create`) but cannot read it: `GET /api/letters/{id}/bench` needs `drafting.request.view`, which a client user holds only through a staff expert allocation (403 `missing_expert_allocation`) | High (journey step 6 broken for the Engineer's own drafter) | RBAC decision needed (compare RU-1); not changed here |
| JG-3 | Any claim-edit change on a locked board unlocked it silently, against D-22. **Fixed 2026-11-18:** every board change on a locked board is refused (409 `claim_board_locked`) until the audited unlock route reopens it with a reason | Was High; fixed | The journey test and the staging spec now expect the refusal, then the audited unlock |
| JG-4 | A time head whose days rest on a linked hindrance escalates with no evidence, so the Statement of Claim reports "records supporting the amount or days pleaded" missing | Medium | Blocks a valid pleading for time heads |
| JG-5 | An approved Statement of Claim cannot be reached without the model and a governed workflow: hand-written versions never pass validation; case readiness has seven open checks, two needing the jurisdiction agent; approval needs a run at `draft_approval_gate` with plan and legal-review receipts, and `ARBITRATION_ENGINE_ROLLOUT_MODE` is off | Blocks criterion 10 | Criterion 10 needs a staging run with the arbitration engine enabled for the canary tenant: an owner decision, outside Phase 4b's non-goals as written ("changing the arbitration engine, rollout mode") |
| JG-6 | No endpoint exposes the backend `RELEASE_SHA` | Low | Build identity recorded by hand |
| JG-7 | Smaller: self-approval of a determination answers a bare 403 with no `code`; determination submit takes an internal user id; custom arbitration reviewers need a built-in role key for case row access; the submission view says `can_export: true` while not approved for issue | Low | Recorded |

## 6. Results

| Run | Date | Deployment (backend SHA, client asset hash) | Result | Evidence |
|---|---|---|---|---|
| — | — | — | Not run | — |

Staging acceptance for 4B-44 needs two consecutive rows reading green on the same deployment. Until then 4B-44 is **built, not accepted**, and exit criteria 4 (staging part) and 10 are not met.
