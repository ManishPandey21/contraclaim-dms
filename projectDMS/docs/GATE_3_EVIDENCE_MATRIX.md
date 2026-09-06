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
| 1 | Login, logout, session refresh, and CSRF behavior. | A browser session can be established and ended; a refresh keeps the same session; an unsafe cookie-authenticated request without a matching CSRF token is refused. | `client/e2e/staging/session-authentication.spec.ts` | `EXECUTABLE` | Written from the server's own contracts and **not yet executed against a deployment** - R-A8J had no staging window. The bullet stays unticked until a run produces the result. |
| 2 | Org-Admin permission save/retrieve, including Client DMS permissions. | An org-admin changes a role's permissions in the browser, the change persists across a reload, and the Client DMS permissions are among those offered. | — | `MISSING` | Needs an org-admin account on staging and a disposable role, so the run leaves no permanent grant behind. Neither exists yet. |
| 3 | Document upload, view, download, share, and authorization denial. | A document uploaded in the browser is viewable and downloadable by its owner, and a user outside its scope is refused rather than shown an empty page. | — | `MISSING` | Needs two accounts in different scopes and a disposable document, plus a teardown that removes it - the drill must not accumulate staging documents. |
| 4 | Contract upload, ingestion status, clause extraction, search, Q&A, and appraisal. | The contract workflow completes against real ingestion, a real vector store and a real model. | `client/e2e/contract-workflows.spec.ts` | `MOCKED` | The existing spec routes `**/api/**` to fixtures, so it proves the UI's own states and nothing about the deployment. Real ingestion also costs model tokens, which the run has to budget for. |
| 5 | Contract timeline link verify/reject. | A proposed timeline link can be verified and rejected in the browser and the decision survives a reload. | — | `MISSING` | Needs a seeded contract with at least one unverified link. |
| 6 | Chronology create, extract, verify/reject, export, and attach-to-arbitration flow. | The chronology lifecycle completes end to end, including the export and the attachment. | — | `MISSING` | Needs seeded source documents; the export leg also needs a download assertion, not just a click. |
| 7 | Arbitration draft create, generate, edit, save version, approve/return, export DOCX/PDF. | The drafting and approval chain completes and produces both export formats. | — | `MISSING` | The arbitration engine is deliberately non-primary (`ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ROLLOUT_MODE=off`, `PRODUCTION_ACCEPTED=false`), so this bullet cannot be earned before that decision changes. It is a **sequencing** gap, not only a test gap. |
| 8 | Empty, loading, and API-error states for every production route. | Every production route renders a deliberate empty state, a deliberate loading state, and a deliberate error state. | `client/e2e/contract-workflows.spec.ts` (contract search only) | `MOCKED` | "Every production route" cannot be established against a deployment at all: an error state needs the API to fail on demand, which means mocking. This bullet is satisfiable **only** by a mocked suite, and the gate text should say so rather than treating it as a staging requirement. |
| 9 | Desktop and mobile smoke coverage for primary workflows. | The primary workflows are usable at desktop, tablet and phone widths on the deployment. | `client/e2e/login-responsive.spec.ts`, `client/e2e/blog.spec.ts` | `EXECUTABLE_PARTIAL` | Both run unmocked at desktop, tablet and 390 px - but over the public and login surfaces only. "Primary workflows" means the authenticated ones, and those need bullets 2, 3 and 5 first. |

## What this means for the score

Two bullets are `EXECUTABLE`, and one of those (9) is partial. Five are `MISSING`.
One (4) has only mocked evidence. One (7) is blocked behind a product decision
rather than behind a test.

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
