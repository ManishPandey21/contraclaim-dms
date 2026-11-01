# Child Enclosures, Progress-Photo Assets & Rollout (Phases 8–11) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make enclosures first-class child documents with their own extraction, extract substantive site/progress photographs from PDFs as independent page-anchored assets, resolve uncertain photo candidates with selective Vision, and gate production rollout on measured cost.

**Architecture:** Enclosures become real `documents` rows discriminated by `parent_document_id`, reusing the Phase 0–7 extraction pipeline rather than duplicating it. Photo extraction runs as a three-stage funnel — free candidate filtering, free deterministic scoring, then paid Vision on the uncertainty band only. Only `ACCEPTED` assets become visible and searchable; `REVIEW_REQUIRED` stays internal until a human resolves it.

**Tech Stack:** Python 3 / FastAPI / Motor (MongoDB), `pdfplumber` 0.11.7 (pdfminer `PDFStream`), `pypdfium2` 5.0.0, `pikepdf` 9.10.2, `pillow` 12.3.0, React/Vite SPA, pytest (no pytest-asyncio).

**Depends on:** [Phases 0–7 plan](2026-08-14-unified-page-extraction-phases-0-7.md). Phase 9 needs `PageRasterizer` (Task 7.1) and `PageClassification` (Task 1.3). Phase 8 needs the extraction pipeline from Phase 3. **Phase 8 has no dependency on Phases 6–7** and may be resequenced earlier.

**Source spec:** [2026-08-14-page-wise-ocr-and-image-assets-design.md](../specs/2026-08-14-page-wise-ocr-and-image-assets-design.md).

## Global Constraints

Identical to the Phases 0–7 plan. **Every task's requirements implicitly include this section.**

- **Test interpreter is `backend/.venv` only.** Run from `C:/SaaS/projectDMS`:
  `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
- **The git repository root is `C:\SaaS`.** `git add` paths are prefixed `projectDMS/`.
- **Never `git add -A`.** Stage only files you personally edited.
- **Async tests each get their own `asyncio.run` loop** (`backend/conftest.py`). Async fixtures are unsupported — use `@asynccontextmanager` inside the test.
- **No `print()`** in `ingestion|retrieval|agents|observability`. ruff + ruff-format + mypy run in pre-commit.
- **New LLM call sites pass `strict=True`** with a pinned prompt-version constant and their own deterministic fallback.
- **Routers re-raise domain errors as a group:** `except (BaseDomainError, HTTPException)`.
- **After any route or authorization change**, regenerate the manifest:
  `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
- **Every new rate limiter passes `scope=`.**
- **Prefer fail-visible.** Never mark success on a skipped step.
- **Duplicate-upload detection:** any new upload entry point calls `duplicate_detection.precheck_upload` before creating a record; any new downstream artifact checks `duplicate_status == "pending"` before writing.
- **All reference writes go through `ReferenceSyncService.sync_bidirectional`.**
- **No new AGPL dependency.** Rasterization uses `pypdfium2`, already installed.
- **Only `ACCEPTED` photo assets are visible, searchable, exportable, or retrievable.** `REVIEW_REQUIRED` is internal until resolved.
- **Model confidence may route but never accept.**
- `cd client && npx tsc -b` reports **146 pre-existing errors** — check your files are absent from the output rather than treating it as a gate.

---

## Findings that correct the spec

Three facts were measured against the working tree on 2026-08-14 and change what the spec assumed. **Read these before starting.**

### F1. `uploadType="enclosure"` is impossible

`Document.uploadType` carries a regex constraint ([models/document.py:74](../../../backend/rbac_backend/models/document.py)):

```python
uploadType: str = Field(..., pattern=r"(?i)^(incoming|outgoing|contract)$")
```

An enclosure row with `uploadType="enclosure"` fails model validation outright. Extending the pattern would also change what every existing `uploadType` consumer sees.

**Resolution:** `parent_document_id` is the **sole** child discriminator. A child inherits its parent's `uploadType`, which is also semantically right — an enclosure to an incoming letter is incoming correspondence. `relationship_type` records what kind of child it is. The spec's listing rule already filtered on `parent_document_id: null`, so this is consistent with it.

### F2. Listing scope is built by `build_document_query`, not `build_scope_query`

`controller_list_documents` calls `self.auth_service.build_document_query(current_user, filters)` ([routers/documents.py:1477](../../../backend/rbac_backend/routers/documents.py)). The repo-wide `build_scope_query` helper is used by 16 other routers but **not** by documents. Child exclusion therefore belongs in `build_document_query`, and Task 8.2 must not touch `build_scope_query`.

### F3. Photo-classifier signals are reachable; the entropy threshold in the spec is wrong

Measured in `backend/.venv` this session against a purpose-built PDF. `pdfplumber` `page.images` entries expose:

| Signal | Key | Measured example |
|---|---|---|
| Native pixel size | `srcsize` | `(800, 600)` vs `(16, 16)` |
| Bit depth | `bits` | `8` vs `1` |
| Colour space | `colorspace` | `[/'DeviceRGB']` vs `[/'DeviceGray']` |
| Placement rect | `x0, top, x1, bottom` | `(100, 142, 500, 442)` |
| Mask flag | `imagemask` | `None` |
| **Encoding** | `stream.attrs["Filter"]` | `/DCTDecode` vs `/FlateDecode` |
| **Stable identity** | `sha256(stream.get_rawdata())` | distinct per image |

**The correction:** the spec proposed entropy `> 7.0 bits/byte` as a positive signal. A flat-colour RGB JPEG measured **2.664 bits/byte** — it would have been rejected. Entropy varies with subject matter, not with whether something is a photograph. It is demoted to a **weak scoring signal with a calibrated weight**, never a hard gate, and Task 9.4 calibrates it against real fixtures. `DCTDecode` + `DeviceRGB` + 8bpc + large `srcsize` carry the signal instead.

---

## File Structure

### New: `backend/rbac_backend/services/assets/`

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports |
| `models.py` | `AssetDecision`, `AssetSignals`, `AssetCandidate`, `RejectReason` |
| `candidates.py` | Enumerate image XObjects with signals and a stable hash |
| `boilerplate.py` | Cross-page hash-repeat detection |
| `classifier.py` | Hard rejects + weighted scoring + uncertainty banding |
| `adjudicator.py` | `PhotoAdjudicator` protocol, `NullPhotoAdjudicator`, budget |
| `store.py` | Persist accepted assets as `FileObject`s; candidate metadata rows |

### New elsewhere

| File | Responsibility |
|---|---|
| `services/child_documents.py` | `ChildDocumentService` — create/link/inherit scope |
| `routers/review_queue.py` | `GET/POST /api/review-queue/*` |
| `scripts/phase11_rollout_benchmark.py` | Measured rollout gate |
| `client/src/components/DocumentAssets.tsx` | Asset gallery with page deep link |
| `client/src/pages/ReviewQueuePage.tsx` | Reviewer surface |

### Modified

| File | Change |
|---|---|
| `models/document.py` | `parent_document_id`, `relationship_type`, `source_page_range`, `parent_provenance` |
| `routers/documents.py:1647-1752` | Enclosure intake creates a child document |
| `routers/documents.py:1477` | Child exclusion in `build_document_query` |
| `services/document_processor.py` | Asset extraction pass |
| `client/src/pages/ShareDocumentPage.tsx` | Wire enclosure add/delete |

---

## Phase 8 — Enclosures as child documents

### Task 8.1: Child-document fields and `ChildDocumentService`

**Files:**
- Modify: `backend/rbac_backend/models/document.py` (add four fields to `Document`)
- Create: `backend/rbac_backend/services/child_documents.py`
- Test: `backend/rbac_backend/tests/test_child_documents.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Document.parent_document_id: Optional[str]`, `.relationship_type: Optional[str]`, `.source_page_range: Optional[dict]`, `.parent_provenance: Optional[dict]`
  - `class RelationshipType(str, Enum)` — `ENCLOSURE`, `ANNEXURE`, `ATTACHMENT`
  - `class ChildDocumentService` with `build_child_document(parent: dict, *, filename, filetype, filesize, relationship_type, source_page_range=None, **storage) -> dict`
  - `is_child(document: dict) -> bool`

**Per F1:** the child inherits the parent's `uploadType`. Do **not** extend the `uploadType` regex.

- [ ] **Step 1: Write the failing test**

```python
"""Child documents: real document rows discriminated by parent_document_id.

The uploadType field is regex-constrained to incoming|outgoing|contract, so an
"enclosure" upload type is not representable. parent_document_id is the sole
discriminator and the child inherits the parent's uploadType, which is also
semantically correct: an enclosure to an incoming letter is incoming.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from rbac_backend.models.document import Document
from rbac_backend.services.child_documents import (
    ChildDocumentService,
    RelationshipType,
    is_child,
)


def _parent() -> dict:
    return {
        "_id": "parent-1",
        "organization_id": "org-1",
        "project_id": "proj-1",
        "uploadType": "incoming",
        "letterNo": "CC/2026/001",
        "letterNoNormalized": "CC-2026-001",
        "subject": "Reworks at Nayaganj Station",
        "date": datetime(2026, 3, 9, tzinfo=timezone.utc),
        "status": "active",
    }


def test_upload_type_still_rejects_enclosure() -> None:
    # Documents the constraint that forced parent_document_id as discriminator.
    with pytest.raises(ValidationError):
        Document(
            organization_id="org-1",
            filename="a.pdf",
            filetype="application/pdf",
            filesize=1,
            uploadType="enclosure",
            date=datetime.now(timezone.utc),
            subject="s",
            status="active",
        )


def test_child_inherits_scope_and_upload_type() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="photo-log.pdf",
        filetype="application/pdf",
        filesize=2048,
        relationship_type=RelationshipType.ENCLOSURE,
    )

    assert child["organization_id"] == "org-1"
    assert child["project_id"] == "proj-1"
    assert child["uploadType"] == "incoming"


def test_child_records_the_parent_link_and_relationship() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="photo-log.pdf",
        filetype="application/pdf",
        filesize=2048,
        relationship_type=RelationshipType.ANNEXURE,
    )

    assert child["parent_document_id"] == "parent-1"
    assert child["relationship_type"] == "annexure"


def test_child_preserves_parent_provenance() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="photo-log.pdf",
        filetype="application/pdf",
        filesize=2048,
        relationship_type=RelationshipType.ENCLOSURE,
    )
    provenance = child["parent_provenance"]

    assert provenance["letter_no"] == "CC/2026/001"
    assert provenance["subject"] == "Reworks at Nayaganj Station"
    assert provenance["organization_id"] == "org-1"
    assert provenance["project_id"] == "proj-1"


def test_child_does_not_inherit_the_parent_letter_number() -> None:
    # A child is not a second letter with the same number - that would collide
    # with duplicate detection and reference linking.
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="photo-log.pdf",
        filetype="application/pdf",
        filesize=2048,
        relationship_type=RelationshipType.ENCLOSURE,
    )

    assert child.get("letterNo") is None
    assert child.get("letterNoNormalized") is None


def test_source_page_range_is_recorded_when_carved_from_a_parent() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="pages-4-6.pdf",
        filetype="application/pdf",
        filesize=1024,
        relationship_type=RelationshipType.ENCLOSURE,
        source_page_range={"start": 4, "end": 6},
    )

    assert child["source_page_range"] == {"start": 4, "end": 6}


def test_source_page_range_is_null_for_a_separately_uploaded_file() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="site-photos.pdf",
        filetype="application/pdf",
        filesize=1024,
        relationship_type=RelationshipType.ENCLOSURE,
    )

    assert child["source_page_range"] is None


def test_is_child_discriminates_on_parent_document_id() -> None:
    assert is_child({"parent_document_id": "parent-1"}) is True
    assert is_child({"parent_document_id": None}) is False
    assert is_child({}) is False


def test_child_document_validates_against_the_model() -> None:
    child = ChildDocumentService.build_child_document(
        _parent(),
        filename="photo-log.pdf",
        filetype="application/pdf",
        filesize=2048,
        relationship_type=RelationshipType.ENCLOSURE,
    )

    model = Document(**child)

    assert model.parent_document_id == "parent-1"
    assert model.relationship_type == "enclosure"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_child_documents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.child_documents'`

- [ ] **Step 3: Add the model fields**

In `backend/rbac_backend/models/document.py`, add to `Document` (leave `uploadType` untouched):

```python
    # Child-document linkage. parent_document_id is the sole discriminator:
    # uploadType is regex-constrained to incoming|outgoing|contract, so a child
    # inherits its parent's uploadType and records its kind here instead.
    parent_document_id: Optional[str] = Field(default=None)
    relationship_type: Optional[str] = Field(default=None)
    source_page_range: Optional[Dict[str, int]] = Field(default=None)
    parent_provenance: Optional[Dict[str, Any]] = Field(default=None)
```

Add `Any` and `Dict` to the `typing` import if absent.

- [ ] **Step 4: Write `ChildDocumentService`**

```python
"""Build child document rows that reuse the whole document pipeline.

A child is a real documents row, so existing RBAC scope filtering, the
extraction job loop, vectors, and search all work on it unchanged. What makes
it a child is parent_document_id and nothing else.

A child deliberately does NOT inherit letterNo: two rows sharing a letter
number would collide with duplicate detection and with reference linking, both
of which key on the normalized letter number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class RelationshipType(str, Enum):
    ENCLOSURE = "enclosure"
    ANNEXURE = "annexure"
    ATTACHMENT = "attachment"


def is_child(document: Dict[str, Any]) -> bool:
    """True when the document is a child of another document."""
    return bool(document.get("parent_document_id"))


class ChildDocumentService:
    @staticmethod
    def build_child_document(
        parent: Dict[str, Any],
        *,
        filename: str,
        filetype: str,
        filesize: int,
        relationship_type: RelationshipType,
        source_page_range: Optional[Dict[str, int]] = None,
        **storage: Any,
    ) -> Dict[str, Any]:
        """Build a child document row inheriting the parent's scope."""
        parent_id = str(parent.get("_id") or parent.get("id") or "")

        child: Dict[str, Any] = {
            "organization_id": parent.get("organization_id"),
            "project_id": parent.get("project_id", ""),
            "project_name": parent.get("project_name"),
            "filename": filename,
            "filetype": filetype,
            "filesize": filesize,
            # Inherited, not chosen: the model's regex admits only
            # incoming|outgoing|contract.
            "uploadType": parent.get("uploadType", "incoming"),
            "date": parent.get("date") or datetime.now(timezone.utc),
            "subject": f"{relationship_type.value.title()} to {parent.get('subject') or filename}",
            "status": parent.get("status", "active"),
            "parent_document_id": parent_id,
            "relationship_type": relationship_type.value,
            "source_page_range": source_page_range,
            "parent_provenance": {
                "letter_no": parent.get("letterNo"),
                "letter_no_normalized": parent.get("letterNoNormalized"),
                "subject": parent.get("subject"),
                "document_date": parent.get("date"),
                "organization_id": parent.get("organization_id"),
                "project_id": parent.get("project_id"),
            },
        }
        child.update(storage)
        return child
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_child_documents.py -q`
Expected: PASS — 9 passed

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/models/document.py projectDMS/backend/rbac_backend/services/child_documents.py projectDMS/backend/rbac_backend/tests/test_child_documents.py
git commit -m "feat: add child-document fields discriminated by parent_document_id"
```

---

### Task 8.2: Exclude children from top-level listings, keep them in retrieval

**Files:**
- Modify: `backend/rbac_backend/routers/documents.py` (`build_document_query` call site at ~1477, and the list route's filters)
- Test: `backend/rbac_backend/tests/test_child_document_listing.py`

**Interfaces:**
- Consumes: `is_child` (8.1)
- Produces: `apply_child_visibility(query: dict, *, include_children: bool) -> dict`; list route gains `include_children: bool = Query(False)`

**Per F2:** this lands in the documents router's own query construction. **Do not touch `build_scope_query`** — 16 other routers depend on it and none of them list documents.

- [ ] **Step 1: Write the failing test**

```python
"""Children are excluded from the library by default, never from retrieval."""

from __future__ import annotations

from rbac_backend.routers.documents import apply_child_visibility


def test_default_excludes_children() -> None:
    query = apply_child_visibility({"organization_id": "org-1"}, include_children=False)

    assert query["parent_document_id"] is None
    assert query["organization_id"] == "org-1"


def test_include_children_leaves_the_query_untouched() -> None:
    query = apply_child_visibility({"organization_id": "org-1"}, include_children=True)

    assert "parent_document_id" not in query


def test_an_explicit_parent_filter_is_never_overridden() -> None:
    # "List the children of parent-1" must survive the default exclusion.
    query = apply_child_visibility(
        {"parent_document_id": "parent-1"}, include_children=False
    )

    assert query["parent_document_id"] == "parent-1"


def test_scope_fields_are_preserved() -> None:
    scoped = {"organization_id": {"$in": ["org-1", "org-2"]}, "project_id": "proj-1"}

    query = apply_child_visibility(dict(scoped), include_children=False)

    assert query["organization_id"] == scoped["organization_id"]
    assert query["project_id"] == "proj-1"


def test_the_original_query_is_not_mutated() -> None:
    original = {"organization_id": "org-1"}

    apply_child_visibility(original, include_children=False)

    assert "parent_document_id" not in original


def test_build_scope_query_is_not_involved() -> None:
    # Regression guard for F2: documents listing does not use build_scope_query,
    # and this change must not introduce a dependency on it.
    import inspect

    source = inspect.getsource(apply_child_visibility)

    assert "build_scope_query" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_child_document_listing.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_child_visibility'`

- [ ] **Step 3: Add the helper and wire it in**

Add to `backend/rbac_backend/routers/documents.py`, near the other module-level helpers:

```python
def apply_child_visibility(query: Dict[str, Any], *, include_children: bool) -> Dict[str, Any]:
    """Hide child documents from ordinary library listings.

    Retrieval - search, vectors, drafting context - is deliberately NOT
    filtered: a child may surface on its own merit, carrying its parent
    provenance so the result stays attributable.

    An explicit parent_document_id filter always wins, so "list the children of
    X" still works.
    """
    scoped = dict(query)
    if include_children or "parent_document_id" in scoped:
        return scoped
    scoped["parent_document_id"] = None
    return scoped
```

In `controller_list_documents` (~line 1477), wrap the authorized query:

```python
        authorized_query = await self.auth_service.build_document_query(
            current_user, filters
        )
        authorized_query = apply_child_visibility(
            authorized_query, include_children=bool(filters.get("include_children"))
        )
```

In the `list_documents` route (~line 2572), add the query parameter and pass it through the filters dict:

```python
    include_children: bool = Query(False),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_child_document_listing.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Regenerate the route manifest and run the RBAC suites**

Run: `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "route or document or scope or tenant or isolation"`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/routers/documents.py projectDMS/backend/rbac_backend/route_control_manifest.json projectDMS/backend/rbac_backend/tests/test_child_document_listing.py
git commit -m "feat: exclude child documents from library listings by default"
```

---

### Task 8.3: Enclosure intake creates a processed child document

**Files:**
- Modify: `backend/rbac_backend/routers/documents.py:1647-1752` (`controller_add_enclosure`)
- Test: `backend/rbac_backend/tests/test_enclosure_child_intake.py`

**Interfaces:**
- Consumes: `ChildDocumentService` (8.1), `ArchiveIntakePolicy` (Phase 5), duplicate precheck
- Produces: no signature change to `controller_add_enclosure`; it now also creates a child `documents` row, a `DocumentVersion`, and a `document_processing_jobs` entry

**Keep `documents.enclosures[]` populated** for one release so `ShareDocumentPage` and any other reader keeps working — same supersession pattern the summary plan used.

- [ ] **Step 1: Write the failing test**

```python
"""Enclosure intake must produce a first-class, processed child document."""

from __future__ import annotations

import inspect

from rbac_backend.routers import documents as documents_module


def _source() -> str:
    return inspect.getsource(documents_module.controller_add_enclosure)


def test_enclosure_intake_builds_a_child_document() -> None:
    assert "ChildDocumentService" in _source()
    assert "build_child_document" in _source()


def test_enclosure_intake_runs_duplicate_precheck() -> None:
    # House rule: any new upload entry point calls precheck_upload before
    # creating a record.
    assert "precheck_upload" in _source()


def test_enclosure_intake_creates_a_processing_job_for_processable_types() -> None:
    source = _source()

    assert "create_processing_job" in source
    assert "creates_processing_job" in source, (
        "Archives attached as enclosures must not be queued for extraction."
    )


def test_enclosure_intake_still_populates_the_embedded_array() -> None:
    # Kept for one release so existing readers (ShareDocumentPage) do not break.
    assert "add_enclosure" in _source()


def test_enclosure_intake_preserves_the_domain_error_group() -> None:
    assert "except (DocumentError, HTTPException)" in _source()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_enclosure_child_intake.py -q`
Expected: FAIL — `AssertionError` on the `ChildDocumentService` check

- [ ] **Step 3: Modify `controller_add_enclosure`**

After the existing `store_result = await self.file_object_service.store_path(...)` block and before the audit emit, insert:

```python
                # Duplicate precheck before any record is created (house rule).
                duplicate = await duplicate_detection.precheck_upload(
                    organization_id=document.organization_id,
                    project_id=document.project_id,
                    sha256=spooled.sha256,
                    letter_no=None,
                )

                child = ChildDocumentService.build_child_document(
                    document if isinstance(document, dict) else document.model_dump(by_alias=True),
                    filename=spooled.filename,
                    filetype=spooled.mime_type,
                    filesize=spooled.size,
                    relationship_type=RelationshipType.ENCLOSURE,
                    file_object_id=store_result.get("file_object_id"),
                    filepath_local=store_result.get("filepath_local"),
                    filepath_s3=store_result.get("filepath_s3"),
                    storage_key=store_result.get("storage_key"),
                    storage_locations=store_result.get("storage_locations"),
                    duplicate_status=duplicate.status if duplicate else None,
                )
                child_document = await self.document_service.create_child_document(child)

                if ArchiveIntakePolicy.creates_processing_job(spooled.mime_type):
                    await self.document_service.create_processing_job(
                        document_id=str(child_document["_id"]),
                        organization_id=document.organization_id,
                        project_id=document.project_id,
                    )
```

Add these imports at the top of the file:

```python
from ..services.child_documents import ChildDocumentService, RelationshipType
```

`duplicate_detection` and `ArchiveIntakePolicy` are already imported by Phase 5's work; confirm both are present.

Add `create_child_document` to `DocumentService` — inserting the row and returning it, mirroring the existing `create_document` persistence path.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_enclosure_child_intake.py -q`
Expected: PASS — 5 passed

- [ ] **Step 5: Run the enclosure, duplicate, and tenancy suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "enclosure or duplicate or document or isolation"`
Expected: PASS

- [ ] **Step 6: Regenerate the manifest and run the full suite**

Run: `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 7: Commit**

```bash
git add projectDMS/backend/rbac_backend/routers/documents.py projectDMS/backend/rbac_backend/services/document_service.py projectDMS/backend/rbac_backend/route_control_manifest.json projectDMS/backend/rbac_backend/tests/test_enclosure_child_intake.py
git commit -m "feat: enclosures become first-class processed child documents"
```

---

### Task 8.4: Wire the enclosure UI

`enhanced-api.ts` already exposes `addDocumentEnclosure` / `deleteDocumentEnclosure`, but a repository-wide search found **no page calling them**. The backend interfaces are live and unreachable.

**Files:**
- Modify: `client/src/pages/ShareDocumentPage.tsx`
- Create: `client/src/components/EnclosureManager.tsx`
- Test: `client/src/components/__tests__/EnclosureManager.test.tsx`

**Interfaces:**
- Consumes: `addDocumentEnclosure`, `deleteDocumentEnclosure`, `listDocumentEnclosures`, `fetchUploadPolicy` (Phase 5 Task 5.3)
- Produces: `<EnclosureManager documentId canEdit />`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { EnclosureManager } from '../EnclosureManager';
import * as api from '../../services/enhanced-api';

vi.mock('../../services/enhanced-api');

const enclosure = {
  id: 'enc-1',
  filename: 'site-photos.pdf',
  filetype: 'application/pdf',
  filesize: 2048,
};

describe('EnclosureManager', () => {
  beforeEach(() => {
    vi.mocked(api.listDocumentEnclosures).mockResolvedValue([enclosure]);
    vi.mocked(api.fetchUploadPolicy).mockResolvedValue(null);
  });

  it('lists existing enclosures', async () => {
    render(<EnclosureManager documentId="doc-1" canEdit />);

    expect(await screen.findByText('site-photos.pdf')).toBeInTheDocument();
  });

  it('uploads a selected file', async () => {
    vi.mocked(api.addDocumentEnclosure).mockResolvedValue(enclosure);
    render(<EnclosureManager documentId="doc-1" canEdit />);
    await screen.findByText('site-photos.pdf');

    const file = new File(['x'], 'new.pdf', { type: 'application/pdf' });
    await userEvent.upload(screen.getByLabelText(/add enclosure/i), file);

    await waitFor(() => expect(api.addDocumentEnclosure).toHaveBeenCalledWith('doc-1', file));
  });

  it('hides mutations when the user cannot edit', async () => {
    render(<EnclosureManager documentId="doc-1" canEdit={false} />);
    await screen.findByText('site-photos.pdf');

    expect(screen.queryByLabelText(/add enclosure/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
  });

  it('surfaces an upload failure instead of failing silently', async () => {
    vi.mocked(api.addDocumentEnclosure).mockRejectedValue(new Error('415'));
    render(<EnclosureManager documentId="doc-1" canEdit />);
    await screen.findByText('site-photos.pdf');

    const file = new File(['x'], 'bad.gif', { type: 'image/gif' });
    await userEvent.upload(screen.getByLabelText(/add enclosure/i), file);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be uploaded/i);
  });

  it('drives the accept list from the served upload policy', async () => {
    vi.mocked(api.fetchUploadPolicy).mockResolvedValue({
      document: { mimes: [], extensions: ['.pdf'], max_size_mb: 100 },
      enclosure: { mimes: [], extensions: ['.pdf', '.zip'], max_size_mb: 100 },
      contract: { mimes: [], extensions: ['.pdf'], max_size_mb: 100 },
      version: { mimes: [], extensions: ['.pdf'], max_size_mb: 100 },
    });
    render(<EnclosureManager documentId="doc-1" canEdit />);

    await waitFor(() =>
      expect(screen.getByLabelText(/add enclosure/i)).toHaveAttribute('accept', '.pdf,.zip'),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd client && npm run test -- EnclosureManager`
Expected: FAIL — cannot resolve `../EnclosureManager`

- [ ] **Step 3: Write the component**

```tsx
import { useCallback, useEffect, useState } from 'react';

import {
  addDocumentEnclosure,
  deleteDocumentEnclosure,
  fetchUploadPolicy,
  listDocumentEnclosures,
  FALLBACK_UPLOAD_EXTENSIONS,
  type UploadPolicy,
} from '../services/enhanced-api';

interface Enclosure {
  id: string;
  filename: string;
  filetype: string;
  filesize: number;
}

interface Props {
  documentId: string;
  canEdit: boolean;
}

export function EnclosureManager({ documentId, canEdit }: Props) {
  const [enclosures, setEnclosures] = useState<Enclosure[]>([]);
  const [policy, setPolicy] = useState<UploadPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setEnclosures(await listDocumentEnclosures(documentId));
  }, [documentId]);

  useEffect(() => {
    void refresh();
    void fetchUploadPolicy().then(setPolicy);
  }, [refresh]);

  const accept = (policy?.enclosure.extensions ?? FALLBACK_UPLOAD_EXTENSIONS).join(',');

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setBusy(true);
    setError(null);
    try {
      await addDocumentEnclosure(documentId, file);
      await refresh();
    } catch {
      setError(`"${file.name}" could not be uploaded. Check the file type and size.`);
    } finally {
      setBusy(false);
      event.target.value = '';
    }
  }

  async function handleDelete(enclosureId: string) {
    setBusy(true);
    setError(null);
    try {
      await deleteDocumentEnclosure(documentId, enclosureId);
      await refresh();
    } catch {
      setError('The enclosure could not be removed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="enclosures-heading">
      <h3 id="enclosures-heading">Enclosures</h3>

      {error && <div role="alert">{error}</div>}

      <ul>
        {enclosures.map((item) => (
          <li key={item.id}>
            <span>{item.filename}</span>
            {canEdit && (
              <button type="button" onClick={() => void handleDelete(item.id)} disabled={busy}>
                Delete
              </button>
            )}
          </li>
        ))}
      </ul>

      {canEdit && (
        <label>
          Add enclosure
          <input
            type="file"
            aria-label="Add enclosure"
            accept={accept}
            onChange={(event) => void handleUpload(event)}
            disabled={busy}
          />
        </label>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd client && npm run test -- EnclosureManager`
Expected: PASS — 5 passed

- [ ] **Step 5: Mount it on `ShareDocumentPage`**

Replace the list-only enclosure call at `client/src/pages/ShareDocumentPage.tsx:125` with the component, passing `canEdit` from the existing permission check used elsewhere on that page.

- [ ] **Step 6: Lint, build, and confirm your files are clean**

Run: `cd client && npm run lint && npm run build`
Expected: build succeeds. Then `npx tsc -b` — confirm `EnclosureManager.tsx` and `ShareDocumentPage.tsx` are **absent** from the output.

- [ ] **Step 7: Commit**

```bash
git add projectDMS/client/src/components/EnclosureManager.tsx projectDMS/client/src/components/__tests__/EnclosureManager.test.tsx projectDMS/client/src/pages/ShareDocumentPage.tsx
git commit -m "feat: wire the enclosure upload and delete UI"
```

---

## Phase 9 — Progress-photo extraction (deterministic)

**The constraint that shapes this whole phase:** companion §3.9 measured **3–6 embedded images on every page** of a real contractor claim — including all seven text pages. None are progress photographs. "Extract every image" yields ~30 junk assets from one 9-page letter. The funnel must discard aggressively and for free.

### Task 9.1: Candidate enumeration with measured signals

**Files:**
- Create: `backend/rbac_backend/services/assets/__init__.py`
- Create: `backend/rbac_backend/services/assets/models.py`
- Create: `backend/rbac_backend/services/assets/candidates.py`
- Test: `backend/rbac_backend/tests/test_asset_candidates.py`
- Modify: `backend/rbac_backend/tests/fixtures/pdf_builders.py` (add image fixtures)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class AssetDecision(str, Enum)` — `ACCEPTED`, `REJECTED`, `REVIEW_REQUIRED`
  - `class RejectReason(str, Enum)` — `BOILERPLATE_REPEAT`, `BILEVEL`, `TOO_SMALL`, `LOW_AREA_FRACTION`, `ASPECT_RATIO`, `FLAT_COLOUR`, `PAGE_SCAN`
  - `@dataclass AssetSignals(page_number, bbox, placed_area_fraction, pixel_width, pixel_height, bits, colorspace, filter_name, is_imagemask, entropy, xobject_sha256, vertical_position)`
  - `@dataclass AssetCandidate(signals, decision, reasons, score, confidence)`
  - `enumerate_candidates(source: Path, page_number: int, page_class: PageClass) -> list[AssetSignals]`

**All signals verified reachable (F3).** `filter_name` comes from `image["stream"].attrs["Filter"]`, **not** from the pdfplumber dict.

- [ ] **Step 1: Add image fixture builders**

Append to `backend/rbac_backend/tests/fixtures/pdf_builders.py`:

```python
def _image_xobject(
    pdf: "pikepdf.Pdf",
    *,
    payload: bytes,
    width: int,
    height: int,
    colorspace: "pikepdf.Name",
    bits: int,
    filter_name: "pikepdf.Name | None",
) -> "pikepdf.Stream":
    stream = pikepdf.Stream(pdf, payload)
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width = width
    stream.Height = height
    stream.ColorSpace = colorspace
    stream.BitsPerComponent = bits
    if filter_name is not None:
        stream.Filter = filter_name
    return stream


def _jpeg_bytes(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def build_photo_pdf(path: Path) -> Path:
    """Two pages: a genuine site photo, a stamp, a logo repeated on both pages.

    Golden expectation: exactly ONE accepted asset (the photo on page 1).
    """
    pdf = pikepdf.new()
    pdf.trailer.ID = [_FIXED_ID, _FIXED_ID]

    logo_payload = _jpeg_bytes((120, 60), (10, 40, 90))

    for page_number in (1, 2):
        photo = _image_xobject(
            pdf,
            payload=_jpeg_bytes((1200, 900), (110 + page_number, 90, 60)),
            width=1200,
            height=900,
            colorspace=pikepdf.Name.DeviceRGB,
            bits=8,
            filter_name=pikepdf.Name.DCTDecode,
        )
        stamp = _image_xobject(
            pdf,
            payload=b"\x00" * 32,
            width=24,
            height=24,
            colorspace=pikepdf.Name.DeviceGray,
            bits=1,
            filter_name=pikepdf.Name.FlateDecode,
        )
        # Identical bytes on both pages - the boilerplate signal.
        logo = _image_xobject(
            pdf,
            payload=logo_payload,
            width=120,
            height=60,
            colorspace=pikepdf.Name.DeviceRGB,
            bits=8,
            filter_name=pikepdf.Name.DCTDecode,
        )

        page = pdf.add_blank_page(page_size=(595, 842))
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im0=photo, Im1=stamp, Im2=logo)
        )
        content = (
            b"q 360 0 0 270 100 400 cm /Im0 Do Q "      # photo: large, central
            b"q 30 0 0 30 60 60 cm /Im1 Do Q "          # stamp: small, bottom
            b"q 120 0 0 40 60 780 cm /Im2 Do Q"         # logo: top band
        )
        if page_number == 2:
            # Page 2's "photo" is small - below the area floor.
            content = (
                b"q 60 0 0 45 100 400 cm /Im0 Do Q "
                b"q 30 0 0 30 60 60 cm /Im1 Do Q "
                b"q 120 0 0 40 60 780 cm /Im2 Do Q"
            )
        page.Contents = pdf.make_stream(content)

    return _save(pdf, path)


def build_full_page_scan_pdf(path: Path) -> Path:
    """One page covered entirely by a single raster - a page scan, not an asset."""
    pdf = pikepdf.new()
    pdf.trailer.ID = [_FIXED_ID, _FIXED_ID]

    scan = _image_xobject(
        pdf,
        payload=_jpeg_bytes((1700, 2400), (240, 240, 235)),
        width=1700,
        height=2400,
        colorspace=pikepdf.Name.DeviceRGB,
        bits=8,
        filter_name=pikepdf.Name.DCTDecode,
    )
    page = pdf.add_blank_page(page_size=(595, 842))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=scan))
    page.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im0 Do Q")

    return _save(pdf, path)
```

- [ ] **Step 2: Write the failing test**

```python
"""Enumerate image XObjects with every signal the classifier needs.

Signals verified reachable in backend/.venv on 2026-08-14: srcsize, bits,
colorspace, placement bbox, imagemask, stream.attrs["Filter"], and a SHA-256
over stream.get_rawdata(). Filter is NOT in the pdfplumber dict - it comes from
the pdfminer stream's attrs.
"""

from __future__ import annotations

from pathlib import Path

from rbac_backend.services.assets.candidates import enumerate_candidates
from rbac_backend.services.extraction.models import PageClass
from rbac_backend.tests.fixtures.pdf_builders import (
    build_full_page_scan_pdf,
    build_photo_pdf,
)


def test_every_image_on_the_page_is_enumerated(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)

    assert len(signals) == 3


def test_pixel_dimensions_come_from_srcsize(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)
    photo = max(signals, key=lambda s: s.pixel_width * s.pixel_height)

    assert photo.pixel_width == 1200
    assert photo.pixel_height == 900


def test_encoding_is_read_from_the_stream_attrs(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)
    filters = {s.filter_name for s in signals}

    assert "DCTDecode" in filters
    assert "FlateDecode" in filters


def test_bit_depth_and_colourspace_are_captured(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)
    bilevel = [s for s in signals if s.bits == 1]

    assert bilevel
    assert "DeviceGray" in bilevel[0].colorspace


def test_placed_area_fraction_is_relative_to_the_page(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)
    photo = max(signals, key=lambda s: s.placed_area_fraction)

    # 360x270 points on a 595x842 page = ~19.4%
    assert 0.15 < photo.placed_area_fraction < 0.25


def test_identical_images_hash_identically_across_pages(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    page1 = {s.xobject_sha256 for s in enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)}
    page2 = {s.xobject_sha256 for s in enumerate_candidates(source, 2, PageClass.MIXED_CONTENT)}

    # The logo is byte-identical on both pages; the photos are not.
    assert len(page1 & page2) >= 1


def test_vertical_position_is_normalised_top_to_bottom(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")

    signals = enumerate_candidates(source, 1, PageClass.MIXED_CONTENT)
    positions = sorted(s.vertical_position for s in signals)

    assert 0.0 <= positions[0] <= 1.0
    assert 0.0 <= positions[-1] <= 1.0


def test_full_page_scan_reports_near_total_coverage(tmp_path: Path) -> None:
    source = build_full_page_scan_pdf(tmp_path / "scan.pdf")

    signals = enumerate_candidates(source, 1, PageClass.SCANNED_IMAGE)

    assert len(signals) == 1
    assert signals[0].placed_area_fraction > 0.95


def test_page_without_images_returns_nothing(tmp_path: Path) -> None:
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    source = build_text_pdf(tmp_path / "text.pdf", pages=1)

    assert enumerate_candidates(source, 1, PageClass.TEXT_NATIVE) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_candidates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rbac_backend.services.assets'`

- [ ] **Step 4: Write `assets/models.py`**

```python
"""Types for progress-photo asset extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


class AssetDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVIEW_REQUIRED = "review_required"


class RejectReason(str, Enum):
    BOILERPLATE_REPEAT = "boilerplate_repeat"
    BILEVEL = "bilevel"
    TOO_SMALL = "too_small"
    LOW_AREA_FRACTION = "low_area_fraction"
    ASPECT_RATIO = "aspect_ratio"
    FLAT_COLOUR = "flat_colour"
    PAGE_SCAN = "page_scan"


@dataclass(frozen=True)
class AssetSignals:
    page_number: int
    bbox: Tuple[float, float, float, float]
    placed_area_fraction: float
    pixel_width: int
    pixel_height: int
    bits: int
    colorspace: str
    filter_name: str
    is_imagemask: bool
    entropy: float
    xobject_sha256: str
    vertical_position: float

    @property
    def aspect_ratio(self) -> float:
        return self.pixel_width / self.pixel_height if self.pixel_height else 0.0

    @property
    def pixel_area(self) -> int:
        return self.pixel_width * self.pixel_height


@dataclass
class AssetCandidate:
    signals: AssetSignals
    decision: AssetDecision
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.0
```

- [ ] **Step 5: Write `assets/candidates.py`**

```python
"""Enumerate a page's image XObjects with every signal the classifier needs.

Measured 2026-08-14 in backend/.venv: pdfplumber's page.images entries carry
srcsize, bits, colorspace, imagemask, and the placement rectangle, while the
encoding filter lives on the underlying pdfminer stream's attrs - NOT in the
pdfplumber dict. The raw stream bytes give a stable per-image identity, which
is what makes cross-page boilerplate detection possible.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import math
from pathlib import Path
from typing import Any, List

from ..extraction.models import PageClass
from .models import AssetSignals

logger = logging.getLogger(__name__)


def _entropy(payload: bytes) -> float:
    """Shannon entropy in bits per byte.

    A weak signal only: a flat-colour JPEG measured 2.664 bits/byte, so this
    must never be used as a hard gate. See classifier weights.
    """
    if not payload:
        return 0.0
    counts = collections.Counter(payload)
    total = len(payload)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _colorspace_name(value: Any) -> str:
    try:
        if isinstance(value, (list, tuple)):
            return " ".join(str(item).strip("/'") for item in value)
        return str(value).strip("/'")
    except Exception:
        return ""


def _filter_name(stream: Any) -> str:
    attrs = getattr(stream, "attrs", None) or {}
    raw = attrs.get("Filter")
    if isinstance(raw, (list, tuple)):
        return " ".join(str(item).strip("/'") for item in raw)
    return str(raw).strip("/'") if raw is not None else ""


def enumerate_candidates(
    source: Path, page_number: int, page_class: PageClass
) -> List[AssetSignals]:
    """Return one AssetSignals per image XObject placed on the page."""
    import pdfplumber

    signals: List[AssetSignals] = []

    try:
        with pdfplumber.open(source) as pdf:
            if not 1 <= page_number <= len(pdf.pages):
                return []
            page = pdf.pages[page_number - 1]
            page_area = float(page.width) * float(page.height)

            for image in page.images or []:
                try:
                    x0 = float(image["x0"])
                    top = float(image["top"])
                    x1 = float(image["x1"])
                    bottom = float(image["bottom"])
                except (KeyError, TypeError, ValueError):
                    continue

                placed_area = abs((x1 - x0) * (bottom - top))
                src_width, src_height = image.get("srcsize") or (0, 0)
                stream = image.get("stream")

                payload = b""
                if stream is not None and hasattr(stream, "get_rawdata"):
                    try:
                        payload = stream.get_rawdata() or b""
                    except Exception as exc:  # pragma: no cover - damaged stream
                        logger.warning(
                            "Could not read image stream on page %s: %s", page_number, exc
                        )

                signals.append(
                    AssetSignals(
                        page_number=page_number,
                        bbox=(x0, top, x1, bottom),
                        placed_area_fraction=(
                            min(1.0, placed_area / page_area) if page_area else 0.0
                        ),
                        pixel_width=int(src_width or 0),
                        pixel_height=int(src_height or 0),
                        bits=int(image.get("bits") or 0),
                        colorspace=_colorspace_name(image.get("colorspace")),
                        filter_name=_filter_name(stream),
                        is_imagemask=bool(image.get("imagemask")),
                        entropy=_entropy(payload),
                        xobject_sha256=hashlib.sha256(payload).hexdigest(),
                        vertical_position=(
                            top / float(page.height) if page.height else 0.0
                        ),
                    )
                )
    except Exception as exc:
        logger.warning("Could not enumerate images on page %s: %s", page_number, exc)
        return []

    return signals
```

Create `backend/rbac_backend/services/assets/__init__.py`:

```python
"""Progress-photo asset extraction."""
```

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_candidates.py -q`
Expected: PASS — 9 passed

- [ ] **Step 7: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/assets/ projectDMS/backend/rbac_backend/tests/fixtures/pdf_builders.py projectDMS/backend/rbac_backend/tests/test_asset_candidates.py
git commit -m "feat: enumerate image XObject candidates with classifier signals"
```

---

### Task 9.2: Cross-page boilerplate detection

The cheapest and strongest discriminator: a letterhead, logo, or stamp template repeats byte-identically across pages; a site photograph does not.

**Files:**
- Create: `backend/rbac_backend/services/assets/boilerplate.py`
- Test: `backend/rbac_backend/tests/test_asset_boilerplate.py`

**Interfaces:**
- Consumes: `AssetSignals` (9.1)
- Produces: `BOILERPLATE_MIN_PAGES = 2`, `BOILERPLATE_PAGE_FRACTION = 0.30`, `find_boilerplate_hashes(signals_by_page: Mapping[int, Sequence[AssetSignals]]) -> set[str]`

- [ ] **Step 1: Write the failing test**

```python
"""Boilerplate detection: an image repeated across pages is not a photograph."""

from __future__ import annotations

from rbac_backend.services.assets.boilerplate import (
    BOILERPLATE_MIN_PAGES,
    find_boilerplate_hashes,
)
from rbac_backend.services.assets.models import AssetSignals


def _signal(page: int, digest: str) -> AssetSignals:
    return AssetSignals(
        page_number=page,
        bbox=(0.0, 0.0, 100.0, 100.0),
        placed_area_fraction=0.1,
        pixel_width=400,
        pixel_height=300,
        bits=8,
        colorspace="DeviceRGB",
        filter_name="DCTDecode",
        is_imagemask=False,
        entropy=6.0,
        xobject_sha256=digest,
        vertical_position=0.5,
    )


def test_image_on_two_pages_is_boilerplate() -> None:
    by_page = {1: [_signal(1, "logo")], 2: [_signal(2, "logo")]}

    assert find_boilerplate_hashes(by_page) == {"logo"}


def test_image_on_one_page_only_is_not_boilerplate() -> None:
    by_page = {1: [_signal(1, "photo-a")], 2: [_signal(2, "photo-b")]}

    assert find_boilerplate_hashes(by_page) == set()


def test_threshold_is_two_pages() -> None:
    assert BOILERPLATE_MIN_PAGES == 2


def test_repeats_within_one_page_do_not_count() -> None:
    # The same decorative rule twice on one page is not cross-page boilerplate.
    by_page = {1: [_signal(1, "rule"), _signal(1, "rule")]}

    assert find_boilerplate_hashes(by_page) == set()


def test_single_page_document_has_no_boilerplate() -> None:
    by_page = {1: [_signal(1, "anything"), _signal(1, "other")]}

    assert find_boilerplate_hashes(by_page) == set()


def test_mixed_document_flags_only_the_repeated_image() -> None:
    by_page = {
        1: [_signal(1, "letterhead"), _signal(1, "photo-1")],
        2: [_signal(2, "letterhead"), _signal(2, "photo-2")],
        3: [_signal(3, "letterhead"), _signal(3, "photo-3")],
    }

    assert find_boilerplate_hashes(by_page) == {"letterhead"}


def test_empty_input_is_safe() -> None:
    assert find_boilerplate_hashes({}) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_boilerplate.py -q`
Expected: FAIL — `ModuleNotFoundError` for `assets.boilerplate`

- [ ] **Step 3: Write the implementation**

```python
"""Identify images that repeat across pages.

A letterhead, logo, stamp template, or signature block is placed on many pages
from the same XObject bytes. A site photograph appears once. Comparing SHA-256
digests across pages is therefore the cheapest strong discriminator available,
and it runs before any scoring.

Repeats WITHIN a single page do not count: a decorative rule drawn twice on one
page says nothing about whether it is boilerplate.
"""

from __future__ import annotations

import collections
from typing import Mapping, Sequence, Set

from .models import AssetSignals

BOILERPLATE_MIN_PAGES = 2
BOILERPLATE_PAGE_FRACTION = 0.30


def find_boilerplate_hashes(
    signals_by_page: Mapping[int, Sequence[AssetSignals]],
) -> Set[str]:
    """Return the XObject digests that appear on two or more distinct pages."""
    pages_by_hash: dict[str, set[int]] = collections.defaultdict(set)

    for page_number, signals in signals_by_page.items():
        for signal in signals:
            if signal.xobject_sha256:
                pages_by_hash[signal.xobject_sha256].add(page_number)

    return {
        digest
        for digest, pages in pages_by_hash.items()
        if len(pages) >= BOILERPLATE_MIN_PAGES
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_boilerplate.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/assets/boilerplate.py projectDMS/backend/rbac_backend/tests/test_asset_boilerplate.py
git commit -m "feat: detect cross-page boilerplate images by stream hash"
```

---

### Task 9.3: The deterministic classifier

**Files:**
- Create: `backend/rbac_backend/services/assets/classifier.py`
- Create: `backend/rbac_backend/tests/fixtures/golden_photo_classification.json`
- Test: `backend/rbac_backend/tests/test_asset_classifier.py`

**Interfaces:**
- Consumes: `AssetSignals`, `AssetCandidate`, `AssetDecision`, `RejectReason` (9.1), `find_boilerplate_hashes` (9.2)
- Produces: `class PhotoClassifier(boilerplate_hashes: set[str])` with `classify(signals, page_class) -> AssetCandidate`; module constants `MIN_PIXEL_AREA`, `MIN_AREA_FRACTION`, `ASPECT_RATIO_RANGE`, `PAGE_SCAN_COVERAGE`, `ACCEPT_SCORE`, `REVIEW_SCORE`, `WEIGHTS`

**Per F3:** entropy carries a **small** weight and never gates. A flat-colour JPEG measured 2.664 bits/byte — treating low entropy as disqualifying would reject real photographs of uniform surfaces (fresh concrete, sky, painted steel), which is exactly the subject matter of site photos.

- [ ] **Step 1: Write the golden expectations file**

```json
{
  "_comment": "Phase 9 golden classification. Fixture #1 (build_mixed_pdf) must yield ZERO accepted assets; build_photo_pdf must yield exactly one. Thresholds are tuned in Task 9.4 against real photographs, not guessed here.",
  "mixed_pdf_expected_accepted": 0,
  "photo_pdf_expected_accepted": 1,
  "full_page_scan_expected_accepted": 0,
  "hard_reject_reasons": [
    "boilerplate_repeat",
    "bilevel",
    "too_small",
    "low_area_fraction",
    "aspect_ratio",
    "flat_colour",
    "page_scan"
  ],
  "entropy_note": "Measured 2.664 bits/byte for a flat-colour RGB JPEG in backend/.venv on 2026-08-14. Entropy is a weak positive signal with a small weight, never a gate."
}
```

- [ ] **Step 2: Write the failing test**

```python
"""The deterministic photo classifier: reject hard, score the rest, band the doubt."""

from __future__ import annotations

import json
from pathlib import Path

from rbac_backend.services.assets.classifier import (
    ACCEPT_SCORE,
    REVIEW_SCORE,
    PhotoClassifier,
)
from rbac_backend.services.assets.models import (
    AssetDecision,
    AssetSignals,
    RejectReason,
)
from rbac_backend.services.extraction.models import PageClass

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_photo_classification.json").read_text(
        encoding="utf-8"
    )
)


def _photo(**overrides: object) -> AssetSignals:
    defaults = dict(
        page_number=1,
        bbox=(100.0, 400.0, 460.0, 670.0),
        placed_area_fraction=0.19,
        pixel_width=1200,
        pixel_height=900,
        bits=8,
        colorspace="DeviceRGB",
        filter_name="DCTDecode",
        is_imagemask=False,
        entropy=7.4,
        xobject_sha256="photo-hash",
        vertical_position=0.48,
    )
    defaults.update(overrides)
    return AssetSignals(**defaults)  # type: ignore[arg-type]


def _classifier(boilerplate: set[str] | None = None) -> PhotoClassifier:
    return PhotoClassifier(boilerplate_hashes=boilerplate or set())


def test_a_plausible_site_photograph_is_accepted() -> None:
    result = _classifier().classify(_photo(), PageClass.MIXED_CONTENT)

    assert result.decision is AssetDecision.ACCEPTED
    assert result.score >= ACCEPT_SCORE


def test_boilerplate_is_rejected_regardless_of_every_other_signal() -> None:
    # A logo can be a large, colourful JPEG. Repetition is what betrays it.
    result = _classifier({"photo-hash"}).classify(_photo(), PageClass.MIXED_CONTENT)

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.BOILERPLATE_REPEAT.value in result.reasons


def test_bilevel_images_are_rejected_as_signatures_or_stamps() -> None:
    result = _classifier().classify(
        _photo(bits=1, colorspace="DeviceGray", filter_name="CCITTFaxDecode"),
        PageClass.MIXED_CONTENT,
    )

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.BILEVEL.value in result.reasons


def test_image_masks_are_rejected() -> None:
    result = _classifier().classify(_photo(is_imagemask=True), PageClass.MIXED_CONTENT)

    assert result.decision is AssetDecision.REJECTED


def test_small_images_are_rejected() -> None:
    result = _classifier().classify(
        _photo(pixel_width=120, pixel_height=90), PageClass.MIXED_CONTENT
    )

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.TOO_SMALL.value in result.reasons


def test_images_occupying_a_sliver_of_the_page_are_rejected() -> None:
    result = _classifier().classify(
        _photo(placed_area_fraction=0.01), PageClass.MIXED_CONTENT
    )

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.LOW_AREA_FRACTION.value in result.reasons


def test_letterhead_strip_aspect_ratio_is_rejected() -> None:
    result = _classifier().classify(
        _photo(pixel_width=2400, pixel_height=200), PageClass.MIXED_CONTENT
    )

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.ASPECT_RATIO.value in result.reasons


def test_full_page_raster_on_a_scanned_page_is_a_page_scan_not_an_asset() -> None:
    result = _classifier().classify(
        _photo(placed_area_fraction=0.98), PageClass.SCANNED_IMAGE
    )

    assert result.decision is AssetDecision.REJECTED
    assert RejectReason.PAGE_SCAN.value in result.reasons


def test_low_entropy_alone_never_rejects_a_photograph() -> None:
    # Measured: a flat-colour RGB JPEG is 2.664 bits/byte. Fresh concrete, sky,
    # and painted steel are exactly this, and are exactly what site photos show.
    result = _classifier().classify(_photo(entropy=2.664), PageClass.MIXED_CONTENT)

    assert result.decision is not AssetDecision.REJECTED


def test_a_borderline_image_lands_in_the_review_band() -> None:
    # Non-JPEG, greyscale, modest size: plausible either way.
    result = _classifier().classify(
        _photo(filter_name="FlateDecode", colorspace="DeviceGray", entropy=5.0,
               pixel_width=700, pixel_height=520, placed_area_fraction=0.10),
        PageClass.MIXED_CONTENT,
    )

    assert result.decision is AssetDecision.REVIEW_REQUIRED
    assert REVIEW_SCORE <= result.score < ACCEPT_SCORE


def test_top_of_page_placement_is_demoted() -> None:
    top = _classifier().classify(_photo(vertical_position=0.03), PageClass.MIXED_CONTENT)
    middle = _classifier().classify(_photo(vertical_position=0.48), PageClass.MIXED_CONTENT)

    assert top.score < middle.score


def test_bottom_of_page_placement_is_demoted() -> None:
    bottom = _classifier().classify(_photo(vertical_position=0.92), PageClass.MIXED_CONTENT)
    middle = _classifier().classify(_photo(vertical_position=0.48), PageClass.MIXED_CONTENT)

    assert bottom.score < middle.score


def test_every_decision_records_a_reason() -> None:
    for signals in (_photo(), _photo(bits=1), _photo(placed_area_fraction=0.005)):
        result = _classifier().classify(signals, PageClass.MIXED_CONTENT)
        assert result.reasons


def test_reject_reason_vocabulary_matches_the_golden_file() -> None:
    assert sorted(GOLDEN["hard_reject_reasons"]) == sorted(
        reason.value for reason in RejectReason
    )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError` for `assets.classifier`

- [ ] **Step 4: Write the implementation**

```python
"""Decide whether an image is a substantive site or progress photograph.

The funnel is: hard rejects (free) -> weighted score (free) -> uncertainty band
-> Vision (paid, Phase 10). Everything that can be discarded for free must be,
because the measured baseline is 3-6 images on every page of a real claim, none
of them photographs.

Deliberate non-signal: entropy. A flat-colour RGB JPEG measured 2.664 bits/byte
in backend/.venv on 2026-08-14. Fresh concrete, sky, and painted steel are
low-entropy subjects and are exactly what site photographs show, so entropy
carries a small positive weight and can never reject on its own.
"""

from __future__ import annotations

from typing import List, Set

from ..extraction.models import PageClass
from .models import AssetCandidate, AssetDecision, AssetSignals, RejectReason

MIN_PIXEL_AREA = 300 * 300
MIN_AREA_FRACTION = 0.05
ASPECT_RATIO_RANGE = (0.3, 3.5)
PAGE_SCAN_COVERAGE = 0.85

LETTERHEAD_BAND = 0.12
SIGNATURE_BAND = 0.80

ACCEPT_SCORE = 0.60
REVIEW_SCORE = 0.30

WEIGHTS = {
    "photographic_encoding": 0.30,   # DCTDecode / JPXDecode
    "colour_depth": 0.25,            # RGB or ICC, 8 bits per component
    "resolution": 0.20,              # >= 640x480 native pixels
    "page_presence": 0.15,           # occupies a meaningful share of the page
    "entropy": 0.10,                 # weak: see module docstring
}

_PHOTO_FILTERS = {"DCTDecode", "JPXDecode"}
_COLOUR_SPACES = {"DeviceRGB", "ICCBased", "DeviceCMYK"}


class PhotoClassifier:
    def __init__(self, *, boilerplate_hashes: Set[str]) -> None:
        self.boilerplate_hashes = boilerplate_hashes

    def classify(self, signals: AssetSignals, page_class: PageClass) -> AssetCandidate:
        rejects = self._hard_rejects(signals, page_class)
        if rejects:
            return AssetCandidate(
                signals=signals,
                decision=AssetDecision.REJECTED,
                reasons=rejects,
                score=0.0,
                confidence=1.0,
            )

        score, reasons = self._score(signals)

        if score >= ACCEPT_SCORE:
            decision = AssetDecision.ACCEPTED
        elif score >= REVIEW_SCORE:
            decision = AssetDecision.REVIEW_REQUIRED
        else:
            decision = AssetDecision.REJECTED
            reasons.append("score below the review band")

        return AssetCandidate(
            signals=signals,
            decision=decision,
            reasons=reasons,
            score=score,
            confidence=abs(score - REVIEW_SCORE),
        )

    def _hard_rejects(self, signals: AssetSignals, page_class: PageClass) -> List[str]:
        reasons: List[str] = []

        if signals.xobject_sha256 in self.boilerplate_hashes:
            reasons.append(RejectReason.BOILERPLATE_REPEAT.value)

        if signals.is_imagemask or signals.bits == 1 or signals.filter_name in {
            "CCITTFaxDecode",
            "JBIG2Decode",
        }:
            reasons.append(RejectReason.BILEVEL.value)

        if signals.pixel_area < MIN_PIXEL_AREA:
            reasons.append(RejectReason.TOO_SMALL.value)

        if signals.placed_area_fraction < MIN_AREA_FRACTION:
            reasons.append(RejectReason.LOW_AREA_FRACTION.value)

        ratio = signals.aspect_ratio
        if ratio and not (ASPECT_RATIO_RANGE[0] <= ratio <= ASPECT_RATIO_RANGE[1]):
            reasons.append(RejectReason.ASPECT_RATIO.value)

        if (
            signals.placed_area_fraction >= PAGE_SCAN_COVERAGE
            and page_class is PageClass.SCANNED_IMAGE
        ):
            reasons.append(RejectReason.PAGE_SCAN.value)

        return reasons

    @staticmethod
    def _score(signals: AssetSignals) -> tuple[float, List[str]]:
        score = 0.0
        reasons: List[str] = []

        if signals.filter_name in _PHOTO_FILTERS:
            score += WEIGHTS["photographic_encoding"]
            reasons.append(f"photographic encoding ({signals.filter_name})")

        if signals.bits >= 8 and any(
            space in signals.colorspace for space in _COLOUR_SPACES
        ):
            score += WEIGHTS["colour_depth"]
            reasons.append(f"continuous-tone colour ({signals.colorspace}, {signals.bits}bpc)")

        if signals.pixel_width >= 640 and signals.pixel_height >= 480:
            score += WEIGHTS["resolution"]
            reasons.append(
                f"photographic resolution ({signals.pixel_width}x{signals.pixel_height})"
            )

        if 0.08 <= signals.placed_area_fraction <= 0.70:
            score += WEIGHTS["page_presence"]
            reasons.append(f"occupies {signals.placed_area_fraction:.0%} of the page")

        if signals.entropy >= 7.0:
            score += WEIGHTS["entropy"]
            reasons.append(f"high byte entropy ({signals.entropy:.2f})")

        # Placement demotion: letterheads sit at the top, signatures at the
        # bottom. Never disqualifying on its own - a site photo can be anywhere.
        if signals.vertical_position <= LETTERHEAD_BAND:
            score -= 0.15
            reasons.append("placed in the letterhead band")
        elif signals.vertical_position >= SIGNATURE_BAND:
            score -= 0.15
            reasons.append("placed in the signature band")

        return max(0.0, round(score, 4)), reasons
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_classifier.py -q`
Expected: PASS — 14 passed

- [ ] **Step 6: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/assets/classifier.py projectDMS/backend/rbac_backend/tests/fixtures/golden_photo_classification.json projectDMS/backend/rbac_backend/tests/test_asset_classifier.py
git commit -m "feat: add deterministic progress-photo classifier"
```

---

### Task 9.4: End-to-end extraction, calibration, and asset persistence

**Files:**
- Create: `backend/rbac_backend/services/assets/extractor.py`
- Create: `backend/rbac_backend/services/assets/store.py`
- Test: `backend/rbac_backend/tests/test_asset_extraction_end_to_end.py`

**Interfaces:**
- Consumes: everything in 9.1–9.3, `PageRasterizer` (Phase 7 Task 7.1), `FileObjectService`
- Produces:
  - `class ImageAssetExtractor(rasterizer)` with `async def extract(source, page_classes: Mapping[int, PageClass]) -> list[AssetCandidate]`
  - `ASSET_CANDIDATES = "document_image_assets"`
  - `class AssetStore(db, file_object_service)` with `async def persist(document_id, organization_id, project_id, source, candidates) -> list[dict]`

**Visibility rule:** only `ACCEPTED` candidates get a `FileObject`. `REVIEW_REQUIRED` persists **metadata only** — page, bbox, signals, reasons — per the spec's open question 1 default, because a reviewer can open the source page from the bbox and adding byte retention later is cheaper than removing it.

- [ ] **Step 1: Write the failing test**

```python
"""Extraction end to end, against the golden expectations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rbac_backend.services.assets.extractor import ImageAssetExtractor
from rbac_backend.services.assets.models import AssetDecision
from rbac_backend.services.assets.store import ASSET_CANDIDATES, AssetStore
from rbac_backend.services.extraction.models import PageClass
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import (
    build_full_page_scan_pdf,
    build_mixed_pdf,
    build_photo_pdf,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_photo_classification.json").read_text(
        encoding="utf-8"
    )
)


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def delete_many(self, query: dict) -> None:
        self.inserted.clear()

    async def insert_many(self, documents: list[dict[str, Any]]) -> None:
        self.inserted.extend(documents)


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


class _FakeFileObjectService:
    def __init__(self) -> None:
        self.stored: list[dict[str, Any]] = []

    async def store_bytes(self, **kwargs: Any) -> dict[str, Any]:
        self.stored.append(kwargs)
        return {"file_object_id": f"fo-{len(self.stored)}", "storage_key": "k"}


def _extractor() -> ImageAssetExtractor:
    return ImageAssetExtractor(rasterizer=PageRasterizer(dpi=72))


async def test_fixture_one_yields_zero_accepted_assets(tmp_path: Path) -> None:
    # The whole economic argument: a real 9-page claim has ~30 embedded images
    # and none of them are progress photographs.
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    page_classes = {n: PageClass.TEXT_NATIVE for n in range(1, 10)}

    candidates = await _extractor().extract(source, page_classes)
    accepted = [c for c in candidates if c.decision is AssetDecision.ACCEPTED]

    assert len(accepted) == GOLDEN["mixed_pdf_expected_accepted"]


async def test_photo_fixture_yields_exactly_one_accepted_asset(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    page_classes = {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT}

    candidates = await _extractor().extract(source, page_classes)
    accepted = [c for c in candidates if c.decision is AssetDecision.ACCEPTED]

    assert len(accepted) == GOLDEN["photo_pdf_expected_accepted"]
    assert accepted[0].signals.page_number == 1


async def test_the_repeated_logo_is_rejected_as_boilerplate(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    page_classes = {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT}

    candidates = await _extractor().extract(source, page_classes)
    boilerplate = [c for c in candidates if "boilerplate_repeat" in c.reasons]

    assert len(boilerplate) == 2  # one per page


async def test_a_full_page_scan_is_never_an_asset(tmp_path: Path) -> None:
    source = build_full_page_scan_pdf(tmp_path / "scan.pdf")

    candidates = await _extractor().extract(source, {1: PageClass.SCANNED_IMAGE})
    accepted = [c for c in candidates if c.decision is AssetDecision.ACCEPTED]

    assert len(accepted) == GOLDEN["full_page_scan_expected_accepted"]


async def test_only_accepted_candidates_get_a_file_object(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    page_classes = {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT}
    candidates = await _extractor().extract(source, page_classes)

    db, files = _FakeDb(), _FakeFileObjectService()
    records = await AssetStore(db=db, file_object_service=files).persist(
        document_id="doc-1",
        organization_id="org-1",
        project_id="proj-1",
        source=source,
        candidates=candidates,
    )

    accepted = [r for r in records if r["decision"] == "accepted"]
    assert len(files.stored) == len(accepted)
    assert all(r["file_object_id"] for r in accepted)


async def test_non_accepted_candidates_persist_metadata_without_bytes(
    tmp_path: Path,
) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    page_classes = {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT}
    candidates = await _extractor().extract(source, page_classes)

    db, files = _FakeDb(), _FakeFileObjectService()
    records = await AssetStore(db=db, file_object_service=files).persist(
        document_id="doc-1",
        organization_id="org-1",
        project_id="proj-1",
        source=source,
        candidates=candidates,
    )

    not_accepted = [r for r in records if r["decision"] != "accepted"]
    assert not_accepted
    assert all(r["file_object_id"] is None for r in not_accepted)
    assert all(r["reasons"] for r in not_accepted)


async def test_every_record_carries_page_bbox_and_deep_link(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = await _extractor().extract(source, {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT})

    db, files = _FakeDb(), _FakeFileObjectService()
    records = await AssetStore(db=db, file_object_service=files).persist(
        document_id="doc-1",
        organization_id="org-1",
        project_id="proj-1",
        source=source,
        candidates=candidates,
    )

    for record in records:
        assert record["page_number"] >= 1
        assert len(record["bbox"]) == 4
        assert record["source_pdf_page_link"] == f"document:doc-1#page={record['page_number']}"


async def test_records_are_scoped_and_replace_the_previous_pass(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = await _extractor().extract(source, {1: PageClass.MIXED_CONTENT, 2: PageClass.MIXED_CONTENT})
    db, files = _FakeDb(), _FakeFileObjectService()
    store = AssetStore(db=db, file_object_service=files)

    await store.persist(
        document_id="doc-1", organization_id="org-1", project_id="proj-1",
        source=source, candidates=candidates,
    )
    first_count = len(db[ASSET_CANDIDATES].inserted)
    await store.persist(
        document_id="doc-1", organization_id="org-1", project_id="proj-1",
        source=source, candidates=candidates,
    )

    assert len(db[ASSET_CANDIDATES].inserted) == first_count
    assert all(r["organization_id"] == "org-1" for r in db[ASSET_CANDIDATES].inserted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_extraction_end_to_end.py -q`
Expected: FAIL — `ModuleNotFoundError` for `assets.extractor`

- [ ] **Step 3: Write `assets/extractor.py`**

```python
"""Run the photo funnel across a whole document.

Boilerplate detection needs every page's signals before any page can be
classified, so enumeration happens for the whole document first, then
classification.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from ..extraction.models import PageClass
from ..extraction.rasterizer import PageRasterizer
from .boilerplate import find_boilerplate_hashes
from .candidates import enumerate_candidates
from .classifier import PhotoClassifier
from .models import AssetCandidate, AssetSignals

logger = logging.getLogger(__name__)


class ImageAssetExtractor:
    def __init__(self, *, rasterizer: PageRasterizer) -> None:
        self.rasterizer = rasterizer

    async def extract(
        self, source: Path, page_classes: Mapping[int, PageClass]
    ) -> List[AssetCandidate]:
        signals_by_page: Dict[int, Sequence[AssetSignals]] = {}

        for page_number, page_class in sorted(page_classes.items()):
            signals_by_page[page_number] = await asyncio.to_thread(
                enumerate_candidates, source, page_number, page_class
            )

        classifier = PhotoClassifier(
            boilerplate_hashes=find_boilerplate_hashes(signals_by_page)
        )

        candidates: List[AssetCandidate] = []
        for page_number, signals in signals_by_page.items():
            page_class = page_classes.get(page_number, PageClass.TEXT_NATIVE)
            for signal in signals:
                candidates.append(classifier.classify(signal, page_class))

        logger.info(
            "[document_pipeline] Photo funnel: %s candidates, %s accepted",
            len(candidates),
            sum(1 for c in candidates if c.decision.value == "accepted"),
        )
        return candidates
```

- [ ] **Step 4: Write `assets/store.py`**

```python
"""Persist photo assets and candidate metadata.

Only ACCEPTED candidates become independent FileObjects and therefore visible,
searchable, and exportable. REVIEW_REQUIRED and REJECTED persist as metadata
rows only: a reviewer opens the source page from the recorded bbox, and adding
byte retention later is cheaper than removing it once it exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..extraction.rasterizer import PageRasterizer, RasterizationError
from .models import AssetCandidate, AssetDecision

logger = logging.getLogger(__name__)

ASSET_CANDIDATES = "document_image_assets"


class AssetStore:
    def __init__(self, *, db: Any, file_object_service: Any) -> None:
        self.db = db
        self.file_object_service = file_object_service
        self.rasterizer = PageRasterizer()

    async def persist(
        self,
        *,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        source: Path,
        candidates: Sequence[AssetCandidate],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []

        for candidate in candidates:
            signals = candidate.signals
            file_object_id: Optional[str] = None

            if candidate.decision is AssetDecision.ACCEPTED:
                file_object_id = await self._store_bytes(
                    document_id=document_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    source=source,
                    candidate=candidate,
                )

            records.append(
                {
                    "document_id": document_id,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id) if project_id else None,
                    "page_number": signals.page_number,
                    "bbox": list(signals.bbox),
                    "decision": candidate.decision.value,
                    "reasons": candidate.reasons,
                    "score": candidate.score,
                    "confidence": candidate.confidence,
                    "adjudication_method": "deterministic",
                    "pixel_width": signals.pixel_width,
                    "pixel_height": signals.pixel_height,
                    "colorspace": signals.colorspace,
                    "filter_name": signals.filter_name,
                    "entropy": signals.entropy,
                    "xobject_sha256": signals.xobject_sha256,
                    "file_object_id": file_object_id,
                    "source_pdf_page_link": f"document:{document_id}#page={signals.page_number}",
                    "created_at": datetime.now(timezone.utc),
                }
            )

        collection = self.db[ASSET_CANDIDATES]
        await collection.delete_many({"document_id": document_id})
        if records:
            await collection.insert_many(records)
        return records

    async def _store_bytes(
        self,
        *,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        source: Path,
        candidate: AssetCandidate,
    ) -> Optional[str]:
        signals = candidate.signals
        try:
            payload = self.rasterizer.crop_region(
                source, signals.page_number, signals.bbox
            )
        except RasterizationError as exc:
            logger.warning(
                "Could not crop asset on page %s of %s: %s",
                signals.page_number,
                source.name,
                exc,
            )
            return None

        result = await self.file_object_service.store_bytes(
            payload=payload,
            mime_type="image/png",
            organization_id=organization_id,
            project_id=project_id,
            original_filename=(
                f"{document_id}-p{signals.page_number}-"
                f"{signals.xobject_sha256[:12]}.png"
            ),
            document_type="image_asset",
            document_id=document_id,
        )
        return result.get("file_object_id")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_extraction_end_to_end.py -q`
Expected: PASS — 8 passed

**If `test_fixture_one_yields_zero_accepted_assets` fails**, the thresholds in `classifier.py` are too loose. Tune `MIN_AREA_FRACTION`, `MIN_PIXEL_AREA`, and `ACCEPT_SCORE` — **never the test**. This assertion is the reason the phase exists.

- [ ] **Step 6: Wire into `DocumentProcessor`**

After the Phase 7 gate-and-ladder block, add:

```python
            # Step 1c: extract substantive site photographs as independent
            # assets. Only ACCEPTED candidates become visible FileObjects.
            if settings.IMAGE_ASSET_EXTRACTION_ENABLED and input_path.suffix.lower() == ".pdf":
                from .assets.extractor import ImageAssetExtractor
                from .assets.store import AssetStore

                page_classes = {
                    page.number: page.classification.page_class
                    for page in extraction.pages
                }
                asset_candidates = await ImageAssetExtractor(
                    rasterizer=PageRasterizer(dpi=int(settings.EXTRACTION_FALLBACK_DPI))
                ).extract(input_path, page_classes)
                await AssetStore(
                    db=self.database_service.db,
                    file_object_service=self.file_object_service,
                ).persist(
                    document_id=document_id or "",
                    organization_id=organization_id or "",
                    project_id=project_id,
                    source=input_path,
                    candidates=asset_candidates,
                )
```

Add to `core/config.py`:

```python
    IMAGE_ASSET_EXTRACTION_ENABLED: bool = Field(
        default=False, validation_alias="IMAGE_ASSET_EXTRACTION_ENABLED"
    )
```

Add `self.file_object_service = None` to `DocumentProcessor.__init__` with a setter, or inject it — follow whichever pattern `database_service` already uses in that class.

- [ ] **Step 7: Run the whole suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/assets/extractor.py projectDMS/backend/rbac_backend/services/assets/store.py projectDMS/backend/rbac_backend/services/document_processor.py projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/tests/test_asset_extraction_end_to_end.py
git commit -m "feat: extract and persist progress-photo assets from PDFs"
```

---

## Phase 10 — Selective Vision adjudication for photo candidates

**Distinct from the Phase 7 ladder:** a different question, a different prompt, a different budget. It shares only `PageRasterizer` and the metering infrastructure.

### Task 10.1: `PhotoAdjudicator` and budgeted invocation

**Files:**
- Create: `backend/rbac_backend/services/assets/adjudicator.py`
- Modify: `backend/rbac_backend/services/assets/extractor.py` (adjudication pass)
- Modify: `backend/rbac_backend/core/config.py`
- Test: `backend/rbac_backend/tests/test_asset_adjudicator.py`

**Interfaces:**
- Consumes: `AssetCandidate`, `AssetDecision` (9.1), `PageRasterizer` (Phase 7)
- Produces:
  - `PHOTO_ADJUDICATION_PROMPT_VERSION = "v1"`
  - `class PhotoAdjudicator(Protocol)` with `async def adjudicate(image_png, candidate) -> AdjudicationVerdict`
  - `class NullPhotoAdjudicator` — always raises `AdjudicatorUnavailable`
  - `@dataclass AdjudicationVerdict(is_photograph, label, confidence, model, model_version, prompt_version)`
  - `async def adjudicate_candidates(candidates, *, source, rasterizer, adjudicator, budget) -> list[AssetCandidate]`

- [ ] **Step 1: Write the failing test**

```python
"""Vision adjudication runs on the uncertainty band only, within budget."""

from __future__ import annotations

from pathlib import Path

import pytest

from rbac_backend.services.assets.adjudicator import (
    PHOTO_ADJUDICATION_PROMPT_VERSION,
    AdjudicationVerdict,
    AdjudicatorUnavailable,
    NullPhotoAdjudicator,
    adjudicate_candidates,
)
from rbac_backend.services.assets.models import (
    AssetCandidate,
    AssetDecision,
    AssetSignals,
)
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import build_photo_pdf


class _StubAdjudicator:
    def __init__(self, *, is_photograph: bool, confidence: float = 0.9) -> None:
        self.is_photograph = is_photograph
        self.confidence = confidence
        self.calls = 0

    async def adjudicate(self, image_png: bytes, candidate: AssetCandidate):
        self.calls += 1
        return AdjudicationVerdict(
            is_photograph=self.is_photograph,
            label="site_photograph" if self.is_photograph else "stamp",
            confidence=self.confidence,
            model="stub-vision",
            model_version="1",
            prompt_version=PHOTO_ADJUDICATION_PROMPT_VERSION,
        )


def _candidate(decision: AssetDecision, page: int = 1) -> AssetCandidate:
    return AssetCandidate(
        signals=AssetSignals(
            page_number=page,
            bbox=(100.0, 400.0, 460.0, 670.0),
            placed_area_fraction=0.19,
            pixel_width=900,
            pixel_height=700,
            bits=8,
            colorspace="DeviceRGB",
            filter_name="FlateDecode",
            is_imagemask=False,
            entropy=5.0,
            xobject_sha256=f"hash-{page}-{decision.value}",
            vertical_position=0.5,
        ),
        decision=decision,
        reasons=["borderline"],
        score=0.45,
    )


async def test_only_review_required_candidates_are_sent(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    adjudicator = _StubAdjudicator(is_photograph=True)
    candidates = [
        _candidate(AssetDecision.ACCEPTED),
        _candidate(AssetDecision.REJECTED),
        _candidate(AssetDecision.REVIEW_REQUIRED),
    ]

    await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=adjudicator, budget=10,
    )

    assert adjudicator.calls == 1


async def test_positive_verdict_promotes_to_accepted(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=_StubAdjudicator(is_photograph=True), budget=10,
    )

    assert resolved[0].decision is AssetDecision.ACCEPTED
    assert resolved[0].adjudication_method == "vision"


async def test_negative_verdict_rejects(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=_StubAdjudicator(is_photograph=False), budget=10,
    )

    assert resolved[0].decision is AssetDecision.REJECTED


async def test_low_confidence_stays_in_review(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=_StubAdjudicator(is_photograph=True, confidence=0.4), budget=10,
    )

    assert resolved[0].decision is AssetDecision.REVIEW_REQUIRED


async def test_budget_exhaustion_leaves_the_remainder_in_review(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    adjudicator = _StubAdjudicator(is_photograph=True)
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED, page=p) for p in (1, 2)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=adjudicator, budget=1,
    )

    assert adjudicator.calls == 1
    assert resolved[1].decision is AssetDecision.REVIEW_REQUIRED


async def test_unavailable_adjudicator_declines_rather_than_accepting(
    tmp_path: Path,
) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=NullPhotoAdjudicator(), budget=10,
    )

    assert resolved[0].decision is AssetDecision.REVIEW_REQUIRED


async def test_no_adjudicator_configured_is_a_no_op(tmp_path: Path) -> None:
    source = build_photo_pdf(tmp_path / "photos.pdf")
    candidates = [_candidate(AssetDecision.REVIEW_REQUIRED)]

    resolved = await adjudicate_candidates(
        candidates, source=source, rasterizer=PageRasterizer(dpi=72),
        adjudicator=None, budget=10,
    )

    assert resolved[0].decision is AssetDecision.REVIEW_REQUIRED


async def test_null_adjudicator_raises() -> None:
    with pytest.raises(AdjudicatorUnavailable):
        await NullPhotoAdjudicator().adjudicate(b"", _candidate(AssetDecision.REVIEW_REQUIRED))


def test_prompt_version_is_pinned() -> None:
    assert PHOTO_ADJUDICATION_PROMPT_VERSION == "v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_adjudicator.py -q`
Expected: FAIL — `ModuleNotFoundError` for `assets.adjudicator`

- [ ] **Step 3: Add `adjudication_method` to `AssetCandidate`**

In `backend/rbac_backend/services/assets/models.py`:

```python
    adjudication_method: str = "deterministic"
```

- [ ] **Step 4: Write the implementation**

```python
"""Resolve the uncertainty band with a vision model, within a hard budget.

The question is narrow and fixed: is this a substantive site or progress
photograph, or is it a signature, stamp, seal, logo, letterhead, barcode, icon,
or decorative graphic?

Confidence may route but never accept: a low-confidence positive leaves the
candidate REVIEW_REQUIRED rather than promoting it. An unavailable, failing, or
over-budget adjudicator declines - it never falls back to accepting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from ..extraction.rasterizer import PageRasterizer, RasterizationError
from .models import AssetCandidate, AssetDecision

logger = logging.getLogger(__name__)

PHOTO_ADJUDICATION_PROMPT_VERSION = "v1"
MIN_ACCEPT_CONFIDENCE = 0.70


class AdjudicatorUnavailable(Exception):
    """Raised when no vision adjudicator can be called."""


@dataclass(frozen=True)
class AdjudicationVerdict:
    is_photograph: bool
    label: str
    confidence: float
    model: str
    model_version: str
    prompt_version: str


@runtime_checkable
class PhotoAdjudicator(Protocol):
    async def adjudicate(
        self, image_png: bytes, candidate: AssetCandidate
    ) -> AdjudicationVerdict:
        ...


class NullPhotoAdjudicator:
    """Declines every request. The safe default when no model is configured."""

    async def adjudicate(
        self, image_png: bytes, candidate: AssetCandidate
    ) -> AdjudicationVerdict:
        raise AdjudicatorUnavailable("No photo adjudicator is configured")


async def adjudicate_candidates(
    candidates: Sequence[AssetCandidate],
    *,
    source: Path,
    rasterizer: PageRasterizer,
    adjudicator: Optional[PhotoAdjudicator],
    budget: int,
) -> List[AssetCandidate]:
    """Send only the uncertainty band, and only while budget remains."""
    resolved = list(candidates)
    if adjudicator is None or budget <= 0:
        return resolved

    spent = 0
    for candidate in resolved:
        if candidate.decision is not AssetDecision.REVIEW_REQUIRED:
            continue
        if spent >= budget:
            continue

        try:
            image = rasterizer.crop_region(
                source, candidate.signals.page_number, candidate.signals.bbox
            )
        except RasterizationError as exc:
            logger.warning("Could not crop candidate for adjudication: %s", exc)
            continue

        spent += 1
        try:
            verdict = await adjudicator.adjudicate(image, candidate)
        except Exception as exc:  # noqa: BLE001 - every failure declines
            logger.warning("Photo adjudication failed: %s", exc)
            candidate.reasons.append(f"adjudication unavailable: {exc}")
            continue

        candidate.adjudication_method = "vision"
        candidate.confidence = verdict.confidence
        candidate.reasons.append(
            f"vision: {verdict.label} (confidence {verdict.confidence:.2f}, "
            f"{verdict.model}@{verdict.prompt_version})"
        )

        if verdict.confidence < MIN_ACCEPT_CONFIDENCE:
            continue  # stays REVIEW_REQUIRED

        candidate.decision = (
            AssetDecision.ACCEPTED if verdict.is_photograph else AssetDecision.REJECTED
        )

    return resolved
```

- [ ] **Step 5: Add the budget setting and wire the pass in**

In `core/config.py`:

```python
    PHOTO_ADJUDICATION_ENABLED: bool = Field(
        default=False, validation_alias="PHOTO_ADJUDICATION_ENABLED"
    )
    PHOTO_ADJUDICATION_MAX_PER_DOCUMENT: int = Field(
        default=5, validation_alias="PHOTO_ADJUDICATION_MAX_PER_DOCUMENT"
    )
```

In `ImageAssetExtractor.extract`, accept an optional `adjudicator` and call `adjudicate_candidates` after classification, passing `settings.PHOTO_ADJUDICATION_MAX_PER_DOCUMENT` as the budget.

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_adjudicator.py -q`
Expected: PASS — 9 passed

- [ ] **Step 7: Confirm spend on fixture #1 remains zero**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_asset_extraction_end_to_end.py -q`
Expected: PASS — fixture #1 still yields zero accepted assets and, because every candidate is hard-rejected, zero adjudication calls.

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/assets/adjudicator.py projectDMS/backend/rbac_backend/services/assets/models.py projectDMS/backend/rbac_backend/services/assets/extractor.py projectDMS/backend/rbac_backend/core/config.py projectDMS/backend/rbac_backend/tests/test_asset_adjudicator.py
git commit -m "feat: adjudicate uncertain photo candidates with budgeted vision"
```

---

## Phase 11 — Review surface, benchmark, and rollout gate

### Task 11.1: Review queue API

One queue, two kinds of work: `REVIEW_REQUIRED` photo candidates and `HUMAN_REVIEW_REQUIRED` pages from the Phase 7 ladder.

**Files:**
- Create: `backend/rbac_backend/services/review_queue.py`
- Create: `backend/rbac_backend/routers/review_queue.py`
- Modify: `backend/rbac_backend/main.py`, `backend/rbac_backend/core/permissions.py`
- Test: `backend/rbac_backend/tests/test_review_queue.py`

**Interfaces:**
- Consumes: `ASSET_CANDIDATES` (9.4), `InterventionLedger.COLLECTION` (Phase 7), `PolicyService`
- Produces:
  - `Permissions.DMS_EXTRACTION_REVIEW = "dms.extraction.review"`
  - `class ReviewQueueService(db)` with `list_items(scope_query, *, kind, limit, offset)` and `resolve_asset(asset_id, *, decision, actor_id)`
  - `GET /api/review-queue`, `POST /api/review-queue/assets/{asset_id}/resolve`

**RBAC:** items are scoped by the document's organization/project, so the queue inherits document scope. The route is gated by `PolicyService.authorize` — it is a mutating, scoped surface, so `require_permission` alone is not enough.

- [ ] **Step 1: Write the failing test**

```python
"""The review queue: scoped, permission-gated, and resolvable."""

from __future__ import annotations

from typing import Any

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.review_queue import ReviewItemKind, ReviewQueueService


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, *args: Any, **kwargs: Any) -> "_FakeCursor":
        return self

    def skip(self, count: int) -> "_FakeCursor":
        return _FakeCursor(self._documents[count:])

    def limit(self, count: int) -> "_FakeCursor":
        return _FakeCursor(self._documents[:count])

    def __aiter__(self):
        async def generator():
            for document in self._documents:
                yield document

        return generator()


class _FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.queries: list[dict[str, Any]] = []
        self.updates: list[tuple[dict, dict]] = []

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        self.queries.append(query)
        return _FakeCursor(self.documents)

    async def update_one(self, query: dict, update: dict) -> None:
        self.updates.append((query, update))


class _FakeDb:
    def __init__(self, **collections: _FakeCollection) -> None:
        self._collections = collections

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection([]))


def test_review_permission_exists() -> None:
    assert Permissions.DMS_EXTRACTION_REVIEW == "dms.extraction.review"


async def test_asset_queue_returns_only_review_required() -> None:
    from rbac_backend.services.assets.store import ASSET_CANDIDATES

    collection = _FakeCollection(
        [{"_id": "a1", "decision": "review_required", "document_id": "doc-1"}]
    )
    service = ReviewQueueService(db=_FakeDb(**{ASSET_CANDIDATES: collection}))

    items = await service.list_items(
        {"organization_id": "org-1"}, kind=ReviewItemKind.ASSET, limit=10, offset=0
    )

    assert collection.queries[0]["decision"] == "review_required"
    assert items[0]["_id"] == "a1"


async def test_queue_applies_the_caller_scope_query() -> None:
    from rbac_backend.services.assets.store import ASSET_CANDIDATES

    collection = _FakeCollection([])
    service = ReviewQueueService(db=_FakeDb(**{ASSET_CANDIDATES: collection}))

    await service.list_items(
        {"organization_id": {"$in": ["org-1"]}},
        kind=ReviewItemKind.ASSET,
        limit=10,
        offset=0,
    )

    assert collection.queries[0]["organization_id"] == {"$in": ["org-1"]}


async def test_page_queue_reads_the_intervention_ledger() -> None:
    from rbac_backend.services.extraction.fallback.ledger import InterventionLedger

    collection = _FakeCollection(
        [{"_id": "i1", "outcome": "human_review_required", "page_number": 2}]
    )
    service = ReviewQueueService(
        db=_FakeDb(**{InterventionLedger.COLLECTION: collection})
    )

    items = await service.list_items(
        {"organization_id": "org-1"}, kind=ReviewItemKind.PAGE, limit=10, offset=0
    )

    assert collection.queries[0]["outcome"] == "human_review_required"
    assert items[0]["page_number"] == 2


async def test_resolving_an_asset_records_the_human_decision() -> None:
    from rbac_backend.services.assets.store import ASSET_CANDIDATES

    collection = _FakeCollection([])
    service = ReviewQueueService(db=_FakeDb(**{ASSET_CANDIDATES: collection}))

    await service.resolve_asset("a1", decision="accepted", actor_id="user-1")

    _, update = collection.updates[0]
    assert update["$set"]["decision"] == "accepted"
    assert update["$set"]["adjudication_method"] == "human"
    assert update["$set"]["resolved_by"] == "user-1"


async def test_resolution_rejects_an_unknown_decision() -> None:
    from rbac_backend.services.assets.store import ASSET_CANDIDATES

    import pytest

    service = ReviewQueueService(db=_FakeDb(**{ASSET_CANDIDATES: _FakeCollection([])}))

    with pytest.raises(ValueError):
        await service.resolve_asset("a1", decision="maybe", actor_id="user-1")


async def test_pagination_is_applied() -> None:
    from rbac_backend.services.assets.store import ASSET_CANDIDATES

    documents = [{"_id": f"a{i}", "decision": "review_required"} for i in range(10)]
    service = ReviewQueueService(
        db=_FakeDb(**{ASSET_CANDIDATES: _FakeCollection(documents)})
    )

    items = await service.list_items(
        {}, kind=ReviewItemKind.ASSET, limit=3, offset=5
    )

    assert len(items) == 3
    assert items[0]["_id"] == "a5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_review_queue.py -q`
Expected: FAIL — `AttributeError: DMS_EXTRACTION_REVIEW`

- [ ] **Step 3: Add the permission**

In `backend/rbac_backend/core/permissions.py`, beside the other `dms.*` constants:

```python
    DMS_EXTRACTION_REVIEW = "dms.extraction.review"
```

Grant it to the roles that already hold document edit-metadata rights, following the existing grant table in that module. **This is a behaviour change** — a role that could not review can now resolve assets — so state it in the commit message.

- [ ] **Step 4: Write the service**

```python
"""One queue for everything a human must look at.

Two kinds of work land here: photo candidates the deterministic funnel and the
vision adjudicator could not settle, and pages the extraction fallback ladder
could not reconstruct. Both are scoped by the caller's document scope query, so
the queue inherits document RBAC rather than defining its own.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

from .assets.store import ASSET_CANDIDATES
from .extraction.fallback.ledger import InterventionLedger

logger = logging.getLogger(__name__)

_VALID_DECISIONS = {"accepted", "rejected"}


class ReviewItemKind(str, Enum):
    ASSET = "asset"
    PAGE = "page"


class ReviewQueueService:
    def __init__(self, *, db: Any) -> None:
        self.db = db

    async def list_items(
        self,
        scope_query: Dict[str, Any],
        *,
        kind: ReviewItemKind,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        if kind is ReviewItemKind.ASSET:
            collection = self.db[ASSET_CANDIDATES]
            query = {**scope_query, "decision": "review_required"}
            sort_key = "created_at"
        else:
            collection = self.db[InterventionLedger.COLLECTION]
            query = {**scope_query, "outcome": "human_review_required"}
            sort_key = "recorded_at"

        cursor = collection.find(query).sort(sort_key, -1).skip(offset).limit(limit)
        return [document async for document in cursor]

    async def resolve_asset(
        self, asset_id: str, *, decision: str, actor_id: str
    ) -> None:
        if decision not in _VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(_VALID_DECISIONS)}, got {decision!r}"
            )

        await self.db[ASSET_CANDIDATES].update_one(
            {"_id": asset_id},
            {
                "$set": {
                    "decision": decision,
                    "adjudication_method": "human",
                    "resolved_by": actor_id,
                    "resolved_at": datetime.now(timezone.utc),
                }
            },
        )
```

- [ ] **Step 5: Write the router**

```python
"""Review queue for unresolved photo candidates and extraction pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.errors import BaseDomainError
from ..core.security import build_scope_query, get_current_user
from ..core.permissions import Permissions
from ..models.user import CurrentUser
from ..services.review_queue import ReviewItemKind, ReviewQueueService

router = APIRouter(prefix="/review-queue", tags=["review-queue"])


@router.get("")
async def list_review_items(
    kind: ReviewItemKind = Query(ReviewItemKind.ASSET),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        service = _service()
        await _authorize(current_user)
        scope = build_scope_query(current_user)
        items = await service.list_items(scope, kind=kind, limit=limit, offset=offset)
        return {"items": items, "kind": kind.value}
    except (BaseDomainError, HTTPException):
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Review queue unavailable") from exc


@router.post("/assets/{asset_id}/resolve")
async def resolve_asset(
    asset_id: str,
    decision: str = Query(..., pattern="^(accepted|rejected)$"),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await _authorize(current_user)
        await _service().resolve_asset(
            asset_id, decision=decision, actor_id=str(current_user.id)
        )
        return {"asset_id": asset_id, "decision": decision}
    except (BaseDomainError, HTTPException):
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Resolution failed") from exc
```

Implement `_service()` and `_authorize()` following the dependency-factory pattern the neighbouring routers use, gating on `Permissions.DMS_EXTRACTION_REVIEW` via `PolicyService.authorize`. Register the router in `main.py` under `/api`.

- [ ] **Step 6: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_review_queue.py -q`
Expected: PASS — 7 passed

- [ ] **Step 7: Regenerate the manifest and run the RBAC suites**

Run: `backend/.venv/Scripts/python.exe scripts/rbac_phase0_route_inventory.py --format contract-json --output backend/rbac_backend/route_control_manifest.json`
Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests -q -k "route or permission or role or domain_error or isolation"`
Expected: PASS (allowing the known notifications manifest failure)

- [ ] **Step 8: Commit**

```bash
git add projectDMS/backend/rbac_backend/services/review_queue.py projectDMS/backend/rbac_backend/routers/review_queue.py projectDMS/backend/rbac_backend/core/permissions.py projectDMS/backend/rbac_backend/main.py projectDMS/backend/rbac_backend/route_control_manifest.json projectDMS/backend/rbac_backend/tests/test_review_queue.py
git commit -m "feat: add extraction review queue

Adds the dms.extraction.review permission and grants it to roles that already
hold document edit-metadata rights. This is a behaviour change: those roles can
now resolve photo candidates and review flagged pages."
```

---

### Task 11.2: Asset gallery with page deep link

**Files:**
- Create: `client/src/components/DocumentAssets.tsx`
- Modify: `client/src/services/enhanced-api.ts`
- Test: `client/src/components/__tests__/DocumentAssets.test.tsx`

**Interfaces:**
- Consumes: `GET /api/documents/{id}/assets`
- Produces: `<DocumentAssets documentId />`, `fetchDocumentAssets(documentId)`

**Visibility rule:** the component renders only `ACCEPTED` assets. The API must not return `review_required` rows to this surface — those belong to the review queue.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { DocumentAssets } from '../DocumentAssets';
import * as api from '../../services/enhanced-api';

vi.mock('../../services/enhanced-api');

const asset = {
  id: 'asset-1',
  page_number: 4,
  bbox: [100, 400, 460, 670],
  url: '/api/files/fo-1',
  source_pdf_page_link: 'document:doc-1#page=4',
};

describe('DocumentAssets', () => {
  beforeEach(() => {
    vi.mocked(api.fetchDocumentAssets).mockResolvedValue([asset]);
  });

  it('renders accepted assets', async () => {
    render(<DocumentAssets documentId="doc-1" />);

    expect(await screen.findByRole('img', { name: /page 4/i })).toBeInTheDocument();
  });

  it('links each asset to its source page', async () => {
    render(<DocumentAssets documentId="doc-1" />);

    const link = await screen.findByRole('link', { name: /open page 4/i });
    expect(link).toHaveAttribute('href', expect.stringContaining('page=4'));
  });

  it('shows an empty state rather than a blank panel', async () => {
    vi.mocked(api.fetchDocumentAssets).mockResolvedValue([]);
    render(<DocumentAssets documentId="doc-1" />);

    expect(await screen.findByText(/no site photographs/i)).toBeInTheDocument();
  });

  it('surfaces a load failure', async () => {
    vi.mocked(api.fetchDocumentAssets).mockRejectedValue(new Error('500'));
    render(<DocumentAssets documentId="doc-1" />);

    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd client && npm run test -- DocumentAssets`
Expected: FAIL — cannot resolve `../DocumentAssets`

- [ ] **Step 3: Write the component and adapter**

Add to `enhanced-api.ts`:

```typescript
export interface DocumentAsset {
  id: string;
  page_number: number;
  bbox: number[];
  url: string;
  source_pdf_page_link: string;
}

export async function fetchDocumentAssets(documentId: string): Promise<DocumentAsset[]> {
  const response = await api.get<{ assets: DocumentAsset[] }>(`/documents/${documentId}/assets`);
  return response.data.assets;
}
```

```tsx
import { useEffect, useState } from 'react';

import { fetchDocumentAssets, type DocumentAsset } from '../services/enhanced-api';

export function DocumentAssets({ documentId }: { documentId: string }) {
  const [assets, setAssets] = useState<DocumentAsset[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchDocumentAssets(documentId).then(setAssets).catch(() => setFailed(true));
  }, [documentId]);

  if (failed) {
    return <div role="alert">Site photographs could not be loaded.</div>;
  }
  if (assets === null) {
    return <div aria-busy="true">Loading site photographs…</div>;
  }
  if (assets.length === 0) {
    return <p>No site photographs were found in this document.</p>;
  }

  return (
    <ul>
      {assets.map((asset) => (
        <li key={asset.id}>
          <img src={asset.url} alt={`Site photograph from page ${asset.page_number}`} />
          <a href={`/documents/${documentId}/view?page=${asset.page_number}`}>
            Open page {asset.page_number}
          </a>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd client && npm run test -- DocumentAssets`
Expected: PASS — 4 passed

- [ ] **Step 5: Lint, build, and confirm your files are clean**

Run: `cd client && npm run lint && npm run build`
Expected: build succeeds. `npx tsc -b` — confirm `DocumentAssets.tsx` is absent from the output.

- [ ] **Step 6: Commit**

```bash
git add projectDMS/client/src/components/DocumentAssets.tsx projectDMS/client/src/components/__tests__/DocumentAssets.test.tsx projectDMS/client/src/services/enhanced-api.ts
git commit -m "feat: render accepted site photographs with a source-page deep link"
```

---

### Task 11.3: Rollout benchmark and gate

**Files:**
- Create: `scripts/phase11_rollout_benchmark.py`
- Create: `docs/architecture/phase11_rollout_benchmark_2026-08-14.md`
- Test: `backend/rbac_backend/tests/test_rollout_benchmark.py`

**Interfaces:**
- Consumes: the Phase 0 baseline in `docs/architecture/phase0_extraction_measurements_2026-08-14.md`
- Produces: `measure_document(path) -> dict`, `compare_to_baseline(measured, baseline) -> dict` with a `within_budget: bool`

**This task is the gate.** Rollout proceeds only if the measured deltas are inside an agreed budget; otherwise the gate thresholds, batching, and concurrency parameters are retuned first.

- [ ] **Step 1: Write the failing test**

```python
"""The rollout gate compares measured cost against the Phase 0 baseline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.phase11_rollout_benchmark import compare_to_baseline

BASELINE = {
    "cpu_percent": 40.0,
    "memory_mib": 800.0,
    "wall_seconds": 12.0,
    "ocr_pages_per_document": 0.4,
    "llm_calls_per_document": 0.0,
}


def test_no_change_is_within_budget() -> None:
    result = compare_to_baseline(dict(BASELINE), BASELINE)

    assert result["within_budget"] is True
    assert result["deltas"]["wall_seconds_pct"] == 0.0


def test_a_modest_increase_is_within_budget() -> None:
    measured = {**BASELINE, "wall_seconds": 14.0, "ocr_pages_per_document": 0.6}

    assert compare_to_baseline(measured, BASELINE)["within_budget"] is True


def test_a_large_time_increase_fails_the_gate() -> None:
    measured = {**BASELINE, "wall_seconds": 60.0}

    result = compare_to_baseline(measured, BASELINE)

    assert result["within_budget"] is False
    assert any("wall_seconds" in reason for reason in result["reasons"])


def test_an_ocr_page_explosion_fails_the_gate() -> None:
    measured = {**BASELINE, "ocr_pages_per_document": 9.0}

    result = compare_to_baseline(measured, BASELINE)

    assert result["within_budget"] is False
    assert any("ocr_pages" in reason for reason in result["reasons"])


def test_any_llm_spend_on_the_baseline_corpus_fails_the_gate() -> None:
    # Fixture #1 must cost nothing. Non-zero here means the quality gate is
    # escalating documents that are correct.
    measured = {**BASELINE, "llm_calls_per_document": 0.5}

    result = compare_to_baseline(measured, BASELINE)

    assert result["within_budget"] is False
    assert any("llm_calls" in reason for reason in result["reasons"])


def test_reasons_are_empty_when_the_gate_passes() -> None:
    assert compare_to_baseline(dict(BASELINE), BASELINE)["reasons"] == []


def test_a_zero_baseline_does_not_divide_by_zero() -> None:
    baseline = {**BASELINE, "ocr_pages_per_document": 0.0}
    measured = {**BASELINE, "ocr_pages_per_document": 1.0}

    result = compare_to_baseline(measured, baseline)

    assert isinstance(result["within_budget"], bool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_rollout_benchmark.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.phase11_rollout_benchmark'`

- [ ] **Step 3: Write the script**

```python
"""Measure the cost of the new pipeline and compare it to the Phase 0 baseline.

Run inside the backend container against a representative corpus:

    docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
      exec -T backend python -m scripts.phase11_rollout_benchmark /app/fixtures

The gate is deliberately strict on LLM calls: the benchmark corpus includes
fixture #1, which is a correct document and must cost nothing. Any spend there
means the quality gate is escalating documents that do not need it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Budgets, agreed before measurement rather than fitted to it.
MAX_WALL_INCREASE_PCT = 100.0
MAX_MEMORY_INCREASE_PCT = 50.0
MAX_OCR_PAGES_PER_DOCUMENT = 3.0
MAX_LLM_CALLS_PER_DOCUMENT = 0.0


def _pct_change(measured: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if measured == 0 else float("inf")
    return round(((measured - baseline) / baseline) * 100.0, 2)


def compare_to_baseline(
    measured: Dict[str, float], baseline: Dict[str, float]
) -> Dict[str, Any]:
    """Compare a measurement against the baseline and apply the rollout budget."""
    deltas = {
        "wall_seconds_pct": _pct_change(
            measured["wall_seconds"], baseline["wall_seconds"]
        ),
        "memory_mib_pct": _pct_change(measured["memory_mib"], baseline["memory_mib"]),
        "cpu_percent_pct": _pct_change(
            measured["cpu_percent"], baseline["cpu_percent"]
        ),
        "ocr_pages_per_document": measured["ocr_pages_per_document"],
        "llm_calls_per_document": measured["llm_calls_per_document"],
    }

    reasons = []
    if deltas["wall_seconds_pct"] > MAX_WALL_INCREASE_PCT:
        reasons.append(
            f"wall_seconds rose {deltas['wall_seconds_pct']}% "
            f"(budget {MAX_WALL_INCREASE_PCT}%)"
        )
    if deltas["memory_mib_pct"] > MAX_MEMORY_INCREASE_PCT:
        reasons.append(
            f"memory_mib rose {deltas['memory_mib_pct']}% "
            f"(budget {MAX_MEMORY_INCREASE_PCT}%)"
        )
    if measured["ocr_pages_per_document"] > MAX_OCR_PAGES_PER_DOCUMENT:
        reasons.append(
            f"ocr_pages_per_document is {measured['ocr_pages_per_document']} "
            f"(budget {MAX_OCR_PAGES_PER_DOCUMENT})"
        )
    if measured["llm_calls_per_document"] > MAX_LLM_CALLS_PER_DOCUMENT:
        reasons.append(
            f"llm_calls_per_document is {measured['llm_calls_per_document']}; "
            "the benchmark corpus is correct and must cost nothing"
        )

    return {"deltas": deltas, "reasons": reasons, "within_budget": not reasons}


def measure_document(path: Path) -> Dict[str, float]:
    """Measure one document through the current pipeline."""
    import resource

    started = time.monotonic()
    # The caller wires the real DocumentProcessor; this records the envelope.
    wall = time.monotonic() - started
    return {
        "wall_seconds": wall,
        "memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "cpu_percent": 0.0,
        "ocr_pages_per_document": 0.0,
        "llm_calls_per_document": 0.0,
    }


if __name__ == "__main__":
    corpus = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    results = [measure_document(pdf) for pdf in sorted(corpus.glob("*.pdf"))]
    print(json.dumps({"documents": len(results), "results": results}, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/rbac_backend/tests/test_rollout_benchmark.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Run the benchmark in the container and record it**

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T backend python -m scripts.phase11_rollout_benchmark /app/fixtures
```

Create `docs/architecture/phase11_rollout_benchmark_2026-08-14.md` with the raw JSON pasted verbatim, the Phase 0 baseline beside it, and this line filled in:

```markdown
**Rollout decision:** within_budget = <true|false>.
<If false, list the reasons and what will be retuned before re-measuring.>
```

- [ ] **Step 6: Verify the gate produced a definite answer**

Re-read the document. If `within_budget` is false, **do not enable the feature flags in production.** Retune `PageExtractionPolicy.max_ocr_pages_per_attempt`, the quality-gate thresholds, or worker concurrency, then re-measure.

- [ ] **Step 7: Commit**

```bash
git add projectDMS/scripts/phase11_rollout_benchmark.py projectDMS/docs/architecture/phase11_rollout_benchmark_2026-08-14.md projectDMS/backend/rbac_backend/tests/test_rollout_benchmark.py
git commit -m "feat: add the rollout benchmark and cost gate"
```

---

## Plan self-review

**1. Spec coverage.** Every Phase 8–11 requirement maps to a task:

| Spec requirement | Task |
|---|---|
| Enclosures as first-class child records with `parent_document_id` | 8.1 |
| `relationship_type`, `source_page_range`, preserved parent provenance | 8.1 |
| Independent conversion/OCR/chunks/vectors/assets | 8.3 (reuses the Phase 3 pipeline) |
| Children excluded from top-level listings, present in retrieval | 8.2 |
| Enclosure UI mutations wired | 8.4 |
| Substantive photographs only; no signatures/stamps/logos/letterheads/barcodes | 9.1–9.3 |
| Each asset linked to document, page, and bbox | 9.4 |
| Viewable with an option to open the source PDF page | 9.4, 11.2 |
| Deterministic filtering + classification, then selective Vision | 9.3, 10.1 |
| Only `ACCEPTED` become visible/searchable; `REVIEW_REQUIRED` internal | 9.4, 10.1, 11.2 |
| Embedded PDF photos in Phase 1 scope | 9.1–9.4 |
| Flattened/scanned-page extraction recorded as P2 | *below* |
| Benchmark CPU, memory, time, OCR-page volume before rollout | 11.3 |
| Review surface for unresolved items | 11.1, 11.2 |

**P2, explicitly not in this plan:** photographs inside a *flattened or fully scanned* page are not separate XObjects — the whole page is one raster and `enumerate_candidates` returns one full-page image, which Task 9.3 hard-rejects as `PAGE_SCAN`. Recovering those needs page-region detection over the rendered raster (OpenCV contours or vision segmentation) and a new dependency. Task 9.3's `test_full_page_raster_on_a_scanned_page_is_a_page_scan_not_an_asset` pins the current behaviour so the gap is visible rather than silent.

**2. Placeholder scan.** Two intentional templates remain, both with a following step that requires completion: Task 11.3 Step 5's `<true|false>` rollout line, and its instruction to list retuning reasons if the gate fails. `measure_document` in Task 11.3 records the envelope and is explicitly noted as needing the real `DocumentProcessor` wired by the caller — flagged rather than hidden.

**3. Type consistency.** `AssetSignals`, `AssetCandidate`, `AssetDecision`, and `RejectReason` are defined once in `assets/models.py` (9.1) and used unchanged in 9.2, 9.3, 9.4, 10.1, and 11.1. `adjudication_method` is added to `AssetCandidate` in Task 10.1 Step 3 before Task 10.1 Step 4 reads it. `PageClass` and `PageRasterizer` come from the Phases 0–7 plan (Tasks 1.2 and 7.1) and are not redefined here. `ASSET_CANDIDATES` (9.4) and `InterventionLedger.COLLECTION` (Phases 0–7 Task 7.2) are the two collections Task 11.1 reads.

**Known caveats for the implementer:**
- Task 8.3 assumes `duplicate_detection.precheck_upload` and `ArchiveIntakePolicy` are already imported into `routers/documents.py` by Phase 5. Confirm both before adding the enclosure block.
- Task 9.4 Step 6 assumes `DocumentProcessor` can reach a `FileObjectService`. It currently cannot — follow whatever injection pattern `database_service` uses in that class rather than constructing one inline.
- Task 11.1 Step 3 grants a new permission to existing roles. Per `CLAUDE.md`, that is a behaviour change and the commit message says so.
