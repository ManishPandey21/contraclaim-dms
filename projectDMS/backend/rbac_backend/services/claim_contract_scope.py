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

**OD-1 (owner decision 2026-11-17, option B).** Unverified and unidentified
evidence of a contract-specific claim needs an authorised, audited human
confirmation that it belongs to the claim's contract before the submission is
exported or the claim is escalated to arbitration. A confirmation is a separate
record (``claim_evidence_contract_confirmations``: who, when, why, the record's
state when confirmed); the evidence itself is never rewritten. A confirmed
mismatch (a record naming another contract) cannot be confirmed and stays blocked.
Release waits for P-03 and the release-impact assessment.
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


CONFIRMATIONS = "claim_evidence_contract_confirmations"
NEEDS_CONFIRMATION = ("unverified", "unidentified")

#: The fields of a record that a confirmation of its contract rests on. A change to any
#: of them is material: the confirmation no longer holds and the record needs confirming
#: again (owner decision 2026-11-17). A tag, a view or a processing flag is not material.
_MATERIAL_FIELDS = {
    "document": (
        "organization_id", "project_id", "contract_id", "sha256", "current_version_id", "version_id",
        "file_object_id", "subject", "date", "letterNo", "letter_no", "from", "to", "uploadType",
    ),
    "hindrance": (
        "organization_id", "project_id", "contract_id", "title", "start_date", "end_date", "responsibility",
        "reference", "hindrance_no", "revision",
    ),
    "eot_submission": (
        "organization_id", "project_id", "contract_id", "eot_reference", "revision_label", "status",
        "contractor_submission_date", "requested_extension_days",
    ),
    "eot_determination": (
        "organization_id", "project_id", "contract_id", "determination_reference", "revision_label", "status",
        "determination_date", "granted_extension_days",
    ),
}


def record_fingerprint(kind: str, record: Optional[Mapping[str, Any]]) -> str:
    """The record as a confirmation of its contract relies on it (owner decision 2026-11-17)."""
    import hashlib
    import json

    fields = _MATERIAL_FIELDS.get(kind, ("organization_id", "project_id", "contract_id"))
    basis = {name: (record or {}).get(name) for name in fields}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class EvidenceItem:
    """One record the claim's evidence relies on whose contract a person must confirm (OD-1)."""

    kind: str
    record_id: str
    label: str
    state: str  # "unverified" | "unidentified"
    #: The record's fingerprint now; a confirmation must have been given against it.
    revision: str = ""
    #: A confirmation that no longer holds because the record changed materially since.
    superseded_confirmation_id: Optional[str] = None
    confirmation_id: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[Any] = None
    reason: Optional[str] = None
    source_reference: Optional[str] = None


@dataclass
class EvidenceIdentity:
    """The claim's evidence, sorted by what it says about its contract.

    ``unverified`` and ``unidentified`` hold the labels still awaiting a person's
    confirmation; ``unconfirmed`` the same records in full; ``confirmed`` those a
    person has confirmed for the claim's contract."""

    conflicts: List[str] = field(default_factory=list)
    verified: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)
    unidentified: List[str] = field(default_factory=list)
    unconfirmed: List[EvidenceItem] = field(default_factory=list)
    confirmed: List[EvidenceItem] = field(default_factory=list)


Found = Tuple[str, Optional[Mapping[str, Any]], str, str]  # label, record, kind, record id


async def _master_keys(db: Any, claim: Mapping[str, Any]) -> set:
    project = project_of(claim)
    if project is None:
        return set()
    query: Dict[str, Any] = {"project_id": project}
    if claim.get("organization_id"):
        query["organization_id"] = str(claim["organization_id"])
    rows = await db.contract_master.find(query, {"contract_id": 1}).to_list(length=500)
    return {str(row.get("contract_id") or "").strip() for row in rows} - {""}


async def _active_confirmations(db: Any, claim: Mapping[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    contract = contract_of(claim)
    if contract is None:
        return {}
    rows = await db[CONFIRMATIONS].find(
        {"claim_id": str(claim.get("_id")), "contract_id": contract, "status": "active"}
    ).to_list(length=2000)
    return {(str(row["record_kind"]), str(row["record_id"])): row for row in rows}


def _sorted(
    claim: Mapping[str, Any], found: List[Found], master: set, confirmations: Dict[Tuple[str, str], Dict[str, Any]]
) -> EvidenceIdentity:
    result = EvidenceIdentity()
    seen: set = set()
    for label, record, kind, record_id in found:
        if (kind, record_id) in seen:
            continue
        seen.add((kind, record_id))
        if conflicts(claim, record):
            result.conflicts.append(label)
            continue
        if contract_of(record) is not None and contract_of(record) in master:
            result.verified.append(label)
            continue
        state = "unidentified" if contract_of(record) is None else "unverified"
        row = confirmations.get((kind, record_id))
        item = EvidenceItem(kind=kind, record_id=record_id, label=label, state=state, revision=record_fingerprint(kind, record))
        if row is not None:
            # Bound to the record's revision and the claim's contract and project: a material
            # change since, or a different contract identity, and it no longer holds.
            holds = (
                row.get("record_revision") == item.revision
                and str(row.get("project_id") or "") == str(project_of(claim) or "")
            )
            if holds:
                item.confirmation_id = str(row["_id"])
                item.confirmed_by, item.confirmed_at = row.get("confirmed_by"), row.get("confirmed_at")
                item.reason, item.source_reference = row.get("reason"), row.get("source_reference")
                result.confirmed.append(item)
                continue
            item.superseded_confirmation_id = str(row["_id"])
        result.unconfirmed.append(item)
        (result.unidentified if state == "unidentified" else result.unverified).append(label)
    return result


def identity_notes(claim: Mapping[str, Any], identity: EvidenceIdentity) -> List[str]:
    """Plain words for each kind of evidence still awaiting a person's confirmation."""
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


UNCONFIRMED_CODE = "claim_evidence_contract_unconfirmed"


def unconfirmed_reason(claim: Mapping[str, Any], identity: EvidenceIdentity) -> Optional[str]:
    """Why export or escalation must wait (OD-1), or None."""
    if contract_of(claim) is None or not identity.unconfirmed:
        return None
    count = len(identity.unconfirmed)
    return (
        f"{count} record{'s' if count != 1 else ''} this claim relies on {'do' if count != 1 else 'does'} not show "
        "which contract {it} belong{s} to. A person authorised to edit the claim must confirm each one belongs to "
        "the claim's contract first.".format(it="they" if count != 1 else "it", s="" if count != 1 else "s")
    )


async def identity_review(
    db: Any, claim: Mapping[str, Any], links: Iterable[Any], document_ids: Iterable[str]
) -> EvidenceIdentity:
    """Every record the claim's evidence relies on, sorted by its contract identity.

    ``links`` are the claim's active head links (``ClaimHeadLink``); their records are
    read as they are now, so a record moved to another contract after linking is
    caught. ``document_ids`` are the claim's linked documents.
    """
    found = await _records(db, links, document_ids)
    has_contract = contract_of(claim) is not None
    master = await _master_keys(db, claim) if has_contract else set()
    confirmations = await _active_confirmations(db, claim) if has_contract else {}
    return _sorted(claim, found, master, confirmations)


async def evidence_conflicts(db: Any, claim: Mapping[str, Any], links: Iterable[Any], document_ids: Iterable[str]) -> List[str]:
    """Every record the claim's evidence relies on that belongs to another contract."""
    return conflicting_labels(claim, [(label, record) for label, record, _kind, _id in await _records(db, links, document_ids)])


async def _records(db: Any, links: Iterable[Any], document_ids: Iterable[str]) -> List[Found]:
    from bson import ObjectId

    collections = _RECORD_COLLECTIONS
    found: List[Found] = []
    for link in links:
        kind = str(getattr(link, "target_type", ""))
        collection = collections.get(kind)
        if collection is None:
            continue
        target_id = str(getattr(link, "target_id", ""))
        candidates: List[Any] = [target_id]
        if ObjectId.is_valid(target_id):
            candidates.append(ObjectId(target_id))
        projection = {name: 1 for name in _MATERIAL_FIELDS.get(kind, ())} | {"contract_id": 1, "project_id": 1}
        record = await db[collection].find_one({"_id": {"$in": candidates}}, projection)
        found.append((str(getattr(link, "target_label", "") or target_id), record, kind, target_id))
    ids = [str(item) for item in document_ids if item]
    if ids:
        keys: List[Any] = list(ids) + [ObjectId(item) for item in ids if ObjectId.is_valid(item)]
        rows: List[Dict[str, Any]] = await db.documents.find(
            {"_id": {"$in": keys}},
            {name: 1 for name in _MATERIAL_FIELDS["document"]} | {"filename": 1},
        ).to_list(length=None)
        found.extend((document_label(row), row, "document", str(row["_id"])) for row in rows)
    return found


def _refuse(code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "user_message": message, "detail": message})


async def confirm(
    db: Any, claim: Mapping[str, Any], identity: EvidenceIdentity, *, kind: str, record_id: str, reason: str,
    user_id: Optional[str], source_reference: str = "", linked_by: Iterable[Optional[str]] = (),
    confirmer: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Record a person's confirmation that one record belongs to the claim's contract (OD-1).

    Only a record the claim's evidence relies on, whose contract is unknown or not in
    the Contract Master, can be confirmed. A record naming another contract cannot.
    Owner decision 2026-11-17: the confirmer holds ``dms.claim.evidence.confirm_contract``
    by explicit grant (checked by the route), gives a reason and the source that shows
    the contract, and is not a person who linked the record to the claim. The
    confirmation is bound to the record's revision and the claim's contract and project."""
    import uuid

    from ..utils.datetime import now_utc

    contract = contract_of(claim)
    if contract is None:
        raise _refuse(
            422, "evidence_confirmation_contract_required",
            "Set the claim's contract first; evidence is confirmed for that contract.",
        )
    text = (reason or "").strip()
    if len(text) < 10:
        raise _refuse(422, "evidence_confirmation_reason_required", "Say how you know this record belongs to the claim's contract.")
    source = (source_reference or "").strip()
    if len(source) < 3:
        raise _refuse(
            422, "evidence_confirmation_source_required",
            "Name the source that shows which contract this record belongs to (a letter, drawing or register reference).",
        )
    if user_id and user_id in {str(person) for person in linked_by if person}:
        raise _refuse(
            403, "evidence_confirmer_not_independent",
            "The person who linked this record to the claim cannot also confirm its contract. Ask someone else "
            "who holds the authority to confirm evidence.",
        )
    item = next((entry for entry in identity.unconfirmed if entry.kind == kind and entry.record_id == record_id), None)
    if item is None:
        if any(entry.kind == kind and entry.record_id == record_id for entry in identity.confirmed):
            raise _refuse(409, "evidence_already_confirmed", "This record is already confirmed for the claim's contract.")
        raise _refuse(
            409, "evidence_confirmation_not_applicable",
            "Only a record this claim relies on whose contract is unknown or not in the Contract Master can be "
            "confirmed. A record of another contract cannot support the claim.",
        )
    row = {
        "_id": str(uuid.uuid4()),
        "claim_id": str(claim.get("_id")),
        "contract_id": contract,
        "organization_id": claim.get("organization_id"),
        "project_id": claim.get("project_id"),
        "record_kind": kind,
        "record_id": record_id,
        "record_label": item.label,
        "state_at_confirmation": item.state,
        "record_revision": item.revision,
        "superseded_confirmation_id": item.superseded_confirmation_id,
        "reason": text,
        "source_reference": source[:500],
        "confirmed_by": user_id,
        "confirmer": dict(confirmer or {}),
        "confirmed_at": now_utc(),
        "status": "active",
    }
    await db[CONFIRMATIONS].insert_one(row)
    return row


async def withdraw(db: Any, claim: Mapping[str, Any], confirmation_id: str, *, reason: str, user_id: Optional[str]) -> Dict[str, Any]:
    """Withdraw a confirmation: the record needs confirming again. Kept, never deleted."""
    from ..utils.datetime import now_utc

    text = (reason or "").strip()
    if len(text) < 10:
        raise _refuse(422, "evidence_confirmation_reason_required", "Say why the confirmation is withdrawn.")
    row = await db[CONFIRMATIONS].find_one_and_update(
        {"_id": confirmation_id, "claim_id": str(claim.get("_id")), "status": "active"},
        {"$set": {"status": "withdrawn", "withdrawn_by": user_id, "withdrawn_at": now_utc(), "withdrawal_reason": text}},
        return_document=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return row
