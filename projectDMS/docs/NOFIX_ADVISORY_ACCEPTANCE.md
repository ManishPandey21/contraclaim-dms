# Accepted no-fix advisories

**Status:** owner-approved, in force from `release/contraclaim-rc1`.
**Decided:** 2026-09-04. **Re-derived against:** pip-audit 2.10.1, llama-index-core 0.14.22.

One advisory is accepted for this release. It is **not** waived, and it is not a
policy for no-fix findings in general: `dependency-scan` ignores exactly one
advisory id and fails on everything else, including a second advisory in the
same package.

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

**The correct statement of the gate's result is "dependency-scan passed with one
formally accepted no-fix advisory exception", never "0 vulnerabilities".**
