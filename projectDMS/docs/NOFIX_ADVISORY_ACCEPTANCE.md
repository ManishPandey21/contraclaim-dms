# Accepted no-fix advisories

**Status:** owner-approved. PYSEC-2026-3740 in force from `release/contraclaim-rc1`;
R3 (GHSA-vfj7-8cjw-p6xm) in force until 2026-10-10.
**Decided:** 2026-09-04 (PYSEC-2026-3740, re-derived against pip-audit 2.10.1,
llama-index-core 0.14.22) and 2026-10-03 (R3, npm 10.8.2).

Two advisories are accepted for this release, one per scanner. Neither is
waived, and neither is a policy for no-fix findings in general: each scanner in
`dependency-scan` excepts exactly one advisory and fails on everything else,
including a second advisory in the same package.

| Scanner | Accepted advisory | Until |
|---|---|---|
| pip-audit | `PYSEC-2026-3740` (NLTK) | `release/contraclaim-rc1` review triggers |
| npm audit | `GHSA-vfj7-8cjw-p6xm` (braces), R3 | **2026-10-10**, enforced by the gate |

---

## PYSEC-2026-3740 — NLTK model-artifact path sandbox bypass

| | |
|---|---|
| **Advisory** | `PYSEC-2026-3740` (aliases `CVE-2026-81726`, `GHSA-8mgp-746c-j5xp`) |
| **Package** | `nltk` |
| **Version in the audited set** | `3.10.3` |
| **Fix available** | **No.** `fix_versions` is empty, and 3.10.3 is the newest release on PyPI — there is nothing to upgrade to. |
| **Decided** | 2026-09-04, by the release owner, on the evidence below |
| **Accepted for** | `release/contraclaim-rc1` only |

### What the advisory says

Several model-artifact APIs treat caller-controlled model paths as ordinary
filenames even when NLTK path security is enforced, so a caller who chooses
those paths can read or write outside the intended root. The affected
components are `TransitionParser.train`, `TransitionParser.parse`,
`AveragedPerceptron.save`, `AveragedPerceptron.load`,
`PerceptronTagger.save_to_json` and `save_maxent_params`.

### Dependency chain

NLTK is **not** pinned in `backend/rbac_backend/requirements.txt`. Resolving
that file gives 183 packages, and exactly one of them declares NLTK:

```
llama-index-core==0.14.22  ->  nltk>=3.9.3
```

Unconditional: no environment marker, no extra. It is a hard dependency.

Derived by parsing the `requires_dist` of every package in the audited closure,
not from documentation. (The legacy `projectDMS/requirements.txt` pins
`nltk==3.9.2` directly, but CI does not audit that file and it is not part of
the deployed backend.)

### Reachability

**Production code: none.** Zero `import nltk` or `from nltk` in
`backend/rbac_backend/`, `services/` or `scripts/`; zero references to any of
the six affected APIs; the string `nltk` does not appear in first-party
production source at all. The only occurrences anywhere in the repository are
in the guard test that names them deliberately.

**The one package that pulls NLTK in: none of the affected APIs.**
`llama-index-core 0.14.22` was unpacked and read. It contains **zero**
references to `TransitionParser`, `AveragedPerceptron`, `PerceptronTagger`,
`save_to_json` or `save_maxent_params`. What it uses is:

- `nltk.tokenize.sent_tokenize`, `wordpunct_tokenize`, `PunktSentenceTokenizer`
- `nltk.corpus.stopwords`
- `nltk.download`, `nltk.data.find`, `nltk.data.path`

**A correction to the earlier record.** This was previously summarised as
"tokenization paths only". That was imprecise: `llama_index/core/utils.py` also
appends to `nltk.data.path` and downloads corpora. It does not change the
disposition — the affected APIs are still never reached — but the wider surface
is stated here rather than glossed.

### Who controls the paths

The vulnerable APIs need a caller-controlled model import or export path. The
only NLTK path this application influences is the data directory
`llama-index-core` computes, in this order: the `NLTK_DATA` environment
variable, a cache bundled inside the installed package, or
`platformdirs.user_cache_dir("llama_index")`. All three are operator or
packaging decisions. **No request field, upload, filename or tenant value
reaches any of them**, and no model import/export path is exposed to
application users at all.

### Classification

**Installed transitively; affected APIs unreachable.** No fixed release exists,
so upgrading is not an option; the risk accepted is the presence of vulnerable
code that nothing calls.

### Compensating controls

`backend/rbac_backend/tests/test_nltk_advisory_reachability.py` fails if
production code imports NLTK, names any of the six affected APIs, or pins NLTK
directly in the audited requirements. It also asserts that its own file set is
non-empty and includes the backend application, because an earlier revision
scanned a directory that did not exist and passed while checking almost
nothing.

### Removal cost and replacement

Removal is not available as a dependency change. `nltk>=3.9.3` is an
unconditional requirement of `llama-index-core`, which
`services/llamaindex_service.py` imports directly. Dropping NLTK means dropping
LlamaIndex, which owns an ingestion and vector path — an architectural change,
not a version bump, and out of scope for a release-hardening slice.

The replacement, if it is ever taken, is the LangChain vector path this
codebase already carries: `services/database_service.py` raises
*"llama_index is not installed; enable it or switch to LangChain vector
service"*, so the alternative exists and is referenced in production code.
Sizing that swap is its own piece of work and nothing here assumes it.

### Review triggers

This acceptance expires on **any** of the following. It is not permanent.

1. NLTK publishes a fixed release — then upgrade; the exception is deleted, not renewed.
2. `llama-index-core`'s NLTK requirement changes, or the package is replaced or removed.
3. Production code imports NLTK directly.
4. Any of the six affected APIs becomes reachable from production.
5. pip-audit reports a **second** NLTK advisory — the exception covers one id and CI fails on the new one.
6. The next major release, whichever comes first.

### Enforcement

| | |
|---|---|
| CI | `.github/workflows/ci.yml`, job `dependency-scan`, `--ignore-vuln PYSEC-2026-3740` |
| Visibility | pip-audit prints `No known vulnerabilities found, 1 ignored` — the exception is in the log on every run |
| Guards | `test_ci_static_gates.py` pins that exactly this one id is ignored, that no package-wide or severity-wide ignore exists, and that this document records the id |
| Reachability | `test_nltk_advisory_reachability.py` |

**The correct statement of the pip-audit result is "passed with one formally
accepted no-fix advisory exception", never "0 vulnerabilities".** While R3 is in
force, `dependency-scan` as a whole passes with **two** such exceptions, one per
scanner.

---

## R3: GHSA-vfj7-8cjw-p6xm, braces stack-exhaustion DoS

| | |
|---|---|
| **Advisory** | `GHSA-vfj7-8cjw-p6xm` (alias `CVE-2026-93687`), npm advisory source `1240992`, CWE-674 |
| **Package** | `braces` |
| **Affected / installed** | `<=3.0.3` / `3.0.3` (one installed copy) |
| **Fix available** | **No.** 3.0.3 is braces' `latest`; no newer release is published (2026-10-03). |
| **Upstream severity** | HIGH. The gate does not key on a CVSS score, because sources report different CVSS versions. |
| **Decided** | 2026-10-03, by the release owner, on the evidence below |
| **Accepted for** | the integrate/prod-20261002 release, **until 2026-10-10** |

### What the advisory says

A deeply nested brace pattern exhausts the stack in braces' parser. To exploit it,
an attacker-controlled pattern must reach braces.

### Dependency chain

npm reports 17 HIGH packages. They are all one advisory, propagated: every HIGH
entry resolves through `via` to `GHSA-vfj7-8cjw-p6xm` alone. (The only other root
in the report is a MODERATE, `GHSA-82fw-gwwq-j7x9` in vitest.)

- `tailwindcss` 3.4.17 -> `chokidar` 3.6.0 / `micromatch` 4.0.8 / `fast-glob` 3.3.2 -> `braces` 3.0.3.
  This also covers its plugins `tailwindcss-animate` and `@tailwindcss/typography`, and `lovable-tagger`.
- `typescript-eslint` 8.11.0 -> `@typescript-eslint/typescript-estree` -> `fast-glob` -> `micromatch` -> `braces`
- `@types/testing-library__jest-dom` -> `@types/jest` -> `expect` -> `jest-message-util` -> `micromatch` -> `braces`

### Reachability

**Application source: none.** Nothing under `projectDMS/client/src` imports
braces, micromatch, chokidar, fast-glob, or any package above. Only the build
configuration does: `tailwind.config.ts`, `eslint.config.js`, and `vite.config.ts`.
`vite.config.ts` loads `lovable-tagger` in development mode only.

**Deployed client image: absent.** This was verified on an image built from
2883e4b (`sha256:3ead9b6c...`), and is re-verified on the final release image:

- The runtime stage runs `node scripts/serve-dist.mjs`, a static server that imports only `node:` built-ins.
- No application `node_modules` is present, and `/usr/local/lib/node_modules` is empty.
- npm, npx, corepack and yarn are removed.
- No file named braces, micromatch, chokidar or fast-glob exists anywhere in the image.
- The bundle contains none of braces' own identifiers.
- The image SBOM has zero npm components.
- Trivy's scan of the image reports no braces finding.

Node is present in the image by design, to serve static files. It never loads
braces or this dependency chain.

### Classification

**Pre-existing, build/test toolchain only; not present in the deployed runtime.**

npm's own classification is not the evidence here. `node_modules/braces` is
**not** marked `dev` in package-lock.json, because `tailwindcss-animate` sits in
`dependencies` and peer-depends on `tailwindcss`, which pulls the whole chain
into npm's production tree. A dev/prod check therefore cannot guard this
exception. What keeps braces out of the runtime is that the plugin is only
`require`d from `tailwind.config.ts` at build time, and that the image copies
only `dist/` and `serve-dist.mjs`. The gate enforces both halves it can see:
the set of packages that reach braces, and the absence of chain imports in `src/`.
The lockfile is unchanged since base `e02b71b`; the advisory was published after it.

### Review triggers

This acceptance expires on **any** of the following. It is not permanent, and it
does not cover any other advisory.

1. **2026-10-10 (UTC, inclusive).** From 2026-10-11 00:00 UTC (05:30 IST) the gate
   fails, whatever else is true. The gate runs only when CI runs, so a commit that
   went green earlier is not re-gated by itself. Deploy only from an exact-head CI
   run made within the window.
2. braces publishes a release above 3.0.3. The gate then fails and demands the upgrade.
3. The advisory's identity changes (package, GHSA, npm source id or range), or another HIGH or CRITICAL appears.
4. An installed braces other than 3.0.3 appears in the lockfile.
5. braces, or any package in the chain, reaches the client runtime image or is imported by `src/`.

### Enforcement

| | |
|---|---|
| CI | `.github/workflows/ci.yml`, job `dependency-scan`, step "Scan frontend dependencies": `.github/scripts/npm_audit_gate.py`, with the raw report kept as the `npm-audit-report` artifact |
| Gate tests | `.github/scripts/test_npm_audit_gate.py`, which runs in the same step before the gate |
| Visibility | The gate prints `TEMPORARY OWNER-APPROVED SECURITY EXCEPTION: GHSA-vfj7-8cjw-p6xm ... expires 2026-10-10` on every pass that uses it. |
| Runtime basis | The gate fails if a package outside the 17 accepted build/test dependents reaches braces, or if any file under `client/src` imports a package of the chain. |
| Guards | `test_ci_static_gates.py` pins that the step runs the gate and its tests, and that nothing swallows or weakens it. It also loads the gate and pins the audit command, every identity constant, the expiry, the dependent set, and that `_is_exception_advisory` rejects each identity variant. |

**The correct statement of the gate's result is "npm audit passed with one
time-bound owner-approved exception (R3)", never "0 vulnerabilities".**
