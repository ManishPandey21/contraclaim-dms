# Letter Bench — staging checks before production

**Status: NOT YET RUN ON STAGING.** This is the runbook for workstream W3 of
`docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md`. Nothing here has been
executed against a deployment. Section 6 is where results are recorded.

The Letter Bench must not be deployed to production, and
`release/contraclaim-rc1` must not be fast-forwarded to `main`, until checks A to
D below have passed on staging and are recorded.

## 1. What has and has not been measured

| Question | Measured? | Where |
|---|---|---|
| Do all 22 migrations apply to an empty MongoDB? | Yes, in a cloud development container, not staging | `docs/GATE_6_FRESH_INSTALL_EVIDENCE.md` |
| Do the three Bench migrations apply on top of a release-line database? | **Rehearsed only** (section 2) | this document |
| Do they apply on a copy of production data? | **No** | check B |
| Does the Bench work with the real language model? | **No.** Every test so far used a fake model | check C |
| Do clause citations resolve against real ingested contracts? | **No** | check C |
| Do real role and allocation data refuse the right users? | **No** | check C |
| Is the wording for the engineer and employer roles approved? | **No** | check D |

## 2. Rehearsal already done (a precondition, not evidence)

On 2026-11-15, in a cloud development container, against a disposable
`mongo:8.0` (one-member replica set):

| Step | Image | Result |
|---|---|---|
| `--apply --fail-on-warning` on an empty database | release line `614890f` | exit 0, 19 applied, 0 warnings |
| `--list` | `main` `48b1e4c` | 19 applied, 3 pending |
| dry run `--fail-on-warning` | `main` | exit 0, 3 `dry_run`, 19 skipped, 0 warnings |
| `--apply --fail-on-warning` | `main` | exit 0, **3 applied**, 19 skipped |
| `--apply` again | `main` | exit 0, 0 applied, 22 skipped |
| read back with `mongosh` | — | ledger 22 rows; three skills at 1.0.0 published; all 11 Bench indexes present, two partial unique |

This shows the three migrations sit cleanly on a database the release line
created. It does not show they sit cleanly on production's data: that database
was empty apart from what the migrations wrote. Check B is still required.

## 3. The checks

### A. Production has not had the three migrations

On the production host (read-only):

```bash
cd /opt/contraclaim-dms/projectDMS && bash scripts/post_cutover_readonly_checks.sh
```

Pass: section D of the report shows `20261114_0001`, `_0002` and `_0003` each as
unapplied. The production checkout was read on 2026-11-15 at `34d664a`
(2026-10-23), which predates those migrations, but the checkout is not the
ledger: this check is still required. If any is recorded as applied, stop and find out what applied it.

### B. Upgrade path on a production-derived staging copy

Follow `docs/GATE_6_UPGRADE_PATH_PROCEDURE.md` exactly, with the backend image
built from `main`. In outline:

1. stop every staging writer;
2. drop the staging database and prove it empty on `rsstg`;
3. restore a current production archive with every guard armed;
4. read the restored collection and document counts back;
5. with the application still stopped, inside a one-off staging backend
   container: `--list`, dry run `--fail-on-warning`, `--apply
   --fail-on-warning`, `--apply` again.

Pass criteria specific to the Bench:

- `--list` before the apply shows exactly the three `20261114_*` migrations
  pending (plus any production ledger rows reported as
  `applied_not_in_catalogue`, as in R-A8W);
- the dry run exits 0 with no warnings;
- the apply reports exactly 3 applied; the re-apply reports 0;
- `skills`, `skill_versions`, `skill_bindings`, `letter_position_boards` and
  `letter_skill_steps` did **not** exist in the restored copy before the apply.
  The unique indexes are created on those collections; if any already holds
  rows, record the count and stop;
- after the apply: three rows in `skill_versions`, each `1.0.0`, `published`,
  `owner_scope: system`; 11 Bench indexes, with
  `bench_board_document_unique` and `bench_board_trigger_unique` carrying their
  partial filter;
- no other collection's document count changed across the apply.

Then start the application (`up -d --no-deps backend contract-worker
document-worker`) and confirm `/health/ready` is 200.

### C. Real model, real documents, real roles

**Staging configuration.** On the staging backend only:

```text
LETTER_BENCH_ROLLOUT_MODE=canary
LETTER_BENCH_CANARY_TENANT_IDS=<the fixture organisation id>
```

Recreate the backend with `up -d --no-deps backend`.

**Fixtures to prepare by hand** (the suite does not create them, because the
point is that they are real):

1. A project in the fixture organisation whose contract has been uploaded and
   ingested, so its clauses are in the clause register.
2. An incoming letter uploaded through the normal document upload into that
   project, with OCR/extraction completed, that cites at least two clauses of
   that contract and makes at least one variation or time claim. **Use a new
   letter for every run**: opening the Bench is idempotent per document and
   skill steps are cached on their input, so a reused letter does not call the
   model. The suite refuses a document that already has a board.
3. Three accounts:
   - a drafter with drafting allocation in the project;
   - a user in the same organisation who can view documents but has no drafting
     allocation;
   - a user in a different organisation.

**Run** (from `projectDMS/client`, Node 20):

```bash
CONTRACLAIM_STAGING_E2E=1 \
E2E_BASE_URL=https://<staging host> \
E2E_PRODUCTION_HOSTS=contraclaim.com,www.contraclaim.com \
E2E_STAGING_ORG_ID=<fixture organisation id> \
E2E_STAGING_PROJECT_ID=<fixture project id> \
E2E_BENCH_DOCUMENT_ID=<the new letter's document id> \
E2E_BENCH_DRAFTER_EMAIL=... E2E_BENCH_DRAFTER_PASSWORD=... \
E2E_BENCH_UNALLOCATED_EMAIL=... E2E_BENCH_UNALLOCATED_PASSWORD=... \
E2E_BENCH_FOREIGN_EMAIL=... E2E_BENCH_FOREIGN_PASSWORD=... \
E2E_EVIDENCE_DIR=<a directory outside the checkout> \
npx playwright test e2e/staging/letter-bench-api.spec.ts --trace off
```

Supply the passwords from the environment or a secrets file, not on a shared
command line. `CONTRACLAIM_STAGING_E2E=1` makes a missing variable a failure
instead of a skip. The suite refuses to run against any host in
`E2E_PRODUCTION_HOSTS`.

**What it asserts** (`client/e2e/staging/letter-bench-api.spec.ts`, 11 tests):

- the Bench is on in `canary` mode for the fixture organisation;
- an anonymous caller gets 401; the unallocated user gets **403** on open, on
  read and on a write; the other-organisation user gets 403 or 404 on read and
  open, never 200;
- opening is idempotent;
- the analysis step is `completed`, **not degraded**, records a model name, and
  every assertion quotes text that is in the document;
- the three system skills are offered at 1.0.0 and their method text is not
  listed;
- each routed evaluation finishes `done` and not degraded;
- after deciding every position and locking, composing produces one non-empty,
  **not degraded** paragraph per assertion, a drafting run id, and at least one
  cited clause resolved to a contract source.

"Degraded" means the deterministic fallback wrote the text because the model
was unavailable or its output was rejected. Any degraded step fails the run: a
pass on fallback text would certify nothing about the model.

**Cost.** One analysis call, up to three evaluation calls
(`E2E_BENCH_MAX_EVALUATIONS`), and one drafting call per assertion. Model spend
on staging needs approval before the run.

**Then read the output as a person.** The suite proves the model answered and
the plumbing held. It cannot judge whether the analysis is right or the reply is
one you would sign. Open the letter on staging at `/letters` and review:

- are the assertions the ones a contracts engineer would pick out;
- is each proposed verdict defensible on the cited clauses;
- does the reply follow the three skill packs (structure, reservation of
  rights, no clause wording invented);
- are `[CONFIRM: ...]` placeholders used wherever the record was silent.

Record who reviewed it and the result in section 6.

**Residue.** The run leaves one letter and its board on staging. Delete the
fixture document and the letter afterwards if staging is to stay clean.

### D. Role overlay wording

The skill packs are written for the contractor. For the engineer and employer
roles the Bench adds a short overlay. Someone with authority over contractual
tone must read both overlays in
`backend/rbac_backend/services/skills/compiler.py` and approve or amend them.
Until then, keep the canary tenant on the contractor role.

## 4. Order, and what each step unlocks

1. A (production unapplied) and B (upgrade path) → the migrations may be applied
   to production in a window.
2. C and D on staging → the Bench may be switched to `canary` for one production
   tenant.
3. A canary period on production with reviewed output → `primary` may be
   considered. That decision is not covered by this document.

Deployment mechanics on production, until FalkorDB normalization is recorded as
done: recreate services only with `up -d --no-deps <service>`. The Bench's own
rollback is `LETTER_BENCH_ROLLOUT_MODE=off` followed by
`up -d --no-deps backend`; the three migrations are additive and need no
rollback to switch the feature off.

## 5. If a check fails

- **B fails on a unique index**: a Bench collection already holds rows. Do not
  drop them; record what they are.
- **C fails on `degraded`**: read `board.last_error` and the backend log for the
  model error. Check the model configuration on staging before changing code.
- **C fails on a 200 where a refusal was expected**: this is an authorization
  defect. Stop; do not proceed to production in any mode.
- **C fails on "no cited clause resolved"**: confirm the project's contract is
  ingested and projection-current before suspecting the Bench.

## 6. Results

| Check | Date (UTC) | Operator | Result | Evidence |
|---|---|---|---|---|
| A. Production unapplied | — | — | NOT YET RUN | — |
| B. Upgrade path on production copy | — | — | NOT YET RUN | — |
| C. Real model, documents, roles (suite) | — | — | NOT YET RUN | — |
| C. Human review of analysis and reply | — | — | NOT YET RUN | — |
| D. Role overlay wording | — | — | NOT YET APPROVED | — |
