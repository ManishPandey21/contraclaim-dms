# R-A9G-0 — owner decision record and Gate 9 sign-off

**Recorded 2026-09-20 in release programme R-A9G-0, offline. Verbatim owner decisions,
transcribed as given, plus the mechanical state they were taken against.**

**PRODUCTION DEPLOYMENT: NONE. Nothing was deployed, merged, migrated, aligned, moved,
rotated or upgraded. G32: NOT RUN.** This phase changed documentation and one guard
anchor only.

This file is the single place the seven owner acts that R-A9F left outstanding are
recorded. Where a gate document, the cutover checklist or the open-gate matrix states a
disposition, that statement is a *rendering* of a decision recorded here.

---

## 1. State the decisions were taken against — re-derived, not taken from a prompt

| Item | Expected | Measured | Match |
|---|---|---|---|
| Branch | `release/contraclaim-rc1` | same (worktree `C:\SaaS-release-rc1`) | yes |
| HEAD at the start of this phase | `79180d55d8d0635780405d662871bec1a69641bc` | same | yes |
| Tree | `3487cde0aacb669924e6528bb4c3d63f7336edca` | same | yes |
| Dirty | 0 | 0 | yes |
| `origin` / `contraclaim` | both == HEAD | both `79180d5…` | yes |
| PR #20 | OPEN / DRAFT / UNMERGED | OPEN, draft, `mergedAt: null`, base `main`, head `79180d5` | yes |
| Final R-A9F CI | run `35518788548`, 5/5 | completed, success, 5/5 on headSha `79180d5` | yes |
| Readiness before these decisions | 85 / raw 85.33 | `85/100`, `raw_score 85.33`, verdict Ready | yes |
| Production branch and HEAD | `main` @ `b2d5025` | `main`, `b2d5025035db103a84d815434ef69804bfc6a526` (read-only) | yes |
| G32 | NOT RUN | NOT RUN, NOT CERTIFIED | yes |

No release source changed after R-A9F. The runtime code is certified at candidate
`fe728b205ed5dfb20d88040bc44214ca71d11c43`; every release commit after the fast-forward
is documentation or release control.

---

## 2. A1–A9 — individual disposition

The owner disposed the consolidated high-risk list **item by item**, not as a block. The
acceptances are **specific to this initial release and are not permanent waivers**.

| ID | Item | Disposition | Follow-up deadline |
|---|---|---|---|
| A1 | Gate 3 b3–b7, b9 deployed-browser E2E gaps | **ACCEPTED** as post-release validation debt. Close the deployed browser / live-integration gaps progressively after release, **b7 (arbitration drafting) prioritised** because it is deterministic and zero-model-cost | **2026-10-15** |
| A2 | Gate 3 b8 degraded / empty / loading-state coverage | **ACCEPTED** as bounded UI debt. The existing structural guards and the generated route denominator **remain mandatory** | **2026-10-15** |
| A3 | P0-002 combined live AI/vector/graph integration proof | **ACCEPTED DEBT — owner approved for initial cutover.** Register status changes from `Open` to `Accepted debt - Owner approved for initial cutover`. **The underlying technical evidence is not to be altered or overstated** | **2026-10-15** |
| A4 | P0-007 Python dependency advisories | **ACCEPTED** for the initial cutover: CI's dependency policy is green, and the remaining coordinated framework upgrade would invalidate broad release evidence | **2026-10-15** |
| A5 | Third-party image security exception | **ACCEPTED** exactly as frozen. See section 8 | expiry **2026-10-15**, remediation **2026-10-06 → 2026-10-15** |
| A6 | Shared production S3 bucket for documents and backups | **ACCEPTED** as bounded post-release architecture debt. **Not permanent.** The post-release remediation plan **must establish an independent backup failure domain** | **2026-10-15** |
| A7 | RPO 24 h / RTO 8 h RECORDED, not demonstrated at production scale | **ACCEPTED**; the existing conservative owner decision stands. Required follow-up: **production-scale recovery timing validation**. Quarterly staging restore drills **remain mandatory** | **2026-10-15** |
| A8 | Arbitration production acceptance incomplete | **ACCEPTED** only because `ARBITRATION_ENGINE_ROLLOUT_MODE=off` and `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false` and the LangGraph engine is not primary. **No future promotion to primary is authorised by this acceptance**; the independent arbitration acceptance receipt remains required | — |
| A9 | The 3 production roles / 4 users carried from R-A9B | **DISCHARGED, NOT ACCEPTED AS DEBT.** See section 7 | discharged |

---

## 3. Gate 9 bullet 2 — ticked

**Owner: YES.** Tick "High severity risks fixed or explicitly accepted."

The consolidated list is fully disposed: A1–A8 explicitly accepted with the deadlines
above, A9 discharged through the production-role-impact review and the no-re-grant
decision. Nothing was fixed in order to earn this bullet; the written acceptance **is**
the evidence, and it is bounded to this cutover.

## 4. Gate 9 bullet 1 — ticked, by formal disposition

**Owner disposition: option (a).**

* **P0-002** moves from `Open` to **`Accepted debt - Owner approved for initial cutover`**,
  follow-up **2026-10-15**.
* **P0-003** and **P0-007** remain in their current formally disposed / partially
  mitigated states. Under the literal Gate 9 b1 structural rule they were never `Open`
  blockers.
* **P0-008** closes on the bullet 6 sign-off below — the fifth and last of its five named
  required resolutions.

With no register row parsing as `Open`, bullet 1 is ticked.

> **The record must make clear that P0-002 was FORMALLY DISPOSED as dated accepted debt,
> not technically closed.** It is stated that way in the bullet, in the register row, and
> here. The combined live staging proof with OCR, ClamAV, OpenAI, Qdrant, FalkorDB and
> Redis enabled has still never been captured.

## 5. Gate 9 bullet 5 — ticked

**Owner: YES.** Tick "Readiness score target is 85 or higher."

Recorded explicitly, as the owner required:

* **Before ticking b5: raw readiness score = 85.33, displayed score = 85.**
* The criterion was therefore **already satisfied with b5 unticked**, which is the
  non-circularity condition `backend/rbac_backend/tests/test_gate9_scoreability.py`
  enforces.
* **Any resulting increase after ticking b5 is checkbox arithmetic recording an
  already-satisfied condition. It is NOT a new staging measurement.**

After the four Gate 9 ticks the scorer reports **91/100, raw 90.67, verdict Ready**. The
delta from 85 / 85.33 is Gate 9 moving 2/6 to 6/6 and nothing else. **The sealed R-A9E
staging evidence measured 85 / raw 85.33 and has not been re-run, re-scoped, edited or
rewritten.** Compare that evidence against 85, never against 91.

## 6. Gate 9 bullet 6 — release owner sign-off

**Owner: YES.** Signer identifier: **Release Owner**.

> ### GATE 9 BULLET 6 — RELEASE OWNER SIGN-OFF
>
> Release: `release/contraclaim-rc1`
>
> Frozen release HEAD before this owner-decision documentation update:
> `79180d55d8d0635780405d662871bec1a69641bc`
>
> Frozen release tree: `3487cde0aacb669924e6528bb4c3d63f7336edca`
>
> Runtime code certified at candidate: `fe728b205ed5dfb20d88040bc44214ca71d11c43`
>
> Final R-A9F CI: `35518788548`, 5/5 GREEN
>
> I have reviewed the consolidated high-risk acceptance list A1–A9.
>
> I accept A1–A8 as the bounded residual-risk profile of this initial production release.
>
> A9 has been discharged through the production-role-impact review, with no re-grant of
> alias-fanout-only authority.
>
> I accept the dated third-party image exception expiring 2026-10-15 and accept
> responsibility as its post-cutover owner.
>
> I approve: FalkorDB Variant A; G32 excluded from this initial cutover; the frozen
> role-alignment operation; the production migration sequence; the ClamAV overlay
> retirement procedure; the backup and rollback contracts; and the production cutover
> described by the frozen R-A9G plan and production manifest.
>
> **Signer: Release Owner**
>
> **Date: 2026-09-20**
>
> This sign-off closes P0-008 after all other required P0-008 conditions already measured
> in the release programme are confirmed.

The other four P0-008 conditions, confirmed: the staging deploy, the smoke after deploy
and the smoke after the restore drill were measured in R-A8Z Stage B
(`scripts/post_deploy_verify.sh`, exit 0); the readiness re-derivation ran in R-A9F.

---

## 7. Production role impact — A9 discharged

**Owner: REVIEWED. NO RE-GRANT** for the three roles carried from R-A9B receipt section 16.

The authority those roles lose was obtained **only through unintended permission-alias
fan-out** and is **not part of the canonical release role contract**. **That removal is
INTENDED.**

| ROLE (anonymised in the production census) | USERS | PERMISSION LOST (fan-out only) | CANONICAL RELEASE CONTRACT SAYS INTENDED? | R-A9F ROLE ALIGNMENT ADDS IT? | OWNER ACTION |
|---|---:|---|---|---|---|
| `custom#2` | 2 | `dms.project.manage`, `dms.task.manage`, `dms.claim.manage`, `projects:create/delete/assign`, 8 keydate / arbitration / chronology / evidence admin grants | **No** — the role held 10 approve/manage siblings but never `dms.project.manage` | **No** — a custom role, outside `ALIGNED_ROLE_IDS` | **None. No re-grant** |
| `custom#3` | 1 | `dms.admin` and **64** `dms.*` policy grants | **No** — held `billing.plan.manage` + `dms.project.manage`, never `dms.admin` | **No** — outside `ALIGNED_ROLE_IDS` | **None. No re-grant** |
| `custom#11` | 1 | `dms.admin`, `dms.project.manage` and **67** policy grants | **No** — held `billing.plan.manage` only | **No** — outside `ALIGNED_ROLE_IDS` | **None. No re-grant** |

Do **not** restore `dms.admin`, `dms.project.manage`, `dms.task.manage`, `dms.claim.manage`
or any related fan-out-derived policy grant merely to preserve broken historical
behaviour. **If an operational need is identified after cutover, grant the specific
required permission explicitly through normal role administration.**

Also carried from the same census, needing no action: 5 further roles change but have
**0 users**; `custom#4` (2 users) loses only a probe artefact; F-A9A-1 production impact
is **0 users**.

**Default-role alignment remains exactly as certified and frozen in
`docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md`:**

| ROLE | USER COUNT | PERMISSION LOST | CANONICAL CONTRACT SAYS INTENDED? | ALIGNMENT ADDS IT? | OWNER ACTION |
|---|---|---|---|---|---|
| `orgadmin` | — | `billing.plan.manage` (removed by approved Class B) | Yes — 54 to 135 is the release contract (129) plus 6 preserved and reported extras | Yes — `align_role_contract`, cutover step 14 | **None** |
| `projectadmin` | — | `billing.plan.manage` | Yes — 53 to 118 is the contract (114) plus 4 preserved | Yes | **None** |
| `projectuser` | — | none | — | **No** — outside `ALIGNED_ROLE_IDS`; **no accidental re-grant** | **None** |
| `orguser` | — | none | — | **No** — outside `ALIGNED_ROLE_IDS`; **no accidental re-grant** | **None** |
| `contractmgr_org`, `settings_manager`, `limited_user` | — | none; they **keep** their pre-existing `billing.plan.manage` | — | No — the removal names two roles and is deliberately not generalised | **None** |

**NO ADDITIONAL MANUAL PRE-CUTOVER RE-GRANT IS REQUIRED. A9 IS DISCHARGED.**

---

## 8. Third-party image exception — accepted

**Owner: YES**, for **this initial cutover only**. Use the exact pinned digests in
`docs/R_A9F_PRODUCTION_MANIFEST.md`.

| Image | Digest (index) | Fixable C/H | Expiry | Remediation window | Post-cutover owner |
|---|---|---:|---|---|---|
| MongoDB `mongo:8.0` | `sha256:ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6` | **273** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| FalkorDB `falkordb/falkordb:v4.0.8` | `sha256:af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8` | **148** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| Qdrant `qdrant/qdrant:v1.12.5` | `sha256:05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae` | **84** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| httpd `httpd:2.4` | `sha256:393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742` | **51** | 2026-10-15 | 2026-10-06 → 2026-10-15, **first** | Release Owner |

**Mandatory review trigger: 2026-10-06** — re-scan the exact deployed digests. A
materially increased finding count, a newly exploitable CRITICAL, or a new public-path
risk **may accelerate remediation ahead of the stated window**.

**The exception is NOT automatically renewable. Silence does not extend it.**

**ClamAV 1.4.6: no exception required. Redis: no exception required.**

---

## 9. FalkorDB cutover — Variant A

**Owner: VARIANT A remains APPROVED. Do not reopen Variant B.** Required rules, restated
as the owner gave them and already carried by
`docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` sections 3 to 5:

fresh rescue archive from `/FalkorDB` · semantic validation · disposable restore proof ·
original production Falkor container preserved · seed release persistence at `/data` ·
start the new Falkor engine out of band · authenticated `PING` · `GRAPH.LIST` · exact
graph parity · application graph smoke · new semantic backup · disposable restore of the
new backup · switch application consumers only after parity · rotate `FALKORDB_PASSWORD`
only after parity · rollback = stop new / start original · keep the original container
through the required acceptance period.

**No Variant-A-specific owner choice remains open before R-A9G.**

## 10. G32

**Owner: CONFIRMED.** G32 is **NOT REQUIRED FOR INITIAL RELEASE · NOT RUN · NOT CERTIFIED
· POST-RELEASE CERTIFICATION · SEPARATE OWNER AUTHORIZATION REQUIRED.** Do not add G32 to
R-A9G.

---

## 11. Build-context divergence — narrow owner-accepted release-freeze exception

**Owner decision, 2026-09-20.** The owner authorised the Gate 9 anti-vacuity re-anchor and then
accepted the one-file build-context divergence it creates.

The only build-context difference between the R-A9E certified runtime candidate and the final
release working tree is **`backend/rbac_backend/tests/test_gate9_scoreability.py`**. Measured
with `git diff --name-only fe728b2 -- <context>`: `./backend` differs by exactly that file,
`./client` by 0 files, `./services/graphiti` by 0 files.

The change is a release/gate guard test. Stated as the exact field set the owner required,
rather than as a rounded summary:

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

**"Build-input differences: 0" must not be recorded anywhere**, because the file is inside the
backend Docker build context. The true pair is **runtime-imported differences 0, Docker
build-context differences 1**, and that is what preserves the narrow provenance exception
instead of quietly dissolving it.

It changes no runtime production code, route, migration, dependency, Dockerfile, compose file,
runtime configuration, authorization implementation or production entrypoint.

**Mandatory runtime-import proof, executed before the commit.**

| Check | Method | Result |
|---|---|---|
| Any reference to the module outside itself | repository-wide grep across `.py`, `.toml`, `.cfg`, `.ini`, `.yml`, `.yaml`, `Dockerfile*`, `.sh` | **none** |
| Runtime import of the tests package | grep for `from/import (rbac_backend.)tests` outside `tests/` | **none** |
| Is `rbac_backend.tests` even a package? | `ls backend/rbac_backend/tests/__init__.py` | **no `__init__.py`** — not an importable package |
| Runtime `__init__.py` referencing tests | grep across runtime `__init__.py` files | **none** |
| pytest plugin registration outside tests | grep for `pytest11` / `pytest_plugins` | **none** |
| Dynamic import / discovery in runtime source | grep for `importlib`, `pkgutil`, `__import__`, `iter_modules`, `walk_packages` | only `ai_workflows.langgraph.*` (a package shim) and hardcoded service lists in `manual/validation/` — **none reach tests** |
| FastAPI startup | `import rbac_backend.main`, inspect `sys.modules` | imported OK, **0** `rbac_backend.tests` modules |
| Worker startup | `import rbac_backend.worker` | imported OK, **0** |
| CLI startup | `rbac_backend.scripts.migrate_database`, `rbac_backend.scripts.align_role_contract` | imported OK, **0** |
| `test_gate9_scoreability` in `sys.modules` after all four | direct check | **False** |

**RESULT: CLEAN — no application test module is reachable from any runtime execution path.**

**Deployable artefact rule (binding).** R-A9G deploys the **R-A9E certified backend image**
`sha256:52fd95afb62f36b77521b0926ec4cafd13235b029fcbc2351f06d942db9a23fb`. It is **not** rebuilt
or re-pinned because this test file changed. If the final CI Docker job produces a different
digest, that image is **verification output only**: it must still pass the normal vulnerability
policy, but it does not replace the certified deployment artefact and the two are recorded
separately. Details: `docs/R_A9F_PRODUCTION_MANIFEST.md` section 2.

**Gate 3 b2 and Gate 4 b8 stand.** Both were measured against the exact R-A9E certified runtime
image; R-A9G deploys that exact image; no runtime source used by it changed; the sole later
divergence is an unimported guard. The measured deployed behaviour is the behaviour being
promoted. **The exception does not broaden**: any future change to runtime source, dependencies,
Dockerfile, compose, migrations, runtime configuration, entrypoints, worker code or client
runtime code stales the relevant image and staging provenance normally.

## 12. Post-release hardening item — test files in the backend image

Recorded by owner decision, **not a blocker for the initial cutover**.

`backend/.dockerignore` excludes `tests/`, a pattern that matches only a context-root `tests`
directory and therefore **not** `rbac_backend/tests`; `backend/Dockerfile` then does `COPY . .`.
The backend build context is 950 tracked files, **403 of them test files**, so the production
backend image appears to ship roughly 403 test files.

**Do not change `.dockerignore` or the Dockerfile during this release freeze.** Post-release:
verify the actual image contents (this workstation has no Docker daemon, so the exclusion
semantics above are read from Docker's documented rules and are **not** measured), update
`.dockerignore` intentionally, prove runtime package behaviour is unchanged, rebuild and
re-scan, and re-earn whatever release evidence the hardened image stales before adopting it.
**Suggested follow-up deadline: 2026-10-15.**

## 13. What is still not authorised

The **R-A9G maintenance window is an owner grant and has not been given.** Recommended
8 hours, minimum 120-minute execution budget, minimum 90-minute protected recovery
reserve, `LATEST_SAFE_STOP = WINDOW_START + 270 min`, gated twice by
`scripts/check_maintenance_time_budget.py --recovery-reserve-minutes 90
--execution-budget-minutes 120`. **No start time is invented here.**

**Production deployment remains NONE until the owner separately authorises that window.**
