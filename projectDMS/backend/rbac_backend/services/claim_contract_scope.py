"""No cross-contract evidence on a claim (plan 4B-23, D-23, AQ-1, C-05).

A project can hold several contracts. A claim is made under one (4B-18,
``claims.contract_id``). A record that says which contract it belongs to - EOT
submissions and determinations always do (``"primary"`` by default), and a
hindrance, document or letter does if it stores ``contract_id`` - may support a
claim only under that same contract.

The rule, in one place:

- a record that carries no contract may be used by any claim;
- a record that carries a contract may be used only by a claim under that
  contract. A claim with no contract identified can therefore use only records
  that carry none; the way forward is to give the claim its contract (4B-18).

It is checked where a record is attached to a claim (head links, document and
letter links) and again where the claim's evidence is used (quantum records,
the submission, escalation), so a link made before the rule existed is caught
before it is relied on. Nothing is removed or rewritten.

**Contract identity** (owner review 2026-11-17). A contract is a Contract Master
entry, and Contract Master is keyed by project and ``contract_id``: the main
contract of every project is ``"primary"``. So identity is the pair, not the
bare key - ``"primary"`` of another project is a different contract, and a
record that names one conflicts with this claim's.

Beyond the conflicts it refuses, ``identity_review`` sorts the claim's evidence
into three kinds, for a contract-specific claim:

- **verified**: names this claim's contract, which the project's Contract
  Master holds;
- **unverified**: names this claim's contract key, but the project's Contract
  Master holds no such entry (key-date records default to ``"primary"``
  without checking that it exists);
- **unidentified**: carries no contract at all (hindrances, most documents and
  letters, records from before contracts were recorded).

The last two are reported, never refused or rewritten: whether they need more
verification before supporting a contract-specific claim is an owner decision
(recorded in the ticket register). Production readings decide how many there are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from fastapi import HTTPException, status

ERROR_CODE = "claim_contract_mismatch"

# Where each kind of linked record is kept. Named here, not imported from the
# Delay Lab: this module is imported by the document relationship service, and
# importing the Delay Lab from it would pull the key-date services into every
# module that links a document. A test pins these to the Delay Lab's names.
_RECORD_COLLECTIONS = {
    "hindrance": "delay_events",
    "eot_submission": "key_date_eot_submissions",
    "eot_determination": "key_date_eot_determinations",
}


def contract_of(record: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The contract a record or claim says it belongs to, or None."""
    if not record:
        return None
    value = record.get("contract_id")
    text = str(value).strip() if value is not None else ""
    return text or None


def project_of(record: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not record:
        return None
    value = record.get("project_id")
    text = str(value).strip() if value is not None else ""
    return text or None


def conflicts(claim: Mapping[str, Any], record: Optional[Mapping[str, Any]]) -> bool:
    """Whether ``record`` belongs to a contract the claim is not made under.

    A contract is identified by project and key together: a record naming the same
    key in another project names another contract."""
    record_contract = contract_of(record)
    if record_contract is None:
        return False
    if record_contract != contract_of(claim):
        return True
    record_project, claim_project = project_of(record), project_of(claim)
    return record_project is not None and claim_project is not None and record_project != claim_project


def message(claim: Mapping[str, Any], labels: List[str]) -> str:
    named = ", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")
    if contract_of(claim) is None:
        return (
            f"{named} belongs to a contract, and this claim has no contract identified. "
            "Set the claim's contract first; only records of that contract can support it."
        )
    return f"{named} belongs to a different contract from this claim. Only records of the claim's own contract can support it."


def refusal(claim: Mapping[str, Any], labels: List[str]) -> HTTPException:
    text = message(claim, labels)
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "claim_contract_mismatch", "user_message": text, "detail": text, "records": labels[:20]},
    )


def ensure_same_contract(claim: Mapping[str, Any], record: Optional[Mapping[str, Any]], label: str) -> None:
    """Refuse (422 ``claim_contract_mismatch``) a record of another contract."""
    if conflicts(claim, record):
        raise refusal(claim, [label])


def conflicting_labels(claim: Mapping[str, Any], records: Iterable[Tuple[str, Optional[Mapping[str, Any]]]]) -> List[str]:
    """Labels of the records, given as ``(label, record)``, that belong to another contract."""
    seen: List[str] = []
    for label, record in records:
        if conflicts(claim, record) and label not in seen:
            seen.append(label)
    return seen


def document_label(document: Mapping[str, Any]) -> str:
    for key in ("letterNo", "letter_no", "subject", "filename"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return f"Document {value.strip()}"
    return "A linked document"


@dataclass
class EvidenceIdentity:
    """The claim's evidence, sorted by what it says about its contract."""

    conflicts: List[str] = field(default_factory=list)
    verified: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)
    unidentified: List[str] = field(default_factory=list)


async def _master_keys(db: Any, claim: Mapping[str, Any]) -> set:
    project = project_of(claim)
    if project is None:
        return set()
    query: Dict[str, Any] = {"project_id": project}
    if claim.get("organization_id"):
        query["organization_id"] = str(claim["organization_id"])
    rows = await db.contract_master.find(query, {"contract_id": 1}).to_list(length=500)
    return {str(row.get("contract_id") or "").strip() for row in rows} - {""}


def _sorted(claim: Mapping[str, Any], found: List[Tuple[str, Optional[Mapping[str, Any]]]], master: set) -> EvidenceIdentity:
    result = EvidenceIdentity()
    for label, record in found:
        if conflicts(claim, record):
            bucket = result.conflicts
        elif contract_of(record) is None:
            bucket = result.unidentified
        elif contract_of(record) in master:
            bucket = result.verified
        else:
            bucket = result.unverified
        if label not in bucket:
            bucket.append(label)
    return result


def identity_notes(claim: Mapping[str, Any], identity: EvidenceIdentity) -> List[str]:
    """Plain warnings for a contract-specific claim. Nothing here blocks."""
    if contract_of(claim) is None:
        return []

    def named(labels: List[str]) -> str:
        return ", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")

    notes: List[str] = []
    if identity.unverified:
        notes.append(
            f"{named(identity.unverified)}: names this claim's contract, but the project's Contract Master "
            "holds no such contract, so it is not confirmed which contract the record belongs to."
        )
    if identity.unidentified:
        notes.append(
            f"{named(identity.unidentified)}: does not say which contract it belongs to. "
            "Check it belongs to this claim's contract before relying on it."
        )
    return notes


async def identity_review(
    db: Any, claim: Mapping[str, Any], links: Iterable[Any], document_ids: Iterable[str]
) -> EvidenceIdentity:
    """Every record the claim's evidence relies on, sorted by its contract identity.

    ``links`` are the claim's active head links (``ClaimHeadLink``); their records are
    read as they are now, so a record moved to another contract after linking is
    caught. ``document_ids`` are the claim's linked documents.
    """
    found = await _records(db, links, document_ids)
    master = await _master_keys(db, claim) if contract_of(claim) is not None else set()
    return _sorted(claim, found, master)


async def evidence_conflicts(db: Any, claim: Mapping[str, Any], links: Iterable[Any], document_ids: Iterable[str]) -> List[str]:
    """Every record the claim's evidence relies on that belongs to another contract."""
    return conflicting_labels(claim, await _records(db, links, document_ids))


async def _records(
    db: Any, links: Iterable[Any], document_ids: Iterable[str]
) -> List[Tuple[str, Optional[Mapping[str, Any]]]]:
    from bson import ObjectId

    collections = _RECORD_COLLECTIONS
    found: List[Tuple[str, Optional[Mapping[str, Any]]]] = []
    for link in links:
        collection = collections.get(str(getattr(link, "target_type", "")))
        if collection is None:
            continue
        target_id = str(getattr(link, "target_id", ""))
        candidates: List[Any] = [target_id]
        if ObjectId.is_valid(target_id):
            candidates.append(ObjectId(target_id))
        record = await db[collection].find_one({"_id": {"$in": candidates}}, {"contract_id": 1, "project_id": 1})
        found.append((str(getattr(link, "target_label", "") or target_id), record))
    ids = [str(item) for item in document_ids if item]
    if ids:
        keys: List[Any] = list(ids) + [ObjectId(item) for item in ids if ObjectId.is_valid(item)]
        rows: List[Dict[str, Any]] = await db.documents.find(
            {"_id": {"$in": keys}},
            {"contract_id": 1, "project_id": 1, "letterNo": 1, "letter_no": 1, "subject": 1, "filename": 1},
        ).to_list(length=None)
        found.extend((document_label(row), row) for row in rows)
    return found
