"""The bulk per-document path imports ``backoff`` at runtime; it must be declared.

``BulkUploadService._async_process_single_document_with_metadata`` does a
function-local ``import backoff`` and wraps ``DocumentService.create_document``
in ``@backoff.on_exception(backoff.expo, (AutoReconnect, NetworkTimeout),
max_tries=3)``. Until this fix ``backoff`` was not in ``requirements.txt``, and
neither the backend image nor the CI install contained it, so the path raised
``ModuleNotFoundError: No module named 'backoff'`` the moment it ran. A
function-local import is invisible to import-time checks and to every test that
does not execute the function, which is how it went unnoticed.

Two kinds of test live here:

* a dependency contract: every third-party package imported by this path has an
  exact pin in ``requirements.txt`` - a transitive install is not a contract;
* runtime tests that execute the real method far enough to cross the import and
  the retry wrapper, and pin the retry semantics the dependency exists for.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pymongo.errors import AutoReconnect

from rbac_backend.services.bulk_upload_service import BulkUploadService

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

# Import name -> distribution name, for the third-party imports of this path.
# An import missing from this map fails the contract test, so a new dependency
# added to the path has to be declared here and in requirements.txt together.
IMPORT_TO_DISTRIBUTION = {
    "backoff": "backoff",
    "pymongo": "pymongo",
}

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _exact_pins() -> Dict[str, str]:
    pins: Dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


def _third_party_imports_of_path() -> List[str]:
    source = textwrap.dedent(
        inspect.getsource(BulkUploadService._async_process_single_document_with_metadata)
    )
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return sorted(r for r in roots if r not in sys.stdlib_module_names)


def test_every_third_party_import_on_the_bulk_path_is_exactly_pinned() -> None:
    imports = _third_party_imports_of_path()
    # The scan must see the import this contract exists for, or it is scanning
    # the wrong thing and would pass vacuously.
    assert "backoff" in imports, imports

    unmapped = [name for name in imports if name not in IMPORT_TO_DISTRIBUTION]
    assert not unmapped, f"map these imports to a distribution and pin them: {unmapped}"

    pins = _exact_pins()
    missing = [
        IMPORT_TO_DISTRIBUTION[name]
        for name in imports
        if _normalise(IMPORT_TO_DISTRIBUTION[name]) not in pins
    ]
    assert not missing, (
        f"imported by BulkUploadService._async_process_single_document_with_metadata "
        f"but not exactly pinned in {REQUIREMENTS.name}: {missing}"
    )


# --- runtime path ----------------------------------------------------------


class _DocumentServiceStub:
    """Stands in for DocumentService; fails ``failures`` times, then succeeds."""

    def __init__(self, failures: List[BaseException] | None = None) -> None:
        self._failures = list(failures or [])
        self.calls: List[Dict[str, Any]] = []

    async def create_document(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self._failures:
            raise self._failures.pop(0)
        return SimpleNamespace(id="doc-123")


class _MetadataServiceMustNotRun:
    async def process_document(self, **_kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - asserted
        raise AssertionError("OCR is disabled; metadata processing must not run")


def _service(document_service: _DocumentServiceStub) -> BulkUploadService:
    # Skip __init__: it builds a real MetadataProcessorService, which this path
    # never touches with OCR disabled.
    service = BulkUploadService.__new__(BulkUploadService)
    service.db = None
    service.metadata_service = _MetadataServiceMustNotRun()
    service.document_service = None
    service._active_jobs = {}

    async def _get_document_service() -> _DocumentServiceStub:
        return document_service

    service._get_document_service = _get_document_service  # type: ignore[method-assign]
    return service


def _doc_data(tmp_path: Path) -> Dict[str, Any]:
    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    # Only the keys this path reads without also forwarding them twice: the
    # method passes upload_type / letter_no / date / current_user explicitly
    # AND through **doc_data, so supplying them here raises TypeError before
    # create_document is reached. That is a separate pre-existing defect.
    return {
        "filename": "letter.pdf",
        "file_path": str(pdf),
        "_row_number": 7,
        "ocr_enabled": False,
    }


def _no_wait(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    """Replace the wait generator so retries do not sleep; count its use."""
    used: List[int] = []

    def _zero_wait(*_args: Any, **_kwargs: Any):
        used.append(1)
        while True:
            yield 0

    monkeypatch.setattr("backoff.expo", _zero_wait)
    return used


async def _run(service: BulkUploadService, doc_data: Dict[str, Any]):
    return await service._async_process_single_document_with_metadata(
        doc_data, "org-1", "proj-1"
    )


async def test_bulk_path_crosses_the_backoff_import_and_creates_the_document(tmp_path: Path) -> None:
    ds = _DocumentServiceStub()

    result = await _run(_service(ds), _doc_data(tmp_path))

    assert result.error is None
    assert result.success is True
    assert result.document_id == "doc-123"
    assert result.row_number == 7
    assert len(ds.calls) == 1
    assert ds.calls[0]["filename"] == "letter.pdf"
    assert ds.calls[0]["organization_id"] == "org-1"
    assert ds.calls[0]["project_id"] == "proj-1"


async def test_transient_mongo_error_is_retried_until_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used = _no_wait(monkeypatch)
    ds = _DocumentServiceStub([AutoReconnect("primary stepped down")] * 2)

    result = await _run(_service(ds), _doc_data(tmp_path))

    assert used, "the retry did not go through backoff.expo"
    assert len(ds.calls) == 3
    assert result.success is True
    assert result.document_id == "doc-123"


async def test_retries_stop_at_three_attempts_and_the_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_wait(monkeypatch)
    ds = _DocumentServiceStub([AutoReconnect("still down")] * 5)

    result = await _run(_service(ds), _doc_data(tmp_path))

    assert len(ds.calls) == 3
    # The surrounding contract: the per-document handler turns the final
    # exception into a failed row rather than raising.
    assert result.success is False
    assert result.document_id is None
    assert result.error == "still down"
    assert result.row_number == 7


async def test_non_transient_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_wait(monkeypatch)
    ds = _DocumentServiceStub([ValueError("Missing required field: letter_no")])

    result = await _run(_service(ds), _doc_data(tmp_path))

    assert len(ds.calls) == 1
    assert result.success is False
    assert result.error == "Missing required field: letter_no"
