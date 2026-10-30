# Gate 3 execution plan — what each bullet needs, and what only the owner can decide

Companion to [GATE_3_EVIDENCE_MATRIX.md](GATE_3_EVIDENCE_MATRIX.md), which says
what each bullet's coverage **is**. This file says what a staging run would have
to **do**, bullet by bullet, and isolates the two bullets that cannot be closed by
writing a test at all.

**Nothing here ticks a bullet.** R-A8R is an offline convergence phase with no
staging window. Gate 3 stands at **1 of 9**.

---

## 1. Status, re-derived 2026-09-09 (R-A8S)

| # | Bullet | Coverage | Score | Blocked on |
|---|---|---|---|---|
| 1 | Login, logout, session refresh, CSRF | `EXECUTABLE` | **1.33 earned** | — closed in R-A8Q Stage B, 4/4 over TLS |
| 2 | Org-Admin permission save/retrieve, incl. Client DMS | `EXECUTABLE` | 1.33 | a staging run |
| 3 | Document upload, view, download, share, denial | `EXECUTABLE_PARTIAL` | 1.33 | a staging run **and** an SMTP-sink decision |
| 4 | Contract upload → ingestion → clauses → search → Q&A → appraisal | `MOCKED` | 1.33 | a spec, real ingestion, a model-token budget |
| 5 | Contract timeline link verify/reject | `MISSING` | 1.33 | a spec and a seeded unverified link |
| 6 | Chronology lifecycle incl. export and attach | `MISSING` | 1.33 | a spec, seeded sources, a download assertion |
| 7 | Arbitration draft → generate → version → approve → export | `EXECUTABLE` | 1.33 | a staging run and an approver account. **Owner decision made in R-A8S: NOT withdrawn** — §4 |
| 8 | Empty and loading states over `GATE_3_ROUTE_INVENTORY.md` | `EXECUTABLE_PARTIAL` | 1.33 | a spec that walks the applicable routes. **Owner decision 8-C applied in R-A8S** — §5 |
| 9 | Desktop and mobile smoke for primary workflows | `EXECUTABLE_PARTIAL` | 1.33 | bullets 2, 3 and 5 first |

Each bullet is worth `12 / 9 = 1.333` weighted points.

### Bullet 1 — preserved

Nothing in this phase touched `client/e2e/staging/session-authentication.spec.ts`
except its header comment, which still claimed the spec had never run. The
executed result stands: R-A8Q Stage B, 2026-09-08, 4/4 passed, chromium,
unmocked, over TLS, under `CONTRACLAIM_STAGING_E2E=1`, with the refresh half
measured past the spec's own assertions.

---

## 2. What the fixtures give bullets 2, 3 and 5

`client/e2e/staging/fixtures.ts`, added in R-A8R. Four properties, each asserted
structurally by `backend/rbac_backend/tests/test_gate3_staging_fixtures.py`:

* **run-owned** — every object's name carries `g3-<E2E_RUN_ID>`, and nothing
  without that tag is ever deleted;
* **idempotent** — creating twice with the same tag returns the existing object;
* **deterministically cleanable** — teardown enumerates by tag, deletes by id,
  treats 404 as removed, reports what it could not remove, and takes the tag as an
  **input**, because the run that needs cleaning is the one that died before its
  `afterAll`;
* **tenant-safe** — every write must name `E2E_STAGING_ORG_ID`, and the module
  refuses any target listed in `E2E_PRODUCTION_HOSTS`, re-checked per write rather
  than once at import.

A teardown context is built by `newFixtureContext`, not by
`playwright.request.newContext()` directly: a bare context inherits **nothing**
from `playwright.config.ts` — not `baseURL`, not `ignoreHTTPSErrors` — so every
relative path in the harness would be an invalid URL and the staging TLS
terminator's certificate would be refused. The teardown would then fail for a
reason that has nothing to do with whether the state was removed.

Required environment, all listed in `.env.staging.example`: `E2E_BASE_URL`,
`E2E_RUN_ID`, `E2E_PRODUCTION_HOSTS`, `E2E_STAGING_ORG_ID`,
`E2E_STAGING_PROJECT_ID`, and three credential pairs — the owner
(`E2E_STAGING_*`), the org-admin (`E2E_ORG_ADMIN_*`) and an out-of-scope user
(`E2E_OUTSIDER_*`). Under `CONTRACLAIM_STAGING_E2E=1` a missing one is a failure,
never a skip.

**No production data.** Documents are generated in-process; nothing is copied out
of production, and no fixture file of business content is checked in.

---

## 3. Executable specifications

Each is SETUP / ACTION / EXPECTED / AUTHORITY-SECURITY / CLEANUP. Where a spec
exists it is named; where it does not, this is what it would have to be.

### Bullet 2 — Org-Admin permission save/retrieve — `client/e2e/staging/org-admin-permissions.spec.ts`

| | |
|---|---|
| **SETUP** | Org-admin session. Disposable organisation-scoped role `g3-<run>-permissions`, created idempotently, permissions emptied. |
| **ACTION** | Grant `dms.document.share` and `dms.document.view`; read them back; then in the browser open `/permissions`, confirm the **Client DMS** group and its "Share Documents" entry render, apply the grant, reload. |
| **EXPECTED** | `GET /api/roles/{id}/permissions` returns both after the write, and still returns `dms.document.share` after a full page reload — so the change left the browser. |
| **AUTHORITY / SECURITY** | The same `PUT /api/roles/{id}` **without** `x-step-up-token` must return **403**. A permission change that succeeds without step-up is one anybody with a stolen cookie can make. The role is organisation-scoped, so no grant escapes the fixture tenant. |
| **CLEANUP** | The role is deleted by id after being enumerated by run tag; `report.failed` must be empty. |

### Bullet 3 — Document lifecycle and denial — `client/e2e/staging/document-lifecycle.spec.ts`

| | |
|---|---|
| **SETUP** | Owner session inside `E2E_STAGING_ORG_ID`; an outsider session in a **different** scope; one disposable document whose `letterNo` carries the run tag, uploaded with OCR off. |
| **ACTION** | Owner reads and downloads it; outsider attempts the detail route, the download, the listing and a share. |
| **EXPECTED** | Owner 200 and the exact bytes uploaded. Outsider **403 or 404** on the detail route and the download; the document **absent from the outsider's listing**; the share refused. |
| **AUTHORITY / SECURITY** | **401 is a failure, not a pass.** The client turns 401 into a forced logout and 403 into a toast, so a scope refusal arriving as 401 logs the user out of their own session. A 200 with an empty body is also a failure — the matrix wording is "refused rather than shown an empty page". The listing half is asserted separately because the gate answers membership and `build_scope_query` answers row visibility; a 403 by id proves only one of them. |
| **CLEANUP** | The document is deleted by id; `report.failed` must be empty. |
| **NOT COVERED** | Delivery of the share. `POST /api/share-document` sends real email through the configured SMTP account. Asserting a click and calling it a share is the mocked-evidence failure in a different costume. Needs a staging SMTP sink — an owner decision about staging configuration, not a test. |

### Bullet 5 — Contract timeline link verify/reject — **spec not written**

| | |
|---|---|
| **SETUP** | A disposable contract in the fixture tenant carrying at least one **unverified** proposed timeline link, seeded through the same run-tag mechanism. |
| **ACTION** | Verify one link and reject another in the browser; reload. |
| **EXPECTED** | Both decisions survive the reload, and the API reflects them. |
| **AUTHORITY / SECURITY** | A user without the contract's scope may not verify or reject; refusal is 403/404, never 401 and never a silent no-op. |
| **CLEANUP** | Contract and links removed by run tag. |
| **WHY NOT WRITTEN HERE** | The seeding path for an *unverified* proposed link is not a single documented API call, and inventing one would make the fixture assert against a shape the product does not produce. This needs a short read of the reference-sync path first, and that is bounded work rather than an owner decision. |

### Bullet 4 — Contract ingestion end to end — **spec not written**

| | |
|---|---|
| **SETUP** | A disposable contract PDF, a project, and a model-token budget the run has explicitly been given. |
| **ACTION** | Upload, poll ingestion status to completion, read extracted clauses, run a search, ask a question, generate an appraisal. |
| **EXPECTED** | Clause-first retrieval returns structured `contract_clauses` records; the answer carries citations; the appraisal report opens. |
| **AUTHORITY / SECURITY** | Ingestion must not mark the document `completed` while writing zero vectors — the Qdrant-401 silent-success class. Assert the vector count, not the status field. |
| **CLEANUP** | Contract, document, clause records and the Qdrant namespace entries removed by run tag. |
| **WHY NOT WRITTEN HERE** | Real ingestion costs model tokens and is non-deterministic in content. It is executable, but its assertions have to be structural (counts, citation presence, namespace) rather than textual, and the budget is an owner call. |

### Bullet 6 — Chronology lifecycle — **spec not written**

| | |
|---|---|
| **SETUP** | Seeded source documents in the fixture tenant. |
| **ACTION** | Create a chronology, extract, verify one entry, reject another, export, attach to an arbitration case. |
| **EXPECTED** | Each state transition survives a reload; the export **downloads a file whose bytes parse**, not merely a click that fires. |
| **AUTHORITY / SECURITY** | Export must respect scope: an out-of-scope user gets 403/404 rather than an empty export. |
| **CLEANUP** | Chronology, entries and the arbitration attachment removed by run tag. |
| **WHY NOT WRITTEN HERE** | The attach-to-arbitration leg lands inside the subject of bullet 7, which is an owner decision. Writing the first five legs and stopping at the sixth would produce a spec that can never pass. |

### Bullet 9 — Desktop and mobile smoke — partially covered

`login-responsive.spec.ts` and `blog.spec.ts` run unmocked at desktop, tablet and
390 px — over the **public and login surfaces only**. "Primary workflows" means
the authenticated ones, so this bullet closes after 2, 3 and 5, by running those
specs at the three widths. No new fixture is needed; the work is a viewport
matrix over specs that will already exist.

---

## 4. OWNER DECISION — Gate 3 bullet 7 (arbitration) — **DECIDED 2026-09-09: NOT WITHDRAWN**

> **Outcome.** The proposal below (option 7-A, withdraw the bullet) was put to the
> owner in R-A8S and **refused on the evidence.** Its premise — that the
> arbitration engine is deliberately not primary — is true of the LangGraph
> *workflow* engine and false of the *drafting* surface this bullet names.
> `ArbitrationDraftingService.generate` (`service.py`) selects its generator from
> `ARBITRATION_DRAFT_MODE`, whose default is `deterministic`, and never consults
> `ArbitrationEnginePolicy`; `langgraph_v1` is reached only from
> `workflow_service.py`. Under the shipped configuration the engine policy returns
> `arbitration_v2` for every request, so the drafting path **is** the primary one,
> and every leg the bullet names has a live endpoint under
> `/api/arbitration/drafts` plus a registered client route.
>
> The bullet stays in the scored gate at 9 bullets.
> `client/e2e/staging/arbitration-drafting.spec.ts` was written instead: create →
> generate → edit → version → return → approve → DOCX + PDF, with
> author-approver separation asserted (self-approval must be refused 409) and
> export magic numbers checked. Zero model cost, no rollout flag touched.
> `test_release_gate_specification.py` re-derives the distinction on every run.
>
> The analysis below is preserved as the record of what was proposed and why it
> did not survive contact with the source.


**Literal gate wording.** "Arbitration draft create, generate, edit, save
version, approve/return, export DOCX/PDF."

**Property intended.** The arbitration drafting and approval chain completes end
to end against a deployment and produces both export formats.

**Why the system cannot satisfy it as written.** The arbitration engine is
deliberately **not primary**, and that is a recorded project decision rather than
an oversight. `core/config.py` defaults: `ARBITRATION_ENGINE_DEFAULT =
arbitration_v2`, `ARBITRATION_ENGINE_ROLLOUT_MODE = off`,
`ARBITRATION_ENGINE_PRODUCTION_ACCEPTED = false`, with the same triple for
`DRAFT_ENGINE_*`. Configuration validation refuses `rollout_mode = primary`
without the corresponding `PRODUCTION_ACCEPTED`. Arbitration acceptance is also
unproven in production — zero cases, zero runs — with crash/restart drills, legal
HITL review and version diff/restore UI all outstanding.

So this is a **sequencing** gap, not a test gap. A browser spec that drove the
chain would either be driving a path the release does not enable, or would
require flipping the rollout flags — which is exactly the change the acceptance
receipt chain exists to gate.

**Options.**

| | Option | What it costs | What it gives |
|---|---|---|---|
| **7-A** | **Withdraw the bullet for this release**, the way Gate 2's FalkorDB vector criterion was withdrawn: record the supersession, the reasoning and the reinstatement trigger, and reduce Gate 3 to 8 scored bullets. | An amendment with a recorded owner approval, plus a structural guard so it cannot silently return while the engine is off. | Gate 3's denominator becomes 8, so each remaining bullet is worth `12/8 = 1.5`. **Bullet 1 alone then scores 1.50 instead of 1.33 — a net +0.17.** It does not "give" points; it stops the gate demanding evidence of a feature the release does not ship. |
| **7-B** | **Flip the engine to primary** and earn the bullet. | The full acceptance receipt chain: production acceptance, crash/restart drills, legal HITL review, version diff/restore UI. Weeks, not days. | 1.33 points and a shipped feature. |
| **7-C** | **Leave it open and unticked.** | Nothing now. | Gate 3 caps at 8/9 = 10.67/12 forever, and Gate 9 bullet 1 ("critical blockers closed") has to absorb the explanation. |

**Security and reliability implications.** 7-A removes no control: nothing the
release ships is left untested, because the release does not ship the arbitration
chain as a primary path. 7-B is the only option that changes what production
does, and it does so by enabling an engine whose production acceptance is
unproven. 7-C is the status quo and is honest, but it leaves a permanently
unreachable checkbox inside a gate whose whole purpose is to be closable.

**Recommended: 7-A**, on the same reasoning the Gate 2 amendment used — a gate
criterion that cannot be satisfied by the supported architecture is not a high
bar, it is an unfalsifiable one. **The owner decides; this note does not.**

**Evidence required after the decision.** For 7-A: a supersession block in the
gate document with the verbatim old wording, the reasoning, an owner approval line
scoped to this release, and a reinstatement trigger — plus a structural guard, in
the shape of `test_gate2_required_inventory.py`, that fails if the arbitration
engine ever becomes primary while the bullet stays withdrawn. For 7-B: the
acceptance receipt chain, then the spec, then a staging run.

---

## 5. OWNER DECISION — Gate 3 bullet 8 (empty / loading / error states) — **DECIDED 2026-09-09: OPTION 8-C APPLIED**

> **Outcome.** The bullet is narrowed to **empty and loading** over an enumerated
> route set, and the error-state requirement is relocated to the component and
> mocked-E2E layer where controlled failure injection is legitimate. The UI
> quality bar does not move; only where the evidence is produced.
>
> The denominator is `docs/GATE_3_ROUTE_INVENTORY.md`, generated by
> `scripts/gate3_route_inventory.py` from `client/src/routes.tsx`:
> **92 routes, 85 authenticated, 85 owing a loading state, 59 owing an empty
> state.** The route list is derived and never typed; empty/loading applicability
> and the error-test location are declared per page with a completeness guard in
> both directions, and the generator says so rather than implying it verifies the
> judgement.
>
> **A bullet replaced a bullet.** Gate 3's denominator stayed at 9 and no point
> was earned by the amendment. `test_gate3_route_inventory.py` fails if Gate 3
> re-acquires an error-state requirement, if the denominator becomes undefined, if
> a route lands undeclared, or if the relocated error-state tests are deleted
> rather than moved.
>
> The analysis below is preserved as the record of the argument.


**Literal gate wording.** "Empty, loading, and API-error states for every
production route."

**Property intended.** Every production route renders a deliberate empty state, a
deliberate loading state and a deliberate error state, rather than a blank page or
an uncaught exception.

**Why the system cannot satisfy it as written.** The gate's own preamble says
"Every bullet in this gate is a **staging** requirement: it is satisfied only by a
run against a real staging environment with the live dependencies enabled." An
**error state needs the API to fail on demand**, and there is no way to make a
real staging deployment fail a chosen request at a chosen moment without
intercepting the network — at which point it is no longer a staging run and the
same preamble disqualifies it. The two requirements are not both satisfiable.
"Every production route" is also unbounded: nothing in the repository enumerates
the production route set for the client, so the bullet has no completion
condition.

**Options.**

| | Option | What it costs | What it gives |
|---|---|---|---|
| **8-A** | **Exempt the bullet from the staging rule in writing** and satisfy it with a mocked suite, stating that this is component evidence and naming the route inventory it must cover. | An enumerated client route list, and a mocked spec per route family. Real work, but bounded and offline. | 1.33 points, honestly labelled as component evidence. The exemption must be explicit, or it reopens the R-A8I confusion the matrix was written to end. |
| **8-B** | **Withdraw the bullet**, as in 7-A, and rely on the mocked component suites without claiming a gate checkbox. | An amendment with owner approval. | Denominator 8 (or 7, with 7-A). No new evidence. |
| **8-C** | **Narrow it** to "every production route renders a deliberate empty and loading state" — both of which a real deployment **can** produce — and split the error state into its own component-level requirement outside Gate 3. | A rewording plus a bounded staging spec over the enumerated routes. | 1.33 points, satisfiable against a deployment, and the error state relocated rather than deleted. **Measured in R-A8T: relocated-and-covered for 36 of the 85 applicable routes, relocated-and-outstanding for 49.** |

**Security and reliability implications.** None of the three removes a control;
the difference is where the evidence lives and what it is called. The real risk is
8-A applied silently: a mocked suite recorded as staging evidence is the exact
false-evidence shape R-A8I had to argue against, and it must be labelled at the
point of the tick, not only here.

**Recommended: 8-C.** It keeps a staging requirement that a staging environment
can actually meet, and it puts the error state where the only honest evidence for
it can be produced. **The owner decides; this note does not.**

**Evidence required after the decision.** All three need the missing artefact
first: an **enumerated list of production client routes**, generated from the
router rather than typed, so "every route" has a denominator. Then 8-A/8-C need
the specs and a run; 8-B needs the amendment and a guard.

---

## 6. What R-A8R deliberately did not do

* Did not tick anything. A spec that exists is not a spec that has passed.
* Did not write specs for bullets 4, 5 and 6, because each needs one fact this
  phase could not establish offline without inventing it — the seeding path for an
  unverified timeline link, a model-token budget, and bullet 7's disposition.
* Did not decide bullets 7 or 8.
* Did not create any staging resource, and did not touch production.

## 7. What R-A8S did, and did not

**Did.** Decided both open bullets — 7 refused, 8 amended under 8-C. Wrote
`client/e2e/staging/arbitration-drafting.spec.ts` and the arbitration-draft
fixture and teardown. Generated `docs/GATE_3_ROUTE_INVENTORY.md` and its
generator and guard. Corrected the evidence matrix's row 7, which had recorded a
`MISSING` status on reasoning the source contradicts.

**Did not.** Tick any Gate 3 bullet — bullet 7's spec has never run against a
deployment, and bullet 8 has a denominator but no walk. Earn any point from
either amendment: the withdrawal that would have earned one was refused, and the
narrowing replaced a bullet rather than removing one. Write specs for bullets 4,
5 or 6, whose blockers are unchanged. Create any staging resource, or touch
production.

**What bullet 7 now needs from a staging window:** an approver account
(`E2E_APPROVER_EMAIL` / `E2E_APPROVER_PASSWORD`) distinct from the author, because
`enforce_author_approver_separation` refuses self-approval with 409 — which the
spec asserts rather than works around.

**What bullet 8 now needs:** a spec that visits the applicable routes in the
inventory and asserts a deliberate empty state and a deliberate loading state on
each. That is writable offline; it was not written in R-A8S because the
denominator had to exist first, and a walk of 85 routes is a phase of its own.
