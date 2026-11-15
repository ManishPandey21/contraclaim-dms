# R-A9F frozen production manifest

**Frozen 2026-09-20, release programme R-A9F. Offline. Nothing here has been deployed.**

Every reference below is a content hash, a commit, a tree or an image digest. There are no
floating tags and no "latest". Two facts are recorded in the untracked R-A9F receipt rather
than here, because a file cannot contain the hash of the commit that adds it: the **final
release commit SHA** and the **final CI run id**. They are in
`.claude/context/contract-master/R-A9F-RELEASE-INTEGRATION-FREEZE-RECEIPT.md` and on PR #20.
Everything a build or a deploy consumes is pinned here by content, which does not depend on
either.

## 1. Source

| Item | Value |
|---|---|
| Certified candidate commit | `fe728b205ed5dfb20d88040bc44214ca71d11c43` |
| Certified candidate tree | `b936ba255698b2a7bade787470baee215c34cf69` |
| Certified candidate branch | `fix/role-assignment-resolution` |
| Release branch | `release/contraclaim-rc1` |
| Integration method | **fast-forward** (`93bbc308af150c8c794550d8f0dcfd152e2d2db4` was already an ancestor); 15 commits |
| Release tree at the moment of integration | `b936ba255698b2a7bade787470baee215c34cf69` — identical to the certified tree |
| Release commits after the fast-forward | documentation and release-control only; no runtime source, Dockerfile, compose file, migration, seed or script |
| Git remotes | `origin` = `ManishPandey21/projectDMS`, `contraclaim` = `ManishPandey21/contraclaim-dms` |
| PR for the release branch | **#20** on `ManishPandey21/contraclaim-dms` — OPEN, DRAFT, UNMERGED |
| PR for the candidate branch | **#21** on `ManishPandey21/contraclaim-dms` — OPEN, DRAFT, UNMERGED |
| CI on the exact certified HEAD | run **35072410951**, 5/5 green on `fe728b2` (Backend lint and tests · Frontend lint, tests, and build · Dependency vulnerability scan · Secret scan · Docker build and image scan) |

## 2. First-party images — certified in R-A9E, not to be rebuilt

R-A9E built these from the certified checkout at `fe728b2` (`docker build --pull --no-cache`)
and scanned them under the exact CI policy. These bytes remain the certified bytes for this
release. If a release tag is required, **retag these exact image ids — do not rebuild.** A
rebuild picks up newer base layers, and that would require a fresh scan and, for anything it
changes, fresh staging evidence.

### Provenance model — precise, after the R-A9G-0 owner decision (2026-09-20)

The earlier absolute form of this paragraph said the release-only commits change **no** build
input. That is no longer exactly true, and the precise statement replaces it:

> **The final release runtime production source and deployment configuration are byte-identical
> to the certified R-A9E candidate, except for one non-runtime Gate 9 test file located inside
> the Docker build context.**

| Field | Value |
|---|---|
| CERTIFIED CANDIDATE | `fe728b205ed5dfb20d88040bc44214ca71d11c43` |
| CERTIFIED BACKEND IMAGE | `sha256:52fd95afb62f36b77521b0926ec4cafd13235b029fcbc2351f06d942db9a23fb` |
| CERTIFIED CLIENT IMAGE | `sha256:c7ccd59ec4d4f5a369d55d095773a90533a2079d786da0f1f2d0dff98d4e00f8` |
| FINAL RELEASE HEAD | the R-A9G-0 owner-decision commit on `release/contraclaim-rc1` |
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

Measured with `git diff --name-only fe728b2 -- <context>` against the release working tree.

**The build-context count is deliberately reported as 1, not 0.** The differing file is a test,
it is imported by nothing at runtime, and it changes no product behaviour - but it lives inside
the `./backend` Docker build context, and `backend/Dockerfile` copies that context with
`COPY . .`. Recording it as "0 build-input differences" would be the comfortable number rather
than the true one, and it is exactly the claim this exception exists to qualify. The honest pair
of statements is: **runtime-imported differences 0, Docker build-context differences 1.**

**Why that file changed at all.** The owner disposed P0-002 as dated accepted debt and closed
P0-008 on the Gate 9 bullet 6 sign-off, which legitimately left the Critical Blocker Register
with zero `Open` rows. The guard's anti-vacuity control asserted that the *real* register still
carried an `Open` row, so it began failing for the one reason it was never meant to — success.
Its own docstring instructed the re-anchor, and the owner authorised it narrowly.

**Runtime-import proof, recorded rather than assumed.** No file in the repository references
`test_gate9_scoreability` outside that file itself; no runtime module imports the
`rbac_backend.tests` package, which is not even a package (there is no `__init__.py`); no
`pytest11` entry point or `pytest_plugins` declaration exists outside the tests; the only
dynamic imports in runtime source are scoped to `ai_workflows.langgraph.*` and to hardcoded
service lists in `manual/validation/`. Importing all four runtime entrypoints —
`rbac_backend.main`, `rbac_backend.worker`, `rbac_backend.scripts.migrate_database` and
`rbac_backend.scripts.align_role_contract` — loads **zero** `rbac_backend.tests` modules and
does not load `test_gate9_scoreability`.

### DEPLOYABLE ARTIFACT RULE — binding

| Field | Value |
|---|---|
| **DEPLOYABLE BACKEND ARTIFACT** | the **R-A9E certified image digest** `sha256:52fd95afb62f36b77521b0926ec4cafd13235b029fcbc2351f06d942db9a23fb` |
| **DEPLOYABLE CLIENT ARTIFACT** | the R-A9E certified image digest `sha256:c7ccd59ec4d4f5a369d55d095773a90533a2079d786da0f1f2d0dff98d4e00f8` |
| **FINAL RELEASE SOURCE HEAD** | contains one later non-runtime, test-only divergence |
| **Provenance relationship** | **runtime-equivalent, but not whole-build-context byte-identical** |

**R-A9G MUST deploy the exact backend image already certified in R-A9E.** Do **not** substitute
a backend image rebuilt from the documentation/test-only release HEAD, and do **not** move this
manifest's deployable digest to a new CI-built image.

**Final-CI Docker output is verification only.** The CI pipeline still runs its Docker
build-and-scan job on the final HEAD. If that build produces a *different* backend image digest
because the changed test file is copied into the image, that image is **CI VERIFICATION OUTPUT
ONLY**. It must still satisfy the normal vulnerability policy, but its existence does not
replace the R-A9E certified deployment artefact, and the two are recorded separately and never
conflated.

**Gate 3 b2 and Gate 4 b8 are not invalidated**, for reasons recorded in
`docs/PRODUCTION_READINESS_RELEASE_GATE.md`: both were measured against the exact R-A9E
certified runtime image, R-A9G deploys that exact image, no runtime source used by it changed,
and the sole later divergence is an unimported guard. **The exception does not broaden** to any
future change in runtime source, dependencies, Dockerfile, compose, migrations, runtime
configuration, entrypoints, worker code or client runtime code.

| Service(s) | Staging tag built in R-A9E | Image id (sha256) | Trivy (CI policy) |
|---|---|---|---|
| backend, contract-worker, document-worker, document-worker-canary (one build context) | `contraclaim-stg-backend:rc1-fe728b2` | `sha256:52fd95afb62f36b77521b0926ec4cafd13235b029fcbc2351f06d942db9a23fb` | exit 0, **0** fixable CRITICAL/HIGH |
| client | `contraclaim-stg-client:rc1-fe728b2` | `sha256:c7ccd59ec4d4f5a369d55d095773a90533a2079d786da0f1f2d0dff98d4e00f8` | exit 0, **0** fixable CRITICAL/HIGH |

Scanner: Trivy **0.74.0** (`--ignore-unfixed --severity CRITICAL,HIGH --exit-code 1`).
Negative control on an old rollback image returned exit 1 with **331** findings, so a zero is
a measurement rather than a broken invocation. The freshness gate said GO with a 24 h maximum
age, and its negative control (the stale `83f553e` images against the R-A9E reports) returned
**NO-GO**, so the gate discriminates.

Image smokes proved the built image carries the certified source: thirteen authorization and
migration files hashed identically in the image and the checkout, `alignment_catalogued=0`,
`system_admin_declared_as_alias=0`, `owner_approved_removal_billing=2`.

**Production first-party tags were untouched throughout R-A9E** (`contraclaim-backend`
`1d48edb5eecb`, `contraclaim-client` `e4f4f60d9bbb`, `contraclaim-contract-worker`
`1ec29c2397d6` before and after). Those are the images the cutover **replaces**; they are the
rollback target and must not be deleted before the rollback-retention rule is satisfied.

## 3. Third-party images — pinned by digest, carried under the dated R-A9E exception

Verified against the running production containers, read-only, on 2026-09-20.

| Service | Repository digest | Local image id | Fixable C/H | Disposition |
|---|---|---|---:|---|
| mongo1/2/3 | `mongo@sha256:ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6` | `sha256:7281281f68a3…` | 273 | EXCEPTED until 2026-10-15 |
| falkordb | `falkordb/falkordb@sha256:af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8` | `sha256:13ee9b3bfcc1…` | 148 | EXCEPTED until 2026-10-15 |
| qdrant | `qdrant/qdrant@sha256:05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae` | `sha256:449e32141460…` | 84 | EXCEPTED until 2026-10-15 |
| gateway (httpd) | `httpd@sha256:393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742` | `sha256:00fe3afeb8c3…` | 51 | EXCEPTED until 2026-10-15 — **remediated first**, the only image on the public path |
| redis | `redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99` | `sha256:487efc061638…` | **0** | clean, no exception |
| clamav | `clamav/clamav@sha256:71fbb76b397cd84a90043caf1178a7f81bd0c131a031e7b0619afd721fbfad41` | `sha256:6dc7ff3fabde…` | **0** | remediated to 1.4.6, no exception |

Total fixable CRITICAL/HIGH under exception: **556** (273 + 148 + 84 + 51). Exception expires
**2026-10-15**; remediation window **2026-10-06 → 2026-10-15**; post-cutover owner recorded in
`docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md`.

## 4. Service-set change the cutover introduces

Production runs **11 compose-managed service containers** today (14 containers in total: the
11 services, plus the three non-service containers named at the end of this section). The
release production compose declares three services
production does **not** currently run:

| Service | Profile | Starts at cutover? | Note |
|---|---|---|---|
| `document-worker` | none | **YES — new production service** | shares the backend image; the web tier must not also be an extraction worker (checklist section 16) |
| `document-worker-canary` | none | **YES — new production service** | shares the backend image |
| `graphiti` | `graph-experimental` | no | profile-gated, not started by default |

Exactly **one** scheduler owner must exist across every running service after startup. That
is a checklist section 16 assertion and it becomes materially harder with two more workers.

Residue observed on the production host on 2026-09-20 that is **not** a production service and
should be cleared before the window: `ra9bs-e2e` (a leftover R-A9B staging Playwright runner,
`node:20-bookworm`), `contraclaim-arbitration-audit-new`, `contraclaim-arbitration-audit-test`.

## 5. Content hashes — sha256 over LF-normalised file bytes

### Compose files

| File | sha256 |
|---|---|
| `docker-compose.prod.yml` | `e3240f056632eef17c0afd92cecd9f1d6222bc5b9048f8ab9d8c93de36384f9f` |
| `docker-compose.mongo-replicaset.yml` | `dc39c95f204db008f34b6e0bf32e84e461190be0b84ff714ca273591d4f0a51f` |
| `docker-compose.yml` | `d607bc04be870c078387fee1e68580e36c872a5df4470343fbb0b6d6dca1ba92` |
| `docker-compose.staging.yml` | `e6b0bf37904fcc633bfcf52aaf91953b2c75f70d6c14f008d62d3c4901b1d0d7` |

> **R-A9H: RETIRED 2026-09-20T21:40Z (R-A9G §13a).** The paragraph below is the frozen
> pre-cutover record.
>
> The temporary `docker-compose.clamav-r-a8z.yml` is **untracked** and lives only on the
> production host, so it has no hash here by design. Its render hash is recorded in the R-A8Z
> and R-A9C evidence (`8ab39508…`). It is retired at checklist section 13a, after the release
> stack is verified.

### Dockerfiles

| File | sha256 |
|---|---|
| `backend/Dockerfile` | `c62aa0ccecc1dad09b5398dc6c4be3e8b5b479fbfc308c1b74793d1781362fc2` |
| `client/Dockerfile` | `1dfae41e7577e19bdc306a0c7cae591bfe56d1669266d09faaaa4d8ebe1d8712` |

### Migration catalogue and the role-alignment operation

| Item | Hash | Notes |
|---|---|---|
| Migration catalogue (`backend/rbac_backend/migrations/`, 22 files) | `084a5dcd0836598d258d02921051102936cfd7de1a0999f83ea31e61754c1038` | rollup: sha256 over the sorted `sha256  path` lines |
| `backend/rbac_backend/migrations/catalog.py` | `45ef84ad709db3fc0f68aca4ff682426be67f9643c06d17cb778e8f53e5455bf` | 19 entries |
| Role-alignment operation (2 files) | `707347f3780d176fde1d72a515b50366d1e29ba924b0b5327a282da2e0356017` | **not** in the migration catalogue |
| `backend/rbac_backend/services/role_contract_alignment.py` | `6dd3c5bc0fad751849db1f70788f42d2b5ea88d0a49b1001940c4f503e474a74` | |
| `backend/rbac_backend/scripts/align_role_contract.py` | `1ccb9c2bdcbfe33c81bb00d42bb32e881d9e65d4e9b04835c120ccb8c1b0be0d` | |

### Procedure and disposition documents

**Re-hashed 2026-09-20 in release programme R-A9G-0.** Five of these documents changed when
the owner's decisions were recorded, and one was added. **Nothing about the release build
changed**: every path below is under `projectDMS/docs/`, which is in none of the three Docker
build contexts, so the image provenance and the Gate 3 b2 / Gate 4 b8 ticks are untouched. The
R-A9F values are kept beside the current ones so the change is visible rather than silent.

| Document | sha256 (current, R-A9G-0) | sha256 at R-A9F | Changed? |
|---|---|---|---|
| `docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md` | `18ae2fad5c0def3b7f5ce76115e53e714c4632ff0221657649fd3d4a31b682ca` | `a5b50a1d8acb09c0d044f7e8c86a01ba24b501803e1e19e9d95fb1f9dfd85656` | yes - owner acceptance recorded |
| `docs/PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` | `d47facbf9e5d18bba70a677d57ce252432de733e521f2b4e1e5f500454408372` | `4b697fa53508285334c10e06270df3676221e940d3337e3222100dce86892634` | yes - Variant A re-confirmed |
| `docs/PRODUCTION_CUTOVER_CHECKLIST.md` | `4f6b7642f189e652f1cdc779c95228b0dcc54f687112bf97d85be2ae91c186bf` | `9cd82014147d8e3cf9405fc4ddcc9972d1681d0eb0a581bd8a984216190e0238` | yes - preconditions and standing decisions |
| `docs/PRODUCTION_READINESS_RELEASE_GATE.md` | `9cdfd140fa2487539a3118679527de7a5c0882a85c6c173f6d67e82fb3889555` | `a38f9dee7bbd4ee36b48e6b52015e9b5741098f6404760124a7760604fb8614a` | yes - Gate 9 1/2/5/6 ticked, register disposed |
| `docs/R_A9F_OPEN_GATE_MATRIX.md` | `79ef030ce7b255b9715add09966b8ba33ac2bb44a63de640c50f273678800fe4` | `b905529fbc1a02236d23a27dd7812141244ec24c292d5c809cd1fed4fa00ffff` | yes - owner answers per row |
| `docs/R_A9G_OWNER_DECISION_RECORD.md` | `03ee5da6eeaeca65d34c239c4923ff67a0ca41b9b99487cb9132d25be3f4f61c` | - | **new in R-A9G-0** |
| `docs/R_A9G_CUTOVER_PLAN.md` | `da07a8c489e2102a361d6ccf7dd594f84ad1a8e114deb1baa0f75e6bf05b049c` | not previously hashed here | yes - preconditions 1-6 recorded |
| `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md` | `522bf14816f3829d5dfbae209a9f1d2a729af5c859d74242420e28751b97be3f` | same | **no - frozen** |
| `docs/GATE_4_B8_ORG_ADMIN_VALIDATION_MAPPING.md` | `1d7ff5c5f57414a0592c2941396ce2d0131f2342b35625058b4ba14d1864c2d9` | same | **no** |
| `docs/GATE_3_EVIDENCE_MATRIX.md` | `9f31e9e2755dde1b43c36213d0285bd417e4d1e8ecb0486862c725fcc5c5f82c` | same | **no** |
| `docs/CLAMAV_SIGNATURE_FRESHNESS.md` | `eae83f988f5c4085eb52eb8eae1d51bc7d9f7e60b0a1549b3c258ad946202f30` | same | **no** |

The frozen role-alignment diff is deliberately in the unchanged set: the owner's decision
confirmed it rather than altering it, so the production run stays a comparison against
bytes that have not moved since R-A9F.

> These hashes are taken at the moment this manifest is generated. The manifest is committed
> in the same commit as the documents it hashes, so they are the committed bytes — except for
> this file itself, which cannot hash itself.

## 6. Gate evidence manifest

| Item | Value |
|---|---|
| R-A9E evidence root | `/var/backups/contraclaim-stg-evidence/R-A9E-20260916T081258Z/candidate-fe728b2/` |
| Files sealed | **65** |
| `SHA256SUMS` sha256 | `26ee94123faa121bd093c482d17627d67ab20617a868dd2406b3db271fe8b76a` |
| Verify result | **0 non-OK lines** (re-verified on the server 2026-09-20 in R-A9F, read-only) |
| Segment A seal | `ac92b552a9c6245b` (50 files) |
| Segment B seal | `3e2c948af8bcd4e8` (60 files) |
| Secret scan | **CLEAN**, exit 0, 69 files scanned, 13 values hunted, 3–5 encodings each, 5 shape patterns |
| Local mirror | `C:\SaaS-fix-role-resolution-evidence\r-a9e\` |

## 7. Production migration manifest

Measured read-only against the live production database on **2026-09-20**
(database `contraclaim`).

| Item | Value |
|---|---:|
| Rows in production `schema_migrations` | **18** |
| Entries in the release catalogue | **19** |
| Catalogue entries already applied | **16** |
| Applied but **not in the catalogue** | **2** |
| Pending at cutover | **3** |
| Role-alignment operation | **separate**, not catalogued |

### Applied but not in the release catalogue — forward-only, never re-derived

| Version | Name | Applied |
|---|---|---|
| `20260730_0001` | `tenant_context_active_flags` | 2026-08-02 23:44:36 |
| `20260806_0001` | `subscription_current_scope_unique` | 2026-08-09 10:25:39 |

Neither migration exists on the release branch or on `contraclaim/main`. They were applied by
an earlier deployment whose code has since left the line. `plan()` iterates the *catalogue* and
asks the ledger only whether each entry is present, so before F-A8V-2 these rows appeared
nowhere in `--list`: the command reported 16 applied and 3 pending against a ledger holding 18
rows and never mentioned the other two. The runner's behaviour is unchanged and correct for a
forward-only runner — an unknown version is ignored for ordering and execution — but the rows
are now **reported**. Guard: `test_migration_runner.py::test_a_ledger_row_with_no_migration_in_the_catalogue_is_reported`.

**This database therefore carries schema effects from code this deployment does not have, and
nothing forward-only will re-derive them.** That is a standing fact, not a cutover blocker.

### Pending at cutover — the exact three, in order

| # | Version | Name | Why it matters |
|---|---|---|---|
| 1 | `20260814_0001` | `document_extraction_indexes` | page-evidence indexes (task 7.6); checklist section 17 asserts it |
| 2 | `20260820_0001` | `entity_document_links` | |
| 3 | `20260906_0001` | `permission_name_unique` | makes `permissions.name` unique. **Applied to staging only today.** Without it the startup seeder races itself. Checklist section 17 asserts `uq_permissions_name` present and 0 duplicates |

### Required order at cutover

1. `migrate_database --list` (inspect) — expect 16 applied / 3 pending / 2 orphan rows reported.
2. `migrate_database --fail-on-warning` **dry run**.
3. `migrate_database --apply --fail-on-warning`. **Exit 2 is expected on the first apply** and
   is computed after the migrations ran: read the JSON and confirm the only warnings are
   `20260721_0001`'s two known informational lines. **Any other warning stops the deploy.**
4. Re-apply for idempotency — expect **0** applied.

Then, and only then, the explicit role alignment, which `migrate_database` never runs:

5. `align_role_contract` — inspect.
6. `align_role_contract` — dry run (proves the plan is stable and still wrote nothing).
7. `align_role_contract --apply`.
8. `align_role_contract --apply` again — must be a **NOOP**, `second_apply_is_noop: true`.

The expected diff for steps 5–8 is frozen in `docs/R_A9F_ROLE_ALIGNMENT_CUTOVER_DIFF.md`:
`orgadmin` 54 → 135 (+82 canonical, −`billing.plan.manage`, 6 preserved), `projectadmin`
53 → 118 (+66, −`billing.plan.manage`, 4 preserved), every other role untouched, `db.users`
never written.

**There is no migration rollback.** `runner.py` never calls a downgrade. Recovery is
roll-forward: fix the cause and re-run `--apply`.

### Production census at freeze time (read-only, 2026-09-20)

| Collection | Count |
|---|---:|
| `permissions` | 263 |
| `roles` | 12 |
| `users` | 9 |
| `documents` | 172 |

The startup seeder is expected to take `permissions` **263 → 265** after the release starts;
assert the catalogue rather than trusting the seeder's log, which is best-effort.

## 8. FalkorDB state at freeze time

| Item | Value |
|---|---|
| Container | `contraclaim-falkordb-1`, id `de249ee3df2009c0ac12655cad95a9edc6d995d152d4632131d5fe74e5a712fc` |
| Started | 2026-09-15T06:02:51Z, 0 restarts |
| Persistence directory | **`/FalkorDB`** (release target: `/data`) |
| Graph | `contraclaim` — **156 nodes, 220 edges, 4 labels** (`Contract`, `ContractDocument`, `Clause`, `Letter`). *R-A9H correction: this row first said "7 labels", a count of `CALL db.labels()` output lines (header + 4 labels + 2 statistics lines)* |
| Rescue archive taken 2026-09-20 | `falkordb-persistence-20260920T110319Z-RA9E-hot.tar.gz`, sha256 `ebecc4cea8a273a597e256b1eb2bd6763d6608da7c5fb233b8e8c2c212c0c7d4`, 63,639 bytes |
| Archive validation | semantic, `redis-persistence` contract — `dump.rdb` + `appendonlydir/` at the canonical root |
| Disposable restore proof | restored engine came up `dir=/data`, `PONG`, graph `contraclaim`, **156 / 220 / 4 labels** (R-A9H correction of "7") — parity with production; 0 residual containers, volumes or networks |
| Cutover variant | **A** (owner decision, R-A9F) |

That archive proves the *procedure*. A **fresh** rescue archive must still be taken inside the
R-A9G window (`PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` section 4.2).

## 9. What this manifest deliberately does not contain

* A production release tag. Nothing has been tagged, because nothing has been deployed.
* A G32 step. G32 is **NOT RUN** and is not part of this cutover.
* Any instruction to rebuild an image.
