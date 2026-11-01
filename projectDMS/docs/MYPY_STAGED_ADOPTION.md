# mypy policy for this release — staged adoption

**Status:** owner-approved, in force from `release/contraclaim-rc1`.
**Decided:** 2026-09-04. **Provenance commit:** merge-base `08d7fa6`.

## Why this exists

mypy has been declared in `.pre-commit-config.yaml` for a long time and never
ran. Its `files:` pattern was anchored at `^backend/...`, the layout from when
`projectDMS` was its own repository; pre-commit hands hooks paths relative to
the git root, which are `projectDMS/backend/...`, so the hook matched **zero
files** and reported `(no files to check) Skipped`. Separately the hook declared
`additional_dependencies: []`, so mypy saw no third-party package and reported
"Library stubs not installed" instead of checking calls. A third defect — a
mongosh snippet in a comment whose `#<space>type:` lines mypy read as PEP 484
type comments — aborted the run with `invalid syntax` before it checked
anything.

Repairing all three switched type checking on for the first time and exposed a
backlog that predates this release. The choice was to hide it or to stage it.

## The policy

1. **Every release-introduced mypy error is fixed.** Not baselined — fixed.
2. **Historical errors present at the merge-base may remain, temporarily.**
3. **CI fails on any new error** relative to the accepted baseline.
4. **Historical debt may only decrease.** The gate is a subset test, so fixing a
   baselined finding keeps CI green; it never requires old errors to be kept.
5. **No broad suppression.** No `ignore_errors`, no `exclude`, no
   `disable_error_code`, no blanket `# type: ignore`, no zero-file scope.
6. **The remaining debt stays visible** — in the gate's own output on every run,
   and in the checked-in baseline.

This is a staged adoption, **not a waiver of new type errors**.

## How it is enforced

| | |
|---|---|
| Gate | `projectDMS/scripts/mypy_staged_gate.py` |
| Baseline | `projectDMS/backend/mypy-baseline.txt` (machine-generated) |
| Hook | `.pre-commit-config.yaml`, hook id `mypy`, unchanged `files:` scope |
| Regenerate | `python projectDMS/scripts/mypy_staged_gate.py --regenerate` |

The gate runs mypy with the same flags the previous `mirrors-mypy` hook passed
(`--ignore-missing-imports --scripts-are-modules`), so the finding set is a
property of the code and not of the invocation. Findings are normalised to
`(path, error code, message)` with the line number dropped, so moving code is
not reported as new.

CI never regenerates the baseline. Growing it requires a reviewed commit.

An environment or configuration failure is reported as
`MYPY EXECUTION FAILURE` with exit code 2 and is never mistaken for accepted
debt.

Type information for third-party packages lives in the hook's
`additional_dependencies`, in whichever form carries types for the pinned
version — `redis==7.0.1` ships `py.typed` so the real package is used
(`types-redis` is deprecated for redis ≥ 5), while `requests==2.33.0` predates
its own `py.typed` and `bleach==6.4.0` ships none, so both take stub packages.
**Stub packages are static-only and must never be added to the runtime
requirements.** They track `backend/rbac_backend/requirements.txt` and must be
updated with it.

## Remaining debt

88 findings across 33 files at the time of adoption, all present at the
merge-base. Grouped for post-release work rather than ticketed individually:

| Theme | Codes | Findings | Shape of the work |
|---|---|---|---|
| Optional not narrowed before use | `union-attr`, `arg-type` (Optional→str) | ~40 | Explicit `None` checks, or annotations that admit the `None` that already reaches them |
| Attribute access on loosely typed payloads | `attr-defined`, `index`, `dict-item` | ~18 | Validate decoded payloads at the boundary instead of assuming shape |
| Container and local annotations missing | `var-annotated`, `assignment` | ~14 | Annotate the container; rename re-used loop variables |
| Names re-annotated or re-imported in one scope | `no-redef` | 3 | Drop the duplicate annotation or the duplicate import |
| Undefined names in dead or lazily evaluated code | `name-defined` | ~5 | Import the symbol, or delete the branch |
| Signature and return mismatches | `return-value`, `call-arg`, `operator`, `misc` | ~8 | Case by case; several are real latent defects |

The last row is the reason this is staged rather than accepted: the same class
of finding, in release code, turned out to be two genuine runtime defects — an
undefined `logger` in the one branch whose job is to refuse a quarantined
document, and a `strict=True` keyword `LLMGenerator.generate` does not accept,
which made every scoped section edit fail into its fallback. Historical debt of
that shape should be assumed to contain more.

## Known gap

`CLAUDE.md` states that drafting LLM call sites pass `strict=True` to
`LLMGenerator.generate`. That parameter does not exist on `LLMGenerator` in this
tree, and the single call site that passed it has been corrected. Either the
convention or the generator needs to change; recorded here rather than resolved
under a typing slice.
