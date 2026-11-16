"""Submission Pack: the claim submission, composed from the locked board.

Six sections, in the order the claims skill sets out: executive summary,
contractual basis, chronology and evidence, delay analysis, quantum, and
relief with reservation of rights.

Each section is first composed from the record, by code: the heads as
decided, clause wording quoted from the project's clause index, the delay
assessment's own figures, the cost lines whose proof is linked, the documents
linked to the claim in date order. That composition is the section when no
published claims skill exists, and it is marked as such.

When a published skill exists it may re-express a section, and its text is
accepted only if it passes three checks against the record composition:

- ``no_invented_amounts``: every number in it is a number on the record;
- ``exact_sub_clause_cited``: every clause it names is one the heads cite;
- ``reservation_of_rights``: the last section still reserves rights.

Text that fails is discarded and the record composition is used, marked
degraded with the reason.

What never enters a submission: a notice period that has not been verified
against the contract (the submission then says only when notice was given), a
cost line whose proof is not linked, the strategy note, and any simulation.

A section is stale when the record it was composed from has changed. Stale
sections are re-composed; the others are left alone.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from xml.sax.saxutils import escape

from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..models.claim import ClaimBench, ClaimHead, NoticeAssessment
from ..models.claim_findings import (
    SECTION_ORDER,
    SECTION_TITLES,
    ClaimSubmission,
    EvidenceItem,
    ParagraphSource,
    SubmissionParagraph,
    SubmissionSection,
)
from ..models.claim_skill_outputs import SUBMISSION_SECTION_SCHEMA_KEY, SubmissionSectionOutput
from ..utils.datetime import now_utc
from .claim_delay_lab import ClaimDelayLab, DelayView, build_view
from .claim_quantum import ClaimQuantum, QuantumView
from .docx_xml import w_paragraph, w_table
from .letter_drafting.prompts import UNTRUSTED_CONTENT_GUARD, register_default_template
from .submission_figures import clauses_in, invented_figures, reserves_rights
from .skills.executor import canonical_hash
from .skills.system_skills import CLAIMS_SKILL_KEY
from .sla_service import _as_datetime

logger = logging.getLogger(__name__)

SUBMISSIONS = "claim_submissions"
MAX_QUOTE_CHARS = 1500
RESERVATION_WORDING = (
    "The Contractor reserves its rights in respect of the matters set out in this submission, including "
    "the right to further time and money arising from them and to supplement these particulars as "
    "further records become available."
)

SUBMISSION_SECTION_PROMPT = "claims.v1.submission_section"
SUBMISSION_SECTION_TEMPLATE = (
    """TASK: apply the skill above to write ONE section of a claim submission, for the {role}.

Section: {section_title}

The record for this section. It is the whole of what may be said:
{record}

OUTPUT: return one JSON object and nothing else, with exactly these keys:
{output_keys}

Rules:
- Re-express the record as formal submission prose. Add nothing to it.
- Every number, date, amount and number of days must be one that appears in the record, unchanged.
- Name only the clauses the record names. Keep quoted clause wording exactly as given, in quotation marks.
- Do not argue timeliness of notice unless the record states the contractual period.
- If the record contains a reservation of rights, keep one.

"""
    + UNTRUSTED_CONTENT_GUARD
    + "\n"
)
register_default_template(SUBMISSION_SECTION_PROMPT, SUBMISSION_SECTION_TEMPLATE)


# --------------------------------------------------------------------------- #
# Requests and responses
# --------------------------------------------------------------------------- #
class ComposeRequest(BaseModel):
    # Sections to re-compose even if their record has not changed. Stale and
    # missing sections are always composed.
    sections: List[str] = Field(default_factory=list)


class SectionView(BaseModel):
    section: SubmissionSection
    stale: bool = False


class SubmissionView(BaseModel):
    claim_id: str
    exists: bool = False
    sections: List[SectionView] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    composed_at: Optional[datetime] = None
    can_compose: bool = False
    cannot_compose_reason: Optional[str] = None
    stale_sections: int = 0
    # False until the claim has been approved through its approval workflow;
    # an export made before then is marked as a draft.
    approved_for_issue: bool = False
    can_export: bool = False
    cannot_export_reason: Optional[str] = None


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def format_day(value: Any) -> str:
    moment = _as_datetime(value)
    return f"{moment.day} {moment.strftime('%B %Y')}" if moment else "date not recorded"


def format_amount(value: float, currency: str = "INR") -> str:
    """Indian digit grouping: 1,23,45,678.00."""
    whole, _, fraction = f"{abs(float(value)):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{currency} {'-' if value < 0 else ''}{whole}.{fraction}"


def _days(count: int) -> str:
    return f"{count} day{'' if count == 1 else 's'}"


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CLAUSE = re.compile(r"(?:Sub-)?Clause\s+([0-9]+(?:\.[0-9]+)*[A-Za-z]?)", re.IGNORECASE)


def numbers_in(text: str) -> Set[str]:
    found: Set[str] = set()
    for token in _NUMBER.findall(text or ""):
        token = token.replace(",", "").rstrip(".")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        if token:
            found.add(token)
    return found


def validate_section_text(key: str, proposed: List[str], record: List[str], cited_clauses: Set[str]) -> List[str]:
    """Reasons a skill's section text cannot be used. Empty means it can.

    4B-28: figures are typed and scaled (money, days, plain numbers; lakh, crore
    and number words), checked per head where a paragraph is about one head; a
    clause may be written "Cl." or "Article"; the reservation must be stated, not
    negated (``submission_figures``).
    """
    reasons: List[str] = []
    text = "\n".join(proposed)
    if not text.strip():
        return ["no text was returned"]
    invented = invented_figures(proposed, record)
    if invented:
        reasons.append("no_invented_amounts: figures not on the record (" + "; ".join(invented[:6]) + ")")
    base_clauses = set().union(*(clauses_in(paragraph) for paragraph in record)) if record else set()
    uncited = sorted(clauses_in(text) - set(cited_clauses) - base_clauses)
    if uncited:
        reasons.append("exact_sub_clause_cited: clauses the heads do not cite (" + ", ".join(uncited[:6]) + ")")
    if key == "relief_and_reservation" and not reserves_rights(text):
        reasons.append("reservation_of_rights: the reservation of rights is missing or negated")
    return reasons


# --------------------------------------------------------------------------- #
# Record composition (pure)
# --------------------------------------------------------------------------- #
class SubmissionRecord(BaseModel):
    """Everything a submission is composed from, gathered once."""

    claim_ref: str
    title: str
    currency: str = "INR"
    event_date: Optional[datetime] = None
    awareness_date: Optional[datetime] = None
    heads: List[ClaimHead] = Field(default_factory=list)
    notice: Optional[NoticeAssessment] = None
    # clause number -> {"found", "title", "document_type", "wording"}
    clauses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    delay: Dict[str, DelayView] = Field(default_factory=dict)
    quantum: Dict[str, QuantumView] = Field(default_factory=dict)
    evidence: List[EvidenceItem] = Field(default_factory=list)


def _p(text: str, *sources: ParagraphSource) -> SubmissionParagraph:
    return SubmissionParagraph(text=text, sources=list(sources))


def _src(kind: str, ref: str = "", label: str = "") -> ParagraphSource:
    return ParagraphSource(kind=kind, ref=ref, label=label)  # type: ignore[arg-type]


def head_days(record: SubmissionRecord, head: ClaimHead) -> tuple[Optional[int], bool]:
    """The days a head asks for, and whether they come from a delay assessment."""
    if head.kind == "cost":
        return None, False
    view = record.delay.get(head.head_id)
    if view is not None and view.events and view.net.settled and view.net.days is not None:
        return view.net.days, True
    return head.eot_days_claimed, False


def head_amount(record: SubmissionRecord, head: ClaimHead) -> tuple[Optional[float], bool]:
    """The amount a head asks for, and whether it comes from a build-up. Locked lines are left out."""
    if head.kind == "time":
        return None, False
    view = record.quantum.get(head.head_id)
    if view is not None and view.lines:
        return view.supported_total, True
    return head.amount_claimed, False


def _kind(head: ClaimHead) -> str:
    return {"time": "time", "cost": "cost", "time_and_cost": "time and cost"}[head.kind]


def compose_record_sections(record: SubmissionRecord) -> Dict[str, SubmissionSection]:
    claim_source = _src("claim", record.claim_ref, f"Claim {record.claim_ref}")
    sections: Dict[str, SubmissionSection] = {}

    def _section(key: str, paragraphs: List[SubmissionParagraph], notes: List[str]) -> None:
        sections[key] = SubmissionSection(
            key=key,  # type: ignore[arg-type]
            title=SECTION_TITLES[key],
            paragraphs=paragraphs,
            internal_notes=notes,
            input_hash=canonical_hash(
                {"paragraphs": [paragraph.model_dump(mode="json") for paragraph in paragraphs], "notes": notes}
            ),
        )

    # -------------------------------------------------- executive summary
    paragraphs: List[SubmissionParagraph] = [
        _p(f"This submission is made by the Contractor in respect of claim {record.claim_ref}: {record.title}.", claim_source)
    ]
    notes: List[str] = []
    paragraphs.append(
        _p(
            f"It comprises {len(record.heads)} head{'' if len(record.heads) == 1 else 's'} of claim: "
            + "; ".join(f"{head.head_id}, {head.title} ({_kind(head)})" for head in record.heads)
            + ".",
            *[_src("head", head.head_id, head.title) for head in record.heads],
        )
    )
    total = 0.0
    any_amount = False
    for head in record.heads:
        days, assessed = head_days(record, head)
        amount, built = head_amount(record, head)
        parts: List[str] = []
        sources = [_src("head", head.head_id, head.title)]
        if days is not None:
            parts.append(f"an extension of time of {_days(days)}")
            if assessed:
                sources.append(_src("delay", head.head_id, f"Delay assessment of {head.head_id}"))
            else:
                notes.append(f"{head.head_id}: the days are the figure entered on the head; no delay assessment supports them.")
        if amount is not None:
            parts.append(f"payment of {format_amount(amount, record.currency)}")
            total += amount
            any_amount = True
            if built:
                sources.append(_src("quantum", head.head_id, f"Quantum build-up of {head.head_id}"))
                locked = record.quantum[head.head_id].locked_count
                if locked:
                    notes.append(
                        f"{head.head_id}: {locked} cost line{'' if locked == 1 else 's'} "
                        f"({format_amount(record.quantum[head.head_id].locked_total, record.currency)}) left out: proof not linked."
                    )
            else:
                notes.append(f"{head.head_id}: the amount is the figure entered on the head; it has no build-up.")
        if parts:
            paragraphs.append(_p(f"Under head {head.head_id} the Contractor claims " + " and ".join(parts) + ".", *sources))
    if any_amount and len(record.heads) > 1:
        paragraphs.append(_p(f"The total amount claimed is {format_amount(total, record.currency)}.", claim_source))
    _section("executive_summary", paragraphs, notes)

    # --------------------------------------------------- contractual basis
    paragraphs, notes = [], []
    for head in record.heads:
        if not head.clause_refs:
            notes.append(f"{head.head_id}: no clause is cited, so the submission states no contractual basis for it.")
            continue
        for ref in head.clause_refs:
            clause = record.clauses.get(ref) or {}
            if not clause.get("found"):
                paragraphs.append(_p(f"Head {head.head_id} relies on Sub-Clause {ref}.", _src("head", head.head_id, head.title)))
                notes.append(f"{head.head_id}: Sub-Clause {ref} is not in the project's clause index, so its wording is not quoted.")
                continue
            wording = " ".join(str(clause.get("wording") or "").split())
            cut = len(wording) > MAX_QUOTE_CHARS
            quoted = wording[:MAX_QUOTE_CHARS].rstrip() + (" [...]" if cut else "")
            title = f" ({clause['title']})" if clause.get("title") else ""
            paragraphs.append(
                _p(
                    f"Head {head.head_id} relies on Sub-Clause {ref}{title}, which provides: “{quoted}”",
                    _src("head", head.head_id, head.title),
                    _src("clause", ref, f"Clause {ref}, {clause.get('document_type') or 'contract'}"),
                )
            )
            if cut:
                notes.append(f"{head.head_id}: the quotation of Sub-Clause {ref} is cut short; the full clause is in the contract.")
    notice = record.notice
    if notice is None or notice.served_on is None:
        notes.append("No notice is recorded as served, so the submission says nothing about notice.")
    else:
        text = f"Notice of the claim was given on {format_day(notice.served_on)}."
        sources = [_src("notice", record.claim_ref, "Notice date on the claim")]
        if notice.period_source == "contract_verified":
            basis = "becoming aware of the event" if notice.basis == "awareness" else "the event"
            text += f" Sub-Clause {notice.clause_no} requires notice within {_days(notice.period_days)} of {basis}"
            if notice.state == "served_late":
                text += f"; notice was given {_days(notice.served_late_by_days or 0)} after that period."
                notes.append("Notice was given after the verified contractual period. The submission states this as the record shows it.")
            else:
                text += "; notice was given within that period."
            sources.append(_src("clause", str(notice.clause_no or ""), f"Clause {notice.clause_no}, notice period verified"))
        else:
            notes.append(
                "The notice period has not been verified against the contract, so the submission states only "
                "the date notice was given and says nothing about whether it was in time."
            )
        paragraphs.append(_p(text, *sources))
    _section("contractual_basis", paragraphs, notes)

    # ----------------------------------------------- chronology and evidence
    dated: List[tuple[datetime, str, ParagraphSource]] = []
    if record.event_date:
        dated.append((record.event_date, "The event giving rise to the claim occurred.", claim_source))
    if record.awareness_date:
        dated.append((record.awareness_date, "The Contractor became aware of the event.", claim_source))
    if notice is not None and notice.served_on is not None:
        dated.append((notice.served_on, "Notice of the claim was given.", _src("notice", record.claim_ref, "Notice date on the claim")))
    seen_events: Set[str] = set()
    for head in record.heads:
        view = record.delay.get(head.head_id)
        for event in view.events if view else []:
            if event.event_id in seen_events:
                continue
            seen_events.add(event.event_id)
            until = f" to {format_day(event.end_date)}" if event.end_date else " (continuing)"
            dated.append(
                (event.start_date, f"{event.label}: {event.title}, from {format_day(event.start_date)}{until}.", _src("delay", event.event_id, event.label))
            )
    dated.sort(key=lambda item: (item[0].replace(tzinfo=None), item[1]))
    paragraphs = [_p(f"{format_day(moment)}: {text}", source) for moment, text, source in dated]
    notes = []
    if record.evidence:
        paragraphs.append(
            _p(
                f"The documents relied on are listed in the evidence schedule as {record.evidence[0].number} to {record.evidence[-1].number}."
                if len(record.evidence) > 1
                else f"The document relied on is listed in the evidence schedule as {record.evidence[0].number}.",
                *[_src("document", item.document_id, f"{item.number} {item.title}") for item in record.evidence],
            )
        )
    else:
        notes.append("No document is linked to the claim, so there is no evidence schedule.")
    _section("chronology_and_evidence", paragraphs, notes)

    # ------------------------------------------------------ delay analysis
    paragraphs, notes = [], []
    for head in record.heads:
        if head.kind == "cost":
            continue
        view = record.delay.get(head.head_id)
        source = _src("delay", head.head_id, f"Delay assessment of {head.head_id}")
        if view is None or not view.events:
            notes.append(f"{head.head_id}: no delay assessment has been made; the section says nothing about how its days arise.")
            continue
        method = {"as_planned_vs_as_built": "as-planned against as-built", "windows": "windows"}.get(view.method or "", view.method or "not stated")
        paragraphs.append(_p(f"Head {head.head_id}: the delay is assessed on the {method} method.", source))
        for event in view.events:
            if not event.counted or event.days is None:
                continue
            owner = (event.decision.risk_owner or "undetermined").replace("_", " ")
            until = format_day(event.end_date) if event.end_date else format_day(view.as_of_date)
            paragraphs.append(
                _p(
                    f"{event.label} ({event.title}) ran from {format_day(event.start_date)} to {until}, {_days(event.days)}, "
                    f"and is a critical delay at the risk of the {owner.title() if owner != 'neutral' else 'neither party'}.",
                    _src("delay", event.event_id, event.label),
                )
            )
        for overlap in view.overlaps:
            if overlap.finding is None:
                continue
            paragraphs.append(
                _p(
                    f"From {format_day(overlap.start_date)} to {format_day(overlap.end_date)} ({_days(overlap.days)}) "
                    f"Employer and Contractor delay ran together. {overlap.finding.basis.rstrip('.')}. "
                    f"{_days(overlap.days_to_eot or 0).capitalize()} of this period {'is' if (overlap.days_to_eot or 0) == 1 else 'are'} included in the extension claimed.",
                    source,
                )
            )
        if view.net.previously_granted:
            paragraphs.append(_p(f"{_days(view.net.previously_granted).capitalize()} have already been granted and are deducted.", source))
        if view.net.settled and view.net.days is not None:
            paragraphs.append(_p(f"The extension of time claimed under head {head.head_id} is {_days(view.net.days)}.", source))
    if not paragraphs and not notes:
        paragraphs.append(_p("No extension of time is claimed in this submission.", claim_source))
    _section("delay_analysis", paragraphs, notes)

    # ------------------------------------------------------------- quantum
    paragraphs, notes = [], []
    for head in record.heads:
        if head.kind == "time":
            continue
        view = record.quantum.get(head.head_id)
        source = _src("quantum", head.head_id, f"Quantum build-up of {head.head_id}")
        if view is None or not view.lines:
            if head.amount_claimed is not None:
                paragraphs.append(
                    _p(f"Head {head.head_id}: the amount claimed is {format_amount(head.amount_claimed, record.currency)}.", _src("head", head.head_id, head.title))
                )
                notes.append(f"{head.head_id}: the amount has no build-up; the section states the figure only.")
            continue
        for item in view.lines:
            if item.locked:
                notes.append(f"{head.head_id}: “{item.line.description}” is left out. " + " ".join(item.lock_reasons))
                continue
            period = (
                f", for {format_day(item.line.period_start)} to {format_day(item.line.period_end)}"
                if item.line.period_start and item.line.period_end
                else ""
            )
            paragraphs.append(
                _p(
                    f"Head {head.head_id}, {item.line.category.replace('_', ' ')}: {item.line.description}{period}: "
                    f"{format_amount(item.line.amount, record.currency)}.",
                    source,
                    *[_src("document", proof.document_id, _evidence_label(record, proof.document_id)) for proof in item.line.records],
                )
            )
        paragraphs.append(_p(f"The amount claimed under head {head.head_id} is {format_amount(view.supported_total, record.currency)}.", source))
    if not paragraphs and not notes:
        paragraphs.append(_p("No payment is claimed in this submission.", claim_source))
    _section("quantum", paragraphs, notes)

    # ------------------------------------------------ relief and reservation
    relief: List[str] = []
    sources = []
    for head in record.heads:
        days, _assessed = head_days(record, head)
        amount, _built = head_amount(record, head)
        if days is not None:
            relief.append(f"an extension of time of {_days(days)} under head {head.head_id}")
        if amount is not None:
            relief.append(f"payment of {format_amount(amount, record.currency)} under head {head.head_id}")
        sources.append(_src("head", head.head_id, head.title))
    paragraphs = []
    if relief:
        paragraphs.append(_p("The Contractor requests: " + "; ".join(relief) + ".", *sources))
    paragraphs.append(_p(RESERVATION_WORDING, _src("standard_wording", "reservation_of_rights", "Standard reservation wording")))
    _section("relief_and_reservation", paragraphs, [] if relief else ["No head states days or an amount, so no relief is quantified."])
    return sections


def _evidence_label(record: SubmissionRecord, document_id: str) -> str:
    for item in record.evidence:
        if item.document_id == document_id:
            return f"{item.number} {item.title}".strip()
    return "Linked document"


def evidence_schedule(documents: List[Dict[str, Any]]) -> List[EvidenceItem]:
    """Linked documents in date order, numbered. Undated documents come last, by title."""
    rows = []
    for document in documents:
        title = str(document.get("subject") or document.get("original_filename") or document.get("filename") or "Document")
        rows.append((_as_datetime(document.get("date")), title, document))
    rows.sort(key=lambda row: (row[0] is None, row[0].replace(tzinfo=None) if row[0] else datetime.min, row[1].lower(), str(row[2].get("_id"))))
    return [
        EvidenceItem(
            number=f"E-{index}",
            document_id=str(document.get("_id")),
            title=title[:300],
            reference=(str(document.get("letter_no") or document.get("letterNo") or "") or None),
            date=moment,
        )
        for index, (moment, title, document) in enumerate(rows, start=1)
    ]


# --------------------------------------------------------------------------- #
# Export (pure)
# --------------------------------------------------------------------------- #
DRAFT_MARK = "DRAFT. Not approved for issue."


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _w_paragraph(text: str, *, size: int = 22, bold: bool = False, space_before: int = 0) -> str:
    # 4B-29: the shared writer removes XML-forbidden characters as well as escaping.
    return w_paragraph(text, size=size, bold=bold, space_before=space_before)


def _w_table(rows: List[List[str]]) -> str:
    # 4B-29: with a w:tblGrid, which Word requires, and equal-length rows.
    return w_table(rows, header=True, size=18)


def submission_docx(submission: ClaimSubmission, heading: str, approved: bool) -> bytes:
    """A plain Word document, written directly: the backend has no DOCX library among its dependencies."""
    import zipfile

    parts: List[str] = [_w_paragraph(heading, size=32, bold=True)]
    if not approved:
        parts.append(_w_paragraph(DRAFT_MARK, size=22, bold=True))
    for number, section in enumerate(submission.sections, start=1):
        parts.append(_w_paragraph(f"{number}. {section.title}", size=26, bold=True, space_before=240))
        parts.extend(_w_paragraph(paragraph.text) for paragraph in section.paragraphs)
    if submission.evidence:
        parts.append(_w_paragraph("Evidence schedule", size=26, bold=True, space_before=240))
        rows = [["No.", "Date", "Reference", "Document"]]
        rows.extend(
            [item.number, format_day(item.date) if item.date else "", item.reference or "", item.title]
            for item in submission.evidence
        )
        parts.append(_w_table(rows))
        parts.append(_w_paragraph(""))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{"".join(parts)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def submission_pdf(submission: ClaimSubmission, heading: str, approved: bool) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=heading)
    styles = getSampleStyleSheet()
    flow: List[Any] = [Paragraph(escape(heading), styles["Heading1"])]
    if not approved:
        flow.append(Paragraph(escape(DRAFT_MARK), styles["Heading4"]))
    for number, section in enumerate(submission.sections, start=1):
        flow.append(Paragraph(escape(f"{number}. {section.title}"), styles["Heading2"]))
        for paragraph in section.paragraphs:
            flow.append(Paragraph(escape(paragraph.text), styles["BodyText"]))
            flow.append(Spacer(1, 4))
    if submission.evidence:
        flow.append(Paragraph("Evidence schedule", styles["Heading2"]))
        data = [["No.", "Date", "Reference", "Document"]]
        for item in submission.evidence:
            data.append(
                [item.number, format_day(item.date) if item.date else "", item.reference or "", Paragraph(escape(item.title), styles["BodyText"])]
            )
        table = Table(data, repeatRows=1, colWidths=[40, 90, 110, 240])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        flow.append(table)
    doc.build(flow)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class ClaimSubmissions:
    def __init__(self, db: Any, bench: Any) -> None:
        self.db = db
        self.bench = bench

    @property
    def _collection(self) -> Any:
        return self.db[SUBMISSIONS]

    async def _documents(self, document_ids: Set[str]) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for document_id in sorted(document_ids):
            candidates: List[Any] = [document_id]
            if ObjectId.is_valid(document_id):
                candidates.append(ObjectId(document_id))
            for candidate in candidates:
                document = await self.db.documents.find_one({"_id": candidate})
                if document:
                    found.append({**document, "_id": document_id})
                    break
        return found

    async def gather(self, claim: Dict[str, Any], bench: ClaimBench, document_ids: Set[str]) -> SubmissionRecord:
        notice, _requirement = await self.bench._notice(claim)
        clauses: Dict[str, Dict[str, Any]] = {}
        organization_id, project_id = str(claim.get("organization_id") or ""), str(claim.get("project_id") or "")
        cited = {ref for head in bench.heads for ref in head.clause_refs}
        if cited and organization_id and project_id:
            for row in await self.bench.requirements.clause_records(organization_id, project_id):
                number = str(row.get("clause_no") or "").strip()
                if number in cited and number not in clauses:
                    clauses[number] = {
                        "found": True,
                        "title": row.get("clause_title"),
                        "document_type": row.get("document_type"),
                        "wording": str(row.get("cleaned_text") or row.get("text") or ""),
                    }
        lab = ClaimDelayLab(self.db, self.bench)
        quantum = ClaimQuantum(self.db, self.bench)
        links = await lab.links_for_claim(str(claim["_id"]))
        delay: Dict[str, DelayView] = {}
        build_ups: Dict[str, QuantumView] = {}
        for head in bench.heads:
            head_links = [link for link in links if link.head_id == head.head_id]
            if head_links:
                delay[head.head_id] = build_view(head, head_links, await lab.current_revisions(head_links))
            if head.quantum_lines:
                build_ups[head.head_id] = await quantum.view(claim, head.head_id)
        return SubmissionRecord(
            claim_ref=str(claim.get("claim_ref") or claim["_id"]),
            title=str(claim.get("title") or ""),
            currency=str(claim.get("currency") or "INR"),
            event_date=_as_datetime(claim.get("event_date")),
            awareness_date=_as_datetime(claim.get("awareness_date")),
            heads=bench.heads,
            notice=notice,
            clauses=clauses,
            delay=delay,
            quantum=build_ups,
            evidence=evidence_schedule(await self._documents(document_ids)),
        )

    async def _stored(self, claim: Dict[str, Any]) -> Optional[ClaimSubmission]:
        row = await self._collection.find_one({"claim_id": str(claim["_id"])})
        return ClaimSubmission(**row) if row else None

    async def _cannot_compose(self, claim: Dict[str, Any], bench: Optional[ClaimBench]) -> Optional[str]:
        perspective = await self.bench._perspective(claim)
        if perspective is None:
            return "State which party this project acts for before composing a submission."
        if perspective != "contractor":
            return (
                f"This project acts as {perspective.title()}. A claim submission is the Contractor's document; "
                "this side's work on the claim is its determination."
            )
        if bench is None:
            return "This claim has no Bench yet."
        if bench.stage != "locked":
            return "Lock the board first. A submission is composed from decided heads, not from work in progress."
        return None

    async def _approved(self, claim: Dict[str, Any]) -> bool:
        approval = await self.db.approvals.find_one({"resource_type": "claim", "resource_id": str(claim["_id"])})
        return bool(approval and approval.get("state") == "approved")

    async def view(self, claim: Dict[str, Any], document_ids: Set[str]) -> SubmissionView:
        bench = self.bench._bench_of(claim)
        stored = await self._stored(claim)
        reason = await self._cannot_compose(claim, bench)
        sections: List[SectionView] = []
        if stored is not None:
            current = compose_record_sections(await self.gather(claim, bench, document_ids)) if bench else {}
            for section in stored.sections:
                fresh = current.get(section.key)
                sections.append(SectionView(section=section, stale=fresh is None or fresh.input_hash != section.input_hash))
        stale = sum(1 for item in sections if item.stale)
        if stored is None:
            export_reason: Optional[str] = "Compose the submission first."
        elif reason:
            export_reason = reason
        elif stale:
            export_reason = (
                f"{stale} section{' no longer matches' if stale == 1 else 's no longer match'} the record. "
                "Re-compose before exporting."
            )
        else:
            export_reason = None
        return SubmissionView(
            claim_id=str(claim["_id"]),
            exists=stored is not None,
            sections=sections,
            evidence=stored.evidence if stored else [],
            composed_at=stored.composed_at if stored else None,
            can_compose=reason is None,
            cannot_compose_reason=reason,
            stale_sections=stale,
            approved_for_issue=await self._approved(claim),
            can_export=export_reason is None,
            cannot_export_reason=export_reason,
        )

    async def compose(
        self, claim: Dict[str, Any], request: ComposeRequest, document_ids: Set[str], user_id: Optional[str]
    ) -> SubmissionView:
        bench = self.bench._bench_of(claim)
        reason = await self._cannot_compose(claim, bench)
        if reason or bench is None:
            raise HTTPException(status_code=409, detail=reason)
        unknown = [key for key in request.sections if key not in SECTION_ORDER]
        if unknown:
            raise HTTPException(status_code=422, detail="Unknown section: " + ", ".join(unknown))
        record = await self.gather(claim, bench, document_ids)
        fresh = compose_record_sections(record)
        stored = await self._stored(claim)
        kept = {section.key: section for section in (stored.sections if stored else [])}
        cited = {ref for head in bench.heads for ref in head.clause_refs}
        sections: List[SubmissionSection] = []
        for key in SECTION_ORDER:
            base = fresh[key]
            previous = kept.get(key)
            if previous is not None and previous.input_hash == base.input_hash and key not in request.sections:
                sections.append(previous)
                continue
            sections.append(await self._with_skill(claim, key, base, cited, user_id, force=key in request.sections))
        submission = ClaimSubmission(
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_id=str(claim["_id"]),
            sections=sections,
            evidence=record.evidence,
            board_version=bench.board_version,
            composed_by=user_id,
        )
        if stored is None:
            await self._collection.insert_one(submission.model_dump(by_alias=True))
        else:
            submission.id, submission.version = stored.id, stored.version + 1
            result = await self._collection.update_one(
                {"_id": stored.id, "version": stored.version},
                {"$set": submission.model_dump(by_alias=True, exclude={"id"})},
            )
            if not getattr(result, "matched_count", 0):
                raise HTTPException(status_code=409, detail="The submission was re-composed by someone else. Reload it.")
        return await self.view(claim, document_ids)

    async def _with_skill(
        self, claim: Dict[str, Any], key: str, base: SubmissionSection, cited: Set[str], user_id: Optional[str], force: bool
    ) -> SubmissionSection:
        """Offer the section to the skill. Its text is used only if it stays within the record."""
        record_text = [paragraph.text for paragraph in base.paragraphs]
        step = await self.bench.runner.run(
            skill_key=CLAIMS_SKILL_KEY,
            subject_type="claim",
            subject_id=f"{claim['_id']}:submission:{key}",
            parent_id=f"{claim['_id']}:findings",
            organization_id=str(claim.get("organization_id") or "") or None,
            project_id=claim.get("project_id"),
            role="contractor",
            prompt_key=SUBMISSION_SECTION_PROMPT,
            inputs={"section": key, "record": record_text},
            payload={"section_title": base.title, "record": "\n\n".join(record_text) or "(nothing on the record for this section)"},
            fallback=lambda: SubmissionSectionOutput(paragraphs=record_text),
            schema_key=SUBMISSION_SECTION_SCHEMA_KEY,
            user_id=user_id,
            force=force,
        )
        section = base.model_copy(deep=True)
        section.step_id = step.step_id
        section.composed_at = now_utc()
        if step.degraded:
            section.degraded, section.degraded_reason = True, step.error
            return section
        proposed = [text.strip() for text in SubmissionSectionOutput(**step.output).paragraphs if text.strip()]
        problems = validate_section_text(key, proposed, record_text, cited)
        if problems:
            section.degraded = True
            section.degraded_reason = "Skill text not used: " + "; ".join(problems)
            logger.info("claim submission: claim=%s section=%s skill text rejected", claim["_id"], key)
            return section
        # The skill's prose is not traceable paragraph by paragraph, so each
        # paragraph carries the sources of the whole section.
        sources: List[ParagraphSource] = []
        for paragraph in base.paragraphs:
            for source in paragraph.sources:
                if source not in sources:
                    sources.append(source)
        section.paragraphs = [SubmissionParagraph(text=text, sources=sources) for text in proposed]
        section.produced_by = "skill"
        return section

    async def export(self, claim: Dict[str, Any], fmt: str, document_ids: Set[str]) -> tuple[bytes, str, str]:
        if fmt not in {"docx", "pdf"}:
            raise HTTPException(status_code=422, detail="format must be docx or pdf")
        view = await self.view(claim, document_ids)
        if not view.can_export:
            raise HTTPException(status_code=409, detail=view.cannot_export_reason)
        stored = await self._stored(claim)
        assert stored is not None
        reference = str(claim.get("claim_ref") or claim["_id"])
        heading = f"Claim submission {reference}: {claim.get('title') or ''}".strip().rstrip(":")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"claim-submission-{reference}").strip("_")
        if fmt == "docx":
            return (
                submission_docx(stored, heading, view.approved_for_issue),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                f"{name}.docx",
            )
        return submission_pdf(stored, heading, view.approved_for_issue), "application/pdf", f"{name}.pdf"


__all__ = [
    "RESERVATION_WORDING",
    "SUBMISSIONS",
    "SUBMISSION_SECTION_PROMPT",
    "ClaimSubmissions",
    "ComposeRequest",
    "SectionView",
    "SubmissionRecord",
    "SubmissionView",
    "compose_record_sections",
    "evidence_schedule",
    "format_amount",
    "format_day",
    "numbers_in",
    "submission_docx",
    "submission_pdf",
    "validate_section_text",
]
