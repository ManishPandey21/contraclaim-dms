# R-A9F open launch-gate matrix — what is still open, and how each item is disposed

**Produced 2026-09-20 in release programme R-A9F, offline, on release HEAD after the
fast-forward to certified candidate `fe728b205ed5dfb20d88040bc44214ca71d11c43`.**

The release gate's own preamble says production promotion is blocked until every launch gate
is checked **or formally disposed**. The readiness score reaching its target does not discharge
that; the score counts checkboxes and implements no cross-gate blocking, which the gate document
says in as many words. This file is the formal disposition, bullet by bullet, so that "85/100"
and "may we promote?" stay two separate questions.

**Nothing in R-A9F ticked a bullet.** Every disposition that needed the owner was marked
`OWNER DECISION REQUIRED` and listed again, consolidated, at the end. No item was ticked,
withdrawn, or re-scoped in order to reach a number.

> **UPDATED 2026-09-20 (R-A9G-0) — the owner has now decided.** The verbatim decisions are
> `docs/R_A9G_OWNER_DECISION_RECORD.md`. Six of the seven required owner acts are recorded;
> the seventh, the maintenance-window grant, is not. Gate 9 bullets 1, 2, 5 and 6 are now
> **CHECKED**, so Gate 9 is 6/6 and the scorer reads **91/100, raw 90.67**. That move is four
> checkboxes and **no new measurement**: the figure the sealed R-A9E staging evidence measured
> is **85 / raw 85.33** and is unchanged. Gate 3 b3-b9 remain open and are now **OWNER-ACCEPTED
> DEBT (accepted 2026-09-20, follow-up deadline 2026-10-15)** rather than *proposed*. The tables
> below carry the owner's answer in each row.

## Scored gates — state after the R-A9E evidence was applied

| Gate | Checked | Weight | Points |
|---|---|---:|---|
| Gate 1: CI And Local Test Baseline | 6/6 | 15 | 15.00 |
| Gate 2: Live Integration Baseline | 6/6 | 10 | 10.00 |
| Gate 3: Browser E2E Coverage | **2/9** | 12 | 2.67 |
| Gate 4: Security And RBAC | **10/10** | 15 | 15.00 |
| Gate 5: Upload And Content Safety | 6/6 | 10 | 10.00 |
| Gate 6: Database, Migrations, And Seeds | 6/6 | 10 | 10.00 |
| Gate 7: Deployment And Environment | 7/7 | 10 | 10.00 |
| Gate 8: Backup, Restore, And Rollback | 8/8 | 10 | 10.00 |
| Gate 9: Final Production Readiness Review | **6/6** (was 2/6) | 8 | 8.00 |
| **Total** | | **100** | **91 (raw 90.67), verdict "Ready"** - was 85 (raw 85.33) at R-A9F, which is the figure the sealed R-A9E evidence measured |

Eleven scored bullets were open at R-A9F: Gate 3 b3–b9 and Gate 9 b1, b2, b5, b6. **After the
owner's decisions of 2026-09-20, seven remain open — Gate 3 b3–b9, all carried as dated
owner-accepted debt — and the four Gate 9 bullets are checked.**

## The disposition matrix

Legend for **DISPOSITION**: `PASS` · `N/A` (documented architecture reason) ·
`SUPERSEDED` (rationale preserved and a structural guard exists) ·
`OWNER-ACCEPTED DEBT` (the gate framework permits acceptance; needs a named owner) ·
`OWNER DECISION REQUIRED` (nothing may be recorded until the owner decides).

| GATE | BULLET | CURRENT STATUS | REQUIRED FOR INITIAL PRODUCTION PROMOTION? | EVIDENCE | OWNER DECISION? | DISPOSITION |
|---|---|---|---|---|---|---|
| 3 | b1 login/logout/session refresh/CSRF | **CHECKED** | yes | `client/e2e/staging/session-authentication.spec.ts`, R-A8Q Stage B 2026-09-08, 4/4 | no | PASS |
| 3 | b2 Org-Admin permission save/retrieve | **CHECKED (R-A9F)** | yes | `client/e2e/staging/org-admin-permissions.spec.ts`, R-A9E Stage B 2026-09-20 on `fe728b2`, 12/12, 1 worker, 0 retries | no | PASS |
| 3 | b3 document upload/view/download/share/denial | OPEN — spec exists, `EXECUTABLE_PARTIAL`, never executed against a deployment; the **share-delivery** leg additionally needs a staging SMTP sink | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 3; `docs/GATE_3_EXECUTION_PLAN.md` | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15.** The authorization half of this bullet (upload, view, download, and the 403/404-not-401 denial on the detail route, the download and the listing) is covered by backend tests, and its tenant-boundary half by Gate 4 bullet 8, which R-A9E measured on a deployment. What is unproven is the *browser* path and the *delivery* of a share. Neither is a new risk introduced by this release. |
| 3 | b4 contract upload/ingestion/clause/search/Q&A/appraisal | OPEN — only mocked coverage exists, and mocked evidence can never satisfy this gate; unticked deliberately in R-A8J | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 4 | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15.** Requires a live staging run with OCR/OpenAI/Qdrant/FalkorDB enabled (P0-002), which is itself accepted debt. |
| 3 | b5 contract timeline link verify/reject | OPEN — never executed against a deployment | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 5 | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15.** |
| 3 | b6 chronology create/extract/verify/export/attach | OPEN — never executed against a deployment | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 6 | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15.** |
| 3 | b7 arbitration draft create/generate/edit/version/approve/export | OPEN — **and explicitly NOT superseded.** R-A8S opened a proposal to withdraw it on the premise that the arbitration engine is non-primary; re-derived from source, that premise is false for the *drafting* surface, and the owner withdrew the proposal | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/PRODUCTION_READINESS_RELEASE_GATE.md`, "Bullet 7 is NOT superseded"; guard `test_release_gate_specification.py::test_the_arbitration_drafting_path_is_independent_of_the_langgraph_rollout` | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15, and PRIORITISED: the owner named b7 first for post-release closure precisely because it is deterministic and costs no model spend.** Satisfiable today in deterministic mode at zero model cost; it simply has never been run. Recording it as debt keeps the bullet alive; withdrawing it would pay ~1.33 points on a premise the source contradicts, which this programme refuses. |
| 3 | b8 empty and loading states across the route inventory | OPEN — already **AMENDED** once (owner decision 8-C, 2026-09-09): the unfalsifiable error-state half was relocated to the component/mocked layer with its own guard, and the denominator became the generated 92-route inventory. The amendment **earned no point**; the bullet stays open until a staging run measures the states | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_ROUTE_INVENTORY.md`; guards `test_gate3_route_inventory.py::test_the_relocated_error_coverage_does_not_shrink` (holds the located error tests at ≥36) and `::validate_gate3_state_bullet` (fails if a Gate 3 bullet asks for an error state again or drops the denominator) | **YES** | **SUPERSEDED in part (error state, already recorded and guarded) + OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15, for the remainder.** The acceptance keeps the structural guards and the generated route denominator mandatory. 49 of the 85 applicable routes still record `MISSING - no fault-injection coverage yet`; that is named debt with a floor, not a lowered bar. |
| 3 | b9 desktop and mobile smoke for primary workflows | OPEN — `login-responsive.spec.ts` and `blog.spec.ts` run unmocked at desktop/tablet/390 px over the public and login surfaces only | **NO — CONFIRMED by the owner, 2026-09-20** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 9 | **YES** | **OWNER-ACCEPTED DEBT — ACCEPTED by the owner, 2026-09-20, follow-up deadline 2026-10-15.** |
| 4 | b8 production Org-Admin permissions manually validated | **CHECKED (R-A9F)** | yes | `docs/GATE_4_B8_ORG_ADMIN_VALIDATION_MAPPING.md`, R-A9E Stage B 2026-09-20 on `fe728b2`, all 14 rows PASS with same-run positive controls | no | PASS |
| 9 | b1 critical blockers closed | **CHECKED (R-A9G-0, 2026-09-20)** — by formal disposition, not by closure. Previously OPEN and **structurally blocked**: the gate document states bullet 1 must not be ticked while any row of the Current Critical Blocker Register is Open, and that is enforced rather than described. P0-002 and P0-008 are Open; P0-003 and P0-007 are Partially mitigated | **YES — it is the gate's own promotion condition** | Current Critical Blocker Register; `backend/rbac_backend/tests/test_gate9_scoreability.py` | **YES** | **DECIDED (owner, 2026-09-20): option (a) — convert, then tick.** P0-002 moved from `Open` to `Accepted debt - Owner approved for initial cutover`, follow-up **2026-10-15**; P0-008 closed on the b6 sign-off, the fifth and last of its named required resolutions. P0-003 and P0-007 stay Partially mitigated and were never `Open` under the rule. **P0-002 is FORMALLY DISPOSED, not technically closed** — the combined live integration proof has still never been captured, and the register row says so in its own cell. |
| 9 | b2 high-severity risks fixed or explicitly accepted | **CHECKED (R-A9G-0, 2026-09-20)** | **YES** | third-party images: `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` "R-A9E cutover exception" (dated, expires 2026-10-15, remediation 2026-10-06 → 2026-10-15, post-cutover owner recorded in R-A9F); Python dependencies: P0-007 with one recorded no-fix exception; S3 posture: `docs/S3_STORAGE_POSTURE_DEBT.md` | **YES** | **DECIDED (owner, 2026-09-20): ACCEPTED.** A1–A8 explicitly accepted as bounded residual risk for this initial cutover only, each with the follow-up recorded below; A9 discharged, not accepted. The written acceptance is the evidence the bullet asks for — nothing was fixed to earn it. |
| 9 | b3 staging smoke passes after deploy | **CHECKED** | yes | `scripts/post_deploy_verify.sh`, R-A8Z Stage B 2026-09-14, exit 0, 24 PASS / 1 WARN / 0 FAIL | no | PASS |
| 9 | b4 staging smoke passes after restore drill | **CHECKED** | yes | `scripts/post_deploy_verify.sh`, R-A8Z Stage B, quiesced production archive restored, 48,032 documents, 0 failed, `CLAMAV_READINESS=OK scope=full` | no | PASS |
| 9 | b5 readiness score target is 85 or higher | **CHECKED (R-A9G-0, 2026-09-20)** — was satisfied in substance and deliberately left unticked by R-A9F | **YES** | scorer on this HEAD: 85/100, raw 85.33, verdict "Ready", with b5 **unticked** — which is exactly the non-circularity condition the gate requires and `test_gate9_scoreability.py` enforces | **YES** | **TICKED BY THE OWNER, 2026-09-20.** Measured **before** the tick: raw 85.33, displayed 85/100, target 85 — so the criterion was already met with b5 unticked, which is the non-circularity rule. The post-decision figure is 91 / raw 90.67 and the increase is **checkbox arithmetic on an already-satisfied condition, not a new staging measurement**. The sealed R-A9E evidence still measures 85 / raw 85.33 and was not re-run or rewritten. |
| 9 | b6 release owner signs off | **CHECKED (R-A9G-0, 2026-09-20)** | **YES — it is the promotion decision itself** | `docs/R_A9G_OWNER_DECISION_RECORD.md` section 6, signer **Release Owner**, dated 2026-09-20 | **DONE** | **SIGNED BY THE OWNER.** Not self-signed and not substituted. This signature is what closes P0-008. |

## Items that are not scored bullets but were raised as open

| ITEM | CURRENT STATUS | REQUIRED FOR INITIAL PRODUCTION PROMOTION? | EVIDENCE | DISPOSITION |
|---|---|---|---|---|
| **Staging SMTP sink** for the Gate 3 share-delivery leg | OPEN in the cutover checklist's standing-decisions table | **NO** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 3; `docs/GATE_3_EXECUTION_PLAN.md` | **N/A for the initial cutover — it is not an independent gate item.** It is a *sub-dependency of Gate 3 b3*, which the owner accepted as dated debt on 2026-09-20. It is a **staging** configuration, so it changes nothing in production and adding it now would buy no production assurance. The rule applied: do not add infrastructure because an old checklist mentions it. If and when the owner requires Gate 3 b3 before cutover, the sink becomes required *at that point*; until then it is carried with b3 and nothing is built in R-A9F. |
| **Falkor rollback variant** (A out-of-band vs B compose-managed) | **DECIDED** | **YES — the cutover cannot be written without it** | `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` section 3 | **DECIDED (owner, R-A9F): VARIANT A**, and **RE-CONFIRMED by the owner on 2026-09-20 (R-A9G-0)** with the seventeen-step sequence restated. Recorded in that document and in the cutover checklist's standing-decisions table. Variant B is withdrawn and must not be substituted for convenience during the window. No Variant-A-specific owner choice remains open. |
| **Third-party image fixable CRITICAL/HIGH** (MongoDB 273, FalkorDB 148, Qdrant 84, httpd 51 = 556) | **EXCEPTED, dated, and ACCEPTED by the owner 2026-09-20** | no longer an independent blocker until expiry | `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` "R-A9E cutover exception" + the R-A9F post-cutover-ownership table | **OWNER-ACCEPTED, DATED.** Expires **2026-10-15**; remediation window 2026-10-06 → 2026-10-15, httpd first. Not permanent acceptance: on expiry the blocker is live again for any image still on those digests. ClamAV (remediated to 1.4.6) and Redis need no exception. **Owner acceptance recorded 2026-09-20 (`docs/R_A9G_OWNER_DECISION_RECORD.md` section 8), post-cutover owner Release Owner, mandatory 2026-10-06 review trigger, not automatically renewable.** |
| **G32 production state** | NOT CERTIFIED, **NOT RUN** — **re-confirmed by the owner 2026-09-20** | **NO** | `.claude/context/contract-master/G32-PRODUCTION-MIGRATION-RUNBOOK.md`; `PRODUCTION_CUTOVER_CHECKLIST.md` section 15 | **N/A for this release, with an architecture reason.** Deploying this release *is* G32's Stage 1: the readers already ignore the shared Letter node's properties, so the release is safe without the state migration. G32 stays a **post-release certification** item and requires its own four approvals. It is **not** added to the production cutover. |
| **Backend image ships test files** (post-release hardening, raised R-A9G-0) | OPEN, **not a cutover blocker** | **NO** | `backend/.dockerignore` excludes `tests/`, which matches only a context-root `tests` directory and therefore **not** `rbac_backend/tests`; `backend/Dockerfile` then does `COPY . .`. The programme's own build-context definition counts 950 tracked files under `projectDMS/backend`, **403 of them under `rbac_backend/tests`** | **POST-RELEASE HARDENING, follow-up 2026-10-15.** Do **not** change `.dockerignore` or the Dockerfile during this release freeze — doing so would itself be a build-input change larger than the one it avoids. Post-release: verify the actual image contents (this host has no Docker daemon, so the exclusion semantics above are read from Docker's documented rules, not measured), update `.dockerignore` deliberately, prove runtime package behaviour unchanged, rebuild and re-scan, and re-earn whatever release evidence the new image stales before adopting it. |
| **F-A9B-2** (legal-words `system:admin` gate) | CLOSED IN CODE (R-A9D) | — | `docs/AUTHZ.md`, "System administration is nobody's alias" | PASS — no longer an owner decision. |
| **F-A9D-1** (production-only `billing.plan.manage` on `orgadmin`/`projectadmin`) | DECIDED (R-A9E), frozen (R-A9F) | **YES — it executes during the cutover** | `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md` | **DECIDED AND FROZEN.** Remove from those two roles only; every other production-only permission preserved and reported; no other role touched. |
| **Owner review of the 3 production roles / 4 users** whose fan-out-only authority R-A9B removed | **DISCHARGED (owner, 2026-09-20)** | **YES — it was a deployment precondition** | R-A9B receipt section 16; `docs/R_A9G_OWNER_DECISION_RECORD.md` section 7 | **DISCHARGED, NOT ACCEPTED AS DEBT.** The owner reviewed `custom#2` (2 users), `custom#3` (1) and `custom#11` (1) and decided **NO RE-GRANT**: that authority existed only through the permission-alias fan-out defect and is not in the canonical release role contract, so its removal is **intended**. No manual pre-cutover re-grant is required. If an operational need appears after cutover, the specific permission is granted explicitly through normal role administration — never by restoring fan-out. |

## Consolidated high-risk acceptance list — and the owner's answer

Presented as one list so the sign-off is a single informed act rather than eleven scattered ones.

**ANSWERED 2026-09-20 (R-A9G-0): individual disposition, not a block acceptance.** A1–A8 are
ACCEPTED as bounded residual risk **for this initial release only — these are not permanent
waivers** — each with the follow-up recorded below. A9 is **DISCHARGED**, not accepted. Verbatim:
`docs/R_A9G_OWNER_DECISION_RECORD.md` section 2.

| # | Item | What is actually unproven | Residual risk if accepted | OWNER DISPOSITION (2026-09-20) |
|---|---|---|---|---|
| A1 | Gate 3 b3–b7, b9 (six browser-E2E bullets) | The browser path through document sharing, contract ingestion/search/Q&A/appraisal, contract timeline, chronology, arbitration drafting, and mobile smoke has never been exercised against a deployed stack | A regression in one of those UI flows reaches production unmeasured. The **backend** authorization for these surfaces is covered by the suite and by Gate 4's 10/10; the gap is the browser layer and live third-party integrations | **ACCEPTED** as post-release validation debt. Close the deployed browser / live-integration gaps progressively after release, **b7 prioritised** (deterministic, zero model cost). Follow-up **2026-10-15** |
| A2 | Gate 3 b8 (empty/loading states) | 49 of 85 applicable routes have no fault-injection coverage; empty/loading states are unmeasured on a deployment | Degraded-state UI defects reach production. Floor guard prevents the located 36 from shrinking | **ACCEPTED** as bounded UI / degraded-state debt. Existing structural guards and the generated route denominator **remain mandatory**. Follow-up **2026-10-15** |
| A3 | P0-002 live AI/vector/graph integration | The live staging proof with OCR/ClamAV/OpenAI/Qdrant/FalkorDB/Redis all enabled has never been captured | Integration-level failures surface first in production | **ACCEPTED DEBT — owner approved for initial cutover.** P0-002 register status changes from `Open` to `Accepted debt - Owner approved for initial cutover`. **The underlying technical evidence is not altered or overstated.** Follow-up **2026-10-15** |
| A4 | P0-007 Python dependency audit | ~60 advisories need a coordinated FastAPI/Starlette + LangChain/LangGraph/Pydantic-AI upgrade; `ecdsa` Minerva is upstream won't-fix and unused on the HS256 path | Known-advisory exposure in dependencies, none with a demonstrated path in this application | **ACCEPTED** for the initial cutover: CI's dependency policy is green and the coordinated framework upgrade would invalidate broad release evidence. Follow-up **2026-10-15** |
| A5 | Third-party images (556 fixable C/H) | No upgrade validated for MongoDB, FalkorDB, Qdrant, httpd | Bounded to **2026-10-15**. httpd is the only image on the public path and is remediated first | **ACCEPTED** exactly as written in the dated exception. Expiry **2026-10-15**, remediation **2026-10-06 → 2026-10-15** |
| A6 | Shared production S3 bucket for documents and backups | Already accepted as post-release debt | A single bucket compromise reaches both documents and backups | **ACCEPTED** as bounded post-release architecture debt. **NOT permanent.** The post-release remediation plan **must establish an independent backup failure domain**. Follow-up **2026-10-15** |
| A7 | RPO 24 h / RTO 8 h | **RECORDED, not DEMONSTRATED** (owner decision 2026-09-08) | The stated recovery objectives have not been proven end to end on production | **ACCEPTED**; the conservative 2026-09-08 decision stands. Required follow-up: **production-scale recovery timing validation**. Quarterly staging restore drills **remain mandatory**. Follow-up **2026-10-15** |
| A8 | Arbitration acceptance in production | Zero production cases/runs; crash/restart drills and legal HITL review outstanding | Confined: `ARBITRATION_ENGINE_ROLLOUT_MODE=off`, `PRODUCTION_ACCEPTED=false` | **ACCEPTED** only while the engine is non-primary. **No future promotion to primary is authorised by this acceptance**; the independent arbitration acceptance receipt remains required |
| A9 | The 3 production roles / 4 users from R-A9B | Their fan-out-only authority was removed in code; the owner has not reviewed who they are | Someone loses authority at cutover that they were in practice relying on | **DISCHARGED, NOT ACCEPTED AS DEBT.** Reviewed; **NO RE-GRANT** — the lost authority came only from alias fan-out and is not in the canonical release role contract, so the removal is **intended** |

## The seven owner approvals — six recorded, one outstanding

Verbatim record of all six: `docs/R_A9G_OWNER_DECISION_RECORD.md`.

| # | Approval | State |
|---|---|---|
| 1 | Accept, in writing, items A1–A9 | **RECORDED 2026-09-20** — individual disposition: A1–A8 accepted, A9 discharged |
| 2 | Tick Gate 9 b2, "high-severity risks fixed or explicitly accepted" | **RECORDED 2026-09-20** — ticked |
| 3 | Decide Gate 9 b1 | **RECORDED 2026-09-20** — option (a): P0-002 converted to dated accepted debt, P0-008 closed on the b6 signature, bullet ticked. **Formal disposition, not technical closure** |
| 4 | Tick Gate 9 b5 | **RECORDED 2026-09-20** — ticked, with the pre-tick figure (raw 85.33 / 85 displayed) preserved as the non-circular basis |
| 5 | Sign Gate 9 b6 | **RECORDED 2026-09-20** — signer **Release Owner** |
| 6 | Review the 3 production roles / 4 users from R-A9B | **RECORDED 2026-09-20** — reviewed, **no re-grant**, removal intended, precondition discharged |
| 7 | **Grant the R-A9G maintenance window** | **OUTSTANDING.** Recommended 8 hours, minimum 120-minute execution budget, minimum 90-minute protected recovery reserve. No start time is invented anywhere in this repository |

Items 1–5 were the Gate 9 closure and are complete. **The gate conditions for promotion are
therefore satisfied. Execution is not authorised**: approval 7 is an owner grant and has not been
given, so **production deployment remains NONE** until it is.

**What the reader should not conclude from "91/100".** The score counts checkboxes. Gate 3 is
still 2/9, seven browser-E2E bullets have still never run against a deployment, and P0-002's live
integration proof has still never been captured. Those are carried as dated accepted debt with a
**2026-10-15** follow-up, not as solved problems.
