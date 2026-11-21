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

1. **Build.** Staging runs one recorded commit of `claude/zen-ptolemy-695liz` (or its successor), backend, workers and client built from it with `--build-arg RELEASE_SHA=<commit>`. The suite reads the backend's commit from `GET /api/build`; set `E2E_CJ_RELEASE_SHA` to the expected commit and the run fails if the deployment is a different one.
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
| `E2E_CJ_LEGAL_REVIEWER_*`, `E2E_CJ_QUANTUM_REVIEWER_*` | Two people with `dms.arbitration.approve` in the Contractor project |
| `E2E_CJ_CONTRACTS_REVIEWER_*`, `E2E_CJ_DELAY_REVIEWER_*` | Two people with `dms.arbitration.approve` in the Contractor project; the run grants them `contract_reviewer` and `delay_reviewer` |
| `E2E_CJ_SENIOR_A_*`, `E2E_CJ_SENIOR_B_*` | Two people with `dms.arbitration.approve` and `dms.arbitration.generate`; the run grants both `senior_legal_approver`. A approves readiness and the draft, B the plan (whoever approved readiness wrote the plan and may not approve it) |
| `E2E_CJ_EXPORTER_*` | A person with `dms.arbitration.view` and `dms.arbitration.export`; the run grants `export_authorizer` |
| `E2E_CJ_CLAUSE_SOURCE_ID` | The id of clause 8.4 in the Contractor project's clause index (`contract_clauses`) |
| `E2E_CJ_FOREIGN_*` | A user of another organisation |
| Optional: `CONTRACLAIM_STAGING_E2E`, `E2E_CJ_RELEASE_SHA`, `E2E_RUN_ID`, `E2E_EVIDENCE_DIR`, `E2E_IGNORE_HTTPS_ERRORS` | Evidence switch and records |

## 4. Running it

From `projectDMS/client`, on a machine that reaches staging, with the variables in the environment (never on the command line):

```bash
CONTRACLAIM_STAGING_E2E=1 npx playwright test e2e/staging/claim-journey-api.spec.ts
```

Then, without changing the deployment, run it a second time. Each run writes `claim-journey` evidence (run id, build identifiers, claim, case and draft ids) to `E2E_EVIDENCE_DIR`. Attach both evidence files and the two Playwright reports to section 6.

**Cost:** two head-test model calls, one determination proposal, one submission composition, one Letter Bench analysis per run. The pleading (step 9) makes no model call.

**Residue:** the run archives its hindrance, deletes its Engineer claim and its started claim, and revokes the reviewer roles it granted. The escalated Contractor claim, its arbitration case, the approved (locked) Statement of Claim and its workflow run remain: no route deletes a case or an approved pleading, and escalation cannot be undone once a pleading version exists. Their ids are in the residue file; remove them by hand on staging after both runs.

## 5. What the journey reaches today, and the gaps it records

The in-process test (CI) walks the same calls and asserts where each stops. The staging run will measure the same points. Findings from writing it (2026-11-18), numbered for the register:

| # | Finding | Severity | Effect on acceptance |
|---|---|---|---|
| JG-1 | No three-question start (`POST /claims/start`, 4B-20 not built). **Fixed 2026-11-18:** `POST /api/claims/start` (what happened, when aware, which contract) creates a draft claim and opens its board with the first head; New Claim asks the three questions, with More details as the full form | Was a plan gap; fixed | Step 1 starts a claim from three questions, then records the journey's claim with its notice and figures through the full form |
| JG-2 | A client drafter could open a Letter Bench pre-filled from a determination but got 403 `missing_expert_allocation` reading it. **Fixed 2026-11-18:** the person who opened a board may read it (board, skills, steps), read-only, while they still hold `drafting.request.create` in its organisation and project. `drafting.request.view` is not widened; no write is opened; nobody else gains a read | Was High; fixed | Step 6 reads the board as the Engineer's own drafter; no staff account needed |
| JG-3 | Any claim-edit change on a locked board unlocked it silently, against D-22. **Fixed 2026-11-18:** every board change on a locked board is refused (409 `claim_board_locked`) until the audited unlock route reopens it with a reason | Was High; fixed | The journey test and the staging spec now expect the refusal, then the audited unlock |
| JG-4 | A time head whose days rest on a linked hindrance escalated with no evidence, so the Statement of Claim reported "records supporting the amount or days pleaded" missing. **Fixed 2026-11-18:** when a head's days come from its settled delay assessment, escalation carries the documents behind the hindrances and key-date EOT records linked to that head as its `evidence_ids`, under the same rules as claim evidence (restricted-evidence refusal, other-contract check) | Was Medium; fixed | The journey's pleaded heads now have no gaps; the path stops at citations, case readiness and the governed workflow (JG-5) |
| JG-5 | An approved Statement of Claim could not be reached. **Fixed 2026-11-18.** The finding's premise was wrong on two counts: the governed workflow runs with `ARBITRATION_ENGINE_ROLLOUT_MODE=off` (that setting chooses the LangGraph engine; off, the default `arbitration_v2` engine runs every gate), and drafting needs no model (the default generator is deterministic and source-grounded). What blocked it were seven defects, each fixed and tested (`tests/test_jg5_pleading_path.py`, the journey test): (a) the workflow looked selected documents up by a string `_id`, so every real (ObjectId) document was "outside case scope" (also the source-pleading lookup); (b) a draft-linked workflow read only rows bound to the draft, never the case's rows, so its matrix gate could not clear; (c) every clause source resolved as `resolution_error` against a real driver (`collection and ...` raises on a Motor collection), so no clause row was ever usable; (d) the pleading ledger cited claim, defence, counterclaim, rejoinder, quantum, notice and expert-alignment rows under types the authority registry did not know, so each was denied; (e) chronology rows carried by escalation name no event and were denied; they are now sourced from their claim, and `origin`/`origin_key` are server-written only; (f) approved issue and jurisdiction rows (required by readiness) and the project registers, added to every ledger as context, counted as inadmissible evidence and as manual facts whether cited or not; uncited they no longer do, cited they are still unverified citations; (g) draft approval and export (and rejoinder permission, filing bundle) ignored the case's scoped reviewer assignments, which the workflow's own gates honour | Was the criterion-10 blocker; fixed | The journey reaches an approved, exported Statement of Claim over HTTP (CI, in process). On staging, step 9 does the same; it needs the accounts and clause id in section 3. **(f): owner conditional approval 2026-11-18, hardened to its eight conditions (`docs/PLEADING_VALIDATION_POLICY.md`); legal sign-off still required before release** |
| JG-6 | No endpoint exposed the backend `RELEASE_SHA`. **Fixed 2026-11-18:** `GET /api/build` returns `{service, release_sha}` for any signed-in user (401 otherwise); only a commit id is reported, anything else reads `unknown`. It is under `/api` because the public gateway proxies only `/api` to the backend | Was Low; fixed | The staging spec reads it and, if `E2E_CJ_RELEASE_SHA` is set, checks the deployment is that commit |
| JG-7 | Smaller rough edges, resolved 2026-11-18 item by item: (a) self-approval of a determination answered a bare 403: **fixed**, now 403 `determination_own_findings` with the same sentence; (b) determination submit takes an internal user id: **by design**, the panel chooses the reviewer with `UserPicker`, which lists only eligible reviewers; (c) a user holding only a custom role sees no arbitration case (404): **not changed, owner decision**, `build_scope_query` grants row visibility only through built-in role keys, for every list endpoint, and whether production custom-role users also hold one is unread (P-05); (d) `can_export: true` while `approved_for_issue: false`: **by design**, a draft export is allowed, headed "DRAFT. Not approved for issue." and audited (`docs/CLAIM_BENCH.md`) | Low | (c) is the only open item |

## 6. Results

| Run | Date | Deployment (backend SHA, client asset hash) | Result | Evidence |
|---|---|---|---|---|
| — | — | — | Not run | — |

Staging acceptance for 4B-44 needs two consecutive rows reading green on the same deployment. Until then 4B-44 is **built, not accepted**, and exit criteria 4 (staging part) and 10 are not met.
