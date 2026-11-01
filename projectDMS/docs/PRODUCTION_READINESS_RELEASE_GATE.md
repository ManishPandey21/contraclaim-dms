# Production Readiness Release Gate

Status: Phase 8 final production-readiness audit recorded; current launch-readiness score is 91/100 (raw 90.67, exceeding the 85 target), re-derived from `scripts/production_readiness_score.py` on 2026-09-20 in release programme R-A9G-0 after the release owner ticked Gate 9 bullets 1, 2, 5 and 6 in a single owner-decision act (`docs/R_A9G_OWNER_DECISION_RECORD.md`) - four checkboxes and no new measurement, so the figure the sealed R-A9E staging evidence measured remains 85 (raw 85.33) and is the number to compare that evidence against. Before that it read 85/100 (raw 85.33) on 2026-09-20 in release programme R-A9F after the R-A9E Stage B staging execution on candidate `fe728b205ed5dfb20d88040bc44214ca71d11c43` earned Gate 3 bullet 2 and Gate 4 bullet 8, and before that on 2026-09-14 in release programme R-A8Z (82, raw 82.50) after its Stage B staging execution earned Gate 5 bullet 6 (live antivirus effective on a deployed stack) and re-earned Gate 9 bullets 3 and 4, and left Gate 3 bullet 2 and Gate 4 bullet 8 open on a measured spec defect (F-A8Z-B1), and before that on 2026-09-14 in release programme R-A8Y (78, raw 78.17), which added Gate 5 bullet 6 unticked (live antivirus effectiveness - production's clamd was serving a 70-day-old signature database behind every green check) and withdrew Gate 9 bullet 4 as stale, and before that on 2026-09-13 in release programme R-A8X (81, raw 81.17), which withdrew Gate 9 bullet 3 because its smoke passed only after an operator replayed a failed `production_backup.sh` by hand (F-A8W-B1), and before that after the R-A8W Stage B staging execution earned Gate 6 bullet 3 and Gate 9 bullet 4 and left Gate 3 bullet 2 and Gate 4 bullet 8 open on measured failures, and before that on 2026-09-09 after release programme R-A8S closed Gate 5 bullets 3 and 5 and attached the fresh-install evidence to Gate 6 bullet 2, and after R-A8R re-measured `npm run lint` as passing and after the R-A8Q Stage B staging execution certified Gate 3 bullet 1 against a deployed stack over TLS and re-confirmed Gate 2, Gate 7 and Gate 8 on current images, and after the R-A8M staging execution certified Gate 2 (6/6), Gate 7 (7/7) and Gate 8 (7/8) and the release owner's 2026-09-08 RPO/RTO decision closed Gate 8's eighth bullet as RECORDED (not demonstrated), after verifying FalkorDB backup and recovery with a destructive disposable drill (release programme R-A4), and after closing the upload-antivirus gate (P0-005) in code and adding Org-Admin permission HTTP-boundary regression coverage (P0-006). Live-integration, E2E, backup/restore, and sign-off blockers remain; the Python dependency scan is green as of release programme R-A5 with one recorded no-fix exception.

Current verdict: **Ready**, which is the scorer's own term for the current checkbox state and not a promotion decision. Gate 9 is now **6/6**: the release owner ticked bullets 1, 2 and 5 and signed bullet 6 on 2026-09-20 (`docs/R_A9G_OWNER_DECISION_RECORD.md`), after R-A8Z had earned bullets 3 and 4 against staging. **The gate conditions for production promotion are therefore satisfied, and one authorisation is not: the R-A9G maintenance window is an owner grant and has not been given.** Read the Gate 9 bullets for what those ticks rest on - bullet 1 was reached by formally disposing P0-002 as dated accepted debt with a 2026-10-15 follow-up, not by capturing the live-integration proof, and Gate 3 remains 2/9 with six browser-E2E bullets carried as owner-accepted debt under the same deadline. The open-bullet disposition matrix for the initial cutover is `docs/R_A9F_OPEN_GATE_MATRIX.md`; the owner's verbatim decisions are `docs/R_A9G_OWNER_DECISION_RECORD.md`. ClamAV signature freshness (F-A8X-2) is root-caused, fixed on the release branch (`docs/CLAMAV_SIGNATURE_FRESHNESS.md`), proven on the R-A8Z staging deployment, and applied to production's `clamav` service alone under a bounded owner authorization in R-A8Z (no other production service changed). Third-party production images carry 556 fixable CRITICAL/HIGH findings (re-measured 2026-09-16T05:12Z by running image id: MongoDB 273, FalkorDB 148, Qdrant 84, httpd 51; ClamAV and Redis 0). They no longer block cutover on their own, because R-A9E carries them under a **dated exception that expires on 2026-10-15** with a 2026-10-06 → 2026-10-15 remediation window (`docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md`, "R-A9E cutover exception"). That is a bounded deferral, not acceptance: on expiry the blocker is live again for any image still on those digests.

**What the ticks above are evidence for, and what they are not (R-A9F, 2026-09-20).** The runtime
code certified by the R-A9E Stage B staging execution is commit
`fe728b205ed5dfb20d88040bc44214ca71d11c43`, tree `b936ba255698b2a7bade787470baee215c34cf69`, on
`fix/role-assignment-resolution`. R-A9F integrated that commit into `release/contraclaim-rc1` by
**fast-forward** - `93bbc308` was already an ancestor - so at the moment of integration the release
tree *was* the certified tree, object for object, and every production-code and build-input category
matched with an identical git blob rollup. The full table is in the phase receipt
(`.claude/context/contract-master/R-A9F-RELEASE-INTEGRATION-FREEZE-RECEIPT.md`, step 5), which is
untracked and does not survive a clone; the tracked record of what is deployable is
`docs/R_A9F_PRODUCTION_MANIFEST.md`.

The sealed staging evidence behind the two ticks above is
`/var/backups/contraclaim-stg-evidence/R-A9E-20260916T081258Z/candidate-fe728b2/` — **65 files**,
`SHA256SUMS` sha256 `26ee94123faa121bd093c482d17627d67ab20617a868dd2406b3db271fe8b76a`, verified
with **0 non-OK lines**, secret scan CLEAN (69 files scanned, 13 values hunted). Segment A seal
`ac92b552a9c6245b` (50 files), Segment B seal `3e2c948af8bcd4e8` (60 files). It is sealed: read
it, never edit it.
Release commits **after** that fast-forward are documentation and release-control commits only; they
change no runtime source, no Dockerfile, no compose file, no migration, no seed and no script, which
is what keeps the R-A9E images and their scans valid for this HEAD. No staging run has been executed
against a release HEAD later than `fe728b2`, and none is claimed. If a future release commit touches
any build input, the R-A9E image provenance and these two ticks are stale and must be re-earned.

**One narrow, owner-accepted exception, R-A9G-0, 2026-09-20.** The R-A9G-0 owner-decision commit
also changes **one** file inside a Docker build context:
`backend/rbac_backend/tests/test_gate9_scoreability.py`, the anti-vacuity guard for Gate 9
bullet 1, re-anchored because the owner's disposition of P0-002 and closure of P0-008
legitimately left the blocker register with zero `Open` rows. Measured against the certified
candidate `fe728b2`, and stated as the exact field set rather than a rounded summary:

| Field | Value |
|---|---|
| Runtime production-code differences | **0** |
| Runtime configuration differences | **0** |
| Dependency differences | **0** |
| Migration differences | **0** |
| Dockerfile differences | **0** |
| Compose differences | **0** |
| **Docker build-context differences** | **1** |
| Exact build-context-only differing file | `backend/rbac_backend/tests/test_gate9_scoreability.py` |
| Classification | non-runtime Gate 9 security / release guard test |
| Runtime-imported differences | **0** |
| `./client` context | **0** differing files |
| `./services/graphiti` context | **0** differing files |

**The build-context count is 1, not 0, and is reported that way deliberately.** The file is a
test that nothing imports at runtime, but it sits inside the `./backend` build context and
`backend/Dockerfile` copies that context with `COPY . .`. Calling it "0 build-input differences"
would be the comfortable number rather than the true one, and it is precisely the claim this
exception exists to qualify.

So the accurate statement is no longer "the final release build inputs are byte-identical to the
certified candidate". It is: **the final release runtime production source and deployment
configuration are byte-identical to the certified R-A9E candidate, except for one non-runtime
Gate 9 guard test that happens to sit inside the Docker build context.** Proven, not assumed:
no file in the repository references that module outside itself; nothing imports the
`rbac_backend.tests` package from runtime (it is not even a package - it has no `__init__.py`);
no pytest plugin is registered outside the tests; and importing all four runtime entrypoints
(`rbac_backend.main`, `rbac_backend.worker`, `rbac_backend.scripts.migrate_database`,
`rbac_backend.scripts.align_role_contract`) loads **zero** `rbac_backend.tests` modules.

**Gate 3 bullet 2 and Gate 4 bullet 8 are NOT invalidated by this**, and the reason is recorded
rather than asserted: both were measured against the exact R-A9E certified runtime image; R-A9G
deploys **that exact image** (`docs/R_A9F_PRODUCTION_MANIFEST.md` pins it and forbids
substituting a rebuild); no runtime production source used by that image has changed; and the
sole later divergence is an unimported release guard. The measured deployed behaviour is
therefore the behaviour being promoted. **This exception does not broaden.** Any future change
to runtime source, dependencies, the Dockerfile, compose, a migration, runtime configuration, an
entrypoint, worker code or client runtime code stales the relevant image and staging provenance
normally.

This document is the tracked release-control checklist for moving Contraclaim DMS from the current hardening branch to a production candidate. Until every launch gate below is satisfied, feature work should be frozen except for production-readiness fixes, test fixes, security fixes, operational hardening, and documentation needed to prove readiness.

## Branch And Freeze Policy

- Active remediation branch created for Phase 0: `codex/production-readiness-phase-0`.
- Feature freeze rule: do not merge new product features into the production candidate until the critical launch blockers are closed or explicitly accepted by the release owner.
- Allowed changes during freeze:
  - failing-test fixes
  - security/RBAC fixes
  - migration/index discipline
  - E2E and integration test coverage
  - deployment, backup, restore, monitoring, and runbook hardening
  - documentation that records verified readiness evidence
- Disallowed changes during freeze:
  - new user-facing modules not needed for readiness
  - speculative refactors
  - mock-only implementations presented as production capability
  - production env relaxation to bypass validation

## Launch Gates

Production promotion is blocked until all gates are checked.

### Gate 1: CI And Local Test Baseline

- [x] `python -m pytest backend/rbac_backend/tests -q` passes with 0 failures.
- [x] Backend tests do not require live OpenAI, MongoDB Atlas Vector Search, Qdrant, FalkorDB, Redis, or network access unless explicitly marked as live integration tests.
- [x] `npm run lint` passes in `client`. evidence: client/package.json (release programme R-A8R, 2026-09-08: `npm run lint` -> `eslint . --max-warnings=0`, eslint v9.39.4, **exit 0, no output**, against `release/contraclaim-rc1` at `c66a1ef`)
- [x] `npm test -- --run` passes in `client`.
- [x] `npm run build` passes in `client`.
- [x] GitHub Actions passes for backend, frontend, dependency scans, and Docker image scans. evidence: .github/workflows/ci.yml (release programme R-A8T, 2026-09-09: run [34376328409](https://github.com/ManishPandey21/contraclaim-dms/actions/runs/34376328409) on `60f196c7c810`, event `pull_request` from PR [contraclaim-dms#20](https://github.com/ManishPandey21/contraclaim-dms/pull/20), **all five jobs green in one run** - `backend-checks` (pre-commit over the whole tree, `compileall`, the full backend suite) 3m51s, `frontend-checks` (toolchain recorded, `npm ci`, lockfile unchanged, lint, vitest, Playwright chromium, build) 5m04s, `dependency-scan` (`pip-audit` with exactly one recorded no-fix id, `npm audit --audit-level=high`) 1m13s, `docker-build-and-scan` (five image builds, the client package-manager class check, five Trivy scans at CRITICAL,HIGH with `exit-code: 1`) 12m30s, and `secret-scan` 13s. This is the first Actions run that has ever executed against this branch, and it took four attempts to get here: F-A8T-6 `pre-commit` rewrote a tracked artefact, F-A8T-11 ten Playwright staging tests errored instead of skipping, F-A8T-12 `gitleaks-action` was refused the PR-commits API without `pull-requests: read`, F-A8T-13 the pinned Trivy action tag no longer resolved and its successor could not install its own default scanner, F-A8T-14 four CVEs in the transitive `httpx2`/`httpcore2`, and F-A8T-15 the image job exhausted the runner's disk. The first two were found and fixed locally before the first push; the rest are what this run bought)

Correction history for the lint box, because it has been wrong in both directions.

**R-A4:** the box was ticked and was not true. `npm run lint` runs
`eslint . --max-warnings=0` and **exited 1** on two
`react-refresh/only-export-components` warnings in
`client/src/pages/PermissionsPage.tsx`. It was unticked.

**R-A8R (2026-09-08):** it is now true, and the paragraph above had gone stale in
the other direction. Release commit `6448663` *fix(client): let the permissions
page export only a component* removed the two warnings, and the command was
re-executed on this branch: eslint v9.39.4, **exit 0, no output**. A correction
that outlives the defect it describes is the same failure as the original tick,
so the box is ticked and this is what it was measured with.

**This is a local run, and Gate 1 bullet 6 is still open.** CI running the same
command on the pushed branch is a separate bullet and a separate piece of
evidence; nothing here earns it.

Current known baseline:

- Full backend suite observed locally after Phase 4 contract hardening: 480 passed, 7 skipped, 238 warnings.
- Backend LlamaIndex tests are now hermetic: `backend/rbac_backend/tests/test_llamaindex_service.py` monkeypatches service-level LlamaIndex/OpenAI/Mongo vector dependencies even when real packages are installed.
- Frontend lint observed locally: passed with `eslint . --max-warnings=0`.
- Frontend Vitest observed locally with CI heap setting: 58 passed, 3 skipped.
- Frontend Playwright E2E observed locally after Phase 5: 5 passed.
- Frontend build observed locally: passed. Build still reports a non-fatal stale Browserslist data warning.
- Frontend dependency audit observed locally after removing the vulnerable React PDF Viewer/PDF.js stack: `npm audit --audit-level=high` found 0 vulnerabilities.
- Python dependency audit observed locally after resolving initial AWS/OpenAI resolver conflicts: `pip-audit -r backend/rbac_backend/requirements.txt` still found 62 known vulnerabilities in 22 packages.
- Python audit remediation is not a small patch: safe completion requires a coordinated FastAPI/Starlette upgrade plus LangChain/LangGraph/Pydantic-AI package-family upgrades, followed by a full backend regression run.
- Local `pre-commit run --all-files --show-diff-on-failure` could not be executed because the active Windows Python environment does not have `pre_commit` installed. CI installs `pre-commit` before running the hook set, so final proof remains the GitHub Actions run.

### Gate 2: Live Integration Baseline

- [x] Run live external tests with `RUN_EXTERNAL_INTEGRATION_TESTS=1` against staging. evidence: backend/rbac_backend/tests/integration/test_external_services_integration.py (R-A8M staging run 2026-09-07: 45 collected, 45 executed, 0 skipped, under `CONTRACLAIM_STAGING_GATE=1`)
- [x] Verify OpenAI document round trip. evidence: backend/rbac_backend/tests/integration/test_external_services_integration.py (R-A8M staging run 2026-09-07: `test_openai_service_document_round_trip_live` passed against the staging key)
- [x] Verify Qdrant vector round trip. evidence: backend/rbac_backend/tests/integration/test_qdrant_containment_live.py (R-A8M staging run 2026-09-07: 9 passed, plus `test_qdrant_langchain_vector_round_trip_live`)
- [x] Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only). evidence: backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py (R-A8M staging run 2026-09-07: 21 passed against the authenticated staging engine, 0 skipped; `GRAPH.LIST` empty before and after)
- [x] Verify Redis queue/runtime-state paths. evidence: backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py (R-A8M staging run 2026-09-07: 9 passed)
- [x] Record all required live integration env vars used for the run. evidence: backend/rbac_backend/tests/staging_gate.py (R-A8M staging run 2026-09-07: `render_report()` captured, 15 findings PRESENT, no value recorded)

Every bullet in this gate is a **staging** requirement: it is satisfied only by a run
against a real staging environment with the live dependencies enabled. A local run
against developer containers is a precondition, never a substitute, and earns no
checkbox here.

**Evidence convention.** A bullet in this gate may be checked only when the same line
carries `evidence: <path-or-command>` naming the executable artefact that proves it —
a test module, a script, or a recorded command. `test_release_gate_specification.py`
rejects a checked bullet with no evidence reference, and rejects an evidence reference
that names a repository path which does not exist.

Current skipped live-test evidence:

- `backend/rbac_backend/tests/integration/test_external_services_integration.py` skips live tests unless `RUN_EXTERNAL_INTEGRATION_TESTS=1`.
- The same test module skips again when required live integration env vars are missing.
- `backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py` covers the bullet 5 queue and runtime-state paths and skips on the same switch. It exists so bullet 5 can carry an `evidence:` reference at all; until a staging run executes it, it is a precondition and not gate evidence.
- These skips are acceptable for normal unit CI, but production release requires a separate staging run with the live dependencies enabled.

**Staging mode is not optional for this gate.** Several live suites default to
developer endpoints - `FALKOR_TEST_HOST`/`FALKOR_TEST_PORT` to `localhost:6380`,
`QDRANT_TEST_URL` to `http://127.0.0.1:6333` - and the Qdrant containment suite
falls back to the checked-out `config/secrets/qdrant_api_key`. A staging run
launched on a developer machine without overrides would therefore measure the
development engines and report the result here. `CONTRACLAIM_STAGING_GATE=1`
(`backend/rbac_backend/tests/staging_gate.py`) refuses every one of those
defaults before collection, requires each endpoint and credential to be supplied
explicitly, and converts a skip in a required live module into a failure. A run
offered as evidence for any bullet in this gate must have been launched with it
set.

#### Superseded requirement: FalkorDB vector round trip

**Old requirement (verbatim, preserved):** "Verify FalkorDB graph/vector round trip."

**Status: SUPERSEDED - 2026-09-04, release `release/contraclaim-rc1`.** Replaced by
"Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only)."
The graph half is unchanged in substance and now names the command family and the
safety rule; the vector half is withdrawn for the reasons below.

**Why the vector half was factually invalid.** It required a FalkorDB *vector* round
trip, which in this codebase means `FalkorDBVectorService`, whose `_create_index`
issues the RediSearch `FT.CREATE` command
(`backend/rbac_backend/services/falkordb_vector_service.py:41`). The deployed engine
does not provide it. Measured on 2026-09-04 against the exact production pin in
`docker-compose.prod.yml`, `falkordb/falkordb:v4.0.8`: Redis 7.2.4, `MODULE LIST`
returns the single module `graph` (version 40008), `COMMAND INFO FT.CREATE` returns an
empty reply, and invoking the command returns
`ERR unknown command 'FT.CREATE'`. The criterion was unsatisfiable by the supported
architecture, not merely unproven.

**Historical architecture assumption.** The requirement dates from a design in which
FalkorDB served as a second vector store alongside Qdrant, written against a Redis
Stack image carrying the RediSearch module. `services/data_sync.py` still reflects that
design: it writes the same payloads to both `LangChainVectorService` and
`FalkorDBVectorService`.

**Current architecture.** Qdrant is the only vector store; FalkorDB is the knowledge
graph, holding letter and clause references. This is the recorded project decision
(`CLAUDE.md`: "Qdrant is the **only** vector store ... FalkorDB keeps `GRAPH.*` use
only") and the documented architecture (`docs/ARCHITECTURE.md`). Mechanically, on this
HEAD: `FalkorDBVectorService` is imported by exactly two places,
`services/data_sync.py` and the integration test module, and `data_sync.sync_data` has
**no caller anywhere in the tree** - it runs only if a human invokes the module
directly. `FalkorGraphService`, by contrast, has live production callers in
`ai_workflows/langgraph/letter_pipeline.py`, `graph/graph_ingestion_service.py`,
`routers/storage_sync.py`, `services/contract_clause/agent.py`,
`services/contract_graph_service.py` and `services/document_service.py`.

**The property the old bullet was meant to establish** - read from its own wording and
from its five siblings, each of which names one live dependency - is that *every
external dependency the production application actually uses answers a real round trip
in the deployed environment*. That property is preserved in full: the graph round trip
still covers FalkorDB, and the vector round trip is already required of Qdrant by the
sibling bullet, which is where the production vector path now lives. Nothing a
production caller depends on has stopped being tested.

**Security and reliability equivalence.** No control is removed. The vector round trip
is still required, of the component that actually serves it. The graph round trip now
additionally names the disposable-namespace rule, so satisfying the gate cannot mutate
a business graph - a constraint the old wording did not carry.

**Local test.** `backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py`
(graph consumer and authority containment) and
`backend/rbac_backend/tests/integration/test_qdrant_containment_live.py` (vector
containment). Both skip when the engine is unreachable, so they are preconditions, not
gate evidence.

**Staging test.** `test_external_services_integration.py::test_falkordb_vector_service_round_trip_live`
implements the withdrawn requirement. It remains in the tree and is not gate evidence
for any bullet. The staging run covers the replacement through the graph suites above
plus the Qdrant live round trip.

**How the withdrawal is enforced, and why it had to be (F-A8Q-1).** For four days it
was not. Required Gate 2 membership
(`backend/rbac_backend/tests/staging_gate.py::GATE2_REQUIRED_LIVE_FILES`) is decided
per **file**, and the withdrawn test lives inside a required file, so the paragraph
above said the test was not gate evidence while the harness still ran it as part of a
required module. R-A8Q's certification run measured the consequence: **45 collected, 45
executed, 44 passed**, the single failure being a test for a requirement withdrawn on
2026-09-04.

Membership is therefore stated at test granularity as well.
`GATE2_WITHDRAWN_LIVE_TESTS` names the node ids inside required modules that Gate 2
does not score, and `CONTRACLAIM_STAGING_GATE` **deselects** them - announced by node
id in the terminal summary, never silently. Outside that switch nothing is removed, so
the test still runs in every ordinary and full-suite run as legacy coverage of a
service that still exists. Deselection rather than skip or xfail is deliberate: a skip
inside a required module is converted to a failure by the same conftest, which is the
right rule for an unmeasured requirement and the wrong answer for one that has been
withdrawn.

`backend/rbac_backend/tests/test_gate2_required_inventory.py` is what stops the
inventory drifting in either direction. It derives each module's capabilities from its
source rather than its filename and refuses: a live FalkorDB vector test inside a
required module that is *not* withdrawn; the loss of Qdrant vector coverage; the loss
of FalkorDB `GRAPH.*` coverage; a withdrawal naming a test that does not exist; a
withdrawal outside the required set; and a withdrawal that would remove coverage
another bullet is scored from. Each rule has a synthetic mutation that breaks exactly
it. Reinstating the requirement is one deletion from `GATE2_WITHDRAWN_LIVE_TESTS`.

**Owner approval: APPROVED FOR `release/contraclaim-rc1`.** Not generalised beyond it.

**Review trigger.** Reinstate the vector half if FalkorDB regains a production
vector-search role - specifically if any production module imports
`FalkorDBVectorService`, if `data_sync.sync_data` gains a caller, or if the deployed
FalkorDB image gains a module providing `FT.CREATE`. The first two are enforced by
`backend/rbac_backend/tests/test_release_gate_specification.py`, which fails the moment
a production caller appears.

#### Gate 2 local preconditions - executed 2026-09-04, not scored

Engine-level preconditions run against local developer containers. **They are not
staging, they satisfy no bullet above, and they earn no points.** They exist so that a
staging failure can be attributed to the environment rather than to the code.

- FalkorDB reachable; `MODULE LIST` = `graph`, `vectorset`; `FT.CREATE` rejected as an
  unknown command; disposable-graph create/query/delete round trip PASS; `GRAPH.LIST`
  identical before and after.
- Redis reachable; runtime-state set/get/delete and queue push/pop round trips PASS.
- Qdrant reachable; disposable collection created, three known vectors upserted,
  nearest-neighbour search returned the expected point, collection deleted; inventory
  identical before and after.
- Application integration: graph end-to-end material-influence suite 21 passed; Qdrant
  containment suite 9 passed, both against the live local engines.
- Negative controls, all failing as required: Falkor on a wrong port, Redis on a wrong
  port, Qdrant on a wrong endpoint, Qdrant without an API key, `FT.CREATE` against the
  deployed engine, and the disposable-graph helper asked to delete `contraclaim`.

### Gate 3: Browser E2E Coverage

- [x] Login, logout, session refresh, and CSRF behavior. evidence: client/e2e/staging/session-authentication.spec.ts (R-A8Q Stage B staging run 2026-09-08: 4/4 passed, unmocked, chromium, against the deployed staging stack over TLS with `CONTRACLAIM_STAGING_E2E=1`; `POST /api/refresh` 200 with exactly one `token_refresh` audit row whose `user_id` resolves to the signed-in user and whose `resource_id` equals the `session_id` in the reissued token; a cookie-authenticated unsafe request without a CSRF header refused)
- [x] Org-Admin permission save/retrieve, including Client DMS permissions. evidence: client/e2e/staging/org-admin-permissions.spec.ts (R-A9E Stage B staging run 2026-09-20T11:31Z on candidate `fe728b2`: **12/12 passed**, 1 worker, 0 retries, 0 unexpected skips, 0 failures, 27.6s, unmocked chromium against the deployed staging stack over TLS with `CONTRACLAIM_STAGING_E2E=1`; the mandatory Security Terms screen is accepted through the page a user would use, the Permissions tab is selected, and the Client DMS grant is saved as a browser action behind the step-up dialog, then read back after a reload and confirmed through `GET /api/roles/{id}/permissions`; the same write without a step-up token is refused 403 and the read-back is unchanged)
- [ ] Document upload, view, download, share, and authorization denial.
- [ ] Contract upload, ingestion status, clause extraction, search, Q&A, and appraisal.
- [ ] Contract timeline link verify/reject.
- [ ] Chronology create, extract, verify/reject, export, and attach-to-arbitration flow.
- [ ] Arbitration draft create, generate, edit, save version, approve/return, export DOCX/PDF.
- [ ] Empty and loading states across the applicable routes in the route inventory `docs/GATE_3_ROUTE_INVENTORY.md`.
- [ ] Desktop and mobile smoke coverage for primary workflows.

**Evidence convention.** Same as Gate 2, plus the part only this gate needs. A
bullet may be checked only when the same line carries `evidence: <path>`, that path
exists, and `docs/GATE_3_EVIDENCE_MATRIX.md` records the bullet as `EXECUTABLE` -
meaning an unmocked spec that runs against a **deployed** stack.
`test_gate3_evidence_matrix.py` enforces all three. A mocked suite proves the
component renders its own states and proves nothing about a deployment, so it can
never satisfy a bullet here.

**How a run reaches staging.** `E2E_BASE_URL` points the Playwright suite at a
deployment and suppresses the dev server; `CONTRACLAIM_STAGING_E2E=1` turns a
missing staging variable from a skip into a failure, the same rule
`CONTRACLAIM_STAGING_GATE` applies to Gate 2. The two mocked suites are excluded
automatically when `E2E_BASE_URL` is set.

Current baseline:

- Playwright E2E is present in `client/package.json`, `client/playwright.config.ts`, `client/e2e/`.
- `contract-workflows.spec.ts` and `contract-master.spec.ts` intercept `**/api/**`. They cover mocked browser workflows for contract upload/progress, search success/empty/error, Q&A validation/cited answer, appraisal generation/report opening, and a mobile contract-search smoke check - and they are excluded from any run that targets a deployment.
- `login-responsive.spec.ts` and `blog.spec.ts` run unmocked at desktop, tablet and 390 px, over the public and login surfaces.
- `staging/session-authentication.spec.ts` covers bullet 1 against a deployment. Written in R-A8J from the server's own contracts and **not yet executed against one**.
- **Bullet 4 was unchecked in R-A8J.** It had been ticked with no `evidence:` reference, and the only spec covering its subject mocks the API - which this gate's own preamble says earns no checkbox. Unticking it lowers the recorded score by one bullet and makes it match what has actually been measured.
- The remaining seven bullets, the property each one asks for, and what is missing are enumerated in `docs/GATE_3_EVIDENCE_MATRIX.md`.

#### Amended requirement: bullet 8, state coverage

**Old requirement (verbatim, preserved):** "Empty, loading, and API-error states for
every production route."

**Status: AMENDED - 2026-09-09, release `release/contraclaim-rc1`, owner decision
8-C.** Replaced by "Empty and loading states across the applicable routes in the
route inventory `docs/GATE_3_ROUTE_INVENTORY.md`." **The UI quality bar is
unchanged.** What changed is where two of the three states are certified, and
that the route set is now enumerated instead of implied.

**Why the old wording could not be satisfied as written, in two independent
ways.**

*The error state.* Gate 3's own preamble says every bullet is satisfied only by a
run against a real staging environment, and `test_gate3_evidence_matrix.py`
refuses to tick a bullet whose evidence is `MOCKED`. An API error state requires
the API to fail on demand, which requires mocking or fault injection - at which
point the run is no longer the unmocked staging run this gate requires. The
bullet asked for two mutually exclusive things, so no evidence could ever satisfy
it. R-A8J recorded this and did not decide it; R-A8R restated it; this is the
decision.

*The denominator.* "Every production route" named no route set. A criterion with
no denominator cannot go green and cannot be shown to have been missed - the same
unfalsifiability that took the FalkorDB vector criterion out of Gate 2 on
2026-09-04.

**Where the error-state requirement went.** To the component and mocked-E2E
layer, which is where controlled failure injection is legitimate and where the
existing coverage already lives: `client/e2e/contract-workflows.spec.ts` (search
success/empty/**error**, Q&A validation, appraisal), `client/e2e/contract-master.spec.ts`,
and the page tests under `client/src/pages/__tests__/`. Every route in the
inventory names its error-test location, and `MISSING` is recorded where none
exists yet - a debt Gate 3's execution plan tracks, not a bar that was lowered.
`test_gate3_route_inventory.py::test_the_error_state_requirement_survives_outside_gate_3`
fails if that coverage is deleted rather than moved.

**The denominator.** `docs/GATE_3_ROUTE_INVENTORY.md`, generated by
`scripts/gate3_route_inventory.py` from `client/src/routes.tsx` - the
registration React Router actually uses, so the list cannot be maintained by
hand or drift from the application. It resolves nested routes to full paths,
reads the session boundary from the `ProtectedRoute` ancestry, and names the page
component each route renders. **92 routes, 85 authenticated, 85 owing a loading
state, 59 owing an empty state**, on this HEAD.

Empty/loading applicability and the error-test location are **declared** per page
in `PAGE_STATES`, not inferred: there is no single empty-state component to
detect, and a substring heuristic over TSX would repeat the mistake
`route_control_manifest.json`'s `enforcement` field made. The guard therefore
enforces *completeness* - a router page with no declaration fails, and a
declaration naming no page fails - and says so rather than implying it verifies
the judgement.

**Security and reliability equivalence, corrected in R-A8T.** Empty and loading
states are now required of a named set rather than an unnamed one, which is a
higher bar than "every route" was in practice, because "every route" was
measured against nothing. That half stands.

**The error-state half was overstated and is restated here with the
measurement.** The claim was "still required and still tested". Counted from a
fresh `scripts/gate3_route_inventory.py --format json` run: of the **85** routes
owing an empty or loading state, **36** name an existing error test and **49**
- across **42** distinct page components - are recorded `MISSING - no
fault-injection coverage yet`. So for the majority of the applicable set the
error-state requirement was relocated to coverage that does not exist yet. The
inventory has always said `MISSING` row by row; the prose above it did not say
how many, which is the kind of true-in-detail, false-in-summary claim this
programme exists to catch.

**What is now enforced rather than described:**
`test_gate3_route_inventory.py::test_the_relocated_error_coverage_does_not_shrink`
holds the located count at or above the 36 measured on 2026-09-09, so the
relocation cannot be quietly emptied out; the 49 remaining are Gate 3 bullet 8's
outstanding work and are enumerated in the inventory.

**Owner approval: APPROVED FOR `release/contraclaim-rc1` (option 8-C).** Not
generalised beyond it. **No point is earned by this amendment**: the bullet stays
unticked until a staging run measures the empty and loading states.

**Review trigger.** Reinstate an in-gate error-state requirement if Gate 3 ever
accepts fault-injected staging evidence - that is, if the gate's "no mocked
evidence" rule is itself amended. Until then,
`test_gate3_route_inventory.py::validate_gate3_state_bullet` fails the moment a
Gate 3 bullet asks for an error state again, or drops the named denominator.

#### Bullet 7 is NOT superseded - correcting the record

R-A8S opened with an owner proposal to withdraw bullet 7 (arbitration draft
create/generate/edit/version/approve/export) on the basis that "the arbitration
engine is intentionally not the primary production path". **Re-derived from the
source, that basis does not hold, and the owner withdrew the proposal.** The
bullet stays in the scored gate.

What is non-primary is the *LangGraph arbitration workflow engine*
(`ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ARBITRATION_ENGINE_ROLLOUT_MODE=off`,
`ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false`). Bullet 7 names the arbitration
**drafting** surface, which is a different code path:

- `ArbitrationDraftingService.generate` selects its generator from
  `ARBITRATION_DRAFT_MODE`, whose default is `deterministic`, and never consults
  `ArbitrationEnginePolicy`. `langgraph_v1` is reached only from
  `services/arbitration_drafting/workflow_service.py`, the case-workflow surface.
- Under the shipped configuration the engine policy returns `arbitration_v2` for
  every request (`engines/policy.py`, branch `default_off_or_v2`), so
  `arbitration_v2` **is** the primary production path, not a disabled one.
- Every leg the bullet names has a live registered endpoint under
  `/api/arbitration/drafts` (`main.py` includes the router), and the client
  registers `/arbitration/drafts` and `/arbitration/drafts/:draftId`.

So bullet 7 is satisfiable against staging today, in deterministic mode, at zero
model cost, without changing any rollout flag. Withdrawing it would have removed
E2E coverage of a live production feature and added ~1.33 points on a premise the
source contradicts. `test_release_gate_specification.py::test_the_arbitration_drafting_path_is_independent_of_the_langgraph_rollout`
re-derives the distinction on every run, so it cannot quietly stop being true.

### Gate 4: Security And RBAC

- [x] `ALLOW_DEV_HEADERS=false` in production release config; production startup validation rejects `true`.
- [x] `RBAC_ENTITLEMENT_FAIL_OPEN=false` by default and in production release config; production startup validation rejects `true`.
- [x] `AUTH_COOKIE_SECURE=true` in production release config; production startup validation rejects `false`.
- [x] `SECRET_KEY` production startup validation requires a non-placeholder value of at least 32 characters.
- [x] Production CORS release config contains no localhost/dev origins; production startup validation rejects localhost/dev origins.
- [x] Org/project tenant isolation tests pass.
- [x] Permission seed drift tests pass.
- [x] Production Org-Admin permissions are manually validated in staging/prod. evidence: docs/GATE_4_B8_ORG_ADMIN_VALIDATION_MAPPING.md (R-A9E Stage B staging run 2026-09-20T11:34Z on candidate `fe728b2`, fresh initializer, no production-copy fixture reused: **all 14 rows PASS**. Rows 1-8 and 10-14 by independent manual probes outside the spec, each with its own positive control, exact expected status, actual status and DB read-back; row 9 by the deployed spec test `navigating the admin pages surfaces no foreign data and no forced logout`. No row is inferred from another. Plus the two sanity properties on the deployed stack: a soft-deleted role stops granting (old session 401, fresh session 403) and `dms.task.manage` alone no longer satisfies a project update (403) while `dms.project.manage` does (200), both with schema-valid bodies that reached authorization rather than 422)
- [x] No real `.env` or secret files are tracked.
- [x] Secret scan passes. evidence: .gitleaks.toml (release programme R-A8T, 2026-09-09: `secret-scan` green in run [34376328409](https://github.com/ManishPandey21/contraclaim-dms/actions/runs/34376328409) on `60f196c7c810` - `gitleaks/gitleaks-action@v2` with `fetch-depth: 0`, so the whole history was scanned, against the root `.gitleaks.toml` whose allowlists exempt specific non-secret VALUES and never a directory. The repository is user-owned rather than organisation-owned, checked from the API, so no `GITLEAKS_LICENSE` applies - R-A8S flagged that as a risk to verify from the run rather than assume, and this is the verification. F-A8T-12: the job's first attempt died 403 `Resource not accessible by integration` calling the PR-commits API, because the workflow granted no `pull-requests` scope; it scanned nothing and its red meant nothing. `pull-requests: read` added)

### Gate 5: Upload And Content Safety

- [x] `ANTIVIRUS_ENABLED=true` in production. Enforced by `Settings.validate_runtime_configuration`: production refuses to boot when antivirus is disabled unless `ANTIVIRUS_REQUIRED_IN_PRODUCTION=false` is set as a recorded override. Proven by `test_config_validation.py::test_production_validation_requires_antivirus_enabled` and `::test_production_validation_allows_explicit_antivirus_opt_out`.
- [x] `CLAMAV_FAIL_OPEN=false` in production. Enforced by startup validation; proven by `test_config_validation.py::test_production_validation_rejects_antivirus_fail_open`.
- [x] Upload MIME, extension, size, and concurrency limits are validated. evidence: backend/rbac_backend/tests/test_upload_limit_enforcement.py (R-A8S 2026-09-09: 21 passed. Boundary at the configured cap, one byte below / exactly / one byte over; a forged `Content-Length` refused because the limit counts bytes actually read; the refusal trips mid-stream and leaves no spool file. Bulk count and total size at and over `BULK_UPLOAD_MAX_FILES` / `BULK_UPLOAD_MAX_SIZE_MB`; the (N+1)th concurrent upload refused with 429 per user and per organisation. Both upload surfaces driven end to end and asserted at **413** - F-A8S-2, the document surface answered 500 and the contract surface 422 before this phase. F-A8S-4, found reviewing this tick: six further upload sites read the whole body into memory - `bank_guarantees.py`, `key_dates.py` and `deep_planning.py` with **no application-level size limit at all**, and `profiles.py`, `folder_structure.py` and the contract chunk endpoint with one applied *after* the read. All six now go through `read_upload_within_limit`. **R-A8T re-reviewed this tick and found a seventh, plus the reason the guard could not see it. F-A8T-1: `POST /api/documents/bulk-upload` takes TWO upload parameters. The route caps `files` by count and total size and never measured `csv_file`, and `services/bulk_upload_service.read_csv_from_upload_file` read that one with `upload_file.file.read()` and then `.decode()`d it - two resident copies of a body bounded only by the gateway. The guard matched only `ast.Name.read()` in `routers/`, so an attribute chain in `services/` was invisible to it. `read_file_within_limit` is the synchronous sibling of the async seam and the CSV read now uses it; the method's `except Exception` tail, which rewrote every failure as `ValueError("CSV processing failed")`, now re-raises `HTTPException` first, so the refusal stays a 413. The guard is widened to `routers/`, `services/` and `utils/`, catches `x.file.read()` and `List[UploadFile]` loop variables, and has three synthetic controls proving it catches each shape. F-A8T-4, the MIME half: `routers/profiles.py` validated `file.content_type` - the client's own header - and wrote it into the stored `data:` URL. It now sniffs the bytes and stores the sniffed type, and GIF and WebP signatures were added to `sniff_mime_from_bytes` so the allowlist it enforces can actually be measured. **R-A8U re-reviewed this tick after an independent security review reported the guard measures spellings while the bullet claims a property.** F-A8T2-5: the guard watched only parameters whose *annotation* named `UploadFile`, so `FileService.store_file(self, upload_file, ...)` did `await upload_file.read()` inside a scanned tree while the guard was green; `FileService.store_chunk` and `S3Service.upload_file` had the same shape. F-A8T2-6: `if node.args: continue` accepted any argument as a chunk request, so `await file.read(-1)` - Starlette's own default, which reads the whole body - passed, as did `read(None)`, an alias, a subscript and an `enumerate` loop variable. The analyser moved to `tests/upload_surface.py` and now recognises an upload by annotation OR name, follows it through assignment, tuple unpacking, `for`/`with` targets, subscripts and attribute chains, and accepts a read only when its size is present, non-`None`, non-negative and no larger than the configured cap. Run red first it reported four live sites: the three unannotated service reads above, **and `POST /api/billing/webhooks/{provider}`, which is intentionally unauthenticated, names no `UploadFile` at all and did `await request.body()`** - so no annotation-shaped guard could ever have seen it, and the only bound was the gateway's `client_max_body_size 200m`. All four are bounded now. `read_request_body_within_limit` is the new streaming seam for a raw `Request`; it raises the same 413, returns bytes identical to `request.body()` (a webhook signature is computed over them) and caches them the way `Request.body()` does. `WEBHOOK_MAX_BODY_SIZE_KB` defaults to 256. `tests/test_upload_route_inventory.py` is the denominator the bullet never had: **19** upload-capable routes derived mechanically from the routers and classified through up to three delegate hops for read method, limit source, limit-before-read and persistence-before-limit; a route the classifier cannot read is a RED test, not a warning. Mutation controls: a route's size seam deleted turns the class guard red; a row written before the seam is reported as persistence before the limit; a raw read placed beside a seam is reported as unbounded. **A second independent review then found that closing the webhook by name had not closed its class.** `POST /api/client-errors` declares a Pydantic body model and never touches `request.body()`; FastAPI materialises and JSON-parses the whole body before the handler is entered, so the model's per-field `max_length` caps and the 60-per-minute IP rate limiter both decide after the memory has been held - on a route that is unauthenticated by design and whose own docstring calls itself "size-capped". Four unauthenticated routes have that shape (`/api/client-errors`, `/api/contact`, `/api/login`, `/api/token`) and no upload guard can see any of them, because the read is FastAPI's. `core/public_body_limit.py` is one pure-ASGI seam for the class: it counts the bytes actually received for a declared set of public paths and answers 413 before the application is entered, with caps read from configuration and per path - one global cap would have to exceed `BULK_UPLOAD_MAX_SIZE_MB` and would then bound a telemetry beacon at 500 MB. Proved through the real application: `/api/client-errors` and `/api/login` answer 413 for a 200 KB body while the handler records nothing, a real beacon still returns 202, an upload route is deliberately **not** capped here, and the mutation control rebuilds the app without the middleware and requires the same request through. The same review also found the class guard recognising a whole-request read by the word "request" in the receiver's spelling (so `req: Request` escaped), `.form()`/`.json()` unwatched, and six drain shapes that never spell `.read()` - `copyfileobj`, `readlines`, `getvalue`, `join`, `list` and `for line in upload.file`. All closed, with the false-positive boundary pinned: iterating a `List[UploadFile]` is walking a list, not draining a body. 134 tests)
- [x] Contract uploads fail closed when ClamAV is unavailable. `routers/contracts.py` and `routers/documents.py` reject with HTTP 400 when `scan_file` returns not-clean; `AntivirusService` returns not-clean when the daemon is unreachable with `fail_open=false` (`test_antivirus_service.py::test_scan_offline_fail_closed`, `::test_scan_clamav_error_fail_closed`). In production the scan branch is always active because antivirus is required.
- [x] Live antivirus in the deployed stack is effective, not merely available: clamd is reachable from the backend, the signature database clamd has **loaded** is within `CLAMAV_SIGNATURE_MAX_AGE_HOURS` (default 48; WARN after 24), a clean file is accepted and EICAR is rejected over INSTREAM - all four in one canonical `scripts/post_deploy_verify.sh` run against the deployed release (`scripts/lib/clamav_readiness.sh` -> `scripts/check_clamav_signature_freshness.py`). **Added unticked in release programme R-A8Y (2026-09-14).** Every bullet above measures configuration or availability; none measured what clamd scans *with*. R-A8Y's read-only production diagnosis found clamd healthy, reachable and fail-closed while serving daily database 28051 built 2026-07-05 06:24 UTC (1,700 h / 70.8 days old): `clamav` sat only on the internal `service-net`, so freshclam had no route and no DNS (`Could not resolve hostname`, 68 update runs, 0 updates since 2026-07-09), and nothing persisted `/var/lib/clamav`. The release fix (egress-net, `clamav_db` volume, `FRESHCLAM_CHECKS=12`, the freshness gate) is proven offline and in a disposable drill (`docs/CLAMAV_SIGNATURE_FRESHNESS.md`); this bullet is earned only by a deployed-stack run. Guards: `test_clamav_signature_freshness.py`, `test_clamav_update_path.py`, `test_post_deploy_clamav_readiness.py`. evidence: docs/CLAMAV_SIGNATURE_FRESHNESS.md (R-A8Z Stage B, 2026-09-14, run `R-A8Z-STAGEB-20260914T143311Z`, release `566a01a`, fresh `contraclaim-stg` deploy on backend `808df1f1a741`, client `7369f50b7760`, `clamav/clamav:1.4.6@sha256:71fbb76b...`: one canonical `scripts/post_deploy_verify.sh` run, exit 0, 24 PASS, 1 WARN, 0 FAIL, whose ClamAV line reads `CLAMAV_READINESS=OK scope=full age_hours=8.60` - clamd reachable from the backend container, loaded daily 28123 built 2026-09-14T06:24:19Z, clean file `stream: OK`, EICAR `Eicar-Test-Signature FOUND` (`21-smoke-raw-after-deploy.txt`). Supporting, from the same window: the staging certification `07` - freshclam updated daily to 28123 over `egress-net` with 0 failures, the `/init` race reproduced (`Clamd was NOT notified`) and healed by the healthcheck RELOAD, database files sha256-identical and the loaded version unchanged across a container recreation on `clamav_db`, peak 949 MiB of 3 GiB - and the same check green again after the restore drill (`31`, 8.78 h))
- [x] Sensitive extracted text is not logged. evidence: backend/rbac_backend/tests/test_extracted_content_not_logged.py (R-A8S 2026-09-09: 26 passed. An AST guard over every logging call in the ingestion and extraction trees - positional, f-string, `%`, `.format`, concatenation, slices, method calls and `extra={}` - with metadata logging explicitly preserved; plus a runtime control that pushes a distinctive marker through the real chunker with the root logger captured at DEBUG and finds it in the chunks and in no log line, field or traceback. It found one real channel: Marker's subprocess stderr, now logged as a length. The guard's scope is the ingestion and extraction trees; seven further modules that handle document-derived text were checked by hand in R-A8S with no real finding, and are named in `CHECKED_BY_HAND_OUTSIDE_THE_GUARD` so that widening the guard is a deliberate act. **R-A8T re-reviewed this tick and found three more channels and one vacuous test.** F-A8T-2: the guard read the arguments of logging calls and not of `raise` statements, so `retrieval/reranker.py` - inside the guard's own scope - could put the model's reply (its answer to a prompt carrying 600 characters of each retrieved passage) into a `ValueError` that `RerankerService.rerank` logs verbatim at WARNING. `services/extraction/image_ocr_runner.py` and `ocrmypdf_runner.py` did the same with up to 500 characters of an OCR tool's stderr/stdout. All three now carry an exit code and a length, and `raise_violations` is the class fix. F-A8T-3: the one test that claimed to cover the exception channel raised a hand-written constant containing no marker, so its assertion was true by construction; it now drives `RerankerService.rerank` with a backend that echoes the passages, and asserts the marker reached the prompt before asserting it reached no log. F-A8T-5: `routers/ai_assistant.py` logged 50 characters of a search query at INFO while `observability/service.py::_redact_query` reduced the identical value to `[redacted len=N]`; it now logs the digest and the length, and the query surface - `ai_assistant.py`, `retrieval_engine.py`, `services/ai_service.py` - is inside the guard's scope rather than outside both it and the declared boundary. **R-A8U closed F-A8T2-7, which the same independent review reported: both matchers were single-expression name checks with no dataflow, so one assignment defeated either** (`detail = f"...{raw[:200]!r}"; raise ValueError(detail)` - `detail` is not a content word). `ContentTaint` is the class fix: a bounded, per-function-scope taint model that follows content through assignment, positional tuple unpacking, `for`/`with`/comprehension targets, f-strings, `%`, `.format`, concatenation, dict fields and exception wrapping, and declassifies only through three written rules - the metadata suffixes, a set of reducing calls (`len`, `sha256(...).hexdigest()`) and boolean prefixes (`use_`, `is_`, `has_`). `raise_violations` now also reads a bare `raise name`. `passage`/`passages` joined CONTENT_WORDS - the guard's own docstring called passage text the leaked content while the word was absent from the set - along with the missing plurals `queries`, `answers`, `prompts`. Running it found one real channel: `retrieval/service.py::_retrieve_contract_evidence` rendered a raw contract-QA sub-query with `%r` at WARNING plus the whole exception beside it, in a module already inside this guard's scope; it now logs a digest, a length and the exception type. Ten indirect leak shapes, six metadata shapes, a static mutation control that re-runs the same module with the taint model empty and requires it to go green (the pre-R-A8U matcher, reproduced), and a runtime control that takes the marker out of the real chunker's output, moves it to a second name and requires the log capture to find it. 73 tests)

### Gate 6: Database, Migrations, And Seeds

- [x] Versioned migration/index plan exists before production promotion.
- [x] Fresh install path is tested against real MongoDB. evidence: docs/GATE_6_FRESH_INSTALL_EVIDENCE.md (R-A8Q Stage B staging run 2026-09-08, mapped requirement-by-requirement in R-A8S: an empty `contraclaim_staging` on the live replica set `rsstg` - read back from `rs.status().set`, not merely configured - took 19 of 19 migrations via `python -m rbac_backend.scripts.migrate_database`, dry run exit 0 with `warnings: []`, second apply 0 newly applied / 19 skipped, permission catalogue 198 == 198 distinct with `uq_permissions_name` unique, and the application served Gate 2 and Gate 3 bullet 1 on that database)
- [x] Existing database upgrade path is tested against a staging copy. evidence: docs/GATE_6_UPGRADE_PATH_EVIDENCE.md (mapped requirement-by-requirement against docs/GATE_6_UPGRADE_PATH_PROCEDURE.md; R-A8W Stage B staging run 2026-09-13, run `R-A8W-STAGEB-20260913T154822Z`, every procedure step executed in order: production archive `contraclaim-20260913-013001` (sha256 equal to its manifest, `gzip -t` clean) restored with every guard armed into an emptied `contraclaim_staging` on replica set `rsstg` - writers stopped, database dropped and proved at 0 collections first; restore exit 0, `STATUS=OK`, `MATCH=YES`, 47,722 documents restored, 0 failed, 0 missing collections, expected collections from the archive prelude; read back 162 collections / 47,722 documents, equal to the Stage A drill figures for that archive, and no `contraclaim` database on `rsstg`. Before any start: permissions 263 == 263 distinct, 0 duplicate groups, ledger 18; `--list` 16 applied / 2 `applied_not_in_catalogue` / 3 pending; `--fail-on-warning` dry run exit 0; apply exactly 3 (`20260906_0001` among them, no warnings, no notices); re-apply 0 applied / 19 skipped; `uq_permissions_name` present and unique, a duplicate insert refused with 11000. Backend on release image `d9eedb2cbee3` then reached `/health/ready` 200 on the restored data; after start permissions 265 == 263 + the 2 release-only names, 0 duplicate groups, 0 pending)
- [x] Permission seeds are versioned and idempotent.
- [x] Backfill/migration entry points support dry-run before mutation.
- [x] Startup index creation is not the only production schema control.

### Gate 7: Deployment And Environment

- [x] `scripts/pre_deploy_readiness.sh` passes. evidence: scripts/pre_deploy_readiness.sh (R-A8M staging run 2026-09-07: 0 failures, 2 warnings, exit 0, with the four runtime values this deployment defines in compose rather than in its env file)
- [x] `scripts/preflight.py` passes. evidence: scripts/preflight.py (R-A8M staging run 2026-09-07: 0 failures, 0 warnings, exit 0, inside the backend container)
- [x] `docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml config` succeeds without unresolved variables. evidence: docker-compose.prod.yml (R-A8M staging run 2026-09-07: the deployed file set renders exit 0, 14 services, no unresolved variable; the base `docker-compose.yml` is deliberately not in the deployed set)
- [x] `/health/live` passes. evidence: backend/rbac_backend/routers/health.py (R-A8M staging run 2026-09-07: 200)
- [x] `/health/ready` passes. evidence: backend/rbac_backend/routers/health.py (R-A8M staging run 2026-09-07: 200 `status: ready`; mongo, contract_queue_redis, runtime_redis, configuration and local_storage all ok)
- [x] `/metrics` is enabled and token-gated. evidence: backend/rbac_backend/routers/health.py (R-A8M staging run 2026-09-07: no token 401, wrong token 401, correct `X-Metrics-Token` 200)
- [x] Worker, queue, Redis, Qdrant, FalkorDB, MongoDB, and storage health are verified. evidence: scripts/post_deploy_verify.sh (R-A8M staging run 2026-09-07: both workers running, and every dependency exercised by the Gate 2 round trips and the Gate 8 restore parity checks)

**The script this bullet names changed in R-A8R, after the run that earned it.**
The tick stands — every dependency the bullet lists was verified by that run, and
nothing was removed. What changed is the edge check: it was conditional on
`PUBLIC_BASE_URL` being set, so an unset value skipped the only control over the
surface users arrive through, and staging (which by design has no public DNS)
could satisfy it only by pointing at production. `scripts/lib/edge_target.sh`
now makes the mode an input and an unset edge a **failure** in both modes
(`docs/OPERATIONS.md` §6.1). The change is strictly stricter, so it cannot
invalidate a run that passed the weaker version — but the script that was run no
longer exists, and **`post_deploy_verify.sh` must be re-executed once** in
whatever window comes next before this evidence line describes a current
artefact.

Required production env groups:

- Runtime: `ENVIRONMENT`, `PUBLIC_BASE_URL`, `PUBLIC_API_BASE_URL`, `FRONTEND_URL`, `API_URL`.
- MongoDB: `DATABASE_URL`, `MONGODB_REPLICA_SET`, `MONGODB_DATABASE`.
- Auth/session: `SECRET_KEY`, `AUTH_COOKIE_SECURE`, `CORS_ORIGINS`, `ALLOW_DEV_HEADERS`, `RBAC_ENTITLEMENT_FAIL_OPEN`.
- Object storage: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET_NAME`, `AWS_REGION`.
- AI/retrieval: `OPENAI_API_KEY`, `OPENAI_MODEL`, `QDRANT_API_KEY`, `QDRANT_URL`, `FALKORDB_PASSWORD`, `FALKORDB_URL`, `FALKORDB_GRAPH_NAME`, `LANGGRAPH_API_TOKEN` when enabled.
- Redis/queue/runtime state: `REDIS_PASSWORD`, `APP_REDIS_URL`, `RUNTIME_STATE_REDIS_URL`, `CONTRACT_QUEUE_REDIS_URL`.
- Email/SMTP: `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, SMTP host/port/security settings, SMTP settings encryption key if enabled.
- Upload safety: `ANTIVIRUS_ENABLED`, `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_FAIL_OPEN`, upload size/concurrency settings.
- Observability: `METRICS_ENABLED`, `METRICS_TOKEN`, log-level settings.
- Billing: `PAYMENT_PROVIDER` and provider-specific keys/webhook secrets if billing is enabled for launch.
- Backup: `BACKUP_ROOT`, `BACKUP_MAX_AGE_HOURS`, `BACKUP_REQUIRED_IN_PRODUCTION`, `BACKUP_REQUIRED_VOLUME_LABELS`, `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`.

### Gate 8: Backup, Restore, And Rollback

- [x] MongoDB logical backup succeeds. evidence: scripts/production_backup.sh (R-A8M staging run 2026-09-07: staging archive written and `sha256sum -c` verified)
- [x] Backend uploads backup succeeds. evidence: scripts/backup_volume.sh (R-A8M staging run 2026-09-07: archive verified, and the restored marker file matched its pre-destruction sha256)
- [x] Qdrant backup or rebuild plan is verified. evidence: scripts/production_restore_volumes.sh (R-A8M staging run 2026-09-07: volume archive verified to contain `raft_state.json`; after destruction and restore the seeded collection returned 3 points, status green)
- [x] FalkorDB backup or Mongo-derived reconciliation/rebuild is verified.
- [x] Redis backup requirement is explicitly accepted or tested. evidence: scripts/production_restore_volumes.sh (R-A8M staging run 2026-09-07: TESTED - volume archive restored and all three seeded keys returned their exact values)
- [x] Full restore drill into staging/isolated environment succeeds. evidence: scripts/mongo_restore.sh (R-A8M staging run 2026-09-07: Mongo, FalkorDB, Qdrant, Redis and the uploads volume destroyed and restored in one exercise; 222 documents restored, 0 failed; permissions 198/198 with 0 duplicates and `uq_permissions_name` intact)
- [x] RPO and RTO are recorded. evidence: docs/OPERATIONS.md (§5 "Recovery objective": the release owner approved Candidate A — Conservative on 2026-09-08 — RPO 24 h, RTO 8 h, restore drill quarterly in staging. **OBJECTIVE RECORDED, not OBJECTIVE FULLY DEMONSTRATED** — neither figure has been validated end to end; staging-scale restore measurements are the accepted evidence basis for this release, and S3 failure-domain separation, production-scale restore timing and detection time remain open.)
- [x] Rollback steps are documented and tested. evidence: docs/SAME_HOST_STAGING_MAINTENANCE_WINDOW.md (R-A8M staging run 2026-09-07: application image rolled back to the retained prior artefact and forward again, health 200 ready at both ends; production tags untouched)

Evidence for the FalkorDB bullet, and only that bullet:
`scripts/falkordb_recovery_drill.sh` seeds a graph on the pinned production
image with production's `REDIS_ARGS` and mount, runs the production backup,
**destroys the container and the volume**, restores with
`scripts/production_restore_volumes.sh`, starts a fresh container, and diffs a
semantic snapshot — graph names, nodes, edges, properties, counts and index
schema all match. Missing, corrupted and stateless-volume archives are each
refused. The drill is repeatable on any host with Docker; re-run it rather than
trusting this checkbox. Adopting the fix on a host that has been running the
previous configuration requires the one-time data move in
`docs/DOCKER_INSTALL_RUNBOOK.md` §15.1.

R-A8M's staging execution closed six of the seven bullets that were open when
that paragraph was written — the uploads, Qdrant and Redis restores, the full
single-exercise restore drill, and deployment rollback in both directions. The
seventh was a decision rather than a measurement, and the owner has now made it.

RPO/RTO targets are the release owner's to set. R-A8M measured what a target has
to be defensible against — daily 01:30 backup cadence with a 26 h freshness
alarm, backup 5 s, off-site sync 9 s, volume restore 3 s, a 222-document Mongo
restore 6 s, image rollback 35 s each way — and R-A8N wrote those measurements
up against candidate policies, with what each would cost and what is still
unmeasured (detection time, production-scale restore timing, off-site
*retrieval* timing), in [RPO_RTO_OWNER_DECISION.md](RPO_RTO_OWNER_DECISION.md).

**On 2026-09-08 the release owner approved Candidate A — Conservative: RPO 24 h,
RTO 8 h, restore drill quarterly in staging**, recorded in
[OPERATIONS.md](OPERATIONS.md) §5. Read the bullet exactly as it is written.
The objective is **RECORDED**; it is **not DEMONSTRATED**. Nothing here says a
recovery has been shown to complete within 8 hours or that data loss has been
shown to stay inside 24 hours. The owner accepted staging-scale restore
measurements as the evidence basis for this release, subject to later
production-scale validation, and three items stay open as production-cutover
debt: separating production document and backup storage into independent S3
failure domains, production-scale restore timing, and recovery-event detection
time. No production S3 architecture change is authorised in this phase.

### Gate 9: Final Production Readiness Review

- [x] Critical blockers closed. evidence: owner decision 2026-09-20 recorded verbatim in `docs/R_A9G_OWNER_DECISION_RECORD.md`. No row of the Current Critical Blocker Register below parses as Open. **P0-002 is FORMALLY DISPOSED as dated accepted debt - owner-approved for this initial cutover with a 2026-10-15 follow-up - and is NOT technically closed:** the combined live AI/vector/graph integration proof has still never been captured, and nothing in this tick asserts otherwise. P0-008 closes on the release owner's sign-off in bullet 6, the fifth and last of its five named required resolutions; the other four - staging deploy, smoke after deploy, smoke after restore drill, and the readiness re-derivation - were measured in R-A8Z and re-derived in R-A9F. P0-003 and P0-007 remain Partially mitigated and were never Open under this rule.
- [x] High severity risks fixed or explicitly accepted. evidence: owner decision 2026-09-20, `docs/R_A9G_OWNER_DECISION_RECORD.md`. The consolidated high-risk list A1-A9 in `docs/R_A9F_OPEN_GATE_MATRIX.md` is fully disposed. A1-A8 are explicitly ACCEPTED as bounded residual risk for this initial cutover only, not permanent waivers: A1 (deployed-browser E2E gaps, b7 prioritised), A2 (degraded/empty/loading states, structural guards and the generated route denominator remain mandatory), A3 (P0-002), A4 (P0-007), A6 (shared S3 bucket, post-release remediation must establish an independent backup failure domain) and A7 (RPO/RTO recorded not demonstrated, quarterly staging restore drills remain mandatory) each carry a **2026-10-15 follow-up deadline**; A5 is the dated third-party image exception (expiry 2026-10-15, remediation 2026-10-06 to 2026-10-15, httpd first, post-cutover owner Release Owner, `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md`); A8 is accepted only while `ARBITRATION_ENGINE_ROLLOUT_MODE=off` and `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false`, and authorises no promotion to primary. **A9 is DISCHARGED, not accepted as debt:** the owner reviewed the 3 production roles / 4 users carried from R-A9B receipt section 16 and decided NO RE-GRANT, because that authority existed only through the permission-alias fan-out defect. Supporting dispositions: `docs/S3_STORAGE_POSTURE_DEBT.md`, `docs/OPERATIONS.md` section 5, and the register below.
- [x] Staging smoke test passes after deploy. evidence: scripts/post_deploy_verify.sh (R-A8Z Stage B, 2026-09-14, run `R-A8Z-STAGEB-20260914T143311Z`, release `566a01a`, fresh `contraclaim-stg` deploy, 19/19 migrations then 0 on re-apply: the release's own `scripts/production_backup.sh` exit 0 on the fresh install with no manual step - every archive VALID under its declared contract, the empty uploads and snapshots volumes under `application-volume`, `backup_status.py` ok (`20`) - then ONE canonical `scripts/post_deploy_verify.sh` run: exit 0, 24 PASS, 1 WARN (`ALLOWED_DOCUMENT_MIMES not visible on the worker container`), 0 FAIL, including the Gate 5 live antivirus check (`21`). F-A8W-B1 is closed on a deployed stack and the R-A8X no-manual-reconstruction rule is met.) **R-A8X disposition (history): NOT EARNED (withdrawn, provenance kept).** R-A8W Stage B ticked it on 2026-09-13 (run `R-A8W-STAGEB-20260913T154822Z`, release `37e79ba`, backend `d9eedb2cbee3`, client `865fd898f818`, fresh `contraclaim-stg` deploy, 19/19 migrations) on a third run of `scripts/post_deploy_verify.sh`: exit 0, 23 PASS, 0 FAIL, 1 WARN. The sequence, all kept in that evidence directory: run 1 failed one check, `/health/operations` 503, because a fresh stack had never been backed up; the release's own `scripts/production_backup.sh` was then run and **aborted** under `set -e` on the empty fresh-install uploads volume (**F-A8W-B1**), so run 2 failed the same check; an operator then replayed that script's remaining per-volume steps by hand into the same stamp (`20d-F-A8W-B1-staging-backup-completion.txt`), and run 3 passed. The release owner's R-A8X rule: a canonical smoke must pass against the deployed release without manually reconstructing a failed release script; a pass that needed one is diagnostic evidence, not certification. The bullet's own words - the smoke passes *after deploy* - describe the deployed release doing it, and here the deployed release could not produce the backup the smoke checks. F-A8W-B1 is fixed offline in R-A8X (`application-volume` backup profile, `test_backup_empty_application_volume.py`); the bullet is re-earned only by a clean canonical run in the next staging window
- [x] Staging smoke test passes after restore drill. evidence: scripts/post_deploy_verify.sh (R-A8Z Stage B, same run and release: this window's quiesced production archive `contraclaim-20260914-202039-RA8Z-quiesced` - sha256 equal to its manifest, `gzip -t` clean - restored with every guard armed into an emptied `contraclaim_staging` on `rsstg` with the writers stopped: 48,032 documents, 0 failed, 162 collections, read-back parity; with the application still stopped 3 migrations applied, re-apply 0, `uq_permissions_name` unique and a duplicate insert refused 11000, permissions 263 -> 265 after start (`24-29`); then the canonical smoke on the 1.4.6 ClamAV service: exit 0, 23 PASS, 2 WARN, 0 FAIL, `CLAMAV_READINESS=OK scope=full` (`31`). WARN 1 `Recent backend logs contain errors` - one `Traceback` line captured, its context not retained before teardown (`31b`); WARN 2 `ALLOWED_DOCUMENT_MIMES`.) **R-A8Y disposition (history): STALE (withdrawn, provenance kept).** R-A8W Stage B ticked it on 2026-09-13 with `scripts/post_deploy_verify.sh` (same run and release as bullet 3, executed after the Gate 6 bullet 3 production-copy restore and migration: exit 0, 22 PASS, 0 FAIL, 2 WARN - a benign trapped bcrypt-version traceback and `ALLOWED_DOCUMENT_MIMES` - permission catalogue 265, superadmin holding all 198 release permissions, `/health/operations` 200 `ok`; `30-gate9b4-smoke-after-restore.txt`). It is stale because R-A8Y changes the ClamAV service the smoke ran against - image (1.4.5 → 1.4.6), network, volume, healthcheck and memory limit - and `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` had pre-registered adopting ClamAV 1.4.6 as staling "every smoke" before this programme began. Supporting, not sufficient on its own: the canonical smoke now also carries the Gate 5 live antivirus check (loaded signature age within 48 h, clean accepted, EICAR rejected); that run's output has no antivirus line at all, and its ClamAV ran the same `service-net`-only definition R-A8Y proved cannot update. Re-earned by a canonical run after the next staging restore drill
- [x] Readiness score target is 85 or higher. evidence: `scripts/production_readiness_score.py` on this release HEAD, measured **before this bullet was ticked**: raw 85.33, displayed 85/100 against a target of 85, verdict Ready. The criterion was therefore already satisfied with bullet 5 unticked, which is exactly the non-circularity condition `backend/rbac_backend/tests/test_gate9_scoreability.py` enforces. Ticked by owner decision 2026-09-20. **Any increase in the figure after this tick is checkbox arithmetic recording an already-satisfied condition. It is not a new staging measurement: no staging run was executed, re-scoped or rewritten to produce it, and the sealed R-A9E staging evidence is unchanged.**
- [x] Release owner signs off. evidence: GATE 9 BULLET 6 - RELEASE OWNER SIGN-OFF, signer **Release Owner**, dated **2026-09-20**, recorded verbatim in `docs/R_A9G_OWNER_DECISION_RECORD.md`. Release `release/contraclaim-rc1`; frozen release HEAD before this owner-decision documentation update `79180d55d8d0635780405d662871bec1a69641bc`, tree `3487cde0aacb669924e6528bb4c3d63f7336edca`; runtime code certified at candidate `fe728b205ed5dfb20d88040bc44214ca71d11c43`; final R-A9F CI run `35518788548`, 5/5 green on that HEAD. The owner accepts A1-A8 as the bounded residual-risk profile of this initial release, records A9 discharged with no re-grant, accepts the dated third-party image exception expiring 2026-10-15 and takes responsibility as its post-cutover owner, and approves FalkorDB Variant A, G32 excluded from this cutover, the frozen role-alignment operation, the production migration sequence, the ClamAV overlay retirement procedure, and the backup and rollback contracts. **This sign-off is what closes P0-008.**

**Gate 9 scoreability - what the preamble's blocking sentence does and does not
say.** "Production promotion is blocked until all gates are checked", under
`## Launch Gates` above, is a condition on **promotion**. It is not a condition
on scoring. `scripts/production_readiness_score.py` implements no entry
conditions, no gate ordering and no cross-gate blocking: it counts checked
bullets per gate section and weights them, and nothing else. So a Gate 9 bullet
that has its own executable evidence earns its points when that evidence exists,
whether or not the other gates are complete. Bullets 3 and 4 are staging
measurements of exactly that kind. Gate 9 is the last gate to be *satisfied*; it
is not a gate that has to be *entered*.

**Bullet 5 is the one exception, because it is self-referential.** It asserts
the readiness figure itself, so counting it towards reaching the threshold it
asserts is circular. Reproduced mechanically in release programme R-A8T: from a
raw 84.000 - which is where the shortest projected route lands - ticking bullet
5 and nothing else produces raw 85.333 and flips the verdict to Ready. Bullet 5
may therefore be ticked only once the figure computed **with bullet 5 unticked**
already meets the target. `backend/rbac_backend/tests/test_gate9_scoreability.py`
enforces that, and reproduces the circular tick against a synthetic document so
the guard has a negative control.

**Bullet 5 is not removed.** Deleting it would shrink Gate 9's denominator from
6 to 5 and raise every other Gate 9 bullet from 1.333 to 1.600 points - a
denominator change that pays points, which is the one thing this programme
refuses to do.

**Bullet 1 inherits the same constraint through the blocker register.** P0-008's
required resolution names the readiness rerun and the owner sign-off, so bullet
1 must not be ticked while any row in the Current Critical Blocker Register is
still Open. That is enforced rather than described.

**Owner decision, 2026-09-20 - how bullets 1, 2, 5 and 6 came to be ticked, and
what that does and does not mean.** All four were ticked in one owner act, whose
verbatim record is `docs/R_A9G_OWNER_DECISION_RECORD.md`. Read in the order the
constraints require:

1. **Bullet 2** was ticked first. Its own words are "fixed **or explicitly
   accepted**", and every high-severity item had a written disposition with an
   owner and, where applicable, a date and an expiry. The owner accepted A1-A8
   and discharged A9. Nothing was fixed to earn it; the acceptance is the
   evidence, and it is bounded to this initial cutover.
2. **Bullet 1** required no Critical Blocker Register row to parse as Open. That
   was reached by **formal disposition, not by closure**: P0-002 moved from Open
   to dated accepted debt carrying A3, and P0-008 closed on the bullet-6
   signature. The register rows below say so in their own cells, so a reader who
   never sees this note cannot mistake disposal for repair.
3. **Bullet 5** was tickable because the figure computed **with bullet 5
   unticked** already met the target - raw 85.33, displayed 85/100 - which is the
   non-circularity rule above. The tick records that measurement; it does not
   create one.
4. **Bullet 6** is the promotion decision itself and was signed by the release
   owner. It cannot be self-signed and was not.

**What ticking these four did to the figure.** Gate 9 moved from 2/6 to 6/6, so
the weighted total moved by +5.33 on four checkboxes and **no new measurement**.
The sealed R-A9E staging evidence measured the 85.33 that bullet 5 asserts, and
that evidence is unchanged, unedited and unrewritten. Any later reader comparing
this document's figure with the R-A9E evidence should expect them to differ by
exactly those four ticks, and by nothing else. If a future phase needs the
measured-at-R-A9E figure, it is 85 / raw 85.33.

Current score evidence:

- Current production launch-readiness score is **91/100** (raw 90.67; re-derived by
  `scripts/production_readiness_score.py`, release programme R-A9G-0, 2026-09-20, after
  the release owner ticked Gate 9 bullets 1, 2, 5 and 6 in one decision act. That move is
  **four checkboxes and no new measurement**: the +5.33 is Gate 9 going 2/6 to 6/6.
  R-A9F had recorded 85 (raw 85.33) on 2026-09-20 with Gate 9 at 2/6, and that is the
  figure the sealed R-A9E staging evidence measured; compare that evidence against 85,
  never against 91. R-A9F reached it after
  the R-A9E Stage B staging execution on candidate `fe728b2` earned Gate 3 bullet 2 and
  Gate 4 bullet 8. R-A8Z had recorded 82 (raw 82.50) on 2026-09-14, after
  its Stage B staging execution earned Gate 5 bullet 6 and Gate 9 bullets 3 and 4,
  and left Gate 3 bullet 2 and Gate 4 bullet 8 open on F-A8Z-B1. R-A8Y had recorded
  78 (raw 78.17), re-derived by
  `scripts/production_readiness_score.py`, release programme R-A8Y, 2026-09-14,
  after Gate 5 gained an unticked sixth bullet for live antivirus effectiveness and
  Gate 9 bullet 4 was withdrawn as stale. R-A8X had recorded 81 (raw 81.17)
  after Gate 9 bullet 3 was withdrawn on the owner's no-manual-reconstruction rule.
  R-A8W Stage B had recorded 82 with that bullet ticked, after Gate 6 bullet 3 and
  Gate 9 bullet 4 were earned against staging. It
  was 78 from R-A8T on 2026-09-09, after the first green Actions run closed Gate 1
  bullet 6 and Gate 4 bullet 10).
  The figure recorded here through Phase 8 was 32/100 and had not been re-derived
  since.
- Current verdict: **Ready** (the scorer's term for the checkbox state; not a promotion decision).
- Score target: **85/100**.
- Scoring script: `python scripts/production_readiness_score.py`.
- Final audit record: `docs/PRODUCTION_READINESS_FINAL_AUDIT.md`.

## Current Critical Blocker Register

| ID | Status | Blocker | Evidence | Required resolution |
| --- | --- | --- | --- | --- |
| P0-001 | Resolved | Backend suite is red | Original full backend run observed 6 failures in `test_llamaindex_service.py`; Phase 1 local rerun now reports 462 passed, 7 skipped | Confirmed in GitHub Actions - run 34376328409, `backend-checks` green on `60f196c` (R-A8T, 2026-09-09) |
| P0-002 | Accepted debt - Owner approved for initial cutover | Live AI/vector/graph integrations are skipped by default | `RUN_EXTERNAL_INTEGRATION_TESTS=1` required for live tests. **The underlying technical evidence is unchanged: the combined live staging proof with OCR, ClamAV, OpenAI, Qdrant, FalkorDB and Redis all enabled has never been captured.** This row is FORMALLY DISPOSED, not resolved | Unchanged: add a staging live-integration release gate and capture results. **Owner decision 2026-09-20 (`docs/R_A9G_OWNER_DECISION_RECORD.md`, item A3): accepted as dated debt for this initial cutover only, follow-up deadline 2026-10-15.** On that date the acceptance lapses and this row is a live blocker again unless a new dated owner decision is recorded |
| P0-003 | Partially mitigated | Browser E2E incomplete | Phase 5 adds Playwright coverage for contract upload/search/Q&A/appraisal, but auth/session, document workflows, timeline, chronology, arbitration, and broad empty/error/mobile coverage remain uncovered | Expand E2E harness to all launch-critical workflows |
| P0-004 | Resolved locally | Migration discipline incomplete | Phase 3 adds `rbac_backend.migrations`, `schema_migrations` ledger, RBAC seed digesting, and `python -m rbac_backend.scripts.migrate_database` dry-run/apply support | Run against staging/fresh MongoDB and confirm in CI/release evidence |
| P0-005 | Resolved (code); staging proof captured and production clamav hardened (R-A8Z) | Upload antivirus can be disabled - and, found in R-A8Y, can be enabled while ineffective | Production startup now refuses to boot unless antivirus is enabled and fail-closed (`ANTIVIRUS_REQUIRED_IN_PRODUCTION` default true); `.env.example` sets `ANTIVIRUS_ENABLED=true`; upload routes reject not-clean files. Tests: `test_config_validation.py` (3 new), `test_antivirus_service.py`. R-A8Y: production clamd served a 70-day-old signature database behind every green check (no egress on the internal `service-net`, no persistent database volume); release fix and freshness gate in `docs/CLAMAV_SIGNATURE_FRESHNESS.md` | Deployed-stack `post_deploy_verify.sh` run showing clamd reachable, loaded signatures within the maximum age, clean accepted, EICAR rejected (Gate 5 bullet 6) - staging first, then production at cutover |
| P0-006 | Mitigated (regression coverage added) | Production Org-Admin permission flow not yet validated | Service round trip covered by `test_role_permission_catalog_drift.py`; HTTP-boundary retrieve now covered by `test_org_admin_permissions_api.py`, reproducing the catalog-missing Client DMS permission failure through `GET /api/roles/{id}/permissions` | Manual save/retrieve validation in staging/prod remains for the Gate 4 box. R-A9A (offline, earns nothing): the staging spec's terms locator (F-A8Z-B1) and row 12's control (F-A8Z-B2, a harness seed defect) are repaired, 12 tests, 14-row mapping rebuilt with positive controls (`docs/GATE_4_B8_ORG_ADMIN_VALIDATION_MAPPING.md`); two intra-tenant authorization defects recorded unfixed for an owner decision - F-A9A-1 (a soft-deleted role still grants its permissions) and F-A9A-2 (a shared legacy alias lets e.g. `dms.task.manage` satisfy `dms.project.manage`). R-A9B (offline, earns nothing): both fixed in the permission resolver - an inactive role contributes nothing and its holders' cached grants are dropped; alias resolution is one hop and never passes through a shared alias. F-A9B-1 is fixed by the same change: the default org admin passed `billing.plan.manage` with no scope and `subscription.entitlement.manage` via `dms.admin` -> `system:admin`. F-A9B-2 (`dms.admin` holders pass the global legal-words `system:admin` gate) is recorded for an owner decision. The Gate 3 b2 / Gate 4 b8 staging run must execute on the R-A9B HEAD. **R-A9E Stage B (2026-09-20, candidate `fe728b2`): executed on that candidate - the org-admin spec 12/12 on the first attempt with one worker and no retries, and all 14 Gate 4 b8 mapping rows PASS with same-run positive controls and database/audit read-back. Gate 3 bullet 2 and Gate 4 bullet 8 are ticked on that evidence. On the deployed stack a soft-deleted role stopped granting (F-A9A-1 closed there) and `dms.task.manage` alone no longer satisfied a project update while `dms.project.manage` did, both with schema-valid bodies that reached authorization - so F-A9A-2 is now measured on a deployment as well as offline. F-A9B-2 was closed in code in R-A9D (`system:admin` is nobody's alias). F-A9D-1, the production-only `billing.plan.manage` grants, is closed by the owner-approved removal in the cutover role alignment, which has not yet run against production.** |
| P0-007 | Partially mitigated | Python dependency scan is red | `requests` bumped to 2.32.4 (CVE-2024-47081). Remaining ~60 advisories require a coordinated FastAPI/Starlette + LangChain/LangGraph/Pydantic-AI upgrade and a full backend regression run; `ecdsa` Minerva (CVE-2024-23342) is upstream won't-fix and unused in our HS256 path | Execute the framework/AI-stack upgrade, rerun `pip-audit` to green (or document accepted won't-fix), rerun backend tests and Docker build |
| P0-008 | Resolved | Final staging deploy, smoke, backup/restore, and release sign-off evidence are missing | **Closed 2026-09-20 by the release owner's Gate 9 bullet 6 sign-off, the fifth and last of the five required resolutions in the right-hand column.** Gate 9 is now 6/6. Historical state, kept: Gate 9 was 2/6: R-A8Z Stage B re-earned the smoke after deploy (the release's own backup script and the canonical smoke, no manual step, F-A8W-B1 closed on a deployed stack) and the smoke after the restore drill (with the live antivirus check); the readiness score now reads 85/100 against its 85 target, so Gate 9 bullet 5 is satisfiable in substance but is deliberately left unticked until the owner ticks it with bullet 6 (see `docs/R_A9F_OPEN_GATE_MATRIX.md`); the owner sign-off and the bullet 1/2 dispositions were outstanding. All five are now recorded: the staging deploy, the smoke after deploy and the smoke after the restore drill were measured in R-A8Z Stage B; the readiness re-derivation ran in R-A9F; and the release owner signed off on 2026-09-20 | Complete staging deploy, smoke after deploy, smoke after restore, readiness-score rerun, and release owner sign-off - **all five recorded** |

## Current Readiness Score

The current production launch-readiness score is **91/100** against a target of
**85/100**. It was **85/100** (raw 85.33) when the R-A9E staging evidence measured it;
the difference is the four Gate 9 checkboxes the owner ticked on 2026-09-20 and nothing
else. The score is generated from checked launch-gate evidence, not from
implementation intent or local-only assumptions.

Run:

```bash
python scripts/production_readiness_score.py
```

The table below is the script's own output, re-derived on 2026-09-06. It previously
read 40/100 with Gate 1 at 5/6; the script has reported 4/6 for Gate 1 since a box
was unchecked, and the stale copy was granting three points nothing had earned.
`test_release_gate_specification.py` now fails if the two disagree again.

It read 37/100 until R-A8J, when Gate 3 bullet 4 was unchecked. That bullet had
been ticked with no `evidence:` reference, and the only spec covering its subject
mocks `**/api/**` - which Gate 3's own preamble says earns no checkbox. The score
is one point lower because a point that had never been earned was being counted,
not because anything regressed.

Current gate score summary, re-derived mechanically from
`scripts/production_readiness_score.py` on 2026-09-20 (R-A9G-0, after the release owner ticked Gate 9 bullets 1, 2, 5 and 6; before that R-A9F, after the R-A9E Stage B staging execution on candidate `fe728b2` earned Gate 3 bullet 2 and Gate 4 bullet 8, which is the run that measured Gate 9 at 2/6 and the total at 85; before that R-A8Z on 2026-09-14, after its Stage B earned Gate 5 bullet 6 and Gate 9 bullets 3 and 4). This table is a rendering
of the checkboxes above and nothing else; when the two disagree, the checkboxes
and their evidence lines are the record. (The previous copy of this table
predated the R-A8M staging execution and still showed Gate 2 at 0/6, Gate 7 at
0/7 and Gate 8 at 1/8 while the bullets above were ticked with evidence — a
stale rendering, not withdrawn evidence.)

| Gate | Score | Checked |
| --- | ---: | ---: |
| Gate 1: CI And Local Test Baseline | 15.00 / 15 | 6 / 6 |
| Gate 2: Live Integration Baseline | 10.00 / 10 | 6 / 6 |
| Gate 3: Browser E2E Coverage | 2.67 / 12 | 2 / 9 |
| Gate 4: Security And RBAC | 15.00 / 15 | 10 / 10 |
| Gate 5: Upload And Content Safety | 10.00 / 10 | 6 / 6 |
| Gate 6: Database, Migrations, And Seeds | 10.00 / 10 | 6 / 6 |
| Gate 7: Deployment And Environment | 10.00 / 10 | 7 / 7 |
| Gate 8: Backup, Restore, And Rollback | 10.00 / 10 | 8 / 8 |
| Gate 9: Final Production Readiness Review | 8.00 / 8 | 6 / 6 |

Total: **91 / 100** against a target of 85.

## Pending Blockers By Phase

| Phase | Pending blockers |
| --- | --- |
| Phase 0 | None currently recorded. |
| Phase 1 | GitHub Actions remote run; Python dependency audit; Docker image scans. |
| Phase 2 | Secret scan/gitleaks; production Org-Admin Client DMS permission save/retrieve validation. |
| Phase 3 | Fresh Mongo migration dry-run/apply; staging-copy migration dry-run/apply; Bash syntax validation on a host with Bash. |
| Phase 4 | Live staging proof with OCR, ClamAV, OpenAI, Qdrant, FalkorDB, Redis, and storage enabled. |
| Phase 5 | Browser E2E for auth/session/CSRF, Org-Admin permissions, document workflows, timeline, chronology, arbitration, and broad route states; live staging browser run. |
| Phase 6 | Live LLM-backed arbitration drafting proof; arbitration browser E2E for create/import/generate/regenerate/export/approval. |
| Phase 7 | Bash syntax validation; staging backup execution, offsite S3 sync, `/health/operations` scrape, restore drill, RPO/RTO, and rollback proof. |
| Phase 8 | R-A9G maintenance window grant, which is an owner act and is not yet given. |

## Evidence Capture Template

Use this format for each release-gate run.

```text
Date:
Branch/commit:
Environment:
Command/check:
Result:
Evidence link or log path:
Owner:
Follow-up issue:
```

## Production Update Evidence - 2026-07-09

Date: 2026-07-09, 17:28-17:44 IST.
Environment: production Ubuntu server reached through SSH alias `contraclaim`.
Source repo: `ManishPandey21/contraclaim-dms`, branch `main`.
Deployed source commit: `8a2069878d08e5b31eaab1a5775648cc76c8a81b`.
Deployment working directory: `/opt/contraclaim-dms/projectDMS` because the GitHub repository root contains the app under `projectDMS/`.

Release preparation:

- GitHub CLI on the server was authenticated as `ManishPandey21`; `git ls-remote` resolved `main` to `8a2069878d08e5b31eaab1a5775648cc76c8a81b`.
- Pre-update production backup completed with stamp `20260709-172824`.
- Backup artifacts:
  - Mongo archive: `/var/backups/contractdms/mongo/contraclaim-20260709-172824.archive.gz`.
  - Manifest: `/var/backups/contractdms/manifests/backup-20260709-172824.json`.
  - Checksums: `/var/backups/contractdms/manifests/checksums-20260709-172824.sha256`.
  - Volume archives under `/var/backups/contractdms/volumes/` for backend uploads, Qdrant data, Qdrant snapshots, FalkorDB data, and Redis data.
- Previous deployment directory preserved at `/opt/contraclaim-dms.preupdate-20260709-172824`.
- Previous deployment archive preserved at `/var/backups/contractdms/release-updates/20260709-172824/deploy-dir-preupdate-20260709-172824.tar.gz`.

Redeploy result:

- Production runtime files were copied into `/opt/contraclaim-dms/projectDMS`: `.env`, `backend/.env`, `client/.env.production`, and `config/secrets/`.
- PDF viewer CSP fix applied and verified in `config/httpd.conf`: `frame-src 'self' https: blob: data:;`.
- Docker build command from `/opt/contraclaim-dms/projectDMS` completed successfully:
  `docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml build --pull`.
- Migration dry-run and apply completed successfully with all listed migrations skipped as already applied.
- Redeploy command completed successfully:
  `docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml up -d --build --remove-orphans`.
- Final service state: backend, client, gateway, ClamAV, MongoDB replica set members, Redis, Qdrant, and FalkorDB healthy; contract worker running.

Host Nginx evidence:

- Active enabled hosts proxy to the Docker gateway:
  - `/etc/nginx/sites-enabled/web.contraclaim.com: proxy_pass http://127.0.0.1:8080;`
  - `/etc/nginx/sites-enabled/api.contraclaim.com: proxy_pass http://127.0.0.1:8080;`
- `sudo nginx -t` result: syntax OK and test successful.
- Note: `/etc/nginx/sites-available/contraclaim` still contains an older `127.0.0.1:8000` proxy line, but the active enabled production hosts above point at `8080`.

Gateway and PDF/CSP evidence:

- `https://web.contraclaim.com/health` returned `HTTP/1.1 200 OK`.
- `http://127.0.0.1:8080/health` returned `HTTP/1.1 200 OK` with body `ok`.
- Live public `Content-Security-Policy` included `frame-src 'self' https: blob: data:;`, which is the required allowance for browser-native PDF iframe/blob/data rendering in the document viewer.

Restore drill:

- Result: pass.
- Restore archive: `/var/backups/contractdms/mongo/contraclaim-20260709-172824.archive.gz`.
- Drill log directory: `/var/backups/contractdms/restore-drills/20260709-174048`.
- Drill restored into a disposable MongoDB container and removed the temporary container, volume, and internal network afterward.
- Restored collection counts:
  - `users`: 8
  - `roles`: 12
  - `permissions`: 234
  - `organizations`: 6
  - `projects`: 5
  - `documents`: 1
  - `contract_files`: 0

Post-deploy verification:

- Command run: `scripts/post_deploy_verify.sh` through a Docker-aware wrapper that set `BACKEND_BASE_URL` to the backend container IP and `PUBLIC_BASE_URL=https://web.contraclaim.com`.
- Result: `0` failures, `1` warning.
- Passing checks included Docker service listing, backend `/health/live`, backend `/health/ready`, observability health, operations health, backup freshness, metrics endpoint, public gateway health, Redis ping, and recent backend log scan.
- Warning: host `mongosh` was intentionally unavailable to the script wrapper because the production `DATABASE_URL` uses Docker service DNS; MongoDB readiness was verified through backend `/health/ready`, which reported `mongo: ok`.

Manual browser note:

- Application-level login and opening a specific protected document viewer route were not manually completed in this session because no application credentials were provided. The deployed CSP/header state that caused the browser PDF block was verified live at the public gateway.

## Future Production Update Method

Use this procedure for future GitHub-to-production updates.

1. Confirm GitHub and current source state.

```bash
ssh contraclaim
gh auth status
cd /opt/contraclaim-dms
git fetch origin main
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

2. Create a pre-update backup before pulling code. In this Docker layout, run Mongo backup from inside `mongo1` so Docker service DNS is not required on the host.

```bash
cd /opt/contraclaim-dms/projectDMS
set -a
source .env
set +a
stamp="$(date '+%Y%m%d-%H%M%S')"
backup_root="/var/backups/contractdms"
mkdir -p "$backup_root/mongo" "$backup_root/volumes" "$backup_root/manifests"
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml ps >"$backup_root/manifests/deployment-$stamp.txt"
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T mongo1 mongodump --archive --gzip --db "${MONGODB_DATABASE:-contraclaim}" >"$backup_root/mongo/${MONGODB_DATABASE:-contraclaim}-$stamp.archive.gz"
test -s "$backup_root/mongo/${MONGODB_DATABASE:-contraclaim}-$stamp.archive.gz"
gzip -t "$backup_root/mongo/${MONGODB_DATABASE:-contraclaim}-$stamp.archive.gz"
```

Then archive the required Docker volumes using a read-only BusyBox container: `${COMPOSE_PROJECT_NAME:-contraclaim}_backend_uploads`, `${COMPOSE_PROJECT_NAME:-contraclaim}_qdrant_data`, `${COMPOSE_PROJECT_NAME:-contraclaim}_qdrant_snapshots`, `${COMPOSE_PROJECT_NAME:-contraclaim}_falkordb_data`, and `${COMPOSE_PROJECT_NAME:-contraclaim}_redis_data`. Write checksums and a `backup-$stamp.json` manifest under `$backup_root/manifests/`.

3. Preserve a rollback copy of the current deployment directory.

```bash
cd /opt
mkdir -p "/var/backups/contractdms/release-updates/$stamp"
tar -czf "/var/backups/contractdms/release-updates/$stamp/deploy-dir-preupdate-$stamp.tar.gz" contraclaim-dms
```

4. Pull the GitHub update.

```bash
cd /opt/contraclaim-dms
git pull --ff-only origin main
cd /opt/contraclaim-dms/projectDMS
```

5. Confirm runtime files are present and not tracked.

```bash
ls -la .env backend/.env client/.env.production config/secrets
git status --short -- .env backend/.env client/.env.production config/secrets
```

6. Confirm the PDF viewer CSP and host gateway line before rebuilding.

```bash
grep -n "frame-src 'self' https: blob: data:" config/httpd.conf
sudo grep -R "proxy_pass http://127.0.0.1:8080" -n /etc/nginx/sites-enabled
sudo nginx -t
```

7. Build, migrate, and redeploy.

```bash
cd /opt/contraclaim-dms/projectDMS
RELEASE_SHA="$(git rev-parse HEAD)" docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml build --pull
# A FULL release: certify the scanned images and write the release manifest
# before anything restarts (deployment guide, 6a-6b).
RELEASE="$(git rev-parse HEAD)"; MANIFESTS=/opt/contraclaim-dms/release-manifests; mkdir -p "$MANIFESTS"
PROJECT="$(docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
for svc in backend contract-worker document-worker client; do
  trivy image --format json --severity HIGH,CRITICAL --ignore-unfixed \
    --output "$MANIFESTS/scan-$svc-$RELEASE.json" "$PROJECT-$svc:latest"
done
python3 scripts/release_manifest.py certify --out "$MANIFESTS/certified-$RELEASE.json" --image backend="$PROJECT-backend:latest" --image contract-worker="$PROJECT-contract-worker:latest" --image document-worker="$PROJECT-document-worker:latest" --image client="$PROJECT-client:latest" --scan backend="$MANIFESTS/scan-backend-$RELEASE.json" --scan contract-worker="$MANIFESTS/scan-contract-worker-$RELEASE.json" --scan document-worker="$MANIFESTS/scan-document-worker-$RELEASE.json" --scan client="$MANIFESTS/scan-client-$RELEASE.json"
python3 scripts/release_manifest.py target --scope FULL --out "$MANIFESTS/$RELEASE.json" --certified "$MANIFESTS/certified-$RELEASE.json" --image backend="$PROJECT-backend:latest" --image contract-worker="$PROJECT-contract-worker:latest" --image document-worker="$PROJECT-document-worker:latest" --image client="$PROJECT-client:latest"
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml run --rm backend python -m rbac_backend.scripts.migrate_database --fail-on-warning
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml run --rm backend python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml up -d --no-build --remove-orphans
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml ps
```

8. Verify gateway and CSP.

```bash
curl -ksSI https://web.contraclaim.com/health
curl -sS -i http://127.0.0.1:8080/health
curl -ksSI https://web.contraclaim.com/ | grep -i "content-security-policy"
```

9. Run an isolated restore drill against the new backup. Restore into a temporary MongoDB container or staging environment only; do not restore into production.

10. Run `scripts/post_deploy_verify.sh` with Docker-aware settings.

```bash
cd /opt/contraclaim-dms/projectDMS
# a new shell after the restore drill: the release and its manifests again
RELEASE="$(git rev-parse HEAD)"; MANIFESTS=/opt/contraclaim-dms/release-manifests
backend_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' contraclaim-backend-1 | sed -n '1p')"
COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
BACKEND_BASE_URL="http://$backend_ip:8000" \
PUBLIC_BASE_URL="https://web.contraclaim.com" \
REQUIRE_FRESH_BACKUP=true \
DEPLOY_SCOPE=FULL RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" \
bash scripts/post_deploy_verify.sh \
  && python3 scripts/release_manifest.py promote --manifest "$MANIFESTS/$RELEASE.json" --current "$MANIFESTS/current.json"
```

If host `mongosh` is installed but cannot resolve Docker service names from `DATABASE_URL`, use a constrained PATH for the verification run and rely on `/health/ready` for MongoDB readiness, as recorded in the 2026-07-09 evidence above.

11. Record the release evidence in this file: commit SHA, backup paths, image/build result, migration result, service health, Nginx line, CSP header, restore drill result, post-deploy verification result, warnings, and rollback path.

## Phase 0 Acceptance Status

- [x] Production-readiness branch created.
- [x] Feature freeze/release-gate policy documented.
- [x] Launch gates defined.
- [x] Required production env groups confirmed from `docker-compose.prod.yml` and `.env.example`.
- [x] Current failing backend test baseline documented.
- [x] Current skipped live integration baseline documented.
- [x] One tracked readiness checklist exists: this file.

## Phase 1 Acceptance Status

- [x] Backend unit/API suite is green locally.
- [x] Backend LlamaIndex tests no longer depend on real OpenAI/Mongo vector-store implementations.
- [x] Frontend lint is green locally.
- [x] Frontend Vitest is green locally and no longer hangs on `LetterDraftPage.basic-render.test.tsx`.
- [x] Frontend production build is green locally.
- [x] Frontend dependency audit is green locally.
- [x] `git diff --check` passes.
- [ ] GitHub Actions remote run has not yet been observed.
- [ ] Python dependency audit is still red and tracked as P0-007.
- [ ] Docker image scans have not yet been observed locally.

## Phase 2 Acceptance Status

- [x] Entitlement checks now fail closed by default: `RBAC_ENTITLEMENT_FAIL_OPEN` default changed to `false`.
- [x] All canonical Client DMS permissions are entitlement-scoped via `CLIENT_DMS_PERMISSIONS`, including timeline, chronology, arbitration, claims, registers, evidence graph, and contract appraisal permissions.
- [x] Archive/read-only subscriptions deny newer DMS write/admin permissions such as evidence-graph verification while still allowing read/export style permissions.
- [x] Root production `.env.example` and CI env declare `RBAC_ENTITLEMENT_FAIL_OPEN=false`.
- [x] Production `.env.example` declares `CLAMAV_FAIL_OPEN=false`.
- [x] Backend local `.env.example` is explicitly marked `ENVIRONMENT=development`, with dev headers and entitlement fail-open disabled.
- [x] Local backend security/RBAC validation passed: `python -m pytest backend/rbac_backend/tests/test_config_validation.py -q`.
- [x] Local RBAC hardening validation passed: `python -m pytest backend/rbac_backend/tests/test_rbac_policy_hardening.py -q`.
- [x] Local tenant and permission drift validation passed: `python -m pytest backend/rbac_backend/tests/test_permission_catalog.py backend/rbac_backend/tests/test_role_permission_catalog_drift.py backend/rbac_backend/tests/test_tenant_isolation.py -q`.
- [x] Full local backend suite passed after the fail-closed change: 470 passed, 7 skipped, 238 warnings.
- [x] Frontend route-permission parity passed: `npx vitest run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] `git ls-files` confirms local `.env` files are not tracked.
- [x] `git diff --check` passes.
- [ ] Local gitleaks secret scan not run because `gitleaks` is not installed in the active environment; CI secret-scan job remains the release proof.
- [ ] Production Org-Admin permission save/retrieve flow still requires staging/prod manual validation.

## Phase 3 Acceptance Status

- [x] Added versioned MongoDB migration package: `backend/rbac_backend/migrations`.
- [x] Added applied-migration ledger support through the `schema_migrations` collection.
- [x] Added migration CLI: `python -m rbac_backend.scripts.migrate_database`.
- [x] Migration CLI supports `--list`, dry-run by default, `--apply`, `--target`, and `--fail-on-warning`.
- [x] Added startup-index baseline migration that runs `rbac_backend.core.database.ensure_indexes` as a controlled production schema step.
- [x] Added deterministic RBAC seed catalog metadata in `backend/rbac_backend/initial_data/seed_catalog.py`.
- [x] Added RBAC seed catalog digest persistence through `seed_catalog_versions`.
- [x] Added explicit `roles:assign` initial permission seed because `projectadmin` references it.
- [x] `scripts/pre_deploy_readiness.sh` now checks that the migration runner exists and can enforce a migration dry-run with `RUN_MIGRATION_DRY_RUN=true REQUIRE_MIGRATION_DRY_RUN=true`.
- [x] Operations runbook documents migration dry-run/apply commands and the deploy dry-run gate.
- [x] Focused local validation passed: `python -m pytest backend/rbac_backend/tests/test_migration_runner.py backend/rbac_backend/tests/test_permission_catalog.py backend/rbac_backend/tests/test_startup_indexes.py -q` -> 12 passed.
- [x] Existing seed regression validation passed: `python -m pytest backend/rbac_backend/tests/test_data_initialization.py -q` -> 2 passed.
- [x] Python compile check passed for the migration and seed catalog modules.
- [x] Migration CLI import/help validation passed from `backend`: `python -m rbac_backend.scripts.migrate_database --help`.
- [ ] Migration dry-run/apply against a fresh MongoDB database remains required for release evidence.
- [ ] Migration dry-run/apply against a staging copy of an existing database remains required for release evidence.
- [ ] Bash syntax validation for shell scripts could not run locally on this Windows host unless WSL or another Bash runtime is available.

## Phase 4 Acceptance Status

- [x] Contract upload-session, multipart upload, chunk upload, upload status, contract list, and contract search routes now run central `PolicyService` authorization checks for the resolved organization/project scope.
- [x] Upload denial tests prove multipart and chunk upload authorization happens before reading or rewinding uploaded bytes.
- [x] Status/list/search denial tests prove scoped contract metadata is not returned and service queries are not executed after policy denial.
- [x] Numbered contract headings such as `1 General Conditions`, `1.1 Notices`, and `1.1.1 Method of Service` now preserve clause number, title, level, and parent hierarchy during extraction.
- [x] Contract ingestion payload tests cover searchable/grounded clause metadata: organization/project, document/upload id, clause id/number/title/type/level/parent, TOC path, page numbers, tags, checksum, and enriched text.
- [x] Contract Q&A regression covers Mongo fallback retrieval, full-clause expansion across split chunks, stable clause citation rewriting, and merged page citations.
- [x] Existing graph-aware contract search tests pass for Falkor-derived graph candidate assembly and clause-seed extraction.
- [x] Existing contract appraisal tests pass, including citation-backed generation, completeness checks, lifecycle, versioning, scope isolation, approve/reject/regenerate, export, and register creation.
- [x] Focused local Phase 4 validation passed: `python -m pytest backend/rbac_backend/tests/test_contract_clause_extraction.py -q`.
- [x] Focused local Phase 4 validation passed: `python -m pytest backend/rbac_backend/tests/test_contract_upload_security.py backend/rbac_backend/tests/test_contract_graph_retrieval.py backend/rbac_backend/tests/test_contract_appraisal.py backend/rbac_backend/tests/test_retrieval_engine_basics.py backend/rbac_backend/tests/test_retrieval_engine_scope_isolation.py -q`.
- [x] Full local backend suite passed after Phase 4 changes: 480 passed, 7 skipped, 238 warnings.
- [x] Backend compile check passed: `python -m compileall -q backend/rbac_backend`.
- [x] Frontend route-permission parity passed: `npx vitest run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] Browser E2E for contract upload/progress, search, Q&A, and appraisal is now covered with a mocked backend; live ingestion/extraction proof remains tracked under Gates 2, 5, and 7.
- [ ] Live staging proof with OCR/ClamAV/OpenAI/Qdrant/FalkorDB/Redis enabled remains missing and is still tracked under Gates 2, 5, and 7.

## Phase 5 Acceptance Status

- [x] Added Playwright E2E runner and scripts: `npm run test:e2e` and `npm run test:e2e:ui`.
- [x] Added local Playwright config with Vite web-server startup, Chromium project, traces/screenshots/videos retained on failure, and CI-safe retries.
- [x] Added mocked browser E2E suite for contract upload/progress, search success/empty/error, contract Q&A validation/cited answer, contract appraisal generation/report opening, and mobile search usability.
- [x] Added stable `data-testid` hooks to the critical contract upload, search, Q&A, and appraisal controls without changing user-facing copy or layout.
- [x] CI frontend job now installs Chromium with `npx playwright install --with-deps chromium` and runs `npm run test:e2e`.
- [x] Playwright artifacts are ignored via `.gitignore` and `client/.gitignore`.
- [x] Local Playwright validation passed: `npm run test:e2e -- --project=chromium` -> 5 passed.
- [x] Local targeted ESLint passed for the new E2E spec, Playwright config, and edited contract pages.
- [x] Full frontend lint passed: `npm run lint`.
- [x] Full frontend Vitest suite passed: 58 passed, 3 skipped.
- [x] Frontend build passed: `npm run build`.
- [x] Frontend dependency audit passed after adding Playwright: `npm audit --audit-level=high` found 0 vulnerabilities.
- [ ] Remaining browser E2E coverage is still required for login/logout/session refresh/CSRF, Org-Admin permissions, document upload/view/download/share/denial, timeline verify/reject, chronology, arbitration drafting, and broad route empty/loading/error states.
- [ ] Live staging browser run with real backend, OCR, ClamAV, OpenAI, Qdrant, FalkorDB, Redis, and storage remains required before production promotion.

## Phase 6 Acceptance Status

- [x] Arbitration generation now uses a stable input hash built from legal inputs, source hashes, claim heads, paragraph responses, section key, prompt version, and source policy.
- [x] Repeated generation with the same input hash reuses the latest version instead of creating duplicate immutable versions.
- [x] Generation runs now record retrieval queries and richer parsed output metadata, including source policy, source hashes, source count, missing-evidence count, validation warnings, approval blockers, and legal-review requirement.
- [x] Source ledger rows now carry verification status, source quality flags, evidence strength, and context warnings for manual facts or unverified AI graph links.
- [x] Paragraph-level SoD/rejoinder denial responses now include supporting citations when mapped, or `[Evidence required]` when support is missing.
- [x] Manual draft versions now receive the same validation report as generated versions.
- [x] Approval now blocks versions with legal drafting safety blockers such as unknown source citations or unsupported amounts/dates, while preserving missing-evidence warnings for review.
- [x] Invalid section regeneration keys are rejected with an explicit allowed-section list.
- [x] Arbitration draft detail UI now displays validation status, source quality flags, missing evidence, legal safety warnings, approval blockers, and section-level regeneration controls.
- [x] Focused backend validation passed: `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py -q` -> 8 passed.
- [x] Backend arbitration plus route-inventory validation passed: `python -m pytest backend/rbac_backend/tests/test_arbitration_drafting.py backend/rbac_backend/tests/test_route_inventory.py -q` -> 12 passed.
- [x] Backend compile check passed for arbitration drafting models/services.
- [x] Frontend targeted lint passed for `ArbitrationDraftingPage.tsx` and `arbitration-drafting-api.ts`.
- [x] Frontend route-permission parity passed: `npm test -- --run src/config/__tests__/rolePermissions.sidebar.test.ts`.
- [x] Frontend production build passed: `npm run build`.
- [ ] Live LLM-backed drafting remains intentionally disabled in this deterministic hardening slice; any future LLM layer must preserve the same source-ledger, validator, versioning, and approval-blocker contract.
- [ ] Browser E2E for arbitration create/import/generate/regenerate/export/approval remains required before production promotion.

## Phase 7 Acceptance Status

- [x] Added backup freshness service: `backend/rbac_backend/services/operations_health.py`.
- [x] Added token-gated `/health/operations` endpoint for backup freshness and operational health reporting.
- [x] Added Prometheus backup gauges: `contractdms_backup_health`, `contractdms_backup_latest_age_seconds`, `contractdms_backup_missing_artifacts`, and `contractdms_backup_unhealthy_artifacts`.
- [x] Added production backup settings to backend config and production validation: `BACKUP_ROOT`, `BACKUP_MAX_AGE_HOURS`, `BACKUP_REQUIRED_IN_PRODUCTION`, `BACKUP_REQUIRED_VOLUME_LABELS`, `BACKUP_S3_BUCKET`, and `BACKUP_S3_PREFIX`.
- [x] Wired production Compose to pass backup settings and mount `BACKUP_ROOT` read-only into backend and worker containers.
- [x] Added standalone backup freshness checker: `scripts/backup_status.py`.
- [x] `scripts/production_backup.sh` now writes a completion manifest, latest manifest, and SHA-256 checksum file.
- [x] `scripts/mongo_backup.sh` now accepts a shared `STAMP` so production backup artifacts align.
- [x] Restore scripts now validate gzip/tar archive integrity before restore.
- [x] `scripts/post_deploy_verify.sh` now sends `METRICS_TOKEN` to `/health/observability`, checks `/health/operations`, and verifies backup freshness.
- [x] `scripts/pre_deploy_readiness.sh` now checks backup scripts, backup S3 configuration, backup freshness, and the current operations/release-gate docs.
- [x] `scripts/smoke_health.py` can optionally include `/health/operations` via `SMOKE_CHECK_OPERATIONS=true`.
- [x] Rewrote `docs/OPERATIONS.md` with clean deployment, monitoring, backup, restore drill, alerting, scheduler, and security operations guidance.
- [x] Focused backend validation passed: `python -m pytest backend/rbac_backend/tests/test_operations_health.py backend/rbac_backend/tests/test_observability.py backend/rbac_backend/tests/test_config_validation.py -q` -> 15 passed.
- [x] Python compile check passed for operations health, observability, health router, backup status, smoke health, and preflight scripts.
- [ ] Bash syntax validation could not run locally because this Windows host maps `bash` to WSL and no WSL distribution is installed.
- [ ] Actual staging backup execution, offsite S3 sync, `/health/operations` scrape, and restore drill remain required before production promotion.

## Phase 8 Acceptance Status

- [x] Added deterministic readiness scoring script: `scripts/production_readiness_score.py`.
- [x] Recorded final production-readiness audit: `docs/PRODUCTION_READINESS_FINAL_AUDIT.md`.
- [x] Launch-readiness score calculated from launch-gate evidence **as at Phase 8**: 32/100. (Superseded — re-derived as 74/100 in R-A8S. Read the readiness-score section above, not this line.)
- [x] Verdict recorded **as at Phase 8**: Not Ready. (Superseded - the scorer now computes Ready. Read the readiness section above, not this line.)
- [x] Pending blockers are recorded by phase.
- [x] Critical blocker register includes final staging deploy/smoke/restore/sign-off gap as `P0-008`.
- [x] Score script compile validation passed: `python -m py_compile scripts/production_readiness_score.py`.
- [x] Score script execution passed: `python scripts/production_readiness_score.py`.
- [x] Critical blockers **as at Phase 8** remained open. (Superseded 2026-09-20: no register row parses as Open - P0-002 formally disposed as dated accepted debt to 2026-10-15, P0-008 closed by the owner sign-off. Read the register and the Gate 9 bullets above, not this line.)
- [x] Readiness score **as at Phase 8** was below the 85/100 target. (Superseded - the scorer now computes 91/100 against that target. Read the readiness section above, not this line.)
- [x] Release owner sign-off **as at Phase 8** was not recorded. (Superseded - signed by Release Owner on 2026-09-20; see Gate 9 bullet 6 and `docs/R_A9G_OWNER_DECISION_RECORD.md`.)
