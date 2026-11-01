# Accepted no-fix OS / base-image findings — deployed images

**Status:** owner-approved, in force from `release/contraclaim-rc1`.
**Decided:** 2026-09-04, at HEAD `34d44f6ef842edbc0a8e1967d5c606ad3525195b`.
**Scanner:** Trivy 0.74.0, vulnerability database downloaded 2026-09-04.

This record covers **only the two images production actually deploys**, and only
findings for which **no fixed package exists today**. It is not a policy for
vulnerabilities in general, it does not cover application-language packages, and
it never covers a finding that has a published fix — a fixable HIGH or CRITICAL
still fails CI, which is demonstrated by the negative control below.

---

## 1. Which images this covers

`docker-compose.prod.yml` builds two images and runs them across six services:

| Image | Build context | Services it runs |
|---|---|---|
| `projectdms-backend` | `projectDMS/backend` | `backend`, `contract-worker`, `document-worker`, `document-worker-canary` |
| `projectdms-client` | `projectDMS/client` | `client` |

`langgraph` is not in the production compose file at all; `docling` is not in it
either; `graphiti` is present but gated behind the `graph-experimental` profile.
None of the three is deployed, so none of them is in scope here.

---

## 2. The exact CI policy result, at this HEAD

Both images were rebuilt from the clean release worktree with the CI build
commands, and scanned with the CI Trivy policy — `--format table --exit-code 1
--ignore-unfixed --severity CRITICAL,HIGH`:

| Image | Base | OS packages | CI-policy findings | Exit |
|---|---|---:|---:|---:|
| `projectdms-backend` | debian 13.6 | 327 | **0** | **0** |
| `projectdms-client` | debian 12.13 | 90 | **0** | **0** |

**Zero fixable CRITICAL or HIGH findings in either deployed image.** Every
finding discussed below was excluded by `ignore-unfixed`, and every one of them
was confirmed to carry no `FixedVersion` in Trivy's own output — the exclusion
was verified against the data, not assumed from the flag.

---

## 3. What `ignore-unfixed` is actually hiding

Re-derived on the same images with the severity filter kept and the
unfixed-exclusion removed:

| Image | Rows | CRITICAL | HIGH | Distinct packages | Distinct CVEs | Rows carrying a fix |
|---|---:|---:|---:|---:|---:|---:|
| `projectdms-backend` (OS) | 242 | 17 | 225 | 47 | 116 | **0** |
| `projectdms-client` (OS) | 58 | 4 | 54 | 18 | 20 | **0** |

Debian's own status for these rows: backend 161 `affected`, 80 `fix_deferred`,
1 `will_not_fix`; client 48 `affected`, 9 `fix_deferred`, 1 `will_not_fix`.

The single `will_not_fix` in each image is named rather than buried:
`libtiff6` / CVE-2026-36849 (HIGH) in the backend, and `zlib1g` /
CVE-2023-45853 (CRITICAL) in the client.

### Application-language packages

The backend carries exactly **one** language-package finding: `nltk` 3.10.3,
CVE-2026-81726 — an alias of `PYSEC-2026-3740`, the advisory already accepted
under `NOFIX_ADVISORY_ACCEPTANCE.md`. **No second NLTK advisory exists.** That
acceptance stands on its own record and is not extended, widened, or renewed by
this document. The client image carries **zero** language-package findings: the
npm CLI, corepack and yarn are deleted from the runtime stage, which is what
removed the one CRITICAL and nineteen HIGH Node-side findings the earlier
release evidence recorded.

---

## 4. Runtime role, measured rather than argued

Counting CVEs says nothing about exposure, so the classification below was taken
from the running containers: `/proc/1/maps` of the live process was read, every
mapped shared object resolved to its Debian package with `dpkg -S`, and that set
intersected with the finding set.

**Backend.** The serving process maps shared objects belonging to 13 OS
packages: `libbz2-1.0`, `libc6`, `libcrypt1`, `libdb5.3t64`, `libffi8`,
`libgcc-s1`, `liblzma5`, `libsqlite3-0`, `libssl3t64`, `libstdc++6`, `libuuid1`,
`libzstd1`, `zlib1g`. Of the 47 packages with an unfixed finding, exactly
**two** are in that set:

| Package | Findings | Severity | Role |
|---|---|---|---|
| `libsqlite3-0` | CVE-2026-11822, CVE-2026-11824 | HIGH | Loaded via Python's `sqlite3`/`aiosqlite`. The application's data stores are MongoDB and Qdrant; no request path opens an attacker-supplied SQLite database. |
| `libuuid1` | CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 | HIGH | Linked transitively. Used to generate identifiers, never to parse untrusted input. |

**No CRITICAL finding is loaded into the backend's serving process.**

**Client.** The `node scripts/serve-dist.mjs` process maps shared objects from
three packages only: `libc6`, `libgcc-s1`, `libstdc++6`. **None of the 18
packages with an unfixed finding is loaded at runtime** — including the
`will_not_fix` CRITICAL in `zlib1g`, because Node links its own bundled zlib and
the system library is never mapped.

### The remaining findings, grouped by why they are present

| Class | Backend packages | Why it is in the image | Loaded by the running process |
|---|---|---|---|
| Kernel headers | `linux-libc-dev` (54 rows, the single largest group) | Pulled in by `build-essential`, needed only to compile Python wheels | **No** — a container has no kernel of its own |
| Media codecs | `libavcodec61`, `libavformat61`, `libavutil59`, `libswresample5` (68 rows) | Transitive dependencies of the OCR/PDF toolchain | **No** — the application processes no audio or video |
| Base-image maintenance | `perl`, `perl-base`, `libperl5.40`, `perl-modules-5.40` (32 rows), `gnupg`/`gpg*`, `dirmngr` | dpkg/apt machinery in the base layer | **No** — nothing invokes them after build |
| System utilities | `util-linux`, `mount`, `bsdutils`, `login`, `libblkid1`, `libmount1`, `libsmartcols1`, `liblastlog2-2` | Debian base | **No** |
| Document toolchain | `libtiff6`, `libtesseract5`, `tesseract-ocr`, `libcups2t64`, `libxml2`, `libglib2.0-0t64`, `libcjson1`, `libmbedcrypto16` | Genuinely used by OCR and PDF handling | **Not in the serving process.** `services/ocr_service.py` and `services/extraction/image_ocr_runner.py` invoke `tesseract`, `gs`, `qpdf`, `unpaper`, `pngquant` and `pdftotext` through `subprocess.run`, so these libraries load into short-lived child processes that run as the same non-root `appuser`, on files that have already passed upload validation |
| Transfer | `curl`, `libcurl3t64-gnutls`, `libcurl4t64`, `libssh2-1t64` | `curl` exists for the compose healthcheck | **No** — Python HTTP goes through `httpx`/`urllib3`, not libcurl |

The client's findings fall entirely into the *base-image maintenance* and
*system utilities* classes (`perl-base`, the `util-linux` family, `wget`,
`gzip`, `libacl1`, `libsystemd0`, `libudev1`, `ncurses`, `libtinfo6`, `zlib1g`),
plus `wget`, which exists solely so the compose healthcheck can run
`wget -q --spider`.

---

## 5. The acceptance, and the conditions it rests on

Each condition was checked, not asserted:

| # | Condition | Evidence |
|---|---|---|
| 1 | No published fixed version | 0 of 300 rows across both images carry a `FixedVersion` |
| 2 | Exact CI image has 0 fixable HIGH/CRITICAL | Both CI-policy scans exited 0 |
| 3 | Excluded only by the existing `ignore-unfixed` policy | The informational scans differ from the CI scans in that one flag alone |
| 4 | No feasible safe package update today | There is nothing to update to; the packages are the Debian base's own |
| 5 | Runtime role recorded | §4, measured from `/proc/1/maps` |
| 6 | Compensating controls documented | §6 |
| 7 | Image runs non-root | Backend `USER appuser`; client `USER app` — both set in their Dockerfiles |
| 8 | Expires automatically when a fix appears | §7 — Trivy stops excluding a finding the moment Debian publishes a fix, and CI then fails |
| 9 | Next release must re-evaluate | §7 |
| 10 | No application-language finding swept in | The one language finding (`nltk`) is governed by its own record and is explicitly excluded here |

**Accepted for `release/contraclaim-rc1` only.**

## 6. Compensating controls

- Both images run as a non-root user, with no package manager present in the
  client runtime image (enforced by a dedicated CI step, not by convention).
- `pip` is uninstalled from the backend image, so nothing at runtime can install
  a package.
- The backend is not published to the host; it sits on internal compose networks
  behind the gateway.
- The packages that are genuinely reachable at runtime (`libsqlite3-0`,
  `libuuid1`) are used for identifier generation and an unused local database
  driver, not to parse untrusted input.
- The document toolchain runs as short-lived subprocesses under the same
  unprivileged user, on files that have already passed upload validation and
  antivirus.
- `dependency-scan` and `secret-scan` remain green independently of this record.

## 7. Review triggers

This acceptance expires on **any** of the following. It is not permanent.

1. Debian publishes a fix for any listed package — Trivy stops excluding it and
   CI fails on the next run, with no action needed to make that happen.
2. The base image changes (`python:3.12-slim` or `node:20-bookworm-slim`).
3. A finding moves from unfixed to fixed in Trivy's database.
4. The production runtime changes such that a package classified here as "not
   loaded" becomes loaded.
5. A new package enters either image.
6. The next release, whichever comes first.

## 8. `ignore-unfixed` — the policy decision

Two options were compared for the five image scans in `docker-build-and-scan`:

**A. Retain `ignore-unfixed: true`, with this record as the release evidence.**
The job fails on anything actionable and stays quiet about what cannot be acted
on. The exclusion is automatically reversed by the scanner the moment a fix
exists.

**B. Remove `ignore-unfixed` and maintain a per-CVE `.trivyignore`.** This would
require listing 116 backend and 20 client CVEs today, and re-listing them
whenever Debian re-scores, splits, or supersedes an advisory. Every entry would
be a hand-maintained suppression that does *not* expire on its own — so the
first time one of them gained a fix, the ignore file would keep hiding it. That
inverts the property that matters: option A cannot hide a fixable finding, while
option B can, silently, through staleness.

**Selected: A — retain `ignore-unfixed: true` unchanged, with this record as the
release evidence.** No workflow change was made.

The load-bearing invariant — *a fixable HIGH or CRITICAL must still fail CI* —
was proved rather than assumed. Running the **exact CI policy**, including
`--ignore-unfixed`, against a deliberately older image (`falkordb/falkordb:v4.0.8`,
debian 12) produced **144 findings (9 CRITICAL, 135 HIGH), every one with
`status: fixed` and a `FixedVersion`, and Trivy exited 1**. `ignore-unfixed`
removes only what cannot be fixed; it cannot conceal what can.

---

## 9. Correct wording

The gate's result is *"image scan passed with zero fixable CRITICAL/HIGH
findings, and a recorded set of unfixed base-image findings accepted for this
release"* — never *"zero vulnerabilities"*, and never a bare count such as
*"300 unfixed vulnerabilities accepted"*.
