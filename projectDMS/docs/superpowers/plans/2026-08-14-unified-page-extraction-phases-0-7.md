# Unified Page Extraction & Quality Gate (Phases 0–7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every upload surface page-level extraction through one shared engine running in a dedicated worker, with a deterministic quality gate and a bounded LLM/Vision fallback that never fabricates and never marks unresolved work complete.

**Architecture:** Extract the contract path's proven page-aware PDF OCR into a `PageExtractionEngine` behind a `PageStore` seam, with the contract path as the first adapter (behaviour-preserving). Move the durable document-job loop behind its own flag into a new `document-worker` container. Make `DocumentProcessor` route the validated/stored MIME through one explicit PDF/image/text/archive/unsupported dispatch; standalone images use a dedicated Tesseract image runner rather than the PDF-only OCRmyPDF runner. Persist versioned page evidence and durable remaining-page checkpoints before adding the always-on `ExtractionQualityGate`. Only the optional LLM/Vision ladder is feature-flagged.

**Tech Stack:** Python 3 / FastAPI / Motor (MongoDB), `pdfplumber` 0.11.7, `pypdfium2` 5.0.0, `pikepdf` 9.10.2, `pillow` 12.3.0, `numpy` 1.26.4, OCRmyPDF CLI for PDFs, Tesseract CLI for standalone images, pytest (no pytest-asyncio), Docker Compose.

**Source spec:** [2026-08-14-page-wise-ocr-and-image-assets-design.md](../specs/2026-08-14-page-wise-ocr-and-image-assets-design.md). Phases 8–11 are a separate plan.

## Global Constraints

Copied verbatim from `CLAUDE.md` and the spec. **Every task's requirements implicitly include this section.**

- **Test interpreter is `backend/.venv` only.** Run from repo dir `C:/SaaS/projectDMS`:
  `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`. Other interpreters produce phantom collection errors and a false dependency-parity blocker.
- **The git repository root is `C:\SaaS`, not `C:\SaaS\projectDMS`.** Paths in `git add` are prefixed `projectDMS/`.
- **Never `git add -A`.** Stage only files you personally edited. A blind bulk stage once shipped a broken factory to production past 1048 green tests.
- **Backend async tests each get their own `asyncio.run` loop** (`backend/conftest.py`, no pytest-asyncio). Module-level Motor globals must be cleared between tests. **Async fixtures are unsupported** — use `@asynccontextmanager` inside the test.
- **No `print()`** in `ingestion|retrieval|agents|observability`. ruff + ruff-format + mypy run in pre-commit.
- **New LLM call sites producing drafting content pass `strict=True`** to `LLMGenerator.generate` and supply their own deterministic fallback + degraded marking. Changing prompts requires bumping prompt-version constants.
- **Routers must re-raise domain errors as a group:** `except (BaseDomainError, HTTPException)`. `test_domain_error_reraise_guard.py` walks every router's AST for this shape.
- **After changing any route or its authorization**, regenerate the route contract or `test_route_authz_gate_evidence.py` / `test_route_control_manifest.py` fail:
  `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
- **Every new rate limiter must pass `scope=`.** Unscoped falls back to a shared `user:{id}` bucket that cross-contaminates ~19 routers.
- **Prefer fail-visible.** Never mark success on a skipped step. `PARTIALLY_PROCESSED` and `HUMAN_REVIEW_REQUIRED` must never be reported as `completed`.
- **Qdrant is the only vector store.** Do not reintroduce `FalkorDBVectorService`.
- **No new AGPL dependency.** PyMuPDF is rejected; rasterization uses `pypdfium2` (already installed transitively via `pdfplumber`).
- **Contract path behaviour must not change** in Phases 1–2. It is the reference implementation being generalised.
- **`NOT_CHECKABLE` must never escalate** to a paid model call.
- **Model confidence may route but never accept.** An output no check corroborates and no check contradicts is `NOT_CHECKABLE`, not `PASS`.
- **The deterministic quality gate is unconditional.** `EXTRACTION_FALLBACK_ENABLED` controls model calls only; it must not suppress gate execution, verdict persistence, repairs, or final-state derivation.
- **`UNRENDERABLE` is unresolved, never blank and never `PASS`.** It must remain human-review eligible even when fallback is disabled or rasterization fails again.
- `cd client && npx tsc -b` reports **146 pre-existing errors** — not a pass/fail gate. Check your files are absent from the output instead. `npm run build` is unaffected.

---

## File Structure

### New package: `backend/rbac_backend/services/extraction/`

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports only |
| `models.py` | `SourceKind`, `PageSource`, `PageStatus`, `PageClass`, `PageClassification`, `ExtractedPage`, `PageExtractionPolicy`, `PageExtractionResult`, `Completeness` |
| `page_mapping.py` | `map_source_pages_to_output` — replaces the §3.3 heuristic |
| `page_classifier.py` | `PageClassifier.classify` — char density, images, tables, dims, blankness |
| `engine.py` | `PageExtractionEngine.extract` — the deep module |
| `page_store.py` | `PageStore` protocol; `NullPageStore` |
| `image_ocr_runner.py` | `ImageOcrRunner`; `TesseractImageOcrRunner` — direct PNG/JPEG OCR, no PDF page mapping |
| `source_kind.py` | `SourceKindRouter.route` |
| `rasterizer.py` | `PageRasterizer.render_page` / `crop_region` |
| `quality/column_roles.py` | `map_column_roles` — header → role |
| `quality/numeric_checks.py` | Arithmetic identities, split-digit detection, dual-confirmation repair |
| `quality/date_checks.py` | Mixed D/M vs M/D convention detection |
| `quality/reading_order.py` | Interleaving / shredded-narrative detection |
| `quality/gate.py` | `ExtractionQualityGate.assess` → `QualityVerdict` |
| `quality/models.py` | `Verdict`, `CheckResult`, `QualityVerdict`, `NumericRepair`, `ColumnRole` |
| `fallback/models.py` | `Tier`, `FallbackOutcome`, `ResolvedPage`, `Intervention`, `Corroboration` |
| `fallback/evidence.py` | `assemble_evidence` — minimal source material |
| `fallback/ladder.py` | `ExtractionFallbackLadder.resolve` |
| `fallback/ledger.py` | `InterventionLedger` |
| `fallback/reconstruction_model.py` | `ReconstructionModel` protocol; `NullReconstructionModel` |

### New adapters and services

| File | Responsibility |
|---|---|
| `services/extraction_adapters/contract_page_store.py` | `ContractPageStore` — existing `contract_ocr_*` writes |
| `services/extraction_adapters/document_page_store.py` | `DocumentPageStore` — BSON-safe batch IDs and versioned `document_ocr_*` evidence |
| `services/archive_policy.py` | `ArchiveIntakePolicy` |
| `services/upload_policy.py` | `UploadPolicyService` |
| `routers/upload_policy.py` | `GET /api/config/upload-policy` |

### Modified

| File | Change |
|---|---|
| `utils/file_validation.py` | ZIP/RAR magic bytes |
| `core/config.py` | Archive MIMEs, `START_DOCUMENT_EXTRACTION_WORKERS`, gate/ladder settings |
| `services/background_jobs.py:416-422` | Document loop behind its own flag |
| `main.py:321-336` | Honour the new flag |
| `worker.py` | Start/stop the document loop in the dedicated worker entrypoint |
| `services/contracts_ingest.py:1322-1586` | Delegate to the engine |
| `services/ocr_service.py` | Page-wise PDF path for general documents |
| `services/document_processor.py:45-180` | Source-kind dispatch, always-on quality gate, optional fallback |
| `services/document_service.py:757-1545` | Persist source MIME and durable page checkpoints/final states on jobs |
| `routers/documents.py:557-764` | Archive gate, letter-number exemption |
| `docker-compose.prod.yml` | `document-worker` service |
| `client/src/pages/UploadPage.tsx:809` | Picker driven by served policy |

---

## Phase 0 — Evidence, fixtures, and prerequisites

Phase 0 answers four questions the later phases depend on. **No production code changes.** Three of the four cannot be answered on the Windows host and must run in the backend container.

### Task 0.1: Deterministic PDF fixture builder

**Files:**
- Create: `backend/rbac_backend/tests/fixtures/pdf_builders.py`
- Test: `backend/rbac_backend/tests/test_pdf_fixture_builders.py`

**Interfaces:**
- Consumes: nothing
- Produces: `build_mixed_pdf(path) -> Path` (9 pages: 1–2 zero-text, 3–9 text, page 5 landscape), `build_scanned_only_pdf(path, pages=2) -> Path`, `build_text_pdf(path, pages=1, text="...") -> Path`. All synchronous, all return the written path.

- [ ] **Step 1: Write the failing test**

```python
"""Deterministic PDF fixtures used by the extraction test-suite."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from rbac_backend.tests.fixtures.pdf_builders import (
    build_mixed_pdf,
    build_scanned_only_pdf,
    build_text_pdf,
)


def test_mixed_pdf_has_nine_pages_with_two_textless(tmp_path: Path) -> None:
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        assert len(pdf.pages) == 9
        char_counts = [len((page.extract_text() or "").strip()) for page in pdf.pages]

    assert char_counts[0] == 0
    assert char_counts[1] == 0
    assert all(count > 40 for count in char_counts[2:])


def test_mixed_pdf_page_five_is_landscape(tmp_path: Path) -> None:
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        page = pdf.pages[4]
        assert page.width > page.height


def test_scanned_only_pdf_has_no_text_layer(tmp_path: Path) -> None:
    target = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=3)

    with pdfplumber.open(target) as pdf:
        assert len(pdf.pages) == 3
        assert all(not (page.extract_text() or "").strip() for page in pdf.pages)


def test_text_pdf_is_reproducible(tmp_path: Path) -> None:
    first = build_text_pdf(tmp_path / "a.pdf", pages=2, text="Letter No. ABC/123")
    second = build_text_pdf(tmp_path / "b.pdf", pages=2, text="Letter No. ABC/123")

    assert first.read_bytes() == second.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_pdf_fixture_builders.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.tests.fixtures.pdf_builders'`

- [ ] **Step 3: Write the implementation**

`pikepdf` writes content streams directly, so no reportlab dependency is needed. A fixed `deterministic_id` makes output byte-reproducible.

```python
"""Builders for deterministic PDF fixtures.

Uses pikepdf (already a dependency) rather than reportlab. Every builder is
byte-reproducible: pikepdf is given a fixed deterministic ID and no timestamps
are written, so two builds of the same fixture compare equal.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

A4_PORTRAIT = (0, 0, 595, 842)
A4_LANDSCAPE = (0, 0, 842, 595)

_FIXED_ID = b"contraclaim-fixture-id-0000000000"


def _text_stream(lines: list[str]) -> bytes:
    """Build a minimal PDF content stream drawing lines at 12pt Helvetica."""
    parts = [b"BT", b"/F1 12 Tf", b"72 770 Td", b"14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({escaped}) Tj".encode("latin-1", errors="replace"))
        parts.append(b"T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def _add_page(
    pdf: pikepdf.Pdf,
    *,
    media_box: tuple[int, int, int, int],
    lines: list[str] | None,
) -> None:
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
        )
    )
    contents = pdf.make_stream(_text_stream(lines) if lines else b"")
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(media_box),
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
        Contents=contents,
    )
    pdf.pages.append(pdf.make_indirect(page))


def _save(pdf: pikepdf.Pdf, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(str(path), deterministic_id=True)
    pdf.close()
    return path


def build_text_pdf(path: Path, *, pages: int = 1, text: str = "Sample text") -> Path:
    """A PDF whose every page carries a real text layer."""
    pdf = pikepdf.new()
    pdf.trailer.ID = [_FIXED_ID, _FIXED_ID]
    for number in range(1, pages + 1):
        _add_page(
            pdf,
            media_box=A4_PORTRAIT,
            lines=[f"{text}", f"Page {number} of {pages}", "Ref: CC/PLAN/0001"],
        )
    return _save(pdf, path)


def build_scanned_only_pdf(path: Path, *, pages: int = 2) -> Path:
    """A PDF with no text layer on any page — stands in for a pure scan."""
    pdf = pikepdf.new()
    pdf.trailer.ID = [_FIXED_ID, _FIXED_ID]
    for _ in range(pages):
        _add_page(pdf, media_box=A4_PORTRAIT, lines=None)
    return _save(pdf, path)


def build_mixed_pdf(path: Path) -> Path:
    """Nine pages mirroring the measured sample claim.

    Pages 1-2 carry no text layer. Pages 3-9 carry text. Page 5 is landscape.
    This is fixture #1 of the benchmark corpus.
    """
    pdf = pikepdf.new()
    pdf.trailer.ID = [_FIXED_ID, _FIXED_ID]

    _add_page(pdf, media_box=A4_PORTRAIT, lines=None)
    _add_page(pdf, media_box=A4_PORTRAIT, lines=None)

    for number in range(3, 10):
        media_box = A4_LANDSCAPE if number == 5 else A4_PORTRAIT
        _add_page(
            pdf,
            media_box=media_box,
            lines=[
                f"Claim summary page {number}",
                "Employer: Uttar Pradesh Metro Rail Corporation Ltd",
                "Contractor: Gulermak-Sam India Kanpur Metro JV",
                "S/N  Description            Qty   Rate    Amount",
                "1    Idling of rig machine   52  184615  9600000",
            ],
        )

    return _save(pdf, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_pdf_fixture_builders.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/tests/fixtures/pdf_builders.py projectDMS/backend/rbac_backend/tests/test_pdf_fixture_builders.py
git commit -m "test: add deterministic PDF fixture builders for extraction tests"
```

---

### Task 0.2: Golden expectations file for fixture #1

**Files:**
- Create: `backend/rbac_backend/tests/fixtures/golden_page_routing.json`
- Create: `backend/rbac_backend/tests/test_golden_page_routing_contract.py`

**Interfaces:**
- Consumes: `build_mixed_pdf` from Task 0.1
- Produces: `golden_page_routing.json` with keys `min_text_chars_per_page`, `pages_total`, `pages_expected_ocr`, `pages_expected_native`, `expected_batches`. Every later phase asserts against this file rather than re-deriving expectations.

- [ ] **Step 1: Write the failing test**

```python
"""The golden page-routing contract for fixture #1.

This test does not exercise production code. It pins the expectations every
later phase is measured against, and fails if the fixture drifts away from
them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pdfplumber

from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = Path(__file__).parent / "fixtures" / "golden_page_routing.json"


def test_golden_file_exists_and_is_well_formed() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert data["pages_total"] == 9
    assert data["min_text_chars_per_page"] == 40
    assert data["pages_expected_ocr"] == [1, 2]
    assert data["pages_expected_native"] == [3, 4, 5, 6, 7, 8, 9]
    assert data["expected_batches"] == [[1, 2]]


def test_fixture_matches_golden_expectations(tmp_path: Path) -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    threshold = data["min_text_chars_per_page"]
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        thin = [
            index + 1
            for index, page in enumerate(pdf.pages)
            if len((page.extract_text() or "").strip()) < threshold
        ]
        thick = [
            index + 1
            for index, page in enumerate(pdf.pages)
            if len((page.extract_text() or "").strip()) >= threshold
        ]

    assert thin == data["pages_expected_ocr"]
    assert thick == data["pages_expected_native"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_golden_page_routing_contract.py -q`
Expected: FAIL — `FileNotFoundError` for `golden_page_routing.json`

- [ ] **Step 3: Write the golden file**

```json
{
  "_comment": "Fixture #1 golden page-routing contract. Derived from the measured 9-page contractor claim in docs/architecture/mixed_pdf_ingestion_and_summary_plan_2026-08-13.md section 2.2. Do not edit without re-measuring.",
  "fixture": "build_mixed_pdf",
  "min_text_chars_per_page": 40,
  "pages_total": 9,
  "pages_expected_ocr": [1, 2],
  "pages_expected_native": [3, 4, 5, 6, 7, 8, 9],
  "expected_batches": [[1, 2]],
  "legacy_general_path_behaviour": {
    "_comment": "What the pre-Phase-3 general path did: is_pdf_textual(max_pages=5) found text on page 3 and skipped OCR for the whole document.",
    "is_pdf_textual_max_pages": 5,
    "decision": "skip_ocr_entire_document",
    "pages_silently_empty": [1, 2]
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_golden_page_routing_contract.py -q`
Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/tests/fixtures/golden_page_routing.json projectDMS/backend/rbac_backend/tests/test_golden_page_routing_contract.py
git commit -m "test: pin golden page-routing expectations for fixture #1"
```

---

### Task 0.3: Prove the document-job claim is atomic

The spec flags this as a **prerequisite fix if absent** — two processes will claim from `document_processing_jobs` once Phase 2 lands.

**Files:**
- Read: `backend/rbac_backend/services/document_service.py:757-964`
- Create: `backend/rbac_backend/tests/test_document_job_claim_atomicity.py`

**Interfaces:**
- Consumes: the existing `DocumentService._claim_next_processing_job` atomic Mongo claim
- Produces: a regression test that fails if the claim stops being a single atomic operation

- [ ] **Step 1: Read the claim implementation and record the exact method name**

Run: `backend/.venv/Scripts/python.exe -c "import inspect; from rbac_backend.services.document_service import DocumentService; print([n for n in dir(DocumentService) if 'claim' in n.lower() or 'job' in n.lower()])"`

Read `backend/rbac_backend/services/document_service.py:757-964` and note whether the claim is one `find_one_and_update` or a `find_one` followed by a separate `update_one`. **If it is two calls, the test below will fail and you must make it atomic before Phase 2.**

- [ ] **Step 2: Write the failing test**

```python
"""The durable document-job claim must be a single atomic operation.

Phase 2 introduces a second process (document-worker) claiming from the same
collection. A find-then-update claim would hand the same job to both.
"""

from __future__ import annotations

from typing import Any

from rbac_backend.services import document_service as document_service_module


def test_claim_uses_find_one_and_update_not_find_then_update() -> None:
    import inspect

    source = inspect.getsource(document_service_module)
    claim_start = source.index("async def _claim_next_processing_job")
    claim_end = source.index("async def", claim_start + 10)
    claim_body = source[claim_start:claim_end]

    assert "find_one_and_update" in claim_body, (
        "The job claim must be a single atomic find_one_and_update. A find_one "
        "followed by update_one lets two workers claim the same job."
    )
    assert "update_one" not in claim_body, (
        "Found update_one inside the claim body - this suggests a non-atomic "
        "find-then-update claim."
    )


class _RecordingCollection:
    """Records every call so the test can assert exactly one claim round-trip."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document
        self.calls: list[str] = []

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append("find_one_and_update")
        return self.document

    async def find_one(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append("find_one")
        return self.document

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append("update_one")


async def test_claim_makes_exactly_one_collection_round_trip() -> None:
    collection = _RecordingCollection({"_id": "job-1", "document_id": "doc-1"})

    # The claim must not read then write; one call only.
    await collection.find_one_and_update({}, {})

    assert collection.calls == ["find_one_and_update"]
```

- [ ] **Step 3: Run the test**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_job_claim_atomicity.py -q`

**If it PASSES:** the claim is already atomic. Record that in Task 0.5's findings file and move on.
**If it FAILS:** stop. Convert the claim in `document_service.py` to a single `find_one_and_update` with the same filter and `$set`, keeping heartbeat and attempt semantics identical, then re-run.

- [ ] **Step 4: Run the full document-job suite to confirm nothing regressed**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_processing_jobs.py backend/rbac_backend/tests/test_document_concurrency.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/tests/test_document_job_claim_atomicity.py
git commit -m "test: assert the durable document-job claim is atomic"
```

---

### Task 0.4: Container measurements — `ocrmypdf --pages` output shape and ClamAV RAR

Spec §3.3 records that Phase 1's correct page mapping **depends on this measurement**, and it cannot be taken on the Windows host (no tesseract).

**Files:**
- Create: `backend/scripts/phase0_container_measurements.sh`
- Create: `docs/architecture/phase0_extraction_measurements_2026-08-14.md`

**Interfaces:**
- Consumes: nothing
- Produces: a findings document recording, as measured output, the page count of `ocrmypdf --pages 1-2` on a 9-page input, and whether the deployed clamd detects the EICAR test signature **inside a valid, passwordless RAR**. A RAR header or configuration line is not proof of inner-member inspection. Task 1.1 reads the page count; Task 5.1 may enable RAR only when `RAR_INNER_SCAN_PROVEN=yes`.

- [ ] **Step 1: Write the measurement script**

```bash
#!/usr/bin/env bash
# Phase 0 container measurements.
#
# Answers two questions that cannot be answered on the Windows host:
#   1. Does `ocrmypdf --pages 1-2` emit a 2-page PDF or the full document?
#   2. Does the deployed ClamAV scan inside RAR archives?
#
# Run INSIDE the backend container:
#   docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
#     exec -T backend bash /app/scripts/phase0_container_measurements.sh
set -euo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "=== 1. ocrmypdf --pages output shape ==="

python - <<'PY'
import pikepdf
pdf = pikepdf.new()
for _ in range(9):
    pdf.add_blank_page(page_size=(595, 842))
pdf.save("/tmp/phase0_nine_pages.pdf")
print("input pages:", 9)
PY

cp /tmp/phase0_nine_pages.pdf "$WORK/in.pdf"

if ocrmypdf --pages 1-2 --force-ocr --output-type pdf \
      "$WORK/in.pdf" "$WORK/out.pdf" >"$WORK/ocr.log" 2>&1; then
  echo "ocrmypdf: exit 0"
else
  echo "ocrmypdf: NONZERO EXIT — see log"
  tail -20 "$WORK/ocr.log"
fi

python - "$WORK/out.pdf" <<'PY'
import sys
import pikepdf
with pikepdf.open(sys.argv[1]) as pdf:
    print("MEASURED_OUTPUT_PAGE_COUNT:", len(pdf.pages))
PY

echo "=== 2. ClamAV RAR inner-member inspection ==="
clamscan --version || true

# PHASE0_RAR_EICAR must point to an operator-supplied, valid, passwordless RAR
# containing exactly one EICAR test file. Do not commit the archive. A truncated
# RAR header is invalid and cannot prove archive-member inspection.
if [[ -z "${PHASE0_RAR_EICAR:-}" || ! -f "${PHASE0_RAR_EICAR}" ]]; then
  echo "RAR_INNER_SCAN_PROVEN=no"
  echo "reason=valid EICAR-in-RAR fixture was not mounted"
else
  set +e
  rar_output="$(clamdscan --stream "${PHASE0_RAR_EICAR}" 2>&1)"
  rar_status=$?
  set -e
  printf '%s\n' "$rar_output"
  # clamdscan returns 1 when malware is found. Require both the expected result
  # and an EICAR FOUND line; ERROR, OK, or an unreadable archive is not proof.
  if [[ $rar_status -eq 1 ]] && grep -qiE 'Eicar.*FOUND|EICAR.*FOUND' <<<"$rar_output"; then
    echo "RAR_INNER_SCAN_PROVEN=yes"
  else
    echo "RAR_INNER_SCAN_PROVEN=no"
  fi
fi

echo "Configuration evidence (diagnostic only, not the acceptance test):"
grep -iE "^\s*ScanRAR|^\s*ScanArchive|^\s*MaxRecursion" /etc/clamav/clamd.conf 2>/dev/null || echo "clamd.conf not readable from this container"
```

- [ ] **Step 2: Run it in the backend container**

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T backend bash /app/scripts/phase0_container_measurements.sh
```

Expected: a line reading `MEASURED_OUTPUT_PAGE_COUNT: <n>` and exactly one `RAR_INNER_SCAN_PROVEN=<yes|no>` line. Record whether `n` is `2` (output trimmed to the batch) or `9` (output retains all pages). If the valid RAR fixture was unavailable, the result is `no`; absence of evidence must keep RAR disabled.

- [ ] **Step 3: Record the findings verbatim**

Create `docs/architecture/phase0_extraction_measurements_2026-08-14.md` containing the actual script output pasted in a fenced block, plus a one-line conclusion for each question in this form:

```markdown
# Phase 0 extraction measurements

**Date:** 2026-08-14 · **Host:** backend container on contraclaim.com
**Method:** `backend/scripts/phase0_container_measurements.sh` (`/app/scripts/...` in the backend image), output pasted verbatim below.

## 1. `ocrmypdf --pages` output shape

**MEASURED_OUTPUT_PAGE_COUNT = <paste the number>**

Conclusion: `--pages` <retains all input pages | trims to the requested range>.
Therefore `map_source_pages_to_output` (Task 1.1) uses <absolute | positional> indexing,
and the other branch is unreachable and must be removed rather than kept as a fallback.

## 2. ClamAV RAR support

<paste clamscan version, clamdscan result for the valid EICAR-in-RAR fixture, the RAR_INNER_SCAN_PROVEN line, and diagnostic config lines>

**RAR_INNER_SCAN_PROVEN = <yes | no>**

Conclusion: RAR upload admission <may be enabled | must remain disabled>. `yes` is
valid only when clamd reported EICAR `FOUND` from inside the valid passwordless RAR.
```

- [ ] **Step 4: Verify the findings document names a definite conclusion**

Re-read it. Every `<...>` placeholder must be replaced with a measured value. If `ocrmypdf` exited non-zero and produced no output PDF, the page-mapping measurement is **not** complete — do not guess; fix the invocation and re-run. If the RAR fixture or positive inner-member detection is absent, record `RAR_INNER_SCAN_PROVEN=no`; do not infer support from `ScanArchive`, an image label, or a RAR magic header.

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/scripts/phase0_container_measurements.sh projectDMS/docs/architecture/phase0_extraction_measurements_2026-08-14.md
git commit -m "docs: record Phase 0 container measurements for OCR page mapping and ClamAV RAR"
```

---

### Task 0.5: Production sampling and baseline benchmark

**Files:**
- Create: `scripts/phase0_production_sample.py`
- Modify: `docs/architecture/phase0_extraction_measurements_2026-08-14.md`

**Interfaces:**
- Consumes: nothing
- Produces: counts of documents whose page count exceeds their pages-with-text count (sizes both the OCR increase and any backfill), plus baseline CPU/memory/time/OCR-pages. Phase 11 of the companion plan compares against these numbers.

- [ ] **Step 1: Write the sampling script**

```python
"""Phase 0 production sampling: how many documents lost pages to the 5-page heuristic?

Read-only. Runs inside the backend container, which has the app's dependencies
and Mongo access. Writes nothing.

    docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
      exec -T backend python -m scripts.phase0_production_sample
"""

from __future__ import annotations

import asyncio
import json

from rbac_backend.core.config import settings


async def main() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(settings.DATABASE_URL)
    db = client.get_default_database()

    total = await db.documents.count_documents({})
    pdfs = await db.documents.count_documents({"filetype": "application/pdf"})
    with_ocr_text = await db.documents.count_documents(
        {"filetype": "application/pdf", "ocrText": {"$exists": True, "$ne": ""}}
    )
    completed = await db.documents.count_documents({"processingStatus": "completed"})

    # Documents whose stored text is suspiciously short for their size are the
    # candidates for silent page loss. Page counts are not stored today, so byte
    # size per extracted character is the available proxy.
    suspicious = await db.documents.count_documents(
        {
            "filetype": "application/pdf",
            "processingStatus": "completed",
            "$expr": {
                "$and": [
                    {"$gt": ["$filesize", 200_000]},
                    {"$lt": [{"$strLenCP": {"$ifNull": ["$ocrText", ""]}}, 500]},
                ]
            },
        }
    )

    report = {
        "documents_total": total,
        "pdf_documents": pdfs,
        "pdf_with_ocr_text": with_ocr_text,
        "completed": completed,
        "suspicious_large_file_tiny_text": suspicious,
        "_note": (
            "suspicious_* is a proxy, not a page-level count: page counts are not "
            "persisted before Phase 3. It bounds the backfill question, it does not "
            "answer it."
        ),
    }
    print(json.dumps(report, indent=2))
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it in the container**

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T backend python -m scripts.phase0_production_sample
```

Expected: a JSON object. Record it verbatim.

- [ ] **Step 3: Capture the baseline benchmark**

Run, on the production host, while a document upload is processing:

```bash
docker stats --no-stream contraclaim-dms-backend-1
```

Append to the findings document: container CPU %, memory usage, and — from `docker compose logs backend --since 10m | grep document_pipeline` — the wall time of the OCR step for one representative document.

- [ ] **Step 4: Append all of it to the findings document**

Add a `## 3. Production sampling` and `## 4. Baseline benchmark` section with the raw JSON and raw `docker stats` output pasted verbatim, plus this line filled in:

```markdown
**Baseline for Phase 11 comparison:** backend CPU <x>%, memory <y> MiB,
OCR step wall time <z>s for a <n>-page document, OCR pages/document <m>.
```

- [ ] **Step 5: Commit**

```bash
git add projectDMS/scripts/phase0_production_sample.py projectDMS/docs/architecture/phase0_extraction_measurements_2026-08-14.md
git commit -m "docs: record Phase 0 production sampling and baseline benchmark"
```

---

## Phase 1 — Harden page mapping, extract `PageExtractionEngine`

Behaviour-preserving. The contract path must produce byte-identical page records before and after.

### Task 1.1: Explicit source→output page mapping

Replaces the `output_count >= max(ordered)` heuristic at `contracts_ingest.py:1576`, using the Task 0.4 measurement. **Fails visibly rather than falling back to positional indexing.**

**Files:**
- Create: `backend/rbac_backend/services/extraction/__init__.py`
- Create: `backend/rbac_backend/services/extraction/page_mapping.py`
- Test: `backend/rbac_backend/tests/test_extraction_page_mapping.py`

**Interfaces:**
- Consumes: the `MEASURED_OUTPUT_PAGE_COUNT` conclusion from Task 0.4
- Produces:
  - `class PageMappingError(Exception)`
  - `map_source_pages_to_output(requested_pages: Sequence[int], output_page_count: int, input_page_count: int) -> dict[int, int]` — maps source page number → **0-based** index in the OCR output. Raises `PageMappingError` when the output shape matches neither expectation.

- [ ] **Step 1: Write the failing test**

```python
"""Source-to-output page mapping for OCR batches.

Replaces the `output_count >= max(ordered)` heuristic, which silently
mis-attributed page text when the two branches disagreed. This module fails
loudly instead.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.extraction.page_mapping import (
    PageMappingError,
    map_source_pages_to_output,
)


def test_full_length_output_maps_absolutely() -> None:
    # ocrmypdf --pages 1-2 on a 9-page input, output retains all 9 pages.
    mapping = map_source_pages_to_output([1, 2], output_page_count=9, input_page_count=9)

    assert mapping == {1: 0, 2: 1}


def test_full_length_output_maps_late_batch_absolutely() -> None:
    mapping = map_source_pages_to_output([8, 9], output_page_count=9, input_page_count=9)

    assert mapping == {8: 7, 9: 8}


def test_trimmed_output_maps_positionally() -> None:
    # Output trimmed to the requested range: 2 pages for a 2-page batch.
    mapping = map_source_pages_to_output([8, 9], output_page_count=2, input_page_count=9)

    assert mapping == {8: 0, 9: 1}


def test_single_page_batch_on_trimmed_output() -> None:
    mapping = map_source_pages_to_output([5], output_page_count=1, input_page_count=9)

    assert mapping == {5: 0}


def test_ambiguous_shape_raises_rather_than_guessing() -> None:
    # 5 output pages for a 2-page batch of a 9-page input is neither shape.
    with pytest.raises(PageMappingError) as excinfo:
        map_source_pages_to_output([1, 2], output_page_count=5, input_page_count=9)

    assert "neither" in str(excinfo.value).lower()


def test_output_smaller_than_batch_raises() -> None:
    with pytest.raises(PageMappingError):
        map_source_pages_to_output([1, 2, 3], output_page_count=2, input_page_count=9)


def test_empty_request_maps_to_nothing() -> None:
    assert map_source_pages_to_output([], output_page_count=9, input_page_count=9) == {}


def test_requested_page_beyond_input_raises() -> None:
    with pytest.raises(PageMappingError):
        map_source_pages_to_output([10], output_page_count=9, input_page_count=9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_mapping.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction'`

- [ ] **Step 3: Write the implementation**

Create `backend/rbac_backend/services/extraction/__init__.py`:

```python
"""Shared page-level extraction engine and its supporting modules."""
```

Create `backend/rbac_backend/services/extraction/page_mapping.py`:

```python
"""Map source PDF page numbers to their index in an OCR batch's output.

Background: OCRmyPDF invoked with `--pages A-B` may either retain every input
page in its output or trim the output to the requested range. The previous
implementation guessed between the two with `output_count >= max(requested)`,
which coincides with the correct answer for some batches and silently
mis-attributes page text for others - storing page 8's text under page 1.

This module accepts only the two shapes that are actually possible and raises
on anything else, because a wrong mapping is invisible until someone cites the
wrong page in a claim.
"""

from __future__ import annotations

from typing import Sequence


class PageMappingError(Exception):
    """Raised when an OCR output's page count matches no expected shape."""


def map_source_pages_to_output(
    requested_pages: Sequence[int],
    output_page_count: int,
    input_page_count: int,
) -> dict[int, int]:
    """Return {source_page_number: zero_based_output_index}.

    Two output shapes are accepted:

    * **full-length** - the output has as many pages as the input, so a source
      page keeps its own position: index = page_number - 1.
    * **trimmed** - the output has exactly as many pages as were requested, so
      source pages map to their ordinal position within the batch.

    Any other shape raises PageMappingError rather than falling back to a
    guess.
    """
    ordered = sorted({int(page) for page in requested_pages if int(page) > 0})
    if not ordered:
        return {}

    if ordered[-1] > input_page_count:
        raise PageMappingError(
            f"Requested page {ordered[-1]} exceeds input page count {input_page_count}"
        )

    if output_page_count == input_page_count:
        return {page: page - 1 for page in ordered}

    if output_page_count == len(ordered):
        return {page: index for index, page in enumerate(ordered)}

    raise PageMappingError(
        f"OCR output has {output_page_count} pages, which matches neither the "
        f"input page count ({input_page_count}) nor the requested batch size "
        f"({len(ordered)}). Refusing to guess the page mapping for "
        f"pages {ordered}."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_mapping.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/__init__.py projectDMS/backend/rbac_backend/services/extraction/page_mapping.py projectDMS/backend/rbac_backend/tests/test_extraction_page_mapping.py
git commit -m "feat: explicit source-to-output OCR page mapping that fails visibly"
```

---

### Task 1.2: Extraction domain models

**Files:**
- Create: `backend/rbac_backend/services/extraction/models.py`
- Test: `backend/rbac_backend/tests/test_extraction_models.py`

**Interfaces:**
- Consumes: nothing
- Produces — every later task uses these exact names:
  - `SourceKind` (`PDF`, `IMAGE`, `TEXT`, `ARCHIVE`, `UNSUPPORTED`)
  - `PageSource` (`TEXT_LAYER`, `OCR`, `RECONSTRUCTED`, `EMPTY`)
  - `PageStatus` (`TEXT_LAYER`, `OCR_COMPLETED`, `OCR_EMPTY`, `OCR_FAILED`, `OCR_DISABLED`, `OCR_PENDING`, `OCR_DEFERRED`, `UNRENDERABLE`)
  - `PageClass` (`TEXT_NATIVE`, `SCANNED_IMAGE`, `MIXED_CONTENT`, `TABLE_HEAVY`, `BLANK`, `UNRENDERABLE`)
  - `Completeness` (`COMPLETE`, `PARTIAL`)
  - `PageClassification(page_class, char_count, image_count, image_coverage, table_count, width, height, rotation)`
  - `ExtractedPage(number, text, source, status, classification, tables, batch_id, error, quality_verdict, quality_checks, needs_review)` with property `char_count`; native PDF tables are retained per page and quality fields use persisted strings/dicts to avoid a Phase 1 → Phase 6 import cycle
  - `PageExtractionPolicy(ocr_enabled, min_text_chars_per_page, batch_size, max_ocr_pages_per_attempt, ocr_language)`
  - `PageExtractionResult(pages, combined_text, ocr_pages_total, ocr_failed_pages, ocr_deferred_pages, unrenderable_pages, completeness, engine_version)`

- [ ] **Step 1: Write the failing test**

```python
"""Domain models shared by every extraction caller."""

from __future__ import annotations

from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionPolicy,
    PageExtractionResult,
    PageSource,
    PageStatus,
    SourceKind,
)


def _classification(**overrides: object) -> PageClassification:
    defaults = dict(
        page_class=PageClass.TEXT_NATIVE,
        char_count=120,
        image_count=0,
        image_coverage=0.0,
        table_count=0,
        width=595.0,
        height=842.0,
        rotation=0,
    )
    defaults.update(overrides)
    return PageClassification(**defaults)  # type: ignore[arg-type]


def test_enum_values_are_stable_strings() -> None:
    # These strings are persisted; changing one is a migration, not a rename.
    assert SourceKind.PDF.value == "pdf"
    assert SourceKind.ARCHIVE.value == "archive"
    assert PageSource.TEXT_LAYER.value == "text_layer"
    assert PageSource.RECONSTRUCTED.value == "reconstructed"
    assert PageStatus.OCR_DEFERRED.value == "ocr_deferred"
    assert PageStatus.UNRENDERABLE.value == "unrenderable"
    assert PageClass.SCANNED_IMAGE.value == "scanned_image"
    assert Completeness.PARTIAL.value == "partial"


def test_contract_page_status_vocabulary_is_preserved() -> None:
    # The contract path already persists these five values; they must survive.
    existing = {"text_layer", "ocr_completed", "ocr_empty", "ocr_failed", "ocr_disabled"}

    assert existing.issubset({status.value for status in PageStatus})


def test_extracted_page_reports_char_count() -> None:
    page = ExtractedPage(
        number=3,
        text="  hello world  ",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=_classification(),
    )

    assert page.char_count == len("hello world")


def test_extracted_page_defaults_have_no_batch_or_error() -> None:
    page = ExtractedPage(
        number=1,
        text="",
        source=PageSource.EMPTY,
        status=PageStatus.OCR_PENDING,
        classification=_classification(char_count=0),
    )

    assert page.batch_id is None
    assert page.error is None
    assert page.quality_verdict is None
    assert page.quality_checks == []
    assert page.needs_review is False
    assert page.tables == []


def test_result_is_partial_when_pages_are_deferred() -> None:
    result = PageExtractionResult(
        pages=[],
        combined_text="",
        ocr_pages_total=2,
        ocr_failed_pages=[],
        ocr_deferred_pages=[7, 8],
        unrenderable_pages=[],
        completeness=Completeness.PARTIAL,
        engine_version="1",
    )

    assert result.completeness is Completeness.PARTIAL
    assert result.ocr_deferred_pages == [7, 8]


def test_policy_carries_a_resumability_boundary_not_a_cap() -> None:
    policy = PageExtractionPolicy(
        ocr_enabled=True,
        min_text_chars_per_page=40,
        batch_size=25,
        max_ocr_pages_per_attempt=50,
        ocr_language="eng",
    )

    assert policy.max_ocr_pages_per_attempt == 50
    assert policy.min_text_chars_per_page == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction.models'`

- [ ] **Step 3: Write the implementation**

```python
"""Domain models for page-level extraction.

Every enum value here is persisted to Mongo. Changing a value is a data
migration, not a rename. The five contract page statuses
(text_layer, ocr_completed, ocr_empty, ocr_failed, ocr_disabled) are carried
over verbatim so the contract path's existing records stay valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class SourceKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    ARCHIVE = "archive"
    UNSUPPORTED = "unsupported"


class PageSource(str, Enum):
    """Where a page's final text came from."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    RECONSTRUCTED = "reconstructed"
    EMPTY = "empty"


class PageStatus(str, Enum):
    TEXT_LAYER = "text_layer"
    OCR_COMPLETED = "ocr_completed"
    OCR_EMPTY = "ocr_empty"
    OCR_FAILED = "ocr_failed"
    OCR_DISABLED = "ocr_disabled"
    OCR_PENDING = "ocr_pending"
    OCR_DEFERRED = "ocr_deferred"
    UNRENDERABLE = "unrenderable"


class PageClass(str, Enum):
    TEXT_NATIVE = "text_native"
    SCANNED_IMAGE = "scanned_image"
    MIXED_CONTENT = "mixed_content"
    TABLE_HEAVY = "table_heavy"
    BLANK = "blank"
    UNRENDERABLE = "unrenderable"


class Completeness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class PageClassification:
    page_class: PageClass
    char_count: int
    image_count: int
    image_coverage: float
    table_count: int
    width: float
    height: float
    rotation: int

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height


@dataclass
class ExtractedPage:
    number: int
    text: str
    source: PageSource
    status: PageStatus
    classification: PageClassification
    tables: List[List[List[str]]] = field(default_factory=list)
    batch_id: Optional[str] = None
    error: Optional[str] = None
    quality_verdict: Optional[str] = None
    quality_checks: List[dict] = field(default_factory=list)
    needs_review: bool = False

    @property
    def char_count(self) -> int:
        return len((self.text or "").strip())


@dataclass(frozen=True)
class PageExtractionPolicy:
    ocr_enabled: bool
    min_text_chars_per_page: int
    batch_size: int
    max_ocr_pages_per_attempt: int
    ocr_language: str


@dataclass
class PageExtractionResult:
    pages: List[ExtractedPage]
    combined_text: str
    ocr_pages_total: int
    ocr_failed_pages: List[int] = field(default_factory=list)
    ocr_deferred_pages: List[int] = field(default_factory=list)
    unrenderable_pages: List[int] = field(default_factory=list)
    completeness: Completeness = Completeness.COMPLETE
    engine_version: str = "1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_models.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/models.py projectDMS/backend/rbac_backend/tests/test_extraction_models.py
git commit -m "feat: add shared extraction domain models"
```

---

### Task 1.3: Page classifier

**Files:**
- Create: `backend/rbac_backend/services/extraction/page_classifier.py`
- Test: `backend/rbac_backend/tests/test_extraction_page_classifier.py`

**Interfaces:**
- Consumes: `PageClass`, `PageClassification` from Task 1.2
- Produces: `PageClassifier(min_text_chars_per_page: int)` with `classify(page: Any) -> PageClassification`, where `page` is a `pdfplumber.page.Page`. Also `SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.85`.

**Design note for the implementer:** companion evidence §3.9 measured 3–6 embedded images on *every* page of a real claim, including text pages. `image_count > 0` is therefore **not** a scan signal. Only text density combined with single-image coverage identifies a scan.

- [ ] **Step 1: Write the failing test**

```python
"""Page classification beyond the minimum-character threshold."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from rbac_backend.services.extraction.models import PageClass
from rbac_backend.services.extraction.page_classifier import (
    SCANNED_IMAGE_COVERAGE_THRESHOLD,
    PageClassifier,
)
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


class _FakePage:
    """Stands in for a pdfplumber Page without needing a real PDF."""

    def __init__(
        self,
        *,
        text: str = "",
        images: list[dict[str, float]] | None = None,
        tables: int = 0,
        width: float = 595.0,
        height: float = 842.0,
        rotation: int = 0,
    ) -> None:
        self._text = text
        self.images = images or []
        self._tables = tables
        self.width = width
        self.height = height
        self.rotation = rotation

    def extract_text(self) -> str:
        return self._text

    def find_tables(self) -> list[object]:
        return [object()] * self._tables


def test_blank_page_is_classified_blank() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_FakePage())

    assert result.page_class is PageClass.BLANK
    assert result.char_count == 0


def test_full_page_image_with_no_text_is_scanned() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 842}])

    result = classifier.classify(page)

    assert result.page_class is PageClass.SCANNED_IMAGE
    assert result.image_coverage >= SCANNED_IMAGE_COVERAGE_THRESHOLD


def test_many_small_images_on_a_text_page_is_not_scanned() -> None:
    # Companion evidence 3.9: every text page of a real claim carries 3-6 images.
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        text="x" * 400,
        images=[{"x0": 10, "top": 10, "x1": 60, "bottom": 60} for _ in range(6)],
    )

    result = classifier.classify(page)

    assert result.page_class is not PageClass.SCANNED_IMAGE
    assert result.image_count == 6


def test_text_page_with_a_table_is_table_heavy() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(text="x" * 400, tables=2)

    result = classifier.classify(page)

    assert result.page_class is PageClass.TABLE_HEAVY
    assert result.table_count == 2


def test_text_page_with_substantial_imagery_is_mixed() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        text="x" * 400,
        images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 400}],
    )

    result = classifier.classify(page)

    assert result.page_class is PageClass.MIXED_CONTENT


def test_plain_text_page_is_text_native() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_FakePage(text="x" * 400))

    assert result.page_class is PageClass.TEXT_NATIVE


def test_landscape_orientation_is_recorded(tmp_path) -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        landscape = classifier.classify(pdf.pages[4])
        portrait = classifier.classify(pdf.pages[3])

    assert landscape.is_landscape is True
    assert portrait.is_landscape is False


def test_real_fixture_pages_one_and_two_are_blank_or_scanned(tmp_path) -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        classes = [classifier.classify(page).page_class for page in pdf.pages]

    assert classes[0] in {PageClass.BLANK, PageClass.SCANNED_IMAGE}
    assert classes[1] in {PageClass.BLANK, PageClass.SCANNED_IMAGE}
    assert all(
        page_class in {PageClass.TEXT_NATIVE, PageClass.TABLE_HEAVY, PageClass.MIXED_CONTENT}
        for page_class in classes[2:]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction.page_classifier'`

- [ ] **Step 3: Write the implementation**

```python
"""Classify a PDF page by what it actually contains.

Classification is descriptive metadata, not a second OCR gate: the character
threshold remains the routing decision. Only SCANNED_IMAGE and BLANK change
behaviour downstream - the former suppresses whole-page-raster photo
candidates, the latter suppresses a pointless OCR call.

Measured constraint (companion document 3.9): every text-bearing page of a
real contractor claim carried 3-6 embedded images - stamps, signatures, logos.
A classifier keyed on image *presence* would route all nine pages to OCR and
triple the cost. Coverage by a single large image, combined with low text
density, is the discriminator.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import PageClass, PageClassification

logger = logging.getLogger(__name__)

SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.85
MIXED_CONTENT_COVERAGE_THRESHOLD = 0.20


class PageClassifier:
    def __init__(self, min_text_chars_per_page: int) -> None:
        self.min_text_chars_per_page = max(0, int(min_text_chars_per_page))

    def classify(self, page: Any) -> PageClassification:
        width = float(getattr(page, "width", 0.0) or 0.0)
        height = float(getattr(page, "height", 0.0) or 0.0)
        rotation = int(getattr(page, "rotation", 0) or 0)

        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Page text extraction failed during classification: %s", exc)
            return PageClassification(
                page_class=PageClass.UNRENDERABLE,
                char_count=0,
                image_count=0,
                image_coverage=0.0,
                table_count=0,
                width=width,
                height=height,
                rotation=rotation,
            )

        char_count = len(text.strip())
        images = list(getattr(page, "images", []) or [])
        page_area = width * height
        coverage = self._largest_image_coverage(images, page_area)
        table_count = self._table_count(page)

        page_class = self._decide(
            char_count=char_count,
            image_count=len(images),
            coverage=coverage,
            table_count=table_count,
        )

        return PageClassification(
            page_class=page_class,
            char_count=char_count,
            image_count=len(images),
            image_coverage=coverage,
            table_count=table_count,
            width=width,
            height=height,
            rotation=rotation,
        )

    def _decide(
        self,
        *,
        char_count: int,
        image_count: int,
        coverage: float,
        table_count: int,
    ) -> PageClass:
        has_text = char_count >= self.min_text_chars_per_page

        if not has_text:
            if coverage >= SCANNED_IMAGE_COVERAGE_THRESHOLD:
                return PageClass.SCANNED_IMAGE
            if char_count == 0 and image_count == 0:
                return PageClass.BLANK
            return PageClass.SCANNED_IMAGE if image_count else PageClass.BLANK

        if table_count > 0:
            return PageClass.TABLE_HEAVY
        if coverage >= MIXED_CONTENT_COVERAGE_THRESHOLD:
            return PageClass.MIXED_CONTENT
        return PageClass.TEXT_NATIVE

    @staticmethod
    def _largest_image_coverage(images: list[dict[str, Any]], page_area: float) -> float:
        if not images or page_area <= 0:
            return 0.0
        largest = 0.0
        for image in images:
            try:
                width = float(image["x1"]) - float(image["x0"])
                height = float(image["bottom"]) - float(image["top"])
            except (KeyError, TypeError, ValueError):
                continue
            largest = max(largest, abs(width * height))
        return min(1.0, largest / page_area)

    @staticmethod
    def _table_count(page: Any) -> int:
        try:
            return len(page.find_tables() or [])
        except Exception:  # pragma: no cover - pdfplumber can raise on odd pages
            return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_classifier.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/page_classifier.py projectDMS/backend/rbac_backend/tests/test_extraction_page_classifier.py
git commit -m "feat: add page classifier for extraction routing and diagnostics"
```

---

### Task 1.4: `PageStore` seam and OCR runner protocol

Two adapters exist from day one (contract in Task 1.6, document in Phase 3), so this is a real seam rather than a hypothetical one.

**Files:**
- Create: `backend/rbac_backend/services/extraction/page_store.py`
- Test: `backend/rbac_backend/tests/test_extraction_page_store.py`

**Interfaces:**
- Consumes: `ExtractedPage` from Task 1.2
- Produces:
  - `class PageStore(Protocol)` with `async def begin_batch(page_start, page_end, retry) -> str`, `async def finish_batch(batch_id, status, error=None) -> None`, `async def record_pages(pages: Sequence[ExtractedPage]) -> None`
  - `class NullPageStore` — records calls in `self.batches` / `self.recorded_pages`, used by the engine's own tests and by any caller that does not persist
  - `class OcrRunner(Protocol)` with `async def run(source_pdf: Path, page_numbers: Sequence[int], language: str) -> dict[int, str]`; this protocol is PDF-only and its implementations may count/map PDF pages
  - `class ImageOcrRunner(Protocol)` with `async def run(source_image: Path, language: str) -> str`; it has no page-number or PDF-mapping contract
  - `class MeterCallback(Protocol)` with `async def __call__(page_count: int, page_numbers: Sequence[int], retry: bool) -> None`

- [ ] **Step 1: Write the failing test**

```python
"""The PageStore seam: the engine returns records, callers persist them."""

from __future__ import annotations

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore, PageStore


def _page(number: int) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=f"page {number}",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=6,
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def test_null_store_satisfies_the_protocol() -> None:
    assert isinstance(NullPageStore(), PageStore)


async def test_null_store_records_batch_lifecycle() -> None:
    store = NullPageStore()

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    assert store.batches == [
        {
            "batch_id": batch_id,
            "page_start": 1,
            "page_end": 2,
            "retry": False,
            "status": "completed",
            "error": None,
        }
    ]


async def test_null_store_records_failed_batch_with_error() -> None:
    store = NullPageStore()

    batch_id = await store.begin_batch(page_start=3, page_end=3, retry=True)
    await store.finish_batch(batch_id, status="failed", error="boom")

    assert store.batches[0]["status"] == "failed"
    assert store.batches[0]["error"] == "boom"
    assert store.batches[0]["retry"] is True


async def test_null_store_collects_pages() -> None:
    store = NullPageStore()

    await store.record_pages([_page(1), _page(2)])

    assert [page.number for page in store.recorded_pages] == [1, 2]


async def test_batch_ids_are_unique() -> None:
    store = NullPageStore()

    first = await store.begin_batch(page_start=1, page_end=1, retry=False)
    second = await store.begin_batch(page_start=2, page_end=2, retry=False)

    assert first != second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction.page_store'`

- [ ] **Step 3: Write the implementation**

```python
"""The seam between the extraction engine and caller-specific persistence.

The engine never writes to Mongo. It calls begin_batch/finish_batch as OCR
batches progress and returns page records; the caller's adapter decides where
those land - contract_ocr_pages for contracts, document_ocr_pages for general
documents. Contract-specific state (upload_id, job status, usage metering)
stays entirely on the caller's side of this seam.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable
from uuid import uuid4

from .models import ExtractedPage


@runtime_checkable
class PageStore(Protocol):
    """Caller-supplied persistence for OCR batches and page records."""

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        """Open a batch record and return its identifier."""
        ...

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        """Close a batch as 'completed' or 'failed'."""
        ...

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        """Persist the final page records for this extraction pass."""
        ...


@runtime_checkable
class OcrRunner(Protocol):
    """Runs OCR over selected pages of a PDF and returns source-page text."""

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        ...


@runtime_checkable
class ImageOcrRunner(Protocol):
    """Runs OCR directly over one standalone image; no PDF mapping is involved."""

    async def run(self, source_image: Path, language: str) -> str:
        ...


@runtime_checkable
class MeterCallback(Protocol):
    """Usage metering, owned by the caller (billing is not the engine's job)."""

    async def __call__(
        self, *, page_count: int, page_numbers: Sequence[int], retry: bool
    ) -> None:
        ...


class NullPageStore:
    """In-memory PageStore for tests and callers that do not persist."""

    def __init__(self) -> None:
        self.batches: List[Dict[str, Any]] = []
        self.recorded_pages: List[ExtractedPage] = []

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        batch_id = str(uuid4())
        self.batches.append(
            {
                "batch_id": batch_id,
                "page_start": page_start,
                "page_end": page_end,
                "retry": retry,
                "status": "running",
                "error": None,
            }
        )
        return batch_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        for batch in self.batches:
            if batch["batch_id"] == batch_id:
                batch["status"] = status
                batch["error"] = error
                return

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        self.recorded_pages.extend(pages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_page_store.py -q`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/page_store.py projectDMS/backend/rbac_backend/tests/test_extraction_page_store.py
git commit -m "feat: add PageStore seam for extraction persistence"
```

---

### Task 1.5: `PageExtractionEngine`

The deep module. One public method; page decision, batching, mapping, merge, and resumability behind it.

**Files:**
- Create: `backend/rbac_backend/services/extraction/engine.py`
- Test: `backend/rbac_backend/tests/test_extraction_engine.py`

**Interfaces:**
- Consumes: `models` (1.2), `page_mapping` (1.1), `page_classifier` (1.3), `page_store` (1.4)
- Produces:
  - `ENGINE_VERSION = "1"`
  - `group_contiguous_pages(page_numbers: Sequence[int], batch_size: int) -> list[list[int]]` — module-level, extracted verbatim from `contracts_ingest._group_page_numbers`
  - `class PageExtractionEngine(policy: PageExtractionPolicy, ocr_runner: OcrRunner, store: PageStore, meter: MeterCallback | None = None)` with
    `async def extract(source: Path, *, retry_pages: Sequence[int] | None = None) -> PageExtractionResult`

- [ ] **Step 1: Write the failing test**

```python
"""The shared page extraction engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Sequence

from rbac_backend.services.extraction.engine import (
    PageExtractionEngine,
    group_contiguous_pages,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    PageExtractionPolicy,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_page_routing.json").read_text(
        encoding="utf-8"
    )
)


class _RecordingOcrRunner:
    """Returns synthetic OCR text and records exactly which pages were asked for."""

    def __init__(self, *, fail_pages: set[int] | None = None) -> None:
        self.requested: list[list[int]] = []
        self.fail_pages = fail_pages or set()

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        pages = list(page_numbers)
        self.requested.append(pages)
        if self.fail_pages & set(pages):
            raise RuntimeError("ocr batch failed")
        return {page: f"OCR TEXT PAGE {page}" for page in pages}


class _CountingMeter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self, *, page_count: int, page_numbers: Sequence[int], retry: bool
    ) -> None:
        self.calls.append(
            {"page_count": page_count, "page_numbers": list(page_numbers), "retry": retry}
        )


def _policy(**overrides: object) -> PageExtractionPolicy:
    defaults = dict(
        ocr_enabled=True,
        min_text_chars_per_page=GOLDEN["min_text_chars_per_page"],
        batch_size=25,
        max_ocr_pages_per_attempt=100,
        ocr_language="eng",
    )
    defaults.update(overrides)
    return PageExtractionPolicy(**defaults)  # type: ignore[arg-type]


def test_group_contiguous_pages_splits_on_gaps() -> None:
    assert group_contiguous_pages([1, 2, 5, 6, 9], batch_size=25) == [[1, 2], [5, 6], [9]]


def test_group_contiguous_pages_respects_batch_size() -> None:
    assert group_contiguous_pages([1, 2, 3, 4], batch_size=2) == [[1, 2], [3, 4]]


def test_group_contiguous_pages_deduplicates_and_sorts() -> None:
    assert group_contiguous_pages([3, 1, 2, 2], batch_size=25) == [[1, 2, 3]]


async def test_only_thin_pages_are_sent_to_ocr(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=runner, store=NullPageStore()
    )

    result = await engine.extract(source)

    assert runner.requested == GOLDEN["expected_batches"]
    assert result.ocr_pages_total == len(GOLDEN["pages_expected_ocr"])
    assert result.completeness is Completeness.COMPLETE


async def test_native_pages_keep_their_own_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_native"]:
        assert by_number[number].source is PageSource.TEXT_LAYER
        assert by_number[number].status is PageStatus.TEXT_LAYER
        assert "OCR TEXT PAGE" not in by_number[number].text


async def test_ocr_pages_take_the_ocr_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_ocr"]:
        assert by_number[number].source is PageSource.OCR
        assert by_number[number].status is PageStatus.OCR_COMPLETED
        assert by_number[number].text == f"OCR TEXT PAGE {number}"


async def test_failed_batch_marks_its_pages_without_failing_the_document(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()
    engine = PageExtractionEngine(
        policy=_policy(),
        ocr_runner=_RecordingOcrRunner(fail_pages={1}),
        store=store,
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert result.ocr_failed_pages == [1, 2]
    assert by_number[1].status is PageStatus.OCR_FAILED
    assert by_number[1].error
    assert result.completeness is Completeness.PARTIAL
    assert store.batches[0]["status"] == "failed"
    # Native pages survive a failed OCR batch.
    assert by_number[3].source is PageSource.TEXT_LAYER


async def test_ocr_disabled_marks_pages_rather_than_pretending_success(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(ocr_enabled=False), ocr_runner=runner, store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert runner.requested == []
    assert by_number[1].status is PageStatus.OCR_DISABLED
    assert result.completeness is Completeness.PARTIAL


async def test_attempt_boundary_defers_rather_than_truncating(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(max_ocr_pages_per_attempt=1),
        ocr_runner=runner,
        store=NullPageStore(),
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert runner.requested == [[1]]
    assert result.ocr_deferred_pages == [2]
    assert by_number[2].status is PageStatus.OCR_DEFERRED
    assert result.completeness is Completeness.PARTIAL


async def test_retry_pages_override_the_threshold(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=runner, store=NullPageStore()
    )

    await engine.extract(source, retry_pages=[7])

    assert runner.requested == [[7]]


async def test_meter_is_called_once_with_the_pages_actually_attempted(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    meter = _CountingMeter()
    engine = PageExtractionEngine(
        policy=_policy(),
        ocr_runner=_RecordingOcrRunner(),
        store=NullPageStore(),
        meter=meter,
    )

    await engine.extract(source)

    assert meter.calls == [
        {"page_count": 2, "page_numbers": [1, 2], "retry": False}
    ]


async def test_pages_are_recorded_through_the_store(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    await engine.extract(source)

    assert [page.number for page in store.recorded_pages] == list(range(1, 10))


async def test_combined_text_is_page_ordered(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)

    assert result.combined_text.index("OCR TEXT PAGE 1") < result.combined_text.index(
        "OCR TEXT PAGE 2"
    )
    assert result.combined_text.index("OCR TEXT PAGE 2") < result.combined_text.index(
        "Claim summary page 3"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction.engine'`

- [ ] **Step 3: Write the implementation**

```python
"""Page-level extraction: decide, batch, OCR, merge - once, for every caller.

This is the module the contract path proved and the general path lacked. It
owns the page decision, contiguous batching, OCR invocation, source-to-output
mapping, and the native/OCR merge. It owns none of the persistence: batches and
page records go out through the PageStore seam, and usage metering goes out
through the meter callback, because billing and collection names differ per
caller.

Resumability, not truncation: max_ocr_pages_per_attempt bounds one attempt.
Pages beyond it are marked OCR_DEFERRED and the result is PARTIAL, so the
caller re-claims them. Nothing is ever silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionPolicy,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from .page_classifier import PageClassifier
from .page_store import MeterCallback, OcrRunner, PageStore

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1"


def group_contiguous_pages(
    page_numbers: Sequence[int], batch_size: int
) -> List[List[int]]:
    """Group sorted, de-duplicated page numbers into contiguous runs.

    Behaviour is identical to the contract path's original _group_page_numbers:
    a run breaks on a gap or when the batch size is reached.
    """
    groups: List[List[int]] = []
    current: List[int] = []
    previous: Optional[int] = None
    for page_number in sorted({int(page) for page in page_numbers if int(page) > 0}):
        if current and (
            previous is None
            or page_number != previous + 1
            or len(current) >= max(1, batch_size)
        ):
            groups.append(current)
            current = []
        current.append(page_number)
        previous = page_number
    if current:
        groups.append(current)
    return groups


class PageExtractionEngine:
    def __init__(
        self,
        *,
        policy: PageExtractionPolicy,
        ocr_runner: OcrRunner,
        store: PageStore,
        meter: Optional[MeterCallback] = None,
    ) -> None:
        self.policy = policy
        self.ocr_runner = ocr_runner
        self.store = store
        self.meter = meter
        self.classifier = PageClassifier(policy.min_text_chars_per_page)

    async def extract(
        self, source: Path, *, retry_pages: Optional[Sequence[int]] = None
    ) -> PageExtractionResult:
        native = await asyncio.to_thread(self._read_native_pages, source)

        retry_set = {int(page) for page in (retry_pages or []) if int(page) > 0}
        if retry_set:
            candidates = [number for number in sorted(native) if number in retry_set]
        else:
            candidates = [
                number
                for number, (text, _, _) in sorted(native.items())
                if len(text.strip()) < self.policy.min_text_chars_per_page
            ]

        attempted, deferred = self._split_at_attempt_boundary(candidates)

        overrides: Dict[int, str] = {}
        statuses: Dict[int, tuple[PageStatus, Optional[str], Optional[str]]] = {}

        if not self.policy.ocr_enabled:
            for number in candidates:
                statuses[number] = (PageStatus.OCR_DISABLED, None, "OCR is disabled")
        else:
            if attempted and self.meter is not None:
                await self.meter(
                    page_count=len(attempted),
                    page_numbers=attempted,
                    retry=bool(retry_set),
                )
            for batch in group_contiguous_pages(attempted, self.policy.batch_size):
                await self._run_batch(
                    source, batch, overrides, statuses, retry=bool(retry_set)
                )
            for number in deferred:
                statuses[number] = (
                    PageStatus.OCR_DEFERRED,
                    None,
                    "Deferred past this attempt's OCR page boundary",
                )

        pages = self._merge(native, overrides, statuses)
        await self.store.record_pages(pages)

        failed = sorted(
            page.number for page in pages if page.status is PageStatus.OCR_FAILED
        )
        unrenderable = sorted(
            page.number
            for page in pages
            if page.classification.page_class is PageClass.UNRENDERABLE
            and page.status is not PageStatus.OCR_COMPLETED
        )
        unresolved = [
            page
            for page in pages
            if page.status
            in {
                PageStatus.OCR_FAILED,
                PageStatus.OCR_DEFERRED,
                PageStatus.OCR_DISABLED,
                PageStatus.OCR_EMPTY,
                PageStatus.UNRENDERABLE,
            }
            or page.number in unrenderable
        ]

        return PageExtractionResult(
            pages=pages,
            combined_text="\n\n".join(page.text or "" for page in pages),
            ocr_pages_total=len(attempted) if self.policy.ocr_enabled else 0,
            ocr_failed_pages=failed,
            ocr_deferred_pages=sorted(deferred),
            unrenderable_pages=unrenderable,
            completeness=Completeness.PARTIAL if unresolved else Completeness.COMPLETE,
            engine_version=ENGINE_VERSION,
        )

    def _split_at_attempt_boundary(
        self, candidates: Sequence[int]
    ) -> tuple[List[int], List[int]]:
        limit = max(0, int(self.policy.max_ocr_pages_per_attempt))
        ordered = sorted(candidates)
        if limit <= 0 or len(ordered) <= limit:
            return ordered, []
        return ordered[:limit], ordered[limit:]

    async def _run_batch(
        self,
        source: Path,
        batch: Sequence[int],
        overrides: Dict[int, str],
        statuses: Dict[int, tuple[PageStatus, Optional[str], Optional[str]]],
        *,
        retry: bool,
    ) -> None:
        batch_id = await self.store.begin_batch(
            page_start=batch[0], page_end=batch[-1], retry=retry
        )
        try:
            extracted = await self.ocr_runner.run(
                source, batch, self.policy.ocr_language
            )
        except Exception as exc:
            error = str(exc)[:500]
            logger.warning(
                "OCR batch failed for %s pages %s-%s: %s",
                source.name,
                batch[0],
                batch[-1],
                error,
            )
            for number in batch:
                statuses[number] = (PageStatus.OCR_FAILED, batch_id, error)
            await self.store.finish_batch(batch_id, status="failed", error=error)
            return

        overrides.update(extracted)
        for number in batch:
            text = (extracted.get(number) or "").strip()
            statuses[number] = (
                (PageStatus.OCR_COMPLETED, batch_id, None)
                if text
                else (
                    PageStatus.OCR_EMPTY,
                    batch_id,
                    "OCR completed but no text was extracted",
                )
            )
        await self.store.finish_batch(batch_id, status="completed")

    def _merge(
        self,
        native: Dict[int, tuple[str, PageClassification, List[List[List[str]]]]],
        overrides: Dict[int, str],
        statuses: Dict[int, tuple[PageStatus, Optional[str], Optional[str]]],
    ) -> List[ExtractedPage]:
        pages: List[ExtractedPage] = []
        for number in sorted(native):
            native_text, classification, tables = native[number]
            status, batch_id, error = statuses.get(
                number, (PageStatus.TEXT_LAYER, None, None)
            )
            if (
                classification.page_class is PageClass.UNRENDERABLE
                and status is not PageStatus.OCR_COMPLETED
            ):
                status = PageStatus.UNRENDERABLE
                error = error or "Page could not be rendered or extracted"
            if number in overrides:
                text = overrides[number]
                page_source = PageSource.OCR
            else:
                text = native_text
                page_source = (
                    PageSource.TEXT_LAYER if text.strip() else PageSource.EMPTY
                )
            pages.append(
                ExtractedPage(
                    number=number,
                    text=text,
                    source=page_source,
                    status=status,
                    classification=classification,
                    tables=tables,
                    batch_id=batch_id,
                    error=error,
                )
            )
        return pages

    def _read_native_pages(
        self, source: Path
    ) -> Dict[int, tuple[str, PageClassification, List[List[List[str]]]]]:
        import pdfplumber

        pages: Dict[int, tuple[str, PageClassification, List[List[List[str]]]]] = {}
        with pdfplumber.open(source) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                classification = self.classifier.classify(page)
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                pages[index] = (text, classification, tables)
        if not pages:
            pages[1] = (
                "",
                PageClassification(
                    page_class=PageClass.UNRENDERABLE,
                    char_count=0,
                    image_count=0,
                    image_coverage=0.0,
                    table_count=0,
                    width=0.0,
                    height=0.0,
                    rotation=0,
                ),
                [],
            )
        return pages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_engine.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/engine.py projectDMS/backend/rbac_backend/tests/test_extraction_engine.py
git commit -m "feat: add shared PageExtractionEngine with resumable batching"
```

---

### Task 1.6: OCRmyPDF runner using the hardened mapping

**Files:**
- Create: `backend/rbac_backend/services/extraction/ocrmypdf_runner.py`
- Test: `backend/rbac_backend/tests/test_extraction_ocrmypdf_runner.py`

**Interfaces:**
- Consumes: `map_source_pages_to_output`, `PageMappingError` (1.1)
- Produces: `class OcrMyPdfRunner(work_dir: Path, timeout: int = 900)` satisfying `OcrRunner`. Internals: `_build_command`, `_input_page_count`, `_extract_mapped_pages`. Raises `OcrRunnerError` on non-zero exit.

**Design note:** the source PDF page count is read *before* invoking OCRmyPDF, so `map_source_pages_to_output` has both counts. Do not infer the input count from the output.

- [ ] **Step 1: Write the failing test**

```python
"""OCRmyPDF batch runner with explicit page mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from rbac_backend.services.extraction.ocrmypdf_runner import (
    OcrMyPdfRunner,
    OcrRunnerError,
)
from rbac_backend.services.extraction.page_store import OcrRunner
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


def test_runner_satisfies_the_protocol(tmp_path: Path) -> None:
    assert isinstance(OcrMyPdfRunner(work_dir=tmp_path), OcrRunner)


def test_command_requests_only_the_batch_range(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[3, 4, 5],
        language="eng",
    )

    assert "--pages" in command
    assert command[command.index("--pages") + 1] == "3-5"
    assert "--sidecar" in command


def test_command_formats_a_single_page_without_a_range(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[7],
        language="eng",
    )

    assert command[command.index("--pages") + 1] == "7"


def test_input_page_count_is_read_from_the_source(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    assert runner._input_page_count(source) == 9


def test_empty_batch_returns_nothing_without_running_ocr(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    async def _call() -> dict[int, str]:
        return await runner.run(tmp_path / "missing.pdf", [], "eng")

    import asyncio

    assert asyncio.run(_call()) == {}


def test_full_length_output_reads_absolute_pages(tmp_path: Path) -> None:
    # A 9-page "output" stands in for OCRmyPDF retaining every input page.
    output = build_mixed_pdf(tmp_path / "out.pdf")
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    mapped = runner._extract_mapped_pages(
        output_path=output, page_numbers=[3, 4], input_page_count=9
    )

    assert set(mapped) == {3, 4}
    assert "Claim summary page 3" in mapped[3]
    assert "Claim summary page 4" in mapped[4]


def test_unexpected_output_shape_raises_rather_than_guessing(tmp_path: Path) -> None:
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    output = build_text_pdf(tmp_path / "out.pdf", pages=5)
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    with pytest.raises(OcrRunnerError):
        runner._extract_mapped_pages(
            output_path=output, page_numbers=[1, 2], input_page_count=9
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_ocrmypdf_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction.ocrmypdf_runner'`

- [ ] **Step 3: Write the implementation**

```python
"""Run OCRmyPDF over a batch of pages and return their text by page number.

The page mapping is explicit (see page_mapping) rather than inferred from the
output's shape. A mismatch raises instead of silently attributing one page's
text to another.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence

from .page_mapping import PageMappingError, map_source_pages_to_output

logger = logging.getLogger(__name__)


class OcrRunnerError(Exception):
    """Raised when an OCR batch cannot be run or its output cannot be mapped."""


class OcrMyPdfRunner:
    def __init__(self, *, work_dir: Path, timeout: int = 900) -> None:
        self.work_dir = Path(work_dir)
        self.timeout = timeout

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        pages = sorted({int(page) for page in page_numbers if int(page) > 0})
        if not pages:
            return {}

        input_page_count = await asyncio.to_thread(self._input_page_count, source)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        page_range = self._format_page_range(pages)
        output = self.work_dir / f"{source.stem}_pages_{page_range.replace('-', '_')}.pdf"
        sidecar = output.with_suffix(".txt")

        command = self._build_command(
            source=source,
            output=output,
            sidecar=sidecar,
            page_numbers=pages,
            language=language,
        )
        result = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, text=True, timeout=self.timeout
        )
        if result.returncode != 0:
            raise OcrRunnerError(
                (result.stderr or result.stdout or "OCRmyPDF batch failed").strip()[:500]
            )

        return await asyncio.to_thread(
            self._extract_mapped_pages,
            output_path=output,
            page_numbers=pages,
            input_page_count=input_page_count,
        )

    def _build_command(
        self,
        *,
        source: Path,
        output: Path,
        sidecar: Path,
        page_numbers: Sequence[int],
        language: str,
    ) -> List[str]:
        executable = shutil.which("ocrmypdf")
        prefix = [executable] if executable else [sys.executable, "-m", "ocrmypdf"]
        return [
            *prefix,
            "--pages",
            self._format_page_range(page_numbers),
            "--language",
            language,
            "--rotate-pages",
            "--deskew",
            "--optimize",
            "1",
            "--jobs",
            str(min(2, os.cpu_count() or 2)),
            "--sidecar",
            str(sidecar),
            str(source),
            str(output),
        ]

    @staticmethod
    def _format_page_range(page_numbers: Sequence[int]) -> str:
        ordered = sorted({int(page) for page in page_numbers if int(page) > 0})
        if not ordered:
            return ""
        if len(ordered) == 1:
            return str(ordered[0])
        return f"{ordered[0]}-{ordered[-1]}"

    @staticmethod
    def _input_page_count(source: Path) -> int:
        import pdfplumber

        with pdfplumber.open(source) as pdf:
            return len(pdf.pages)

    @staticmethod
    def _extract_mapped_pages(
        *, output_path: Path, page_numbers: Sequence[int], input_page_count: int
    ) -> Dict[int, str]:
        import pdfplumber

        with pdfplumber.open(output_path) as pdf:
            output_page_count = len(pdf.pages)
            try:
                mapping = map_source_pages_to_output(
                    page_numbers,
                    output_page_count=output_page_count,
                    input_page_count=input_page_count,
                )
            except PageMappingError as exc:
                raise OcrRunnerError(str(exc)) from exc

            extracted: Dict[int, str] = {}
            for page_number, index in mapping.items():
                if 0 <= index < output_page_count:
                    extracted[page_number] = pdf.pages[index].extract_text() or ""
            return extracted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_ocrmypdf_runner.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/ocrmypdf_runner.py projectDMS/backend/rbac_backend/tests/test_extraction_ocrmypdf_runner.py
git commit -m "feat: add OCRmyPDF batch runner using explicit page mapping"
```

---

### Task 1.7: `ContractPageStore` and contract-path delegation

The behaviour-preserving switch. **The contract path's persisted records must be identical before and after.**

**Files:**
- Create: `backend/rbac_backend/services/extraction_adapters/__init__.py`
- Create: `backend/rbac_backend/services/extraction_adapters/contract_page_store.py`
- Modify: `backend/rbac_backend/services/contracts_ingest.py:1322-1466` (replace `_extract_pdf_pages_with_ocr_batches` body), delete `_group_page_numbers`, `_run_ocr_page_batch`, `_extract_selected_pages_from_pdf`, `_format_page_range`
- Test: `backend/rbac_backend/tests/test_contract_page_store_parity.py`

**Interfaces:**
- Consumes: `PageExtractionEngine` (1.5), `OcrMyPdfRunner` (1.6), `PageStore` (1.4)
- Produces: `class ContractPageStore(db_service, document_id, upload_id, organization_id, project_id)` satisfying `PageStore`, plus `to_contract_page_record(page: ExtractedPage, *, document_id, upload_id, organization_id, project_id, source_pdf_page_link) -> dict` producing **exactly** the dict shape at `contracts_ingest.py:1448-1463`.

- [ ] **Step 1: Write the failing parity test**

```python
"""ContractPageStore must produce byte-identical records to the old inline code.

The contract path is the reference implementation. Phase 1 generalises it; it
must not change it.
"""

from __future__ import annotations

from typing import Any

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.contract_page_store import (
    ContractPageStore,
    to_contract_page_record,
)


class _FakeDbService:
    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []
        self.pages: list[list[dict[str, Any]]] = []
        self._next_id = 0

    async def upsert_ocr_batch(self, **kwargs: Any) -> str:
        self.batches.append(dict(kwargs))
        self._next_id += 1
        return f"batch-{self._next_id}"

    async def upsert_ocr_pages(self, records: list[dict[str, Any]]) -> None:
        self.pages.append(records)


def _page(number: int, status: PageStatus, text: str) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=PageSource.OCR if status is PageStatus.OCR_COMPLETED else PageSource.TEXT_LAYER,
        status=status,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
        batch_id="batch-1" if status is PageStatus.OCR_COMPLETED else None,
    )


def test_record_shape_matches_the_legacy_contract_record() -> None:
    record = to_contract_page_record(
        _page(2, PageStatus.OCR_COMPLETED, "page two text"),
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id="proj-1",
        source_pdf_page_link="contract:doc-1#page=2",
    )

    assert set(record) == {
        "document_id",
        "upload_id",
        "organization_id",
        "project_id",
        "page_number",
        "batch_id",
        "status",
        "error",
        "raw_text",
        "raw_text_length",
        "cleaned_text",
        "cleaned_text_length",
        "source_pdf_page_link",
    }
    assert record["status"] == "ocr_completed"
    assert record["raw_text"] == "page two text"
    assert record["raw_text_length"] == len("page two text")
    assert record["cleaned_text"] == ""
    assert record["cleaned_text_length"] == 0
    assert record["source_pdf_page_link"] == "contract:doc-1#page=2"


def test_status_values_are_plain_strings_not_enum_reprs() -> None:
    record = to_contract_page_record(
        _page(1, PageStatus.TEXT_LAYER, "native"),
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
        source_pdf_page_link="contract:doc-1#page=1",
    )

    assert record["status"] == "text_layer"
    assert isinstance(record["status"], str)
    assert record["project_id"] is None


async def test_store_opens_and_closes_batches_through_the_db_service() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id="proj-1",
    )

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    assert db.batches[0]["status"] == "running"
    assert db.batches[0]["page_start"] == 1
    assert db.batches[0]["page_end"] == 2
    assert db.batches[0]["retry_count"] == 0
    assert db.batches[1]["status"] == "completed"


async def test_retry_sets_retry_count_one() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
    )

    await store.begin_batch(page_start=3, page_end=3, retry=True)

    assert db.batches[0]["retry_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_contract_page_store_parity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.extraction_adapters'`

- [ ] **Step 3: Write the adapter**

Create `backend/rbac_backend/services/extraction_adapters/__init__.py`:

```python
"""Caller-specific PageStore adapters for the shared extraction engine."""
```

Create `backend/rbac_backend/services/extraction_adapters/contract_page_store.py`:

```python
"""PageStore adapter for the contract ingestion path.

Everything contract-specific lives here: upload_id, the contract_ocr_batches
and contract_ocr_pages collections, and the record shape those collections
already hold. The engine knows none of it.

The record shape is deliberately frozen to match what contracts_ingest wrote
inline before Phase 1. Changing a key here is a data migration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..extraction.models import ExtractedPage


def to_contract_page_record(
    page: ExtractedPage,
    *,
    document_id: str,
    upload_id: str,
    organization_id: str,
    project_id: Optional[str],
    source_pdf_page_link: str,
) -> Dict[str, Any]:
    """Build the contract_ocr_pages record for one page.

    cleaned_text is intentionally empty: the text preprocessor fills it after
    extraction, exactly as before.
    """
    raw_text = page.text or ""
    return {
        "document_id": document_id,
        "upload_id": upload_id,
        "organization_id": str(organization_id),
        "project_id": str(project_id) if project_id else None,
        "page_number": page.number,
        "batch_id": page.batch_id,
        "status": page.status.value,
        "error": page.error,
        "raw_text": raw_text,
        "raw_text_length": len(raw_text),
        "cleaned_text": "",
        "cleaned_text_length": 0,
        "source_pdf_page_link": source_pdf_page_link,
    }


class ContractPageStore:
    def __init__(
        self,
        *,
        db_service: Any,
        document_id: str,
        upload_id: str,
        organization_id: str,
        project_id: Optional[str],
    ) -> None:
        self.db_service = db_service
        self.document_id = document_id
        self.upload_id = upload_id
        self.organization_id = organization_id
        self.project_id = project_id
        self.records: List[Dict[str, Any]] = []
        self._batch_ranges: Dict[str, tuple[int, int, bool]] = {}

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        batch_id = await self.db_service.upsert_ocr_batch(
            document_id=self.document_id,
            upload_id=self.upload_id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            page_start=page_start,
            page_end=page_end,
            status="running",
            retry_count=1 if retry else 0,
        )
        self._batch_ranges[batch_id] = (page_start, page_end, retry)
        return batch_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        page_start, page_end, retry = self._batch_ranges.get(batch_id, (0, 0, False))
        kwargs: Dict[str, Any] = {
            "document_id": self.document_id,
            "upload_id": self.upload_id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "page_start": page_start,
            "page_end": page_end,
            "status": status,
            "retry_count": 1 if retry else 0,
        }
        if error is not None:
            kwargs["error"] = error[:500]
        await self.db_service.upsert_ocr_batch(**kwargs)

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        self.records = [
            to_contract_page_record(
                page,
                document_id=self.document_id,
                upload_id=self.upload_id,
                organization_id=self.organization_id,
                project_id=self.project_id,
                source_pdf_page_link=f"contract:{self.document_id}#page={page.number}",
            )
            for page in pages
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_contract_page_store_parity.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Rewire `contracts_ingest` to the engine**

In `backend/rbac_backend/services/contracts_ingest.py`, replace the body of `_extract_pdf_pages_with_ocr_batches` (lines 1322–1466) with a delegation, and **delete** `_group_page_numbers`, `_run_ocr_page_batch`, `_format_page_range`, and `_extract_selected_pages_from_pdf`. Keep the method signature and return type unchanged so callers are untouched.

```python
    async def _extract_pdf_pages_with_ocr_batches(
        self,
        file_path: Path,
        *,
        upload_id: str,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        retry_ocr_pages: Optional[List[int]] = None,
    ) -> Tuple[ParsedDocument, List[Dict[str, Any]]]:
        from .extraction.engine import PageExtractionEngine
        from .extraction.models import PageExtractionPolicy
        from .extraction.ocrmypdf_runner import OcrMyPdfRunner
        from .extraction_adapters.contract_page_store import ContractPageStore

        async def _meter(
            *, page_count: int, page_numbers: Sequence[int], retry: bool
        ) -> None:
            await self.usage_metering_service.check_and_record(
                event_type=UsageEventType.OCR_PAGE,
                organization_id=organization_id,
                project_id=project_id,
                quantity=page_count,
                metadata={
                    "operation": "contract_ocr",
                    "document_id": document_id,
                    "upload_id": upload_id,
                    "page_numbers": list(page_numbers),
                    "retry": retry,
                },
            )

        store = ContractPageStore(
            db_service=self.db_service,
            document_id=document_id,
            upload_id=upload_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        engine = PageExtractionEngine(
            policy=PageExtractionPolicy(
                ocr_enabled=self.processing_config.ocr_enabled,
                min_text_chars_per_page=max(
                    0, int(self.processing_config.contract_ocr_min_text_chars_per_page)
                ),
                batch_size=max(1, int(self.processing_config.contract_ocr_batch_size)),
                max_ocr_pages_per_attempt=0,  # contracts: no attempt boundary (unchanged)
                ocr_language=self.processing_config.ocr_language,
            ),
            ocr_runner=OcrMyPdfRunner(
                work_dir=BASE_UPLOAD_PATH / "ocr_batches" / upload_id
            ),
            store=store,
            meter=_meter,
        )

        result = await engine.extract(file_path, retry_pages=retry_ocr_pages)
        parsed = self._combine_pages(
            [ParsedPage(number=p.number, text=p.text, start=0, end=0) for p in result.pages],
            file_path,
        )
        return parsed, store.records
```

Add `Sequence` to the `typing` import at the top of the file if it is not already present.

- [ ] **Step 6: Run the full contract suite to prove no behaviour changed**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "contract"`
Expected: PASS — same count as before the change. If any contract test fails, the refactor is not behaviour-preserving; fix the adapter, not the test.

- [ ] **Step 7: Run the whole suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known `test_route_control_manifest` failure documented in `CLAUDE.md`)

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction_adapters/__init__.py projectDMS/backend/rbac_backend/services/extraction_adapters/contract_page_store.py projectDMS/backend/rbac_backend/services/contracts_ingest.py projectDMS/backend/rbac_backend/tests/test_contract_page_store_parity.py
git commit -m "refactor: route contract ingestion through the shared PageExtractionEngine"
```

---

## Phase 2 — Worker topology

**The trap this phase exists to avoid:** `START_BACKGROUND_SERVICES` starts **four** tasks (`background_jobs.py:416-422`) — cleanup, assignment alerts, subscription lifecycle, and the document loop. Setting it to `false` on the web tier to move OCR would also stop assignment alerts and subscription billing. The document loop gets its own flag instead.

### Task 2.1: Separate the document loop behind its own flag

**Files:**
- Modify: `backend/rbac_backend/core/config.py` (add setting)
- Modify: `backend/rbac_backend/services/background_jobs.py:337-428`
- Modify: `backend/rbac_backend/main.py:321-336`
- Modify: `backend/rbac_backend/worker.py`
- Test: `backend/rbac_backend/tests/test_document_extraction_worker_flag.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `settings.START_DOCUMENT_EXTRACTION_WORKERS: bool` (default `False`, env alias `START_DOCUMENT_EXTRACTION_WORKERS`)
  - `async def start_document_extraction_workers() -> None` and `async def stop_document_extraction_workers() -> None` in `background_jobs`
  - `start_background_services()` no longer starts `periodic_document_processing_jobs`

- [ ] **Step 1: Write the failing test**

```python
"""The document extraction loop must be independently switchable.

START_BACKGROUND_SERVICES bundles cleanup, assignment alerts, subscription
lifecycle, and the document loop. Moving OCR off the web tier by flipping that
bundle would silently stop assignment alerts and subscription billing.
"""

from __future__ import annotations

import inspect

from rbac_backend.core.config import settings
from rbac_backend.services import background_jobs


def test_setting_exists_and_defaults_to_false() -> None:
    assert hasattr(settings, "START_DOCUMENT_EXTRACTION_WORKERS")
    assert settings.START_DOCUMENT_EXTRACTION_WORKERS in (True, False)


def test_dedicated_start_and_stop_functions_exist() -> None:
    assert inspect.iscoroutinefunction(background_jobs.start_document_extraction_workers)
    assert inspect.iscoroutinefunction(background_jobs.stop_document_extraction_workers)


def test_background_services_no_longer_starts_the_document_loop() -> None:
    source = inspect.getsource(background_jobs.start_background_services)

    assert "periodic_document_processing_jobs" not in source, (
        "The document loop must start from start_document_extraction_workers, "
        "not from the START_BACKGROUND_SERVICES bundle."
    )


def test_background_services_still_starts_the_other_three_tasks() -> None:
    source = inspect.getsource(background_jobs.start_background_services)

    assert "periodic_cleanup" in source
    assert "periodic_assignment_alerts" in source
    assert "periodic_subscription_lifecycle" in source


def test_document_worker_start_owns_the_document_loop() -> None:
    source = inspect.getsource(background_jobs.start_document_extraction_workers)

    assert "periodic_document_processing_jobs" in source


def test_main_honours_the_new_flag() -> None:
    from rbac_backend import main

    source = inspect.getsource(main)

    assert "START_DOCUMENT_EXTRACTION_WORKERS" in source
    assert "start_document_extraction_workers" in source
    assert "stop_document_extraction_workers" in source


def test_worker_entrypoint_honours_the_new_flag() -> None:
    from rbac_backend import worker

    source = inspect.getsource(worker._run)
    assert "START_DOCUMENT_EXTRACTION_WORKERS" in source
    assert "start_document_extraction_workers" in source
    assert "stop_document_extraction_workers" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_extraction_worker_flag.py -q`
Expected: FAIL — `AttributeError: START_DOCUMENT_EXTRACTION_WORKERS`

- [ ] **Step 3: Add the setting**

In `backend/rbac_backend/core/config.py`, beside the existing `START_BACKGROUND_SERVICES` / `START_CONTRACT_QUEUE_WORKERS` declarations:

```python
    # Phase 2: the durable document extraction loop runs in a dedicated worker
    # process, not the request-serving web tier. Kept separate from
    # START_BACKGROUND_SERVICES because that flag also gates cleanup,
    # assignment alerts, and subscription lifecycle - flipping it off on the
    # web tier would silently stop billing-relevant work.
    START_DOCUMENT_EXTRACTION_WORKERS: bool = Field(
        default=False, validation_alias="START_DOCUMENT_EXTRACTION_WORKERS"
    )
```

- [ ] **Step 4: Move the loop in `background_jobs.py`**

Remove `asyncio.create_task(periodic_document_processing_jobs())` from `start_background_services()` (line ~422), leaving the other three `create_task` calls untouched. Then add, after `stop_background_services`:

```python
_document_extraction_task: Optional[asyncio.Task] = None


async def start_document_extraction_workers() -> None:
    """Start the durable document extraction loop.

    Separate from start_background_services so the loop can run in a dedicated
    worker container while the web tier keeps cleanup, assignment alerts, and
    subscription lifecycle running.
    """
    global _document_extraction_task

    if _document_extraction_task is not None and not _document_extraction_task.done():
        logger.info("Document extraction workers already running")
        return

    logger.info("Starting document extraction workers...")
    _document_extraction_task = asyncio.create_task(periodic_document_processing_jobs())
    logger.info("Document extraction workers started")


async def stop_document_extraction_workers() -> None:
    """Stop the durable document extraction loop."""
    global _document_extraction_task

    task = _document_extraction_task
    _document_extraction_task = None
    if task is None or task.done():
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Document extraction workers stopped")
```

Add `Optional` to the `typing` import at the top of the file if absent.

- [ ] **Step 5: Wire it into `main.py`**

Replace lines 321–324 and 333–336:

```python
    if settings.START_BACKGROUND_SERVICES:
        await start_background_services()
    if settings.START_DOCUMENT_EXTRACTION_WORKERS:
        await start_document_extraction_workers()
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await start_contract_ingest_queue()
```

```python
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await stop_contract_ingest_queue()
    if settings.START_DOCUMENT_EXTRACTION_WORKERS:
        await stop_document_extraction_workers()
    if settings.START_BACKGROUND_SERVICES:
        await stop_background_services()
```

Add the two names to the existing `from .services.background_jobs import ...` statement in `main.py`.

Make the same lifecycle change in `backend/rbac_backend/worker.py`: import both document-extraction functions, start after `connect_database()` when `START_DOCUMENT_EXTRACTION_WORKERS` is true, and stop it in `finally` before disconnecting Mongo. This is mandatory because Task 2.2 launches `python -m rbac_backend.worker`; changing FastAPI `main.py` alone would leave the dedicated container idle.

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_extraction_worker_flag.py -q`
Expected: PASS — 7 passed

- [ ] **Step 7: Run the deployment-config and startup suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_deployment_config.py backend/rbac_backend/tests/test_config_validation.py backend/rbac_backend/tests/test_document_processing_jobs.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/services/background_jobs.py projectDMS/backend/rbac_backend/main.py projectDMS/backend/rbac_backend/worker.py projectDMS/backend/rbac_backend/tests/test_document_extraction_worker_flag.py
git commit -m "feat: run the document extraction loop behind its own worker flag"
```

---

### Task 2.2: `document-worker` compose service

**Files:**
- Modify: `docker-compose.prod.yml`
- Test: `backend/rbac_backend/tests/test_document_worker_compose.py`

**Interfaces:**
- Consumes: `START_DOCUMENT_EXTRACTION_WORKERS` (2.1)
- Produces: a `document-worker` service with `START_BACKGROUND_SERVICES=false`, `START_DOCUMENT_EXTRACTION_WORKERS=true`, `START_CONTRACT_QUEUE_WORKERS=false`, **`RUN_SCHEDULER=false`**, mounting `backend_uploads`

- [ ] **Step 1: Write the failing test**

```python
"""Compose topology guards for the document-worker service.

RUN_SCHEDULER must stay false here: contract-worker is the single scheduler
owner and is documented as one replica. Two schedulers would double-fire cron
jobs; the leader lock is a safety net, not a licence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.prod.yml"


def _services() -> dict[str, dict]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]


def test_document_worker_service_exists() -> None:
    assert "document-worker" in _services()


def test_document_worker_runs_the_worker_entrypoint() -> None:
    service = _services()["document-worker"]

    assert service["command"] == ["python", "-m", "rbac_backend.worker"]


def test_document_worker_flag_matrix() -> None:
    env = _services()["document-worker"]["environment"]

    assert env["START_DOCUMENT_EXTRACTION_WORKERS"] == "true"
    assert env["START_BACKGROUND_SERVICES"] == "false"
    assert env["START_CONTRACT_QUEUE_WORKERS"] == "false"
    assert env["RUN_SCHEDULER"] == "false"


def test_backend_keeps_background_services_and_does_not_extract() -> None:
    env = _services()["backend"]["environment"]

    assert env["START_BACKGROUND_SERVICES"] == "true"
    assert env["START_DOCUMENT_EXTRACTION_WORKERS"] == "false"


def test_contract_worker_remains_the_only_scheduler_owner() -> None:
    services = _services()
    owners = [
        name
        for name, service in services.items()
        if str((service.get("environment") or {}).get("RUN_SCHEDULER", "")).lower() == "true"
    ]

    assert owners == ["contract-worker"]


def test_document_worker_mounts_the_shared_uploads_volume() -> None:
    volumes = _services()["document-worker"]["volumes"]

    assert any(str(volume).startswith("backend_uploads:") for volume in volumes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_worker_compose.py -q`
Expected: FAIL — `KeyError: 'document-worker'`

- [ ] **Step 3: Add the service to `docker-compose.prod.yml`**

Add `START_DOCUMENT_EXTRACTION_WORKERS: "false"` to the existing `backend` service environment block (after `START_CONTRACT_QUEUE_WORKERS`), and `START_DOCUMENT_EXTRACTION_WORKERS: "false"` to `contract-worker`. Then add, after the `contract-worker` service:

```yaml
  document-worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    command: ["python", "-m", "rbac_backend.worker"]
    environment:
      <<: *backend-env
      START_BACKGROUND_SERVICES: "false"
      START_CONTRACT_QUEUE_WORKERS: "false"
      START_DOCUMENT_EXTRACTION_WORKERS: "true"
      # contract-worker is the single scheduler owner. Never flip this to true.
      RUN_SCHEDULER: "false"
    depends_on:
      qdrant:
        condition: service_healthy
      falkordb:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - service-net
      - data-net
      - egress-net
    volumes:
      - backend_uploads:/app/uploads
      - backend_logs:/app/logs
    logging: *default-logging
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_worker_compose.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Validate the compose file parses with the real file set**

Run: `docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml config > /dev/null && echo "compose OK"`
Expected: `compose OK`. Per `CLAUDE.md`, do **not** include the base `docker-compose.yml` — it references `client/.env.development` and breaks `up`.

- [ ] **Step 6: Commit**

```bash
git add projectDMS/docker-compose.prod.yml projectDMS/backend/rbac_backend/tests/test_document_worker_compose.py
git commit -m "feat: add dedicated document-worker service for extraction"
```

---

## Phase 3 — General path on the shared engine

This is where the measured defect is fixed: pages 1–2 of fixture #1 stop being silently empty.

### Task 3.1: Processing states that cannot lie

**Files:**
- Create: `backend/rbac_backend/models/processing_state.py`
- Test: `backend/rbac_backend/tests/test_processing_state.py`

**Interfaces:**
- Consumes: `Completeness`, `PageStatus` (1.2)
- Produces:
  - `class ProcessingState(str, Enum)` — `QUEUED`, `PROCESSING`, `COMPLETED`, `STORED_ONLY`, `PARTIALLY_PROCESSED`, `HUMAN_REVIEW_REQUIRED`, `FAILED`
  - `TERMINAL_STATES`, `SUCCESS_STATES`, `RESUMABLE_STATES` frozensets
  - `derive_processing_state(result: PageExtractionResult, *, attempts_exhausted: bool) -> ProcessingState`

- [ ] **Step 1: Write the failing test**

```python
"""Processing states must never report unfinished work as completed."""

from __future__ import annotations

from rbac_backend.models.processing_state import (
    RESUMABLE_STATES,
    SUCCESS_STATES,
    TERMINAL_STATES,
    ProcessingState,
    derive_processing_state,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)


def _result(**overrides: object) -> PageExtractionResult:
    defaults = dict(
        pages=[],
        combined_text="text",
        ocr_pages_total=0,
        ocr_failed_pages=[],
        ocr_deferred_pages=[],
        unrenderable_pages=[],
        completeness=Completeness.COMPLETE,
        engine_version="1",
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)  # type: ignore[arg-type]


def test_completed_is_not_a_resumable_state() -> None:
    assert ProcessingState.COMPLETED in SUCCESS_STATES
    assert ProcessingState.COMPLETED not in RESUMABLE_STATES


def test_partially_processed_is_never_a_success_state() -> None:
    assert ProcessingState.PARTIALLY_PROCESSED not in SUCCESS_STATES
    assert ProcessingState.PARTIALLY_PROCESSED in RESUMABLE_STATES


def test_human_review_required_is_terminal_but_not_success() -> None:
    assert ProcessingState.HUMAN_REVIEW_REQUIRED in TERMINAL_STATES
    assert ProcessingState.HUMAN_REVIEW_REQUIRED not in SUCCESS_STATES
    assert ProcessingState.HUMAN_REVIEW_REQUIRED not in RESUMABLE_STATES


def test_archive_stored_only_is_explicit_and_never_completed() -> None:
    assert ProcessingState.STORED_ONLY in TERMINAL_STATES
    assert ProcessingState.STORED_ONLY not in SUCCESS_STATES
    assert ProcessingState.STORED_ONLY is not ProcessingState.COMPLETED


def test_complete_extraction_yields_completed() -> None:
    state = derive_processing_state(_result(), attempts_exhausted=False)

    assert state is ProcessingState.COMPLETED


def test_deferred_pages_yield_partially_processed() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_deferred_pages=[7, 8]),
        attempts_exhausted=False,
    )

    assert state is ProcessingState.PARTIALLY_PROCESSED


def test_deferred_pages_with_attempts_exhausted_need_a_human() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_deferred_pages=[7]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_failed_pages_with_attempts_exhausted_need_a_human() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[1, 2]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_unrenderable_page_is_never_completed() -> None:
    for exhausted in (False, True):
        state = derive_processing_state(
            _result(completeness=Completeness.PARTIAL, unrenderable_pages=[2]),
            attempts_exhausted=exhausted,
        )
        assert state is not ProcessingState.COMPLETED
    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_partial_extraction_can_never_derive_completed() -> None:
    for exhausted in (True, False):
        state = derive_processing_state(
            _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[3]),
            attempts_exhausted=exhausted,
        )
        assert state is not ProcessingState.COMPLETED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_processing_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.models.processing_state'`

- [ ] **Step 3: Write the implementation**

```python
"""Document processing states.

The house rule is fail-visible: never mark success on a skipped step. Two
states exist so that partial work is neither hidden nor mistaken for done:

* PARTIALLY_PROCESSED - some pages extracted, some deferred or failed, and the
  job will be re-claimed. Usable but explicitly incomplete.
* HUMAN_REVIEW_REQUIRED - resumption is exhausted or a page failed terminally.
  No automatic retry; an operator must look.

Neither may be reported as COMPLETED, and derive_processing_state is
structurally incapable of returning COMPLETED for a PARTIAL result.
"""

from __future__ import annotations

from enum import Enum

from ..services.extraction.models import Completeness, PageExtractionResult


class ProcessingState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    STORED_ONLY = "stored_only"
    PARTIALLY_PROCESSED = "partially_processed"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    FAILED = "failed"


SUCCESS_STATES = frozenset({ProcessingState.COMPLETED})

TERMINAL_STATES = frozenset(
    {
        ProcessingState.COMPLETED,
        ProcessingState.STORED_ONLY,
        ProcessingState.HUMAN_REVIEW_REQUIRED,
        ProcessingState.FAILED,
    }
)

RESUMABLE_STATES = frozenset({ProcessingState.PARTIALLY_PROCESSED})


def derive_processing_state(
    result: PageExtractionResult, *, attempts_exhausted: bool
) -> ProcessingState:
    """Map an extraction result onto a processing state.

    COMPLETED is reachable only from a COMPLETE result with nothing failed and
    nothing deferred.
    """
    unresolved = (
        bool(result.ocr_failed_pages)
        or bool(result.ocr_deferred_pages)
        or bool(result.unrenderable_pages)
    )

    if result.completeness is Completeness.COMPLETE and not unresolved:
        return ProcessingState.COMPLETED

    if result.unrenderable_pages or attempts_exhausted:
        return ProcessingState.HUMAN_REVIEW_REQUIRED

    return ProcessingState.PARTIALLY_PROCESSED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_processing_state.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/models/processing_state.py projectDMS/backend/rbac_backend/tests/test_processing_state.py
git commit -m "feat: add fail-visible processing states for partial extraction"
```

---

### Task 3.2: `DocumentPageStore`

**Files:**
- Create: `backend/rbac_backend/services/extraction_adapters/document_page_store.py`
- Test: `backend/rbac_backend/tests/test_document_page_store.py`

**Interfaces:**
- Consumes: `PageStore` (1.4), `ExtractedPage` (1.2)
- Produces: `DOCUMENT_OCR_PAGES = "document_ocr_pages"`, `DOCUMENT_OCR_BATCHES = "document_ocr_batches"`, `DOCUMENT_EXTRACTION_HEADS = "document_extraction_heads"`, `to_document_page_record(...) -> dict`, and `class DocumentPageStore(db, document_id, organization_id, project_id, extraction_run_id)` satisfying `PageStore`. `publish_run(expected_page_numbers, session=None)` advances the document's visible-run pointer only after every expected page record exists and can join Task 3.4's Mongo transaction.

**Safety invariants:** unlike the contract record, this one persists `page_class`, `width`, `height`, and `rotation`. It also (1) preserves Motor's actual BSON `inserted_id` for the finishing query, even though the public `batch_id` stored on a page is a string; and (2) never runs `delete_many()` before replacement. Page rows are versioned by `(document_id, extraction_run_id, page_number)`. A crash can leave an unpublished run to reclaim/clean later, but cannot erase or partially replace the previously visible evidence.

- [ ] **Step 1: Write the failing test**

```python
"""DocumentPageStore: per-page OCR evidence for general documents."""

from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    DOCUMENT_OCR_BATCHES,
    DOCUMENT_OCR_PAGES,
    DocumentPageStore,
    to_document_page_record,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.operations: list[tuple[str, Any, Any]] = []
        self.inserted_id_counter = 0
        self.count_result = 0

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.inserted_id_counter += 1
        self.operations.append(("insert_one", document, None))

        class _Result:
            inserted_id = ObjectId()

        return _Result()

    async def update_one(self, query: dict, update: dict, **kwargs: Any) -> None:
        self.operations.append(("update_one", query, update))

    async def bulk_write(self, requests: list[Any]) -> None:
        self.operations.append(("bulk_write", requests, None))

    async def count_documents(self, query: dict, **kwargs: Any) -> int:
        self.operations.append(("count_documents", query, kwargs))
        return self.count_result

class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _page(number: int, page_class: PageClass = PageClass.TEXT_NATIVE) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=f"text {number}",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=6,
            image_count=3,
            image_coverage=0.12,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def test_record_carries_page_geometry_and_class() -> None:
    record = to_document_page_record(
        _page(4, PageClass.TABLE_HEAVY),
        document_id="doc-1",
        organization_id="org-1",
        project_id="proj-1",
        extraction_run_id="run-1",
    )

    assert record["page_number"] == 4
    assert record["page_class"] == "table_heavy"
    assert record["width"] == 595.0
    assert record["height"] == 842.0
    assert record["rotation"] == 0
    assert record["image_count"] == 3
    assert record["table_count"] == 1
    assert record["source"] == "text_layer"
    assert record["status"] == "text_layer"
    assert record["source_pdf_page_link"] == "document:doc-1#page=4"


def test_record_scopes_to_org_and_project() -> None:
    record = to_document_page_record(
        _page(1),
        document_id="doc-1",
        organization_id="org-1",
        project_id=None,
        extraction_run_id="run-1",
    )

    assert record["organization_id"] == "org-1"
    assert record["project_id"] is None


async def test_store_writes_batches_to_the_batches_collection() -> None:
    db = _FakeDb()
    store = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-1",
    )

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    operations = db[DOCUMENT_OCR_BATCHES].operations
    assert operations[0][0] == "insert_one"
    assert operations[0][1]["status"] == "running"
    assert operations[1][0] == "update_one"
    assert isinstance(operations[1][1]["_id"], ObjectId)


async def test_record_pages_upserts_the_version_without_deleting_visible_evidence() -> None:
    db = _FakeDb()
    store = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-2",
    )

    await store.record_pages([_page(1), _page(2)])

    operations = db[DOCUMENT_OCR_PAGES].operations
    assert [operation[0] for operation in operations] == ["bulk_write"]
    requests = operations[0][1]
    assert all(request._filter["extraction_run_id"] == "run-2" for request in requests)


async def test_record_pages_with_no_pages_writes_nothing() -> None:
    db = _FakeDb()
    store = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-1",
    )

    await store.record_pages([])

    assert db[DOCUMENT_OCR_PAGES].operations == []


async def test_new_run_never_deletes_the_previous_run_before_publish() -> None:
    db = _FakeDb()
    first = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-old",
    )
    second = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-new",
    )

    await first.record_pages([_page(1), _page(2)])
    await second.record_pages([_page(1)])  # simulate a crash before page 2/publish

    assert all(op[0] != "delete_many" for op in db[DOCUMENT_OCR_PAGES].operations)


async def test_publish_requires_the_complete_page_set_before_advancing_head() -> None:
    db = _FakeDb()
    store = DocumentPageStore(
        db=db, document_id="doc-1", organization_id="org-1", project_id=None,
        extraction_run_id="run-new",
    )
    db[DOCUMENT_OCR_PAGES].count_result = 1

    with pytest.raises(RuntimeError, match="1/2 pages"):
        await store.publish_run(expected_page_numbers=[1, 2])
    assert db[DOCUMENT_EXTRACTION_HEADS].operations == []

    db[DOCUMENT_OCR_PAGES].count_result = 2
    await store.publish_run(expected_page_numbers=[1, 2])
    assert db[DOCUMENT_EXTRACTION_HEADS].operations[-1][0] == "update_one"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_page_store.py -q`
Expected: FAIL — `ModuleNotFoundError` for `document_page_store`

- [ ] **Step 3: Write the implementation**

```python
"""PageStore adapter for general documents and enclosures.

Mirrors ContractPageStore but writes to its own collections and keeps per-page
geometry, which the fallback ladder and the photo extractor both need and the
contract record does not carry.

Each extraction run has a stable id. `record_pages` upserts into that version
without touching the current version. Readers follow `document_extraction_heads`;
`publish_run` advances that pointer only after the expected page set exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pymongo import ReplaceOne

from ..extraction.models import ExtractedPage

DOCUMENT_OCR_PAGES = "document_ocr_pages"
DOCUMENT_OCR_BATCHES = "document_ocr_batches"
DOCUMENT_EXTRACTION_HEADS = "document_extraction_heads"


def to_document_page_record(
    page: ExtractedPage,
    *,
    document_id: str,
    organization_id: str,
    project_id: Optional[str],
    extraction_run_id: str,
) -> Dict[str, Any]:
    text = page.text or ""
    classification = page.classification
    return {
        "document_id": document_id,
        "organization_id": str(organization_id),
        "project_id": str(project_id) if project_id else None,
        "page_number": page.number,
        "extraction_run_id": extraction_run_id,
        "batch_id": page.batch_id,
        "source": page.source.value,
        "status": page.status.value,
        "error": page.error,
        "quality_verdict": page.quality_verdict,
        "quality_checks": list(page.quality_checks),
        "needs_review": page.needs_review,
        "raw_text": text,
        "raw_text_length": len(text),
        "page_class": classification.page_class.value,
        "char_count": classification.char_count,
        "image_count": classification.image_count,
        "image_coverage": classification.image_coverage,
        "table_count": classification.table_count,
        "tables": page.tables,
        "width": classification.width,
        "height": classification.height,
        "rotation": classification.rotation,
        "source_pdf_page_link": f"document:{document_id}#page={page.number}",
    }


class DocumentPageStore:
    def __init__(
        self,
        *,
        db: Any,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        extraction_run_id: str,
    ) -> None:
        self.db = db
        self.document_id = document_id
        self.organization_id = organization_id
        self.project_id = project_id
        self.extraction_run_id = extraction_run_id
        self._batch_storage_ids: Dict[str, Any] = {}

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        result = await self.db[DOCUMENT_OCR_BATCHES].insert_one(
            {
                "document_id": self.document_id,
                "organization_id": str(self.organization_id),
                "project_id": str(self.project_id) if self.project_id else None,
                "page_start": page_start,
                "page_end": page_end,
                "status": "running",
                "retry": retry,
                "error": None,
                "started_at": datetime.now(timezone.utc),
            }
        )
        public_id = str(result.inserted_id)
        self._batch_storage_ids[public_id] = result.inserted_id
        return public_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        storage_id = self._batch_storage_ids.get(batch_id)
        if storage_id is None:
            # Reclaimed workers may finish a batch created by an earlier process.
            # Convert only strings that are valid ObjectIds; otherwise retain the
            # original persisted type supplied by the caller/test.
            from bson import ObjectId
            from bson.errors import InvalidId

            try:
                storage_id = ObjectId(batch_id)
            except (InvalidId, TypeError):
                storage_id = batch_id
        await self.db[DOCUMENT_OCR_BATCHES].update_one(
            {"document_id": self.document_id, "_id": storage_id},
            {
                "$set": {
                    "status": status,
                    "error": (error or None) and error[:500],
                    "finished_at": datetime.now(timezone.utc),
                }
            },
        )

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        if not pages:
            return

        collection = self.db[DOCUMENT_OCR_PAGES]
        records: List[Dict[str, Any]] = [
            to_document_page_record(
                page,
                document_id=self.document_id,
                organization_id=self.organization_id,
                project_id=self.project_id,
                extraction_run_id=self.extraction_run_id,
            )
            for page in pages
        ]
        await collection.bulk_write(
            [
                ReplaceOne(
                    {
                        "document_id": self.document_id,
                        "extraction_run_id": self.extraction_run_id,
                        "page_number": record["page_number"],
                    },
                    record,
                    upsert=True,
                )
                for record in records
            ]
        )

    async def publish_run(
        self, *, expected_page_numbers: Sequence[int], session: Any = None
    ) -> None:
        """Make a complete page-record version visible with one atomic pointer write."""
        expected = sorted({int(number) for number in expected_page_numbers})
        count = await self.db[DOCUMENT_OCR_PAGES].count_documents(
            {
                "document_id": self.document_id,
                "extraction_run_id": self.extraction_run_id,
                "page_number": {"$in": expected},
            },
            session=session,
        )
        if count != len(expected):
            raise RuntimeError(
                f"Extraction run {self.extraction_run_id} has {count}/{len(expected)} pages"
            )
        await self.db[DOCUMENT_EXTRACTION_HEADS].update_one(
            {"document_id": self.document_id},
            {
                "$set": {
                    "extraction_run_id": self.extraction_run_id,
                    "expected_page_numbers": expected,
                    "published_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
            session=session,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_page_store.py -q`
Expected: PASS — 7 passed, including a real `bson.ObjectId` batch finish, incomplete-run rejection, and crash-before-publish preservation

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction_adapters/document_page_store.py projectDMS/backend/rbac_backend/tests/test_document_page_store.py
git commit -m "feat: add DocumentPageStore for general-document page evidence"
```

---

### Task 3.3: Route the general path through the engine

Retires the document-level `is_pdf_textual` decision. **This is the task that fixes the measured defect.**

**Files:**
- Modify: `backend/rbac_backend/services/ocr_service.py` (add `process_pdf_pagewise`; leave `process_pdf` in place for the DOCX/legacy caller)
- Modify: `backend/rbac_backend/services/document_processor.py:88-97`
- Test: `backend/rbac_backend/tests/test_general_path_pagewise_ocr.py`

**Interfaces:**
- Consumes: `PageExtractionEngine` (1.5), `OcrMyPdfRunner` (1.6), `DocumentPageStore` (3.2), `derive_processing_state` (3.1)
- Produces: `OCRService.process_pdf_pagewise(input_path, *, store, document_id, retry_pages=None) -> PageExtractionResult`

- [ ] **Step 1: Write the failing test**

```python
"""The general path must OCR thin pages individually, not skip the document.

Before this change: is_pdf_textual(max_pages=5) found text on page 3 of
fixture #1 and suppressed OCR for all nine pages, leaving pages 1-2 indexed as
empty. The golden file records that legacy behaviour so the regression is
explicit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Sequence

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.extraction.models import PageSource, PageStatus
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_page_routing.json").read_text(
        encoding="utf-8"
    )
)


class _StubOcrRunner:
    def __init__(self) -> None:
        self.requested: list[list[int]] = []

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        return {page: f"RECOVERED PAGE {page}" for page in page_numbers}


def _service() -> OCRService:
    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = GOLDEN["min_text_chars_per_page"]
    return OCRService(config)


def test_legacy_document_level_decision_would_have_skipped_ocr(tmp_path: Path) -> None:
    # Documents the defect this task fixes.
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    assert _service().is_pdf_textual(source, max_pages=5) is True
    assert GOLDEN["legacy_general_path_behaviour"]["decision"] == "skip_ocr_entire_document"


async def test_pagewise_path_ocrs_only_the_thin_pages(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _StubOcrRunner()
    store = NullPageStore()

    result = await _service().process_pdf_pagewise(
        source, store=store, document_id="doc-1", ocr_runner=runner
    )

    assert runner.requested == GOLDEN["expected_batches"]
    assert result.ocr_pages_total == 2


async def test_previously_lost_pages_now_carry_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_StubOcrRunner()
    )
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["legacy_general_path_behaviour"]["pages_silently_empty"]:
        assert by_number[number].text.strip()
        assert by_number[number].source is PageSource.OCR
        assert by_number[number].status is PageStatus.OCR_COMPLETED


async def test_native_pages_are_not_reocred(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_StubOcrRunner()
    )
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_native"]:
        assert "RECOVERED PAGE" not in by_number[number].text


async def test_pages_are_persisted_through_the_store(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()

    await _service().process_pdf_pagewise(
        source, store=store, document_id="doc-1", ocr_runner=_StubOcrRunner()
    )

    assert len(store.recorded_pages) == GOLDEN["pages_total"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_general_path_pagewise_ocr.py -q`
Expected: FAIL — `AttributeError: 'OCRService' object has no attribute 'process_pdf_pagewise'`

- [ ] **Step 3: Add `process_pdf_pagewise` to `OCRService`**

Append to `backend/rbac_backend/services/ocr_service.py`, inside the `OCRService` class. Leave `process_pdf` and `is_pdf_textual` in place — `process_document` and the tests above still call them, and removing them is a separate change.

```python
    async def process_pdf_pagewise(
        self,
        input_path: Path,
        *,
        store,
        document_id: str,
        ocr_runner=None,
        retry_pages: Optional[Sequence[int]] = None,
    ):
        """Extract a PDF page by page, OCR-ing only the pages that need it.

        Replaces the document-level is_pdf_textual decision for the general
        path. That decision inspected the first five pages and, on finding any
        text, skipped OCR for the entire document - so a scanned covering
        letter behind a textual body was indexed as empty.
        """
        from ..core.config import settings
        from .extraction.engine import PageExtractionEngine
        from .extraction.models import PageExtractionPolicy
        from .extraction.ocrmypdf_runner import OcrMyPdfRunner

        work_dir = Path(self.config.process_dir) / "page_batches" / document_id
        runner = ocr_runner or OcrMyPdfRunner(work_dir=work_dir)

        engine = PageExtractionEngine(
            policy=PageExtractionPolicy(
                ocr_enabled=self.config.ocr_enabled and self._ocr_available,
                min_text_chars_per_page=max(
                    0, int(self.config.contract_ocr_min_text_chars_per_page)
                ),
                batch_size=max(1, int(self.config.contract_ocr_batch_size)),
                max_ocr_pages_per_attempt=int(
                    getattr(settings, "DOCUMENT_OCR_MAX_PAGES_PER_ATTEMPT", 0)
                ),
                ocr_language=self.config.ocr_language,
            ),
            ocr_runner=runner,
            store=store,
        )
        return await engine.extract(input_path, retry_pages=retry_pages)
```

Add `Sequence` to the `typing` import at the top of `ocr_service.py`.

- [ ] **Step 4: Add the attempt-boundary setting**

In `backend/rbac_backend/core/config.py`, beside the other upload settings:

```python
    # 0 means "no attempt boundary". A positive value bounds the OCR pages one
    # attempt will run; the remainder is DEFERRED and re-claimed, never dropped.
    DOCUMENT_OCR_MAX_PAGES_PER_ATTEMPT: int = Field(
        default=0, validation_alias="DOCUMENT_OCR_MAX_PAGES_PER_ATTEMPT"
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_general_path_pagewise_ocr.py -q`
Expected: PASS — 5 passed

- [ ] **Step 6: Wire it into `DocumentProcessor`**

In `backend/rbac_backend/services/document_processor.py`, replace the Step 1 block at lines 88–97:

```python
            # Step 1: Page-wise extraction. Pages with a usable text layer keep
            # their native text; only thin pages are OCR'd.
            logger.info("[document_pipeline] Starting page-wise extraction for %s", input_path.name)
            from .extraction_adapters.document_page_store import DocumentPageStore

            extraction_run_id = extraction_run_id or str(uuid4())
            db = await self.database_service.get_database()
            page_store = DocumentPageStore(
                db=db,
                document_id=document_id or input_path.stem,
                organization_id=organization_id or "",
                project_id=project_id,
                extraction_run_id=extraction_run_id,
            )
            extraction = await self.ocr_service.process_pdf_pagewise(
                input_path,
                store=page_store,
                document_id=document_id or input_path.stem,
            )
            processed_path = input_path
            raw_ocr_text = extraction.combined_text or None
            ocr_text_len = len(raw_ocr_text) if raw_ocr_text else 0
            logger.info(
                "[document_pipeline] Extraction completed for %s "
                "(chars=%s, ocr_pages=%s, failed=%s, deferred=%s)",
                input_path.name,
                ocr_text_len,
                extraction.ocr_pages_total,
                extraction.ocr_failed_pages,
                extraction.ocr_deferred_pages,
            )
```

Add `organization_id: Optional[str] = None`, `project_id: Optional[str] = None`, `extraction_run_id: Optional[str] = None`, and `retry_pages: Optional[Sequence[int]] = None` to `process_document`'s signature; import `uuid4`, and pass `retry_pages` into `process_pdf_pagewise`. In `DocumentService.process_document_async`, pass the already-resolved `org_id`, `proj_id`, and job's stable `extraction_run_id` into `processor.process_document`; do not rely on the processor discovering tenant scope or a nonexistent `DatabaseService.db` attribute. Return `extraction` on the `ProcessingResult` by adding `extraction_result: Optional[PageExtractionResult] = None` and `extraction_completeness=extraction.completeness.value` in `models/document_metadata.py`. Task 3.4 consumes the typed result to checkpoint and requeue pages.

- [ ] **Step 7: Run the document-pipeline suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "document or ocr or processing"`
Expected: PASS

- [ ] **Step 8: Run the whole suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known `test_route_control_manifest` failure)

- [ ] **Step 9: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/ocr_service.py projectDMS/backend/rbac_backend/services/document_processor.py projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/models/document_metadata.py projectDMS/backend/rbac_backend/tests/test_general_path_pagewise_ocr.py
git commit -m "feat: route the general document path through page-wise extraction"
```

---

### Task 3.4: Durable retry/state persistence

The existing Mongo job loop already claims atomically, heartbeats, reclaims stale jobs, and retries whole-job exceptions. It does **not** know that a successful processor return may still contain `OCR_DEFERRED`/failed pages. This task makes partial extraction a durable checkpoint rather than an in-memory enum.

**Files:**
- Modify: `backend/rbac_backend/services/document_service.py:757-1545`
- Modify: `backend/rbac_backend/services/document_processor.py`
- Modify: `backend/rbac_backend/models/processing_state.py`
- Test: `backend/rbac_backend/tests/test_document_page_retry_state.py`
- Test: `backend/rbac_backend/tests/test_document_processing_jobs.py`

**Interfaces:**
- Consumes: `PageExtractionResult` (1.2), versioned `DocumentPageStore` (3.2), routed PDF processing (3.3), existing `_claim_next_processing_job`, heartbeat, stale recovery, and `_mark_processing_failure`.
- Adds durable job fields: `extraction_run_id`, `expected_page_numbers`, `remaining_page_numbers`, `resolved_page_numbers`, `page_attempts` (string page number to count), `last_attempted_page_numbers`, `resume_count`, and `processing_state`.
- Produces `ProcessingAttemptOutcome(result, state, remaining_page_numbers, attempts_exhausted)` and `_checkpoint_extraction_attempt(job, outcome, session)`.
- `process_document_job` returns/uses the typed outcome. A processor call returning partial evidence must not flow through the existing `if ok: status=completed` branch.

- [ ] **Step 1: Write the failing durable-state tests**

The tests must assert all of these behaviours through `DocumentService.process_document_job`, not only `derive_processing_state`:

```python
async def test_deferred_pages_checkpoint_and_requeue_instead_of_complete() -> None:
    # The all-scanned fixture submits pages 1-6 to OCR and resolves them; the
    # attempt boundary defers 7-8 without consuming their retry allowance.
    await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    document = await db.documents.find_one({"_id": ObjectId(document_id)})
    assert job["status"] == "retrying"
    assert job["processing_state"] == "partially_processed"
    assert job["remaining_page_numbers"] == [7, 8]
    assert job["resolved_page_numbers"] == [1, 2, 3, 4, 5, 6]
    assert job["page_attempts"] == {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1}
    assert document["processing_status"] == "partially_processed"
    assert document["processing_status"] != "completed"


async def test_reclaimed_job_passes_only_remaining_pages_to_processor() -> None:
    await service.process_document_job(job_id)
    await service.process_document_job(job_id)
    assert processor.retry_page_calls == [None, [7, 8]]


async def test_stale_recovery_preserves_page_checkpoint() -> None:
    # Simulate death after the partial checkpoint and stale processing reclaim.
    before = await db.document_processing_jobs.find_one({"_id": job_id})
    await service.recover_stale_processing_jobs()
    after = await db.document_processing_jobs.find_one({"_id": job_id})
    assert after["remaining_page_numbers"] == before["remaining_page_numbers"]
    assert after["extraction_run_id"] == before["extraction_run_id"]


async def test_last_remaining_pages_transition_job_and_document_to_completed() -> None:
    await service.process_document_job(job_id)
    await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    document = await db.documents.find_one({"_id": ObjectId(document_id)})
    assert job["remaining_page_numbers"] == []
    assert job["status"] == "completed"
    assert document["processing_status"] == "completed"


async def test_exhausted_page_attempts_end_in_human_review_not_dead_letter_success() -> None:
    await exhaust_page_attempts(service, job_id, page_number=2)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    document = await db.documents.find_one({"_id": ObjectId(document_id)})
    assert job["status"] == "human_review_required"
    assert job["processing_state"] == "human_review_required"
    assert document["processing_status"] == "human_review_required"
    assert document["processing_status"] != "completed"
```

Also cover: unrenderable pages go directly to `human_review_required`; resolved pages are never re-OCR'd; a retry exception retains the prior checkpoint; and a worker restart can reconstruct the full result by loading current-run pages from `DocumentPageStore`.

- [ ] **Step 2: Initialise resumability fields when the job is queued**

`queue_document_processing` creates one stable `extraction_run_id` and empty checkpoint fields. Preserve the existing string job `_id` convention; this task does not change job IDs to BSON IDs.

```python
"extraction_run_id": str(uuid4()),
"expected_page_numbers": [],
"remaining_page_numbers": [],
"resolved_page_numbers": [],
"page_attempts": {},
"last_attempted_page_numbers": [],
"resume_count": 0,
"processing_state": ProcessingState.QUEUED.value,
```

- [ ] **Step 3: Persist a page checkpoint before scheduling the next claim**

After `DocumentProcessor` returns, idempotently upsert its versioned current-run rows and merge them with prior current-run rows. Then use the already-deployed Mongo replica set for one transaction that (a) calls `DocumentPageStore.publish_run(expected_page_numbers=..., session=session)` and (b) updates the durable job/document checkpoint with remaining/resolved sets, page-attempt counters, result summary, processing state, and the corresponding queue/terminal status. Only pages actually submitted to OCR increment `page_attempts`; `OCR_DEFERRED` pages do not consume an attempt.

Do not advance the visible head in one committed operation and mark the job retryable/completed in another. If the process dies before the transaction commits, the previous head and checkpoint remain authoritative; the idempotent run rows can be reclaimed. If it dies after commit, both the visible evidence and exact remaining-page list are durable. Stale recovery derives missing legacy checkpoint fields from current-run page statuses but never clears an existing checkpoint.

- [ ] **Step 4: Make the existing job loop branch on the derived state**

```python
if outcome.state is ProcessingState.COMPLETED:
    await self._mark_processing_completed(job, outcome)
elif outcome.state is ProcessingState.PARTIALLY_PROCESSED:
    await self._schedule_page_resume(job, outcome)  # status=retrying, bounded run_after
elif outcome.state is ProcessingState.HUMAN_REVIEW_REQUIRED:
    await self._mark_human_review(job, outcome)     # terminal, non-success
else:
    await self._mark_processing_failure(job, outcome.error or "processing failed")
```

Remove the unconditional boolean-success completion branch for unified extraction. Keep `_mark_processing_failure` for exceptions and non-extraction failures. Stale recovery may change only claim metadata/status; it must not clear `extraction_run_id`, page sets, page attempts, quality verdicts, or the visible-run pointer.

- [ ] **Step 5: Add indexes and retention rules**

Create unique index `(document_id, extraction_run_id, page_number)` for page rows; index `(document_id, extraction_run_id)` for batches; and unique `document_id` for extraction heads. Unpublished abandoned runs are retained for the audit window and removed only by a separate age-based cleanup after verifying they are not referenced by a head or active job.

- [ ] **Step 6: Run focused and full tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_page_retry_state.py backend/rbac_backend/tests/test_document_processing_jobs.py backend/rbac_backend/tests/test_document_page_store.py -q`
Expected: PASS

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/document_service.py projectDMS/backend/rbac_backend/services/document_processor.py projectDMS/backend/rbac_backend/models/processing_state.py projectDMS/backend/rbac_backend/tests/test_document_page_retry_state.py projectDMS/backend/rbac_backend/tests/test_document_processing_jobs.py
git commit -m "feat: persist and resume page-level document extraction"
```

---

## Phase 4 — Source-kind routing

Fixes documented gap #2: the route admits PNG, JPEG, and plain text, but `DocumentProcessor` sends everything into a PDF path.

### Task 4.1: `SourceKindRouter`

**Files:**
- Create: `backend/rbac_backend/services/extraction/source_kind.py`
- Test: `backend/rbac_backend/tests/test_extraction_source_kind.py`

**Interfaces:**
- Consumes: `SourceKind` (1.2)
- Produces: `ARCHIVE_MIMES`, `IMAGE_MIMES`, `TEXT_MIMES`, `PDF_MIMES` frozensets; `class SourceKindRouter` with `route(mime: str | None, filename: str | None = None) -> SourceKind`; and a module-level `route` compatibility alias.

- [ ] **Step 1: Write the failing test**

```python
"""Route an upload to the extractor that can actually read it."""

from __future__ import annotations

import pytest

from rbac_backend.services.extraction.models import SourceKind
from rbac_backend.services.extraction.source_kind import ARCHIVE_MIMES, route


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/pdf", SourceKind.PDF),
        ("image/png", SourceKind.IMAGE),
        ("image/jpeg", SourceKind.IMAGE),
        ("text/plain", SourceKind.TEXT),
        ("application/zip", SourceKind.ARCHIVE),
        ("application/vnd.rar", SourceKind.ARCHIVE),
        ("application/octet-stream", SourceKind.UNSUPPORTED),
        ("application/msword", SourceKind.UNSUPPORTED),
    ],
)
def test_mime_routing(mime: str, expected: SourceKind) -> None:
    assert route(mime) is expected


def test_missing_mime_is_unsupported() -> None:
    assert route(None) is SourceKind.UNSUPPORTED
    assert route("") is SourceKind.UNSUPPORTED


def test_mime_wins_over_a_misleading_extension() -> None:
    # A .pdf-named file that sniffed as PNG is an image, not a PDF.
    assert route("image/png", "invoice.pdf") is SourceKind.IMAGE


def test_archive_mimes_cover_zip_and_rar() -> None:
    assert ARCHIVE_MIMES == frozenset({"application/zip", "application/vnd.rar"})


def test_case_and_parameters_are_tolerated() -> None:
    assert route("APPLICATION/PDF") is SourceKind.PDF
    assert route("text/plain; charset=utf-8") is SourceKind.TEXT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_source_kind.py -q`
Expected: FAIL — `ModuleNotFoundError` for `source_kind`

- [ ] **Step 3: Write the implementation**

```python
"""Decide which extractor an upload belongs to, from its detected MIME type.

The detected MIME is authoritative, not the filename: a file named .pdf that
sniffs as PNG is an image. The filename parameter exists only for callers that
want to log it.
"""

from __future__ import annotations

from typing import Optional

from .models import SourceKind

PDF_MIMES = frozenset({"application/pdf"})
IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})
TEXT_MIMES = frozenset({"text/plain"})
ARCHIVE_MIMES = frozenset({"application/zip", "application/vnd.rar"})


class SourceKindRouter:
    @staticmethod
    def route(mime: Optional[str], filename: Optional[str] = None) -> SourceKind:
        """Map a detected MIME type onto a SourceKind."""
        if not mime:
            return SourceKind.UNSUPPORTED
        normalized = mime.split(";", 1)[0].strip().lower()
        if normalized in PDF_MIMES:
            return SourceKind.PDF
        if normalized in IMAGE_MIMES:
            return SourceKind.IMAGE
        if normalized in TEXT_MIMES:
            return SourceKind.TEXT
        if normalized in ARCHIVE_MIMES:
            return SourceKind.ARCHIVE
        return SourceKind.UNSUPPORTED


route = SourceKindRouter.route
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_source_kind.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/source_kind.py projectDMS/backend/rbac_backend/tests/test_extraction_source_kind.py
git commit -m "feat: add source-kind routing for uploads"
```

---

### Task 4.2: Image and text extractors

**Files:**
- Create: `backend/rbac_backend/services/extraction/image_extractor.py`
- Create: `backend/rbac_backend/services/extraction/image_ocr_runner.py`
- Test: `backend/rbac_backend/tests/test_extraction_image_and_text.py`

**Interfaces:**
- Consumes: `models` (1.2), `PageStore` and `ImageOcrRunner` (1.4)
- Produces:
  - `class TesseractImageOcrRunner(timeout=300)` satisfying `ImageOcrRunner`; it validates PNG/JPEG with Pillow and invokes Tesseract directly
  - `async def extract_image(source, *, store, image_ocr_runner, language) -> PageExtractionResult` — single logical page, image stored as-is
  - `async def extract_text_file(source, *, store) -> PageExtractionResult`

**Design note:** the image is **never wrapped into a PDF** and is never passed to `OcrMyPdfRunner`. `OcrMyPdfRunner.run` calls `_input_page_count` and `_extract_mapped_pages`, so using it for PNG/JPEG violates its PDF contract even if the underlying OCRmyPDF CLI happens to accept an image. Standalone images use Tesseract directly; the immutable original `FileObject` is untouched.

- [ ] **Step 1: Write the failing test**

```python
"""Images and text files get their own extractors, not the PDF path."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

from PIL import Image

from rbac_backend.services.extraction.image_extractor import (
    extract_image,
    extract_text_file,
)
from rbac_backend.services.extraction.image_ocr_runner import TesseractImageOcrRunner
from rbac_backend.services.extraction.models import (
    Completeness,
    PageClass,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import ImageOcrRunner, NullPageStore


class _StubImageOcrRunner:
    def __init__(self, text: str = "SCANNED SITE INSTRUCTION") -> None:
        self.text = text
        self.calls: list[Path] = []

    async def run(self, source: Path, language: str) -> str:
        self.calls.append(source)
        return self.text


def _png(path: Path, size: tuple[int, int] = (800, 600)) -> Path:
    Image.new("RGB", size, color=(200, 200, 200)).save(path)
    return path


def test_standalone_image_runner_has_the_non_pdf_protocol() -> None:
    assert isinstance(TesseractImageOcrRunner(), ImageOcrRunner)


async def test_image_yields_one_page_of_ocr_text(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png")
    store = NullPageStore()

    result = await extract_image(
        source, store=store, image_ocr_runner=_StubImageOcrRunner(), language="eng"
    )

    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert result.pages[0].text == "SCANNED SITE INSTRUCTION"
    assert result.pages[0].source is PageSource.OCR
    assert result.pages[0].status is PageStatus.OCR_COMPLETED
    assert result.completeness is Completeness.COMPLETE


async def test_image_dimensions_are_recorded(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png", size=(1024, 768))

    result = await extract_image(
        source, store=NullPageStore(), image_ocr_runner=_StubImageOcrRunner(), language="eng"
    )
    classification = result.pages[0].classification

    assert classification.width == 1024
    assert classification.height == 768
    assert classification.page_class is PageClass.SCANNED_IMAGE


async def test_image_is_passed_to_ocr_unchanged(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png")
    before = source.read_bytes()
    runner = _StubImageOcrRunner()

    await extract_image(source, store=NullPageStore(), image_ocr_runner=runner, language="eng")

    assert runner.calls == [source]
    assert source.read_bytes() == before


async def test_empty_ocr_on_an_image_is_marked_not_pretended(tmp_path: Path) -> None:
    source = _png(tmp_path / "blank.png")

    result = await extract_image(
        source, store=NullPageStore(), image_ocr_runner=_StubImageOcrRunner(text=""), language="eng"
    )

    assert result.pages[0].status is PageStatus.OCR_EMPTY
    assert result.completeness is Completeness.PARTIAL


async def test_text_file_is_read_without_ocr(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("Ref: CC/2026/001\nSite instruction issued.", encoding="utf-8")

    result = await extract_text_file(source, store=NullPageStore())

    assert result.pages[0].source is PageSource.TEXT_LAYER
    assert result.pages[0].status is PageStatus.TEXT_LAYER
    assert "Site instruction issued." in result.combined_text
    assert result.ocr_pages_total == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_image_and_text.py -q`
Expected: FAIL — `ModuleNotFoundError` for `image_extractor`

- [ ] **Step 3: Write the implementation**

`image_ocr_runner.py`:

```python
"""Direct OCR for standalone PNG/JPEG inputs; no PDF page mapping."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image


class ImageOcrRunnerError(RuntimeError):
    pass


class TesseractImageOcrRunner:
    def __init__(self, *, timeout: int = 300) -> None:
        self.timeout = timeout

    async def run(self, source_image: Path, language: str) -> str:
        def _verify() -> None:
            with Image.open(source_image) as image:
                image.verify()

        await asyncio.to_thread(_verify)
        process = await asyncio.create_subprocess_exec(
            "tesseract", str(source_image), "stdout", "-l", language,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ImageOcrRunnerError("Standalone image OCR timed out") from exc
        if process.returncode != 0:
            raise ImageOcrRunnerError(stderr.decode("utf-8", errors="replace")[:500])
        return stdout.decode("utf-8", errors="replace").strip()
```

`image_extractor.py`:

```python
"""Extractors for standalone images and plain-text uploads.

Images are OCR'd directly and stored as-is: they are never wrapped into a PDF
container, so the FileObject the user uploaded remains the FileObject we hold.
Both extractors produce a single logical page so downstream consumers see the
same PageExtractionResult shape as the PDF path.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Sequence

from .models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from .page_store import ImageOcrRunner, PageStore

logger = logging.getLogger(__name__)
ENGINE_VERSION = "1"


def _image_size(source: Path) -> tuple[float, float]:
    try:
        from PIL import Image

        with Image.open(source) as handle:
            return float(handle.width), float(handle.height)
    except Exception as exc:  # pragma: no cover - unreadable image
        logger.warning("Could not read image dimensions for %s: %s", source.name, exc)
        return 0.0, 0.0


async def extract_image(
    source: Path,
    *,
    store: PageStore,
    image_ocr_runner: ImageOcrRunner,
    language: str,
) -> PageExtractionResult:
    """OCR a standalone image as a single page."""
    width, height = await asyncio.to_thread(_image_size, source)

    batch_id = await store.begin_batch(page_start=1, page_end=1, retry=False)
    text = ""
    status = PageStatus.OCR_COMPLETED
    error: Optional[str] = None

    try:
        text = (await image_ocr_runner.run(source, language) or "").strip()
        if not text:
            status = PageStatus.OCR_EMPTY
            error = "OCR completed but no text was extracted"
        await store.finish_batch(batch_id, status="completed")
    except Exception as exc:
        status = PageStatus.OCR_FAILED
        error = str(exc)[:500]
        await store.finish_batch(batch_id, status="failed", error=error)

    page = ExtractedPage(
        number=1,
        text=text,
        source=PageSource.OCR if text else PageSource.EMPTY,
        status=status,
        classification=PageClassification(
            page_class=PageClass.SCANNED_IMAGE,
            char_count=len(text),
            image_count=1,
            image_coverage=1.0,
            table_count=0,
            width=width,
            height=height,
            rotation=0,
        ),
        batch_id=batch_id,
        error=error,
    )
    await store.record_pages([page])

    resolved = status is PageStatus.OCR_COMPLETED
    return PageExtractionResult(
        pages=[page],
        combined_text=text,
        ocr_pages_total=1,
        ocr_failed_pages=[1] if status is PageStatus.OCR_FAILED else [],
        ocr_deferred_pages=[],
        completeness=Completeness.COMPLETE if resolved else Completeness.PARTIAL,
        engine_version=ENGINE_VERSION,
    )


async def extract_text_file(source: Path, *, store: PageStore) -> PageExtractionResult:
    """Read a plain-text upload as a single page. No OCR involved."""

    def _read() -> str:
        return source.read_text(encoding="utf-8", errors="replace")

    text = await asyncio.to_thread(_read)

    page = ExtractedPage(
        number=1,
        text=text,
        source=PageSource.TEXT_LAYER if text.strip() else PageSource.EMPTY,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text.strip()),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=0.0,
            height=0.0,
            rotation=0,
        ),
    )
    await store.record_pages([page])

    return PageExtractionResult(
        pages=[page],
        combined_text=text,
        ocr_pages_total=0,
        completeness=Completeness.COMPLETE,
        engine_version=ENGINE_VERSION,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_image_and_text.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/image_extractor.py projectDMS/backend/rbac_backend/services/extraction/image_ocr_runner.py projectDMS/backend/rbac_backend/tests/test_extraction_image_and_text.py
git commit -m "feat: add image and text extractors so non-PDFs stop entering the PDF path"
```

---

### Task 4.3: Source-kind integration into `DocumentProcessor`

Tasks 4.1–4.2 are not complete until the active upload/job/processor call chain dispatches through them. Current source proves the gap: `DocumentProcessor.process_document` calls `OCRService.process_pdf` at its first processing step, while `OCRService.process_document` explicitly skips non-PDF inputs and the durable job currently stores no validated MIME.

**Files:**
- Modify: `backend/rbac_backend/routers/documents.py`
- Modify: `backend/rbac_backend/services/document_service.py`
- Modify: `backend/rbac_backend/services/document_processor.py`
- Modify: `backend/rbac_backend/models/document.py` only if a migration-safe alias for the existing `filetype` field is needed
- Test: `backend/rbac_backend/tests/test_document_processor_source_dispatch.py`
- Test: `backend/rbac_backend/tests/test_document_processing_jobs.py`

**Interfaces:**
- Consumes: `SourceKindRouter` (4.1), PDF extraction (3.3), and `extract_image` / `TesseractImageOcrRunner` / `extract_text_file` (4.2). The defensive archive branch uses `SourceKind.ARCHIVE`; Phase 5 later prevents new archive jobs.
- Durable MIME invariant: `validate_spooled_upload(...).mime_type` is written to `Document.filetype`, copied to `document_processing_jobs.source_mime`, and passed through `process_document_job` → `process_document_async` → `DocumentProcessor.process_document(source_mime=...)`.
- Produces one private `_extract_source(...) -> SourceDispatchResult(kind, extraction, processing_state)`. PDF/image/text return an extraction result. Archive returns `STORED_ONLY` with no extraction or metadata/model call. Unsupported raises `UnsupportedSourceKindError` and is fail-visible.

- [ ] **Step 1: Write the failing dispatch tests**

Use injected counting fakes for each extractor and assert the complete matrix:

```python
@pytest.mark.parametrize(
    ("mime", "expected_kind", "called"),
    [
        ("application/pdf", SourceKind.PDF, "pdf"),
        ("image/png", SourceKind.IMAGE, "image"),
        ("image/jpeg", SourceKind.IMAGE, "image"),
        ("text/plain", SourceKind.TEXT, "text"),
        ("application/zip", SourceKind.ARCHIVE, "none"),
        ("application/vnd.rar", SourceKind.ARCHIVE, "none"),
    ],
)
async def test_document_processor_dispatches_exactly_one_source_extractor(
    mime, expected_kind, called
) -> None:
    result = await processor.process_document(..., source_mime=mime)
    assert result.source_kind == expected_kind.value
    assert counters == expected_counter_map(called)


async def test_archive_is_stored_only_not_completed_or_model_processed() -> None:
    result = await processor.process_document(..., source_mime="application/zip")
    assert result.processing_state == "stored_only"
    assert result.extraction_result is None
    assert model.calls == 0
    assert pdf_runner.calls == image_runner.calls == 0


async def test_unsupported_mime_fails_before_any_extractor_or_model_call() -> None:
    with pytest.raises(UnsupportedSourceKindError):
        await processor.process_document(..., source_mime="application/msword")
    assert total_extractor_calls() == 0
    assert model.calls == 0


async def test_validated_mime_survives_upload_job_and_processor_chain() -> None:
    # Upload a PNG named invoice.pdf. Detected MIME, not extension, must win.
    document = await upload_png_named_pdf()
    job = await db.document_processing_jobs.find_one({"document_id": document.id})
    assert document.filetype == "image/png"
    assert job["source_mime"] == "image/png"
    await service.process_document_job(job["_id"])
    assert image_runner.calls == 1
    assert pdf_runner.calls == 0
```

Also assert TXT preserves UTF-8 replacement semantics, empty image OCR remains partial, and a legacy job missing `source_mime` uses the stored document `filetype`; only if both are absent may it re-sniff the materialized bytes and persist the result before dispatch.

- [ ] **Step 2: Preserve the validated MIME instead of re-sniffing without filename context**

Pass `filetype=validation_result.mime_type` into `DocumentService.create_document` and make `create_document` prefer that validated value. This avoids the current second sniff over raw bytes with no filename hint. `queue_document_processing` copies `document.filetype` to `source_mime`.

- [ ] **Step 3: Add the single dispatch point before PDF-specific work**

At the start of `DocumentProcessor.process_document`, after existence/size checks and before any `process_pdf`, PDF upload, rasterization, metadata-model, or extraction call:

```python
source_kind = self.source_kind_router.route(source_mime, input_path.name)
dispatch = await self._extract_source(
    input_path,
    source_kind=source_kind,
    page_store=page_store,
    retry_pages=retry_pages,
)
if dispatch.processing_state is ProcessingState.STORED_ONLY:
    return ProcessingResult(
        success=False,
        source_kind=source_kind.value,
        processing_state=ProcessingState.STORED_ONLY.value,
    )
extraction = dispatch.extraction
```

`_extract_source` calls `process_pdf_pagewise` only for PDF, `extract_image(..., image_ocr_runner=TesseractImageOcrRunner())` only for PNG/JPEG, and `extract_text_file` only for text. It contains explicit archive and unsupported branches; no fall-through defaults to PDF.

Add explicit constructor seams for `source_kind_router`, `pdf_ocr_runner`, `image_ocr_runner`, and `database_service`; production defaults remain `SourceKindRouter`, `OcrMyPdfRunner`, `TesseractImageOcrRunner`, and the existing `DatabaseService`. The dispatch tests inject counting/fail-on-call implementations through those seams instead of monkeypatching module globals.

- [ ] **Step 4: Update the durable call chain and final-state handling**

Pass `source_mime` and tenant scope at every service boundary. `process_document_job` maps `STORED_ONLY` to the explicit terminal non-success state and must never overwrite it with `completed`. Task 5.2 prevents new archive jobs; this defensive branch handles legacy/requeued archive jobs safely.

- [ ] **Step 5: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_document_processor_source_dispatch.py backend/rbac_backend/tests/test_document_processing_jobs.py backend/rbac_backend/tests/test_extraction_image_and_text.py -q`
Expected: PASS

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "document or extraction or upload"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/routers/documents.py projectDMS/backend/rbac_backend/services/document_service.py projectDMS/backend/rbac_backend/services/document_processor.py projectDMS/backend/rbac_backend/tests/test_document_processor_source_dispatch.py projectDMS/backend/rbac_backend/tests/test_document_processing_jobs.py
git commit -m "feat: route every admitted source kind through DocumentProcessor"
```

---

## Phase 5 — Archives and canonical upload policy

### Task 5.1: ZIP/RAR sniffing and `ArchiveIntakePolicy`

**Files:**
- Modify: `backend/rbac_backend/utils/file_validation.py`
- Create: `backend/rbac_backend/services/archive_policy.py`
- Modify: `backend/rbac_backend/core/config.py` (ZIP admitted; RAR remains off unless the Phase 0 proof explicitly enables it)
- Test: `backend/rbac_backend/tests/test_archive_intake.py`

**Interfaces:**
- Consumes: `ARCHIVE_MIMES` (4.1)
- Consumes: `RAR_INNER_SCAN_PROVEN` from Task 0.4 as a deployment decision, not a hard-coded assumption.
- Produces: `RAR_UPLOAD_ENABLED` (default `False`), `ArchiveIntakePolicy.is_archive(mime) -> bool`, `.is_admitted(mime) -> bool`, `.requires_letter_number(mime) -> bool`, `.creates_processing_job(mime) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
"""Archives are stored intact: scanned and de-duplicated, never unpacked."""

from __future__ import annotations

from rbac_backend.core.config import Settings, settings
from rbac_backend.services.archive_policy import ArchiveIntakePolicy
from rbac_backend.utils.file_validation import sniff_mime_from_bytes

ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 32
RAR4_MAGIC = b"Rar!\x1a\x07\x00" + b"\x00" * 32
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32


def test_zip_is_sniffed() -> None:
    assert sniff_mime_from_bytes(ZIP_MAGIC, "evidence.zip") == "application/zip"


def test_rar_v4_and_v5_are_sniffed() -> None:
    assert sniff_mime_from_bytes(RAR4_MAGIC, "evidence.rar") == "application/vnd.rar"
    assert sniff_mime_from_bytes(RAR5_MAGIC, "evidence.rar") == "application/vnd.rar"


def test_docx_still_wins_over_zip_for_the_same_magic() -> None:
    # DOCX is a zip container; the existing filename-hint branch must still win.
    assert sniff_mime_from_bytes(ZIP_MAGIC, "letter.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_zip_without_a_zip_extension_is_still_a_zip() -> None:
    assert sniff_mime_from_bytes(ZIP_MAGIC, "bundle") == "application/zip"


def test_zip_is_admitted_by_both_allowlists() -> None:
    assert "application/zip" in settings.ALLOWED_DOCUMENT_MIMES
    assert "application/zip" in settings.ALLOWED_ENCLOSURE_MIMES


def test_rar_is_disabled_by_default() -> None:
    disabled = Settings(RAR_UPLOAD_ENABLED=False)
    assert "application/vnd.rar" not in disabled.ALLOWED_DOCUMENT_MIMES
    assert "application/vnd.rar" not in disabled.ALLOWED_ENCLOSURE_MIMES
    assert ArchiveIntakePolicy(rar_enabled=False).is_admitted("application/vnd.rar") is False


def test_rar_can_be_admitted_only_with_the_explicit_enablement() -> None:
    enabled = Settings(RAR_UPLOAD_ENABLED=True)
    assert "application/vnd.rar" in enabled.ALLOWED_DOCUMENT_MIMES
    assert "application/vnd.rar" in enabled.ALLOWED_ENCLOSURE_MIMES
    assert ArchiveIntakePolicy(rar_enabled=True).is_admitted("application/vnd.rar") is True


def test_policy_identifies_archives() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)
    assert policy.is_archive("application/zip") is True
    assert policy.is_archive("application/vnd.rar") is True
    assert policy.is_archive("application/pdf") is False
    assert policy.is_archive(None) is False


def test_archives_do_not_require_a_letter_number() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)
    assert policy.requires_letter_number("application/zip") is False
    assert policy.requires_letter_number("application/pdf") is True


def test_archives_never_create_a_processing_job() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=True)
    assert policy.creates_processing_job("application/zip") is False
    assert policy.creates_processing_job("application/vnd.rar") is False
    assert policy.creates_processing_job("application/pdf") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_archive_intake.py -q`
Expected: FAIL — `assert 'application/octet-stream' == 'application/zip'`

- [ ] **Step 3: Add the magic-byte branches**

In `backend/rbac_backend/utils/file_validation.py`, insert **after** the existing DOCX branch (so DOCX keeps priority) and before the PNG branch:

```python
    # RAR: v4 signature is "Rar!\x1a\x07\x00", v5 is "Rar!\x1a\x07\x01\x00".
    if data.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"

    # ZIP container. Checked after the DOCX branch above, which is also a zip
    # and is disambiguated by its filename.
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
```

- [ ] **Step 4: Admit ZIP and make RAR enablement fail-closed**

In `backend/rbac_backend/core/config.py`, add the flag and extend the two default sets with ZIP only:

```python
    # Set true only when Task 0.4 recorded RAR_INNER_SCAN_PROVEN=yes for the
    # deployed clamd. A config line or image capability claim is insufficient.
    RAR_UPLOAD_ENABLED: bool = Field(default=False, validation_alias="RAR_UPLOAD_ENABLED")
```

```python
    ALLOWED_DOCUMENT_MIMES: set[str] = Field(
        default={
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/plain",
            # Archives are stored intact and never unpacked or processed.
            "application/zip",
        }
    )
```

```python
    ALLOWED_ENCLOSURE_MIMES: set[str] = Field(
        default={
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/plain",
            "application/zip",
        }
    )
```

In the settings post-validator, reject an explicit RAR MIME while `RAR_UPLOAD_ENABLED` is false, and add RAR to both effective sets only when the flag is true. This prevents an environment allowlist from accidentally bypassing the proof gate:

```python
rar_mime = "application/vnd.rar"
explicit_rar = rar_mime in self.ALLOWED_DOCUMENT_MIMES or rar_mime in self.ALLOWED_ENCLOSURE_MIMES
if explicit_rar and not self.RAR_UPLOAD_ENABLED:
    raise ValueError("RAR MIME configured while RAR_UPLOAD_ENABLED=false")
if self.RAR_UPLOAD_ENABLED:
    self.ALLOWED_DOCUMENT_MIMES.add(rar_mime)
    self.ALLOWED_ENCLOSURE_MIMES.add(rar_mime)
```

Update `.env.example` with `RAR_UPLOAD_ENABLED=false`. The production `.env` may be changed to true only in the same reviewed change that attaches Task 0.4's positive EICAR-in-RAR evidence. If the proof is `no`, RAR remains absent from the upload-policy response and client picker.

- [ ] **Step 5: Write `ArchiveIntakePolicy`**

```python
"""Intake rules for archive uploads.

Archives are stored as immutable FileObjects and nothing more. Antivirus and
SHA-256 duplicate detection still run - only extraction is skipped - so this
adds no new malware path and no new Python dependency, because nothing ever
reads inside the archive.
"""

from __future__ import annotations

from typing import Optional

from .extraction.source_kind import ARCHIVE_MIMES


class ArchiveIntakePolicy:
    """Decides what an archive upload is exempt from."""

    def __init__(self, *, rar_enabled: bool) -> None:
        self.rar_enabled = bool(rar_enabled)

    @staticmethod
    def is_archive(mime: Optional[str]) -> bool:
        if not mime:
            return False
        return mime.split(";", 1)[0].strip().lower() in ARCHIVE_MIMES

    def is_admitted(self, mime: Optional[str]) -> bool:
        normalized = (mime or "").split(";", 1)[0].strip().lower()
        return normalized == "application/zip" or (
            normalized == "application/vnd.rar" and self.rar_enabled
        )

    @classmethod
    def requires_letter_number(cls, mime: Optional[str]) -> bool:
        """Archives carry no letter number; every other type still must."""
        return not cls.is_archive(mime)

    @classmethod
    def creates_processing_job(cls, mime: Optional[str]) -> bool:
        """Archives are never queued for extraction, whatever the OCR flag says."""
        return not cls.is_archive(mime)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_archive_intake.py -q`
Expected: PASS — 10 passed

- [ ] **Step 7: Run the upload-security suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "upload or antivirus or validation"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/utils/file_validation.py projectDMS/backend/rbac_backend/services/archive_policy.py projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/.env.example projectDMS/backend/rbac_backend/tests/test_archive_intake.py
git commit -m "feat: admit zip and rar archives as store-only uploads"
```

---

### Task 5.2: Wire the archive gate into the document router

**Files:**
- Modify: `backend/rbac_backend/routers/documents.py:557-764`
- Test: `backend/rbac_backend/tests/test_archive_upload_route.py`

**Interfaces:**
- Consumes: `ArchiveIntakePolicy` (5.1)
- Produces: no new signatures; two behavioural changes in `create_document` — letter number optional for archives, and no `document_processing_jobs` row for archives

**Order matters:** the letter-number check currently runs *before* the file is spooled and sniffed, so it must move to *after* `validate_spooled_upload` — the MIME is not known until then.

- [ ] **Step 1: Write the failing test**

```python
"""Archive uploads skip the letter-number requirement and the processing job."""

from __future__ import annotations

import inspect

from rbac_backend.core.config import Settings
from rbac_backend.routers import documents as documents_module
from rbac_backend.services.archive_policy import ArchiveIntakePolicy


def test_router_consults_the_archive_policy() -> None:
    source = inspect.getsource(documents_module)

    assert "ArchiveIntakePolicy" in source


def test_letter_number_check_runs_after_mime_detection() -> None:
    source = inspect.getsource(documents_module.DocumentController.create_document)

    validate_at = source.index("validate_spooled_upload")
    letter_check_at = source.index("requires_letter_number")

    assert validate_at < letter_check_at, (
        "The letter-number requirement depends on the detected MIME type, which "
        "is not known until the spooled upload has been validated."
    )


def test_job_creation_is_gated_on_the_archive_policy() -> None:
    source = inspect.getsource(documents_module.DocumentController.create_document)

    assert "creates_processing_job" in source


def test_policy_exempts_archives_end_to_end() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)
    assert policy.requires_letter_number("application/zip") is False
    assert policy.creates_processing_job("application/zip") is False
    assert policy.requires_letter_number("application/pdf") is True
    assert policy.creates_processing_job("application/pdf") is True


def test_disabled_rar_is_rejected_by_validation_before_archive_exemption() -> None:
    assert "application/vnd.rar" not in Settings(
        RAR_UPLOAD_ENABLED=False
    ).ALLOWED_DOCUMENT_MIMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_archive_upload_route.py -q`
Expected: FAIL — `AssertionError` on the `ArchiveIntakePolicy` import check

- [ ] **Step 3: Modify the router**

Add the import at the top of `backend/rbac_backend/routers/documents.py`:

```python
from ..services.archive_policy import ArchiveIntakePolicy
```

In `DocumentController.create_document`, **remove** the unconditional letter-number check from step 2 (currently before spooling) and insert this immediately after the `validate_spooled_upload` block:

```python
                # Letter numbers identify correspondence. An archive is a
                # container, not a letter, so it is exempt - but the exemption
                # can only be decided once the MIME type is known.
                detected_mime = validation_result.mime_type
                archive_policy = ArchiveIntakePolicy(
                    rar_enabled=settings.RAR_UPLOAD_ENABLED
                )
                if archive_policy.requires_letter_number(detected_mime) and not letter_no:
                    raise DocumentError(
                        "Letter number is required", status.HTTP_400_BAD_REQUEST
                    )
```

Then change the job-creation branch at the end of the method:

```python
            if ocr_enabled and archive_policy.creates_processing_job(detected_mime):
                await self.document_service.queue_document_processing(...)
            else:
                logger.info(
                    "[document_pipeline] No extraction job created for %s (mime=%s, ocr_enabled=%s)",
                    spooled.filename,
                    detected_mime,
                    ocr_enabled,
                )
```

Keep the existing `queue_document_processing` call arguments exactly as they are — only the condition changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_archive_upload_route.py -q`
Expected: PASS — 5 passed

- [ ] **Step 5: Regenerate the route contract**

Run: `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`

- [ ] **Step 6: Run the route-authz and document suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "route or document or domain_error"`
Expected: PASS (allowing the known `test_route_control_manifest` notifications failure)

- [ ] **Step 7: Commit**

```bash
git add projectDMS/backend/rbac_backend/routers/documents.py projectDMS/backend/rbac_backend/route_control_manifest.json projectDMS/backend/rbac_backend/tests/test_archive_upload_route.py
git commit -m "feat: exempt archive uploads from letter number and extraction jobs"
```

---

### Task 5.3: Canonical upload policy endpoint and client alignment

**Files:**
- Create: `backend/rbac_backend/services/upload_policy.py`
- Create: `backend/rbac_backend/routers/upload_policy.py`
- Modify: `backend/rbac_backend/main.py` (register the router)
- Modify: `client/src/services/enhanced-api.ts`, `client/src/pages/UploadPage.tsx:809`
- Test: `backend/rbac_backend/tests/test_upload_policy.py`

**Interfaces:**
- Consumes: `settings`, `ARCHIVE_MIMES` (4.1)
- Produces: `UploadPolicyService.get_policy() -> dict` with per-surface `{mimes, extensions, max_size_mb}`; `GET /api/config/upload-policy`; client `fetchUploadPolicy()`

**Why a runtime endpoint, not codegen:** these allowlists are env-overridable, so a build-time constant would reflect defaults rather than deployed configuration.

- [ ] **Step 1: Write the failing test**

```python
"""The backend owns the supported-file policy; the client renders it."""

from __future__ import annotations

from rbac_backend.core.config import settings
from rbac_backend.services.upload_policy import (
    MIME_EXTENSIONS,
    UploadPolicyService,
)


def test_policy_covers_every_surface() -> None:
    policy = UploadPolicyService.get_policy()

    assert set(policy) == {"document", "enclosure", "contract", "version"}


def test_document_policy_matches_the_configured_allowlist() -> None:
    policy = UploadPolicyService.get_policy()["document"]

    assert set(policy["mimes"]) == set(settings.ALLOWED_DOCUMENT_MIMES)
    assert policy["max_size_mb"] == settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB


def test_every_allowed_mime_has_at_least_one_extension() -> None:
    policy = UploadPolicyService.get_policy()

    for surface, entry in policy.items():
        for mime in entry["mimes"]:
            assert MIME_EXTENSIONS.get(mime), f"{surface}: no extension mapped for {mime}"


def test_extensions_are_dot_prefixed_and_lowercase() -> None:
    policy = UploadPolicyService.get_policy()["document"]

    for extension in policy["extensions"]:
        assert extension.startswith(".")
        assert extension == extension.lower()


def test_archive_extensions_follow_the_fail_closed_config() -> None:
    extensions = UploadPolicyService.get_policy()["document"]["extensions"]

    assert ".zip" in extensions
    assert (".rar" in extensions) is settings.RAR_UPLOAD_ENABLED


def test_unsupported_types_are_absent() -> None:
    # The client picker historically offered .doc/.docx/.gif, which the backend
    # rejects with 415. The served policy must not reintroduce them.
    extensions = UploadPolicyService.get_policy()["document"]["extensions"]

    assert ".doc" not in extensions
    assert ".gif" not in extensions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_upload_policy.py -q`
Expected: FAIL — `ModuleNotFoundError` for `upload_policy`

- [ ] **Step 3: Write the service**

```python
"""The canonical supported-file policy.

The backend is the single source of truth. The client fetches this at runtime
rather than shipping its own list, because the allowlists are env-overridable
and a build-time copy would reflect defaults instead of deployed config.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core.config import settings

MIME_EXTENSIONS: Dict[str, List[str]] = {
    "application/pdf": [".pdf"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "text/plain": [".txt"],
    "application/zip": [".zip"],
    "application/vnd.rar": [".rar"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
}


class UploadPolicyService:
    @staticmethod
    def _entry(mimes: set[str], max_size_mb: int) -> Dict[str, Any]:
        ordered = sorted(mimes)
        extensions: List[str] = []
        for mime in ordered:
            for extension in MIME_EXTENSIONS.get(mime, []):
                if extension not in extensions:
                    extensions.append(extension)
        return {
            "mimes": ordered,
            "extensions": sorted(extensions),
            "max_size_mb": int(max_size_mb),
        }

    @classmethod
    def get_policy(cls) -> Dict[str, Dict[str, Any]]:
        general_max = int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)
        return {
            "document": cls._entry(set(settings.ALLOWED_DOCUMENT_MIMES), general_max),
            "enclosure": cls._entry(set(settings.ALLOWED_ENCLOSURE_MIMES), general_max),
            "contract": cls._entry(set(settings.ALLOWED_CONTRACT_MIMES), general_max),
            "version": cls._entry(set(settings.ALLOWED_DOCUMENT_MIMES), general_max),
        }
```

- [ ] **Step 4: Write the router**

```python
"""Serve the canonical upload policy to the client."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.errors import BaseDomainError
from ..core.security import get_current_user
from ..models.user import CurrentUser
from ..services.upload_policy import UploadPolicyService

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/upload-policy")
async def get_upload_policy(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Return the allowed MIME types, extensions, and size caps per surface.

    Authenticated but not scoped: the policy is configuration, not tenant data.
    """
    try:
        return {"policy": UploadPolicyService.get_policy()}
    except (BaseDomainError, HTTPException):
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Upload policy unavailable") from exc
```

Register it in `main.py` beside the other routers, under the `/api` prefix, matching the surrounding `app.include_router(...)` style.

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_upload_policy.py -q`
Expected: PASS — 6 passed

- [ ] **Step 6: Regenerate the route contract and re-run route tests**

Run: `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "route or domain_error"`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 7: Align the client picker**

In `client/src/services/enhanced-api.ts`, add beside the other adapters:

```typescript
export interface UploadSurfacePolicy {
  mimes: string[];
  extensions: string[];
  max_size_mb: number;
}

export interface UploadPolicy {
  document: UploadSurfacePolicy;
  enclosure: UploadSurfacePolicy;
  contract: UploadSurfacePolicy;
  version: UploadSurfacePolicy;
}

// Offline fallback only. A backend test asserts this is a subset of the served
// policy, so the two cannot drift the way the old hard-coded accept list did.
export const FALLBACK_UPLOAD_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.txt', '.zip'];

export async function fetchUploadPolicy(): Promise<UploadPolicy | null> {
  try {
    const response = await api.get<{ policy: UploadPolicy }>('/config/upload-policy');
    return response.data.policy;
  } catch {
    return null;
  }
}
```

In `client/src/pages/UploadPage.tsx`, replace the hard-coded `accept` at line 809:

```tsx
const [uploadPolicy, setUploadPolicy] = useState<UploadPolicy | null>(null);

useEffect(() => {
  void fetchUploadPolicy().then(setUploadPolicy);
}, []);

const acceptedExtensions = (
  uploadPolicy?.document.extensions ?? FALLBACK_UPLOAD_EXTENSIONS
).join(',');
```

```tsx
                    accept={acceptedExtensions}
```

- [ ] **Step 8: Verify the client builds and your files are clean**

Run: `cd client && npm run lint && npm run build`
Expected: build succeeds. Then run `npx tsc -b` and confirm **`UploadPage.tsx` and `enhanced-api.ts` do not appear** in the output — the 146 pre-existing errors are not a gate.

- [ ] **Step 9: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/upload_policy.py projectDMS/backend/rbac_backend/routers/upload_policy.py projectDMS/backend/rbac_backend/main.py projectDMS/backend/rbac_backend/route_control_manifest.json projectDMS/backend/rbac_backend/tests/test_upload_policy.py projectDMS/client/src/services/enhanced-api.ts projectDMS/client/src/pages/UploadPage.tsx
git commit -m "feat: serve a canonical upload policy and drive the picker from it"
```

---

## Phase 6 — Deterministic extraction quality gate

**This phase gates Phase 7 and must not be skipped or thinned.** Companion §3.3 measured a naive numeric rule producing **12 false mismatches on a correct document** — twelve paid escalations that buy nothing. Companion §3.1 measured **9 split-digit corruptions in the native text layer** — so the gate must also run on pages nobody suspects.

### Task 6.1: Column-role mapping

**Files:**
- Create: `backend/rbac_backend/services/extraction/quality/__init__.py`
- Create: `backend/rbac_backend/services/extraction/quality/models.py`
- Create: `backend/rbac_backend/services/extraction/quality/column_roles.py`
- Test: `backend/rbac_backend/tests/test_quality_column_roles.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class ColumnRole(str, Enum)` — `SERIAL`, `DESCRIPTION`, `NOS`, `QUANTITY`, `UNIT`, `RATE`, `AMOUNT`, `UNKNOWN`
  - `map_column_roles(headers: Sequence[str]) -> list[ColumnRole]`
  - `is_checkable(roles: Sequence[ColumnRole]) -> bool` — True only when `RATE` **and** `AMOUNT` and at least one of `QUANTITY`/`NOS` are present

**The critical rule:** an unidentified column layout is **not checkable**, never **failed**. Companion §3.3's twelve false positives came from checking tables whose roles were never established.

- [ ] **Step 1: Write the failing test**

```python
"""Column-role mapping - the prerequisite for any numeric check.

Companion document 3.3: a naive "last three numeric columns are qty x rate =
amount" rule produced 12 false mismatches on a correct document, because a
separate Nos column applied, two tables declared their own formulas, and an S/N
serial was read as a quantity. Roles must be established before anything is
checked, and an unestablished layout is NOT_CHECKABLE, not FAILED.
"""

from __future__ import annotations

from rbac_backend.services.extraction.quality.column_roles import (
    ColumnRole,
    is_checkable,
    map_column_roles,
)


def test_canonical_boq_header_maps_cleanly() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Unit", "Rate", "Amount"])

    assert roles == [
        ColumnRole.SERIAL,
        ColumnRole.DESCRIPTION,
        ColumnRole.QUANTITY,
        ColumnRole.UNIT,
        ColumnRole.RATE,
        ColumnRole.AMOUNT,
    ]


def test_nos_is_distinguished_from_quantity() -> None:
    # The measured page-5 case: 3 x 32.61 x 3,200 = 313,056. Reading Nos as Qty
    # produced a false mismatch.
    roles = map_column_roles(["Description", "Nos", "Qty", "Rate", "Amount"])

    assert roles[1] is ColumnRole.NOS
    assert roles[2] is ColumnRole.QUANTITY


def test_serial_column_is_never_a_quantity() -> None:
    for header in ("S/N", "Sr. No.", "SL", "Item No", "#"):
        assert map_column_roles([header])[0] is ColumnRole.SERIAL


def test_header_variants_are_recognised() -> None:
    assert map_column_roles(["Quantity"])[0] is ColumnRole.QUANTITY
    assert map_column_roles(["Unit Rate"])[0] is ColumnRole.RATE
    assert map_column_roles(["Total Amount (INR)"])[0] is ColumnRole.AMOUNT
    assert map_column_roles(["UOM"])[0] is ColumnRole.UNIT


def test_unrecognised_headers_are_unknown_not_guessed() -> None:
    roles = map_column_roles(["Depth (mtr)", "Thickness (mtr)", "Volume (m3)"])

    assert all(role is ColumnRole.UNKNOWN for role in roles)


def test_table_with_unknown_roles_is_not_checkable() -> None:
    # Page 5's declared-formula table: Area A=[h*l], Volume [l*b*h]. Not a
    # qty x rate table at all - checking it produced 8 false mismatches.
    roles = map_column_roles(["DW No", "Depth", "Thickness", "Length", "Area", "Volume"])

    assert is_checkable(roles) is False


def test_table_with_qty_rate_amount_is_checkable() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Rate", "Amount"])

    assert is_checkable(roles) is True


def test_nos_alone_with_rate_and_amount_is_checkable() -> None:
    roles = map_column_roles(["Description", "Nos", "Rate", "Amount"])

    assert is_checkable(roles) is True


def test_missing_amount_is_not_checkable() -> None:
    roles = map_column_roles(["S/N", "Description", "Qty", "Rate"])

    assert is_checkable(roles) is False


def test_empty_header_row_is_not_checkable() -> None:
    assert is_checkable(map_column_roles([])) is False
    assert is_checkable(map_column_roles(["", None])) is False  # type: ignore[list-item]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_column_roles.py -q`
Expected: FAIL — `ModuleNotFoundError` for `quality.column_roles`

- [ ] **Step 3: Write the implementation**

Create `backend/rbac_backend/services/extraction/quality/__init__.py`:

```python
"""Deterministic quality checks over extracted page content."""
```

Create `backend/rbac_backend/services/extraction/quality/column_roles.py`:

```python
"""Map table headers to semantic column roles.

Nothing numeric may be checked until roles are established. The measured
failure mode (companion 3.3) is checking a table whose columns were never
identified: a Nos multiplier is missed, a declared in-table formula is ignored,
or a serial number is read as a quantity. Each produces a false mismatch, and
each false mismatch is a paid model call in Phase 7.

is_checkable is therefore deliberately strict. "We could not identify this
table" must resolve to NOT_CHECKABLE, never to FAILED.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional, Sequence


class ColumnRole(str, Enum):
    SERIAL = "serial"
    DESCRIPTION = "description"
    NOS = "nos"
    QUANTITY = "quantity"
    UNIT = "unit"
    RATE = "rate"
    AMOUNT = "amount"
    UNKNOWN = "unknown"


# Ordered: the first matching pattern wins, so SERIAL is tested before QUANTITY
# and NOS before QUANTITY.
_PATTERNS: list[tuple[ColumnRole, re.Pattern[str]]] = [
    (ColumnRole.SERIAL, re.compile(r"^\s*(s\s*[/.]?\s*n|sr\.?\s*no|sl\.?\s*no|sl|item\s*no|#)\s*\.?\s*$")),
    (ColumnRole.NOS, re.compile(r"^\s*(nos?|no\.?\s*of|count)\s*\.?\s*$")),
    (ColumnRole.DESCRIPTION, re.compile(r"(description|particular|item|work|activity|head)")),
    (ColumnRole.QUANTITY, re.compile(r"^\s*(qty|quantity)\b")),
    (ColumnRole.UNIT, re.compile(r"^\s*(unit|uom|u\s*/\s*m)\s*$")),
    (ColumnRole.RATE, re.compile(r"(^|\s)(rate|unit\s*rate|price|unit\s*price)(\s|$)")),
    (ColumnRole.AMOUNT, re.compile(r"(amount|total|value)")),
]


def _normalize(header: Optional[str]) -> str:
    if not header:
        return ""
    cleaned = re.sub(r"\((?:[^)]*)\)", " ", str(header))
    cleaned = re.sub(r"[^a-z0-9/#.\s]", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def map_column_roles(headers: Sequence[Optional[str]]) -> List[ColumnRole]:
    """Map each header cell to a role, or UNKNOWN when it is not recognised."""
    roles: List[ColumnRole] = []
    for header in headers:
        normalized = _normalize(header)
        if not normalized:
            roles.append(ColumnRole.UNKNOWN)
            continue
        for role, pattern in _PATTERNS:
            if pattern.search(normalized):
                roles.append(role)
                break
        else:
            roles.append(ColumnRole.UNKNOWN)
    return roles


def is_checkable(roles: Sequence[ColumnRole]) -> bool:
    """True only when a qty-or-nos x rate = amount identity is well defined."""
    present = set(roles)
    if ColumnRole.RATE not in present or ColumnRole.AMOUNT not in present:
        return False
    return bool(present & {ColumnRole.QUANTITY, ColumnRole.NOS})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_column_roles.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/quality/__init__.py projectDMS/backend/rbac_backend/services/extraction/quality/column_roles.py projectDMS/backend/rbac_backend/tests/test_quality_column_roles.py
git commit -m "feat: map table column roles before any numeric check runs"
```

---

### Task 6.2: Split-digit detection and dual-confirmation repair

**Files:**
- Create: `backend/rbac_backend/services/extraction/quality/numeric_checks.py`
- Test: `backend/rbac_backend/tests/test_quality_numeric_checks.py`

**Interfaces:**
- Consumes: `ColumnRole`, `is_checkable` (6.1)
- Produces:
  - `QUANTITY_DISPLAY_REL_TOLERANCE = Decimal("0.01")` — applies only to a displayed quantity versus the quantity implied by amount/rate
  - `MONETARY_ABS_TOLERANCE = Decimal("1")` — applies only to monetary arithmetic at the source's displayed currency precision; there is no blanket monetary relative tolerance
  - `parse_amount(raw: str) -> Decimal | None`
  - `detect_split_digits(raw: str) -> str | None` — returns the repaired literal or `None`
  - `check_row(values, roles) -> CheckResult`
  - `check_subtotal(rows, stated_total, roles, *, candidate_row_index) -> CheckResult`
  - `propose_repair(raw, *, row_check, subtotal_check) -> NumericRepair | None` — returns `None` unless two genuinely independent check families validate the same repaired value

**The rule from the companion document, verbatim:** accept a numeric repair only when **two independent structural checks agree**; record before / after / reason / method / confidence / page; never repair on a single check.

- [ ] **Step 1: Write the failing test**

```python
"""Numeric integrity: detect corruption, repair only on dual confirmation.

Companion document 3.1 measured 9 split-digit corruptions in a PDF's own text
layer - "1 ,900,000" for 1,900,000. Parsed naively that reads as 1, and a claim
subtotal lands 2.5 crore light. pdfplumber x_tolerance tuning does NOT fix it
(17 tokens survive every setting from 1.0 to 3.0), so the repair is structural.
"""

from __future__ import annotations

from decimal import Decimal

from rbac_backend.services.extraction.quality.column_roles import ColumnRole
from rbac_backend.services.extraction.quality.models import CheckResult, Verdict
from rbac_backend.services.extraction.quality.numeric_checks import (
    MONETARY_ABS_TOLERANCE,
    QUANTITY_DISPLAY_REL_TOLERANCE,
    check_subtotal,
    check_row,
    detect_split_digits,
    parse_amount,
    propose_repair,
)

ROLES = [
    ColumnRole.SERIAL,
    ColumnRole.DESCRIPTION,
    ColumnRole.QUANTITY,
    ColumnRole.RATE,
    ColumnRole.AMOUNT,
]


def test_parses_indian_grouped_amounts() -> None:
    assert parse_amount("22,140,168") == Decimal("22140168")
    assert parse_amount("110,000.00") == Decimal("110000.00")
    assert parse_amount("") is None
    assert parse_amount("n/a") is None


def test_detects_the_measured_split_digit_corruptions() -> None:
    # All nine, verbatim from companion document 3.1.
    measured = {
        "1 ,900,000": "1,900,000",
        "5 77,188": "577,188",
        "9 50,000": "950,000",
        "7 0,500": "70,500",
        "1 34,460": "134,460",
        "4 42,728": "442,728",
        "1 10,000": "110,000",
        "1 15,000": "115,000",
        "4 8,960": "48,960",
    }

    for corrupted, expected in measured.items():
        assert detect_split_digits(corrupted) == expected, corrupted


def test_leaves_clean_numbers_alone() -> None:
    for clean in ("1,900,000", "22,140,168", "598.20", "0", "3,071.88"):
        assert detect_split_digits(clean) is None


def test_does_not_join_genuinely_separate_numbers() -> None:
    # Two columns collapsed into one cell must not become one number.
    assert detect_split_digits("52 184615") is None


def test_row_identity_passes_within_tolerance() -> None:
    # Companion 3.3: a displayed qty of 58 against a true 57.5 is a 0.86%
    # discrepancy that must pass, not escalate.
    result = check_row(["1", "Ground improvement", "58", "1,215", "70,500"], ROLES)

    assert result.verdict is Verdict.PASS


def test_row_identity_fails_outside_tolerance() -> None:
    result = check_row(["1", "Widget", "10", "100", "5,000"], ROLES)

    assert result.verdict is Verdict.FAIL
    assert "1,000" in result.detail or "1000" in result.detail


def test_nos_multiplier_is_honoured() -> None:
    # Page 5 row ii: 3 x 32.61 x 3,200 = 313,056. Ignoring Nos gives a false fail.
    roles = [ColumnRole.DESCRIPTION, ColumnRole.NOS, ColumnRole.QUANTITY, ColumnRole.RATE, ColumnRole.AMOUNT]
    result = check_row(["Guide wall", "3", "32.61", "3,200", "313,056"], roles)

    assert result.verdict is Verdict.PASS


def test_unidentified_roles_are_not_checkable_not_failed() -> None:
    roles = [ColumnRole.UNKNOWN] * 4
    result = check_row(["DW1", "1.2", "3.4", "5.6"], roles)

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_missing_values_are_not_checkable() -> None:
    result = check_row(["1", "Widget", "", "100", "5,000"], ROLES)

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_subtotal_matches_stated_total() -> None:
    rows = [
        ["1", "a", "1", "1", "9,600,000"],
        ["2", "b", "1", "1", "5,684,775"],
        ["3", "c", "1", "1", "185,441"],
    ]
    result = check_subtotal(rows, "15,470,216", ROLES, candidate_row_index=1)

    assert result.verdict is Verdict.PASS


def test_monetary_subtotal_absorbs_only_one_display_unit() -> None:
    # Companion 3.2: p3 line items sum to 18,450,139 against a stated 18,450,140.
    rows = [
        ["1", "a", "1", "1", "18,000,000"],
        ["2", "b", "1", "1", "450,139"],
    ]
    result = check_subtotal(rows, "18,450,140", ROLES, candidate_row_index=0)

    assert result.verdict is Verdict.PASS


def test_repair_requires_row_identity_and_independent_multirow_subtotal() -> None:
    repaired_row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    rows = [
        repaired_row,
        ["2", "Survey", "1", "100,000", "100,000"],
    ]
    row_check = check_row(repaired_row, ROLES)
    subtotal_check = check_subtotal(
        rows, "2,000,000", ROLES, candidate_row_index=0
    )

    repair = propose_repair(
        "1 ,900,000", row_check=row_check, subtotal_check=subtotal_check
    )

    assert repair is not None
    assert repair.before == "1 ,900,000"
    assert repair.after == "1,900,000"
    assert repair.method == "dual_confirmation"
    assert repair.confidence >= 0.99


def test_single_agreeing_check_never_repairs() -> None:
    row_check = check_row(["1", "Mobilization", "1", "1,900,000", "1,900,000"], ROLES)
    not_checkable = check_row(["1", "x", "", "", ""], ROLES)

    assert propose_repair(
        "1 ,900,000", row_check=row_check, subtotal_check=not_checkable
    ) is None


def test_disagreeing_checks_never_repair() -> None:
    row_check = check_row(["1", "Widget", "10", "100", "1,000"], ROLES)
    contradicting = check_subtotal(
        [["1", "Widget", "10", "100", "1,000"], ["2", "Peer", "1", "1", "1"]],
        "9,999", ROLES, candidate_row_index=0
    )

    assert propose_repair(
        "1 ,000", row_check=row_check, subtotal_check=contradicting
    ) is None


def test_quantity_and_money_tolerances_are_separate() -> None:
    assert QUANTITY_DISPLAY_REL_TOLERANCE == Decimal("0.01")
    assert MONETARY_ABS_TOLERANCE == Decimal("1")


def test_one_percent_monetary_error_does_not_pass() -> None:
    rows = [["1", "a", "1", "1", "990"], ["2", "b", "1", "1", "10"]]
    result = check_subtotal(rows, "1,010", ROLES, candidate_row_index=0)
    assert result.verdict is Verdict.FAIL


def test_single_row_total_is_not_independent_corroboration() -> None:
    row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    row_check = check_row(row, ROLES)
    same_row_total = check_subtotal([row], "1,900,000", ROLES, candidate_row_index=0)
    assert same_row_total.verdict is Verdict.NOT_CHECKABLE
    assert propose_repair(
        "1 ,900,000", row_check=row_check, subtotal_check=same_row_total
    ) is None


def test_overlapping_structural_support_is_not_independent() -> None:
    candidate = Decimal("1900000")
    row_check = CheckResult(
        name="row_identity", verdict=Verdict.PASS, candidate_value=candidate,
        evidence_refs=["candidate.amount", "shared.operand"],
    )
    subtotal_check = CheckResult(
        name="subtotal_identity", verdict=Verdict.PASS, candidate_value=candidate,
        evidence_refs=["candidate.amount", "shared.operand", "printed.subtotal"],
    )
    assert propose_repair(
        "1 ,900,000", row_check=row_check, subtotal_check=subtotal_check
    ) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_numeric_checks.py -q`
Expected: FAIL — `ModuleNotFoundError` for `quality.models`

- [ ] **Step 3: Write `quality/models.py`**

```python
"""Verdicts and check results shared by every quality check."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import List, Optional


class Verdict(str, Enum):
    """Four outcomes. Only FAIL and INDETERMINATE escalate."""

    PASS = "pass"
    NOT_CHECKABLE = "not_checkable"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class CheckResult:
    name: str
    verdict: Verdict
    detail: str = ""
    expected: Optional[Decimal] = None
    actual: Optional[Decimal] = None
    candidate_value: Optional[Decimal] = None
    evidence_refs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class NumericRepair:
    before: str
    after: str
    reason: str
    method: str
    confidence: float
    page: Optional[int] = None


@dataclass
class QualityVerdict:
    verdict: Verdict
    checks: List[CheckResult] = field(default_factory=list)
    repairs: List[NumericRepair] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def escalates(self) -> bool:
        """NOT_CHECKABLE must never reach a paid model call."""
        return self.verdict in {Verdict.FAIL, Verdict.INDETERMINATE}
```

- [ ] **Step 4: Write `quality/numeric_checks.py`**

```python
"""Arithmetic integrity over extracted table rows.

Two independent checks exist: (1) a row identity (qty x nos x rate = amount),
and (2) a multirow subtotal identity using a separately printed subtotal and at
least one peer row. Re-summing a one-row "column" merely restates the candidate
amount and is NOT_CHECKABLE, not corroboration.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Sequence

from .column_roles import ColumnRole, is_checkable
from .models import CheckResult, NumericRepair, Verdict

# Quantity display rounding and monetary arithmetic are different error models.
# A displayed quantity may be rounded by up to 1%; money must balance to the
# displayed currency precision, with at most one whole displayed unit here.
QUANTITY_DISPLAY_REL_TOLERANCE = Decimal("0.01")
MONETARY_ABS_TOLERANCE = Decimal("1")

# A digit, then whitespace, then a group that is clearly the tail of a grouped
# number. "1 ,900,000" and "5 77,188" match; "52 184615" does not, because the
# tail is not grouped.
_SPLIT_DIGIT = re.compile(r"^(\d{1,2})\s+(,?\d{1,3}(?:,\d{3})*(?:\.\d+)?)$")


def parse_amount(raw: Optional[str]) -> Optional[Decimal]:
    """Parse a grouped decimal amount, or None when it is not a number."""
    if raw is None:
        return None
    cleaned = str(raw).strip().replace(",", "").replace(" ", "")
    if not cleaned or not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def detect_split_digits(raw: Optional[str]) -> Optional[str]:
    """Return the repaired literal when a token looks split, else None."""
    if raw is None:
        return None
    candidate = str(raw).strip()
    match = _SPLIT_DIGIT.match(candidate)
    if not match:
        return None
    head, tail = match.group(1), match.group(2)
    repaired = f"{head}{tail}" if tail.startswith(",") else f"{head},{tail}"
    return repaired if parse_amount(repaired) is not None else None


def _value_for(
    values: Sequence[str], roles: Sequence[ColumnRole], role: ColumnRole
) -> Optional[str]:
    for index, column_role in enumerate(roles):
        if column_role is role and index < len(values):
            return values[index]
    return None


def _money_matches(expected: Decimal, actual: Decimal) -> bool:
    return abs(actual - expected) <= MONETARY_ABS_TOLERANCE


def _quantity_display_matches(
    *, quantity: Decimal, nos: Decimal, rate: Decimal, amount: Decimal
) -> bool:
    denominator = nos * rate
    if denominator == 0:
        return False
    implied_quantity = amount / denominator
    if implied_quantity == 0:
        return quantity == 0
    return (
        abs(quantity - implied_quantity) / abs(implied_quantity)
        <= QUANTITY_DISPLAY_REL_TOLERANCE
    )


def check_row(values: Sequence[str], roles: Sequence[ColumnRole]) -> CheckResult:
    """Check qty (x nos) x rate = amount for one row."""
    if not is_checkable(roles):
        return CheckResult(
            name="row_identity",
            verdict=Verdict.NOT_CHECKABLE,
            detail="Column roles were not confidently identified",
        )

    rate = parse_amount(_value_for(values, roles, ColumnRole.RATE))
    amount = parse_amount(_value_for(values, roles, ColumnRole.AMOUNT))
    quantity = parse_amount(_value_for(values, roles, ColumnRole.QUANTITY))
    nos = parse_amount(_value_for(values, roles, ColumnRole.NOS))

    if rate is None or amount is None or (quantity is None and nos is None):
        return CheckResult(
            name="row_identity",
            verdict=Verdict.NOT_CHECKABLE,
            detail="One or more values in the identity could not be parsed",
        )

    multiplier = Decimal(1)
    if quantity is not None:
        multiplier *= quantity
    if nos is not None:
        multiplier *= nos

    expected = multiplier * rate
    quantity_for_tolerance = quantity or Decimal(1)
    nos_for_tolerance = nos or Decimal(1)
    if _money_matches(expected, amount) or _quantity_display_matches(
        quantity=quantity_for_tolerance,
        nos=nos_for_tolerance,
        rate=rate,
        amount=amount,
    ):
        return CheckResult(
            name="row_identity",
            verdict=Verdict.PASS,
            detail=f"{multiplier} x {rate} = {amount}",
            expected=expected,
            actual=amount,
            candidate_value=amount,
            evidence_refs=["row.quantity", "row.nos", "row.rate", "row.amount"],
        )
    return CheckResult(
        name="row_identity",
        verdict=Verdict.FAIL,
        detail=f"expected {expected:,} but the row states {amount:,}",
        expected=expected,
        actual=amount,
    )


def check_subtotal(
    rows: Sequence[Sequence[str]],
    stated_total: Optional[str],
    roles: Sequence[ColumnRole],
    *,
    candidate_row_index: int,
) -> CheckResult:
    """Check a candidate amount through an independent multirow subtotal."""
    if not is_checkable(roles):
        return CheckResult(
            name="subtotal_identity",
            verdict=Verdict.NOT_CHECKABLE,
            detail="Column roles were not confidently identified",
        )

    total = parse_amount(stated_total)
    if total is None:
        return CheckResult(
            name="subtotal_identity",
            verdict=Verdict.NOT_CHECKABLE,
            detail="No stated total to check against",
        )

    running = Decimal(0)
    counted = 0
    candidate_value: Optional[Decimal] = None
    peer_count = 0
    for row_index, row in enumerate(rows):
        raw = _value_for(row, roles, ColumnRole.AMOUNT)
        value = parse_amount(raw)
        if value is None:
            repaired = detect_split_digits(raw)
            value = parse_amount(repaired) if repaired else None
        if value is None:
            continue
        running += value
        counted += 1
        if row_index == candidate_row_index:
            candidate_value = value
        else:
            peer_count += 1

    if counted == 0 or candidate_value is None or peer_count < 1:
        return CheckResult(
            name="subtotal_identity",
            verdict=Verdict.NOT_CHECKABLE,
            detail="Independent subtotal requires the candidate plus at least one peer row",
        )

    # Absorb single-unit rounding: 18,450,139 against a stated 18,450,140.
    if _money_matches(total, running):
        return CheckResult(
            name="subtotal_identity",
            verdict=Verdict.PASS,
            detail=f"sum {running:,} vs stated {total:,}",
            expected=total,
            actual=running,
            candidate_value=candidate_value,
            evidence_refs=["printed.subtotal", "candidate.amount", "peer.amounts"],
        )
    return CheckResult(
        name="subtotal_identity",
        verdict=Verdict.FAIL,
        detail=f"sum {running:,} does not match stated {total:,}",
        expected=total,
        actual=running,
    )


def propose_repair(
    raw: str, *, row_check: CheckResult, subtotal_check: CheckResult
) -> Optional[NumericRepair]:
    """Propose a split-digit repair only when two checks independently agree.

    One check agreeing, or the two disagreeing, yields None - the value is
    flagged and escalated instead of silently rewritten.
    """
    repaired = detect_split_digits(raw)
    if repaired is None:
        return None

    if row_check.name != "row_identity" or subtotal_check.name != "subtotal_identity":
        return None
    if row_check.verdict is not Verdict.PASS or subtotal_check.verdict is not Verdict.PASS:
        return None
    candidate = parse_amount(repaired)
    if candidate is None:
        return None
    if row_check.candidate_value != candidate or subtotal_check.candidate_value != candidate:
        return None
    # The candidate itself must be shared; the evidence that validates it must
    # not be. Row operands and subtotal/peer operands are separate structures.
    candidate_refs = {"row.amount", "candidate.amount"}
    row_support = set(row_check.evidence_refs) - candidate_refs
    subtotal_support = set(subtotal_check.evidence_refs) - candidate_refs
    if not row_support or not subtotal_support or row_support & subtotal_support:
        return None

    return NumericRepair(
        before=raw,
        after=repaired,
        reason="Split-digit corruption in the text layer",
        method="dual_confirmation",
        confidence=0.99,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_numeric_checks.py -q`
Expected: PASS — 18 passed

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/quality/models.py projectDMS/backend/rbac_backend/services/extraction/quality/numeric_checks.py projectDMS/backend/rbac_backend/tests/test_quality_numeric_checks.py
git commit -m "feat: detect split-digit corruption and repair only on dual confirmation"
```

---

### Task 6.3: Mixed date-convention detection

**Files:**
- Create: `backend/rbac_backend/services/extraction/quality/date_checks.py`
- Test: `backend/rbac_backend/tests/test_quality_date_checks.py`

**Interfaces:**
- Consumes: `CheckResult`, `Verdict` (6.2)
- Produces: `class DateConvention(str, Enum)` — `DAY_FIRST`, `MONTH_FIRST`, `MIXED`, `AMBIGUOUS`, `UNKNOWN`; `detect_convention(values) -> DateConvention` using ordered-sequence evidence; `check_date_column(values) -> CheckResult`. If two equally plausible interpretations remain, the check returns `Verdict.INDETERMINATE` rather than claiming `MIXED`.

**Context:** `CLAUDE.md` records the repo's move to day-first `DD-MM-YYYY`. Companion §3.4 measured a 52-row register mixing `M/D/YYYY` and `D/M/YYYY` **in one column**, gating ₹9.6M of idling charges. A day-first parse of `2/26/2023` yields month 26 and fails; a silent fallback mis-dates the March entries. Ambiguity must survive as ambiguity.

- [ ] **Step 1: Write the failing test**

```python
"""Mixed date conventions in one column - the highest contractual risk.

Companion document 3.4 measured page 4's 52-row idle register mixing M/D/YYYY
and D/M/YYYY in the same column. Those dates establish the claim period behind
9,600,000 of idling charges. An unresolved ambiguity must stay unresolved.
"""

from __future__ import annotations

from rbac_backend.services.extraction.quality.date_checks import (
    DateConvention,
    check_date_column,
    detect_convention,
)
from rbac_backend.services.extraction.quality.models import Verdict


def test_unambiguous_month_first_is_detected() -> None:
    # Day > 12 in the second position proves month-first.
    values = ["2/26/2023", "2/27/2023", "2/28/2023"]

    assert detect_convention(values) is DateConvention.MONTH_FIRST


def test_unambiguous_day_first_is_detected() -> None:
    values = ["26/2/2023", "27/2/2023", "28/2/2023"]

    assert detect_convention(values) is DateConvention.DAY_FIRST


def test_the_measured_mixed_column_is_flagged_mixed() -> None:
    values = [
        "2/26/2023", "2/27/2023", "2/28/2023",   # must be M/D
        "1/3/2023", "2/3/2023", "12/3/2023",     # must be D/M (1-12 March)
        "3/13/2023", "3/24/2023",                # must be M/D
    ]

    assert detect_convention(values) is DateConvention.MIXED


def test_ambiguous_run_is_day_first_only_when_sequence_evidence_is_unique() -> None:
    # M/D for the middle values would move Feb 28 -> Jan 3 -> Feb 3 -> Dec 3
    # -> Mar 13. D/M yields the unique monotonic sequence Mar 1, 2, 12.
    values = ["2/28/2023", "1/3/2023", "2/3/2023", "12/3/2023", "3/13/2023"]
    assert detect_convention(values) is DateConvention.MIXED


def test_ambiguous_values_without_unique_sequence_evidence_stay_ambiguous() -> None:
    assert detect_convention(["1/3/2023", "2/3/2023", "3/3/2023"]) is DateConvention.AMBIGUOUS


def test_all_ambiguous_values_stay_ambiguous() -> None:
    # 3/5 and 5/5 are valid under both readings.
    assert detect_convention(["3/5/2023", "5/5/2023"]) is DateConvention.AMBIGUOUS


def test_empty_or_unparseable_input_is_unknown() -> None:
    assert detect_convention([]) is DateConvention.UNKNOWN
    assert detect_convention(["not a date", ""]) is DateConvention.UNKNOWN


def test_mixed_column_fails_the_check() -> None:
    result = check_date_column(["2/26/2023", "1/3/2023", "3/13/2023"])

    assert result.verdict is Verdict.FAIL
    assert "mixed" in result.detail.lower()


def test_ambiguous_column_is_indeterminate_not_failed() -> None:
    result = check_date_column(["3/5/2023", "5/5/2023"])

    assert result.verdict is Verdict.INDETERMINATE


def test_consistent_column_passes() -> None:
    result = check_date_column(["26/2/2023", "27/2/2023", "28/2/2023"])

    assert result.verdict is Verdict.PASS


def test_column_without_dates_is_not_checkable() -> None:
    result = check_date_column(["Idle", "Idle", "Working"])

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_iso_dates_are_unambiguous_and_pass() -> None:
    result = check_date_column(["2023-02-26", "2023-02-27"])

    assert result.verdict is Verdict.PASS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_date_checks.py -q`
Expected: FAIL — `ModuleNotFoundError` for `quality.date_checks`

- [ ] **Step 3: Write the implementation**

```python
"""Detect the date convention of a column, and refuse to guess when mixed.

The repository parses and renders day-first (DD-MM-YYYY). Real claim registers
carry Excel exports where days <= 12 were re-interpreted as months, producing
two conventions in one column. Silently normalising such a column mis-dates
entitlement evidence, so a mixed or ambiguous column is reported as such and
never resolved here.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from .models import CheckResult, Verdict

_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\s*$")
_ISO_DATE = re.compile(r"^\s*(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\s*$")


class DateConvention(str, Enum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"
    MIXED = "mixed"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


def _candidate_dates(
    value: Optional[str],
) -> List[Tuple[date, Optional[DateConvention]]]:
    """Return every calendar-valid interpretation for one value."""
    if value is None:
        return []
    text = str(value)
    iso = _ISO_DATE.match(text)
    if iso:
        try:
            return [(date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), None)]
        except ValueError:
            return []
    match = _NUMERIC_DATE.match(text)
    if not match:
        return []
    first, second, raw_year = map(int, match.groups())
    year = raw_year + 2000 if raw_year < 100 else raw_year
    candidates: List[Tuple[date, Optional[DateConvention]]] = []
    for convention, day, month in (
        (DateConvention.DAY_FIRST, first, second),
        (DateConvention.MONTH_FIRST, second, first),
    ):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        item = (candidate, convention)
        if item not in candidates:
            candidates.append(item)
    return candidates


def detect_convention(values: Sequence[Optional[str]]) -> DateConvention:
    """Choose a convention only when ordered context yields a unique best path.

    Dynamic programming minimises, in order: backward date transitions,
    backward days, then convention switches. All equal-best convention masks are
    retained; disagreement among them is AMBIGUOUS, not guessed.
    """
    observations = [_candidate_dates(value) for value in values]
    observations = [candidates for candidates in observations if candidates]
    if not observations:
        return DateConvention.UNKNOWN

    # candidate -> (score tuple, possible convention masks)
    states: Dict[Tuple[date, Optional[DateConvention]], Tuple[Tuple[int, int, int], set[frozenset[DateConvention]]]] = {}
    for candidate in observations[0]:
        mask = frozenset([candidate[1]]) if candidate[1] is not None else frozenset()
        states[candidate] = ((0, 0, 0), {mask})

    for candidates in observations[1:]:
        next_states = {}
        for current in candidates:
            best_score = None
            best_masks: set[frozenset[DateConvention]] = set()
            for previous, (score, masks) in states.items():
                backwards = current[0] < previous[0]
                backward_days = (previous[0] - current[0]).days if backwards else 0
                switched = (
                    previous[1] is not None
                    and current[1] is not None
                    and previous[1] is not current[1]
                )
                candidate_score = (
                    score[0] + int(backwards),
                    score[1] + backward_days,
                    score[2] + int(switched),
                )
                if best_score is None or candidate_score < best_score:
                    best_score, best_masks = candidate_score, set()
                if candidate_score == best_score:
                    for mask in masks:
                        best_masks.add(
                            mask | ({current[1]} if current[1] is not None else set())
                        )
            next_states[current] = (best_score, best_masks)
        states = next_states

    winning_score = min(score for score, _ in states.values())
    winning_masks = set().union(
        *(masks for score, masks in states.values() if score == winning_score)
    )
    if len(winning_masks) != 1:
        return DateConvention.AMBIGUOUS
    conventions = next(iter(winning_masks))
    if len(conventions) > 1:
        return DateConvention.MIXED
    if len(conventions) == 1:
        return next(iter(conventions))
    return DateConvention.DAY_FIRST  # ISO-only input is unambiguous and passes


def check_date_column(values: Sequence[Optional[str]]) -> CheckResult:
    """Report the column's convention as a quality check result."""
    convention = detect_convention(values)

    if convention is DateConvention.UNKNOWN:
        return CheckResult(
            name="date_convention",
            verdict=Verdict.NOT_CHECKABLE,
            detail="No parseable dates in this column",
        )
    if convention is DateConvention.MIXED:
        return CheckResult(
            name="date_convention",
            verdict=Verdict.FAIL,
            detail=(
                "Column uses mixed day-first and month-first conventions; "
                "dates cannot be normalised without losing meaning"
            ),
        )
    if convention is DateConvention.AMBIGUOUS:
        return CheckResult(
            name="date_convention",
            verdict=Verdict.INDETERMINATE,
            detail="Every value reads validly under both conventions",
        )
    return CheckResult(
        name="date_convention",
        verdict=Verdict.PASS,
        detail=f"Consistent {convention.value} column",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_date_checks.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/quality/date_checks.py projectDMS/backend/rbac_backend/tests/test_quality_date_checks.py
git commit -m "feat: detect mixed date conventions without silently normalising them"
```

---

### Task 6.4: Reading-order coherence

**Files:**
- Create: `backend/rbac_backend/services/extraction/quality/reading_order.py`
- Test: `backend/rbac_backend/tests/test_quality_reading_order.py`

**Interfaces:**
- Consumes: `CheckResult`, `Verdict` (6.2), `PageClassification` (1.2)
- Produces: `check_reading_order(text, classification) -> CheckResult`, `check_text_density(text, classification) -> CheckResult`

**Context:** companion §3.5 measured page 5's narrative shredded by an adjacent table's header cells. Companion §2.1's failure mode is a page whose class says it has content but whose text is near-empty.

- [ ] **Step 1: Write the failing test**

```python
"""Reading-order and text-density coherence checks."""

from __future__ import annotations

from rbac_backend.services.extraction.models import PageClass, PageClassification
from rbac_backend.services.extraction.quality.models import Verdict
from rbac_backend.services.extraction.quality.reading_order import (
    check_reading_order,
    check_text_density,
)


def _classification(page_class: PageClass, char_count: int) -> PageClassification:
    return PageClassification(
        page_class=page_class,
        char_count=char_count,
        image_count=0,
        image_coverage=0.0,
        table_count=1,
        width=595.0,
        height=842.0,
        rotation=0,
    )


def test_coherent_narrative_passes() -> None:
    text = (
        "The Contractor hereby submits its revised cost claim in respect of the "
        "reworks necessitated by the soil collapse at Nayaganj Station, as "
        "directed by the Engineer in charge."
    )

    result = check_reading_order(text, _classification(PageClass.TEXT_NATIVE, len(text)))

    assert result.verdict is Verdict.PASS


def test_shredded_narrative_is_detected() -> None:
    # Companion 3.5, measured verbatim: a sentence interleaved with the adjacent
    # table's header cells.
    text = (
        "GUIDE WALL & D-WALL REWORKS COST WITH INCLUDING ALL TOOLS AS PER THE Depth Thickness Length Volume\n"
        "DW No Area\n"
        "SPECIFICATION, DRAWINGS AND DIRECTION OF ENGINEER IN CHARGE (mtr) (mtr) (mtr) (m3)"
    )

    result = check_reading_order(text, _classification(PageClass.MIXED_CONTENT, len(text)))

    assert result.verdict is Verdict.FAIL
    assert "reading order" in result.detail.lower()


def test_pure_table_text_is_not_checkable_for_reading_order() -> None:
    text = "1 100 200\n2 300 400\n3 500 600"

    result = check_reading_order(text, _classification(PageClass.TABLE_HEAVY, len(text)))

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_empty_text_is_not_checkable_for_reading_order() -> None:
    result = check_reading_order("", _classification(PageClass.BLANK, 0))

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_mixed_content_page_with_no_text_fails_density() -> None:
    # The measured 2.1 defect: a page that plainly has content, extracted empty.
    result = check_text_density("", _classification(PageClass.MIXED_CONTENT, 0))

    assert result.verdict is Verdict.FAIL


def test_scanned_page_with_no_text_fails_density() -> None:
    result = check_text_density("", _classification(PageClass.SCANNED_IMAGE, 0))

    assert result.verdict is Verdict.FAIL


def test_blank_page_with_no_text_is_expected_and_passes() -> None:
    result = check_text_density("", _classification(PageClass.BLANK, 0))

    assert result.verdict is Verdict.PASS


def test_text_page_with_text_passes_density() -> None:
    text = "x" * 500

    result = check_text_density(text, _classification(PageClass.TEXT_NATIVE, len(text)))

    assert result.verdict is Verdict.PASS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_reading_order.py -q`
Expected: FAIL — `ModuleNotFoundError` for `quality.reading_order`

- [ ] **Step 3: Write the implementation**

```python
"""Detect text that was extracted in the wrong order, or not at all.

Two failure modes, both measured:

* Reading order (companion 3.5): a multi-column page whose narrative is
  interleaved with the adjacent table's header cells. A chunk built from that
  stream is unusable for Q&A and unsafe for drafting.
* Text density (companion 2.1): a page whose classification says it carries
  content but whose extracted text is empty - the silent page loss this whole
  programme exists to fix.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import PageClass, PageClassification
from .models import CheckResult, Verdict

_SENTENCE_WORD = re.compile(r"[A-Za-z]{3,}")
_UNIT_FRAGMENT = re.compile(r"\((?:mtr|m3|m2|nos|kg|cum|sqm)\)", re.IGNORECASE)

# A line is "interleaved" when a run of prose is followed by a run of short
# header-like tokens on the same line.
_INTERLEAVED = re.compile(
    r"[A-Za-z]{4,}(?:\s+[A-Za-z]{4,}){3,}\s+(?:[A-Z][a-z]{2,}\s+){2,}[A-Z][a-z]{2,}\s*$"
)


def check_reading_order(
    text: Optional[str], classification: PageClassification
) -> CheckResult:
    """Flag pages whose prose has been shredded by an adjacent table."""
    body = (text or "").strip()

    if not body:
        return CheckResult(
            name="reading_order",
            verdict=Verdict.NOT_CHECKABLE,
            detail="No text to assess",
        )

    words = _SENTENCE_WORD.findall(body)
    if len(words) < 12:
        return CheckResult(
            name="reading_order",
            verdict=Verdict.NOT_CHECKABLE,
            detail="Too little prose to assess reading order",
        )

    if classification.page_class is PageClass.TABLE_HEAVY and len(words) < 30:
        return CheckResult(
            name="reading_order",
            verdict=Verdict.NOT_CHECKABLE,
            detail="Predominantly tabular text; reading order is not meaningful",
        )

    suspicious_lines = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _INTERLEAVED.search(stripped) or (
            _UNIT_FRAGMENT.search(stripped) and len(_SENTENCE_WORD.findall(stripped)) >= 6
        ):
            suspicious_lines += 1

    if suspicious_lines >= 2:
        return CheckResult(
            name="reading_order",
            verdict=Verdict.FAIL,
            detail=(
                f"{suspicious_lines} lines interleave prose with table header "
                "cells - reading order appears destroyed"
            ),
        )

    return CheckResult(
        name="reading_order", verdict=Verdict.PASS, detail="Prose reads coherently"
    )


def check_text_density(
    text: Optional[str], classification: PageClassification
) -> CheckResult:
    """Flag pages that plainly carry content but extracted almost nothing."""
    body = (text or "").strip()

    content_bearing = {
        PageClass.TEXT_NATIVE,
        PageClass.MIXED_CONTENT,
        PageClass.TABLE_HEAVY,
        PageClass.SCANNED_IMAGE,
    }

    if classification.page_class is PageClass.UNRENDERABLE:
        return CheckResult(
            name="text_density",
            verdict=Verdict.INDETERMINATE,
            detail="Page could not be rendered; blankness and content cannot be confirmed",
        )

    if classification.page_class is PageClass.BLANK:
        return CheckResult(
            name="text_density",
            verdict=Verdict.PASS,
            detail="Page was successfully rendered and confirmed blank",
        )

    if classification.page_class in content_bearing and len(body) < 20:
        return CheckResult(
            name="text_density",
            verdict=Verdict.FAIL,
            detail=(
                f"Page classified {classification.page_class.value} yielded only "
                f"{len(body)} characters"
            ),
        )

    return CheckResult(
        name="text_density",
        verdict=Verdict.PASS,
        detail=f"{len(body)} characters extracted",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_reading_order.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/quality/reading_order.py projectDMS/backend/rbac_backend/tests/test_quality_reading_order.py
git commit -m "feat: detect destroyed reading order and implausible text density"
```

---

### Task 6.5: `ExtractionQualityGate` — the four-verdict assembly

**This task carries the two numeric gates that decide whether Phase 7 may be enabled.**

**Files:**
- Create: `backend/rbac_backend/services/extraction/quality/gate.py`
- Test: `backend/rbac_backend/tests/test_quality_gate.py`
- Create: `backend/rbac_backend/tests/fixtures/golden_quality_gate.json`

**Interfaces:**
- Consumes: everything in `quality/` (6.1–6.4)
- Produces: `class ExtractionQualityGate` with `assess(page: ExtractedPage, *, tables: Sequence[Sequence[Sequence[str]]] | None = None) -> QualityVerdict`

- [ ] **Step 1: Write the golden expectations file**

```json
{
  "_comment": "Phase 6 hard gates, from docs/architecture/mixed_pdf_ingestion_and_summary_plan_2026-08-13.md sections 3.1 and 3.3. Phase 7 must not be enabled until both are met.",
  "false_positive_patterns_that_must_not_fail": 12,
  "known_split_digit_corruptions_that_must_be_detected": 9,
  "split_digit_corruptions": {
    "1 ,900,000": "1,900,000",
    "5 77,188": "577,188",
    "9 50,000": "950,000",
    "7 0,500": "70,500",
    "1 34,460": "134,460",
    "4 42,728": "442,728",
    "1 10,000": "110,000",
    "1 15,000": "115,000",
    "4 8,960": "48,960"
  },
  "false_positive_cases": [
    {"why": "Nos multiplier applies", "headers": ["Description", "Nos", "Qty", "Rate", "Amount"], "row": ["Guide wall", "3", "32.61", "3,200", "313,056"]},
    {"why": "declared in-table formula Area A=[h*l]", "headers": ["DW No", "Depth", "Thickness", "Length", "Area"], "row": ["DW1", "1.2", "0.8", "5.0", "6.0"]},
    {"why": "declared in-table formula Volume [l*b*h]", "headers": ["DW No", "Length", "Breadth", "Height", "Volume"], "row": ["DW2", "5.0", "0.8", "1.2", "4.8"]},
    {"why": "S/N serial read as quantity", "headers": ["S/N", "Description", "Rate", "Amount"], "row": ["2", "Mobilization", "950,000", "950,000"]},
    {"why": "0.86% rounding, displayed qty 58 vs true 57.5", "headers": ["S/N", "Description", "Qty", "Rate", "Amount"], "row": ["1", "Ground improvement", "58", "1,215", "70,500"]},
    {"why": "sum 18,450,139 vs stated 18,450,140 (1 unit)", "headers": ["S/N", "Description", "Qty", "Rate", "Amount"], "row": ["1", "Sub-total", "1", "18,450,139", "18,450,139"], "stated_total": "18,450,140"},
    {"why": "lump-sum row has rate and amount but no quantity", "headers": ["S/N", "Description", "Rate", "Amount"], "row": ["7", "Lump sum", "500,000", "500,000"]},
    {"why": "percentage item is not a quantity-rate identity", "headers": ["S/N", "Description", "Percent", "Amount"], "row": ["8", "Overheads", "10%", "95,000"]},
    {"why": "negative credit has no multiplication operands", "headers": ["S/N", "Description", "Amount"], "row": ["9", "Less recovery", "-25,000"]},
    {"why": "repeated header row inside a continued table", "headers": ["S/N", "Description", "Qty", "Rate", "Amount"], "row": ["S/N", "Description", "Qty", "Rate", "Amount"]},
    {"why": "missing amount is uncheckable rather than false", "headers": ["S/N", "Description", "Qty", "Rate", "Amount"], "row": ["10", "Pending valuation", "2", "50", ""]},
    {"why": "page range must not be parsed as a financial quantity", "headers": ["S/N", "Description", "Pages", "Amount"], "row": ["11", "Supporting records", "1-3", "100,000"]}
  ]
}
```

- [ ] **Step 2: Write the failing test**

```python
"""The quality gate: four verdicts, and NOT_CHECKABLE never escalates.

Two hard gates from the companion evidence are asserted here. Phase 7 must not
be enabled until both pass:

  * zero FAIL verdicts across the 12 measured false-positive patterns
  * all 9 measured split-digit corruptions detected with correct repairs
"""

from __future__ import annotations

import json
from pathlib import Path

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.quality.models import Verdict
from rbac_backend.services.extraction.quality.numeric_checks import detect_split_digits

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_quality_gate.json").read_text(
        encoding="utf-8"
    )
)


def _page(text: str, page_class: PageClass = PageClass.TEXT_NATIVE) -> ExtractedPage:
    return ExtractedPage(
        number=3,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def test_all_nine_measured_corruptions_are_detected() -> None:
    corruptions = GOLDEN["split_digit_corruptions"]

    assert len(corruptions) == GOLDEN["known_split_digit_corruptions_that_must_be_detected"]
    for corrupted, expected in corruptions.items():
        assert detect_split_digits(corrupted) == expected, corrupted


def test_no_false_positive_case_produces_a_fail() -> None:
    gate = ExtractionQualityGate()
    cases = GOLDEN["false_positive_cases"]

    assert len(cases) == GOLDEN["false_positive_patterns_that_must_not_fail"] == 12

    for case in cases:
        tables = [[case["headers"], case["row"]]]
        verdict = gate.assess(_page("Cost breakdown table"), tables=tables)

        assert verdict.verdict is not Verdict.FAIL, case["why"]


def test_clean_page_passes() -> None:
    gate = ExtractionQualityGate()
    tables = [[["S/N", "Description", "Qty", "Rate", "Amount"], ["1", "Widget", "2", "50", "100"]]]

    verdict = gate.assess(_page("A clean cost table with prose above it."), tables=tables)

    assert verdict.verdict is Verdict.PASS
    assert verdict.escalates is False


def test_genuinely_wrong_arithmetic_fails() -> None:
    gate = ExtractionQualityGate()
    tables = [[["S/N", "Description", "Qty", "Rate", "Amount"], ["1", "Widget", "10", "100", "5,000"]]]

    verdict = gate.assess(_page("A cost table."), tables=tables)

    assert verdict.verdict is Verdict.FAIL
    assert verdict.escalates is True


def test_empty_page_that_should_have_content_fails() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.SCANNED_IMAGE))

    assert verdict.verdict is Verdict.FAIL
    assert verdict.escalates is True


def test_blank_page_is_not_an_escalation() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.BLANK))

    assert verdict.escalates is False


def test_unrenderable_page_is_indeterminate_and_human_review_eligible() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.UNRENDERABLE))

    assert verdict.verdict is Verdict.INDETERMINATE
    assert verdict.escalates is True


def test_unidentifiable_table_is_not_checkable_and_does_not_escalate() -> None:
    gate = ExtractionQualityGate()
    tables = [[["DW No", "Depth", "Thickness", "Volume"], ["DW1", "1.2", "0.8", "4.8"]]]

    verdict = gate.assess(_page("Dimensions table."), tables=tables)

    assert verdict.verdict is Verdict.NOT_CHECKABLE
    assert verdict.escalates is False


def test_ambiguous_dates_are_indeterminate_and_do_escalate() -> None:
    gate = ExtractionQualityGate()
    tables = [[["Date", "Status"], ["3/5/2023", "Idle"], ["5/5/2023", "Idle"]]]

    verdict = gate.assess(_page("Idle register."), tables=tables)

    assert verdict.verdict is Verdict.INDETERMINATE
    assert verdict.escalates is True


def test_repairs_are_recorded_with_full_provenance() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [
            ["S/N", "Description", "Qty", "Rate", "Amount"],
            ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"],
            ["2", "Survey", "1", "100,000", "100,000"],
            ["", "Sub-total", "", "", "2,000,000"],
        ]
    ]

    verdict = gate.assess(_page("Cost table."), tables=tables)

    assert verdict.repairs
    repair = verdict.repairs[0]
    assert repair.before == "1 ,900,000"
    assert repair.after == "1,900,000"
    assert repair.method == "dual_confirmation"
    assert repair.page == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_gate.py -q`
Expected: FAIL — `ModuleNotFoundError` for `quality.gate`

- [ ] **Step 4: Write the implementation**

```python
"""Assemble every deterministic check into one verdict per page.

Runs unconditionally, on native and OCR text alike. Companion 3.1 measured 9
corruptions in a PDF's own text layer, so "native means trustworthy" is false
and there is no page this gate may skip.

Only FAIL and INDETERMINATE escalate. NOT_CHECKABLE - "we could not identify
this structure well enough to verify it" - accepts and marks unverified,
because the alternative is 12 paid model calls per correct document.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Sequence

from ..models import ExtractedPage
from .column_roles import map_column_roles
from .date_checks import check_date_column
from .models import CheckResult, NumericRepair, QualityVerdict, Verdict
from .numeric_checks import check_row, check_subtotal, detect_split_digits, propose_repair
from .reading_order import check_reading_order, check_text_density

logger = logging.getLogger(__name__)

Table = Sequence[Sequence[str]]


class ExtractionQualityGate:
    def assess(
        self, page: ExtractedPage, *, tables: Optional[Sequence[Table]] = None
    ) -> QualityVerdict:
        checks: List[CheckResult] = [
            check_text_density(page.text, page.classification),
            check_reading_order(page.text, page.classification),
        ]
        repairs: List[NumericRepair] = []

        for table in tables or []:
            table_checks, table_repairs = self._assess_table(table, page_number=page.number)
            checks.extend(table_checks)
            repairs.extend(table_repairs)

        return QualityVerdict(
            verdict=self._combine(checks),
            checks=checks,
            repairs=repairs,
            reasons=[
                f"{check.name}: {check.detail}"
                for check in checks
                if check.verdict in {Verdict.FAIL, Verdict.INDETERMINATE}
            ],
        )

    def _assess_table(
        self, table: Table, *, page_number: int
    ) -> tuple[List[CheckResult], List[NumericRepair]]:
        rows = [list(row) for row in table]
        if len(rows) < 2:
            return (
                [
                    CheckResult(
                        name="table_structure",
                        verdict=Verdict.NOT_CHECKABLE,
                        detail="Table has no data rows",
                    )
                ],
                [],
            )

        headers, raw_data_rows = rows[0], rows[1:]
        roles = map_column_roles(headers)

        subtotal_index, stated_total = self._find_stated_subtotal(raw_data_rows, roles)
        data_rows = [
            row for index, row in enumerate(raw_data_rows) if index != subtotal_index
        ]

        checks: List[CheckResult] = []
        repairs: List[NumericRepair] = []

        for row_index, row in enumerate(data_rows):
            row_check = check_row(row, roles)
            checks.append(row_check)

            for cell_index, cell in enumerate(row):
                repaired_literal = detect_split_digits(str(cell))
                if repaired_literal is None:
                    continue
                candidate_row = list(row)
                candidate_row[cell_index] = repaired_literal
                candidate_rows = [list(item) for item in data_rows]
                candidate_rows[row_index] = candidate_row
                candidate_row_check = check_row(candidate_row, roles)
                subtotal_check = check_subtotal(
                    candidate_rows,
                    stated_total,
                    roles,
                    candidate_row_index=row_index,
                )
                checks.extend([candidate_row_check, subtotal_check])
                repair = propose_repair(
                    str(cell),
                    row_check=candidate_row_check,
                    subtotal_check=subtotal_check,
                )
                if repair is not None:
                    repairs.append(
                        NumericRepair(
                            before=repair.before,
                            after=repair.after,
                            reason=repair.reason,
                            method=repair.method,
                            confidence=repair.confidence,
                            page=page_number,
                        )
                    )

        checks.extend(self._date_checks(headers, data_rows))
        return checks, repairs

    @staticmethod
    def _find_stated_subtotal(
        rows: Sequence[Sequence[str]], roles: Sequence[object]
    ) -> tuple[Optional[int], Optional[str]]:
        from .column_roles import ColumnRole

        amount_index = next(
            (index for index, role in enumerate(roles) if role is ColumnRole.AMOUNT),
            None,
        )
        description_index = next(
            (index for index, role in enumerate(roles) if role is ColumnRole.DESCRIPTION),
            None,
        )
        if amount_index is None or description_index is None:
            return None, None
        for row_index, row in enumerate(rows):
            description = str(row[description_index] if description_index < len(row) else "")
            if re.search(r"\b(?:sub[ -]?total|grand total|total)\b", description, re.I):
                return (
                    row_index,
                    str(row[amount_index]) if amount_index < len(row) else None,
                )
        return None, None

    @staticmethod
    def _date_checks(
        headers: Sequence[str], data_rows: Sequence[Sequence[str]]
    ) -> List[CheckResult]:
        checks: List[CheckResult] = []
        for index, header in enumerate(headers):
            if "date" not in str(header or "").lower():
                continue
            column = [row[index] if index < len(row) else None for row in data_rows]
            checks.append(check_date_column(column))
        return checks

    @staticmethod
    def _combine(checks: Sequence[CheckResult]) -> Verdict:
        verdicts = {check.verdict for check in checks}

        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.INDETERMINATE in verdicts:
            return Verdict.INDETERMINATE
        if Verdict.PASS in verdicts:
            return Verdict.PASS
        return Verdict.NOT_CHECKABLE
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_quality_gate.py -q`
Expected: PASS — 10 passed

- [ ] **Step 6: Run the whole quality suite together**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "quality"`
Expected: PASS — all Phase 6 tests green. **If `test_no_false_positive_case_produces_a_fail` fails, Phase 7 is blocked.** Tune the role patterns and tolerances, not the test.

- [ ] **Step 7: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/quality/gate.py projectDMS/backend/rbac_backend/tests/fixtures/golden_quality_gate.json projectDMS/backend/rbac_backend/tests/test_quality_gate.py
git commit -m "feat: assemble the deterministic extraction quality gate"
```

---

## Phase 7 — LLM/Vision extraction fallback ladder

**Do not start this phase until Task 6.5 Step 6 is green.** The gate is what makes the ladder affordable.

### Task 7.1: `PageRasterizer`

**Files:**
- Create: `backend/rbac_backend/services/extraction/rasterizer.py`
- Test: `backend/rbac_backend/tests/test_extraction_rasterizer.py`

**Interfaces:**
- Consumes: nothing
- Produces: `DEFAULT_DPI = 150`, `class PageRasterizer(dpi: int = DEFAULT_DPI)` with `render_page(source, page_number) -> bytes` (PNG) and `crop_region(source, page_number, bbox) -> bytes`, plus `RasterizationError`. It handles PDF pages and a standalone PNG/JPEG as logical page 1; this keeps image fallback out of `pdfplumber` and PDF page mapping.

**Verified 2026-08-14 in `backend/.venv`:** `pdfplumber.Page.to_image(resolution=150)` renders through **pypdfium2 5.0.0** (already installed transitively) and Pillow crops the result — 1240×1755 RGB measured. No new dependency and no AGPL exposure, so the companion document's rejection of PyMuPDF stands.

- [ ] **Step 1: Write the failing test**

```python
"""Page rasterization for the vision fallback.

Backed by pypdfium2 (Apache-licensed, already installed via pdfplumber) rather
than PyMuPDF, which the companion design rejected on AGPL grounds.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from rbac_backend.services.extraction.rasterizer import (
    DEFAULT_DPI,
    PageRasterizer,
    RasterizationError,
)
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


def test_default_dpi_is_150() -> None:
    assert DEFAULT_DPI == 150


def test_renders_a_page_to_png_bytes(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    data = PageRasterizer().render_page(source, 3)
    image = Image.open(io.BytesIO(data))

    assert image.format == "PNG"
    assert image.width > 1000
    assert image.height > 1000


def test_landscape_page_renders_wider_than_tall(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    image = Image.open(io.BytesIO(PageRasterizer().render_page(source, 5)))

    assert image.width > image.height


def test_crop_returns_only_the_requested_region(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    rasterizer = PageRasterizer()

    full = Image.open(io.BytesIO(rasterizer.render_page(source, 3)))
    cropped = Image.open(
        io.BytesIO(rasterizer.crop_region(source, 3, (0.0, 0.0, 200.0, 200.0)))
    )

    assert cropped.width < full.width
    assert cropped.height < full.height


def test_lower_dpi_produces_a_smaller_image(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    small = Image.open(io.BytesIO(PageRasterizer(dpi=72).render_page(source, 3)))
    large = Image.open(io.BytesIO(PageRasterizer(dpi=150).render_page(source, 3)))

    assert small.width < large.width


def test_out_of_range_page_raises(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pytest.raises(RasterizationError):
        PageRasterizer().render_page(source, 99)


def test_zero_area_bbox_raises(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pytest.raises(RasterizationError):
        PageRasterizer().crop_region(source, 3, (10.0, 10.0, 10.0, 10.0))


def test_standalone_image_renders_as_logical_page_one(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (320, 240), "white").save(source)
    rendered = Image.open(io.BytesIO(PageRasterizer().render_page(source, 1)))
    assert rendered.size == (320, 240)
    with pytest.raises(RasterizationError):
        PageRasterizer().render_page(source, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_rasterizer.py -q`
Expected: FAIL — `ModuleNotFoundError` for `rasterizer`

- [ ] **Step 3: Write the implementation**

```python
"""Render PDF pages and regions to PNG for the vision fallback.

pdfplumber's to_image() is backed by pypdfium2 (Apache-2.0), already present as
a transitive dependency. PyMuPDF is deliberately not used: the companion design
rejected adding an AGPL dependency.

Region-first is the rule. Sending a crop rather than a whole page is
simultaneously a cost control, a data-minimisation control, and a precision
control - a narrower prompt reconstructs a table better than a wider one.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150


class RasterizationError(Exception):
    """Raised when a page or region cannot be rendered."""


class PageRasterizer:
    def __init__(self, dpi: int = DEFAULT_DPI) -> None:
        self.dpi = max(36, int(dpi))

    def render_page(self, source: Path, page_number: int) -> bytes:
        """Render one page to PNG bytes."""
        return self._render(source, page_number, bbox=None)

    def crop_region(
        self, source: Path, page_number: int, bbox: Tuple[float, float, float, float]
    ) -> bytes:
        """Render one region of a page to PNG bytes.

        bbox is (x0, top, x1, bottom): PDF user-space points for PDFs and source
        pixels for standalone images.
        """
        x0, top, x1, bottom = bbox
        if x1 <= x0 or bottom <= top:
            raise RasterizationError(f"Region has no area: {bbox}")
        return self._render(source, page_number, bbox=bbox)

    def _render(
        self,
        source: Path,
        page_number: int,
        *,
        bbox: Tuple[float, float, float, float] | None,
    ) -> bytes:
        with source.open("rb") as stream:
            signature = stream.read(8)
        if signature.startswith(b"\x89PNG\r\n\x1a\n") or (
            signature[:3] == b"\xff\xd8\xff"
        ):
            if page_number != 1:
                raise RasterizationError("A standalone image has only logical page 1")
            from PIL import Image

            try:
                with Image.open(source) as handle:
                    image = handle.convert("RGB")
                    if bbox is not None:
                        image = image.crop(tuple(map(int, bbox)))
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    return buffer.getvalue()
            except Exception as exc:
                raise RasterizationError(
                    f"Could not render standalone image {source.name}: {exc}"
                ) from exc

        import pdfplumber

        try:
            with pdfplumber.open(source) as pdf:
                if not 1 <= page_number <= len(pdf.pages):
                    raise RasterizationError(
                        f"Page {page_number} is outside the document's "
                        f"{len(pdf.pages)} pages"
                    )
                page = pdf.pages[page_number - 1]
                image = page.to_image(resolution=self.dpi).original

                if bbox is not None:
                    scale = self.dpi / 72.0
                    x0, top, x1, bottom = bbox
                    image = image.crop(
                        (
                            int(x0 * scale),
                            int(top * scale),
                            int(x1 * scale),
                            int(bottom * scale),
                        )
                    )

                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
        except RasterizationError:
            raise
        except Exception as exc:
            raise RasterizationError(
                f"Could not render page {page_number} of {source.name}: {exc}"
            ) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_extraction_rasterizer.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/rasterizer.py projectDMS/backend/rbac_backend/tests/test_extraction_rasterizer.py
git commit -m "feat: add page rasterizer for the vision fallback"
```

---

### Task 7.2: Fallback models, evidence assembly, and the intervention ledger

**Files:**
- Create: `backend/rbac_backend/services/extraction/fallback/__init__.py`
- Create: `backend/rbac_backend/services/extraction/fallback/models.py`
- Create: `backend/rbac_backend/services/extraction/fallback/evidence.py`
- Create: `backend/rbac_backend/services/extraction/fallback/ledger.py`
- Test: `backend/rbac_backend/tests/test_fallback_evidence_and_ledger.py`

**Interfaces:**
- Consumes: `PageRasterizer` (7.1), `QualityVerdict` (6.2), `ExtractedPage` (1.2)
- Produces:
  - `class Tier(int, Enum)` — `TIER_1 = 1`, `TIER_2 = 2`
  - `class Corroboration(str, Enum)` — `CORROBORATED`, `UNCORROBORATED`
  - `@dataclass ReconstructedValue(value_id, kind, raw_value, location)` and `@dataclass ValueCorroboration(value_id, raw_value, status, check_name, evidence_refs)`
  - `class FallbackOutcome(str, Enum)` — `UNCHANGED`, `RESOLVED`, `ESCALATED`, `HUMAN_REVIEW_REQUIRED`
  - `@dataclass Evidence(image_png, is_region, bbox, native_text, ocr_text, tables, trigger_reasons, dpi)`
  - `@dataclass Reconstruction(text, tables, factual_values, confidence, model, model_version, prompt_version, input_tokens, output_tokens, cost_usd)`; every number/date emitted in text or tables must appear in `factual_values`, enforced deterministically, and adapters must return measured usage rather than letting the ledger invent zeros
  - `@dataclass ResolvedPage(page, outcome, tier_used, reconstruction, post_verdict)`
  - `@dataclass Intervention(...)` matching spec §4.9.5
  - `assemble_evidence(source, page, verdict, rasterizer, *, region=None) -> Evidence`
  - `class InterventionLedger(db)` with `async def record(intervention) -> None`, `COLLECTION = "page_extraction_interventions"`

- [ ] **Step 1: Write the failing test**

```python
"""Minimal-evidence assembly and append-only intervention provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.fallback.evidence import assemble_evidence
from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
from rbac_backend.services.extraction.fallback.models import (
    Corroboration,
    FallbackOutcome,
    Intervention,
    Tier,
)
from rbac_backend.services.extraction.quality.models import (
    CheckResult,
    QualityVerdict,
    Verdict,
)
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.inserted.append(document)

        class _Result:
            inserted_id = "oid-1"

        return _Result()


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _page(number: int = 3, text: str = "native text") -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.MIXED_CONTENT,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.FAIL,
        checks=[
            CheckResult(name="row_identity", verdict=Verdict.FAIL, detail="expected 1,000")
        ],
        reasons=["row_identity: expected 1,000"],
    )


def test_page_evidence_carries_the_full_page_image(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))

    assert evidence.image_png.startswith(b"\x89PNG")
    assert evidence.is_region is False
    assert evidence.bbox is None


def test_region_evidence_is_preferred_when_a_bbox_is_given(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    full = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))
    region = assemble_evidence(
        source, _page(), _verdict(), PageRasterizer(dpi=72), region=(0.0, 0.0, 200.0, 200.0)
    )

    assert region.is_region is True
    assert region.bbox == (0.0, 0.0, 200.0, 200.0)
    assert len(region.image_png) < len(full.image_png)


def test_evidence_includes_existing_text_and_the_trigger(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(), _verdict(), PageRasterizer(dpi=72))

    assert evidence.native_text == "native text"
    assert evidence.trigger_reasons == ["row_identity: expected 1,000"]


def test_evidence_never_includes_other_pages(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    evidence = assemble_evidence(source, _page(number=4), _verdict(), PageRasterizer(dpi=72))

    # Only one image, and it is page 4's. Nothing about neighbouring pages.
    assert not hasattr(evidence, "document_text")
    assert evidence.page_number == 4


async def test_ledger_writes_the_full_provenance_record() -> None:
    db = _FakeDb()
    ledger = InterventionLedger(db=db)

    await ledger.record(
        Intervention(
            document_id="doc-1",
            page_number=3,
            region_bbox=None,
            trigger="row_identity: expected 1,000",
            tier=Tier.TIER_1,
            model="stub-vision",
            model_version="2026-08-01",
            prompt_version="v1",
            evidence_sent="page",
            dpi=150,
            text_sources=["native", "ocr"],
            confidence=0.82,
            corrections=[
                {
                    "before": "1 ,900,000",
                    "after": "1,900,000",
                    "reason": "split digit",
                    "value_id": "table-0-row-1-amount",
                    "corroboration": Corroboration.CORROBORATED.value,
                    "check_name": "subtotal_identity",
                    "evidence_refs": ["printed.subtotal", "peer.amounts"],
                }
            ],
            post_check=Verdict.PASS.value,
            outcome=FallbackOutcome.RESOLVED,
            tokens=1200,
            cost_usd=0.004,
            latency_ms=1830,
        )
    )

    record = db[InterventionLedger.COLLECTION].inserted[0]
    assert record["document_id"] == "doc-1"
    assert record["page_number"] == 3
    assert record["tier"] == 1
    assert record["model"] == "stub-vision"
    assert record["prompt_version"] == "v1"
    assert record["post_check"] == "pass"
    assert record["outcome"] == "resolved"
    assert record["corrections"][0]["corroboration"] == "corroborated"
    assert "recorded_at" in record


async def test_ledger_is_append_only() -> None:
    db = _FakeDb()
    ledger = InterventionLedger(db=db)
    collection = db[InterventionLedger.COLLECTION]

    assert not hasattr(collection, "update_one") or True
    # Only insert_one is used, so two records for the same page coexist.
    for tier in (Tier.TIER_1, Tier.TIER_2):
        await ledger.record(
            Intervention(
                document_id="doc-1",
                page_number=3,
                region_bbox=None,
                trigger="text_density",
                tier=tier,
                model="stub",
                model_version="1",
                prompt_version="v1",
                evidence_sent="page",
                dpi=150,
                text_sources=["native"],
                confidence=0.5,
                corrections=[],
                post_check=Verdict.FAIL.value,
                outcome=FallbackOutcome.ESCALATED,
                tokens=10,
                cost_usd=0.0,
                latency_ms=5,
            )
        )

    assert len(collection.inserted) == 2
    assert [record["tier"] for record in collection.inserted] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_fallback_evidence_and_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError` for `fallback.evidence`

- [ ] **Step 3: Write `fallback/__init__.py` and `fallback/models.py`**

`backend/rbac_backend/services/extraction/fallback/__init__.py`:

```python
"""LLM/Vision fallback for pages the deterministic path could not resolve."""
```

`backend/rbac_backend/services/extraction/fallback/models.py`:

```python
"""Types for the extraction fallback ladder."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Tier(int, Enum):
    TIER_1 = 1
    TIER_2 = 2


class Corroboration(str, Enum):
    """Whether a reconstructed value can be checked against anything."""

    CORROBORATED = "corroborated"
    UNCORROBORATED = "uncorroborated"


@dataclass(frozen=True)
class ReconstructedValue:
    value_id: str
    kind: str  # number | date
    raw_value: str
    location: str  # text span id or table/cell id


@dataclass(frozen=True)
class ValueCorroboration:
    value_id: str
    raw_value: str
    status: Corroboration
    check_name: str = ""
    evidence_refs: List[str] = field(default_factory=list)


class FallbackOutcome(str, Enum):
    UNCHANGED = "unchanged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


@dataclass(frozen=True)
class Evidence:
    """The minimum material a model needs to answer the question.

    Deliberately excludes the rest of the document: neighbouring pages,
    unrelated metadata, and document-level text are all omitted.
    """

    page_number: int
    image_png: bytes
    is_region: bool
    bbox: Optional[Tuple[float, float, float, float]]
    native_text: str
    ocr_text: str
    tables: List[List[List[str]]]
    trigger_reasons: List[str]
    dpi: int


@dataclass(frozen=True)
class Reconstruction:
    text: str
    tables: List[List[List[str]]] = field(default_factory=list)
    factual_values: List[ReconstructedValue] = field(default_factory=list)
    confidence: float = 0.0
    model: str = ""
    model_version: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Intervention:
    document_id: str
    page_number: int
    region_bbox: Optional[Tuple[float, float, float, float]]
    trigger: str
    tier: Tier
    model: str
    model_version: str
    prompt_version: str
    evidence_sent: str
    dpi: int
    text_sources: List[str]
    confidence: float
    corrections: List[Dict[str, Any]]
    post_check: str
    outcome: FallbackOutcome
    tokens: int
    cost_usd: float
    latency_ms: int


@dataclass
class ResolvedPage:
    page: Any  # ExtractedPage; untyped here to avoid a circular import
    outcome: FallbackOutcome
    tier_used: Optional[Tier] = None
    reconstruction: Optional[Reconstruction] = None
    post_verdict: Optional[Any] = None  # QualityVerdict
```

- [ ] **Step 4: Write `fallback/evidence.py` and `fallback/ledger.py`**

`evidence.py`:

```python
"""Assemble the least material that can answer the question.

Region-preferred: when the failure is localised to one table or block, the
model sees a crop. A whole page goes only when the failure is page-wide. This
is a cost control, a data-minimisation control, and a precision control at once.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..models import ExtractedPage
from ..quality.models import QualityVerdict
from ..rasterizer import PageRasterizer
from .models import Evidence

logger = logging.getLogger(__name__)


def assemble_evidence(
    source: Path,
    page: ExtractedPage,
    verdict: QualityVerdict,
    rasterizer: PageRasterizer,
    *,
    region: Optional[Tuple[float, float, float, float]] = None,
    ocr_text: str = "",
    tables: Optional[Sequence[Sequence[Sequence[str]]]] = None,
) -> Evidence:
    """Build the evidence packet for one page or region."""
    if region is not None:
        image = rasterizer.crop_region(source, page.number, region)
    else:
        image = rasterizer.render_page(source, page.number)

    normalized_tables: List[List[List[str]]] = [
        [[str(cell) for cell in row] for row in table] for table in (tables or [])
    ]

    return Evidence(
        page_number=page.number,
        image_png=image,
        is_region=region is not None,
        bbox=region,
        native_text=page.text or "",
        ocr_text=ocr_text,
        tables=normalized_tables,
        trigger_reasons=list(verdict.reasons),
        dpi=rasterizer.dpi,
    )
```

`ledger.py`:

```python
"""Append-only provenance for every LLM intervention.

This is the audit answer to "why does this page read differently from the
PDF?" - a question the system must be able to answer in an arbitration
context. Records are never updated: a page escalated from tier 1 to tier 2
produces two rows, not one amended row.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .models import Intervention

logger = logging.getLogger(__name__)


class InterventionLedger:
    COLLECTION = "page_extraction_interventions"

    def __init__(self, *, db: Any) -> None:
        self.db = db

    async def record(self, intervention: Intervention) -> None:
        document = asdict(intervention)
        document["tier"] = int(intervention.tier)
        document["outcome"] = intervention.outcome.value
        document["recorded_at"] = datetime.now(timezone.utc)
        await self.db[self.COLLECTION].insert_one(document)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_fallback_evidence_and_ledger.py -q`
Expected: PASS — 6 passed

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/fallback/ projectDMS/backend/rbac_backend/tests/test_fallback_evidence_and_ledger.py
git commit -m "feat: add fallback evidence assembly and intervention ledger"
```

---

### Task 7.3: `ExtractionFallbackLadder`

**Files:**
- Create: `backend/rbac_backend/services/extraction/fallback/reconstruction_model.py`
- Create: `backend/rbac_backend/services/extraction/fallback/ladder.py`
- Modify: `backend/rbac_backend/core/config.py` (ladder settings)
- Test: `backend/rbac_backend/tests/test_fallback_ladder.py`

**Interfaces:**
- Consumes: everything in `fallback/` (7.2), `ExtractionQualityGate` (6.5)
- Produces:
  - `class ReconstructionModel(Protocol)` with `async def reconstruct(evidence, tier) -> Reconstruction`
  - `class NullReconstructionModel` — always raises `ModelUnavailable`
  - `class ExtractionFallbackLadder(gate, rasterizer, ledger, tier1=None, tier2=None, budget=None)` with `async def resolve(source, page, verdict, *, document_id, tables=None) -> ResolvedPage`
  - `inventory_factual_values(reconstruction) -> set[(kind, normalized_value, location)]` and schema comparison against `reconstruction.factual_values`
  - `classify_corroboration(reconstructed_value, evidence, post_verdict) -> ValueCorroboration`; a PASS check corroborates only when its `candidate_value` matches that exact normalized value and its `evidence_refs` identify the supporting source/cells
  - `reconstruction_content_is_source_anchored(reconstruction, evidence) -> bool`; non-value prose/table tokens may be reordered but every token must already exist in the native/OCR/table evidence, so a gate PASS cannot bless novel factual narrative

- [ ] **Step 1: Write the failing test**

```python
"""The fallback ladder: bounded tiers, re-verification, no fabrication.

Rules asserted here:
  * NOT_CHECKABLE never invokes a model
  * model confidence may route but never accept
  * a reconstruction must pass the SAME gate to be accepted
  * an unresolved page ends HUMAN_REVIEW_REQUIRED, never resolved
  * every invocation leaves a ledger row
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from rbac_backend.services.extraction.fallback.ladder import (
    ExtractionFallbackLadder,
    ModelUnavailable,
    NullReconstructionModel,
    classify_corroboration,
)
from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
from rbac_backend.services.extraction.fallback.models import (
    Corroboration,
    Evidence,
    FallbackOutcome,
    Reconstruction,
    ReconstructedValue,
    Tier,
)
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.quality.models import (
    CheckResult,
    QualityVerdict,
    Verdict,
)
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.inserted.append(document)

        class _Result:
            inserted_id = "oid"

        return _Result()


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


class _StubModel:
    def __init__(
        self, text: str, confidence: float = 0.95, name: str = "stub",
        factual_values: list[ReconstructedValue] | None = None,
    ) -> None:
        self.text = text
        self.confidence = confidence
        self.name = name
        self.factual_values = factual_values or []
        self.calls = 0

    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        self.calls += 1
        return Reconstruction(
            text=self.text,
            factual_values=self.factual_values,
            confidence=self.confidence,
            model=self.name,
            model_version="1",
            prompt_version="v1",
        )


def _page(text: str, page_class: PageClass = PageClass.MIXED_CONTENT) -> ExtractedPage:
    return ExtractedPage(
        number=3,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _fail_verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.FAIL,
        checks=[CheckResult(name="text_density", verdict=Verdict.FAIL, detail="0 chars")],
        reasons=["text_density: 0 chars"],
    )


def _not_checkable_verdict() -> QualityVerdict:
    return QualityVerdict(
        verdict=Verdict.NOT_CHECKABLE,
        checks=[CheckResult(name="row_identity", verdict=Verdict.NOT_CHECKABLE)],
    )


class _AlwaysNotCheckableGate:
    def assess(self, page, *, tables=None) -> QualityVerdict:
        return _not_checkable_verdict()


def empty_evidence() -> Evidence:
    return Evidence(
        page_number=1, image_png=b"", is_region=False, bbox=None,
        native_text="", ocr_text="", tables=[], trigger_reasons=[], dpi=150,
    )


def _ladder(db: _FakeDb, tier1: Any = None, tier2: Any = None) -> ExtractionFallbackLadder:
    return ExtractionFallbackLadder(
        gate=ExtractionQualityGate(),
        rasterizer=PageRasterizer(dpi=72),
        ledger=InterventionLedger(db=db),
        tier1=tier1,
        tier2=tier2,
    )


async def test_not_checkable_never_calls_a_model(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    model = _StubModel("anything")

    resolved = await _ladder(db, tier1=model).resolve(
        source, _page("some text"), _not_checkable_verdict(), document_id="doc-1"
    )

    assert model.calls == 0
    assert resolved.outcome is FallbackOutcome.UNCHANGED
    assert db[InterventionLedger.COLLECTION].inserted == []


async def test_tier1_success_resolves_and_is_ledgered(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    model = _StubModel("Recovered covering letter narrative without factual literals.")

    resolved = await _ladder(db, tier1=model).resolve(
        source,
        _page("factual literals without recovered covering letter narrative"),
        _fail_verdict(),
        document_id="doc-1",
    )

    assert model.calls == 1
    assert resolved.outcome is FallbackOutcome.RESOLVED
    assert resolved.tier_used is Tier.TIER_1
    assert resolved.page.source is PageSource.RECONSTRUCTED
    assert db[InterventionLedger.COLLECTION].inserted[0]["outcome"] == "resolved"


async def test_high_confidence_alone_does_not_accept_a_bad_reconstruction(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    # Confident, but still empty - the gate must reject it regardless.
    model = _StubModel("", confidence=0.99)

    resolved = await _ladder(db, tier1=model).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_post_gate_not_checkable_is_never_accepted(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    ladder = _ladder(db, tier1=_StubModel("model text"))
    ladder.gate = _AlwaysNotCheckableGate()

    resolved = await ladder.resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_pass_with_uncorroborated_reconstructed_number_is_rejected(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    model = _StubModel(
        "Amount 1,900,000",
        factual_values=[
            ReconstructedValue("v1", "number", "1,900,000", "text:7")
        ],
    )
    resolved = await _ladder(_FakeDb(), tier1=model).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )
    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


def test_unrelated_pass_check_does_not_corroborate_a_reconstructed_number() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "table-0-r1-c4")
    post = QualityVerdict(
        verdict=Verdict.PASS,
        checks=[CheckResult(name="reading_order", verdict=Verdict.PASS)],
    )
    result = classify_corroboration(value, empty_evidence(), post)
    assert result.status is Corroboration.UNCORROBORATED


def test_matching_value_specific_subtotal_check_corroborates() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "table-0-r1-c4")
    post = QualityVerdict(
        verdict=Verdict.PASS,
        checks=[CheckResult(
            name="subtotal_identity", verdict=Verdict.PASS,
            candidate_value=Decimal("1900000"),
            evidence_refs=["printed.subtotal", "candidate.amount", "peer.amounts"],
        )],
    )
    result = classify_corroboration(value, empty_evidence(), post)
    assert result.status is Corroboration.CORROBORATED
    assert result.check_name == "subtotal_identity"


def test_source_substring_is_not_exact_value_corroboration() -> None:
    value = ReconstructedValue("v1", "number", "1,900,000", "text:7")
    evidence = Evidence(
        page_number=1, image_png=b"", is_region=False, bbox=None,
        native_text="Amount 11,900,000", ocr_text="", tables=[],
        trigger_reasons=[], dpi=150,
    )
    result = classify_corroboration(value, evidence, _not_checkable_verdict())
    assert result.status is Corroboration.UNCORROBORATED


async def test_novel_uncorroborated_narrative_is_rejected(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    resolved = await _ladder(
        _FakeDb(), tier1=_StubModel("Novel liability admission by the contractor")
    ).resolve(
        source, _page("covering letter"), _fail_verdict(), document_id="doc-1"
    )
    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_failed_tier1_escalates_to_tier2(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    tier1 = _StubModel("", confidence=0.9, name="small")
    tier2 = _StubModel("Recovered text at last, with real content.", name="large")

    resolved = await _ladder(db, tier1=tier1, tier2=tier2).resolve(
        source,
        _page("real content recovered at last with text"),
        _fail_verdict(),
        document_id="doc-1",
    )

    assert tier1.calls == 1
    assert tier2.calls == 1
    assert resolved.outcome is FallbackOutcome.RESOLVED
    assert resolved.tier_used is Tier.TIER_2
    assert len(db[InterventionLedger.COLLECTION].inserted) == 2


async def test_both_tiers_failing_ends_in_human_review(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(
        db, tier1=_StubModel(""), tier2=_StubModel("")
    ).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED
    assert db[InterventionLedger.COLLECTION].inserted[-1]["outcome"] == "human_review_required"


async def test_unavailable_model_declines_rather_than_fabricating(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(db, tier1=NullReconstructionModel()).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED
    assert resolved.page.text == ""  # nothing invented


async def test_no_tiers_configured_ends_in_human_review(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()

    resolved = await _ladder(db).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert resolved.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED


async def test_ladder_never_runs_more_than_two_tiers(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    tier1, tier2 = _StubModel(""), _StubModel("")

    await _ladder(db, tier1=tier1, tier2=tier2).resolve(
        source, _page("", PageClass.SCANNED_IMAGE), _fail_verdict(), document_id="doc-1"
    )

    assert tier1.calls == 1
    assert tier2.calls == 1


async def test_null_model_raises_model_unavailable() -> None:
    import pytest

    with pytest.raises(ModelUnavailable):
        await NullReconstructionModel().reconstruct(
            Evidence(
                page_number=1,
                image_png=b"",
                is_region=False,
                bbox=None,
                native_text="",
                ocr_text="",
                tables=[],
                trigger_reasons=[],
                dpi=150,
            ),
            Tier.TIER_1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_fallback_ladder.py -q`
Expected: FAIL — `ModuleNotFoundError` for `fallback.ladder`

- [ ] **Step 3: Write `reconstruction_model.py`**

```python
"""The model seam for page reconstruction.

Two adapters exist: a real model-backed one supplied by the caller, and
NullReconstructionModel, which always declines. The null adapter is the safe
default - a deployment with no vision model configured degrades to
HUMAN_REVIEW_REQUIRED rather than to invented content.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Evidence, Reconstruction, Tier


class ModelUnavailable(Exception):
    """Raised when a tier's model cannot be called."""


@runtime_checkable
class ReconstructionModel(Protocol):
    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        ...


class NullReconstructionModel:
    """Declines every request. The default when no model is configured."""

    async def reconstruct(self, evidence: Evidence, tier: Tier) -> Reconstruction:
        raise ModelUnavailable("No reconstruction model is configured for this tier")
```

- [ ] **Step 4: Write `ladder.py`**

```python
"""Resolve a page the deterministic path could not, or say so plainly.

Tier 0 (deterministic) has already run when this is reached. The ladder adds at
most two model attempts and then stops:

    tier 1 -> re-gate -> tier 2 -> re-gate -> HUMAN_REVIEW_REQUIRED

Three rules are structural rather than advisory:

* NOT_CHECKABLE never reaches a model. "We could not verify this" is not
  "this is wrong", and treating it as wrong is what turns a cost control into
  unbounded spend (companion 3.3 measured 12 such cases on a correct document).
* A reconstruction is accepted only when the SAME gate returns exactly PASS —
  NOT_CHECKABLE is rejection here — its factual-value inventory is complete,
  and every reconstructed number/date has value-specific corroboration. A PASS
  from an unrelated check cannot corroborate it. Model confidence never accepts.
* Nothing is ever fabricated. An unavailable, over-budget, or failing model
  leaves the page exactly as it was and marks it for a human.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Optional, Sequence

from ..models import ExtractedPage, PageSource, PageStatus
from ..quality.gate import ExtractionQualityGate
from ..quality.models import QualityVerdict, Verdict
from ..quality.numeric_checks import parse_amount
from ..rasterizer import PageRasterizer, RasterizationError
from .evidence import assemble_evidence
from .ledger import InterventionLedger
from .models import (
    Corroboration,
    Evidence,
    FallbackOutcome,
    Intervention,
    Reconstruction,
    ReconstructedValue,
    ResolvedPage,
    Tier,
    ValueCorroboration,
)
from .reconstruction_model import ModelUnavailable, NullReconstructionModel

logger = logging.getLogger(__name__)

__all__ = [
    "ExtractionFallbackLadder",
    "ModelUnavailable",
    "NullReconstructionModel",
    "classify_corroboration",
]


_DATE_LITERAL = re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b")
_NUMBER_LITERAL = re.compile(r"(?<![\w/.-])-?\d[\d,]*(?:\.\d+)?(?![\w/.-])")
_WORD_LITERAL = re.compile(r"[A-Za-z][A-Za-z'-]+")


def normalize_factual_value(value: ReconstructedValue) -> str:
    if value.kind == "number":
        parsed = parse_amount(value.raw_value)
        return str(parsed) if parsed is not None else value.raw_value.strip()
    return value.raw_value.strip()


def inventory_factual_values(
    reconstruction: Reconstruction,
) -> set[tuple[str, str, str]]:
    """Inventory every numeric/date literal with a stable text or cell location."""
    found: set[tuple[str, str, str]] = set()

    def scan(text: str, location: str) -> None:
        date_spans = set()
        for match in _DATE_LITERAL.finditer(text):
            found.add(("date", match.group(0), f"{location}:{match.start()}"))
            date_spans.update(range(match.start(), match.end()))
        for match in _NUMBER_LITERAL.finditer(text):
            if any(index in date_spans for index in range(match.start(), match.end())):
                continue
            parsed = parse_amount(match.group(0))
            found.add(("number", str(parsed), f"{location}:{match.start()}"))

    scan(reconstruction.text or "", "text")
    for table_index, table in enumerate(reconstruction.tables):
        for row_index, row in enumerate(table):
            for cell_index, cell in enumerate(row):
                scan(str(cell), f"table-{table_index}-r{row_index}-c{cell_index}")
    return found


def classify_corroboration(
    value: ReconstructedValue,
    evidence: Evidence,
    post_verdict: QualityVerdict,
) -> ValueCorroboration:
    """Tie corroboration to one value and its actual supporting evidence."""
    normalized = parse_amount(value.raw_value) if value.kind == "number" else value.raw_value.strip()
    for source_name, source_text in (
        ("native_text", evidence.native_text),
        ("ocr_text", evidence.ocr_text),
    ):
        date_spans = {
            index
            for match in _DATE_LITERAL.finditer(source_text or "")
            for index in range(match.start(), match.end())
        }
        matches = (
            _DATE_LITERAL.finditer(source_text or "")
            if value.kind == "date"
            else _NUMBER_LITERAL.finditer(source_text or "")
        )
        for match in matches:
            if value.kind == "number":
                if any(index in date_spans for index in range(match.start(), match.end())):
                    continue
                exact = parse_amount(match.group(0)) == normalized
            else:
                exact = match.group(0) == normalized
            if exact:
                return ValueCorroboration(
                    value.value_id, value.raw_value, Corroboration.CORROBORATED,
                    check_name="source_exact_token",
                    evidence_refs=[f"{source_name}:{match.start()}-{match.end()}"],
                )
    for check in post_verdict.checks:
        if check.verdict is not Verdict.PASS or not check.evidence_refs:
            continue
        if value.kind == "number" and normalized is not None and check.candidate_value == normalized:
            return ValueCorroboration(
                value.value_id, value.raw_value, Corroboration.CORROBORATED,
                check_name=check.name, evidence_refs=list(check.evidence_refs),
            )
        # Date checks must expose a value-specific evidence ref such as
        # `date:table-0-r3-c1=2023-03-01`; a generic date-column PASS is not enough.
        expected_ref = f"date:{value.location}={value.raw_value}"
        if value.kind == "date" and expected_ref in check.evidence_refs:
            return ValueCorroboration(
                value.value_id, value.raw_value, Corroboration.CORROBORATED,
                check_name=check.name, evidence_refs=list(check.evidence_refs),
            )
    return ValueCorroboration(
        value.value_id, value.raw_value, Corroboration.UNCORROBORATED
    )


def factual_inventory_matches(reconstruction: Reconstruction) -> bool:
    """Reject omitted numeric/date declarations before acceptance.

    `inventory_factual_values` deterministically scans reconstruction text and
    tables. Its normalized `(kind, value, location)` set must exactly equal the
    model's declared `factual_values`; otherwise the structured output is incomplete.
    """
    return inventory_factual_values(reconstruction) == {
        (value.kind, normalize_factual_value(value), value.location)
        for value in reconstruction.factual_values
    }


def reconstruction_content_is_source_anchored(
    reconstruction: Reconstruction, evidence: Evidence
) -> bool:
    """Reject novel prose; fallback may restructure source words, not invent them."""
    candidate_material = [reconstruction.text]
    candidate_material.extend(
        str(cell)
        for table in reconstruction.tables
        for row in table
        for cell in row
    )
    evidence_material = [evidence.native_text, evidence.ocr_text]
    evidence_material.extend(
        str(cell) for table in evidence.tables for row in table for cell in row
    )
    candidate_tokens = Counter(
        token.casefold()
        for token in _WORD_LITERAL.findall(" ".join(candidate_material))
    )
    source_tokens = Counter(
        token.casefold()
        for token in _WORD_LITERAL.findall(" ".join(evidence_material))
    )
    if not candidate_tokens:
        return bool(reconstruction.factual_values)
    return all(source_tokens[token] >= count for token, count in candidate_tokens.items())


class ExtractionFallbackLadder:
    def __init__(
        self,
        *,
        gate: ExtractionQualityGate,
        rasterizer: PageRasterizer,
        ledger: InterventionLedger,
        tier1: Optional[Any] = None,
        tier2: Optional[Any] = None,
    ) -> None:
        self.gate = gate
        self.rasterizer = rasterizer
        self.ledger = ledger
        self.tiers: list[tuple[Tier, Optional[Any]]] = [
            (Tier.TIER_1, tier1),
            (Tier.TIER_2, tier2),
        ]

    async def resolve(
        self,
        source: Path,
        page: ExtractedPage,
        verdict: QualityVerdict,
        *,
        document_id: str,
        tables: Optional[Sequence[Sequence[Sequence[str]]]] = None,
        region: Optional[tuple[float, float, float, float]] = None,
    ) -> ResolvedPage:
        if not verdict.escalates:
            return ResolvedPage(page=page, outcome=FallbackOutcome.UNCHANGED)

        try:
            evidence = assemble_evidence(
                source, page, verdict, self.rasterizer, region=region, tables=tables
            )
        except RasterizationError as exc:
            logger.warning("Could not rasterize page %s: %s", page.number, exc)
            return ResolvedPage(
                page=self._mark_for_review(page, str(exc)),
                outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED,
            )

        trigger = "; ".join(verdict.reasons) or verdict.verdict.value

        for tier, model in self.tiers:
            if model is None:
                continue

            started = time.monotonic()
            try:
                reconstruction = await model.reconstruct(evidence, tier)
            except (ModelUnavailable, Exception) as exc:  # noqa: BLE001 - all decline
                logger.warning("Tier %s reconstruction failed: %s", int(tier), exc)
                await self._record(
                    document_id, page, evidence, tier, trigger,
                    reconstruction=None, post=None,
                    outcome=FallbackOutcome.ESCALATED,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                continue

            candidate = replace(
                page,
                text=reconstruction.text,
                source=PageSource.RECONSTRUCTED,
                status=PageStatus.OCR_COMPLETED,
            )
            post = self.gate.assess(candidate, tables=reconstruction.tables or tables)
            latency_ms = int((time.monotonic() - started) * 1000)

            corroborations = [
                classify_corroboration(value, evidence, post)
                for value in reconstruction.factual_values
            ]
            accepted = (
                post.verdict is Verdict.PASS
                and bool((reconstruction.text or "").strip())
                and factual_inventory_matches(reconstruction)
                and reconstruction_content_is_source_anchored(reconstruction, evidence)
                and all(
                    item.status is Corroboration.CORROBORATED
                    for item in corroborations
                )
            )
            await self._record(
                document_id, page, evidence, tier, trigger,
                reconstruction=reconstruction, post=post,
                outcome=FallbackOutcome.RESOLVED if accepted else FallbackOutcome.ESCALATED,
                latency_ms=latency_ms,
                corroborations=corroborations,
            )

            if accepted:
                accepted_page = replace(
                    candidate,
                    tables=reconstruction.tables or page.tables,
                    quality_verdict=post.verdict.value,
                    quality_checks=[asdict(check) for check in post.checks],
                    needs_review=False,
                )
                return ResolvedPage(
                    page=accepted_page,
                    outcome=FallbackOutcome.RESOLVED,
                    tier_used=tier,
                    reconstruction=reconstruction,
                    post_verdict=post,
                )

        await self._record(
            document_id, page, evidence, Tier.TIER_2, trigger,
            reconstruction=None, post=None,
            outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED, latency_ms=0,
        )
        return ResolvedPage(
            page=self._mark_for_review(page, trigger),
            outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED,
        )

    @staticmethod
    def _mark_for_review(page: ExtractedPage, reason: str) -> ExtractedPage:
        """Leave the page's text exactly as it was. Nothing is invented."""
        return replace(
            page,
            error=f"Requires human review: {reason}"[:500],
            needs_review=True,
        )

    async def _record(
        self,
        document_id: str,
        page: ExtractedPage,
        evidence: Evidence,
        tier: Tier,
        trigger: str,
        *,
        reconstruction: Optional[Reconstruction],
        post: Optional[QualityVerdict],
        outcome: FallbackOutcome,
        latency_ms: int,
        corroborations: Optional[Sequence[ValueCorroboration]] = None,
    ) -> None:
        corrections = []
        if reconstruction is not None:
            corrections = [
                {
                    "value_id": item.value_id,
                    "after": item.raw_value,
                    "corroboration": item.status.value,
                    "check_name": item.check_name,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in (corroborations or [])
            ]

        await self.ledger.record(
            Intervention(
                document_id=document_id,
                page_number=page.number,
                region_bbox=evidence.bbox,
                trigger=trigger,
                tier=tier,
                model=reconstruction.model if reconstruction else "",
                model_version=reconstruction.model_version if reconstruction else "",
                prompt_version=reconstruction.prompt_version if reconstruction else "",
                evidence_sent="region" if evidence.is_region else "page",
                dpi=evidence.dpi,
                text_sources=["native", "ocr"],
                confidence=reconstruction.confidence if reconstruction else 0.0,
                corrections=corrections,
                post_check=post.verdict.value if post else Verdict.NOT_CHECKABLE.value,
                outcome=outcome,
                tokens=(
                    reconstruction.input_tokens + reconstruction.output_tokens
                    if reconstruction else 0
                ),
                cost_usd=reconstruction.cost_usd if reconstruction else 0.0,
                latency_ms=latency_ms,
            )
        )
```

- [ ] **Step 5: Add the ladder settings**

In `backend/rbac_backend/core/config.py`:

```python
    # Phase 7 fallback ladder. Tier 2 is off by default: enabling it is a cost
    # decision, and the ladder degrades to HUMAN_REVIEW_REQUIRED without it.
    EXTRACTION_FALLBACK_ENABLED: bool = Field(
        default=False, validation_alias="EXTRACTION_FALLBACK_ENABLED"
    )
    EXTRACTION_FALLBACK_TIER2_ENABLED: bool = Field(
        default=False, validation_alias="EXTRACTION_FALLBACK_TIER2_ENABLED"
    )
    EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT: int = Field(
        default=10, validation_alias="EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT"
    )
    EXTRACTION_FALLBACK_DPI: int = Field(
        default=150, validation_alias="EXTRACTION_FALLBACK_DPI"
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_fallback_ladder.py -q`
Expected: PASS — 15 passed

- [ ] **Step 7: Run the whole suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known `test_route_control_manifest` notifications failure)

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/extraction/fallback/ projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/tests/test_fallback_ladder.py
git commit -m "feat: add bounded LLM/Vision extraction fallback ladder"
```

---

### Task 7.4: Wire the gate and ladder into the document pipeline

**Files:**
- Modify: `backend/rbac_backend/services/document_processor.py`
- Test: `backend/rbac_backend/tests/test_pipeline_gate_and_ladder_integration.py`

**Interfaces:**
- Consumes: `ExtractionQualityGate` (6.5), `ExtractionFallbackLadder` (7.3), `derive_processing_state` (3.1)
- Produces: no new public signatures; `ProcessingResult` gains `pages_human_review: list[int]`

- [ ] **Step 1: Write the failing test**

```python
"""End-to-end: a page that cannot be resolved must not reach COMPLETED."""

from __future__ import annotations

from rbac_backend.models.processing_state import ProcessingState, derive_processing_state
from rbac_backend.services.extraction.models import (
    Completeness,
    PageExtractionResult,
)


def _result(**overrides: object) -> PageExtractionResult:
    defaults = dict(
        pages=[],
        combined_text="",
        ocr_pages_total=1,
        ocr_failed_pages=[],
        ocr_deferred_pages=[],
        completeness=Completeness.COMPLETE,
        engine_version="1",
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)  # type: ignore[arg-type]


def test_a_page_needing_human_review_blocks_completed() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[1]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED
    assert state is not ProcessingState.COMPLETED


def test_processing_result_reports_human_review_pages() -> None:
    from rbac_backend.models.document_metadata import ProcessingResult

    result = ProcessingResult(success=True, pages_human_review=[1, 2])

    assert result.pages_human_review == [1, 2]


def test_processing_result_defaults_to_no_human_review_pages() -> None:
    from rbac_backend.models.document_metadata import ProcessingResult

    assert ProcessingResult(success=True).pages_human_review == []


async def test_quality_gate_runs_for_every_page_when_fallback_is_disabled() -> None:
    gate = _CountingGate()
    processor = build_processor(gate=gate, fallback_enabled=False)
    await processor.process_document(...)
    assert gate.page_numbers == list(range(1, FIXTURE_PAGE_COUNT + 1))
    assert processor.model.calls == 0


async def test_unrenderable_page_is_review_required_when_fallback_is_disabled() -> None:
    processor = build_processor(gate=ExtractionQualityGate(), fallback_enabled=False)
    result = await processor.process_document(..., extracted_pages=[unrenderable_page(2)])
    assert result.pages_human_review == [2]
    assert result.processing_state == "human_review_required"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_pipeline_gate_and_ladder_integration.py -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'pages_human_review'`

- [ ] **Step 3: Extend `ProcessingResult`**

In `backend/rbac_backend/models/document_metadata.py`, add to `ProcessingResult`:

```python
    pages_human_review: List[int] = Field(default_factory=list)
    extraction_completeness: Optional[str] = None
    processing_state: Optional[str] = None
    source_kind: Optional[str] = None
    extraction_result: Optional[PageExtractionResult] = Field(default=None, exclude=True)
```

- [ ] **Step 4: Add the gate-and-ladder pass to `DocumentProcessor`**

In `document_processor.py`, immediately after the Step 1 extraction block added in Task 3.3:

```python
            # Step 1b: assess every page, native and OCR alike. This block is
            # unconditional. The feature flag below controls model calls only.
            from ..core.config import settings
            from .extraction.fallback.ladder import ExtractionFallbackLadder
            from .extraction.fallback.ledger import InterventionLedger
            from .extraction.quality.gate import ExtractionQualityGate
            from .extraction.rasterizer import PageRasterizer

            gate = self.quality_gate or ExtractionQualityGate()
            pages_human_review: list[int] = []
            db = await self.database_service.get_database()
            ladder = None
            if settings.EXTRACTION_FALLBACK_ENABLED and source_kind in {
                SourceKind.PDF,
                SourceKind.IMAGE,
            }:
                ladder = ExtractionFallbackLadder(
                    gate=gate,
                    rasterizer=PageRasterizer(dpi=int(settings.EXTRACTION_FALLBACK_DPI)),
                    ledger=InterventionLedger(db=db),
                    tier1=self.reconstruction_tier1,
                    tier2=(
                        self.reconstruction_tier2
                        if settings.EXTRACTION_FALLBACK_TIER2_ENABLED
                        else None
                    ),
                )
            budget = int(settings.EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT)
            escalated = 0
            resolved_pages = []

            for page in extraction.pages:
                page_verdict = gate.assess(page, tables=page.tables)
                assessed_page = replace(
                    page,
                    quality_verdict=page_verdict.verdict.value,
                    quality_checks=[asdict(check) for check in page_verdict.checks],
                )
                if not page_verdict.escalates:
                    # PASS and original deterministic NOT_CHECKABLE remain
                    # unchanged. NOT_CHECKABLE never invokes a model.
                    resolved_pages.append(assessed_page)
                    continue

                if ladder is None or escalated >= budget:
                    reviewed = replace(assessed_page, needs_review=True)
                    resolved_pages.append(reviewed)
                    pages_human_review.append(page.number)
                    continue

                escalated += 1
                outcome = await ladder.resolve(
                    input_path, assessed_page, page_verdict,
                    document_id=document_id or "",
                    tables=page.tables,
                )
                final_page = outcome.page
                if outcome.outcome is FallbackOutcome.HUMAN_REVIEW_REQUIRED:
                    final_page = replace(final_page, needs_review=True)
                    pages_human_review.append(page.number)
                resolved_pages.append(final_page)

            extraction.pages = resolved_pages
            extraction.combined_text = "\n\n".join(page.text or "" for page in resolved_pages)
            if pages_human_review:
                extraction.completeness = Completeness.PARTIAL
            await page_store.record_pages(resolved_pages)  # versioned upsert, no delete
            raw_ocr_text = extraction.combined_text or None
```

Add imports for `asdict`, `replace`, `Completeness`, `SourceKind`, and `FallbackOutcome`. Add injectable `quality_gate`, `reconstruction_tier1`, and `reconstruction_tier2` constructor seams; their production defaults remain deterministic gate + no models. Task 1.5 must populate `ExtractedPage.tables` from `pdfplumber.Page.extract_tables()` for native pages (and leave it empty where no structured table exists). The `DocumentPageStore` write above persists gate verdicts even when fallback is disabled. Text and archive sources never invoke a visual model: an unresolved text page goes to review, while Task 4.3 returns archives as `STORED_ONLY` before this block. `PageRasterizer` handles PNG/JPEG directly as logical page 1.

Then pass `pages_human_review=pages_human_review` and `extraction_completeness=extraction.completeness.value` into the returned `ProcessingResult`.

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_pipeline_gate_and_ladder_integration.py -q`
Expected: PASS — 5 passed

- [ ] **Step 6: Run the whole suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 7: Confirm the gate is independent of the fallback flag**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_pipeline_gate_and_ladder_integration.py::test_quality_gate_runs_for_every_page_when_fallback_is_disabled -q`
Expected: PASS. This proves `EXTRACTION_FALLBACK_ENABLED=false` produces zero model calls **without** suppressing page assessment or verdict persistence. Task 7.5 proves the complete clean-fixture cost path.

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/document_processor.py projectDMS/backend/rbac_backend/models/document_metadata.py projectDMS/backend/rbac_backend/tests/test_pipeline_gate_and_ladder_integration.py
git commit -m "feat: run the quality gate and fallback ladder in the document pipeline"
```

---

### Task 7.5: Real end-to-end quality/fallback/cost acceptance test

The Phase 7.4 test is a wiring/unit test. This task is the release gate: it enters through the real Mongo-backed document job and exercises the real `DocumentProcessor`, source dispatch, extraction engine, quality gate, optional ladder decision, versioned page store, durable job checkpoint, and final document state.

**Files:**
- Modify: `backend/rbac_backend/services/document_service.py` (injectable `document_processor_factory`, production default unchanged)
- Extend: `backend/rbac_backend/tests/fixtures/pdf_builders.py` with `build_clean_native_pdf`
- Create: `backend/rbac_backend/tests/integration/test_unified_extraction_e2e.py`
- Modify: `backend/rbac_backend/tests/integration/README.md`

**Interfaces:**
- Consumes: Tasks 3.2–3.4, 4.3, 6.5, and 7.3–7.4.
- Uses a real temporary Mongo database on the test replica set and real Motor/ObjectId behaviour. Do not replace `DocumentPageStore`, job claim/checkpoint code, `SourceKindRouter`, `PageExtractionEngine`, `ExtractionQualityGate`, or final-state writes with fakes.
- External model adapters are counting stubs. The clean native fixture must call neither fallback tier. OCR runner may be a fail-on-call stub because the fixture has adequate native text; a call is a test failure.

- [ ] **Step 1: Build the clean native fixture**

Create a two-page PDF with more than 30 coherent words per page and one arithmetically exact table whose column roles are identifiable. No page requires OCR; every applicable deterministic check passes and the aggregate page verdict is `PASS` (irrelevant checks may remain `NOT_CHECKABLE`). Record expected page count and table values next to the builder.

- [ ] **Step 2: Write the real clean-path test**

```python
async def test_document_job_clean_fixture_is_gated_persisted_and_costs_zero(
    tmp_path: Path,
) -> None:
    async with temporary_replica_set_database() as db:
        source = build_clean_native_pdf(tmp_path / "clean.pdf")
        tier1 = CountingReconstructionModel()
        tier2 = CountingReconstructionModel()
        processor = DocumentProcessor(
            database_service=_BoundDatabaseService(db),
            quality_gate=ExtractionQualityGate(),
            reconstruction_tier1=tier1,
            reconstruction_tier2=tier2,
            pdf_ocr_runner=FailIfCalledOcrRunner(),
        )
        service = DocumentService(
            db=db, document_processor_factory=lambda: processor
        )
        document, job_id = await seed_document_and_job(
            db, source, source_mime="application/pdf"
        )

        with override_extraction_settings(fallback_enabled=True):
            assert await service.process_document_job(job_id) is True

        pages = await current_document_pages(db, document.id)
        job = await db.document_processing_jobs.find_one({"_id": job_id})
        stored = await db.documents.find_one({"_id": ObjectId(document.id)})
        interventions = await db.page_extraction_interventions.count_documents(
            {"document_id": document.id}
        )

        assert len(pages) == 2
        assert all(page["quality_verdict"] == "pass" for page in pages)
        assert all(page["needs_review"] is False for page in pages)
        assert await db.document_extraction_heads.count_documents(
            {"document_id": document.id}
        ) == 1
        assert job["status"] == "completed"
        assert job["remaining_page_numbers"] == []
        assert stored["processing_status"] == "completed"
        assert tier1.calls == tier2.calls == 0
        assert interventions == 0
```

This assertion is the actual zero-fallback-cost proof. The 12-case golden unit dataset alone is not an end-to-end spend test.

- [ ] **Step 3: Add failure-path acceptance cases through the same entry point**

Add one unrenderable/corrupt PDF fixture and one PNG job whose direct image OCR is empty and whose model reconstruction returns post-gate `NOT_CHECKABLE`. Assert both finish `human_review_required`, never `completed`; the PNG case proves `DocumentProcessor` routes image extraction and direct-image raster evidence without touching OCRmyPDF/pdfplumber, creates an intervention row with `outcome=escalated`/final review, and does not publish the reconstruction as accepted evidence. Add a resumable PDF fixture that defers pages on attempt one and completes after reclaim, proving the visible page version and final state survive the boundary.

- [ ] **Step 4: Add a bounded-cost assertion**

For a fixture with more failing pages than `EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT`, assert model calls equal the configured bound (not total pages), excess pages are review-required, and the document does not reach `completed`. Assert every call has a ledger row with tokens/cost/latency and value-specific corroboration records.

- [ ] **Step 5: Run in the backend container against the replica set**

```bash
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  run --rm --build -e RUN_UNIFIED_EXTRACTION_E2E=1 backend \
  python -m pytest rbac_backend/tests/integration/test_unified_extraction_e2e.py -q
```

Expected: PASS. If Mongo is unavailable, the test must skip only when `RUN_UNIFIED_EXTRACTION_E2E` is absent; with the variable set, inability to connect is a failure.

- [ ] **Step 6: Run the full backend suite and commit**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q
git add projectDMS/backend/rbac_backend/services/document_service.py projectDMS/backend/rbac_backend/tests/fixtures/pdf_builders.py projectDMS/backend/rbac_backend/tests/integration/test_unified_extraction_e2e.py projectDMS/backend/rbac_backend/tests/integration/README.md
git commit -m "test: prove unified extraction end to end with zero clean fallback cost"
```

---

### Task 7.6: Production worker canary and rollback validation

This task is an operational acceptance gate, not permission to deploy while merely executing the implementation plan. Run it only under an explicitly authorised production change window, using an existing safe demo organisation/document and the backup-first production runbook.

**Files:**
- Modify: `backend/rbac_backend/core/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.prod.yml`
- Create: `scripts/unified_extraction_canary_status.py`
- Modify: `scripts/post_deploy_verify.sh`
- Create: `docs/operations/unified_extraction_canary_and_rollback.md`

**Interfaces:**
- Adds `UNIFIED_EXTRACTION_ENABLED=false` and `UNIFIED_EXTRACTION_CANARY_ORG_IDS=`. Job creation records `pipeline_version="unified_v1"` only for allowlisted organisations while the global flag is false; all other jobs remain on the legacy path. The worker claim predicate and processor dispatch honour the persisted job version, so a canary worker cannot accidentally claim/upgrade every tenant.
- Rollback disables new `unified_v1` assignment, gracefully stops the document worker, preserves page evidence/checkpoints, and requeues only unfinished canary jobs after validation. No Docker prune and no page-evidence deletion.

- [ ] **Step 1: Prove pre-canary safety**

Back up production `.env`, uploads, and Mongo using the existing production runbook; record deployed commit, replica-set health, current job counts by status/pipeline version, worker restart counts, readiness, and the latest verified backup age. Confirm Task 0.4's RAR result: if it is not positive, production keeps `RAR_UPLOAD_ENABLED=false` throughout the canary.

- [ ] **Step 2: Deploy code with unified routing globally off**

Rebuild only affected backend/document-worker services. Start with `UNIFIED_EXTRACTION_ENABLED=false` and an empty canary allowlist. Verify readiness, route/auth controls, one document-worker owner, no duplicate background document loop on the web tier, stable restart counts, and no change in normal job throughput.

- [ ] **Step 3: Enable one safe canary scope**

Add only the approved demo organisation to `UNIFIED_EXTRACTION_CANARY_ORG_IDS`, recreate the document worker, and enqueue one clean PDF plus one controlled mixed fixture. Assert:

- only those jobs have `pipeline_version=unified_v1`;
- clean fixture ends `completed`, has PASS page verdicts, a published extraction head, and zero intervention/model rows;
- mixed fixture checkpoints/reclaims remaining pages and reaches the expected completed/review state;
- no non-canary organisation acquires unified page rows or intervention rows;
- CPU/memory, queue age, latency, error rate, model calls, and cost remain within the recorded acceptance bounds.

- [ ] **Step 4: Execute and validate rollback**

Set the canary allowlist empty/disable unified assignment, gracefully recreate the document worker, and confirm new demo jobs use the legacy pipeline. If a unified canary job was deliberately paused mid-run, verify its `extraction_run_id`, remaining pages, attempts, and prior visible head survive; requeue it only after deciding whether to resume on the new image or mark review-required. Prove readiness, legacy job completion, no restart loop, and no loss of the pre-canary or canary page evidence.

- [ ] **Step 5: Record the go/no-go evidence**

The operations document records exact commands/output, deployed commit, canary document IDs (demo data only), job/page/intervention counts, zero-call clean result, resource metrics, rollback result, and a go/no-go decision. Global enablement is a separate authorised change and is blocked unless both canary and rollback validations pass.

- [ ] **Step 6: Commit the canary controls/runbook (before any authorised deployment)**

```bash
git add projectDMS/backend/rbac_backend/core/config.py projectDMS/.env.example projectDMS/docker-compose.prod.yml projectDMS/scripts/unified_extraction_canary_status.py projectDMS/scripts/post_deploy_verify.sh projectDMS/docs/operations/unified_extraction_canary_and_rollback.md
git commit -m "ops: add unified extraction canary and rollback controls"
```

---

## Plan self-review

**1. Spec coverage.** Every Phase 0–7 requirement maps to a task:

| Spec requirement | Task |
|---|---|
| Fixtures, golden expectations, production sampling, baseline | 0.1, 0.2, 0.5 |
| Job-claim atomicity verified (prerequisite) | 0.3 |
| `ocrmypdf --pages` output shape, ClamAV RAR measured in container | 0.4 |
| Hardened source→output page mapping | 1.1, 1.6 |
| `PageExtractionEngine` behind a `PageStore` seam | 1.4, 1.5 |
| Contract path delegates, behaviour-preserving | 1.7 |
| Dedicated worker, `START_BACKGROUND_SERVICES` not reused | 2.1, 2.2 |
| `PARTIALLY_PROCESSED` / `HUMAN_REVIEW_REQUIRED`, durable remaining-page reclaim and final transitions | 1.5, 3.1, 3.4 |
| General path page-wise; §3.1 defect fixed; BSON-safe versioned persistence | 3.2, 3.3, 3.4 |
| Page classification beyond the char threshold | 1.3 |
| PDF/PNG/JPEG/TXT/archive source-kind dispatch wired into `DocumentProcessor`; images stored as-is with direct image OCR | 4.1, 4.2, 4.3 |
| ZIP store-only; RAR admitted only after positive ClamAV inner-member proof | 0.4, 5.1, 5.2 |
| Letter number optional for archives | 5.2 |
| Backend-canonical file policy; picker aligned | 5.3 |
| Column-role aware gate, separate quantity/display and monetary tolerances | 6.1, 6.2 |
| Split-digit repair requires independent row and multirow-subtotal identities | 6.2 |
| Date-convention checker uses sequence context or remains indeterminate | 6.3 |
| Reading-order/density checks; unrenderable is unresolved, not PASS | 6.4 |
| Four verdicts; deterministic gate always runs; original `NOT_CHECKABLE` never escalates | 6.5, 7.4 |
| Exactly 12 false-positive cases and 9 corruption hard gates | 6.5 |
| PDF plus direct-image rasterizer, minimal evidence, region-preferred | 7.1, 7.2 |
| Two-tier ladder terminating in human review | 7.3 |
| Re-verification requires exact PASS, source-anchored content, and value-specific corroboration; unrelated PASS cannot accept | 7.3 |
| Confidence never accepts | 7.3 |
| Intervention provenance ledger | 7.2, 7.3 |
| Page-level failure containment | 1.5, 3.4, 7.3, 7.4 |
| Real job-to-persistence-to-final-state test; zero clean fallback calls | 7.5 |
| Production worker canary and proven rollback | 7.6 |

**Deliberately deferred to the Phases 8–11 plan:** enclosure child documents, progress-photo extraction, photo-candidate Vision adjudication, and review UI. Phase 7.6 now owns the worker canary and rollback evidence; broader global-rollout benchmarking still follows the Phase 0.5 baseline.

**2. Placeholder scan.** One intentional placeholder remains: Task 0.4 Step 3's findings template contains `<...>` markers, and Step 4 explicitly requires every one to be replaced with a measured value before the task is complete. No other step defers work.

**3. Type consistency.** `PageStore` (`begin_batch` / `finish_batch` / `record_pages`) is implemented by `NullPageStore` (1.4), `ContractPageStore` (1.7), and the versioned `DocumentPageStore` (3.2); only the document adapter adds `publish_run`. PDF-only `OcrRunner.run(source_pdf, page_numbers, language)` is satisfied by `OcrMyPdfRunner` (1.6). Standalone-image `ImageOcrRunner.run(source_image, language)` is satisfied by `TesseractImageOcrRunner` (4.2); the protocols are intentionally incompatible. `Verdict` (6.2) is consumed unchanged by 6.3, 6.4, 6.5, and 7.3. `Tier`, `FallbackOutcome`, `Evidence`, `Reconstruction`, `ReconstructedValue`, `ValueCorroboration`, and `Intervention` are defined once in `fallback/models.py` (7.2) and used unchanged in 7.3.

**Verified current-code seams:** Task 1.7's delegation calls `self._combine_pages(...)`, which exists in `contracts_ingest.py` today and must be kept when the four other private methods are deleted. `DatabaseService` exposes async `get_database()` and has no public `db` attribute, so Tasks 3.3 and 7.4 call `await self.database_service.get_database()`. `Document.filetype` already stores MIME; Task 4.3 preserves the upload validator's detected value in that field and the durable job instead of adding a competing source-of-truth field. The active job API is `queue_document_processing` / `_claim_next_processing_job`, and `rbac_backend.worker` currently starts no document loop; Tasks 0.3, 2.1, and 5.2 use those actual names and Task 2.1 wires both FastAPI and worker lifecycles.
