# Post-canary cleanup, quick-win closure and backlog — 2026-10-15

Scope: repository and disk cleanup only. No production configuration, canary routing,
deployment or datastore change was made. The one production contact was a read-only
`docker exec … python -c "find_spec('backoff')"` on the running backend container.
This file is not committed.

## 0. State re-established

| Item | Value |
|---|---|
| Release | `contraclaim/release/contraclaim-rc1` = `b98c1f487b31f7963e1b877c475ece7e4b721487`, tree `87e3cf936e021cd66220419baedaae1779b4e903` (both match the expected values) |
| Local `release/contraclaim-rc1` | `2533b19d`, stale and left alone |
| Production checkout | `b98c1f4`, backend image `8042765f` |
| Peer sessions | none running. The disk-cleanup session (`6031ff30…`) was idle, but its background trim task (pwsh 18568) was still alive |
| Fetch | `git fetch --all --no-prune` (`--prune=false` is rejected by git) |

## 1. fstrim / VHDX

- The background task started Docker, then polled `docker info` 36 times. Each call took about 48 s and returned
  `500 Internal Server Error … dockerDesktopLinuxEngine/v1.55/info`, so the task printed `ready=False`.
- **fstrim did not run.** The task then killed Docker Desktop and ran `wsl --shutdown`. Afterwards no Docker process
  was running, `docker-desktop` showed `Stopped`, and `docker_data.vhdx` measured 33.17 GiB (35.6 GB). That is the
  same size as before, give or take 0.3 GB.
- Compacting without a trim inside the guest reclaims almost nothing. The engine has to be healthy first:
  `docker info` must succeed, then `fstrim -av` inside the guest, then `wsl --shutdown`, then compact.
- Owner-only compact (elevated prompt, with Docker Desktop fully exited and `wsl -l -v` showing Stopped):

  ```
  diskpart
  select vdisk file="C:\Users\santo\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
  attach vdisk readonly
  compact vdisk
  detach vdisk
  exit
  ```

## 2. Canary observation (closed by the canary session; read only here)

| | |
|---|---|
| Window | 2026-10-13 15:36:32Z → 2026-10-14 15:36Z; the observer finished at 15:40:45Z after 284 snapshots, 0 failed |
| Canary org | `c06c95b2-c6f3-4e79-998f-af4843524f7b` (GULERMAK-SAM JV) |
| Canary jobs | **0**: 0 completed, 0 human review, 0 partially processed, 0 failed, 0 dead-lettered, 0 retries |
| Cross-tenant routing leaks, worker cross-claims | 0 / 0 |
| Canonical, vector and graph failures | 0 / 0 / 0, because nothing was exercised |
| OOM / restarts | 0 / 0; memory stayed flat at 165–166 MiB |
| Result | **INSUFFICIENT VOLUME**. The canary session recommended EXTEND, and the owner's A–E decision is still open |

The evidence is on the production server at `/var/backups/contraclaim-stg-evidence/REL-b98c1f48-CANARY-20261013/`.
It was not touched. One new P2 was observed there: 8 digest notifications were skipped because a `Notification`
`_id` was stored as an ObjectId.

## 3. Findings that contradict the 2026-10-14 report

- **`C:\SaaS-fix-reference-integrity` is NOT byte-identical to release.** It holds about 1,466 lines of uncommitted,
  unmerged work: degraded-extraction preservation in `reference_sync_service.py` (`removal_withheld`,
  `_could_be_misread`, OCR folding), plus a new 1,079-line `test_reference_sync_degraded_preservation.py`. None of
  this is on release. **KEPT.** The work should be committed to its own branch and decided on.
- **`backoff` is missing, but nothing reachable needs it.** The production image lacks the module
  (`find_spec('backoff') = None`). However, the only importer is `BulkUploadService._async_process_single_document_with_metadata`,
  which is called only by `process_documents_with_metadata`, and no router, worker or service calls that.
  Production has never failed on it. Recommendation: **remove the dead path and its import, do not add the
  dependency.** Branch `fix/bulk-upload-backoff-dependency` is kept until the owner decides.

## 4. Actions taken

| Action | Proof |
|---|---|
| Closed PR #18 on both repos | Content was folded into `bb8f1437` (an ancestor of release): 9 of 12 files identical, every symbol present, all 7 backend and 2 client tests present by name. The two heads are content-identical |
| Deleted local `codex/reference-linking-workflow-contraclaim` / `-projectdms` | `-d` accepted (each was equal to its upstream) |
| Deleted local `Backend` (843c5922) | 2025-02-11 "Create App.jsx": a 33-line `Frontend/src/App.jsx` stub on a separate root (`cf529a44`). No common ancestor with release, and `origin/Backend` is gone |
| `-D` deleted 11 superseded locals, one at a time | `fix/bulk-ocr-enabled-parsing`, `integrate/prod-20261001`, `integrate/prod-20261002`, `replay/dryrun-20261003`, `replay/dryrun-pr33`, `local/phase-b-pr35-pr36-base`, `codex/landing-contraclaim-sync-20260710`: `git cherry` against release = 0. `backup/feat-all-scope-pre-90418258`: its 3 commits are the same SHAs inside #45. `fix/correspondence-qdrant-contract`, `fix/correspondence-source-authority`: their unmatched commits' 51 and 69 test functions all exist in release. `fix/bulk-csv-blank-cells`: superseded by the combined contract (`test_boolean_columns_follow_the_combined_contract`) |
| Removed worktrees | `C:\SaaS-fix-corr-qdrant`, `C:\SaaS-fix-corr-source-authority`, `C:\SaaS-fix-bulk-csv-blank-cells` (each clean, no junctions) |
| Kept | `checkpoint/prod-20261002-eed4999` (rollback ref). `fix/correspondence-summary-reply-points` and its worktree `C:\SaaS-fix-corr-prompt` are proven superseded (only `ef6a68f6` is unmatched, and all its tests are in release), but removal was **denied by the permission classifier** |
| PR #44 | Merged release into the branch (no rebase, no force), pushed to both remotes. Head `68236a29`, CI 5/5 green, `CLEAN`. **Not merged; waiting for owner approval** |
| TipTap flake | PR contraclaim-dms#67, head `230d6fec`, base release, independent of #44. Branch `fix/tiptap-test-teardown` from release (worktree `C:\SaaS-fix-tiptap-teardown`; its `client\node_modules` is a junction into `C:\SaaS-fix-link-modal-layout`) |

## 5. TipTap teardown flake

| | |
|---|---|
| Test file | `client/src/components/letter-template/__tests__/RichTextEditor.tiptap-upgrade.test.tsx` |
| Component | `RichTextEditor` (`@tiptap/react` 3.31.2 `useEditor`) |
| Stack | `Timeout._onTimeout @tiptap/react/dist/index.js:441` → `Editor.destroy` → `EditorView.destroyPluginViews` → `@tiptap/core` drag plugin `destroy()` → `window.removeEventListener` |
| After-teardown callback | `EditorInstanceManager.scheduleDestroy` defers `editor.destroy()` by 1 ms, so a StrictMode remount can reclaim the editor. After the file's last test, vitest tears down jsdom, so the timer fires with no `window` |
| Class | B (test lifecycle), triggered by C (library deferred destroy). Not a production bug, since a browser always has `window` |
| Repro | 0/6 in isolation locally; the race is load-dependent. **Deterministic probe**, which deletes `window` in `afterAll` to simulate teardown: 3/3 red before the fix, 0/5 after |
| Fix | In the file's `afterEach`, call `cleanup()` and then await a 10 ms macrotask, so the deferred destroy runs while jsdom exists. No global suppression, no retry |
| Verification | Focused test 10/10 clean. Full suite: 1 unhandled error in the first run (its text was not captured, and that run collected 660 tests instead of 668); later full runs 665/668 passed with 0 errors. Lint clean. Build OK |

## 6. PR #19 and PR #45

- **#19: do NOT close as superseded.** Release has scope resolution by a different route (CL-4A `tenant_context`).
  It lacks:
  - `CANONICAL_ROLE_ALIASES` (release has `core/role_reference.py` plus three local tables)
  - AI strict mode (`LLMGenerator.generate` has no `strict`)
  - the `FalkorDBVectorService` removal (still in release, imported by `data_sync.py`)
  - the legacy `documents.py` deletion
  - about 25 tests (scope matrix, tenant-query, notification/ws scope, `test_role_alias_single_source`)
  - `route_control_manifest.json`

  `codex/csv-import-scope` is a strict superset of the #19 head. Either keep both, or close #19 as "unreviewable
  as a unit, carried by `codex/csv-import-scope`", and open focused PRs off release for each gap.
- **#45: UPDATE EXISTING PR.**
  - None of its six requirements is in release (`ALL_SELECTION`, `__all__` on the wire, contextual All Projects,
    deep-planning bounded by the navbar, client TenantContext defaults, e2e navbar selection).
  - The 53 newer release commits touch none of its 10 files, and `git merge-tree` finds 0 conflicts.
  - The true diff is about 10 files, +1107/−88 (about 300 production lines and 800 test lines).
  - Next steps: merge release in, run the backend suite, vitest and Playwright, add a test for `__all__` on the
    `ALLOW_DEV_HEADERS` path in `security.py:369`, then CI.
  - It has never had green CI.

## 7. CLAUDE.md corrections proposed (not applied)

Against the **release** copy (`projectDMS/CLAUDE.md` at b98c1f48):

| Line | Current claim | Fact on release | Proposed |
|---|---|---|---|
| 86–89 | "Role normalization lives once in `core/permissions.py` (`CANONICAL_ROLE_ALIASES`…)" | No `CANONICAL_ROLE_ALIASES`. `core/role_reference.py` `ROLE_ALIASES` (re-exported by `security.py`) plus local tables in `authorization_service.py:20`, `permission_service.py:37`, `rbac_service.py:13` | State the four tables, and that the single source exists only on `codex/csv-import-scope` |
| 97–99 | New drafting LLM calls pass `strict=True` to `LLMGenerator.generate` | `retrieval/generator.py` `generate` has no `strict` parameter | Mark it as a convention whose implementation lives only on `codex/*` (#19), not yet on release |
| 113–115 | "Trust `gate_evidence` in `route_control_manifest.json`" | No manifest on release; line 57 of the same file says so | Delete, or point to `scripts/gate3_route_inventory.py` / `test_route_inventory.py` |
| 216–217 | "`FalkorDBVectorService` was deliberately deleted" | `services/falkordb_vector_service.py` is present and imported by `services/data_sync.py` | "is slated for deletion (done on `codex/*`, not on release)", or delete it on release |

Against the **main-checkout working copy** (dirty, on `integrate/key-date-eot`): replace the "Open, blocks cutover: an
org admin can make itself Super Admin" paragraph with "Fixed on release: `core/role_reference.py` never resolves an
alias to a system document; `scripts/system_role_audit.py` is gated in `pre_deploy_readiness.sh:213`."

## 8. Commands awaiting the owner

Remote deletion was denied (`[Git Destructive]`). Every ref below is an ancestor of release or superseded, has no
open PR, and is not release, main, backup, checkpoint or reconcile:

```bash
cd /c/SaaS && git push contraclaim --delete docs/chronology-foundation-design feat/cl1-relationship-hardening feat/cl2-variation-correspondence-links feat/cl3a-hindrance-scope-integration feat/cl3b-programme-chronology-links feat/cl4a-core-active-project-scope feat/hindrance-constraint-register feat/release-1.1.1-extraction-review feat/release-1.1.1-extraction-review-ui feature/canonical-evidence-store fix/ai-tenant-isolation fix/api-unified-routing-env fix/chronology-event-date-truthfulness fix/chronology-verification-authority fix/client-terminal-processing-states fix/contract-master-reprojection-runtime fix/correspondence-metadata-integrity fix/document-processing-status-route fix/document-status-truthfulness fix/extraction-pagewise-canary fix/numeric-integrity-native-and-ocr fix/quality-gate-table-checkability fix/security-dependency-refresh-20261001 fix/tags-rate-limit-budgets hotfix/deep-planning-document-scope hotfix/deep-planning-target-letter-scope hotfix/deep-planning-tenant-isolation hotfix/letters-tenant-isolation hotfix/scope-builders-fail-closed hotfix/ws-token-validation security/pydantic-ai-1.107.7 security/pymongo-4.18.2-retire-r4 security/pymongo-temporary-cve-exception security/r3-braces-renewal security/source-map-js-1.2.2 codex/reference-linking-workflow hotfix/security-deps-20261001 verify/r4-plus-source-map-js
```

```bash
cd /c/SaaS && git push origin --delete codex/production-readiness-phase-0 docs/chronology-foundation-design feat/cl1-relationship-hardening feat/cl2-variation-correspondence-links feat/cl3a-hindrance-scope-integration feat/cl3b-programme-chronology-links feat/cl4a-core-active-project-scope feat/hindrance-constraint-register feat/release-1.1.1-extraction-review feat/release-1.1.1-extraction-review-ui feature/canonical-evidence-store feature/security-billing-ops-weeks-1-4 fix/ai-tenant-isolation fix/api-unified-routing-env fix/chronology-event-date-truthfulness fix/chronology-verification-authority fix/client-terminal-processing-states fix/contract-master-reprojection-runtime fix/correspondence-metadata-integrity fix/document-processing-status-route fix/document-status-truthfulness fix/extraction-pagewise-canary fix/numeric-integrity-native-and-ocr fix/quality-gate-table-checkability fix/security-dependency-refresh-20261001 fix/tags-rate-limit-budgets hotfix/deep-planning-target-letter-scope security/pydantic-ai-1.107.7 security/pymongo-4.18.2-retire-r4 codex/reference-linking-workflow hotfix/security-deps-20261001
```

Local removals denied in-session:

```bash
cd /c/SaaS && git worktree remove /c/SaaS-fix-corr-prompt && git branch -D fix/correspondence-summary-reply-points
```

Stale target-letter worktree. It is fully redundant: its test is an older, weaker draft of release's (the same
3 test names, and release adds the embedding sink and the no-side-effect assertion), and its router is older than
release's #42 scope fix:

```bash
cd /c/SaaS && git worktree remove --force /c/SaaS-hotfix-target-letter
```

---

## 9. Second pass (same day): quick wins closed

| Item | Result |
|---|---|
| PR #44 | Merged with a merge commit, pinned to head `68236a2984ef4ecbde2e8db03e55130e77410fc0` (CI 5/5, 0 unresolved threads, diff limited to the 4 Variation-dialog files). Merge commit `e7443420d2450b21000d12521b1056309b2a40fd`. Release CI on `e7443420`: success |
| PR #67 | Old head `230d6fec` passed 5/5. Release (after #44) was merged in (no rebase, no force-push), giving final head `f3a2f334afdf9ee2d2ccea1d2dab5b61011db7fa`; its diff against release is the one test file only. CI 5/5 on that exact head. Merged pinned to it. Merge commit `eeeab0a9866b0d6abcea4f4466254cf47c91b92f` |
| Release now | `eeeab0a9866b0d6abcea4f4466254cf47c91b92f`, tree `9f9264890500d06d370f22b48b7686a7cb2d1709`. Client and test changes only; not deployed. Release CI on this SHA was in progress at the time of writing |
| Reference-integrity work | Preserved on `wip/reference-integrity-preservation` @ `6b585d097c24f323824cbcb917a852e038937516` (base `447d1822`). The 9 files were committed by explicit pathspec: 8 services plus `tests/test_reference_sync_degraded_preservation.py`, +1466/−20. Working tree clean. Pushed to `contraclaim`. No PR |
| `C:\SaaS-hotfix-target-letter` | Re-proved (router older than release's #42 fix, test a weaker subset, edges test identical), then removed. It was a detached HEAD, so there was no branch to delete. About 0.035 GB |
| `C:\SaaS-fix-corr-prompt` | Re-proved (clean, never pushed, no PR, `44cb662f` patch-equivalent in release, all of `ef6a68f6`'s tests in release). Worktree removed. `-d` refused because the content landed under different SHAs, so it was deleted with `-D` |
| Merged worktrees | `C:\SaaS-fix-tiptap-teardown` (its node_modules junction was deleted first, and a scan found no junction targeting either worktree) and `C:\SaaS-fix-link-modal-layout` were removed. Their branches were deleted with `-d` |
| Docs patch | Worktree `C:\SaaS-docs-claude-md`, branch `docs/claude-md-stale-status` off `e7443420`, **uncommitted**. Four corrections to `projectDMS/CLAUDE.md` (§7) |

Remote safe-delete lists (§8) now also include, on both remotes, `fix/variation-link-modal-layout` and
`fix/tiptap-test-teardown`. Both are ancestors of release.

## 10. PR #19 decomposition (plan only; PR untouched)

| Piece | Content | Value | Risk | Dependency | Small PR |
|---|---|---|---|---|---|
| 19A role-alias normalisation | Collapse `role_reference.ROLE_ALIASES` + 3 local tables into one source; port `test_role_alias_single_source`, `test_role_alias_resolution`, `test_rbac_resolution_regressions` | High: the tables disagree today, and role resolution is a security boundary | Medium: adding an alias is a behaviour change, so diff each table's effective mapping first | Must respect `role_reference.py`'s "an alias never resolves to a system document" rule | YES |
| 19B drafting LLM strict mode | `strict=True` on `LLMGenerator.generate` and drafting call sites, deterministic fallback + degraded marking; port `test_ai_harness_hardening` | Medium–high: stops silent fallback text being presented as drafted content | Medium: touches several drafting paths; prompt-version pins | none | YES, split per call-site family if needed |
| 19C Falkor vector cleanup | Delete `falkordb_vector_service.py` and its only importer path in `data_sync.py`; also the legacy `routers/documents.py` deletion (check that the `insurance.py` import moves first) | Medium: removes dead write-only code and a decision mismatch | Low | 19C-documents needs the `insurance.py` import moved | YES |
| 19D regression tests | About 25 tests: scope matrix, tenant-query (letters, email groups, tasks, report clause), notification/ws scope, lifecycle catalogue, dependency factories | High: the factory test guards the "green suite, broken factory" failure mode | Low–medium: must be re-targeted to CL-4A `tenant_context`, not #19's `effective_scope.py` | Do not port `effective_scope.py`, which would be a parallel scope model | YES, in 2–3 batches |

Do not port `core/effective_scope.py`, `route_control_manifest.json` or `TenantContextGate.tsx` wholesale: CL-4A
replaced that architecture.

## 11. Backlog items created

- **Remove the dead backoff-dependent code path.** Delete `BulkUploadService.process_documents_with_metadata`,
  `_process_single_document_with_metadata` and `_async_process_single_document_with_metadata` (no caller outside
  tests), plus their `import backoff`, and adapt `test_legacy_ocr_cid_guard.py:691-720`. Do not add the dependency.
  Branch `fix/bulk-upload-backoff-dependency` (worktree `C:\SaaS-fix-backoff`) then becomes obsolete.
- **Docker storage maintenance.** Repair the Docker engine (`docker info` → 500), prune proven-disposable
  objects, `fstrim -av` inside the guest, `wsl --shutdown`, and compact the VHDX (§1). No action now.
- **Disk watch.** `%TEMP%\DiagOutputDir\RdClientAutoTrace` (the Remote Desktop client's auto-trace) is 5.6 GB and
  was still growing at 23:20. Free space fell from 20.8 GB to 15.1 GB during this session for that reason, not
  because of anything this session did. Docker Desktop also restarted itself at 23:20 (not from this session).
  Both are owner decisions.

## 12. Canary

The result is INSUFFICIENT VOLUME, and the recommendation is to EXTEND with the same org (GULERMAK-SAM JV only).
The production-session prompt is `docs/operations/CANARY_EXTENSION_PROMPT_2026-10-15.md`, and needs owner
approval. Its exit criteria are at least 4 fresh `unified_v1` jobs (native, scanned, mixed and table-heavy where
available) plus time. Any human review is done by an org-admin or project-admin, not Super Admin.
