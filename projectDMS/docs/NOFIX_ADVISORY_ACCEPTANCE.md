# Accepted no-fix advisories

**Status:** owner-approved. PYSEC-2026-3740 in force from `release/contraclaim-rc1`;
R3 (GHSA-vfj7-8cjw-p6xm) in force until 2026-11-05 (renewed 2026-10-07 from 2026-10-10); R4 (four PyMongo advisories)
in force until 2026-11-05.
**Decided:** 2026-09-04 (PYSEC-2026-3740, re-derived against pip-audit 2.10.1,
llama-index-core 0.14.22) and 2026-10-03 (R3, npm 10.8.2).

Two advisories are accepted for this release, one per scanner. Neither is
waived, and neither is a policy for no-fix findings in general: each scanner in
`dependency-scan` excepts exactly one advisory and fails on everything else,
including a second advisory in the same package.

| Scanner | Accepted advisory | Until |
|---|---|---|
| pip-audit | `PYSEC-2026-3740` (NLTK) | `release/contraclaim-rc1` review triggers |
| npm audit | `GHSA-vfj7-8cjw-p6xm` (braces), R3 | **2026-11-05** (renewed once, 2026-10-07), enforced by the gate |
| pip-audit + Trivy (backend and LangGraph images) | `CVE-2026-88029`, `CVE-2026-96747`, `CVE-2026-96748`, `CVE-2026-96749` (PyMongo 4.16.0), R4 | **2026-11-05**, enforced by the gate |

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
| **Accepted for** | `release/contraclaim-rc1`, **until 2026-11-05** (originally until 2026-10-10; renewed 2026-10-07, see *Renewal 2026-10-07* below) |

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

1. **2026-11-05 (UTC, inclusive).** From 2026-11-06 00:00 UTC (05:30 IST) the gate
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
| Visibility | The gate prints `TEMPORARY OWNER-APPROVED SECURITY EXCEPTION: GHSA-vfj7-8cjw-p6xm ... expires 2026-11-05` on every pass that uses it. |
| Runtime basis | The gate fails if a package outside the 17 accepted build/test dependents reaches braces, or if any file under `client/src` imports a package of the chain. |
| Guards | `test_ci_static_gates.py` pins that the step runs the gate and its tests, and that nothing swallows or weakens it. It also loads the gate and pins the audit command, every identity constant, the expiry, the dependent set, and that `_is_exception_advisory` rejects each identity variant. |

**The correct statement of the gate's result is "npm audit passed with one
time-bound owner-approved exception (R3)", never "0 vulnerabilities".**

### Renewal 2026-10-07 (from 2026-10-10 to 2026-11-05)

Renewed explicitly, once, by the release owner, after a fresh review on release
`160f18f` (2026-10-07). This is not a silent extension. Every premise of the
original acceptance was re-checked:

| Premise | Re-checked result |
|---|---|
| No patched release | `braces` 3.0.3 (2024-05-21) is still the newest version on npm; the advisory still lists no patched version (updated 2026-10-02). |
| No clean dependency path | `braces` arrives through `tailwindcss` 3 (chokidar, fast-glob, micromatch), `typescript-eslint` (fast-glob) and `@types/jest` (expect, jest-message-util, micromatch). A newer `typescript-eslint` might remove one path, but Tailwind 3 keeps `braces` in the tree. Leaving Tailwind 3 is a major migration, not a dependency bump. |
| One installed copy | `package-lock.json` has a single `node_modules/braces` at 3.0.3. |
| Dependent set | `npm audit` (npm 10, Node 20) reports 14 HIGH packages, all inside the 17 accepted dependents, none new. `@tailwindcss/typography`, `lovable-tagger` and `tailwindcss-animate` are no longer HIGH, which narrows the exposure. The gate's own `evaluate_report` confirms every HIGH is rooted in this advisory. |
| Build/test only | The client image's runtime stage copies only `dist/` and `scripts/serve-dist.mjs`. The chain is loaded only by `vite.config.ts` (`lovable-tagger`) and `tailwind.config.ts` (`tailwindcss`, `tailwindcss-animate`) at build time. |
| No `client/src` import | No file under `client/src` imports any package of the chain. The gate re-checks this on every run. |

Why 2026-11-05: a short, 29-day window, aligned with R4, so both no-fix exceptions come up for review on the same date. The upgrade trigger is unchanged: a published `braces` release above 3.0.3 fails the gate immediately.

---

## R4: four PyMongo 4.16.0 advisories, pending a compatible checkpoint release

**THIS DOES NOT FIX THE UNDERLYING PYMONGO VULNERABILITIES.** It temporarily accepts
four specifically reviewed findings, by advisory id only, until **2026-11-05**.

| | |
|---|---|
| **Advisories** | `CVE-2026-88029` (GHSA-8fvv-fgr5-f8ch, medium), `CVE-2026-96747` (GHSA-qx36-8mw2-4r3x, medium), `CVE-2026-96748` (GHSA-vp6j-j7w5-5xjj, high), `CVE-2026-96749` (GHSA-v4x9-3549-crwv, high) |
| **Package / pinned** | `pymongo` / `4.16.0` (`backend/rbac_backend/requirements.txt`; the legacy manifests pin 4.11.2) |
| **Fix available** | **Yes, but not installable.** Fixed in 4.18.1 (88029) and 4.18.2 (the other three). `langgraph-checkpoint-mongodb`, imported unconditionally by the live letter-drafting engine (`services/letter_drafting/langgraph_engine.py`), permits no fixed line in any released version: 0.4.0 (pinned) `>=4.12,<4.17`; 0.5.0 (latest on PyPI, 2026-09-04, Python >=3.11) `>=4.12,<4.18`. Its GitHub `main` declares `>=4.18.2` but is unreleased, and unreleased code is not shipped. Overriding the declared constraint is not done either. |
| **Owner** | @ManishPandey21 |
| **Decided** | 2026-10-06, by the release owner |
| **Accepted for** | `release/contraclaim-rc1`, **until 2026-11-05 (UTC, inclusive)** |
| **Tracking issue** | ManishPandey21/contraclaim-dms#46 |

### Per-advisory record

| CVE | Affected feature | Used by ContraClaim | Reachable from untrusted input | Compensating control | Why the exception is acceptable |
|---|---|---|---|---|---|
| CVE-2026-88029 | GridFS read/write/delete by id: a mapping passed as the id is read as query criteria | **No.** No `gridfs`/`GridFSBucket` import or call anywhere in the repository (2026-10-06) | No | Files live in S3, not GridFS | The affected code path is not reachable in the current ContraClaim deployment. |
| CVE-2026-96747 | CSFLE / Queryable Encryption: a `.sock` KMS endpoint in a key-vault document opens an `AF_UNIX` connection | **No.** No `ClientEncryption`, `AutoEncryptionOpts` or key vault anywhere | No | No key vault exists; the TLS handshake would fail anyway (advisory) | The affected code path is not reachable in the current ContraClaim deployment. |
| CVE-2026-96748 | Connection-string parsing: a percent-encoded `,` or `:` in the host section injects an extra seed host | **Yes**, every client parses a URI | **No.** All seven `MongoClient`/`AsyncIOMotorClient` constructions take the URI from operator configuration (`settings.DATABASE_URL`, `config.mongo_uri`, `database_url` from settings); no request, tenant or user value is interpolated into a URI | URIs are set at deploy time in secrets/env | The affected code path is not reachable from untrusted input in the current ContraClaim deployment. |
| CVE-2026-96749 | Native BSON encoder: signed 32-bit size overflow when one document is built from more than ~2 GiB of caller-supplied data | **Yes**, every write encodes BSON with the C extension | **Request paths: no.** nginx `client_max_body_size 200m`; uploads 100 MB (general) and 50 MB (contract); webhook, auth and telemetry bodies 1 MB, 16 KB and 64 KB. **Background paths: not proven.** Extracted text has no total per-document cap, there is no decompression guard, and no memory limit on the backend or worker containers, so a decompression-amplified upload remains a theoretical internal path. | Upload size limits, ClamAV scan before processing; MongoDB rejects >16 MB documents but only after encoding | Accepted short-term on the size gap (two orders of magnitude on every request path) with the residual stated, not as "not exploitable". Follow-up in #46: cap stored extracted text per document. |

Trivy reports only the two HIGH ids at the image scan's CRITICAL,HIGH threshold;
the two MEDIUM ids are excepted in pip-audit alone.

Two images carry `pymongo 4.16.0`. The **backend** image installs it directly; the
assessment above is for it. The **LangGraph service** image
(`services/langgraph`) gets it only transitively, from its own pin of
`langgraph-checkpoint-mongodb==0.4.0`. That service never imports pymongo, motor or
bson (it checkpoints with the in-memory `MemorySaver`), and
`docker-compose.prod.yml` does not define it, so for that image all four affected
code paths are not reachable in the current ContraClaim deployment.

### Unblock condition

The **released** package metadata of `langgraph-checkpoint-mongodb` on PyPI permits
`pymongo>=4.18.2`. GitHub `main` does not count. Then, in one PR: upgrade the
checkpoint package, upgrade `pymongo` to `>=4.18.2` in all three pinned manifests,
remove every part of R4 (the four `--ignore-vuln` flags, the Trivy ignore file and
input, the gate step, its tests and this record), and rerun the targeted checkpoint
tests, the real-Mongo suites, the full backend suite, the dependency scan and the
image scan.

Forward-compatibility evidence for that upgrade, gathered 2026-10-06 with a
**test-only** override of the checkpoint package's PyMongo cap (not a supported,
deployable resolution): PyMongo 4.18.2 with Motor 3.7.0 on Python 3.12 passed 154
targeted Mongo/Motor/LangGraph-checkpoint tests and all 540 tests of CI's 24
real-Mongo suites against a MongoDB 8.0.5 replica set.

### Review triggers

1. **2026-11-05 (UTC, inclusive).** From 2026-11-06 the gate fails with
   "PyMongo temporary security exception expired; reassess or upgrade", and Trivy's
   own `expired_at` stops ignoring the two HIGH ids. Extending it needs a new
   decision; the gate's tests pin the date.
2. A released `langgraph-checkpoint-mongodb` permits `pymongo>=4.18.2`. The gate
   reads PyPI on every run and fails, naming the release.
3. The `pymongo` pin moves off 4.16.0 (the gate fails until R4 is removed in the
   same change).
4. Any further PyMongo advisory, or any other finding: pip-audit and Trivy fail
   on it, because only these ids are excepted.

### Enforcement

| | |
|---|---|
| CI | `.github/workflows/ci.yml`, job `dependency-scan`, step "Scan Python dependencies": gate tests, then `.github/scripts/pymongo_exception_gate.py`, then `pip-audit` with one `--ignore-vuln` per R4 id |
| Image scan | Backend and LangGraph images only: `trivyignores: .github/trivy/pymongo-r4.trivyignore.yaml`, two ids scoped to `pkg:pypi/pymongo@4.16.0`, each with `expired_at: 2026-11-05`. Because the LangGraph image resolves pymongo transitively, a newer resolution there stops matching the purl and fails the scan visibly. |
| Gate tests | `.github/scripts/test_pymongo_exception_gate.py` (expiry boundary, pin change, released/pre-release/yanked checkpoint versions, unreadable PyPI answers fail closed) |
| Guards | `test_ci_static_gates.py` pins the exact ignored-id list, that the gate and its tests run before the scan, that only the backend and LangGraph image scans carry an ignore file, and that file's exact ids, purl and expiry |

**The correct statement of the scan result is "pip-audit and the backend image
scan passed with a time-bound owner-approved exception (R4)", never
"0 vulnerabilities".**
