# R-A9F open launch-gate matrix — what is still open, and how each item is disposed

**Produced 2026-09-20 in release programme R-A9F, offline, on release HEAD after the
fast-forward to certified candidate `fe728b205ed5dfb20d88040bc44214ca71d11c43`.**

The release gate's own preamble says production promotion is blocked until every launch gate
is checked **or formally disposed**. The readiness score reaching its target does not discharge
that; the score counts checkboxes and implements no cross-gate blocking, which the gate document
says in as many words. This file is the formal disposition, bullet by bullet, so that "85/100"
and "may we promote?" stay two separate questions.

**Nothing here ticks a bullet.** Every disposition that needs the owner is marked
`OWNER DECISION REQUIRED` and is listed again, consolidated, at the end. No item was ticked,
withdrawn, or re-scoped in order to reach a number.

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
| Gate 9: Final Production Readiness Review | **2/6** | 8 | 2.67 |
| **Total** | | **100** | **85 (raw 85.33), verdict "Ready"** |

Eleven scored bullets are open: Gate 3 b3–b9 and Gate 9 b1, b2, b5, b6.

## The disposition matrix

Legend for **DISPOSITION**: `PASS` · `N/A` (documented architecture reason) ·
`SUPERSEDED` (rationale preserved and a structural guard exists) ·
`OWNER-ACCEPTED DEBT` (the gate framework permits acceptance; needs a named owner) ·
`OWNER DECISION REQUIRED` (nothing may be recorded until the owner decides).

| GATE | BULLET | CURRENT STATUS | REQUIRED FOR INITIAL PRODUCTION PROMOTION? | EVIDENCE | OWNER DECISION? | DISPOSITION |
|---|---|---|---|---|---|---|
| 3 | b1 login/logout/session refresh/CSRF | **CHECKED** | yes | `client/e2e/staging/session-authentication.spec.ts`, R-A8Q Stage B 2026-09-08, 4/4 | no | PASS |
| 3 | b2 Org-Admin permission save/retrieve | **CHECKED (R-A9F)** | yes | `client/e2e/staging/org-admin-permissions.spec.ts`, R-A9E Stage B 2026-09-20 on `fe728b2`, 12/12, 1 worker, 0 retries | no | PASS |
| 3 | b3 document upload/view/download/share/denial | OPEN — spec exists, `EXECUTABLE_PARTIAL`, never executed against a deployment; the **share-delivery** leg additionally needs a staging SMTP sink | **NO for the initial cutover — proposed** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 3; `docs/GATE_3_EXECUTION_PLAN.md` | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** The authorization half of this bullet (upload, view, download, and the 403/404-not-401 denial on the detail route, the download and the listing) is covered by backend tests, and its tenant-boundary half by Gate 4 bullet 8, which R-A9E measured on a deployment. What is unproven is the *browser* path and the *delivery* of a share. Neither is a new risk introduced by this release. |
| 3 | b4 contract upload/ingestion/clause/search/Q&A/appraisal | OPEN — only mocked coverage exists, and mocked evidence can never satisfy this gate; unticked deliberately in R-A8J | **NO for the initial cutover — proposed** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 4 | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** Requires a live staging run with OCR/OpenAI/Qdrant/FalkorDB enabled (P0-002), which is itself accepted debt. |
| 3 | b5 contract timeline link verify/reject | OPEN — never executed against a deployment | **NO for the initial cutover — proposed** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 5 | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** |
| 3 | b6 chronology create/extract/verify/export/attach | OPEN — never executed against a deployment | **NO for the initial cutover — proposed** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 6 | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** |
| 3 | b7 arbitration draft create/generate/edit/version/approve/export | OPEN — **and explicitly NOT superseded.** R-A8S opened a proposal to withdraw it on the premise that the arbitration engine is non-primary; re-derived from source, that premise is false for the *drafting* surface, and the owner withdrew the proposal | **NO for the initial cutover — proposed** | `docs/PRODUCTION_READINESS_RELEASE_GATE.md`, "Bullet 7 is NOT superseded"; guard `test_release_gate_specification.py::test_the_arbitration_drafting_path_is_independent_of_the_langgraph_rollout` | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** Satisfiable today in deterministic mode at zero model cost; it simply has never been run. Recording it as debt keeps the bullet alive; withdrawing it would pay ~1.33 points on a premise the source contradicts, which this programme refuses. |
| 3 | b8 empty and loading states across the route inventory | OPEN — already **AMENDED** once (owner decision 8-C, 2026-09-09): the unfalsifiable error-state half was relocated to the component/mocked layer with its own guard, and the denominator became the generated 92-route inventory. The amendment **earned no point**; the bullet stays open until a staging run measures the states | **NO for the initial cutover — proposed** | `docs/GATE_3_ROUTE_INVENTORY.md`; guards `test_gate3_route_inventory.py::test_the_relocated_error_coverage_does_not_shrink` (holds the located error tests at ≥36) and `::validate_gate3_state_bullet` (fails if a Gate 3 bullet asks for an error state again or drops the denominator) | **YES** | **SUPERSEDED in part (error state, already recorded and guarded) + OWNER-ACCEPTED DEBT — PROPOSED for the remainder.** 49 of the 85 applicable routes still record `MISSING - no fault-injection coverage yet`; that is named debt with a floor, not a lowered bar. |
| 3 | b9 desktop and mobile smoke for primary workflows | OPEN — `login-responsive.spec.ts` and `blog.spec.ts` run unmocked at desktop/tablet/390 px over the public and login surfaces only | **NO for the initial cutover — proposed** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 9 | **YES** | **OWNER-ACCEPTED DEBT — PROPOSED.** |
| 4 | b8 production Org-Admin permissions manually validated | **CHECKED (R-A9F)** | yes | `docs/GATE_4_B8_ORG_ADMIN_VALIDATION_MAPPING.md`, R-A9E Stage B 2026-09-20 on `fe728b2`, all 14 rows PASS with same-run positive controls | no | PASS |
| 9 | b1 critical blockers closed | OPEN — and **structurally blocked**: the gate document states bullet 1 must not be ticked while any row of the Current Critical Blocker Register is Open, and that is enforced rather than described. P0-002 and P0-008 are Open; P0-003 and P0-007 are Partially mitigated | **YES — it is the gate's own promotion condition** | Current Critical Blocker Register; `backend/rbac_backend/tests/test_gate9_scoreability.py` | **YES** | **OWNER DECISION REQUIRED.** It can be ticked only by closing P0-002/P0-008 (which needs the live-integration and sign-off evidence) **or** by the owner converting the remaining register rows to accepted debt in writing. R-A9F does neither. |
| 9 | b2 high-severity risks fixed or explicitly accepted | OPEN | **YES** | third-party images: `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` "R-A9E cutover exception" (dated, expires 2026-10-15, remediation 2026-10-06 → 2026-10-15, post-cutover owner recorded in R-A9F); Python dependencies: P0-007 with one recorded no-fix exception; S3 posture: `docs/S3_STORAGE_POSTURE_DEBT.md` | **YES** | **OWNER DECISION REQUIRED — but the inputs are complete.** Every high-severity item now has a written disposition with an owner, a date and an expiry. What is missing is the single act of the owner accepting the consolidated list (below) and ticking the bullet. |
| 9 | b3 staging smoke passes after deploy | **CHECKED** | yes | `scripts/post_deploy_verify.sh`, R-A8Z Stage B 2026-09-14, exit 0, 24 PASS / 1 WARN / 0 FAIL | no | PASS |
| 9 | b4 staging smoke passes after restore drill | **CHECKED** | yes | `scripts/post_deploy_verify.sh`, R-A8Z Stage B, quiesced production archive restored, 48,032 documents, 0 failed, `CLAMAV_READINESS=OK scope=full` | no | PASS |
| 9 | b5 readiness score target is 85 or higher | OPEN — **satisfied in substance, deliberately left unticked** | **YES** | scorer on this HEAD: 85/100, raw 85.33, verdict "Ready", with b5 **unticked** — which is exactly the non-circularity condition the gate requires and `test_gate9_scoreability.py` enforces | **YES** | **OWNER TICK PENDING (not debt).** The bullet is now legitimately tickable for the first time. R-A9F does not tick it, for one reason: the committed score must equal the figure the sealed R-A9E evidence measured (85 / raw 85.33). Ticking b5 here would move it to raw 86.67 on no new measurement. The owner ticks it together with b6. |
| 9 | b6 release owner signs off | OPEN | **YES — it is the promotion decision itself** | — | **YES** | **OWNER DECISION REQUIRED.** Cannot be self-signed. Nothing in R-A9F substitutes for it. |

## Items that are not scored bullets but were raised as open

| ITEM | CURRENT STATUS | REQUIRED FOR INITIAL PRODUCTION PROMOTION? | EVIDENCE | DISPOSITION |
|---|---|---|---|---|
| **Staging SMTP sink** for the Gate 3 share-delivery leg | OPEN in the cutover checklist's standing-decisions table | **NO** | `docs/GATE_3_EVIDENCE_MATRIX.md` row 3; `docs/GATE_3_EXECUTION_PLAN.md` | **N/A for the initial cutover — it is not an independent gate item.** It is a *sub-dependency of Gate 3 b3*, which is itself proposed as accepted debt. It is a **staging** configuration, so it changes nothing in production and adding it now would buy no production assurance. The rule applied: do not add infrastructure because an old checklist mentions it. If and when the owner requires Gate 3 b3 before cutover, the sink becomes required *at that point*; until then it is carried with b3 and nothing is built in R-A9F. |
| **Falkor rollback variant** (A out-of-band vs B compose-managed) | was OPEN | **YES — the cutover cannot be written without it** | `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` section 3 | **DECIDED (owner, R-A9F): VARIANT A.** Recorded in that document and in the cutover checklist's standing-decisions table. Variant B is withdrawn. |
| **Third-party image fixable CRITICAL/HIGH** (MongoDB 273, FalkorDB 148, Qdrant 84, httpd 51 = 556) | EXCEPTED, dated | no longer an independent blocker until expiry | `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` "R-A9E cutover exception" + the R-A9F post-cutover-ownership table | **OWNER-ACCEPTED, DATED.** Expires **2026-10-15**; remediation window 2026-10-06 → 2026-10-15, httpd first. Not permanent acceptance: on expiry the blocker is live again for any image still on those digests. ClamAV (remediated to 1.4.6) and Redis need no exception. |
| **G32 production state** | NOT CERTIFIED, **NOT RUN** | **NO** | `.claude/context/contract-master/G32-PRODUCTION-MIGRATION-RUNBOOK.md`; `PRODUCTION_CUTOVER_CHECKLIST.md` section 15 | **N/A for this release, with an architecture reason.** Deploying this release *is* G32's Stage 1: the readers already ignore the shared Letter node's properties, so the release is safe without the state migration. G32 stays a **post-release certification** item and requires its own four approvals. It is **not** added to the production cutover. |
| **F-A9B-2** (legal-words `system:admin` gate) | CLOSED IN CODE (R-A9D) | — | `docs/AUTHZ.md`, "System administration is nobody's alias" | PASS — no longer an owner decision. |
| **F-A9D-1** (production-only `billing.plan.manage` on `orgadmin`/`projectadmin`) | DECIDED (R-A9E), frozen (R-A9F) | **YES — it executes during the cutover** | `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md` | **DECIDED AND FROZEN.** Remove from those two roles only; every other production-only permission preserved and reported; no other role touched. |
| **Owner review of the 3 production roles / 4 users** whose fan-out-only authority R-A9B removed | carried since R-A9B | **YES — recorded as a deployment precondition** | R-A9B receipt section 16 | **OWNER DECISION REQUIRED.** Still carried; R-A9F does not discharge it. |

## Consolidated high-risk acceptance list — what the owner is being asked to accept

Presented as one list so the sign-off is a single informed act rather than eleven scattered ones.

| # | Item | What is actually unproven | Residual risk if accepted |
|---|---|---|---|
| A1 | Gate 3 b3–b7, b9 (six browser-E2E bullets) | The browser path through document sharing, contract ingestion/search/Q&A/appraisal, contract timeline, chronology, arbitration drafting, and mobile smoke has never been exercised against a deployed stack | A regression in one of those UI flows reaches production unmeasured. The **backend** authorization for these surfaces is covered by the suite and by Gate 4's 10/10; the gap is the browser layer and live third-party integrations |
| A2 | Gate 3 b8 (empty/loading states) | 49 of 85 applicable routes have no fault-injection coverage; empty/loading states are unmeasured on a deployment | Degraded-state UI defects reach production. Floor guard prevents the located 36 from shrinking |
| A3 | P0-002 live AI/vector/graph integration | The live staging proof with OCR/ClamAV/OpenAI/Qdrant/FalkorDB/Redis all enabled has never been captured | Integration-level failures surface first in production |
| A4 | P0-007 Python dependency audit | ~60 advisories need a coordinated FastAPI/Starlette + LangChain/LangGraph/Pydantic-AI upgrade; `ecdsa` Minerva is upstream won't-fix and unused on the HS256 path | Known-advisory exposure in dependencies, none with a demonstrated path in this application |
| A5 | Third-party images (556 fixable C/H) | No upgrade validated for MongoDB, FalkorDB, Qdrant, httpd | Bounded to **2026-10-15**. httpd is the only image on the public path and is remediated first |
| A6 | Shared production S3 bucket for documents and backups | Already accepted as post-release debt | A single bucket compromise reaches both documents and backups |
| A7 | RPO 24 h / RTO 8 h | **RECORDED, not DEMONSTRATED** (owner decision 2026-09-08) | The stated recovery objectives have not been proven end to end on production |
| A8 | Arbitration acceptance in production | Zero production cases/runs; crash/restart drills and legal HITL review outstanding | Confined: `ARBITRATION_ENGINE_ROLLOUT_MODE=off`, `PRODUCTION_ACCEPTED=false` |
| A9 | The 3 production roles / 4 users from R-A9B | Their fan-out-only authority was removed in code; the owner has not reviewed who they are | Someone loses authority at cutover that they were in practice relying on |

## Exact owner approvals still required before R-A9G may be authorised

1. **Accept, in writing, items A1–A9 above** (or direct that any of them be closed first).
2. **Tick Gate 9 b2** once A1–A9 are accepted — "high-severity risks fixed or explicitly accepted".
3. **Decide Gate 9 b1**: either close P0-002 and P0-008, or convert the remaining Critical Blocker Register rows to accepted debt in writing, then tick.
4. **Tick Gate 9 b5.** It is legitimately tickable now (the score reaches 85 with b5 itself unticked). R-A9F deliberately left it for the owner.
5. **Sign Gate 9 b6** — the release sign-off, which is the promotion decision itself.
6. **Review the 3 production roles / 4 users** carried from R-A9B and confirm the authority removal is intended.
7. **Grant the R-A9G maintenance window** (recommended 8 hours; see the cutover runbook).

Items 1–5 are the Gate 9 closure. Until all seven are recorded, **production promotion is not
authorised, whatever the score reads.**
