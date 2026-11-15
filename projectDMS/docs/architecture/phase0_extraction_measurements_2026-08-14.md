# Phase 0 extraction measurements

**Date:** 2026-08-14
**Host:** production `contraclaim` stack (there is no separate staging environment)
**Server state at measurement:** branch `main`, HEAD `b2d5025`, compose file set
`docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml` (read from the running
container's `com.docker.compose.project.config_files` label, not assumed)
**Method:** `scripts/phase0_container_measurements.sh` and
`scripts/phase0_production_sample.py`, piped into the running containers via stdin so the
server checkout was not modified. Both are read-only. Output pasted verbatim below.

---

## 1. `ocrmypdf --pages` output shape

**This is the measurement Phase 1 Task 1.1 was blocked on.**

```
==================================================
1. ocrmypdf --pages output shape
==================================================
INPUT_PAGE_COUNT: 9
ocrmypdf exit: 0
MEASURED_OUTPUT_PAGE_COUNT: 9
```

**MEASURED_OUTPUT_PAGE_COUNT = 9** for a 9-page input processed with `--pages 1-2`.

**Conclusion:** `--pages` limits which pages are *OCR'd*; the output PDF **retains every input
page**. Therefore `map_source_pages_to_output` uses **absolute** indexing
(`output_index = page_number - 1`), and the positional branch is **unreachable**.

**Consequence for the existing code.** In `contracts_ingest.py:1565-1586`,
`if output_count >= max(ordered)` is now known to be **always true** in production, because
`output_count` always equals the full document page count. The positional fallback
(`source_index = index`) is dead code. So the current contract path is **correct**, not buggy
— the spec's §3.3 concern was that one of the two branches must be dead or the selection
between them is coincidence, and this measurement settles which.

It remains a hazard worth removing: the dead branch encodes a silent
wrong-page-attribution failure mode that would activate if the tool, its version, or its
flags ever changed. Task 1.1 should keep the absolute mapping, **assert** the expected output
shape, and fail the batch visibly rather than falling back to positional indexing.

**Caveat, flagged:** the trimmed-output branch in the planned
`map_source_pages_to_output` is now unvalidated against any real tool. It should either be
removed, or kept only as the raise-path. Task 1.1 decides; this document does not.

---

## 2. ClamAV RAR support

The backend container carries no ClamAV binaries, so the question was put to the `clamav`
container directly.

```
bash: line 61: clamscan: command not found
clamscan not present in this container
```

```
--- version ---
ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026
--- archive settings (effective) ---
conf: /etc/clamav/clamd.conf
  (no explicit archive lines - defaults apply)
--- libclamunrar present? ---
/usr/lib/libclamunrar.so
/usr/lib/libclamunrar.so.12
/usr/lib/libclamunrar.so.12.0.3
/usr/lib/libclamunrar_iface.so
/usr/lib/libclamunrar_iface.so.12
/usr/lib/libclamunrar_iface.so.12.0.3
```

Archive recursion was then tested functionally with the standard EICAR test file nested
inside a ZIP, with a benign control to distinguish detection failure from access failure:

```
--- ZIP containing EICAR (expect FOUND) ---
/tmp/tmp.LAbNOb/eicar.zip: Eicar-Test-Signature FOUND
exit: 1
--- benign control (expect OK) ---
/tmp/tmp.LAbNOb/benign.txt: OK
exit: 0
```

**Conclusion:** archive scanning is **live and working** — clamd recurses into a ZIP and
detects a nested signature, and returns clean for a benign file. `libclamunrar` is linked and
`ScanRAR` is at its default (enabled), so RAR archives are expected to be scanned.

**Not proven, flagged:** RAR was **not** tested functionally. A valid RAR archive cannot be
synthesised from the tooling available here (the format is proprietary and no archiver is
installed), and a hand-built archive that scanned clean would be indistinguishable from a
scanner that ignored it — a misleading result is worse than none. Phase 5 must carry an
EICAR-in-RAR fixture produced by a real archiver before `.rar` intake is enabled in
production.

**Operational note:** `clamdscan` cannot read arbitrary host paths in this deployment
(`File path check failure: Permission denied` for both the test file and the control). The
application's antivirus adapter already streams file content rather than passing paths, so
this does not affect it — but any future AV tooling must use `--stream` or `--fdpass`.

---

## 3. Production sampling

```json
{
  "documents_total": 146,
  "pdf_documents": 146,
  "completed": 122,
  "ocr_enabled": 146,
  "with_ocr_text": 140,
  "completed_but_no_text": 0,
  "suspicious_large_file_tiny_text": 5
}
```

**Conclusion: backfill is a footnote, not a project.**

- The corpus is **146 documents**, all PDF, all uploaded with OCR enabled.
- **Zero** documents are marked `completed` with no extracted text at all — so the
  catastrophic case (a document fully lost to the 5-page heuristic) does not appear in
  production.
- **5** documents are large (>200 KB) with under 500 extracted characters. These are the
  candidates for silent page loss and can be re-processed individually after Phase 3.
- 24 documents (146 − 122) are not `completed`; this measurement does not classify them.

**Caveat, flagged:** `suspicious_large_file_tiny_text` is a **proxy**. Page counts are not
persisted before Phase 3, so "pages missing text" cannot be counted directly. The real
figure is knowable only after Phase 3 records per-page state. What this measurement
establishes is the *bound*: at most a handful of documents, not hundreds.

---

## 4. Baseline benchmark

```
--- docker stats (single sample) ---
contraclaim-backend-1	        0.47%	505.4MiB / 22.91GiB	2.15%
contraclaim-contract-worker-1	0.14%	208.1MiB / 22.91GiB	0.89%
contraclaim-clamav-1	        0.01%	950.2MiB / 2GiB	46.40%

--- host ---
8 cores
               total        used        free      shared  buff/cache   available
Mem:           23463        6539        1332           4       15990       16923
```

```
--- recent document_pipeline OCR timings ---
0
```

**Baseline for Phase 11 comparison:** backend CPU **0.47%**, memory **505.4 MiB**;
contract-worker CPU **0.14%**, memory **208.1 MiB**. Host: **8 cores, 23 GB RAM**, 16.9 GB
available.

**This is an IDLE baseline only, and that is a finding in itself.** A `docker logs` search
over the last 720 hours returned **zero** `document_pipeline` lines — no general-document
uploads have been processed in 30 days. There is therefore **no production load baseline to
compare Phase 11 against**, and none can be harvested from history.

**Consequence for Phase 11:** its benchmark cannot be a before/after comparison of production
telemetry. It must run the fixture corpus explicitly through both the old and new pipelines
on comparable hardware and compare those numbers. Task 11.3's `compare_to_baseline` still
applies; the baseline must be *generated*, not *recalled*.

**Capacity note:** `clamav` sits at **950 MiB of a 2 GiB limit (46%)** while idle. It is the
tightest resource in the stack by a wide margin. Phase 5 admits ZIP and RAR uploads, which
are the most expensive things ClamAV scans (decompression is recursive and memory-hungry).
The container's memory limit should be reviewed before archive intake is enabled, and
`MaxScanSize` / `MaxRecursion` set explicitly rather than left at defaults.

---

## 5. Verification status

- **Measured, reproducible:** the OCRmyPDF output page count, the ClamAV version and
  `libclamunrar` linkage, the EICAR-in-ZIP detection and its benign control, all seven
  production counts, and the `docker stats` / host figures. All produced by running the two
  committed scripts against the live stack in this session.
- **Read from the running system:** server branch, HEAD, and compose file set.
- **Not proven, flagged above:** RAR-specific scanning (no RAR sample obtainable); the true
  per-document page-loss count (page counts not persisted until Phase 3); any load-state
  resource baseline (no pipeline activity in 30 days).
- **Not attempted:** no data was written, no migration run, no container restarted, no code
  deployed. The server checkout was not modified — both scripts were piped in over stdin.

---

## 6. Effect on the plans

| Plan item | Status after this measurement |
|---|---|
| Task 1.1 page mapping | **Unblocked.** Absolute indexing confirmed. Assert the shape; do not keep a positional fallback. |
| Task 0.3 job-claim atomicity | Already resolved separately: the claim is a single `find_one_and_update`. **Phase 2 is not blocked.** |
| Phase 5 `.rar` intake | **Conditionally blocked.** Needs an EICAR-in-RAR fixture from a real archiver before production enablement. |
| Phase 5 ClamAV capacity | **New work.** Review the 2 GiB limit and set `MaxScanSize`/`MaxRecursion` explicitly before archive intake. |
| Backfill of pre-Phase-3 documents | **Descoped to a footnote.** At most 5 candidates; zero total losses. |
| Phase 11 benchmark | **Method changed.** No historical baseline exists; the corpus must be run through both pipelines explicitly. |
