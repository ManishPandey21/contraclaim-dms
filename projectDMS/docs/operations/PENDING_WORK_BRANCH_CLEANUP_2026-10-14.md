# Pending work, branch consolidation and space cleanup — 2026-10-14

Scope: repository cleanup and backlog consolidation only. No production, canary,
deployment, `.env`, container or datastore change was made. Not committed.

## 0. Re-established state

| Item | Value |
|---|---|
| Git root | `C:\SaaS` |
| Main checkout branch / HEAD | `integrate/key-date-eot` @ `a179ca9e` (tree `6c97f77d`), dirty: 90 modified, 203 untracked — protected |
| Remotes | `contraclaim` = ManishPandey21/contraclaim-dms (authoritative), `origin` = ManishPandey21/projectDMS (older) |
| Default branch (both) | `main` — the two `main`s have diverged (`contraclaim/main` b2d50250, `origin/main` 50a0b477) |
| Release | `contraclaim/release/contraclaim-rc1` = `b98c1f487b31f7963e1b877c475ece7e4b721487`, tree `87e3cf936e021cd66220419baedaae1779b4e903` — matches the expected values |
| `origin/release/contraclaim-rc1` | `1e89bf30` (stale mirror, ancestor of release) |
| Local `release/contraclaim-rc1` | `2533b19d` (stale, ancestor of release; left alone) |
| Fetch | `git fetch --all --no-prune` (`--prune=false` is not valid syntax) |
| Release CI | tip run 38384940714 red: "Run frontend tests" — 665/665 tests pass, job fails on one unhandled `ReferenceError: window is not defined` from a TipTap editor destroy timer firing after jsdom teardown. The earlier run on the same release (10:15Z) was green. Flake, not a product defect. |
| Docker | daemon unresponsive: `docker system df` did not return within 300 s. Phase 14 could not be measured. |
| Peer sessions consulted | Release 1.1 canary (uses `C:\SaaS-release-1.1-docs` and `C:\SaaS-release-cert-d7eef845` — do not touch), Release 1.1.1 deployment (approved removing `C:\SaaS-release-1.1.1`; keep `C:\SaaS-release-cert-1.1.1`) |

Method: a branch counts as merged only if its head is an ancestor of the release.
Superseded means not an ancestor but proven by `git cherry` (patch-equivalent
commits in release), byte comparison of the files it added, or `git range-diff`.

## 1. What was done

| Action | Items | Proof |
|---|---|---|
| Worktree removed | `C:\SaaS-release-1.1.1` | branch head 34b244aa is an ancestor of release (#66 merged); clean; peer approved; its `client\node_modules` junction to `C:\SaaS` was deleted first (target still has 466 entries) |
| Worktree removed | `C:\SaaS-verify-combo` | `verify/r4-plus-source-map-js`, PR #49 closed "verification only, do not merge"; #47/#48 merged, #52 retired R4; `git cherry` = 0 |
| Worktree removed | `C:\SaaS-prod-integration-20261001`, `C:\SaaS-prod-integration-20261002` | `git cherry` = 0 for both (every commit patch-equivalent in release); clean |
| Worktree removed | `C:\SaaS-integ-pr38-41` | detached trial merges of #38–#41, all merged into release; clean |
| Worktree removed | `C:\SaaS-fix-bulk-ocr-flag` | `git cherry` = 0; its only test file is byte-identical in release; clean |
| Local branch deleted (`-d`) | `feat/cl2-variation-correspondence-links`, `feat/cl3a-hindrance-scope-integration`, `feat/cl3b-programme-chronology-links`, `feat/cl4a-core-active-project-scope`, `feat/release-1.1.1-extraction-review`, `feat/release-1.1.1-extraction-review-ui` | each head is an ancestor of release; `-d` verified against the upstream (release where no upstream existed) |
| Local branch deleted (`-d`) | `verify/r4-plus-source-map-js`, `hotfix/security-deps-20261001` | `-d` accepted (equal to their upstream); `git cherry` vs release = 0 (PyJWT/urllib3 refresh landed via #37) |
| npm cache cleared | `%LOCALAPPDATA%\npm-cache` (0.44 GB) | disposable download cache |
| Refused / stopped | `-d` refused for 9 superseded branches (see Owner decisions); `uv cache prune` failed (cache locked by another process); remote deletion denied by the auto-mode permission classifier | no forcing attempted |

## 2. Worktree inventory (after)

| Worktree | GB | Branch @ head | State | Bucket | Note |
|---|---|---|---|---|---|
| `C:\SaaS` | 6.95 (venv 3.28, node_modules 0.30) | `integrate/key-date-eot` @ a179ca9e | dirty 90 M / 203 ?? ; 89 commits ahead of `contraclaim/main` | G — protect | main checkout; the venv is the only backend test interpreter |
| `C:\SaaS-fix-tags-ratelimit` | 0.36 (node_modules 0.30) | `fix/tags-rate-limit-budgets` @ 816ad8e4 | clean, merged | A — blocked | its `client\node_modules` is the junction target of `C:\SaaS-feat-all-scope` |
| `C:\SaaS-fix-link-modal-layout` | 0.34 | `fix/variation-link-modal-layout` @ 7a9dcaa3 | clean, pushed, PR #44 | C | CI 5/5 green (2026-10-05), merges clean, 64 behind |
| `C:\SaaS-fix-reference-integrity` | 0.22 | `fix/reference-sync-degraded-preservation` @ 447d1822 (merged) | 8 modified + 1 added, **all byte-identical to release** | B — dirty | needs `--force` to remove → owner |
| `C:\SaaS-hotfix-target-letter` | 0.03 | detached @ b30003ad (#41, merged) | 2 modified (identical to release), 1 untracked test that **differs** from release's version | G | likely an earlier draft of #42's test; owner look |
| `C:\SaaS-ocr-bufsize-memory` | 0.03 | `measure/ocrmypdf-bufsize-memory` @ 99ab5224 (= local main, merged) | untracked `backend/tools/benchmarks/` (Dockerfile + measurement script) not in release | E | the BUFSIZ RSS measurement debt; preserve the script |
| `C:\SaaS-fix-backoff` | 0.05 | `fix/bulk-upload-backoff-dependency` @ 221ca1ed | local-only commit, clean | C | release still lacks `backoff` in requirements; merges clean |
| `C:\SaaS-fix-bulk-csv-blank-cells` | 0.05 | `fix/bulk-csv-blank-cells` @ fd2d7be0 | local-only, clean | B | release carries the newer combined version (`test_boolean_columns_follow_the_combined_contract`) |
| `C:\SaaS-fix-corr-qdrant` | 0.03 | `fix/correspondence-qdrant-contract` @ ef6a68f6 | local-only | B | 7 of 9 added files byte-identical in release, other 2 present and evolved |
| `C:\SaaS-fix-corr-prompt` | 0.04 | `fix/correspondence-summary-reply-points` @ 44cb662f | local-only | B | its added test is byte-identical in release |
| `C:\SaaS-fix-corr-source-authority` | 0.06 | `fix/correspondence-source-authority` @ 5e4ab0aa | local-only | B | `source_text.py` and tests byte-identical in release |
| `C:\SaaS-release-1.1-docs` | 0.03 | `docs/release-1.1-certification` @ dbdc470e | local-only commit, clean | C — protect | in use by canary session; push it |
| `C:\SaaS-release-rc1` | 0.06 | `docs/ocr-canonical-release-certification` @ addfd33e | 6 local-only commits (574-line certification record), clean | C — protect | push / PR it; worktree name is misleading |
| `C:\SaaS-feat-all-scope` | 0.05 | `feat/explicit-all-scope-selection` @ 9c6f6ec2 | clean, pushed, PR #45 draft | D | `client\node_modules` is a junction into `C:\SaaS-fix-tags-ratelimit` |

Non-worktree directories (protected, not touched): `C:\SaaS-release-cert-d7eef845`,
`C:\SaaS-release-cert-1.1.1`, `C:\SaaS-release-cert-2533b19`, `C:\SaaS-release-tools`
(0.76 GB; contains a junction `ra3\base-client\client\node_modules` → `C:\SaaS`),
`C:\SaaS-cleanup-evidence`, `C:\SaaS-staging-cert`, `C:\SaaS-canary-local`,
`C:\SaaS-fix-role-resolution-evidence`, `C:\SaaS_publish`.

## 3. Local branches (31 after, 39 before)

| Branch | Bucket | Evidence / reason |
|---|---|---|
| `main`, `release/contraclaim-rc1` | protected | stale but ancestors of release |
| `integrate/key-date-eot` | G | main checkout, dirty |
| `fix/tags-rate-limit-budgets`, `fix/reference-sync-degraded-preservation`, `measure/ocrmypdf-bufsize-memory` | A (worktree-held) | merged; delete after their worktrees go |
| `feat/explicit-all-scope-selection` | D | PR #45 |
| `fix/variation-link-modal-layout` | C | PR #44 |
| `fix/bulk-upload-backoff-dependency` | C | open debt, local only |
| `docs/release-1.1-certification`, `docs/ocr-canonical-release-certification` | C | local-only release evidence — push |
| `fix/correspondence-qdrant-contract`, `fix/correspondence-summary-reply-points`, `fix/correspondence-source-authority`, `fix/bulk-csv-blank-cells`, `fix/bulk-ocr-enabled-parsing` | B | superseded (proof in §2); `-d` refuses → owner `-D` |
| `integrate/prod-20261001`, `integrate/prod-20261002`, `replay/dryrun-20261003`, `replay/dryrun-pr33`, `checkpoint/prod-20261002-eed4999`, `local/phase-b-pr35-pr36-base`, `codex/landing-contraclaim-sync-20260710` | B | `git cherry` = 0 vs release; `-d` refuses → owner `-D` |
| `backup/feat-all-scope-pre-90418258` | B | `git range-diff`: its 3 feature commits are identical (`=`) in #45; its only extra commit is #50, merged |
| `backup/contract-master-v1-pre-cl4a` | F | backup of #31's pre-reconcile state; one commit (`714c5eec` CI suites/IDOR note) not patch-equivalent; keep as archive |
| `codex/arbitration-llm-drafting-p3` | E/F | PR #19 open on both repos, conflicting, last touched 2026-08-10; scope resolver later re-done in CL-4A / scope-builder hotfixes |
| `codex/csv-import-scope` | E | carries `CANONICAL_ROLE_ALIASES`; release still has four `ROLE_ALIASES` tables — the single-source idea is still relevant, the branch is not mergeable |
| `codex/reference-linking-workflow-contraclaim`, `codex/reference-linking-workflow-projectdms` | F | PR #18 draft, July, conflicting; reference sync now goes through `ReferenceSyncService` |
| `feat/letter-deletion-cascade` | E (verify) | release has `cascade_delete_cleanup` and `test_letter_deletion_cascade.py` (differs); probably superseded — 30-min check |
| `Backend` | G | 2025-02-11 "Create App.jsx", local only, no remote; looks like a stray — owner |

## 4. Remote branches

`contraclaim`: 47 branches (+HEAD). `origin`: 40 (+HEAD). None deleted (permission denied).

Proven safe to delete — head is an ancestor of release, no open PR, not referenced by
any script, runbook or workflow in the release tree:

- **contraclaim (35):** docs/chronology-foundation-design, feat/cl1-relationship-hardening, feat/cl2-variation-correspondence-links, feat/cl3a-hindrance-scope-integration, feat/cl3b-programme-chronology-links, feat/cl4a-core-active-project-scope, feat/hindrance-constraint-register, feat/release-1.1.1-extraction-review, feat/release-1.1.1-extraction-review-ui, feature/canonical-evidence-store, fix/ai-tenant-isolation, fix/api-unified-routing-env, fix/chronology-event-date-truthfulness, fix/chronology-verification-authority, fix/client-terminal-processing-states, fix/contract-master-reprojection-runtime, fix/correspondence-metadata-integrity, fix/document-processing-status-route, fix/document-status-truthfulness, fix/extraction-pagewise-canary, fix/numeric-integrity-native-and-ocr, fix/quality-gate-table-checkability, fix/security-dependency-refresh-20261001, fix/tags-rate-limit-budgets, hotfix/deep-planning-document-scope, hotfix/deep-planning-target-letter-scope, hotfix/deep-planning-tenant-isolation, hotfix/letters-tenant-isolation, hotfix/scope-builders-fail-closed, hotfix/ws-token-validation, security/pydantic-ai-1.107.7, security/pymongo-4.18.2-retire-r4, security/pymongo-temporary-cve-exception, security/r3-braces-renewal, security/source-map-js-1.2.2
- **origin (29):** codex/production-readiness-phase-0, feature/security-billing-ops-weeks-1-4, plus the same names above that exist there (docs/chronology-foundation-design, feat/cl1…cl4a, feat/hindrance-constraint-register, feat/release-1.1.1-extraction-review(-ui), feature/canonical-evidence-store, fix/ai-tenant-isolation, fix/api-unified-routing-env, fix/chronology-*, fix/client-terminal-processing-states, fix/contract-master-reprojection-runtime, fix/correspondence-metadata-integrity, fix/document-processing-status-route, fix/document-status-truthfulness, fix/extraction-pagewise-canary, fix/numeric-integrity-native-and-ocr, fix/quality-gate-table-checkability, fix/security-dependency-refresh-20261001, fix/tags-rate-limit-budgets, hotfix/deep-planning-target-letter-scope, security/pydantic-ai-1.107.7, security/pymongo-4.18.2-retire-r4)

Superseded on the remotes (owner call): `verify/r4-plus-source-map-js` (#49 closed),
`hotfix/security-deps-20261001` (in release via #37), `origin/origin` (a branch literally
named `origin`, same SHA as `origin/main`).

Kept deliberately: `main`, `release/*`, `reconcile/cl4a-onto-release-rc1` (PR #32 evidence
candidate), `integrate/key-date-eot` (name of the main checkout's branch), `backup/*`,
and every open-PR head.

## 5. PR census

| PR | Repo | State | Base | CI | Mergeable | Relevance | Bucket |
|---|---|---|---|---|---|---|---|
| #45 Explicit All Organisations / All Projects | contraclaim | open draft | release | jobs never started 2026-10-07 (billing block, since lifted — release runs of 2026-10-13 ran) | clean, 53 behind | HIGH: Super Admin consolidated-view correctness | D |
| #44 Variation link dialog viewport | contraclaim | open | release | 5/5 green (2026-10-05) | clean, 64 behind | MEDIUM: UI defect | C |
| #20 release → main | contraclaim | open draft | main | backend green, frontend red (the flake) | clean | evidence PR, do not merge | protected |
| #19 RBAC navbar data filtering | both | open | main | — | conflicting | LOW/SUPERSEDED | E/F |
| #18 idempotent reference linking | both | open draft | main | — | conflicting | OBSOLETE | F |
| #26, #27, #29, #30 (CL-2…CL-4A) | contraclaim | closed 2026-10-13 | stacked | — | — | merged via #32 | A (branches) |
| #49 verify R4 + source-map-js | contraclaim | closed | release | — | — | verification only | B |
| #62 chronology → main | contraclaim | closed | main | — | — | wrong base; #61 merged | done |
| Dependabot #1–#17 | both | closed | main | — | — | closed by owner in July | none |

Merged PRs whose branches still exist: every remote branch in the §4 deletion list.

## 6. Scoring of remaining work

| Item | Value | Urgency | Effort | Risk | Deps | Space | Merge-ready | Time |
|---|---|---|---|---|---|---|---|---|
| Push release-cert docs (2 branches) | 4 | 4 | 1 | 1 | none | 0.1 GB | 5 | <30 min |
| Owner `-D` of 14 superseded local branches + 5 worktrees | 3 | 3 | 1 | 1 | approval | 0.2 GB | n/a | <30 min |
| Remote deletion (64 refs) | 3 | 2 | 1 | 1 | approval | 0 | n/a | <30 min |
| #44 modal layout | 3 | 2 | 1 | 1 | rerun CI | 0.34 GB | 4 | 30–60 min |
| backoff dependency declaration | 3 | 3 | 1 | 1 | rebase | 0.05 GB | 4 | 30–60 min |
| Release CI TipTap teardown flake | 3 | 3 | 2 | 1 | none | 0 | — | 30–60 min |
| CLAUDE.md "org admin can make itself Super Admin" marked open | 3 | 3 | 1 | 1 | none | 0 | — | <30 min (fix shipped: `system_role_audit.py` is in release and gated in `pre_deploy_readiness.sh`) |
| #45 explicit ALL scope | 5 | 3 | 3 | 3 | rebase 53 commits, CI, review | frees fix-tags worktree | 2 | 2–4 h |
| Letter-deletion-cascade supersession check | 2 | 1 | 1 | 1 | none | 0 | — | <30 min |
| BUFSIZ benchmark preservation | 2 | 1 | 1 | 1 | none | 0.03 GB | — | <30 min |
| Role-alias single source (from csv-import-scope idea) | 4 | 2 | 3 | 3 | fresh branch | 0 | 1 | 2–4 h |
| G15 / G16 / R4 table order / Canary B (extraction) | 4 | 3 | 4 | 3 | canary observation | 0 | — | multi-session |
| Super Admin `review_extraction` permission-document consistency (P2) | 3 | 2 | 2 | 2 | none | 0 | — | 1–2 h |
| Chronology A0c / D5 / D6, enclosure extraction, contract page store | 4 | 2 | 5 | 3 | design | 0 | — | multi-session |
| Docker VHDX compaction | 4 | 4 | 2 | 2 | Docker healthy, owner | up to ~20 GB | — | 1–2 h |

No branch exists for: SMTP Unexpected EOF, `ALLOWED_DOCUMENT_MIMES` warning, narrow-width
review UI cosmetics, whole-document embedding hold, Matter Context Packs, Contract Rules
Graph / Project Evidence Graph. They are backlog items only.

## 7. Top 10 low-effort closures

| # | Item | Branch / PR | Action | Time | Tests | Risk | Space | Close now |
|---|---|---|---|---|---|---|---|---|
| 1 | Release 1.0/OCR certification record | `docs/ocr-canonical-release-certification` | push both remotes, docs PR to release | 15 min | none (docs) | none | 0.06 GB after merge | YES |
| 2 | Release 1.1 certification record | `docs/release-1.1-certification` | push, docs PR (coordinate with canary session) | 15 min | none | none | 0.03 GB | YES |
| 3 | Superseded local work | 14 branches in §3 bucket B | `git branch -D` after owner OK | 10 min | none | none | — | YES (owner) |
| 4 | Superseded correspondence/bulk worktrees | 4 worktrees | `git worktree remove` | 5 min | none | none | 0.2 GB | YES (owner) |
| 5 | Merged remote branches | 64 refs | owner runs the commands in §11 | 5 min | none | none | — | YES (owner) |
| 6 | Variation dialog viewport | #44 | merge release in, rerun CI, merge | 45 min | frontend | low | 0.34 GB | READY FOR OWNER APPROVAL TO COMPLETE |
| 7 | `backoff` undeclared | `fix/bulk-upload-backoff-dependency` | rebase, push, PR | 45 min | backend test file | low | 0.05 GB | READY FOR OWNER APPROVAL TO COMPLETE |
| 8 | Reference-integrity worktree | dirty but identical | `git worktree remove --force` after owner OK | 5 min | none | none | 0.22 GB | YES (owner) |
| 9 | CLAUDE.md stale "open debt" | — | mark the self-escalation fix shipped | 10 min | none | none | — | YES |
| 10 | Release CI TipTap flake | — | unmount editors / fake timers in the offending test | 45 min | frontend | low | — | YES |

## 8. Priority items

- **P0** — none found in the repository. Disk is the operational P0: C: has 1–4 GB free
  and the Docker daemon is unresponsive (likely related).
- **P1**
  - #45 explicit ALL scope (Super Admin consolidated views; correctness of the
    `EffectiveScope` model). Rebase onto release (53 behind), rerun CI, review. 2–4 h.
    Target: Release 1.2.
  - Extraction G15 / G16 / R4 / Canary B while the unified_v1 observation runs. Multi-session.
    Target: Release 1.2.
  - Release-tip CI red (flake) — a red release gate hides real regressions. <1 h.
- **P2**
  - Super Admin `dms.document.review_extraction` permission-document consistency. 1–2 h.
  - `backoff` dependency declaration. <1 h.
  - Role-alias single source: four drifted `ROLE_ALIASES` tables remain in release. 2–4 h.
  - #44 dialog layout. <1 h.
- **P3**
  - SMTP Unexpected EOF, `ALLOWED_DOCUMENT_MIMES` warning, narrow-width review UI cosmetics.
  - BUFSIZ RSS measurement (preserve the script now, measure later).
  - Chronology A0c / D5 / D6, enclosure extraction, contract page store separation, Matter
    Context Packs, Contract Rules Graph — strategic, needs design.

## 9. Disk

| Measure | Value |
|---|---|
| Free at start | 1.2 GB (238 GB volume) |
| Free at end | 3.8 GB — the free figure moved during the session from activity outside this one (pagefile, peers), so it is not all attributable to cleanup |
| Recovered by this session | about 0.9 GB (six worktrees ≈ 0.45 GB, npm cache 0.44 GB) |
| Largest consumers | Docker `docker_data.vhdx` 35.9 GB; `pagefile.sys` 14.7 GB; `C:\SaaS` 6.95 GB (venv 3.28 GB); `%TEMP%` 4.5 GB; `hiberfil.sys` 3.9 GB; uv cache 1.9 GB (mostly hardlinks); Playwright 1.1 GB |
| `%TEMP%` breakdown | `claude\` 1.9 GB (live session task output — not touched), `CC378832-…` 0.96 GB (unknown), `DiagOutputDir` 0.41 GB, `DockerDesktopUpdates` 0.34 GB, `vscode-stable-user-x64` 0.24 GB, `wsl-crashes` 0.08 GB, nine 54 MB random-named files |
| Remaining worktree space recoverable | ≈ 1.3 GB if every B/C worktree closes |

The real space is in Docker. Once the daemon responds: `docker system df -v`, classify
images against Release 1.1.1 / rollback / certification, remove dangling images and
build cache, then compact the VHDX (`wsl --shutdown` + `Optimize-VHD` or diskpart
`compact vdisk`). That needs owner approval because it stops Docker. Disabling
hibernation (`powercfg /h off`) would free 3.9 GB — also an owner decision.

## 10. Balance work plan

| Lane | Priority | Item | Branch / PR | Effort | Risk | Dependency | Next action | Target | Space |
|---|---|---|---|---|---|---|---|---|---|
| 1 Close now | P2 | Push both release-cert docs | 2 local branches | <30 min | none | canary session ok | push + docs PR | now | 0.1 GB |
| 1 | P3 | Delete superseded local branches / worktrees | §3 B | <30 min | none | owner OK | `-D` / `worktree remove` | now | 0.4 GB |
| 1 | P3 | Delete merged remote branches | §4 | <30 min | none | owner runs | §11 commands | now | 0 |
| 1 | P2 | #44 modal layout | #44 | <1 h | low | CI | merge release, CI, merge | 1.1.2 | 0.34 GB |
| 1 | P2 | backoff dependency | local | <1 h | low | none | rebase, PR | 1.1.2 | 0.05 GB |
| 1 | P1 | Release CI flake | new | <1 h | low | none | fix test teardown | 1.1.2 | 0 |
| 2 Next release | P1 | Explicit ALL scope | #45 | 2–4 h | medium | rebase, CI | rebase onto b98c1f4 | 1.2 | 0.4 GB (frees fix-tags) |
| 2 | P1 | G15 / G16 / R4 / Canary B | none yet | multi | medium | observation data | plan after canary | 1.2 | 0 |
| 2 | P2 | Super Admin review_extraction doc | none | 1–2 h | low | none | red test, fix | 1.2 | 0 |
| 2 | P2 | Role-alias single source | fresh from csv-import-scope idea | 2–4 h | medium | behaviour change review | design note | 1.2 | 0 |
| 3 Strategic | P3 | Chronology A0c / D5 / D6 | none | multi | — | design | — | 1.3+ | 0 |
| 3 | P3 | Enclosures, contract page store, Matter Context Packs, Rules / Evidence Graph | none | multi | — | design | — | 1.3+ | 0 |
| 4 Defer / archive | — | #19, #18, `codex/reference-linking-workflow*`, `codex/arbitration-llm-drafting-p3` | PRs #19/#18 | — | — | owner | close PRs, tag `archive/*` | — | 0 |
| 4 | — | `codex/csv-import-scope`, `backup/contract-master-v1-pre-cl4a` | — | — | — | — | tag `archive/*` | — | 0 |
| 4 | — | BUFSIZ benchmark | `measure/…` | <30 min | none | — | commit script to the branch, push, remove worktree | — | 0.03 GB |

## 11. Commands awaiting owner approval

Remote deletion was denied by this session's permission classifier. To run it yourself:

```bash
cd /c/SaaS && git push contraclaim --delete docs/chronology-foundation-design feat/cl1-relationship-hardening feat/cl2-variation-correspondence-links feat/cl3a-hindrance-scope-integration feat/cl3b-programme-chronology-links feat/cl4a-core-active-project-scope feat/hindrance-constraint-register feat/release-1.1.1-extraction-review feat/release-1.1.1-extraction-review-ui feature/canonical-evidence-store fix/ai-tenant-isolation fix/api-unified-routing-env fix/chronology-event-date-truthfulness fix/chronology-verification-authority fix/client-terminal-processing-states fix/contract-master-reprojection-runtime fix/correspondence-metadata-integrity fix/document-processing-status-route fix/document-status-truthfulness fix/extraction-pagewise-canary fix/numeric-integrity-native-and-ocr fix/quality-gate-table-checkability fix/security-dependency-refresh-20261001 fix/tags-rate-limit-budgets hotfix/deep-planning-document-scope hotfix/deep-planning-target-letter-scope hotfix/deep-planning-tenant-isolation hotfix/letters-tenant-isolation hotfix/scope-builders-fail-closed hotfix/ws-token-validation security/pydantic-ai-1.107.7 security/pymongo-4.18.2-retire-r4 security/pymongo-temporary-cve-exception security/r3-braces-renewal security/source-map-js-1.2.2
```

```bash
cd /c/SaaS && git push origin --delete codex/production-readiness-phase-0 docs/chronology-foundation-design feat/cl1-relationship-hardening feat/cl2-variation-correspondence-links feat/cl3a-hindrance-scope-integration feat/cl3b-programme-chronology-links feat/cl4a-core-active-project-scope feat/hindrance-constraint-register feat/release-1.1.1-extraction-review feat/release-1.1.1-extraction-review-ui feature/canonical-evidence-store feature/security-billing-ops-weeks-1-4 fix/ai-tenant-isolation fix/api-unified-routing-env fix/chronology-event-date-truthfulness fix/chronology-verification-authority fix/client-terminal-processing-states fix/contract-master-reprojection-runtime fix/correspondence-metadata-integrity fix/document-processing-status-route fix/document-status-truthfulness fix/extraction-pagewise-canary fix/numeric-integrity-native-and-ocr fix/quality-gate-table-checkability fix/security-dependency-refresh-20261001 fix/tags-rate-limit-budgets hotfix/deep-planning-target-letter-scope security/pydantic-ai-1.107.7 security/pymongo-4.18.2-retire-r4
```

Superseded local branches (needs `-D`; proof in §2–§3):

```bash
cd /c/SaaS && git worktree remove /c/SaaS-fix-corr-qdrant && git worktree remove /c/SaaS-fix-corr-prompt && git worktree remove /c/SaaS-fix-corr-source-authority && git worktree remove /c/SaaS-fix-bulk-csv-blank-cells && git branch -D fix/correspondence-qdrant-contract fix/correspondence-summary-reply-points fix/correspondence-source-authority fix/bulk-csv-blank-cells fix/bulk-ocr-enabled-parsing integrate/prod-20261001 integrate/prod-20261002 replay/dryrun-20261003 replay/dryrun-pr33 checkpoint/prod-20261002-eed4999 local/phase-b-pr35-pr36-base codex/landing-contraclaim-sync-20260710 backup/feat-all-scope-pre-90418258
```

## 12. Final summary

```
RELEASE SHA = b98c1f487b31f7963e1b877c475ece7e4b721487
RELEASE TREE = 87e3cf936e021cd66220419baedaae1779b4e903
LOCAL BRANCHES BEFORE = 39      AFTER = 31
REMOTE BRANCHES BEFORE = contraclaim 47, origin 40      AFTER = unchanged (deletion denied)
WORKTREES BEFORE = 20      AFTER = 14
OPEN PRS = 4 on contraclaim-dms (#45 draft, #44, #20 draft, #19), 2 on projectDMS (#19, #18 draft)
DRAFT PRS = #45, #20, #18
CLOSED UNMERGED PRS = #62, #49, #30, #29, #27, #26, dependabot #1–#17 (both repos)
MERGED WORKTREES REMOVED = 1 (SaaS-release-1.1.1)
SUPERSEDED WORKTREES REMOVED = 5
MERGED BRANCHES DELETED = 6 local
SUPERSEDED BRANCHES DELETED = 2 local (verify/r4-plus-source-map-js, hotfix/security-deps-20261001)
DIRTY WORKTREES PROTECTED = 3 (C:\SaaS, SaaS-fix-reference-integrity, SaaS-hotfix-target-letter) + 1 with untracked tool (SaaS-ocr-bufsize-memory)
LOCAL-ONLY WORK PROTECTED = docs/ocr-canonical-release-certification (6), docs/release-1.1-certification (1), fix/bulk-upload-backoff-dependency (1), Backend (1)
SPACE BEFORE = 1.2 GB free    SPACE AFTER = 3.8 GB free    RECOVERED BY THIS SESSION ≈ 0.9 GB
PRODUCTION CHANGED = NO
CANARY CHANGED = NO
DEPLOYMENT PERFORMED = NO
```
