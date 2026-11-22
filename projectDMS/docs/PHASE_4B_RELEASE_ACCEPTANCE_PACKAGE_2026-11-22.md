# Phase 4b release-acceptance package (2026-11-22)

**Decision: NO-GO for production release.** The code is complete for the frozen scope and CI-verified. Production evidence, security sign-off, legal sign-off, expert validation, staging acceptance and written owner acceptance are all missing. None of those can be produced by more code.

**Scope freeze (owner directive 2026-11-22).** Phase 4b feature development is frozen. A code change is permitted only for:

- a demonstrated release-blocking Critical or High vulnerability;
- a proven material financial-integrity or cross-tenant authorisation defect;
- a data-loss risk;
- a failure of an existing mandatory acceptance criterion.

This package changes no code. Every other finding is in the post-release backlog (section 8).

**How to read this document.** It is the single release register for Phase 4b. The detailed records it points to remain canonical for their subjects:

- [`PHASE_4B_TICKET_REGISTER.md`](PHASE_4B_TICKET_REGISTER.md)
- [`SECURITY_REMEDIATION_2026-11-19.md`](SECURITY_REMEDIATION_2026-11-19.md)
- [`DEF4_BILLING_PERMISSION_SEPARATION.md`](DEF4_BILLING_PERMISSION_SEPARATION.md) §16–17
- [`M1_BUILT_IN_ROLE_SCOPING.md`](M1_BUILT_IN_ROLE_SCOPING.md) §8
- [`PHASE_4B_READINGS_OPERATOR_PROCEDURE.md`](PHASE_4B_READINGS_OPERATOR_PROCEDURE.md)
- [`CLAIM_BENCH_STAGING_CHECKS.md`](CLAIM_BENCH_STAGING_CHECKS.md)
- [`SKILL_GOLDEN_SETS.md`](SKILL_GOLDEN_SETS.md)
- [`PLEADING_VALIDATION_POLICY.md`](PLEADING_VALIDATION_POLICY.md)
- [`SECRET_SCAN_EXCEPTION_REQUEST_2026-11-16.md`](SECRET_SCAN_EXCEPTION_REQUEST_2026-11-16.md)
- the plan, `superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md`: §8 (migrations and rollback) and §9 (exit criteria)

**Status vocabulary.** These are kept separate everywhere and never merged:

| Term | Meaning |
|---|---|
| Implemented | The code exists and its proof test ran |
| CI-verified | The proof test ran in a recorded CI run |
| Production evidence | Read from production by authorised read-only access |
| Security-approved | Signed off by the authorised security owner |
| Legal-approved | Signed off by the authorised legal reviewer |
| Owner-accepted | Accepted in writing by the release owner |

A reading that was not taken is **Pending**, never zero.

---

## 1. Release baseline (verified 2026-11-22)

| Item | Value | How verified |
|---|---|---|
| Repository and branch | `ManishPandey21/contraclaim-dms`, `claude/zen-ptolemy-695liz` | `git branch --show-current` |
| HEAD | `02747c010cea6107342629942bd296a1e1a60f7a` (`02747c0`). Local HEAD equals `origin/claude/zen-ptolemy-695liz` | `git rev-parse` after `git fetch` |
| Working tree | Clean before this document was written | `git status --short` |
| Final implementation commit | `1b77419`. `1b77419..02747c0` changes four documents only (37 insertions, 20 deletions) and no code | `git diff --stat 1b77419 02747c0` |
| Pull request | [ManishPandey21/contraclaim-dms#114](https://github.com/ManishPandey21/contraclaim-dms/pull/114): **open, draft, unmerged**; base `main` at `6d65bc3`; 247 commits, 467 files | GitHub API |
| CI on HEAD `02747c0` | [Run 44899347550](https://github.com/ManishPandey21/contraclaim-dms/actions/runs/44899347550), conclusion **failure**, from the Secret scan alone. See the table below | GitHub Actions job list |
| CI on final code `1b77419` | [Run 44895171828](https://github.com/ManishPandey21/contraclaim-dms/actions/runs/44895171828). See the table below | Job logs, recorded 2026-11-21 |
| Local at `1b77419` | Backend unit suite 8,458 passed, 0 failed. Payment, M1 and P-05 suites on real MongoDB: 359 passed. mypy staged gate passed | Recorded in the register |
| Flags in code (defaults) | `BILLING_EXPLICIT_PERMISSIONS=false`; `CHECKOUT_REPLACEMENT_ENABLED=false`; `PAYMENT_MISMATCH_POLICY=off`; `BUILT_IN_ROLE_ASSIGNMENTS=off` (`on` is refused at start-up); `CLAIM_BENCH_ROLLOUT_MODE=off`; `PAYMENT_PROVIDER=noop` | `core/config.py`; `docker-compose.prod.yml` line 40 |
| Skill packs | Draft only; none published | Plan; migration `20261115_0001` seeds drafts |
| Production checkout | `release/contraclaim-rc1` at `34d664a`. Last read 2026-11-15; **not re-read** | Production — verified, 2026-11-15 only |

**CI jobs on HEAD `02747c0` (run 44899347550):**

| Job | Result |
|---|---|
| Backend lint and tests (pre-commit, general suite, exhibit confinement, real-Mongo step) | Passed |
| Frontend lint, tests, mocked Playwright and build | Passed |
| Dependency vulnerability scan | Passed |
| Docker build and image scan (backend, client, LangGraph, Graphiti, Docling) | Passed |
| Secret scan (Gitleaks) | **Failed**: "leaks found: 2". These are the two known historical findings, `1125e1e…claim_bench.py:generic-api-key:60` and `d5014c6…test_claim_arbitration_readings_mongo.py:generic-api-key:57` |

**Counts at the final code (run 44895171828 on `1b77419`):**

- backend general suite: 8,485 passed, 1,461 skipped (the real-Mongo modules, which run in their own step), 4 xfailed;
- exhibit confinement: 20 passed;
- real MongoDB: "all 107 module(s) ran: 1367 test case(s)", 1,367 passed, none skipped;
- frontend, dependency scan and Docker build and scan: passed;
- Gitleaks: failed on the two known findings.

**What CI does not prove:**

- **Security:** CI success is not security approval.
- **Mocked Playwright:** the frontend job's `test:e2e` step runs mocked suites only. These do not count towards staging acceptance (section 5).

---

## 2. Release-readiness register

The last column of each table answers one question: does this item **genuinely prevent a production release** of the frozen Phase 4b code, deployed with every flag at its default?

- **Yes:** it blocks release.
- **Conditional:** it blocks only if a pending reading shows exposure.
- **No:** it does not block release. It is a Phase 5 condition or an activation condition instead.

### 2.1 Phase 4b acceptance requirements (plan §9 exit criteria)

| # | Criterion | Code state | Evidence | Blocker | Decision-maker | Blocks release? |
|---|---|---|---|---|---|---|
| 1 | 4B-01 to 4B-47 on the working branch with proof tests (4B-48 excluded) | Done, with three tickets partial: 4B-04 (M-01 waits on P-02), 4B-42 (no real sets) and 4B-44 (not run on staging) | Register; run 44895171828 | Partials, below | Release owner | No, for the code itself. The partials block through criteria 4, 6, 7 and 10 |
| 2 | Every §6.1 finding maps to a passing real-dependency test | Done | Register traceability; real-Mongo 107 modules | — | Release owner | No |
| 3 | RBAC matrix, concurrency and security suites green | Done | Run 44895171828 | — | Release owner | No |
| 4 | Mocked Playwright green in CI, **and** two consecutive deployed staging passes | Mocked suites: done. Staging: **not run** | Mocked: run 44899347550. Staging: none | No staging stack or accounts provided. A pack must be published on staging (needs a real golden set) | Staging environment owner, release owner | **Yes** |
| 5 | Gate 6 fresh install for the final migration count; route inventories diffed | Done: 33 migrations; Gate 6 33/33 | `GATE_6_FRESH_INSTALL_EVIDENCE.md` (sandbox proxy-CA caveat) | Fresh-install evidence only, not production migration evidence | Release owner | No |
| 6 | P-01 to P-16 recorded for staging and production; D-09, D-10 and AQ-3 finalised against them | Reader built and tested on a disposable database only | **None.** Every reading Pending (section 3) | No authorised operator has run it (AQ-11) | Release owner names the operator | **Yes** |
| 7 | Claims and EOT golden-set baseline runs recorded; no pack published | Workflow built; synthetic sets only | Records-only baseline. No real set, no model baseline | Reviewing experts, legal approver and data-owner consent | Experts, senior legal approver, data owner | **Yes**, through criterion 4. It is also a Phase 5 condition |
| 8 | AQ-1 to AQ-10 decided; AQ-11 resolved by access; AQ-12 decided | AQ-1 to AQ-10 and AQ-12 decided | Plan §13 | AQ-11 open: no read-only access granted | Release owner | **Yes** (same as criterion 6) |
| 9 | Owner accepts Phase 4b in writing | — | None | Everything above | Release owner | **Yes** |
| 10 | Full claim → escalation → pleading → approval journey with real dependencies | In-process real-route test passes in CI. Deployed run: none | `test_claim_journey_http_mongo.py` in CI. Staging: none | Same as criterion 4 | Staging environment owner | **Yes** |

### 2.2 The 49 Phase 4b tickets

The register's stage matrix gives each ticket's original commits and runs. Every ticket's proof tests also ran in the final-code run 44895171828 (whole suite green, Gitleaks aside). That includes 4B-20, 4B-43 and 4B-44, whose register rows still read "Pending CI".

"Owner" means the ticket's acceptance status:

| Status | Tickets |
|---|---|
| Accepted as implemented and CI-verified; not for release | 4B-13, 4B-14, 4B-31, 4B-46 |
| Provisionally accepted as implemented | 4B-23, 4B-24, 4B-25, 4B-26 |
| Not accepted | All others |

| Ticket | Subject | Code | Production or staging evidence needed | Decision-maker | Blocks release? |
|---|---|---|---|---|---|
| 4B-01 | Read-only readings P-01 to P-16 | Done (reader only) | **All P-readings Pending** | Release owner and operator | **Yes** |
| 4B-02 | Real-dependency route tests | Done | None | Owner | No |
| 4B-03 | Rollout guard | Done | None | Owner | No |
| 4B-04 | Policy floor, M-01 | Partial | P-02 (migration order) | Owner, after P-02 | **Yes**, until P-02 sets the migration order |
| 4B-05 | Transactional record, revoke, publish and deprecate | Done | None | Owner | No |
| 4B-06 | Seed reviewer identities (M-02) | Done, not released | P-05, P-10 (conflicts are reported as warnings) | Owner, after P-05/P-10 | **Conditional**: the migration warns on conflicts and `--fail-on-warning` stops it |
| 4B-07 | Reviewer role assignments | Done | P-05 legacy holders, P-10 | Owner | Conditional (P-05) |
| 4B-08 | Users screen keeps the role list | Done | P-06 | Owner | No (the fix only stops loss) |
| 4B-09 | Reviewer queue | Done | None | Owner | No |
| 4B-10 | Super Admin exclusion; explicit grants | Done | P-05 role documents | Owner | Conditional (P-05) |
| 4B-11 | Pickers and plain language | Done | None | Owner | No |
| 4B-12 | Claim create and status integrity | Done | P-04 | Owner | No (coerces defensively) |
| 4B-13 | Signals | Done | None | Owner (accepted as implemented) | No |
| 4B-14 | Notice verification with audit | Done | P-14 | Owner (accepted as implemented) | No |
| 4B-15 | Perspective (D-11) | Done | P-09 | Owner | No |
| 4B-16 | Lock and stale | Done | None | Owner | No |
| 4B-17 | Single amount source | Done | None | Owner | No |
| 4B-18 | Claim contract identity (M-04) | Done, not released | P-03 | Owner, after P-03 | Conditional (P-03; D-10) |
| 4B-19 | Lazy board coercion for legacy claims | Done, not released | P-03 | Owner, after P-03 | Conditional (P-03) |
| 4B-20 | Three-question start | Done | None | Owner | No |
| 4B-21 | Already-granted from the register | Done | P-03, P-07 | Owner | No |
| 4B-22 | Concurrency engine | Done | P-03, P-07 | Owner | No |
| 4B-23 | Contract scope on links | Done (with corrections and OD-1) | P-03 | Owner (provisional) | No |
| 4B-24 | Quantum in paise; formulas | Done (with corrections) | None | Owner (provisional) | No |
| 4B-25 | Net-EOT table and timeline | Done | None | Owner (provisional) | No |
| 4B-26 | Determination four-eyes | Done (with correction) | None | Owner (provisional) | No |
| 4B-27 | Evidence snapshot and viewer redaction | Done | None | Owner | No |
| 4B-28 | Submission validators | Done | None | Owner | No |
| 4B-29 | Word sanitiser | Done | AQ-7 decided | Owner | No |
| 4B-30 | `python-docx` behind `DOCX_WRITER` (default `minimal`) | Done | None. Production export is unchanged by default | Owner; security owner if `python-docx` is enabled | No |
| 4B-31 | Pre-filled determination and RFI letters | Done | None | Owner (accepted as implemented) | No |
| 4B-32 | Section editing with provenance | Done | None | Owner | No |
| 4B-33 | B2 pleaded-head blockers | Done | P-07, P-13 | Owner | **Conditional.** New blockers apply to drafts in flight, flag or not; P-07 counts them |
| 4B-34 | Matrix-row pickers | Done | None | Owner | No |
| 4B-35 | Citations and exhibit numbers (M-05 part) | Done | Production index and duplicates not read | Owner | Conditional (duplicates would stop the unique index) |
| 4B-36 | Escalation safety (M-05) | Done | P-02, P-07 | Owner | Conditional (P-02 order) |
| 4B-37 | Un-escalation | Done | None | Owner | No |
| 4B-38 | Validate case on attach | Done | None | Owner | No |
| 4B-39 | Approve/Export gating parity | Done | None | Owner | No |
| 4B-40 | Gate stepper, multi-select escalation | Done | None | Owner | No |
| 4B-41 | Server-side evaluation runner (index migration `20261123_0001`) | Done | P-08, P-02 | Owner | No |
| 4B-42 | Golden sets | Partial: workflow only | **Real expert-labelled sets** | Experts, senior legal approver, data owner | **Yes**, through staging step 2 (criterion 4) |
| 4B-43 | Mocked Playwright suites | Done | None (mocked only) | Owner | No |
| 4B-44 | Deployed staging suite and runbook | Partial: **not run** | **Two consecutive deployed passes** | Staging environment owner | **Yes** |
| 4B-45 | Case-less draft compatibility (D-26) | Done | P-12 (before switching to `strict`) | Owner | **Conditional.** `POST /arbitration/drafts` without a case answers 422 unless `scratch: true`; P-12 counts other callers |
| 4B-46 | Privilege wall and provenance suite | Done | None | Owner (accepted as implemented) | No |
| 4B-47 | Real-MongoDB suites in CI | Done | None | Owner | No |
| 4B-48 | Defence-pleadings bundle evaluation | Not started | — | Owner | **No.** Excluded from the exit criteria; see backlog |
| 4B-49 | Extraction-review RBAC audit | Done (audit only) | P-15 | Owner | No. Steps 2 to 5 are a separate release |

### 2.3 Security findings (SEC, DEF, M, F, R and related)

Full records: the security report and DEF-4 §11–17. "Fixed" means implemented, tested and CI-verified, and **not** security-approved.

| Finding | Severity | Origin | State | Blocks release? | Decision-maker |
|---|---|---|---|---|---|
| SEC-1 custom-role permissions pooled across organisations | High | Pre-existing | Fixed (with DEF-1 project boundary) | No, if the security owner signs off. P-05 counts role holders affected | Security owner |
| SEC-2 role carrying non-delegable permissions | High | Pre-existing | Fixed | Same as SEC-1 | Security owner |
| SEC-3 role assignment fences | High | Pre-existing | Fixed | Same as SEC-1 | Security owner |
| SEC-4 disabled account kept authenticating (with DEF-2 WebSocket) | Medium | Pre-existing | Fixed (with and without Redis) | Same as SEC-1 | Security owner |
| DEF-3 dropped `organizations` | — | Pre-existing | Fixed | No | Security owner |
| DEF-4 billing visibility through legacy aliases | Medium | Pre-existing | Separation built behind `BILLING_EXPLICIT_PERMISSIONS=false`; **not activated** | No. With the flag off, behaviour is unchanged and the exposure is a known pre-existing one. Activation is a separate decision | Owner, after P-05 |
| DEF-5 string user ids get 422/401 | Medium (data-dependent) | Pre-existing | Tests and design only | **Conditional** on the P-05 count of string-id users | Owner, after P-05 |
| DEF-6 notification service handle | — | Pre-existing | Fixed | No | — |
| T-1 to T-4 | High to Low (T-2 review found one Critical, fixed) | Mixed | Fixed | No | Security owner |
| M4 global pricing administration | Medium | Pre-existing | Fixed | No | Security owner |
| L3 subscription scope on `PUT` | Low | Pre-existing | Fixed | No | Security owner |
| F1 to F4 payment bypasses (review 7) | F1 Critical, F2 to F4 High | Pre-existing | Fixed | No, if signed off | Security owner |
| F3 package scope | Medium | Pre-existing | Fixed | No | Security owner |
| A1, A2 (review 8) | High | Pre-existing | Fixed | No | Security owner |
| R6-5 to R6-8 subscription lifecycle | Low to Medium | Pre-existing | Fixed | No | Security owner |
| R10-1 to R14-3 (reviews 10 to 14) | One High latent, then Medium and Low | Introduced and fixed | Fixed | No | Security owner |
| **M1** built-in roles reach every organisation a user belongs to | **High** | **Pre-existing; live in production today** | Audited; shadow evaluator built (default `off`); **not remediated** | **Conditional, and the security owner's call.** Releasing does not introduce or widen M1; it is live at `34d664a`. P-05 `built_in_role_scope_impact` measures who is exposed (`users_reaching_more_than_one_organisation`, `org_wide_role_reaching_a_secondary_organisation`). If that count is zero, the exposure is nil today. If it is not zero, the security owner decides between accepting the risk with a date and holding the release. Remediation is designed (M1 §4) and needs approval; it is not new scope | Security owner, then release owner |
| R10-17 M1 design interpretation | Info | Introduced | Recorded | No | Backlog |
| N3 host-clock stamps; N4 older plan resurfaces | Low | Introduced, or by design | Recorded | No | Backlog |
| R6-3 residual (gateway moved, local write refused) | Low | Narrowed window | Recorded | No | Backlog |
| Gitleaks: two historical findings | — | Pre-existing in branch history | Exception **submitted, not applied**; gate unchanged and red | **Yes.** A mandatory CI gate fails | Security owner |
| picomatch GHSA-fc5q-39xc-vjpf; R5 exception proposal | High (ReDoS, build tooling) | Pre-existing | Patch proposed, not applied; the dependency scan currently passes because upstream advisory data changed | **Yes, until the security owner closes it** | Security owner |

### 2.4 Gateway and subscription changes

| Change | Flag | Behaviour change at release with defaults | Blocks release? | Decision-maker |
|---|---|---|---|---|
| Gateway identity protection: only checkout binds; webhook signature, body-hash idempotency, binding and order checks | Always on | Webhooks with an ambiguous or unverified binding are refused | **Conditional.** P-05 `gateway_bindings_by_class`, and migration `20261124_0001` raises on duplicate gateway ids, webhook bodies or payment records | Owner and finance, after P-05 |
| Entitlement resolution: status-aware; ambiguity fails closed | Always on | A scope with two or more unstamped governing rows **denies** (`ambiguous_subscriptions`) | **Conditional.** P-05 `subscription_security_impact` and `overlapping_governing_scopes`. Tenants in those scopes lose access on release until resolved | Owner, after P-05 |
| R6-5 to R6-8: allowlisted `PUT`; direct create, trial extension and no-charge conversion are Super Admin only; one trial per scope | Always on | Tenant administrators lose those direct writes | No. This is an intended behaviour change; it needs owner acknowledgement in the acceptance | Owner |
| R6-7 durable cancellation; lifecycle changes as durable operations | Always on | New `subscription_operations` collection; review queue | No | Owner |
| WS4 provider reliability: timeouts, `outcome_unknown`, no second create after a timeout | Always on | With `PAYMENT_PROVIDER=noop` there are no provider calls | No | Security owner (review) |
| WS1 checkout replacement | `CHECKOUT_REPLACEMENT_ENABLED=false` | None | No. Activation is separate, needs the Razorpay test-mode verification, and is refused for Razorpay today | Owner |
| WS2 amount-mismatch policy | `PAYMENT_MISMATCH_POLICY=off` | None | No. Activation needs an owner-approved grace length (1 to 30 days) | Owner and finance |
| WS3 legacy bindings and old cancellations | Read-only reading | None | No. The remediation sequence (DEF-4 §17.4) is a separate, finance-approved programme | Owner and finance |
| WS5 M1 shadow evaluator | `BUILT_IN_ROLE_ASSIGNMENTS=off` | None | No | Owner (M1 approval) |
| DEF-4 billing separation | `BILLING_EXPLICIT_PERMISSIONS=false` | None | No | Owner |
| **Razorpay SDK absent from `requirements.txt`** | — | If production runs `PAYMENT_PROVIDER=razorpay`, the new image cannot call the provider | **Conditional.** Production's value is **not read**; the default is `noop`. If it is `razorpay`, this is a release blocker (payment failure). If it is `noop`, it is not | Operator reads it (section 3, R-PAY); owner and security owner approve any dependency change |

### 2.5 Outstanding migrations

Production last read at `34d664a` (2026-11-15), whose ledger has never been read. The catalogue on this branch has **33** migrations. Those not known to be in production:

| Migration | Effect | Data written? | Precondition reading | Rollback |
|---|---|---|---|---|
| `20261114_0001` skill registry, `_0002` seed system skills, `_0003` Letter Bench indexes (already on `main`, no production receipt) | Indexes; system skill seed | Seeds skill documents | P-02, P-08 | Drop indexes; seeded skills are unpublished |
| `20261115_0001` seed claim and arbitration skill drafts | Four packs stored as **draft** 1.0.0; approval indexes | Draft skill versions | P-08 | Remove unpublished drafts; drop indexes |
| `20261116_0001` Claim Bench indexes (M-01) | Indexes | No | P-02 | Drop |
| `20261117_0001` claim-head link indexes | Indexes | No | P-02 | Drop |
| `20261118_0001` claim-findings indexes | Indexes | No | P-02 | Drop |
| `20261119_0001` claim escalation index and scratch drafts | Index; **marks case-less drafts as scratch** | Yes: one flag per case-less draft; nothing deleted, no status changed | **P-07, P-12** | Drop the index; unset the scratch marker on the drafts it marked (the dry run reports the count) |
| `20261120_0001` arbitration matrix origin unique (M-05) | Unique index | No | P-07; duplicates would stop it | Drop |
| `20261121_0001` reviewer role identities (M-02) | Nine identity-only roles; skip-existing; conflicts reported as warnings | Yes: role documents with no permissions | **P-05, P-10** | Remove only while no assignment references them |
| `20261121_0002` reviewer role assignment indexes (M-03) | Indexes | No | P-02 | Drop |
| `20261122_0001` claim contract index (M-04) | Index | No | P-03 | Drop |
| `20261123_0001` `skill_eval_runs` indexes | Indexes | No | P-08 | Drop |
| `20261124_0001` subscription gateway identity unique | Partial unique indexes on gateway id, webhook body hash and payment records. **Raises and blocks later migrations while duplicates exist** | No | **P-05 `duplicate_payment_groups`** and duplicate bindings must read zero | Drop the indexes |

Release prerequisites for migrations (plan §8.2; `CLAUDE.md`):

1. Read P-02.
2. Dry-run the whole sequence with `--fail-on-warning` inside the backend container, **against a restored copy of production data**, not production.
3. Apply only with separate written authorisation.

No migration has been run anywhere but disposable databases.

### 2.6 Staging requirements

The checklist is in section 5. As of today nothing has been run on staging: no stack, canary tenant or accounts have been provided. One precondition is circular unless the owner sequences it: the journey's head test needs `indian-infra-claims` **published on staging only**, which needs a real golden set (4B-42).

### 2.7 Production readings

All Pending. The operator package is in section 3.

### 2.8 Legal and expert approvals

| Approval | State | Blocks release? | Who must provide it |
|---|---|---|---|
| JG-5(f) pleading-ledger context exemption policy, version 1 (hash `dc8d5437…7d3c`) | Owner conditional approval 2026-11-18. **No legal sign-off** | **Yes.** The policy is not flag-gated; it changes which drafts can be approved | Authorised independent legal reviewer |
| Expert-labelled golden sets, claims and EOT | None. Book, sheets, adjudication and set are all missing | **Yes**, through staging; and for any pack publication and Phase 5 | Two independent reviewing experts per pack, the `senior_legal_approver` as adjudicator, and the data owner for consent on anonymised real matters |
| Skill-pack publication | Not requested; all packs draft | No (packs stay unpublished at release) | Evaluator, legal signatory and publisher, who must be three different people (D-03) |

---

## 3. Production readings: operator package (P-01 to P-16)

### 3.1 Status

**No reading was taken.** This session has no production or staging access. AQ-11 requires an operator named by the release owner, and no such access was granted. Each reading is therefore **Pending, not zero**. Nothing in this package was inferred from the absence of data.

**Exact action needed.** The release owner must:

1. name an operator with authorised read-only shell access to the production host;
2. have them run [`PHASE_4B_READINGS_OPERATOR_PROCEDURE.md`](PHASE_4B_READINGS_OPERATOR_PROCEDURE.md) §2–4 with the scripts taken from commit **`02747c0`**;
3. then run it once more on staging.

No production mutation is involved. The reader refuses writes, `$out` and `$merge`, and it prints counts only.

### 3.2 Priority order

| Priority | Reading | Why first | Release decision it settles |
|---|---|---|---|
| 1 | **P-01** deployed commit and **running image labels** | Baseline for all the others; the image labels were never read | Which migrations and code are live |
| 2 | **P-02** migration ledger | Release order of all 33 | Migration sequence (section 2.5) |
| 3 | **P-05 `payment_integrity_impact`**: `duplicate_payment_groups`, `gateway_bindings_by_class`, `overlapping_governing_scopes`, `overlapping_gateway_bound_scopes`, `payments_refused_for_review`, `cancelled_bound_without_gateway_evidence`, `operations_needing_review`, `trial_conversions_awaiting_payment`, `no_charge_conversions` | Migration `20261124_0001` raises on duplicates; overlapping governing scopes deny on release | Whether the gateway and subscription changes block release (section 2.4) |
| 4 | **P-05 `legacy_binding_and_cancellation_reconciliation`**: bindings verified, conflicting, unverified or requiring reconciliation; old cancellations by class; per organisation, no personal data | Measures pre-R6-7 cancellations that may still bill | Finance exposure; DEF-4 §17.4 sequence |
| 5 | **P-05 `subscription_security_impact`** | Counts ambiguous scopes | Tenant access loss on release |
| 6 | **P-05 `built_in_role_scope_impact`**: `users_reaching_more_than_one_organisation`, `org_wide_role_reaching_a_secondary_organisation`, `mixed_built_in_and_custom_roles`, `mixed_org_wide_and_project_tier_roles`, `billing_admin_reaching_more_than_one_organisation`, `projects_outside_the_primary_organisation`, `unrecognised_role_keys` | Exposure under M1 | Security owner's M1 decision (section 2.3) |
| 7 | **P-05 `billing_permission_impact`, `flag_off_disclosure_impact`, `custom_role_compatibility`**, reviewer role documents | Permission impact of DEF-4 and SEC-1 to SEC-3; M-02 conflicts | DEF-5 and DEF-4 decisions; M-02 seeding |
| 8 | **P-05 `built_in_role_shadow_divergence`** | Expected empty: shadow is off. Any data there is unexpected and should be reported | — |
| 9 | **R-PAY (operator step, not in the script)**: read whether `PAYMENT_PROVIDER` is set in the running backend and its value, which is not a secret, as P-10 does for its variable. Do not print any other variable | Razorpay SDK absence (section 2.4) | Whether the SDK gap blocks release |
| 10 | P-03, P-04, P-06, P-07, P-12, P-13, P-14 | Claim and draft compatibility | 4B-18, 4B-19, 4B-33, 4B-45, `20261119_0001`, D-10 |
| 11 | P-08, P-09, P-10, P-11, P-15, P-16 | Remaining | M-01 and M-02, D-09, AQ-3, 4B-49, KD-1 |

**R-PAY command.** Replace `<backend>` with the container name from `docker ps`. Run it on the host, never with a credential on the command line:

```bash
docker exec <backend> sh -c 'if [ -n "${PAYMENT_PROVIDER+x}" ]; then echo "PAYMENT_PROVIDER set: $PAYMENT_PROVIDER"; else echo "PAYMENT_PROVIDER unset (default noop)"; fi'
```

**Read-only cancellation reconciliation route.** This is `GET /api/plan-settings/reconciliation/cancellations`. It exists only on this branch, so it **cannot** be run against production until the code is deployed. It is listed for the post-deployment verification, not for the pre-release readings.

### 3.3 Evidence template

Use the template in the operator procedure §5, one block per environment, and add this row:

```text
R-PAY PAYMENT_PROVIDER in the running backend |  |  |
```

| Reading | Production | Staging |
|---|---|---|
| P-01 | Checkout read 2026-11-15 (`34d664a`); image labels **Pending** | Pending |
| P-02 to P-16 | **Pending** | **Pending** |
| R-PAY | **Pending** | **Pending** |

Results go in `POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md` §6, after the by-value secret scan reads CLEAN.

---

## 4. Security sign-off evidence package

**For:** the authorised security owner.

**Question:** may the frozen Phase 4b code (PR #114 at `02747c0`, final code `1b77419`) be released with every flag at its default? Answer each item independently.

No scanning gate, allow-list or exception has been changed by this work. The agent reviews referred to below (reviews 7 to 14 and earlier) are **not** the authorised review.

### 4.1 Gitleaks: two outstanding findings

- **Findings.** `generic-api-key` on `claims.v1.head_test` at `1125e1e:…/services/claim_bench.py:60`, and on `claim-private-1` at `d5014c6:…/tests/integration/test_claim_arbitration_readings_mongo.py:57`. CI reports exactly these two on every run, including run 44899347550 on HEAD.
- **Owner's assessment.** Neither is a credential: one is a prompt-template name, the other a synthetic test id. The current files pass a directory scan with no exceptions.
- **Proposed action, not applied.** A `.gitleaksignore` holding exactly the two fingerprints. Controls 2 to 9 in the request show that real secrets are still caught in those same files, and that the same line in a new commit is caught.
- **Alternatives.** A squash merge, or a history rewrite, which is not authorised.
- **What is asked.** Confirm both values at their commits, approve or reject the fingerprint exception, and record the decision in the request's table.
- **The gate stays red until then.**

### 4.2 picomatch (GHSA-fc5q-39xc-vjpf) and the R5 proposal

- **The advisory.** A ReDoS in picomatch up to 4.0.7. It is build and test tooling only, absent from the runtime client image (the Docker scan passes). Installed: `picomatch@2.3.2` (via jest-util) and `4.0.4` (via rollup-plugin-visualizer).
- **History.**
  - Run 406 failed on the new advisory: 20 HIGH findings, all rooted in it.
  - Run 437 passed with no repository change. Advisory data changed upstream: 14 moderate, no high.
  - Every later run passes, including run 44899347550.
  - No exception was used.
- **The proposal.** A maximum 30-day exception, `docs/hotfix/R5_PICOMATCH_GATE_PROPOSED.patch`, **not applied**. It carries pinned identity, versions and dependents; a runtime-source check; an expiry; and automatic failure on a patched release. 57 negative controls pass on a patched copy.
- **What is asked.**
  - Confirm what changed upstream.
  - Decide whether the advisory is closed, withdrawn or re-rated.
  - Then close R5, or apply or reject the patch.
  - Do not assume the green scan settles it.

### 4.3 Cross-tenant RBAC and M1

- **Fixed and CI-verified.** SEC-1 to SEC-4, DEF-1 to DEF-3 and DEF-6, T-1 to T-4, M4, L3, F3, and the `POST /permissions/check` disclosure. Evidence: security report sections 1–9; mutation 30/32 killed, the other 2 equivalent; re-reviews.
- **Open: M1 (High, pre-existing, live in production).** Built-in roles count in every organisation the user belongs to.
  - **Audit:** M1 §1–3.
  - **Negative tests:** M1-E1 to M1-E5, which pin current behaviour.
  - **Shadow evaluator:** M1 §8. It covers `PolicyService.authorize` allow decisions only; `build_scope_query` and the other role-name sites are not shadowed.
  - **The decision asked for:**
    1. Is M1 release-blocking, given that the release does not widen it?
    2. If not, what is the accepted-risk expiry?
    3. Approve M1 remediation step 1, the assignments with grant before revoke.
  - **Input:** P-05 `built_in_role_scope_impact` (section 3, priority 6).
- **Open: DEF-4.** Legacy aliases still expose billing reads to organisation members while `BILLING_EXPLICIT_PERMISSIONS=false`. This is pre-existing and pinned by tests. Activation is an owner decision after P-05.
- **Open: DEF-5** (string user ids). Data-dependent; P-05 counts them.

### 4.4 Payment, webhook and subscription security

- **Webhook.** Signature required; body-hash idempotency under concurrency (unique index); binding, ownership, order and activation checks; `subscription.authenticated` is not an activation (R10-12).
- **Identity.** Only checkout binds a gateway id; ordinary writes cannot set it (R6-8 allowlist); migration `20261124_0001` adds the unique guarantees.
- **Lifecycle.** Durable operations with idempotency keys, scope leases and token fencing. Gateway first, then local. A review queue. No automatic refund, rebinding or legacy cancellation.
- **Reviews.** F1 Critical and F2 to F4, A1 and A2 High (all pre-existing) were fixed. Reviews 10 to 14 found one latent High and several Medium, introduced and fixed; re-review 14 found nothing Medium or higher in a UTC deployment.
- **Residual, flags at default (DEF-4 §17):**
  - checkout over a paid Razorpay plan bills both until the old one is cancelled (Medium; latent under `noop`);
  - a renewal mismatch is flagged with no time limit (Medium);
  - an unresolvable price activates (Medium; closed only under `enforce`);
  - the Razorpay SDK is absent;
  - Razorpay scheduled start is unverified;
  - the Razorpay reference search is bounded;
  - the answer to a second cycle-end cancel is undocumented.
- **What is asked.** Accept the residuals as pre-existing with flags off, or name which must close before release.

### 4.5 Always-on provider-reliability changes (WS4)

Not flag-gated, so they ship on release:

- every provider call has a bounded timeout and classified outcome (`not_sent`, `rejected` or `unknown`);
- reads retry; mutations retry only when `not_sent`;
- an `unknown` outcome is reconciled with the same operation, and a second create under a new idempotency key is never made after a timeout;
- checkouts stopped mid-call are quarantined and resolved only by evidence or a Super Admin statement;
- abandoned checkouts are never cancelled while the provider is unreadable.

With `PAYMENT_PROVIDER=noop`, none of this calls a provider. Code: `services/payment_gateway_reliability.py`, `services/payment_gateway.py`, `services/monetization_service.py`. Tests: `test_payment_gateway_reliability.py` (35 unit), `test_provider_reliability_mongo.py`, and review modules 10 to 13.

**What is asked:** review for denial of service and lockout. In particular, a scope blocked by an `outcome_unknown` checkout stays blocked until a Super Admin resolves it.

### 4.6 Historical migration and legacy-data risks

- `20261124_0001` refuses to run, and blocks later migrations, while duplicates exist. This is fail-closed, but it can stop a release mid-sequence. Mitigation: read P-05 first, and dry-run on a restored copy.
- Legacy gateway bindings without provenance are still trusted by the webhook until classified (Medium until measured).
- Cancelled bound subscriptions from before R6-7 may still bill at the provider (unknown until measured).
- Built-in roles stored under ObjectIds without `is_system` now count as custom (SEC-1). P-05 counts them.
- `20261119_0001` writes a flag on real drafts; `20261121_0001` writes role documents (skip-existing).
- RTO and RPO are recorded but not demonstrated (post-cutover assessment, R5 risk). A rollback that needs a restore is untested at production size.

**What is asked:** approve the migration order and the precondition rule "P-05 duplicates = 0 before `20261124_0001`".

### 4.7 Sign-off record

| Item | Decision | Conditions or expiry | Security owner | Date |
|---|---|---|---|---|
| 4.1 Gitleaks fingerprints | Pending | | | |
| 4.2 picomatch / R5 | Pending | | | |
| 4.3 M1 release-blocking? | Pending | | | |
| 4.3 DEF-4 and DEF-5 residuals | Pending | | | |
| 4.4 Payment residuals | Pending | | | |
| 4.5 WS4 always-on | Pending | | | |
| 4.6 Migration and legacy risks | Pending | | | |
| 4.8 S-0 repository visibility | Pending | | | |

### 4.8 Reviewer pages: publication status (2026-11-22)

**Pages prepared:**
- *ContraClaim DMS — Phase 4b Security Release Review*: this section 4, plus a decision register and a final-recommendation block.
- *ContraClaim DMS — JG-5(f) Pleading Validation Legal Review*: section 6.1 and the policy in full.

| Item | State |
|---|---|
| Security review page URL | **None: prepared, not published** |
| Legal review page URL | **None: prepared, not published** |
| Prepared on | 2026-11-22 |
| Source commit referenced by both pages | `70d63b56e9c1643df2e7d4fd0d186e1d0621f6da` |
| Required access | Security page: the named security reviewer and the release owners. Legal page: the named legal reviewer and the release owners, and it may be more restricted. Read and comment only; no anonymous access; no link sharing; access grants logged |
| Security reviewer | Pending (not yet named or authorised) |
| Legal reviewer | Pending (not yet named or authorised) |
| Formal approval status | **Pending**, both registers |

**Why the pages are not published.**
- No approved private documentation destination is configured for this work.
- **This repository is public** (`ManishPandey21/contraclaim-dms`, read 2026-11-22), so the pages were not added to it.
- No restricted repository, wiki or review portal is reachable from the preparing session.
- No reviewer identity has been given, so no access could be granted or tested.

The prepared copies are held outside the repository, in the preparing session's private scratch space, which only the release owner can open. Repository visibility was **not** changed.

**New finding S-0.** Because the repository is public, this package, the security remediation report, the DEF-4 and M1 analyses (including open High findings) and the secret-scan exception request are publicly readable today. This is recorded for the security reviewer and the release owner. Changing visibility needs their explicit authorisation.

---

## 5. Deployed staging acceptance checklist

**Owner:** the staging environment owner. The runbook is [`CLAIM_BENCH_STAGING_CHECKS.md`](CLAIM_BENCH_STAGING_CHECKS.md).

**What counts.** Only `client/e2e/staging/claim-journey-api.spec.ts`, run against the deployed stack with `CONTRACLAIM_STAGING_E2E=1`, **twice consecutively on the same deployment**, with no skip, no retry and no missing variable. Mocked Playwright and the in-process journey test do not count.

### 5.1 Preconditions

| # | Item | Pass condition | Responsible |
|---|---|---|---|
| S-1 | Staging is not production | `assertTargetIsNotProduction` passes; `E2E_PRODUCTION_HOSTS` includes `contraclaim.com` | Staging owner |
| S-2 | One recorded build | Backend, all four application services (including `document-worker`) and the client are built from one commit, with `RELEASE_SHA` set; `GET /api/build` returns that commit; `E2E_CJ_RELEASE_SHA` is set | Staging owner |
| S-3 | Readings on staging | Operator procedure run on staging (section 3); P-02, P-03 and P-05 read **before** migrations if staging is a restored production copy | Operator |
| S-4 | Migrations | `migrate_database --list`, then a `--fail-on-warning` dry run inside the backend container, then apply. Expected: 33 migrations | Staging owner |
| S-5 | Flags (staging only) | `CLAIM_BENCH_ROLLOUT_MODE=canary` and `LETTER_BENCH_ROLLOUT_MODE=canary` for the canary organisation alone. Defaults kept: `BILLING_EXPLICIT_PERMISSIONS=false`, `CHECKOUT_REPLACEMENT_ENABLED=false`, `PAYMENT_MISMATCH_POLICY=off`, `BUILT_IN_ROLE_ASSIGNMENTS=off`, `PAYMENT_PROVIDER=noop`. Production stays `off` throughout | Staging owner |
| S-6 | Tenant and data | An internal canary organisation with a `claims_commercial_desk` subscription; Contractor and Engineer projects with contracts ingested; one notice letter and one record letter uploaded through the normal path | Staging owner |
| S-7 | Skill on staging only | `indian-infra-claims` published **on staging only**, through the 4B-05 workflow, with a passing run on a **real** golden set (4B-42). Without it the suite cannot pass. This is a blocker, not a setting to work around | Experts, legal approver, staging publisher |
| S-8 | Accounts | Every account in runbook §3, each a real user with exactly the grants named; four-eyes pairs are different people | Staging owner |

### 5.2 What the two runs must show

| Area | Check | Evidence |
|---|---|---|
| Claim to arbitration | Three-question start → board → head test → notice verified by a second person → lock → determination with four-eyes (Engineer) → Letter Bench read by the client drafter → escalation → case → Statement of Claim drafted from the escalated claim → every gate approved by separate reviewers → export | Spec steps 1–9; evidence file with run, build, claim, case and draft ids |
| RBAC boundaries | A foreign-organisation user gets 403/404 on every journey object; Super Admin is excluded from privileged routes; four-eyes self-approval is refused (`determination_own_findings`); a locked board refuses changes (409 `claim_board_locked`) until the audited unlock | Spec assertions |
| Permissions | Reviewer roles granted by assignment only; revoked at the end of the run; no permission migration | Residue file; audit events |
| Word and PDF output | Pleading DOCX and PDF open and parse. The `minimal` writer and sanitiser are used (`DOCX_WRITER` default). Context rows are absent from the filed annexure list and source schedule (JG-5(f) control 6) | Exported files attached to the evidence |
| Migrations | Dry run clean; 33 applied; `20261119_0001` count matches the dry run | Migration output |
| Subscription compatibility | The canary tenant's subscription resolves (not `ambiguous_subscriptions`); with `noop`, no provider call is made; plan settings are read-only for the tenant admin where R6-8 says so | Spec plus one plan-settings read |
| Rollback rehearsal (recommended) | Set `CLAIM_BENCH_ROLLOUT_MODE=off`: Bench routes return 404 and the register keeps working | Note in the results |

**Razorpay on staging.** Do **not** run Razorpay tests on staging under this release. The SDK is not in the requirements, and adding or activating it needs the approved dependency change and security assessment. Any Razorpay check (scheduled start for WS1, second cycle-end cancel) is to be run later, only in an authorised Razorpay **test-mode** account, after that approval. It is **not** a Phase 4b release condition while production uses `noop`.

### 5.3 Results (to fill)

| Run | Date (UTC) | Backend SHA | Client asset hash | Result | Evidence file |
|---|---|---|---|---|---|
| 1 | — | — | — | Not run | — |
| 2 | — | — | — | Not run | — |

---

## 6. Legal and skill approvals

**Nothing in this section can be replaced by tests.** Synthetic sets and in-repository tests are not expert validation, and none is offered as such.

### 6.1 JG-5(f) legal review

- **Material for the reviewer:** [`PLEADING_VALIDATION_POLICY.md`](PLEADING_VALIDATION_POLICY.md). It gives the policy and its hash, the eight owner conditions with their enforcement and tests, and the known limits (paraphrase without a reference is not detected as reliance).
- **Ask:**
  1. Approve or reject policy version 1 (hash `dc8d5437926780b2c8d19fb17f51086257d6d68e59f234bcede4b5d6fcb87d3c`) for filing validation.
  2. Name any condition for release.
- **If the policy changes:** a change bumps the version and hash, and versions validated under the old hash lose their exemptions.
- **Reviewer:** the authorised independent legal reviewer, not the owner. **Record:** reviewer, date, decision and conditions in the policy's status line.
- **State:** Pending.

### 6.2 Expert-labelled golden sets (claims and EOT)

The workflow is in [`SKILL_GOLDEN_SETS.md`](SKILL_GOLDEN_SETS.md), steps 1–8. For each pack it needs:

| Input | Provider | State |
|---|---|---|
| Matter book (15 matters, composition rules met, anonymised) | A reviewing expert as matter author | Not written |
| Consent reference for each anonymised real matter | Data owner | None |
| Two independent label sheets with attestation | Expert A and Expert B (different people) | None |
| Adjudication of disagreements | `senior_legal_approver`, who is neither expert | None |
| Restricted, encrypted evaluation store (AQ-10) | Operations | Not confirmed |
| Server run by someone who will not approve the pack | Evaluator | Not run |

**Release owner action:** name the experts, the adjudicator, the data owner and the store.

---

## 7. GO / NO-GO

### 7.1 Decision

**NO-GO.** Release can be reconsidered only when every "Yes" blocker in 7.3 is closed and each "Conditional" item is resolved by its reading.

### 7.2 What is ready (evidence)

- **Frozen scope.** Code is complete for 4B-01 to 4B-47, except the three partials, which wait on people or environments rather than code.
- **Security and payment fixes.** SEC, DEF, T, M4, L3, F and R6 are implemented, as are the payment-integrity workstreams.
- **Defaults are safe.** Every new behaviour that is not a fix is behind a default-off flag.
- **CI.** Run 44899347550 on HEAD and run 44895171828 on the final code are green except Gitleaks. At final code: 8,485 backend tests, 1,367 real-MongoDB tests in 107 modules with none skipped, and the frontend, dependency and image scans.
- **Migrations.** Gate 6 fresh install 33/33.
- **Pre-production tooling.** The read-only reader, the operator procedure, the staging suite and runbook, the golden-set workflow, and the secret-scan exception request with its evidence all exist and are tested.

### 7.3 Genuine release blockers

| # | Blocker | Closed by |
|---|---|---|
| B-1 | P-01 to P-16 and R-PAY not read on production or staging (exit criteria 6 and 8) | An authorised operator (section 3) |
| B-2 | Secret scan gate red: two historical Gitleaks findings | Security owner decision (4.1) |
| B-3 | picomatch and R5 not closed by the security owner | Security owner (4.2) |
| B-4 | No authorised security sign-off of the release, including the M1 decision | Security owner (4.3–4.6) |
| B-5 | JG-5(f) legal sign-off missing; the policy ships ungated | Legal reviewer (6.1) |
| B-6 | No real expert-labelled golden set. This blocks staging step S-7 | Experts, adjudicator, data owner (6.2) |
| B-7 | No deployed staging run; two consecutive passes required (exit criteria 4 and 10) | Staging owner, after B-6 |
| B-8 | Migration dry run on a restored production copy not done | Operator, after B-1 |
| B-9 | No written owner acceptance of Phase 4b (exit criterion 9) | Release owner, last |

**Conditional blockers.** Each becomes a "Yes" only if its reading shows exposure:

| # | Becomes a blocker if | Reading |
|---|---|---|
| C-1 | Duplicate payment groups or duplicate gateway bindings are non-zero (`20261124_0001` cannot run) | P-05 |
| C-2 | Overlapping governing scopes are non-zero (tenants denied on release) | P-05 |
| C-3 | `PAYMENT_PROVIDER=razorpay` in production (the SDK is absent from the image) | R-PAY |
| C-4 | Users reaching more than one organisation through built-in roles are non-zero, **and** the security owner rules M1 release-blocking | P-05 |
| C-5 | Drafts in flight newly blocked, or case-less draft callers other than the bundled client | P-07, P-12 |
| C-6 | M-02 role conflicts, or string-id users (DEF-5) | P-05, P-10 |
| C-7 | P-03 values that the legacy coercion (4B-18, 4B-19) does not cover | P-03 |

If a conditional blocker fires, the permitted response is limited:

- data remediation under separate approval; or
- a code change only if it meets the scope-freeze rule;
- not new features.

### 7.4 Exact outstanding owner approvals

1. **Name the people:** the operator for the readings, the staging environment owner, the reviewing experts, the adjudicator, the data owner, the security owner and the legal reviewer, if not already named.
2. **Accept the always-on behaviour changes** for tenants (section 2.4): R6-5 to R6-8 restrictions, ambiguity denial, webhook refusals, and new approval blockers on drafts in flight.
3. **Approve the migration order** after P-02, and authorise applying migrations to staging, then separately to production.
4. **Decide D-09, D-10 and AQ-3** against P-03, P-05 and P-10 (exit criterion 6).
5. **Approve the staging-only publication** of `indian-infra-claims` once a real set passes.
6. **Accept Phase 4b in writing** (exit criterion 9), naming per-ticket acceptance for those still "Not accepted".
7. **Not needed for this release** (activation decisions, deferred):
   - `BILLING_EXPLICIT_PERMISSIONS`;
   - `CHECKOUT_REPLACEMENT_ENABLED`;
   - `PAYMENT_MISMATCH_POLICY` and its grace length;
   - `BUILT_IN_ROLE_ASSIGNMENTS=shadow`;
   - the Razorpay SDK;
   - the DEF-4 §17.4 remediation sequence;
   - M1 remediation;
   - `ARBITRATION_CASELESS_DRAFT_MODE=strict`;
   - `DOCX_WRITER=python-docx`.

### 7.5 Production migration and rollback prerequisites

**Before deploying:**

1. A fresh, verified backup, with a restore tested on that backup. RTO is not yet demonstrated, so the owner accepts this risk explicitly or schedules a timed restore.
2. Readings P-01, P-02 and P-05 in hand, with every C-1 to C-7 condition resolved.
3. The 33-migration sequence dry-run with `--fail-on-warning` on a restored production copy, with no warning, run inside the backend container.
4. A certified image set from one commit, **retagged, never rebuilt**, covering all four application services. The deploy uses `--no-deps --no-build`, never a blanket `up` (FalkorDB topology, `CLAUDE.md`).
5. Production flags at defaults, recorded before and after: `CLAIM_BENCH_ROLLOUT_MODE=off`, all four billing/M1 flags off, `PAYMENT_PROVIDER` unchanged.

**Rollback** (plan §8.5 plus this pass):

| Change | Rollback | Data effect |
|---|---|---|
| Application | Retag and start the previous certified images | New collections (`subscription_operations`, `payment_discrepancies`, `rbac_shadow_observations`, `reviewer_role_assignments`, `skill_eval_runs`) are ignored by the old code and kept. With `noop`, no in-flight provider operation is orphaned |
| Bench exposure | `CLAIM_BENCH_ROLLOUT_MODE=off` | Board data kept |
| Index migrations | Drop the indexes | None |
| `20261119_0001` scratch marker | Unset the marker on the counted drafts | None deleted |
| `20261121_0001` reviewer identities | Remove only if unreferenced; otherwise revoke the assignments first | — |
| Seeded draft skills | Leave or remove; they are unpublished | — |
| `20261124_0001` | Drop the unique indexes | None |

Every migration is additive or index-only, and none deletes user data.

### 7.6 Deferred improvements (not release blockers)

The post-release backlog (section 8) holds every finding that does not meet the scope-freeze rule. Each activation in 7.4 item 7 is a separate decision with its own evidence, and none of it is part of this release.

### 7.7 Recommended release sequence

1. **Readings.** The owner names the operator, who runs P-01 to P-16 and R-PAY on production and on staging (B-1). Resolve C-1 to C-7.
2. **In parallel, no dependency on 1:**
   - the security owner reviews section 4 (B-2 to B-4);
   - the legal reviewer reviews JG-5(f) (B-5);
   - the experts and adjudicator build the claims set (B-6).
3. **Owner decisions:** D-09, D-10 and AQ-3; the migration order; acceptance of the behaviour changes (7.4 items 2–4).
4. **Dry run.** 33 migrations on a restored production copy (B-8).
5. **Staging.** Deploy one commit; readings; migrations; the claims pack published on staging only (S-7); two consecutive passes (B-7).
6. **Applying any approved security decision.** Applying the `.gitleaksignore` or the R5 patch is a change to a scanning gate, made only by or with the security owner's written approval, then CI rerun on the exact head.
7. **Owner acceptance.** Written acceptance of Phase 4b (B-9). Only then merge, by squash or as the owner directs.
8. **Production.** Deploy with the flags `off`. Run post-deploy verification, including the read-only cancellation reconciliation route. Then the canary per plan §8.3 (one internal tenant for at least 14 days) under separate approval.
9. **Phase 5** stays blocked until step 7.

---

## 8. Post-release backlog

These are recorded, not scheduled. None meets the scope-freeze rule today. If a reading changes the evidence for an item, it is re-assessed against that rule.

| ID | Item | Severity | Evidence | Rationale for deferral |
|---|---|---|---|---|
| BL-1 | M1 remediation: per-assignment built-in roles, then `shadow`, then enforcement | High (pre-existing) | M1 §1–8; P-05 pending | Designed and approved in principle. Needs P-05, security review and owner approval; release does not widen it. **It moves into scope if the security owner rules it release-blocking (C-4)** |
| BL-2 | DEF-4 activation (`BILLING_EXPLICIT_PERMISSIONS`) with grant before revoke | Medium | DEF-4 §1–15 | Needs P-05 and owner approval; pre-existing exposure |
| BL-3 | DEF-5 string user ids | Medium (data-dependent) | Security report §5 | Fix designed; waits on P-05 count (C-6) |
| BL-4 | WS1 checkout replacement activation; Razorpay `start_at` verification in test mode | Medium (latent under `noop`) | DEF-4 §17 | Needs the SDK dependency decision and test-mode evidence |
| BL-5 | WS2 amount-mismatch activation and grace length | Medium | DEF-4 §17 | Owner and finance decision |
| BL-6 | Razorpay SDK into requirements (`pkg_resources` dependency) | Blocks `razorpay` only | DEF-4 §17.6 | Dependency and security review; not needed under `noop` (unless C-3) |
| BL-7 | Legacy binding and old-cancellation remediation sequence | Unknown until measured | DEF-4 §17.4 | Finance-approved, four-eyes programme after P-05 |
| BL-8 | Gateway re-binding; package transfer | — | DEF-4 §16 | Designed only |
| BL-9 | Razorpay reference search bounded to 5 pages; second cycle-end cancel undocumented | Low | DEF-4 §17.8–17.11 | Fails safe (manual check or review) |
| BL-10 | R10-17 M1 design interpretation; N3 host-clock stamps; N4 older plan resurfaces; R6-3 residual window | Info/Low | Security report; DEF-4 §17.7 | Recorded; no integrity loss shown |
| BL-11 | M1 shadow coverage of `build_scope_query` and other role-name sites | Low | M1 §8 | Shadow is diagnostic only |
| BL-12 | Fourth-pass Low items: Super Admin role checks outside `can()`; sign-in form on first `/me` failure; profile cache race; `skill_bindings`/`skills` index gates; unused client alias helpers | Low | Security report, fourth pass | No exposure shown |
| BL-13 | RU-1 claim-signal visibility | Usability/RBAC (open) | Register | Owner decision |
| BL-14 | JG-7(c) custom-role row visibility beyond the claims list; JG-7(c) arbitration case visibility for custom-role-only users | Owner decision | Runbook §5 JG-7 | Opt-in by design |
| BL-15 | 4B-48 defence-pleadings bundle evaluation | — | Plan D-28 | Excluded from exit criteria |
| BL-16 | Phase 4 deferrals: dispute ladder, limitation placeholder, Traverse Board, version diff, exhibit chips, counterclaim agent, opponent upload; Approve button not disabled by the panel | — | PR #114 description | Server already refuses; UI only |
| BL-17 | 4B-49 steps 2 to 5 (extraction-review access) | — | AQ-12 | Separate release; needs P-15 |
| BL-18 | `ARBITRATION_CASELESS_DRAFT_MODE=strict`; `DOCX_WRITER=python-docx` | — | 4B-45, 4B-30 | Need P-12 / security review respectively |
| BL-19 | C-25 arbitration case-lookup hotfix | Depends on P-13 | `C25_HOTFIX_PROPOSAL.md` | Production exposure unread |
| BL-20 | KD-1 key-date correction | Depends on P-16 | KD-1 doc | No recalculation without P-16 and project approval |
| BL-21 | Client `npx tsc -b` pre-existing errors | Low | `CLAUDE.md` | Build unaffected |
| BL-22 | GitHub Actions Node 20 deprecation warnings (`checkout@v4`, `setup-python@v5`, `gitleaks-action@v2`) | Low | Run 44899347550 annotations | CI still runs; change outside the frozen scope |

---

## 9. Record of this package

| Date | Event |
|---|---|
| 2026-11-22 | Package written at HEAD `02747c0` under the scope freeze. No code, flag, gate, exception, migration or production state changed. No production or staging access was used. |
