# Readiness convergence — the whole gap, and the shortest defensible route to 85

Re-derived mechanically on 2026-09-08 (release programme R-A8R) from
`scripts/production_readiness_score.py` against
[PRODUCTION_READINESS_RELEASE_GATE.md](PRODUCTION_READINESS_RELEASE_GATE.md) at
release HEAD. Nothing here is copied from a receipt.

**Score: 68/100. Target: 85. Verdict: Not Ready. Gate 9: OPEN.**

The score moved 66 → 68 in this phase for one reason, and it is worth being
precise about it: `npm run lint` was re-executed and **exits 0** (eslint v9.39.4,
no output). The gate document carried a correction paragraph asserting that it
exits 1, written in R-A4 and made false by release commit `6448663`. A correction
that outlives the defect it describes is the same failure as the original wrong
tick, so the paragraph was replaced with the measurement and the box was ticked.
**No other bullet moved, and nothing was ticked from a document.**

---

## 1. Arithmetic

| Gate | Weight | Checked | Points | Per bullet | Remaining |
|---|---:|---:|---:|---:|---:|
| 1 CI and local test baseline | 15 | 5/6 | 12.50 | 2.500 | 2.500 |
| 2 Live integration | 10 | 6/6 | 10.00 | — | 0 |
| 3 Browser E2E | 12 | 1/9 | 1.33 | 1.333 | 10.667 |
| 4 Security and RBAC | 15 | 8/10 | 12.00 | 1.500 | 3.000 |
| 5 Upload and content safety | 10 | 3/5 | 6.00 | 2.000 | 4.000 |
| 6 Database, migrations, seeds | 10 | 4/6 | 6.67 | 1.667 | 3.333 |
| 7 Deployment | 10 | 7/7 | 10.00 | — | 0 |
| 8 Backup / restore / rollback | 10 | 8/8 | 10.00 | — | 0 |
| 9 Final review | 8 | 0/6 | 0.00 | 1.333 | 8.000 |
| **Total** | **100** | | **68.50 raw → 68** | | **31.50** |

**Weighted points needed for 85.** The scorer rounds with `round()`, which is
banker's rounding, so **84.5 displays as 84**. The requirement is therefore
**raw > 84.50**, i.e. **more than 16.00 further points** — not 17, and not 16.5.

Every non-Gate-3 bullet that remains is worth **12.833** in total. That is less
than 16.00, so **Gate 3 bullets are unavoidable on every route to 85.**

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

### Gate 5 — Upload and content safety (4.000 remaining)

| Bullet | Weight | Status | Intended property | Existing evidence | Why not scored | Type | Minimum next action |
|---|---:|---|---|---|---|---|---|
| 3 — Upload MIME, extension, size and concurrency limits are validated | 2.000 | unchecked | All four limit families are enforced and proven | **MIME and extension: covered** — `test_upload_policy.py`, 10 tests over the document / enclosure / contract allowlists, extension shape, the archive fail-closed config and the client fallback subset. **Size: enforced, untested** — `GENERAL_UPLOAD_MAX_FILE_SIZE_MB` and `CONTRACT_UPLOAD_MAX_FILE_SIZE_MB` are applied at `routers/documents.py:704`, `:1851`, `routers/contracts.py:91`, `routers/folder_structure.py:324`, `services/contract_service.py:189`. **Concurrency: enforced, untested** — `BULK_UPLOAD_MAX_FILES` at `routers/documents.py:3138` | Two of the four families have no test at all. `test_document_concurrency.py` is about optimistic document revisions, not upload concurrency, and does not cover this | MISSING EXECUTABLE EVIDENCE | **OFFLINE NOW.** Add router tests: an over-limit upload refused at each of the five enforcement sites, and a bulk upload of `BULK_UPLOAD_MAX_FILES + 1` refused. The limits are already read from settings, so parametrise on the setting rather than on a literal |
| 5 — Sensitive extracted text is not logged | 2.000 | unchecked | Document text extracted from uploads never reaches a log sink | `observability/service.py:64` redacts a query to `[redacted len=N]`. `pre-commit` forbids `print()` in `ingestion\|retrieval\|agents\|observability` | Redaction exists for *queries*. Nothing establishes the property for extracted document text, and no test asserts it anywhere | MISSING EXECUTABLE EVIDENCE | **OFFLINE NOW.** A static AST gate in the shape of `test_ci_static_gates.py`: no logging call in the ingestion / extraction path may take an argument derived from extracted text, plus a runtime control that captures log records during an extraction and asserts the payload is absent |

### Gate 6 — Database, migrations and seeds (3.333 remaining)

| Bullet | Weight | Status | Intended property | Existing evidence | Why not scored | Type | Minimum next action |
|---|---:|---|---|---|---|---|---|
| 2 — Fresh install path is tested against real MongoDB | 1.667 | unchecked | A brand-new database reaches a working schema through the migration runner, not through startup index creation | **Strong, and already captured.** R-A8Q §16, against `rsstg` (a real 3-node replica set, database `contraclaim_staging`): `--list` 19 migrations, dry run exit 0 with `warnings: []`, apply exit 0 with **19 applied**, second apply **0 applied / 19 skipped**, `--list` after 19/19. Permission catalogue seeded 198/198 distinct, `uq_permissions_name` unique and present, re-seeding idempotent | Nobody has ticked it. Gate 6 carries no `evidence:` convention, so the tick is a judgement about whether that run satisfies the wording — and R-A8Q did not make it | MISSING EXECUTABLE EVIDENCE (arguably already met) | **Cheapest point on the board.** A reviewer confirms the R-A8Q evidence answers the bullet's wording and ticks it with an evidence line. **No new execution.** If the reviewer says the wording needs a container-level fresh install rather than a fresh database, it becomes FOCUSED STAGING |
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
| 7 | **OWNER DECISION** | the arbitration engine is deliberately not primary |
| 8 | **OWNER DECISION** | unsatisfiable as written — an error state needs the API to fail on demand, which the gate's own staging rule forbids |
| 9 | MISSING EXECUTABLE EVIDENCE | closes after 2, 3 and 5, as a viewport matrix |

### Gate 9 — Final review (8.000 remaining)

All six are downstream. See §5.

### Superseded / obsolete items found in this phase

| Item | Disposition |
|---|---|
| Gate 2's FalkorDB vector criterion | **OBSOLETE/SUPERSEDED**, withdrawn 2026-09-04, and as of R-A8R enforced rather than only described (`GATE2_WITHDRAWN_LIVE_TESTS`) |
| The R-A4 lint correction paragraph | **DOCUMENTATION ONLY**, stale — replaced with the current measurement |
| "Current production launch-readiness score: **32/100**" (Gate 9 block), the same figure in blocker P0-008, and the rendered gate table's Gate 1 row and total | **DOCUMENTATION ONLY**, stale. The drift guard's regex could not see any of them — a colon instead of "is", and spaces around the slash. Guard extended, with three mutation proofs |

---

## 3. MINIMUM PATH TO 85

Ranked by points per unit of effort and by what has to be true first.

| # | Item | Points | Effort | Risk | Outage? | Owner decision? | Depends on |
|---|---|---:|---|---|---|---|---|
| 1 | **G6 b2** — confirm the R-A8Q fresh-install evidence and tick | 1.667 | minutes | none | no | reviewer judgement | nothing |
| 2 | **G5 b3** — upload size and bulk-count refusal tests | 2.000 | hours, offline | low | no | no | nothing |
| 3 | **G5 b5** — extracted text never logged, static + runtime | 2.000 | hours, offline | low | no | no | nothing |
| 4 | **G1 b6 + G4 b10** — push, and record the CI run | 4.000 | one push, one run | low | no | authorisation to push | branch freeze |
| 5 | **G3 b7 and b8 disposition** | +0.381 | a decision | none | no | **yes** | §4 and §5 of the execution plan |
| 6 | **G3 b2 + G4 b8** — one staging run of the org-admin spec | 2.833 | one focused staging window | medium | **staging only** | no | fixtures (done), staging stack |
| 7 | **G3 b3** — one staging run of the document lifecycle spec | 1.714 | same window | medium | staging only | share-leg SMTP decision, or accept partial | same |
| 8 | **G6 b3** — production-copy restore, then migrate | 1.667 | same window | medium | staging only | no | a production Mongo archive |

**Totals.**

* Without any Gate 3 withdrawal: items 1–4 and 6–8 give
  `1.667 + 2.000 + 2.000 + 4.000 + 2.833 + 1.714(→1.333) + 1.667`. At the current
  denominator each Gate 3 bullet is 1.333, so the non-Gate-3 total is **12.833**
  and **three** Gate 3 bullets are required: `12.833 + 4.000 = 16.833` →
  **85.33 → 85**. Two would give 84.0.
* With bullets 7 and 8 withdrawn (item 5), Gate 3's denominator becomes 7 and each
  bullet is worth `12/7 = 1.714`. The already-earned bullet 1 rises by 0.381, and
  **two** Gate 3 bullets suffice: `12.833 + 0.381 + 3.429 = 16.64` →
  **85.14 → 85**.

**Read that second line carefully.** The withdrawal moves the score **because the
denominator changed, not because evidence was captured.** That is only legitimate
if the withdrawals are right on their own merits — which is why
[GATE_3_EXECUTION_PLAN.md](GATE_3_EXECUTION_PLAN.md) §4 and §5 argue them on
those merits and leave the decision to the owner. **If the owner does not accept
them, the route is three Gate 3 bullets, not two.** Withdrawing a bullet to reach
a number is score manipulation; withdrawing a criterion the architecture cannot
satisfy is what Gate 2 already did, with a recorded approval and a structural
guard.

**Shortest defensible route, stated plainly:**

1. one reviewer confirmation (G6 b2);
2. two offline test suites (G5 b3, G5 b5);
3. one push and one green CI run (G1 b6, G4 b10);
4. one owner decision on Gate 3 bullets 7 and 8;
5. **one focused staging window** covering G3 b2 (+G4 b8), G3 b3 and G6 b3.

That is **85**, and it needs exactly one staging execution.

## 4. PATH TO 100

Everything above, plus:

| Item | Points | Blocked on |
|---|---:|---|
| G3 b4 — real contract ingestion end to end | 1.333–1.714 | a model-token budget; structural assertions, not textual |
| G3 b5 — timeline link verify/reject | 1.333–1.714 | the seeding path for an *unverified* proposed link |
| G3 b6 — chronology lifecycle | 1.333–1.714 | includes attach-to-arbitration, so it is downstream of bullet 7 |
| G3 b7 — arbitration | 1.333 | the acceptance receipt chain: production acceptance, crash/restart drills, legal HITL review, version diff/restore UI. **Weeks** |
| G3 b8 — empty/loading/error states | 1.333 | an enumerated production route list, generated from the router |
| G3 b9 — desktop and mobile smoke | 1.333–1.714 | 2, 3 and 5 first; then a viewport matrix |
| G9 b1–b6 | 8.000 | §5 |

100 requires bullet 7, and bullet 7 requires shipping the arbitration engine as a
primary path. **100 is not a release-timescale target.** 85 is.

---

## 5. Gate 9 entry contract

Read from the gate document itself, not from a summary. Gate 9 has six scored
bullets, and the Launch Gates preamble adds a condition that is easy to miss:
**"Production promotion is blocked until all gates are checked."** Reaching 85 is
necessary and is not sufficient.

| # | Requirement | Source | Current status | Evidence | Blocker |
|---|---|---|---|---|---|
| E0 | **Every gate checked** | Launch Gates preamble | **NOT MET** — 5/6, 6/6, 1/9, 8/10, 3/5, 4/6, 7/7, 8/8, 0/6 | the scorer | Gates 1, 3, 4, 5, 6 |
| 1 | Critical blockers closed | Gate 9 b1 | **NOT MET** | Blocker register: P0-002, P0-003, P0-007 (partial), P0-008 open | Gates 2/3 evidence, dependency posture, staging deploy evidence |
| 2 | High severity risks fixed or explicitly accepted | Gate 9 b2 | **PARTIAL** | Accepted in writing: no-fix CVEs (`docs/NOFIX_ADVISORY_ACCEPTANCE.md`, `docs/IMAGE_NOFIX_CVE_ACCEPTANCE.md`), shared S3 bucket, RPO/RTO as RECORDED-not-DEMONSTRATED, Gate 2 vector withdrawal | Needs one consolidated acceptance list the owner signs, rather than five documents |
| 3 | Staging smoke test passes after deploy | Gate 9 b3 | **NOT MET** | R-A8Q brought staging up and certified Gates 2/7/8 against it, but that was not a *deploy of this release to staging* followed by a smoke | A staging deploy of the release images, then `post_deploy_verify.sh` |
| 4 | Staging smoke test passes after restore drill | Gate 9 b4 | **NOT MET** | R-A8Q ran `mongo_restore.sh` (145/145 collections, 218/218 docs, 490/490 indexes, MATCH=YES) but did not smoke the application afterwards | Restore, then smoke, in that order, in one run |
| 5 | Readiness score ≥ 85 | Gate 9 b5 | **NOT MET** — 68 | the scorer | §3 |
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

**FOCUSED STAGING REQUIRED — one window, five bullets. Not a full Gate-2/7/8
rehearsal.**

| Bullet | What the run must do |
|---|---|
| **Gate 3 bullet 2** | `client/e2e/staging/org-admin-permissions.spec.ts` with `E2E_BASE_URL` and `CONTRACLAIM_STAGING_E2E=1` |
| **Gate 4 bullet 8** | the same run — it is the same measurement, so record it against both |
| **Gate 3 bullet 3** | `client/e2e/staging/document-lifecycle.spec.ts`; ticks only if the share-leg decision is made, otherwise it stays `EXECUTABLE_PARTIAL` |
| **Gate 6 bullet 3** | restore a production Mongo archive into `rsstg`, then `--list` / `--fail-on-warning` / `--apply` / re-apply |
| **Gate 9 bullets 3 and 4** | deploy the release images to staging, smoke; then restore, smoke again. Two smokes, in that order |

**Gates 2, 7 and 8 do not need re-running.** All three are full, their evidence is
from R-A8M and R-A8Q against the current images, and nothing in R-A8R changed a
production source file they depend on — the changes were `tests/`, `docs/`,
`client/e2e/`, `.env*.example`, and `scripts/post_deploy_verify.sh` plus its new
library. The last of those is Gate 7 bullet 7's evidence, so **`post_deploy_verify.sh`
must be re-run once** in whatever window comes next; it does not require a
dedicated one, and the edge change makes the run stricter rather than weaker.

**Do not schedule this window until Gate 3 bullets 7 and 8 are decided.** If the
owner takes 7-A and 8-C, the run needs two Gate 3 bullets rather than three, and
the enumerated-route work that 8-C implies is offline and can happen first.
