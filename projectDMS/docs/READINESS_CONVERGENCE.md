# Readiness convergence — the whole gap, and the shortest defensible route to 85

Re-derived mechanically on 2026-09-09 (release programme R-A8S) from
`scripts/production_readiness_score.py` against
[PRODUCTION_READINESS_RELEASE_GATE.md](PRODUCTION_READINESS_RELEASE_GATE.md) at
release HEAD. Nothing here is copied from a receipt.

**Score: 74/100. Target: 85. Verdict: Ready with Conditions. Gate 9: OPEN.**

The score moved 68 → 74 in R-A8S, from three bullets and no staging window:

* **Gate 5 bullet 3** (+2.000) — upload size, bulk and concurrency limits now
  have permanent refusal tests against the configured limits. Running them found
  F-A8S-2: an oversize document upload answered **500**, and the sibling contract
  surface answered 422, for the same condition. Both now answer **413**.
* **Gate 5 bullet 5** (+2.000) — an AST guard over every logging call in the
  ingestion and extraction trees, plus a runtime marker control. It found one
  real channel (Marker's subprocess stderr) and closed it.
* **Gate 6 bullet 2** (+1.667) — the R-A8Q Stage B fresh-install measurements
  mapped requirement-by-requirement against the bullet's wording and attached as
  `docs/GATE_6_FRESH_INSTALL_EVIDENCE.md`. No new execution.

Rounding accounts for the remaining fraction: raw 68.50 → 74.17.

**Two Gate 3 amendments landed and neither moved the score, deliberately.**
Bullet 8 was narrowed and a bullet replaced a bullet, so the denominator stayed
at 9. Bullet 7's proposed withdrawal was **refused on the evidence** — see §2.

---

## 1. Arithmetic

| Gate | Weight | Checked | Points | Per bullet | Remaining |
|---|---:|---:|---:|---:|---:|
| 1 CI and local test baseline | 15 | 5/6 | 12.50 | 2.500 | 2.500 |
| 2 Live integration | 10 | 6/6 | 10.00 | — | 0 |
| 3 Browser E2E | 12 | 1/9 | 1.33 | 1.333 | 10.667 |
| 4 Security and RBAC | 15 | 8/10 | 12.00 | 1.500 | 3.000 |
| 5 Upload and content safety | 10 | **5/5** | **10.00** | — | **0** |
| 6 Database, migrations, seeds | 10 | **5/6** | **8.33** | 1.667 | **1.667** |
| 7 Deployment | 10 | 7/7 | 10.00 | — | 0 |
| 8 Backup / restore / rollback | 10 | 8/8 | 10.00 | — | 0 |
| 9 Final review | 8 | 0/6 | 0.00 | 1.333 | 8.000 |
| **Total** | **100** | | **74.17 raw → 74** | | **25.83** |

**Weighted points needed for 85.** The scorer rounds with `round()`, which is
banker's rounding, so **84.5 displays as 84**. The requirement is therefore
**raw > 84.50**, i.e. **more than 10.333 further points** — not 11, and not 10.5.

Every remaining bullet outside Gates 3 and 9 is worth **7.167** in total
(G1 b6 2.500, G4 b8 1.500, G4 b10 1.500, G6 b3 1.667). That is less than 10.333,
so **Gate 3 or Gate 9 bullets are unavoidable on every route to 85** — and Gate 9
bullet 5 ("readiness score ≥ 85") cannot be one of them, because counting it
towards reaching 85 is circular.

**Every remaining offline bullet is now closed.** Nothing left on the board can
be earned without either a push or a deployment.

---

## 2. Gap matrix — every unchecked bullet, Gates 1–9

`TYPE` is one of: MISSING EXECUTABLE EVIDENCE · OWNER DECISION · RELEASE DEFECT ·
PRODUCTION-CUTOVER EVIDENCE · MANUAL REVIEW · OBSOLETE/SUPERSEDED ·
DOCUMENTATION ONLY.

### Gate 1 — CI and local test baseline (2.500 remaining)

| Bullet | Weight | Status | Intended property | Existing evidence | Why not scored | Type | Minimum next action |
|---|---:|---|---|---|---|---|---|
| 6 — GitHub Actions passes for backend, frontend, dependency scans and Docker image scans | 2.500 | unchecked | The repository's own CI, on the release commit, is green across all four jobs | `.github/workflows/ci.yml` defines `secret-scan`, `backend-checks`, `frontend-checks`, `dependency-scan`, `docker-build-and-scan`. Locally: backend suite green, `npm run lint` exit 0, `npm run build` passes, `pip-audit` green with one recorded no-fix exception, Trivy 0/0 in R-A8Q | **The release branch has never been pushed.** No Actions run exists for any commit on it, so there is no result to record | MISSING EXECUTABLE EVIDENCE | Push `release/contraclaim-rc1` to both remotes and record the run. That is cutover-checklist §1 and §3, and it is the first thing that phase does |

### Gate 4 — Security and RBAC (3.000 remaining)

| Bullet | Weight | Status | Intended property | Existing evidence | Why not scored | Type | Minimum next action |
|---|---:|---|---|---|---|---|---|
| 8 — Production Org-Admin permissions manually validated in staging/prod | 1.500 | unchecked | An org-admin can actually save and retrieve role permissions, including the Client DMS group, against a deployment | `test_role_permission_catalog_drift.py` (service round trip), `test_org_admin_permissions_api.py` (HTTP boundary). Both hermetic | No deployment has been exercised. P0-006 records the service and HTTP layers as covered and the deployment layer as not | MANUAL REVIEW → now automatable | **This bullet and Gate 3 bullet 2 are the same measurement.** `client/e2e/staging/org-admin-permissions.spec.ts` (R-A8R) covers it; one staging run closes both — **2.833 points from one execution** |
| 10 — Secret scan passes | 1.500 | unchecked | No credential is committed anywhere in the history | `.gitleaks.toml` and the `secret-scan` job (gitleaks, `fetch-depth: 0`). `scripts/evidence_secret_scan.py` scans *release evidence* by value and was CLEAN in R-A8Q with 37 values hunted — a different scan of a different corpus | Same cause as Gate 1 bullet 6: no Actions run on this branch | MISSING EXECUTABLE EVIDENCE | Same push. `gitleaks` is not installed on the dev host, so this is genuinely CI-gated |

### Gate 5 — Upload and content safety (0 remaining) — CLOSED in R-A8S

| Bullet | Weight | Status | What closed it |
|---|---:|---|---|
| 3 — Upload MIME, extension, size and concurrency limits are validated | 2.000 | **checked** | `test_upload_limit_enforcement.py`, 21 tests. Every boundary is read from `settings` at call time, so raising a limit cannot leave a green test measuring nothing. A `Content-Length` that lies buys nothing, because the cap counts bytes actually read; the refusal trips mid-stream and cleans up its spool file. Bulk count and total size at and over their configured limits; the (N+1)th concurrent upload refused 429 per user and per organisation |
| 5 — Sensitive extracted text is not logged | 2.000 | **checked** | `test_extracted_content_not_logged.py`, 26 tests. An AST guard over every logging call in `ingestion/`, `services/extraction/`, `services/contract_clause/`, `retrieval/` and four named modules, covering positional args, f-strings, `%`, `.format`, concatenation, slices, method calls and `extra={}` — with eight tests pinning metadata logging as still allowed. Plus a runtime control that pushes a distinctive marker through the real chunker with the root logger captured at DEBUG, and a negative control proving the capture handler sees all three leak channels |

**F-A8S-2, found by running rather than reading.** The size limit raised a bare
`ValueError`, and the two upload surfaces disagreed about what that meant.
`routers/contracts.py` runs under `handle_exceptions` and answered **422**;
`DocumentController` has its own handler whose `except Exception` answered
**500 "Document creation service temporarily unavailable"**. A refusal a client
cannot tell apart from an outage makes the user retry the same oversize file and
makes monitoring count a policy decision as a 5xx. `UploadTooLargeError` now
carries 413 from the seam that makes the decision — the shape
`UploadConcurrencyLimiter` already uses for 429 — so all three call sites answer
413 without any of them being edited.

**F-A8S-4, found by reviewing the tick rather than the code.** The bullet says
"upload ... size ... limits are validated" and says nothing about only the
spooled paths counting. Six more upload surfaces read the whole body into memory:
`bank_guarantees.py`, `key_dates.py` and `deep_planning.py` with **no
application-level limit at all** (bounded only by the gateway's
`client_max_body_size 200m`), and `profiles.py`, `folder_structure.py` and the
contract chunk endpoint with one applied *after* the read — which bounds what is
stored, not what the process holds. All six now use `read_upload_within_limit`,
and an AST guard fails any router that reads an `UploadFile` unbounded.

### Gate 6 — Database, migrations and seeds (1.667 remaining)

| Bullet | Weight | Status | Intended property | Existing evidence | Why not scored | Type | Minimum next action |
|---|---:|---|---|---|---|---|---|
| 2 — Fresh install path is tested against real MongoDB | 1.667 | **checked in R-A8S** | A brand-new database reaches a working schema through the migration runner | R-A8Q §16 against `rsstg`, mapped requirement-by-requirement in `docs/GATE_6_FRESH_INSTALL_EVIDENCE.md`: six literal elements, six measurements, no element satisfied by inference from another | — | CLOSED | The tick expires when the migration catalogue moves. `test_gate6_fresh_install_evidence.py` pins the count at the 19 the run applied, so a twentieth migration — never applied to an empty database — forces the tick to be re-earned rather than inherited |
| 3 — Existing database upgrade path is tested against a staging copy | 1.667 | unchecked | A database **carrying production data** survives the migrations | R-A8Q's Mongo restore drill recovered 145/145 collections, 218/218 documents, 490/490 indexes — but that was a staging backup restored into staging, and the migration run in §16 was against a **fresh** database (19 of 19 applied) | The two halves were never composed: nothing has restored a production-shaped copy and *then* migrated it | MISSING EXECUTABLE EVIDENCE | **FOCUSED STAGING.** Restore a production Mongo archive into the staging replica set, run `--list` (expect a partial ledger), `--fail-on-warning`, `--apply`, then re-apply for idempotence, and assert the permission catalogue and the unique index afterwards |

### Gate 3 — Browser E2E (10.667 remaining)

Enumerated bullet by bullet in
[GATE_3_EXECUTION_PLAN.md](GATE_3_EXECUTION_PLAN.md) with SETUP / ACTION /
EXPECTED / AUTHORITY / CLEANUP for each. Summary of type:

| Bullets | Type | Note |
|---|---|---|
| 2 | MISSING EXECUTABLE EVIDENCE | spec and fixtures now exist; needs a run |
| 3 | MISSING EXECUTABLE EVIDENCE + OWNER DECISION | spec exists for all legs but delivery of the share; that leg needs a staging SMTP sink |
| 4, 5, 6 | MISSING EXECUTABLE EVIDENCE | each needs one fact this phase could not establish offline without inventing it |
| 7 | MISSING EXECUTABLE EVIDENCE | **the owner decision was made and refused.** The withdrawal rested on "the arbitration engine is deliberately not primary", which is true of the LangGraph *workflow* engine and false of the *drafting* surface this bullet names: `ArbitrationDraftingService.generate` reads `ARBITRATION_DRAFT_MODE` (default `deterministic`) and never consults `ArbitrationEnginePolicy`, and that policy returns `arbitration_v2` for every request under the shipped configuration. `client/e2e/staging/arbitration-drafting.spec.ts` was written in R-A8S instead; it needs a run and an approver account, and costs no model tokens |
| 8 | **AMENDED** (owner decision 8-C) | narrowed to empty and loading over `docs/GATE_3_ROUTE_INVENTORY.md` (92 routes, 85 loading, 59 empty, generated from the router); the error state moved to the fault-injection layer. **A bullet replaced a bullet — the denominator stayed at 9 and no point was earned** |
| 9 | MISSING EXECUTABLE EVIDENCE | closes after 2, 3 and 5, as a viewport matrix |

### Gate 9 — Final review (8.000 remaining)

All six are downstream. See §5.

### Superseded / obsolete items found in this phase

| Item | Disposition |
|---|---|
| Gate 2's FalkorDB vector criterion | **OBSOLETE/SUPERSEDED**, withdrawn 2026-09-04, and as of R-A8R enforced rather than only described (`GATE2_WITHDRAWN_LIVE_TESTS`) |
| The R-A4 lint correction paragraph | **DOCUMENTATION ONLY**, stale — replaced with the current measurement |
| "Current production launch-readiness score: **32/100**" (Gate 9 block), the same figure in blocker P0-008, and the rendered gate table's Gate 1 row and total | **DOCUMENTATION ONLY**, stale. The drift guard's regex could not see any of them — a colon instead of "is", and spaces around the slash. Guard extended, with three mutation proofs |
| Gate 3 bullet 8's "every production route" | **AMENDED** in R-A8S under owner decision 8-C. Unsatisfiable in two independent ways: an error state needs the API to fail on demand under a gate that accepts only unmocked staging runs, over a denominator that named no route set |
| The first cut of the route parser (**F-A8S-5**) | **FIXED in R-A8S before the inventory was trusted.** The two-axis review fed it synthetic routers and it dropped six route forms silently: an `index` route, a single-quoted `path`, a template-literal `path`, a `>` inside a quoted attribute value (which also desynced the scan, losing every route after it), and `element=` declared before `path=`. None appears in `routes.tsx` today, which is luck rather than a property — and the cross-check against the client's own `routeInventory.test.ts` regex could not have caught it, because that regex shares the same blind spots. The parser now tracks quote state, blanks braced expressions instead of truncating at `element=`, reads all three path spellings and resolves index routes; and a leaf `<Route>` it cannot place now **raises** rather than skips. The inventory is unchanged at 92/85/59/85, so this was hardening and not a recount |
| The Gate 3 evidence matrix's row 7 (`MISSING`, "cannot be earned before that decision changes") | **WRONG, corrected in R-A8S.** It cited `ARBITRATION_ENGINE_*`, which governs the LangGraph workflow engine and not the drafting surface the bullet names. The bullet is satisfiable in staging today, in deterministic mode, at zero model cost |

---

## 3. MINIMUM PATH TO 85

Re-derived on 2026-09-09 at the R-A8S final state. **Base raw 74.167. The
requirement is raw > 84.50, so more than 10.333 further points.**

**Every offline bullet is gone.** R-A8S closed the last three (G5 b3, G5 b5,
G6 b2). Nothing remaining can be earned without a push or a deployment, which is
the useful thing this re-derivation says: the next phase is not a coding phase.

| # | Item | Points | OFFLINE / PUSH / STAGING | Dependency | Required for 85? |
|---|---|---:|---|---|---|
| 1 | **G1 b6** — GitHub Actions green on the release commit | 2.500 | **PUSH** | the branch reaching a remote | **YES** — it is the largest single bullet left and needs no window |
| 2 | **G4 b10** — secret scan passes | 1.500 | **PUSH** | the same push; `gitleaks` is not installed on the dev host | **YES** — same run, no extra cost |
| 3 | **G3 b2** — org-admin permission save/retrieve | 1.333 | **STAGING** | `org-admin-permissions.spec.ts` (exists), a staging stack, `E2E_ORG_ADMIN_*` | **YES** — it carries item 4 |
| 4 | **G4 b8** — production Org-Admin permissions validated | 1.500 | **STAGING** | *the same execution as item 3* | **YES** — 2.833 points from one spec run is the best ratio on the board |
| 5 | **G6 b3** — production-copy restore, then migrate | 1.667 | **STAGING** | a production Mongo archive | **YES** — the largest staging bullet |
| 6 | **G9 b3** — staging smoke after deploy | 1.333 | **STAGING** | deploying the release images to staging | **YES** (or one substitute from item 8) |
| 7 | **G9 b4** — staging smoke after restore drill | 1.333 | **STAGING** | a restore in the same window, smoke after it | **YES** (or one substitute from item 8) |
| 8 | **G3 b3 / G3 b7 / G3 b9** — document lifecycle, arbitration drafting, viewport matrix | 1.333 each | **STAGING** | b3 needs the SMTP-sink decision for its share leg; b7 needs an approver account; b9 needs b2 and b3 first | substitutable for items 6–7, one for one |
| 9 | **G9 b1, b2, b6** — blockers closed, risks accepted, owner sign-off | 1.333 each | OWNER | b1 is downstream of everything; b2 is one consolidated acceptance document; b6 is a named human | not on the shortest path, but b2 is cheap and offline |
| — | **G9 b5** — readiness score ≥ 85 | 1.333 | — | the score itself | **excluded.** Counting it towards reaching 85 is circular |

**The shortest route, priced.**

| Step | Points | Running raw | Displays |
|---|---:|---:|---|
| start | — | 74.167 | 74 |
| push + CI green (G1 b6, G4 b10) | +4.000 | 78.167 | 78 |
| one spec run (G3 b2 + G4 b8) | +2.833 | 81.000 | 81 |
| production-copy restore then migrate (G6 b3) | +1.667 | 82.667 | 83 |
| staging deploy smoke (G9 b3) | +1.333 | 84.000 | **84** |
| post-restore smoke (G9 b4) | +1.333 | 85.333 | **85** |

**Note the 84.** Stopping one bullet earlier lands on exactly 84.000, which is
below the 84.50 threshold. There is no route that reaches 85 with fewer than
**five staging-earned bullets plus the push**, because the four largest
non-Gate-3 items only total 7.167.

**Exact minimum number of staging bullets: five** (G3 b2, G4 b8, G6 b3, and any
two of G3 b3 / G3 b7 / G3 b9 / G9 b3 / G9 b4). Substituting G9 b2 — an owner
acceptance document, not a staging run — for one of the last two brings it to
four, and is the only offline substitution left on the board.

**What changed from the R-A8R projection.** That projection assumed Gate 3
bullets 7 and 8 might both be withdrawn, shrinking Gate 3's denominator to 7 and
raising the already-earned bullet 1 by 0.381. Neither withdrawal happened: b7 was
refused on the evidence, and b8 replaced a bullet rather than removing one. **The
denominator is still 9, and no point in this phase came from a denominator
change.** Every one of the six points R-A8S added came from a measurement.

**Shortest defensible route, stated plainly:**

1. one push and one green CI run (G1 b6, G4 b10);
2. **one focused staging window** covering G3 b2 (+G4 b8), G6 b3, and a deploy
   smoke and a post-restore smoke (G9 b3, G9 b4).

That is **85**, and it still needs exactly one staging execution — but the window
is now larger than R-A8R projected, because it has to carry Gate 9's two smokes
rather than lean on a denominator change.

## 4. PATH TO 100

Everything above, plus:

| Item | Points | Blocked on |
|---|---:|---|
| G3 b4 — real contract ingestion end to end | 1.333–1.714 | a model-token budget; structural assertions, not textual |
| G3 b5 — timeline link verify/reject | 1.333–1.714 | the seeding path for an *unverified* proposed link |
| G3 b6 — chronology lifecycle | 1.333–1.714 | includes attach-to-arbitration, so it is downstream of bullet 7 |
| G3 b7 — arbitration drafting | 1.333 | **not the acceptance receipt chain** — that gates the LangGraph *workflow* engine, which this bullet does not touch. It needs a staging run of `arbitration-drafting.spec.ts` and an approver account, and no model budget |
| G3 b8 — empty and loading states | 1.333 | a spec that walks the 85 loading and 59 empty applicable routes in `GATE_3_ROUTE_INVENTORY.md` against a deployment. The denominator now exists; the walk does not |
| G3 b9 — desktop and mobile smoke | 1.333–1.714 | 2, 3 and 5 first; then a viewport matrix |
| G9 b1–b6 | 8.000 | §5 |

**The R-A8R reading of bullet 7 was wrong and is corrected here.** It said 100
requires shipping the arbitration engine as a primary path. It does not: the
drafting surface bullet 7 names is already primary, and the LangGraph rollout is
a different decision. What still makes 100 non-release-timescale is G3 b4 (a real
ingestion token budget), G3 b6 (chronology seeding) and Gate 9 b6 (a named human
signing off). **100 is not a release-timescale target.** 85 is.

---

## 5. Gate 9 entry contract

Read from the gate document itself, not from a summary. Gate 9 has six scored
bullets, and the Launch Gates preamble adds a condition that is easy to miss:
**"Production promotion is blocked until all gates are checked."** Reaching 85 is
necessary and is not sufficient.

| # | Requirement | Source | Current status | Evidence | Blocker |
|---|---|---|---|---|---|
| E0 | **Every gate checked** | Launch Gates preamble | **NOT MET** — 5/6, 6/6, 1/9, 8/10, **5/5**, **5/6**, 7/7, 8/8, 0/6 | the scorer | Gates 1, 3, 4, 6 — **Gate 5 is now full** |
| 1 | Critical blockers closed | Gate 9 b1 | **NOT MET** | Blocker register: P0-002, P0-003, P0-007 (partial), P0-008 open | Gates 2/3 evidence, dependency posture, staging deploy evidence |
| 2 | High severity risks fixed or explicitly accepted | Gate 9 b2 | **PARTIAL** | Accepted in writing: no-fix CVEs (`docs/NOFIX_ADVISORY_ACCEPTANCE.md`, `docs/IMAGE_NOFIX_CVE_ACCEPTANCE.md`), shared S3 bucket, RPO/RTO as RECORDED-not-DEMONSTRATED, Gate 2 vector withdrawal | Needs one consolidated acceptance list the owner signs, rather than five documents |
| 3 | Staging smoke test passes after deploy | Gate 9 b3 | **NOT MET** | R-A8Q brought staging up and certified Gates 2/7/8 against it, but that was not a *deploy of this release to staging* followed by a smoke | A staging deploy of the release images, then `post_deploy_verify.sh` |
| 4 | Staging smoke test passes after restore drill | Gate 9 b4 | **NOT MET** | R-A8Q ran `mongo_restore.sh` (145/145 collections, 218/218 docs, 490/490 indexes, MATCH=YES) but did not smoke the application afterwards | Restore, then smoke, in that order, in one run |
| 5 | Readiness score ≥ 85 | Gate 9 b5 | **NOT MET** — 74 | the scorer | §3 |
| 6 | Release owner signs off | Gate 9 b6 | **NOT MET** | No named approver exists in this repository | A named owner, a date, and a signed statement |

**Additional source-defined conditions that are not Gate 9 bullets but gate the
same promotion:**

* the branch must reach a deployable branch (it is local only);
* the FalkorDB `/data` cutover must be sequenced with the deploy, or the nightly
  backup fails every night — `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md`;
* **G32 production state: NOT CERTIFIED**, under its own separate authorisation.

**Gate 9 is not run in this phase and must not be.** Four of its six bullets have
no evidence at all, and one of those (b6) cannot be produced by any process in
this repository.

---

## 6. Production S3 failure domain — disposition

**Question:** is a separate production backup bucket MANDATORY BEFORE CUTOVER, or
OWNER-ACCEPTED POST-RELEASE DEBT?

**Answer, read from the current release policy: OWNER-ACCEPTED POST-RELEASE
DEBT. This is already decided; no new owner decision is required for the
release.**

The chain, in the documents themselves:

1. `docs/S3_STORAGE_POSTURE_DEBT.md` §1 records the measurement — production runs
   one bucket, `contraclaim`, serving both `AWS_BUCKET_NAME` and
   `BACKUP_S3_PREFIX=contraclaim/backups` — and states it is "sequenced after the
   release, not inside it".
2. `docs/RPO_RTO_OWNER_DECISION.md` §5 lists bucket separation as **precondition
   1** before *any* RPO row may be recorded as met.
3. The same document's header records that on 2026-09-08 the owner **approved
   Candidate A anyway**, explicitly accepting staging-scale measurements as the
   evidence basis for this release and **carrying items 1 and 3 as
   production-cutover debt**.
4. Gate 8 bullet 7's evidence line says the same thing at the point of the tick:
   "**OBJECTIVE RECORDED, not OBJECTIVE FULLY DEMONSTRATED** … S3 failure-domain
   separation, production-scale restore timing and detection time remain open."

So the precondition was overridden by an explicit, dated, recorded owner
acceptance. That is a legitimate way to close it, and it is not a
contradiction — but it is only visible if you read the header before §5.

**What R-A8R adds is not a new decision. It is a confirmation point.** The
cutover is the moment of highest data risk in this programme — a persistence
move plus 19 migrations — and it is the one moment where "the off-site copy is a
copy on different hardware, not a copy in a different failure domain" stops being
an abstract statement.

**For the owner, at cutover-checklist §8, one question:**

> The standing acceptance of the shared bucket was made on 2026-09-08 against
> normal operations. Does it still hold for the cutover window itself, where a
> persistence move and 19 migrations run back to back?

| | Option | Risk | Cutover impact | Rollback impact | RPO implication |
|---|---|---|---|---|---|
| **S3-A** | **Confirm the standing acceptance.** Proceed; separate the bucket after the release. | One deletion policy, one lifecycle rule, one credential compromise reaches both documents and backups. Unchanged from today | none | none | The stated 24 h RPO continues to assume an off-site copy that is not an independent failure domain. Say so wherever it is quoted |
| **S3-B** | **Separate the bucket before the cutover.** | Lowest data risk at the riskiest moment | Adds a bucket creation, an IAM change, a prefix copy, a byte-for-byte verification and a nightly-run confirmation — all before the window. Days, not hours | An extra thing that can be wrong during a rollback | Makes the 24 h RPO honest |
| **S3-C** | **Take one extra full off-site copy into a second bucket for the window only**, then revert. | Nearly all of S3-B's benefit for the window, at a fraction of the change | One extra bucket and one extra sync, both outside the outage | The window's backups live outside the blast radius | Unchanged as a standing objective; the window itself is covered |

**Recommendation: S3-C**, then S3-B after the release. It puts the protection
exactly where the risk is concentrated without making a production storage
architecture change a cutover dependency. **No AWS resource was changed in this
phase, and this note decides nothing.**

---

## 7. Next staging requirement

**FOCUSED STAGING REQUIRED — one window. Not a full Gate-2/7/8 rehearsal.**
Re-derived at the R-A8S state: five staging-earned bullets are needed for 85, so
the window below is the minimum and the two Gate 9 smokes are no longer optional
extras.

| Bullet | What the run must do |
|---|---|
| **Gate 3 bullet 2** | `client/e2e/staging/org-admin-permissions.spec.ts` with `E2E_BASE_URL` and `CONTRACLAIM_STAGING_E2E=1` |
| **Gate 4 bullet 8** | the same run — it is the same measurement, so record it against both |
| **Gate 3 bullet 3** | `client/e2e/staging/document-lifecycle.spec.ts`; ticks only if the share-leg decision is made, otherwise it stays `EXECUTABLE_PARTIAL` |
| **Gate 3 bullet 7** | `client/e2e/staging/arbitration-drafting.spec.ts` (new in R-A8S); needs `E2E_APPROVER_EMAIL`/`_PASSWORD` for the author-approver separation half, and no model budget |
| **Gate 6 bullet 3** | restore a production Mongo archive into `rsstg`, then `--list` / `--fail-on-warning` / `--apply` / re-apply |
| **Gate 9 bullets 3 and 4** | deploy the release images to staging, smoke; then restore, smoke again. Two smokes, in that order. **These are now on the critical path to 85, not spare capacity** |

**Gates 2, 7 and 8 do not need re-running.** All three are full, their evidence is
from R-A8M and R-A8Q against the current images, and nothing in R-A8R changed a
production source file they depend on — the changes were `tests/`, `docs/`,
`client/e2e/`, `.env*.example`, and `scripts/post_deploy_verify.sh` plus its new
library. The last of those is Gate 7 bullet 7's evidence, so **`post_deploy_verify.sh`
must be re-run once** in whatever window comes next; it does not require a
dedicated one, and the edge change makes the run stricter rather than weaker.

**Both Gate 3 decisions are now made** (b7 refused, b8 amended under 8-C), and
the enumerated-route work 8-C implied is done. The remaining precondition is the
**push**, because G1 b6 and G4 b10 are worth 4.000 between them, need no window,
and are the cheapest points left — do them first, so the staging window is sized
against a known 78 rather than a projected one.
