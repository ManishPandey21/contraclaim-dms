"""The reviewer workflow that turns expert work into a real golden set (4B-42, plan section 7).

A real set is never typed in one file by one person. It is assembled from four
documents, each written by a different hand, each bound to the others by hash:

1. **Matter book** - the matters, written or selected by the reviewing experts. It
   has no labels: whoever writes a matter does not pre-label it. An anonymised real
   matter names the owner's consent and passes the anonymisation scan.
2. **Two label sheets** - one per expert, each made independently from the same
   matter book (the sheet names the book's hash), each labelling every matter.
3. **Adjudication sheet** - by the legal approver, who is neither expert. It settles
   every label the experts disagree on, with a reason, and touches nothing they agree on.
4. **Assembled set** - built by :func:`assemble` from the three, never edited by hand,
   with a manifest recording every input hash. ``suite_hash`` of the assembled set is
   what a server run records; any later change makes earlier runs stale.

Nothing here invents a label, and nothing here can produce a synthetic set: a matter
book refuses ``source: synthetic``. Real sets and their sheets live in the restricted
evaluation store outside the repository (AQ-10).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evaluation import (
    GOLDEN_SET_REQUIREMENTS,
    EvalClassificationLabel,
    EvalLabels,
    EvalMatter,
    EvalMatterRecord,
    EvalProvenance,
    EvalSuite,
    suite_hash,
    suite_problems,
)
from .executor import canonical_hash

#: What a template leaves for a person to write. A book or sheet that still holds it is refused.
PLACEHOLDER = "TO BE WRITTEN"
INDEPENDENCE_ATTESTATION = (
    "I labelled every matter in this sheet myself, from the matter book named by its hash, "
    "without seeing or discussing the other expert's labels."
)

ELEMENT_STATES = ("supported", "weak", "missing", "not_applicable")

#: The labels each pack is scored on, with the values the pack's output schema allows
#: (models/claim_skill_outputs.py).
LABEL_VOCABULARY: Dict[str, Dict[str, Any]] = {
    "indian-infra-claims": {
        "prompt_key": "claims.v1.head_test",
        "payload": ("facts", "title", "kind", "event_summary", "clauses"),
        "elements": {name: ELEMENT_STATES for name in ("event", "notice", "contractual_basis", "causation", "quantum")},
        "classification": ("classification", ("excusable", "compensable", "contractor_risk", "undetermined")),
    },
    "indian-infra-eot-claims": {
        "prompt_key": "letter_drafting.v3.skill_step",
        "payload": ("task", "subject", "contract_form", "material", "sources", "inputs"),
        "elements": {"criticality": ("critical", "not_critical", "unproven")},
        "classification": ("risk_owner", ("employer", "contractor", "neutral", "undetermined")),
    },
}
ROLES = ("contractor", "engineer", "employer")

#: Identifying data that must not survive anonymisation. A hit is reported, never auto-redacted.
_IDENTIFIERS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("a phone number", re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("a PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("a GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    ("an Aadhaar number", re.compile(r"(?<!\d)\d{4}[\s-]\d{4}[\s-]\d{4}(?!\d)")),
    ("a bank account or IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
class BookProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["anonymised_real", "expert_written"]
    #: Who wrote or selected the matter. They may also be one of its labellers.
    prepared_by: str = Field(..., min_length=1, max_length=120)
    consent_reference: Optional[str] = Field(default=None, max_length=200)
    #: Who removed identifying details from an anonymised real matter.
    anonymised_by: Optional[str] = Field(default=None, max_length=120)


class BookMatter(BaseModel):
    """A matter as written, before anyone labels it. There is deliberately no ``labels`` field."""

    model_config = ConfigDict(extra="forbid")

    matter_id: str = Field(..., min_length=1, max_length=80)
    role: str
    payload: Dict[str, str]
    record: EvalMatterRecord = Field(default_factory=EvalMatterRecord)
    composition: List[str] = Field(default_factory=list)
    provenance: BookProvenance


class MatterBook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_key: str = Field(..., min_length=1, max_length=120)
    skill_key: str
    prompt_key: str
    matters: List[BookMatter] = Field(..., min_length=1)


class LabelSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_key: str
    matter_book_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    expert: str = Field(..., min_length=1, max_length=120)
    attestation: str
    labelled_on: str = Field(..., min_length=1, max_length=40)
    labels: Dict[str, EvalLabels]


class AdjudicatedLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A single state or classification, or for ``information_required`` the settled list.
    value: Any
    reason: str = Field(..., min_length=10, max_length=2000)


class AdjudicationSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_key: str
    matter_book_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    label_sheet_hashes: List[str] = Field(..., min_length=2, max_length=2)
    adjudicator: str = Field(..., min_length=1, max_length=120)
    adjudicated_on: str = Field(..., min_length=1, max_length=40)
    #: matter id -> label key (``elements.<name>``, ``classification``, ``information_required``) -> decision
    decisions: Dict[str, Dict[str, AdjudicatedLabel]] = Field(default_factory=dict)


def document_hash(document: BaseModel) -> str:
    return canonical_hash(document.model_dump(mode="json"))


def _vocabulary(skill_key: str) -> Dict[str, Any]:
    vocabulary = LABEL_VOCABULARY.get(skill_key)
    if vocabulary is None:
        raise ValueError(f"no golden-set workflow is defined for {skill_key}")
    return vocabulary


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def _slot_tags(skill_key: str) -> List[List[str]]:
    """One tag list per matter slot, so a book filled slot by slot meets the composition rules."""
    requirement = GOLDEN_SET_REQUIREMENTS[skill_key]
    slots: List[List[str]] = []
    for rule in requirement["rules"]:
        if rule.tag == "perspective":
            continue
        already = sum(rule.tag in tags for tags in slots)
        if rule.tag.startswith("late_notice_") and already < rule.minimum:
            # Each prejudice case is also a late-notice matter.
            for tags in slots:
                if tags == ["late_notice"] and already < rule.minimum:
                    tags.append(rule.tag)
                    already += 1
        slots.extend([[rule.tag] for _ in range(rule.minimum - already)])
    while len(slots) < requirement["matters"]:
        slots.append([])
    return slots


def book_template(skill_key: str) -> Dict[str, Any]:
    """A blank matter book: the pack's slots with their composition tags, every field to be written."""
    vocabulary = _vocabulary(skill_key)
    requirement = GOLDEN_SET_REQUIREMENTS[skill_key]
    perspective = next((rule.minimum for rule in requirement["rules"] if rule.tag == "perspective"), 0)
    matters = []
    for index, tags in enumerate(_slot_tags(skill_key), start=1):
        matters.append({
            "matter_id": f"M{index:02d}",
            "role": "engineer" if index <= perspective else PLACEHOLDER,
            "payload": {name: PLACEHOLDER for name in vocabulary["payload"]},
            "record": {"clauses": [PLACEHOLDER], "notice_days": [], "privileged_markers": [PLACEHOLDER]},
            "composition": tags,
            "provenance": {"source": "expert_written", "prepared_by": PLACEHOLDER,
                           "consent_reference": None, "anonymised_by": None},
        })
    return {"suite_key": f"{skill_key}-real-{PLACEHOLDER}", "skill_key": skill_key,
            "prompt_key": vocabulary["prompt_key"], "matters": matters}


def label_sheet_template(book: MatterBook, expert: str) -> Dict[str, Any]:
    """A blank sheet for one expert, bound to the book. Every value is to be written."""
    vocabulary = _vocabulary(book.skill_key)
    field, _ = vocabulary["classification"]
    return {
        "skill_key": book.skill_key,
        "matter_book_hash": document_hash(book),
        "expert": expert,
        "attestation": PLACEHOLDER,
        "labelled_on": PLACEHOLDER,
        "labels": {
            matter.matter_id: {
                "elements": {name: PLACEHOLDER for name in vocabulary["elements"]},
                "classification": {"field": field, "expected": PLACEHOLDER},
                "information_required": [],
            }
            for matter in book.matters
        },
    }


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def anonymisation_hits(book: MatterBook, names: Sequence[str] = ()) -> List[str]:
    """Identifying details left in the matters. ``names`` are the parties, people and places to look for."""
    hits: List[str] = []
    wanted = [name.strip() for name in names if name.strip()]
    for matter in book.matters:
        text = "\n".join(_strings(matter.payload)) + "\n" + "\n".join(_strings(matter.record.model_dump()))
        for meaning, pattern in _IDENTIFIERS:
            if pattern.search(text):
                hits.append(f"{matter.matter_id}: contains {meaning}")
        for name in wanted:
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
                hits.append(f"{matter.matter_id}: names '{name}'")
    return hits


def book_problems(book: MatterBook, names: Sequence[str] = ()) -> List[str]:
    vocabulary = _vocabulary(book.skill_key)
    problems: List[str] = []
    if book.prompt_key != vocabulary["prompt_key"]:
        problems.append(f"prompt_key is {book.prompt_key}; the pack's prompt is {vocabulary['prompt_key']}")
    if PLACEHOLDER in book.suite_key:
        problems.append("suite_key is still the template's")
    seen: set[str] = set()
    for matter in book.matters:
        where = matter.matter_id
        if where in seen:
            problems.append(f"{where}: matter id used twice")
        seen.add(where)
        if any(PLACEHOLDER in text for text in _strings(matter.model_dump(mode="json"))):
            problems.append(f"{where}: still holds '{PLACEHOLDER}'")
        if matter.role not in ROLES:
            problems.append(f"{where}: role must be one of {', '.join(ROLES)}")
        if set(matter.payload) != set(vocabulary["payload"]):
            problems.append(f"{where}: payload must have exactly {', '.join(vocabulary['payload'])}")
        if not matter.record.clauses:
            problems.append(f"{where}: record names no clause")
        origin = matter.provenance
        if origin.source == "anonymised_real":
            if not origin.consent_reference:
                problems.append(f"{where}: an anonymised real matter needs the owner's consent reference")
            if not origin.anonymised_by:
                problems.append(f"{where}: an anonymised real matter names who anonymised it")
    problems.extend(anonymisation_hits(book, names))
    problems.extend(_composition_problems(book))
    return problems


def _composition_problems(book: MatterBook) -> List[str]:
    """The composition part of ``suite_problems``, read before any label exists."""
    probe = EvalSuite(
        suite_key=book.suite_key, skill_key=book.skill_key, prompt_key=book.prompt_key,
        matters=[
            EvalMatter(matter_id=m.matter_id, role=m.role, payload=m.payload, record=m.record,
                       labels=EvalLabels(), composition=m.composition,
                       provenance=EvalProvenance(source=m.provenance.source, labelled_by=["a", "b"],
                                                 adjudicated_by="c", consent_reference="probe"))
            for m in book.matters
        ],
    )
    return suite_problems(probe)


def sheet_problems(book: MatterBook, sheet: LabelSheet) -> List[str]:
    vocabulary = _vocabulary(book.skill_key)
    field, classes = vocabulary["classification"]
    problems: List[str] = []
    who = sheet.expert
    if sheet.skill_key != book.skill_key:
        problems.append(f"{who}: sheet is for {sheet.skill_key}, the book for {book.skill_key}")
    if sheet.matter_book_hash != document_hash(book):
        problems.append(f"{who}: sheet was made from another version of the matter book")
    if sheet.attestation != INDEPENDENCE_ATTESTATION:
        problems.append(f"{who}: the independence attestation is missing or altered")
    if PLACEHOLDER in sheet.labelled_on or PLACEHOLDER in sheet.expert:
        problems.append(f"{who}: expert or date not filled in")
    wanted = [matter.matter_id for matter in book.matters]
    missing = [matter_id for matter_id in wanted if matter_id not in sheet.labels]
    extra = sorted(set(sheet.labels) - set(wanted))
    if missing:
        problems.append(f"{who}: no labels for {', '.join(missing)}")
    if extra:
        problems.append(f"{who}: labels for matters not in the book: {', '.join(extra)}")
    for matter_id, labels in sheet.labels.items():
        if set(labels.elements) != set(vocabulary["elements"]):
            problems.append(f"{who} {matter_id}: elements must be exactly {', '.join(vocabulary['elements'])}")
        for name, state in labels.elements.items():
            allowed = vocabulary["elements"].get(name, ())
            if state not in allowed:
                problems.append(f"{who} {matter_id}: {name} '{state}' is not one of {', '.join(allowed)}")
        label = labels.classification
        if label is None or label.field != field or label.expected not in classes:
            problems.append(f"{who} {matter_id}: {field} must be one of {', '.join(classes)}")
    return problems


def label_values(labels: EvalLabels) -> Dict[str, Any]:
    values: Dict[str, Any] = {f"elements.{name}": state for name, state in labels.elements.items()}
    values["classification"] = labels.classification.expected if labels.classification else None
    values["information_required"] = sorted({item.strip() for item in labels.information_required if item.strip()})
    return values


def disagreements(first: LabelSheet, second: LabelSheet) -> Dict[str, List[str]]:
    """Matter id -> the label keys on which the two experts differ."""
    found: Dict[str, List[str]] = {}
    for matter_id in sorted(set(first.labels) & set(second.labels)):
        a = label_values(first.labels[matter_id])
        b = label_values(second.labels[matter_id])
        keys = sorted(key for key in set(a) | set(b) if a.get(key) != b.get(key))
        if keys:
            found[matter_id] = keys
    return found


def _allowed(skill_key: str, key: str, value: Any) -> bool:
    vocabulary = _vocabulary(skill_key)
    if key.startswith("elements."):
        return value in vocabulary["elements"].get(key.split(".", 1)[1], ())
    if key == "classification":
        return value in vocabulary["classification"][1]
    if key == "information_required":
        return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
    return False


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
class AssemblyRefused(ValueError):
    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def assemble(
    book: MatterBook,
    first: LabelSheet,
    second: LabelSheet,
    adjudication: AdjudicationSheet,
    names: Sequence[str] = (),
) -> Tuple[EvalSuite, Dict[str, Any]]:
    """The golden set and its manifest, or :class:`AssemblyRefused` with every reason."""
    problems = book_problems(book, names) + sheet_problems(book, first) + sheet_problems(book, second)
    experts = {first.expert.strip(), second.expert.strip()}
    if len(experts) < 2:
        problems.append("both label sheets are by the same expert; two independent experts are needed")
    if adjudication.adjudicator.strip() in experts:
        problems.append("the adjudicator labelled the matters; the legal approver must be a third person")
    if adjudication.skill_key != book.skill_key or adjudication.matter_book_hash != document_hash(book):
        problems.append("the adjudication sheet was made for another matter book")
    if sorted(adjudication.label_sheet_hashes) != sorted([document_hash(first), document_hash(second)]):
        problems.append("the adjudication sheet names other label sheets")
    if PLACEHOLDER in adjudication.adjudicated_on:
        problems.append("the adjudication date is not filled in")

    differ = disagreements(first, second)
    for matter_id, keys in differ.items():
        decided = adjudication.decisions.get(matter_id, {})
        for key in keys:
            decision = decided.get(key)
            if decision is None:
                problems.append(f"{matter_id}: '{key}' is disputed and not adjudicated")
            elif not _allowed(book.skill_key, key, decision.value):
                problems.append(f"{matter_id}: adjudicated '{key}' value {decision.value!r} is not allowed")
    for matter_id, decided in adjudication.decisions.items():
        for key in decided:
            if key not in differ.get(matter_id, []):
                problems.append(f"{matter_id}: '{key}' was agreed by both experts; the adjudicator may not change it")
    if problems:
        raise AssemblyRefused(problems)

    field, _ = _vocabulary(book.skill_key)["classification"]
    matters: List[EvalMatter] = []
    for matter in book.matters:
        values = label_values(first.labels[matter.matter_id])
        for key, decision in adjudication.decisions.get(matter.matter_id, {}).items():
            values[key] = sorted(decision.value) if key == "information_required" else decision.value
        labels = EvalLabels(
            elements={key.split(".", 1)[1]: value for key, value in values.items() if key.startswith("elements.")},
            classification=EvalClassificationLabel(field=field, expected=values["classification"]),
            information_required=values["information_required"],
        )
        matters.append(EvalMatter(
            matter_id=matter.matter_id, role=matter.role, payload=matter.payload, record=matter.record,
            labels=labels, composition=matter.composition,
            provenance=EvalProvenance(
                source=matter.provenance.source,
                labelled_by=sorted(experts),
                adjudicated_by=adjudication.adjudicator.strip(),
                disagreements=differ.get(matter.matter_id, []),
                consent_reference=matter.provenance.consent_reference,
            ),
        ))
    suite = EvalSuite(suite_key=book.suite_key, skill_key=book.skill_key, prompt_key=book.prompt_key, matters=matters)
    remaining = suite_problems(suite)
    if remaining:
        raise AssemblyRefused(remaining)
    manifest = {
        "suite_key": suite.suite_key,
        "skill_key": suite.skill_key,
        "suite_hash": suite_hash(suite),
        "matter_book_hash": document_hash(book),
        "label_sheets": sorted(
            ({"expert": sheet.expert, "hash": document_hash(sheet)} for sheet in (first, second)),
            key=lambda item: item["expert"],
        ),
        "adjudication": {"adjudicator": adjudication.adjudicator, "hash": document_hash(adjudication)},
        "matters": len(matters),
        "disputed_labels": sum(len(keys) for keys in differ.values()),
        "assembled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return suite, manifest


def verify(suite: EvalSuite, manifest: Dict[str, Any]) -> List[str]:
    """Whether a stored set is still the one its manifest recorded."""
    problems: List[str] = []
    if suite_hash(suite) != manifest.get("suite_hash"):
        problems.append("the golden set has changed since it was assembled; assemble it again from its sheets")
    problems.extend(suite_problems(suite))
    return problems


def parse(model: type[BaseModel], data: Any, label: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise AssemblyRefused([f"{label}: {item['loc']}: {item['msg']}" for item in error.errors()]) from error
