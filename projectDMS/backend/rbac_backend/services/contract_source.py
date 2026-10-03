"""Whether a document's derived evidence belongs to the contract writers.

A contract's evidence - clause rows with page provenance and their vector
points - has exactly two writers: contract ingest (and its reindex) before
promotion, and the contract-worker's reprojection once a Contract Master
instrument names the document. Every generic writer (the ingestion pipeline,
the general reprocess path, storage-sync repair, the vector reconcilers) must
leave such a document alone, and every one asks this module, so they cannot
drift apart.

A document is a contract source when either holds:

* it is a contract upload (``uploadType`` "contract", any casing or padding,
  as the document model accepts), promoted or not; or
* a Contract Master instrument names it (``governed_by_contract_master``),
  whatever its upload type says.

The second check reads the database, so a writer that runs for a while asks
again immediately before each destructive step: promotion can commit, and an
upload type can be edited, while it works.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

CONTRACT_UPLOAD_TYPE = "contract"


class ContractSourceWriteRefused(RuntimeError):
    """A generic writer reached a contract source; nothing was written."""


def is_contract_upload(document: Optional[Mapping[str, Any]]) -> bool:
    """The upload-type half of the rule; needs no database read."""
    return str((document or {}).get("uploadType") or "").strip().lower() == CONTRACT_UPLOAD_TYPE


async def is_contract_source(
    db: Any, document_id: Any, document: Optional[Mapping[str, Any]] = None
) -> bool:
    """The whole rule. ``document`` is the canonical row when the caller has it.

    Fails closed: an unreadable instrument collection raises (see
    ``governed_by_contract_master``) rather than answering "not a contract".
    """
    if is_contract_upload(document):
        return True
    from .document_service import governed_by_contract_master

    stored_id = (document or {}).get("_id")
    return await governed_by_contract_master(db, document_id, stored_id)


async def refuse_contract_source_write(
    db: Any, document_id: Any, document: Optional[Mapping[str, Any]], *, step: str
) -> None:
    """Raise ``ContractSourceWriteRefused`` if ``document_id`` is a contract source.

    ``document`` should be re-read by the caller just before ``step``, so an
    upload type edited mid-run counts; the instrument is always read fresh.
    """
    if await is_contract_source(db, document_id, document):
        raise ContractSourceWriteRefused(
            f"Document {document_id} is a contract source (a contract upload or "
            f"governed by a Contract Master instrument); {step} was not performed. "
            "Its evidence is rebuilt by the contract ingest, reindex and reprojection."
        )
