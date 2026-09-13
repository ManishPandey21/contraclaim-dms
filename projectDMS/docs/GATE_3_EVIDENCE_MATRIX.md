# Gate 3 evidence matrix

R-A8I ran **33 of 33** Playwright tests green against the staging deployment and
earned **no Gate 3 checkbox**. Nothing was broken; the executions did not
correspond to what the bullets ask. Three separate reasons, and each is worth
naming because they need different fixes:

1. **The suite could not target a deployment at all.** `playwright.config.ts`
   hard-coded `baseURL http://127.0.0.1:5173` and a `webServer` that starts a Vite
   dev server. Staging was reached through a temporary config written for the run
   and deleted afterwards - evidence produced by an artefact that is not in the
   repository is not evidence. *Fixed in R-A8J: `E2E_BASE_URL` selects the target
   and suppresses the dev server.*
2. **Most of the passing tests mock the network.** `contract-workflows.spec.ts`
   and `contract-master.spec.ts` intercept `**/api/**`. Pointed at staging they
   would assert on their own fixtures while appearing to exercise the deployment,
   which is worse than not running them. *Fixed in R-A8J: they are excluded when
   `E2E_BASE_URL` is set.*
3. **Eight of the nine bullets have no spec in the tree.** That is a test gap, and
   no amount of configuration closes it. It is enumerated below.

This file is the enumeration, and `test_gate3_evidence_matrix.py` enforces it: a
bullet may be ticked in `PRODUCTION_READINESS_RELEASE_GATE.md` only if its row
here is `EXECUTABLE` and names an artefact that exists.

## Status vocabulary

| Status | Meaning |
|---|---|
| `EXECUTABLE` | A spec exists that runs unmocked against a deployed stack. |
| `EXECUTABLE_PARTIAL` | An unmocked spec exists but covers only part of what the bullet asks. Not tickable. |
| `MOCKED` | A spec exists but intercepts the API. Component evidence, never deployment evidence. |
| `MISSING` | No spec exists. |
| `MANUAL` | The property cannot be established by an automated browser run; an owner records it. |

## The matrix

| # | Bullet | Intended property | Executable evidence | Current coverage | Missing coverage |
|---|---|---|---|---|---|
| 1 | Login, logout, session refresh, and CSRF behavior. | A browser session can be established and ended; a refresh keeps the same session; an unsafe cookie-authenticated request without a matching CSRF token is refused. | `client/e2e/staging/session-authentication.spec.ts` | `EXECUTABLE` | **Executed against a deployment in R-A8Q Stage B, 2026-09-08: 4/4 passed** over TLS, unmocked, under `CONTRACLAIM_STAGING_E2E=1`. The refresh half was measured beyond the spec's own assertions: one `token_refresh` audit row, its user resolving to the signed-in account, its `resource_id` equal to the reissued token's `session_id`. The bullet is ticked. |
| 2 | Org-Admin permission save/retrieve, including Client DMS permissions. | An org-admin changes a role's permissions in the browser, the change persists across a reload, and the Client DMS permissions are among those offered. | `client/e2e/staging/org-admin-permissions.spec.ts`, `client/e2e/staging/fixtures.ts` | `EXECUTABLE` | Spec written in R-A8R against the deployed page's own affordances and the server's own contracts; the disposable role is created and deleted by the run-owned fixture harness. **Executed against a deployment in R-A8W Stage B, 2026-09-13, twice: 7/9 passed each time, and the bullet stays UNTICKED.** Passed: a Client DMS grant (`dms.document.share`, `dms.document.view`) saved through the API and read back from `GET /api/roles/{id}/permissions`, and the same write refused 403 without a step-up token. Failed: the browser leg. The first run landed on the mandatory Security Terms acceptance screen, because the freshly seeded org-admin had not accepted the active terms (a fixture gap; the second run accepted them through `POST /api/security-terms/accept` first). The second run reached Permissions Management and still failed, on **F-A8W-B4, a spec defect**: the page opens on `<Tabs defaultValue="roles">` and renders the permission groups only in the `permissions` tab, and the spec asserts "Client DMS" without selecting that tab. The spec must select the tab before this bullet can be re-measured. **R-A8X corrected the spec offline (not yet re-executed, so still UNTICKED):** the terms are accepted through the terms page (`openProtectedPage` ticks the consent box and presses "Accept and Continue", observed on the wire) rather than by a harness POST; the Permissions tab is selected and the matrix narrowed to the disposable role before "Client DMS" is asserted; and the grant is now made in the browser - tick "Share Documents", "Save Changes", the step-up dialog's "Verify" - then read back from the server as exactly `[dms.document.share]` and found still ticked after a reload. `test_gate3_staging_fixtures.py` pins all three. After the R-A8X independent review the file also runs serially (its tests share one run-owned role), scopes the role picker to the Permissions tab panel (the navbar's native selects are comboboxes too), waits for the role's permission load before asserting, refuses a fixture role that is not organisation-scoped in `E2E_STAGING_ORG_ID`, and adds two Gate 4 probes - a foreign user and role read by id, and the org admin attempting to move itself into the foreign organisation (the escalation that review found, fixed offline). The Gate 4 half now also needs `E2E_FOREIGN_USER_ID` and `E2E_FOREIGN_ROLE_ID`. Needs `E2E_ORG_ADMIN_EMAIL`/`_PASSWORD` and `E2E_STAGING_ORG_ID` on the staging stack. **R-A8W added the Gate 4 bullet 8 half to the same file**: org admin allowed in its own organisation (own org listed and readable, own projects visible and non-empty) and refused outside it (foreign organisation and project by direct id 403/404, a foreign-organisation filter leaks no row, no foreign role or user visible, the admin pages walked in the browser receive no foreign object and no 401). Read-only against the foreign tenant; also needs `E2E_FOREIGN_ORG_ID` and `E2E_FOREIGN_PROJECT_ID`. |
| 3 | Document upload, view, download, share, and authorization denial. | A document uploaded in the browser is viewable and downloadable by its owner, and a user outside its scope is refused rather than shown an empty page. | `client/e2e/staging/document-lifecycle.spec.ts`, `client/e2e/staging/fixtures.ts` | `EXECUTABLE_PARTIAL` | Upload, view, download and the denial (detail route, download and **listing**, accepting 403/404 and never 401 or an empty 200) are covered and torn down. The **share** leg is split: `POST /api/share-document` authorises with `dms.document.share` and then sends real email, so its refusal is asserted and its delivery is not. Delivery needs a staging SMTP sink, which is an owner decision about staging configuration rather than a test to write. **Not yet executed against a deployment.** |
| 4 | Contract upload, ingestion status, clause extraction, search, Q&A, and appraisal. | The contract workflow completes against real ingestion, a real vector store and a real model. | `client/e2e/contract-workflows.spec.ts` | `MOCKED` | The existing spec routes `**/api/**` to fixtures, so it proves the UI's own states and nothing about the deployment. Real ingestion also costs model tokens, which the run has to budget for. |
| 5 | Contract timeline link verify/reject. | A proposed timeline link can be verified and rejected in the browser and the decision survives a reload. | — | `MISSING` | Needs a seeded contract with at least one unverified link. |
| 6 | Chronology create, extract, verify/reject, export, and attach-to-arbitration flow. | The chronology lifecycle completes end to end, including the export and the attachment. | — | `MISSING` | Needs seeded source documents; the export leg also needs a download assertion, not just a click. |
| 7 | Arbitration draft create, generate, edit, save version, approve/return, export DOCX/PDF. | The drafting and approval chain completes and produces both export formats. | `client/e2e/staging/arbitration-drafting.spec.ts`, `client/e2e/staging/fixtures.ts` | `EXECUTABLE` | **The previous row was wrong and was corrected in R-A8S.** It read `MISSING` and said the bullet "cannot be earned before that decision changes", citing `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ROLLOUT_MODE=off`, `PRODUCTION_ACCEPTED=false`. Those flags govern the LangGraph **workflow** engine, reached only from `services/arbitration_drafting/workflow_service.py`. This bullet names the **drafting** surface, whose `ArbitrationDraftingService.generate` selects its generator from `ARBITRATION_DRAFT_MODE` (default `deterministic`) and never consults `ArbitrationEnginePolicy`; under the shipped configuration that policy returns `arbitration_v2` for every request, so the drafting path **is** primary. Every leg the bullet names has a live endpoint under `/api/arbitration/drafts` and a registered client route. The spec was written in R-A8S and runs at zero model cost. Needs `E2E_APPROVER_EMAIL`/`_PASSWORD` for the author-approver separation half. **Not yet executed against a deployment.** |
| 8 | Empty and loading states across the applicable routes in the route inventory. | Every route the inventory marks applicable renders a deliberate empty state and a deliberate loading state. | `docs/GATE_3_ROUTE_INVENTORY.md`, `client/e2e/staging/staging-target.ts` | `EXECUTABLE_PARTIAL` | **AMENDED in R-A8S (owner decision 8-C); see the supersession block in the gate document.** The old wording asked for two mutually exclusive things - an API-error state needs the API to fail on demand, and a run that mocks or faults the API is not the unmocked staging run this gate requires - over a denominator ("every production route") that named no route set. The error-state requirement moved to the component and mocked-E2E layer, where controlled failure is legitimate, and every row of the inventory names its error-test location. **R-A8T counted those locations rather than trusting the sentence: of the 85 applicable routes, 36 name an existing error test and 49 - across 42 page components - name `MISSING - no fault-injection coverage yet`. The relocation is real for 36 and outstanding for 49**, and `test_the_relocated_error_coverage_does_not_shrink` ratchets the 36 so the destination cannot be emptied afterwards. The denominator is now `docs/GATE_3_ROUTE_INVENTORY.md`, generated from `client/src/routes.tsx`: **92 routes, 85 owing a loading state, 59 owing an empty state**. `EXECUTABLE_PARTIAL` because no spec yet walks that set against a deployment - the denominator exists, the walk does not. |
| 9 | Desktop and mobile smoke coverage for primary workflows. | The primary workflows are usable at desktop, tablet and phone widths on the deployment. | `client/e2e/login-responsive.spec.ts`, `client/e2e/blog.spec.ts` | `EXECUTABLE_PARTIAL` | Both run unmocked at desktop, tablet and 390 px - but over the public and login surfaces only. "Primary workflows" means the authenticated ones, and those need bullets 2, 3 and 5 first. |

## What this means for the score

Bullet 1 has been executed against a deployment and is ticked. Bullets 2 and 3
gained executable artefacts in R-A8R and **neither is ticked**: a spec that
exists is not a spec that has run, and the fourth condition below is the one no
test can check. Bullets 3 and 9 are partial for different reasons - 3 because one
leg of it has an external side effect nobody has decided about, 9 because its
subject is the authenticated workflows that bullets 2, 3 and 5 have to establish
first. Three are `MISSING` (5, 6 and — as a workflow — 4's real ingestion). One
(4) has only mocked evidence. One (7) is blocked behind a product decision rather
than behind a test, and one (8) cannot be satisfied as written at all.

**R-A8R added the fixture harness bullets 2, 3 and 5 all needed.**
`client/e2e/staging/fixtures.ts` creates run-owned objects (every name carries
`g3-<E2E_RUN_ID>`), creates them idempotently, deletes only what carries the tag,
reports what it could not delete, refuses any write that does not name
`E2E_STAGING_ORG_ID`, and refuses to run at all against a host listed in
`E2E_PRODUCTION_HOSTS`. Those four properties are asserted structurally by
`backend/rbac_backend/tests/test_gate3_staging_fixtures.py`, which establishes
that the rules are written into the harness — not that the harness has run.

Bullet 8 deserves a decision rather than a spec. The gate text says *"Every bullet
in this gate is a staging requirement: it is satisfied only by a run against a
real staging environment"*, and an on-demand API failure cannot be produced
against a real staging environment without mocking it - at which point it is no
longer a staging run. Either the bullet is exempted from the staging rule in
writing, or it is withdrawn the way the FalkorDB vector criterion was in Gate 2.
R-A8J does not decide it; it records that the bullet as written cannot be
satisfied.

## Ticking a bullet

A bullet may be ticked only when **all** of the following hold, and
`test_gate3_evidence_matrix.py` checks the first three mechanically:

1. its row here is `EXECUTABLE`;
2. the artefact named in that row exists in the repository;
3. the bullet line in `PRODUCTION_READINESS_RELEASE_GATE.md` carries
   `evidence: <path>` naming that artefact;
4. a staging run with `E2E_BASE_URL` set and `CONTRACLAIM_STAGING_E2E=1` has
   executed it green, and the run is recorded in a phase receipt.

The fourth is the one no test can check, which is why it is written down.
