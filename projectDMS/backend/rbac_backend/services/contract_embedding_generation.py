"""Embedding input for contract clauses, with the classification taken out.

Instrument classification used to sit inside the embedded string, which made a
legal correction expensive: every chunk of the document had to be re-embedded,
and a failed re-embed blocked evidence. That inverts the authority direction —
a derived store gets a veto over an authoritative legal fact.

It is also wrong on the modelling. Instrument kind is a document-level *legal*
fact, not a semantic property of clause text. In similarity space it lets a
classification perturb ranking, which is the relevance/precedence conflation the
architecture forbids. It belongs in the eligible-set prefilter, where it already
is.

Two consequences worth stating plainly:

* **A correction is now payload-only, or nothing at all.** The same clause embeds
  to the same string before and after a reclassification.
* **Old vectors do not need to be re-embedded for correctness.** They are already
  unreachable through the canonical eligible set, which never consults the
  payload. The generation bump makes them *identifiable*; containment makes them
  *harmless*. Nothing on the read path is gated on the migration finishing —
  that would turn a stalled housekeeping job into a safety problem.

``source_classification_revision`` is carried as a **negative hint only**: it may
cause a candidate to be dropped, never to be admitted.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Sequence

from ..models.contract_document import ContractDocumentType

logger = logging.getLogger(__name__)

__all__ = [
    "CONTRACT_EMBEDDING_GENERATION",
    "ClassificationInEmbeddingInput",
    "build_embedding_input",
    "build_vector_payload",
    "is_stale_generation",
]

#: Bumped when the embedded string's composition changes. Generation 1 embedded
#: the instrument classification; generation 2 does not, and the two are not
#: comparable in embedding space, so points must be distinguishable.
CONTRACT_EMBEDDING_GENERATION = 2

#: The classification vocabulary, lowercased once. Checked against the
#: *structural* fields only — never against clause body text, where a clause may
#: legitimately discuss an amendment or the general conditions.
_CLASSIFICATION_TOKENS = frozenset(
    {member.value.lower() for member in ContractDocumentType}
    | {member.name.lower() for member in ContractDocumentType}
)


class ClassificationInEmbeddingInput(ValueError):
    """A structural field carried an instrument classification.

    Raised rather than stripped: silently removing it would hide a caller that
    still believes classification belongs in similarity space, and the next
    field they add would go in unnoticed.
    """


def _reject_classification(field: str, value: Optional[str]) -> None:
    if not value:
        return
    if str(value).strip().lower() in _CLASSIFICATION_TOKENS:
        raise ClassificationInEmbeddingInput(
            f"{field}={value!r} is an instrument classification; instrument kind "
            "is a document-level legal fact and does not belong in the embedded "
            "text. It is applied through the canonical eligible set instead."
        )


def build_embedding_input(
    *,
    clause_number: str,
    clause_title: Optional[str],
    section_heading: Optional[str],
    toc_path: Sequence[str],
    page_numbers: Sequence[int],
    clause_tags: Sequence[str],
    text: str,
) -> str:
    """The string that gets embedded.

    Deliberately takes no classification argument, so folding one back in
    requires changing this signature rather than passing an extra value.
    """
    _reject_classification("section_heading", section_heading)
    for tag in clause_tags:
        _reject_classification("clause_tag", tag)
    for element in toc_path:
        _reject_classification("toc_path", element)

    parts = [f"Clause {clause_number}: {clause_title or ''}".rstrip()]
    if section_heading:
        parts.append(f"Section: {section_heading}")
    if toc_path:
        parts.append(f"Hierarchy: {' > '.join(toc_path)}")
    if page_numbers:
        parts.append(f"Pages: {', '.join(str(page) for page in page_numbers)}")
    if clause_tags:
        parts.append(f"Clause tags: {', '.join(clause_tags)}")
    parts.append(text)
    return "\n".join(parts)


def build_vector_payload(
    *,
    chunk_id: str,
    document_id: str,
    organization_id: str,
    clause_number: str,
    source_classification_revision: Optional[int] = None,
    clause_start_position: Optional[int] = None,
    upload_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Payload for one contract clause point.

    Carries no instrument type, no section type and no priority: a payload that
    looks authoritative invites a consumer to trust it, and the payload is not
    authority. Identity and the two hints are all it needs.
    """
    payload: Dict[str, Any] = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "organization_id": organization_id,
        "clause_number": clause_number,
        "embedding_generation": CONTRACT_EMBEDDING_GENERATION,
        "uploadType": "contract",
    }
    if clause_start_position is not None:
        payload["clause_start_position"] = clause_start_position
    if upload_id is not None:
        payload["upload_id"] = upload_id
    if source_classification_revision is not None:
        # Negative hint. It may drop a candidate; it may never admit one.
        payload["source_classification_revision"] = source_classification_revision
    return payload


def is_stale_generation(
    payload: Mapping[str, Any], *, authoritative_revision: int
) -> bool:
    """Does this point describe a superseded classification generation?

    Strict inequality in both directions. A missing revision is stale because
    absence is not evidence of currency, and a revision *ahead* of the
    authoritative one is stale too: a payload cannot vouch for itself, and
    treating a larger number as "newer, therefore fine" would let the derived
    store authorise its own content.
    """
    recorded = payload.get("source_classification_revision")
    if recorded is None:
        return True
    try:
        return int(recorded) != int(authoritative_revision)
    except (TypeError, ValueError):
        return True
