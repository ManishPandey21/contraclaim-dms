# Branch / worktree / disk cleanup — 2026-10-13

Not committed. Repository and local-disk hygiene only. Production was not touched: no SSH,
no deploy, no container or data change. No PR was merged or closed.

Restore evidence: `C:\SaaS-cleanup-evidence\20261013-cleanup\` (`all_refs_before.txt` holds
every ref SHA before cleanup, plus `removed.txt`, `deleted_local.txt`, the inventories and the
remote delete plan). To restore a branch: `git branch <name> <sha-from-all_refs_before.txt>`.

## 1. Repository and remotes

| Item | Value |
|---|---|
| Git root | `C:\SaaS` (`projectDMS/.git` is stale and ignored) |
| Main checkout branch | `integrate/key-date-eot` @ `a179ca9e` (tree `6c97f77d`) |
| Main checkout status | dirty: 90 modified, 204 untracked, 0 staged. Untouched. |
| `contraclaim` | `https://github.com/ManishPandey21/contraclaim-dms.git` (fetch + push), default branch `main` |
| `origin` | `https://github.com/ManishPandey21/projectDMS.git` (fetch + push), default branch `main` |

Fetched with `git fetch --all --no-prune`. The `--prune=false` form in the brief is rejected by
git ("option `prune' takes no value").

## 2. Authoritative refs

| Ref | SHA | Tree |
|---|---|---|
| `contraclaim/release/contraclaim-rc1` (**release**) | `d7eef845e6e8` | `fbb00621` |
| `contraclaim/main` | `b2d50250` | `dce49a80` |
| `origin/main` | `50a0b477` | `6652811c` |
| `origin/release/contraclaim-rc1` | `1e89bf30` (stale: 55+ commits behind contraclaim's) | `c133968f` |
| local `release/contraclaim-rc1` | `2533b19d` (stale; left as is) | |
| local `main` | `99ab5224` (on the release lineage, not on either remote `main`; left as is) | |

Tags (3, untouched): `backup/contraclaim-main-20260708`, `backup/origin-main-20260708`,
`pre-integration-02c3217`.

## 3. Worktrees

Before: 40. After: 20.

### Removed (19), each re-verified immediately before removal: clean, HEAD on a remote, HEAD an ancestor of release (or of `origin/main`)

| Worktree | Branch / HEAD | PR |
|---|---|---|
| SaaS-canonical-evidence | feature/canonical-evidence-store `9ba90bc8` | #58 merged |
| SaaS-chronology-authority | fix/chronology-event-date-truthfulness `aedcd633` | #61 merged (#62 duplicate closed) |
| SaaS-cl4a-release-reconcile | reconcile/cl4a-onto-release-rc1 `4fcd012e` | #32 merged |
| SaaS-client-terminal-states | fix/client-terminal-processing-states `4cf45c60` | #57 merged |
| SaaS-dep-pydantic-ai | security/pydantic-ai-1.107.7 `6dc82446` | #53 merged |
| SaaS-dep-pymongo-r4 | security/pymongo-4.18.2-retire-r4 `b613d688` | #52 merged |
| SaaS-extraction-canary | fix/extraction-pagewise-canary `7641b476` | #51 merged |
| SaaS-fix-api-routing-env | fix/api-unified-routing-env `45b7f1cb` | #63 merged |
| SaaS-fix-processing-status | fix/document-processing-status-route `4abfc487` | #64 merged |
| SaaS-numeric-integrity | fix/numeric-integrity-native-and-ocr `d0b254db` | #54 merged |
| SaaS-quality-gate-tables | fix/quality-gate-table-checkability `4c2fcf76` | #55 merged |
| SaaS-sec-pymongo | security/pymongo-4.18.2 `d04ace6d` (= #42 merge commit) | reachable |
| SaaS-sec-pymongo-exc | security/pymongo-temporary-cve-exception `3625c626` | #47 merged |
| SaaS-sec-r3 | security/r3-braces-renewal `6d0091bd` | #50 merged |
| SaaS-sec-sourcemap | detached `7f304712` (= #48 merge commit) | #48 merged |
| SaaS-status-truthfulness | fix/document-status-truthfulness `6675a568` | #56 merged |
| SaaS-release-tools\bisect | detached `130a5a8a` | reachable; parent `C:\SaaS-release-tools` (evidence logs) kept |
| SaaS\.codex-wt-restore-contraclaim | codex/restore-known-good-contraclaim `ad781b4b` | reachable |
| SaaS\.codex-wt-restore-origin | codex/restore-known-good-origin `bb83c692` | ancestor of `origin/main` |

Safety steps taken:
- Ignored files were checked first. Only empty `bulk_upload.log` files, Playwright
  `test-results`, and in `bisect` the `.env` / `backend/.env` / `config/secrets/qdrant_api_key`,
  whose `.env` hashes match the `C:\SaaS\projectDMS` copies.
- Two worktrees had `client\node_modules` junctions. `SaaS-chronology-authority` pointed into
  the **main checkout** (`C:\SaaS\projectDMS\client\node_modules`), and `SaaS-cl4a-release-reconcile`
  pointed at the non-existent `C:\SaaS-cl4a`. Each junction was unlinked itself with
  `[System.IO.Directory]::Delete(path, $false)` before `git worktree remove`. The main checkout's
  node_modules was verified intact afterwards.
- No `--force` was used.

### Partial: `C:\SaaS\projectDMS\.kilo\worktrees\malachite-peak`
`git worktree remove` failed with "Permission denied", most likely a file locked by the Kilo
extension. The git metadata is gone, but the directory remains: about 1,400 files, all tracked
content of `520ac682`, which is on both remotes. **Owner:** close the Kilo/VS Code session that
holds it, then delete the folder.

### Retained (20)

| Worktree | Why |
|---|---|
| `C:\SaaS` | main checkout, dirty (294 entries), 2 local-only commits |
| SaaS-release-1.1.1 | active Release 1.1.1 session (peer confirmed) |
| SaaS-release-1.1-docs | 1 unpushed docs commit `dbdc470e` (peer asked to keep) |
| SaaS-release-rc1 | 6 unpushed docs commits on `docs/ocr-canonical-release-certification` |
| SaaS-feat-all-scope | PR #45 open |
| SaaS-fix-link-modal-layout | PR #44 open |
| SaaS-fix-tags-ratelimit | merged, but its `node_modules` is the junction target of SaaS-feat-all-scope; removing it would break PR #45's worktree |
| SaaS-fix-reference-integrity | dirty (8 modified): uncommitted reference-sync degraded-preservation work |
| SaaS-hotfix-target-letter | dirty (2 modified, 1 untracked), detached at `b30003ad` |
| SaaS-ocr-bufsize-memory | 1 untracked benchmark file |
| SaaS-fix-backoff, -fix-bulk-csv-blank-cells, -fix-bulk-ocr-flag | local-only commit each |
| SaaS-fix-corr-qdrant, -fix-corr-prompt, -fix-corr-source-authority | local-only Phase B chain |
| SaaS-integ-pr38-41 | detached, 4 local-only commits |
| SaaS-prod-integration-20261001 / -20261002 | 9 / 35 local-only commits (patch-equivalent to release, see §10) |
| SaaS-verify-combo | PR #49 closed-unmerged (classified F) |

Not worktrees, untouched: `C:\SaaS_publish`, `SaaS-canary-local`, `SaaS-cleanup-evidence`,
`SaaS-fix-role-resolution-evidence`, `SaaS-release-cert-2533b19`, `SaaS-release-cert-d7eef845`
(staging cert harness, peer asked to keep), `SaaS-release-tools`, `SaaS-staging-cert`.

## 4. Local branches

Before: 95. After: 38. All 57 deletions used `git branch -d`, run from the `SaaS-release-1.1.1`
worktree (HEAD = release tip). That avoids the trap where `-d` compares to the main checkout's
non-release HEAD. Zero refusals. Each branch was first proven an ancestor of release (42) or of
`origin/main` (15). Full list with SHAs: `deleted_local.txt`.

Deleted groups:
- **Merged PR branches:** canonical-evidence-store, client-terminal-processing-states,
  pydantic-ai, pymongo-retire-r4, extraction-pagewise-canary, api-unified-routing-env,
  document-processing-status-route, numeric-integrity, quality-gate-table-checkability,
  pymongo-4.18.2, pymongo-temporary-cve-exception, r3-braces-renewal, document-status-truthfulness,
  chronology-event-date-truthfulness, chronology-verification-authority,
  chronology-foundation-design, cl1-relationship-hardening, hindrance-constraint-register,
  reconcile/cl4a-onto-release-rc1, pr33-update, source-map-js-1.2.2, Frontend.
- **July–August `codex/*-contraclaim*` sync branches (21):** ancestors of release and contraclaim/main.
- **July–August `codex/*-origin*` sync branches (15):** ancestors of `origin/main`.

### Classification of the 38 that remain

| Branch | Class | Notes |
|---|---|---|
| main, release/contraclaim-rc1 | A PROTECTED | both stale vs remote; not reset |
| feat/release-1.1.1-extraction-review | A PROTECTED | active session |
| integrate/key-date-eot | C DIRTY_WORKTREE | main checkout; 2 local-only commits are patch-equivalent in release (cherry 0) |
| feat/explicit-all-scope-selection | B ACTIVE_OPEN_PR | #45 draft |
| fix/variation-link-modal-layout | B ACTIVE_OPEN_PR | #44 |
| feat/cl2-…, feat/cl3a-…, feat/cl3b-…, feat/cl4a-… | B ACTIVE_OPEN_PR | content fully in release (via #32); PRs #26/#27/#29/#30 still open drafts |
| codex/arbitration-llm-drafting-p3 | B ACTIVE_OPEN_PR | #19 open, **base `main`**, conflicting |
| codex/reference-linking-workflow-contraclaim / -projectdms | B ACTIVE_OPEN_PR | #18 draft, **base `main`**, conflicting |
| fix/tags-rate-limit-budgets | E (merged) but retained | junction target for SaaS-feat-all-scope |
| fix/reference-sync-degraded-preservation | C DIRTY_WORKTREE | head in release; uncommitted edits |
| measure/ocrmypdf-bufsize-memory | C DIRTY_WORKTREE | 1 untracked file |
| docs/ocr-canonical-release-certification | D LOCAL_ONLY | 6 docs commits |
| docs/release-1.1-certification | D LOCAL_ONLY | 1 docs commit |
| fix/bulk-csv-blank-cells, fix/bulk-ocr-enabled-parsing, fix/bulk-upload-backoff-dependency | D LOCAL_ONLY | 1 commit each |
| fix/correspondence-qdrant-contract, -summary-reply-points, -source-authority, local/phase-b-pr35-pr36-base | D LOCAL_ONLY | Phase B stack |
| integrate/prod-20261001, integrate/prod-20261002, checkpoint/prod-20261002-eed4999, replay/dryrun-20261003, replay/dryrun-pr33 | D / G | local-only; every commit patch-equivalent to release (cherry 0) |
| codex/csv-import-scope | D (1 ahead of its remote) | 92 unique, 39 not patch-present |
| codex/landing-contraclaim-sync-20260710 | D | 1 local commit, patch-equivalent (cherry 0) |
| verify/r4-plus-source-map-js | F CLOSED_UNMERGED | #49 verification-only |
| hotfix/security-deps-20261001 | F | no PR; patch-equivalent to #37 (cherry 0) |
| feat/letter-deletion-cascade | H UNKNOWN | 3 unique, 1 not patch-present |
| backup/contract-master-v1-pre-cl4a, backup/feat-all-scope-pre-90418258 | G ARCHIVE | |
| Backend | G | 2025 "Create App.jsx"; upstream gone |

## 5. Remote branches (`contraclaim`)

Before: 46 (plus HEAD). After: 46. **No remote branch was deleted.** The auto-mode permission
classifier blocked `git push contraclaim --delete` as a destructive git action, and that was not
worked around.

26 branches are proven safe to delete. For each, the remote head equals the merged PR's head
SHA, it is an ancestor of release, it is the base of no open PR, and no non-audit file in release
names it (`remote_delete_plan.tsv`):

```
docs/chronology-foundation-design (#59)  feature/canonical-evidence-store (#58)
fix/ai-tenant-isolation (#35)  fix/api-unified-routing-env (#63)
fix/chronology-event-date-truthfulness (#61)  fix/chronology-verification-authority (#60)
fix/client-terminal-processing-states (#57)  fix/correspondence-metadata-integrity (#36)
fix/document-processing-status-route (#64)  fix/document-status-truthfulness (#56)
fix/extraction-pagewise-canary (#51)  fix/numeric-integrity-native-and-ocr (#54)
fix/quality-gate-table-checkability (#55)  fix/security-dependency-refresh-20261001 (#37)
fix/tags-rate-limit-budgets (#33)  hotfix/deep-planning-document-scope (#43)
hotfix/deep-planning-target-letter-scope (#42)  hotfix/deep-planning-tenant-isolation (#41)
hotfix/letters-tenant-isolation (#38)  hotfix/scope-builders-fail-closed (#40)
hotfix/ws-token-validation (#39)  security/pydantic-ai-1.107.7 (#53)
security/pymongo-4.18.2-retire-r4 (#52)  security/pymongo-temporary-cve-exception (#47)
security/r3-braces-renewal (#50)  security/source-map-js-1.2.2 (#48)
```

Retained on purpose:
- `release/contraclaim-rc1`, `main`, `backup/*`.
- `feat/cl1-relationship-hardening`: base of open #26; deleting it would auto-close #26.
- `feat/cl2/cl3a/cl3b/cl4a`: open PRs.
- `feat/hindrance-constraint-register`: named in `docs/HINDRANCE_STAGING_CERTIFICATION_PLAN.md`.
- `fix/contract-master-reprojection-runtime`: named in a debt document.
- `reconcile/cl4a-onto-release-rc1`: CL-4A evidence candidate.
- `integrate/key-date-eot`: main checkout lineage.
- `verify/*`, `hotfix/security-deps-20261001`, `codex/*`, `feat/letter-deletion-cascade`,
  `feat/explicit-all-scope-selection`, `fix/variation-link-modal-layout`.

### Older `origin` (projectDMS): recommendations only
It mirrors most contraclaim feature branches. The same 26 names, where present on `origin`, are
merged by content. Also merged: `codex/production-readiness-phase-0` and
`feature/security-billing-ops-weeks-1-4` (ancestors of `origin/main`). `origin/release/contraclaim-rc1`
is stale at `1e89bf30`. Recommendation: decide whether `origin` stays a mirror. If it does,
fast-forward its release; if not, archive the repo rather than pruning it branch by branch.

## 6. GitHub PR state (contraclaim-dms)

| PR | Head → base | State | Mergeable / CI | Action |
|---|---|---|---|---|
| #45 | feat/explicit-all-scope-selection → release | open draft | mergeable; **4 failed / 1 skipped**, 44 behind release | owner: rerun CI (memory: Actions billing block on 2026-10-07), update onto release |
| #44 | fix/variation-link-modal-layout → release | open | clean, 5/5 green but 55 behind release | update onto release, re-run CI, review, merge |
| #30, #29, #27, #26 | CL-4A/3B/3A/2 stack | open drafts | 5/5 green; **all content already in release via #32** | close as superseded, then delete branches |
| #20 | release → **main** | open draft | clean 5/5 | long-lived release→main PR; owner decision on when main catches up |
| #19 | codex/arbitration-llm-drafting-p3 → **main** | open | conflicting | base is `main`, not release: rebase onto release or close |
| #18 | codex/reference-linking-workflow → **main** | open draft | conflicting | July work, likely superseded by the reference-sync fixes; close or rebase |
| #49 | verify/r4-plus-source-map-js | closed unmerged | | verification-only; delete branch later |
| #62 | fix/chronology-event-date-truthfulness → main | closed unmerged | | duplicate of #61 (merged) |
| #1–#17 | dependabot | closed | | branches already gone |

projectDMS repo: #19 and #18 are mirrors of the same work, open against `main`.

## 7. Deleted merged branches
57 local (§4). 0 remote (blocked, §5).

## 8. Protected branches
main, release/contraclaim-rc1, feat/release-1.1.1-extraction-review, integrate/key-date-eot,
feat/explicit-all-scope-selection, fix/variation-link-modal-layout, feat/cl2/cl3a/cl3b/cl4a,
codex/arbitration-llm-drafting-p3, codex/reference-linking-workflow-*, fix/tags-rate-limit-budgets,
fix/reference-sync-degraded-preservation, measure/ocrmypdf-bufsize-memory, and every D/F/G/H
branch in §4.

## 9. Local-only branches and commits
On no remote: docs/ocr-canonical-release-certification (6), docs/release-1.1-certification (1),
fix/bulk-csv-blank-cells (1), fix/bulk-ocr-enabled-parsing (1), fix/bulk-upload-backoff-dependency (1),
fix/correspondence-qdrant-contract (2), fix/correspondence-summary-reply-points (3),
fix/correspondence-source-authority (4), local/phase-b-pr35-pr36-base (1, "do not push"),
integrate/key-date-eot (2), integrate/prod-20261001 (9), integrate/prod-20261002 (35),
checkpoint/prod-20261002-eed4999 (13), replay/dryrun-20261003 (36), replay/dryrun-pr33 (4),
codex/csv-import-scope (1 ahead), codex/landing-contraclaim-sync-20260710 (1),
backup/feat-all-scope-pre-90418258 (1), Backend (1). Detached worktree SaaS-integ-pr38-41 (4).

## 10. Closed-unmerged and local-only: recommendations

| Branch | Purpose | Not patch-present in release | Recommendation |
|---|---|---|---|
| docs/ocr-canonical-release-certification | Release 1.0 certification record | 6 / 6 | REBASE/UPDATE THEN PR (docs) |
| docs/release-1.1-certification | Release 1.1 cert + canary runbook fix | 1 / 1 | REBASE/UPDATE THEN PR (docs); peer owns it |
| fix/correspondence-qdrant-contract → summary-reply-points → source-authority | Phase B Qdrant contract, prompt v4, source authority | 1, +1, +2 | NEEDS OWNER DECISION: rebase onto release minus the local base `9a071c5d`, then 1–3 PRs |
| local/phase-b-pr35-pr36-base | local base for the above | 0 | SUPERSEDED — delete after the Phase B stack is rebased |
| fix/bulk-csv-blank-cells | preserve blank CSV cells | 1 | REBASE/UPDATE THEN PR (check against the CSV pin) |
| fix/bulk-ocr-enabled-parsing | ocr_enabled=false in bulk CSV | 0 (equivalent in release) | SUPERSEDED — DELETE LATER |
| fix/bulk-upload-backoff-dependency | declare `backoff` | 1 | NEEDS OWNER DECISION (owner-accepted debt in rc1) |
| hotfix/security-deps-20261001 | PyJWT/urllib3 | 0 (= #37) | SUPERSEDED — DELETE LATER (local + remote) |
| verify/r4-plus-source-map-js | CI proof only | 0 | SUPERSEDED — DELETE LATER |
| integrate/prod-*, checkpoint/prod-*, replay/* | integration rehearsals | 0 | ARCHIVE (tag if wanted) then delete |
| codex/arbitration-llm-drafting-p3, codex/csv-import-scope | RBAC navbar/role-alias single source, CSV scope | 36 / 39 | NEEDS OWNER DECISION: rebase onto release, or close #19 |
| codex/reference-linking-workflow-* | idempotent reference linking (July) | 1 | NEEDS OWNER DECISION (likely superseded) |
| feat/letter-deletion-cascade | letter deletion cascade | 1 | NEEDS OWNER DECISION: compare with release's cascade |
| codex/landing-contraclaim-sync-20260710 | landing copy | 0 | SUPERSEDED — DELETE LATER |
| backup/* | pre-rebase backups | | ARCHIVE |
| Backend | 2025 scaffold | | SUPERSEDED — DELETE LATER |

## 11. Open PR merge plan (nothing merged)

| Order | PR | Priority | Action | Update? | CI? | Owner approval |
|---|---|---|---|---|---|---|
| 1 | #44 | P2 | merge release in, rerun CI, review | yes (55 behind) | yes | yes |
| 2 | #26/#27/#29/#30 | P4 | close as superseded by #32 | no | no | yes |
| 3 | #45 | P3 (scope / security-adjacent) | fix CI (billing), update onto release, review | yes (44 behind) | yes | yes |
| 4 | #18, #19 | P4 / owner | close, or rebase onto release (base is `main`) | yes | yes | yes |
| — | #20 | n/a | keep as release→main tracker | | | yes |

## 12. Docker

### Follow-up, same day, owner-approved
PRs #26, #27, #29 and #30 were closed as superseded, each with a comment citing #32 and release
`d7eef845`. Their branches were kept.

Docker Desktop 4.87 was started once the Temp and npm deletions had made room. Its restart
policy auto-started the `contract-ai` stack (redis, qdrant, falkordb); that stack is local and is
not production.

Before: images 14.66 GB (26), containers 1.20 GB (29), volumes 8.75 GB (60), build cache 7.71 GB.

Removed (IDs in the evidence folder: `docker_volumes_removed.txt`, `docker_images_removed.txt`):
- **42 anonymous, unattached volumes.** They carry no labels and no compose project, and were
  created in pairs between 2026-10-01 and 2026-10-11. That matches the `/data/db` +
  `/data/configdb` pair of throwaway `mongo:8.0` test containers. Removed by explicit ID, not
  with `docker volume prune`. About 5.0 GB.
- **6 unused images:**
  - `projectdms-backend:38992d6e…` and `projectdms-client:38992d6e…`, the 2026-10-02
    integration rehearsal, which is patch-equivalent to release;
  - `projectdms-client:pr37-npmfix`;
  - `projectdms-backend:hotfixb281`, from the superseded hotfix/security-deps;
  - `projectdms-backend:deps2e56`;
  - `aquasec/trivy:0.69.3`, superseded by 0.70.0.

  About 4.0 GB.
- **Dangling build cache** (`docker builder prune -f`): 2.66 GB.

After: images 10.65 GB (20), volumes 3.79 GB (18), build cache 5.06 GB (60 entries, of which
35 MB is dangling). Space freed inside the VHDX: about 11.7 GB.

Kept on purpose:
- Both certification stacks: `ccert-d7eef845` is the Release 1.1 certification and the peer asked
  to keep it; `ccert-2533b19` holds the Release 1.0 certified images, which are possible rollback
  images. Kept with their containers, images and named volumes.
- The `vanshavali` stack and images (another project).
- `contract-ai` and its volumes, including the unattached named `contract-ai_mongo_data`.
- `ccert-trivy-cache` (1.6 GB, the certification harness's Trivy DB).
- `cert-node20` (stopped, 840 MB writable layer).
- `python:3.12` (1.6 GB, unused, can be pulled again).

**The VHDX has not shrunk.** `docker_data.vhdx` is still 35.8 GB and is not sparse. Compaction
needs an elevated shell, which this session does not have. The auto-mode classifier also blocked
an in-VM `fstrim` (a privileged container). Owner procedure, from an **Administrator** PowerShell:
1. Quit Docker Desktop.
2. `wsl --shutdown`
3. `diskpart`, then:
   - `select vdisk file="C:\Users\santo\AppData\Local\Docker\wsl\disk\docker_data.vhdx"`
   - `attach vdisk readonly`
   - `compact vdisk`
   - `detach vdisk`
   - `exit`

Optional further reclaim, owner decision: `docker builder prune --all` (5 GB of build cache
referenced by the certification builds; it can be rebuilt), `cert-node20`, `python:3.12`,
`ccert-trivy-cache`.

## 13. Caches (measured only, nothing deleted)

| Item | Size | Classification |
|---|---|---|
| `%LOCALAPPDATA%\Temp\tn1odmfj` (VS / MSBuild installer payload, 2026-10-09) | 2.0 GB | candidate, owner (confirm no VS install pending) |
| `%LOCALAPPDATA%\Temp\claude` (session scratchpads) | 1.9 GB | keep while peer sessions are live |
| `%LOCALAPPDATA%\Temp\DiagOutputDir\RdClientAutoTrace` | 1.4 GB | safe candidate (RDP client traces) |
| `%LOCALAPPDATA%\Temp\*\swap.vhdx` (2 files, 10-09 / 10-10) | 1.5 GB | safe candidate while WSL is stopped |
| `%LOCALAPPDATA%\Temp\DockerDesktopUpdates` | 0.3 GB | candidate |
| npm cache | 0.86 GB | safe (regenerable); avoid while a peer runs npm |
| uv cache | 2.7 GB apparent (mostly hardlinks, about 1 GB real) | low value |
| ms-playwright | 1.1 GB | keep (e2e) |
| pip cache | 0.03 GB | negligible |
| main backend `.venv` / client node_modules | 0.81 / 0.30 GB | keep |
| `C:\SaaS-release-tools` | 0.8 GB | evidence logs, keep |

## 14. Disk space

| Point | Free on C: |
|---|---|
| Session start | 5.5 GB |
| Before deletions (during audit) | 0.11 GB: the pagefile grew from 11.2 GB to 14.0 GB under memory pressure |
| After worktree removal | 1.70 GB |
| After branch deletion | about 1.6 GB (branch refs are bytes; the movement is pagefile and peer activity) |
| End of first pass | 1.58 GB (pagefile 12.8 GB) |
| After deleting the approved Temp items (VS payload, RDP traces, 2 WSL swap.vhdx) | 2.44 GB, then further gains |
| After `npm cache clean --force` | 8.82 GB |
| After starting Docker and pruning inside the VHDX | 6.12 GB: Docker running pushed the pagefile back to 14.0 GB, and the VHDX is not compacted yet |

Measured worktree reclaim: about 1.6 GB. Git object store: 71 MB packed + 9 MB loose; `git gc`
would reclaim almost nothing and was not run.

## 15. Remaining risks
- **Disk is still critical (about 1.6 GB).** Pagefile growth alone consumed 2.8 GB in minutes. Tools
  hang near 0 (see the 2026-09-26 cleanup). The biggest lever is the 36 GB Docker VHDX (prune, then
  compact), followed by the Temp candidates (about 5 GB).
- The 26 merged remote branches remain until the owner runs the delete.
- The `malachite-peak` leftover folder is locked.
- Local `main` and local `release/contraclaim-rc1` are stale pointers; `origin/release` is stale.
- PR #45 CI is red, most likely Actions billing.

## 16. Balance plan

| Order | Branch / PR | Type | Why it remains | Diff | Dependency | Risk | Tests | Action | Target release |
|---|---|---|---|---|---|---|---|---|---|
| 1 | feat/release-1.1.1-extraction-review | feature | active | 0 so far | release `d7eef845` | med | full suite + e2e | continue in peer session | Release 1.1.1 |
| 2 | docs/release-1.1-certification | docs | unpushed | 3 files | none | low | none | push, PR to release | Release 1.1.1 (docs) |
| 3 | docs/ocr-canonical-release-certification | docs | unpushed | 1 file | none | low | none | push, PR to release | Release 1.0 record |
| 4 | #44 variation link modal | fix | open | 4 files | update onto release | low | vitest + CI | update, review, merge | ask owner |
| 5 | fix/reference-sync-degraded-preservation (dirty) | fix | uncommitted | 8 files | none | med | backend suite | commit + PR, or discard: owner | ask owner |
| 6 | Phase B stack (qdrant contract, prompt v4, source authority) | fix / feature | local only | 33–64 files vs old base | rebase off `9a071c5d` | high | backend + golden QA | rebase, split PRs | ask owner (Release 1.2?) |
| 7 | fix/bulk-csv-blank-cells, fix/bulk-upload-backoff-dependency | fix | local only | 2–3 files | none | low | bulk-upload tests | rebase + PR | ask owner (Release 1.2?) |
| 8 | #45 explicit ALL scope | feature (scope) | draft, red CI | 10 files | CI billing | med–high (authz) | RBAC suites + e2e | fix CI, update, review | ask owner |
| 9 | #26/#27/#29/#30 | stale PRs | content merged | 0 | none | none | none | close | n/a |
| 10 | #19 / codex/csv-import-scope | feature (RBAC) | base main, conflicting | ~400 files | rebase | high | full | owner: rebase selected commits or close | later architecture |
| 11 | #18, feat/letter-deletion-cascade | feature | old July work | 12–19 files | compare to release | med | targeted | verify superseded, then close | ask owner |
| 12 | integrate/*, replay/*, checkpoint/*, verify/*, hotfix/security-deps, backup/*, Backend, landing-sync, bulk-ocr-enabled | archive | superseded | 0 net | none | none | none | tag if wanted, then `-D` with owner approval | n/a |

Chronology A0c / D5 / D6, human-review UI, metadata / SMTP: no branches exist yet. Target releases
are unconfirmed; owner to assign.
