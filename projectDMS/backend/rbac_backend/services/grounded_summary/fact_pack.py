"""Stage A: the Fact Pack - deterministic, no model, provenance on every fact.

Inputs are one :class:`CanonicalEvidence` (already authorised and verified
against its manifest) and the document row. Every fact is either

* read from the canonical text by a deterministic pattern (clauses, amounts,
  quantities, dates, stated positions) and carries the page and the character
  span it was read from; or
* a structured-metadata value (letter number, date, subject, parties,
  references) that is *located* in the canonical text. Location decides its
  status: a value the text does not state is never presented as verified.

Never read: ``summary``, ``key_reply_points``, ``full_text`` or any other
model rendering. Never repaired here: an amount is what the canonical page
says, with the quality gate's own verdict beside it.

Statements made in the letter (positions, requests, reservations, claims) are
kept verbatim and attributed to the author. They are the author's account,
never established fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ...models.grounded_summary import GroundedFact, SourceSpan
from ..canonical_evidence_service import CanonicalEvidence
from ..falkor_graph_service import normalize_letter_code
from ..metadata_integrity import effective_human_edited_fields

#: Cap on the quoted sentence carried with a fact.
QUOTE_LIMIT = 400
#: Caps per fact family, so a 300-page claim cannot produce a summary bigger
#: than the document payload limit allows.
MAX_STATEMENTS = 60
MAX_DATES = 25
MAX_AMOUNTS = 30
MAX_QUANTITIES = 20
MAX_CLAUSES = 30
MAX_REFERENCES = 40

#: Page statuses whose text is not settled evidence.
_UNRESOLVED_PAGE_STATUSES = frozenset(
    {"ocr_failed", "ocr_pending", "ocr_deferred", "unrenderable", "failed", "deferred"}
)
_MACHINE_FAILED_VERDICTS = frozenset({"fail", "indeterminate"})

#: Strength order, strongest first. An item citing several facts takes the
#: weakest of them.
STATUS_ORDER: Tuple[str, ...] = (
    "verified",
    "source_stated",
    "human_approved",
    "not_checkable",
    "uncertain",
)


def weakest(statuses: Iterable[str]) -> str:
    ranked = [status for status in statuses if status in STATUS_ORDER]
    if not ranked:
        return "uncertain"
    return max(ranked, key=STATUS_ORDER.index)


# --- text location ----------------------------------------------------------

_QUOTE_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)


class _Locator:
    """Case- and whitespace-insensitive search that answers original offsets."""

    def __init__(self, text: str) -> None:
        self.text = text
        chars: List[str] = []
        index: List[int] = []
        previous_space = True
        for offset, char in enumerate(text.translate(_QUOTE_FOLD)):
            if char.isspace():
                if previous_space:
                    continue
                chars.append(" ")
                index.append(offset)
                previous_space = True
                continue
            chars.append(char.lower())
            index.append(offset)
            previous_space = False
        self.norm = "".join(chars)
        self.index = index

    @staticmethod
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).translate(_QUOTE_FOLD)).strip().lower()

    def find(self, value: Any) -> Optional[Tuple[int, int]]:
        needle = self.normalize(str(value or ""))
        if len(needle) < 2:
            return None
        at = self.norm.find(needle)
        if at < 0:
            return None
        start = self.index[at]
        end = self.index[at + len(needle) - 1] + 1
        return start, end


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quote(text: str) -> str:
    value = _collapse(text)
    if len(value) <= QUOTE_LIMIT:
        return value
    return value[: QUOTE_LIMIT - 1].rstrip() + "…"


# --- sentences --------------------------------------------------------------

_ABBREVIATIONS = frozenset(
    {
        "no", "nos", "dt", "dtd", "rs", "cl", "sub", "ref", "refs", "vide", "mr",
        "mrs", "ms", "dr", "ltd", "pvt", "co", "inc", "st", "viz", "ie", "eg",
        "etc", "sr", "jr", "para", "encl", "approx", "fig", "vol", "pg", "p",
        "ch", "sec", "art", "govt", "dept", "m/s", "w.e.f", "i.e", "e.g",
    }
)
#: A sentence ends at terminal punctuation, at a blank line, or at a line break
#: that opens a header field or a numbered/bulleted entry (a letter's header
#: and reference block carry no full stops).
_BOUNDARY = re.compile(
    r"(?<=[.!?;])\s+(?=[\"'(“]?[A-Z0-9])|\n[ \t]*\n|"
    r"\n(?=[ \t]*(?:\(?\d{1,2}[).]|\(?[a-z]\)|[-•*]\s|"
    r"(?i:sub(?:ject)?|ref(?:erence)?s?|to|from|dated?|encl(?:osures?)?|cc|copy\s+to|for)\b))"
)
_HEADER_LINE = re.compile(
    r"^\s*(?:sub(?:ject)?|ref(?:erence)?s?|to|from|date[d]?|dt|encl(?:osures?)?|"
    r"copy\s+to|cc|letter\s+no|our\s+ref|your\s+ref|kind\s+attn|attn)\b\s*[:.\-]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    text: str
    page_number: int


def split_sentences(evidence: CanonicalEvidence) -> List[Sentence]:
    """Sentences inside each page's span; a sentence never crosses a page."""
    text = evidence.text or ""
    sentences: List[Sentence] = []
    for page in evidence.pages:
        if page.end <= page.start:
            continue
        cursor = page.start
        segment = text[page.start : page.end]
        cuts: List[int] = []
        for match in _BOUNDARY.finditer(segment):
            before = segment[: match.start()].rstrip()
            word = re.search(r"([A-Za-z/.]+)\.$", before)
            if word and word.group(1).lower().rstrip(".") in _ABBREVIATIONS:
                continue
            cuts.append(page.start + match.start())
        bounds = cuts + [page.end]
        for cut in bounds:
            raw = text[cursor:cut]
            stripped = raw.strip()
            if stripped:
                lead = len(raw) - len(raw.lstrip())
                start = cursor + lead
                sentences.append(
                    Sentence(start=start, end=start + len(stripped), text=stripped, page_number=page.page_number)
                )
            cursor = cut
    return sentences


# --- statement classification -------------------------------------------------

_BACKGROUND = re.compile(
    r"^\W*(?:\d+[.)]\s*)?(?:with\s+reference\s+to|reference\s+is\s+(?:drawn|invited|made)|"
    r"this\s+(?:has|is)\s+(?:reference|in\s+reference)|as\s+you\s+are\s+aware|"
    r"in\s+continuation|further\s+to|we\s+refer\s+to|kindly\s+refer|please\s+refer)",
    re.IGNORECASE,
)
_RESERVATION = re.compile(
    r"\breserv(?:e|es|ed|ing)\b[^.]{0,80}\bright|\bwithout\s+prejudice\b", re.IGNORECASE
)
_INSTRUCTION = re.compile(
    r"\byou\s+are\s+(?:hereby\s+)?(?:instructed|directed)\b|\bare\s+hereby\s+(?:instructed|directed)\b|"
    r"\bshall\s+(?:immediately|forthwith)\b|\byou\s+are\s+required\s+to\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(?:we|i)\s+(?:(?:hereby|would|therefore|kindly|again|once\s+again)\s+)*(?:request|seek|urge|pray)\b|"
    r"\byou\s+are\s+(?:hereby\s+|kindly\s+)?requested\b|\bkindly\b|\bplease\b|"
    r"\brequest(?:ed)?\s+(?:you|your|the\s+engineer|the\s+employer)\b|"
    r"\bseek(?:s|ing)?\s+(?:your\s+)?(?:approval|determination|extension|consent|instructions?)\b",
    re.IGNORECASE,
)
_CLAIM = re.compile(
    r"\bclaim(?:s|ed|ing)?\b|\bentitle(?:d|ment)\b|\bcompensat\w*|\bextension\s+of\s+time\b|\bEOT\b|"
    r"\badditional\s+(?:cost|costs|payment|expenditure|expenses)\b|\breimburs\w*|\bprolongation\b|\bdamages\b",
    re.IGNORECASE,
)
_POSITION = re.compile(
    r"\bdue\s+to\b|\battributable\b|\bcaused\b|\bbecause\b|\bfail(?:ed|ure|s)?\b|"
    r"\bnot\s+(?:yet\s+|been\s+)*(?:handed|provided|made\s+available|available|approved|released|received)\b|"
    r"\bdelay(?:ed|s)?\b|\bhindrance\w*|\bobstruct\w*|\bresponsib\w*|\bwe\s+(?:state|submit|contend|maintain)\b",
    re.IGNORECASE,
)


def _is_prose(text: str) -> bool:
    """A written sentence, not a table row or heading run together.

    Table cells carry no terminal punctuation and are mostly figures; a
    heading such as "Claim summary" would otherwise read as a claim.
    """
    words = text.split()
    if len(words) < 6:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 3:
        cells = [line for line in lines if len(line) < 45 and not re.search(r"[.!?;]$", line)]
        if len(cells) * 2 > len(lines):
            return False
    numeric = sum(1 for word in words if re.search(r"\d", word))
    if numeric / len(words) > 0.3:
        return False
    return bool(re.search(r"[.!?;:\"”)]\s*$", text)) or len(words) >= 15


def classify_statement(text: str) -> Optional[str]:
    """The role a sentence plays, or None when it is none of them."""
    if _BACKGROUND.search(text):
        return "background"
    if _RESERVATION.search(text):
        return "reservation"
    if _INSTRUCTION.search(text):
        return "instruction"
    if _REQUEST.search(text):
        return "request"
    if _CLAIM.search(text):
        return "claim"
    if _POSITION.search(text):
        return "party_position"
    return None


# --- dates --------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_NUMERIC_DATE = re.compile(r"(?<![\d/.\-])(\d{1,2})[./\-](\d{1,2})[./\-](\d{4}|\d{2})(?![\d/.\-]*\d)")
_DAY_MONTH_DATE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-.,]*{_MONTH_NAME}\.?[\s\-.,']*(\d{{4}}|\d{{2}})\b", re.IGNORECASE
)
_MONTH_DAY_DATE = re.compile(rf"\b{_MONTH_NAME}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class DateMention:
    start: int
    end: int
    stated: str
    #: Every calendar date the stated text can mean. One reading = unambiguous.
    readings: Tuple[date, ...]
    #: The day-first reading of a numeric date, when it is one of the readings.
    day_first: Optional[date] = None

    @property
    def ambiguous(self) -> bool:
        return len(self.readings) > 1


def find_dates(text: str) -> List[DateMention]:
    mentions: Dict[int, DateMention] = {}
    for match in _NUMERIC_DATE.finditer(text):
        first, second, year = int(match.group(1)), int(match.group(2)), _year(match.group(3))
        day_first = _safe_date(year, second, first)
        month_first = _safe_date(year, first, second)
        readings = tuple(sorted({value for value in (day_first, month_first) if value}))
        if not readings:
            continue
        mentions[match.start()] = DateMention(
            match.start(), match.end(), match.group(0), readings, day_first=day_first
        )
    for match in _DAY_MONTH_DATE.finditer(text):
        month = _MONTHS[match.group(2)[:3].lower()]
        value = _safe_date(_year(match.group(3)), month, int(match.group(1)))
        if value:
            mentions[match.start()] = DateMention(match.start(), match.end(), match.group(0), (value,))
    for match in _MONTH_DAY_DATE.finditer(text):
        month = _MONTHS[match.group(1)[:3].lower()]
        value = _safe_date(int(match.group(3)), month, int(match.group(2)))
        if value and match.start() not in mentions:
            mentions[match.start()] = DateMention(match.start(), match.end(), match.group(0), (value,))
    return [mentions[key] for key in sorted(mentions)]


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(text[: len(datetime.now().strftime(pattern))], pattern).date()
            except ValueError:
                continue
    return None


# --- amounts, quantities, clauses --------------------------------------------------

_CURRENCY_AMOUNT = re.compile(
    r"(?:\bINR\b|\bRs\b\.?|₹|\bRupees\b)\s*:?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:/-)?"
    r"(?:\s*(lakhs?|lacs?|crores?|cr\b|million|mn\b))?",
    re.IGNORECASE,
)
_MULTIPLIERS = {
    "lakh": Decimal("100000"), "lakhs": Decimal("100000"), "lac": Decimal("100000"),
    "lacs": Decimal("100000"), "crore": Decimal("10000000"), "crores": Decimal("10000000"),
    "cr": Decimal("10000000"), "million": Decimal("1000000"), "mn": Decimal("1000000"),
}
_NUMBER_TOKEN = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)(?![\w,]*\d)")
_QUANTITY = re.compile(
    r"\b(\d[\d,]*(?:\.\d+)?)\s*(days?|months?|weeks?|years?|hours?|hrs|nos\.?|cum|m3|sqm|m2|rmt|km|"
    r"MT|tonnes?|metres?|meters?|kg)\b",
    re.IGNORECASE,
)
_CLAUSE = re.compile(
    r"\b(?:(?:Sub[-\s]?)?Clause|Cl\.|Article|GCC|SCC|PCC)\s*(?:No\.?\s*)?"
    r"(\d+(?:\.\d+)*(?:\s?\([a-z0-9]{1,4}\))*)"
    r"(?:\s+of\s+(?:the\s+)?(GCC|SCC|PCC|General\s+Conditions(?:\s+of\s+Contract)?|"
    r"Special\s+Conditions(?:\s+of\s+Contract)?|Particular\s+Conditions(?:\s+of\s+Contract)?))?",
    re.IGNORECASE,
)


def _decimal(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _json_number(value: Decimal) -> Any:
    return int(value) if value == value.to_integral_value() else float(value)


_CODE_TOKEN = re.compile(r"[A-Za-z0-9]+(?:\s?[/\-_.]\s?[A-Za-z0-9()]+){2,}")


# --- page reliability -----------------------------------------------------------


@dataclass
class PageState:
    page_number: int
    #: None for a clean page; otherwise the status a fact on it must carry.
    cap: Optional[str]
    machine_failed: bool
    approved: bool
    withheld: bool
    unresolved: bool
    failed_checks: List[str] = field(default_factory=list)


def _page_states(evidence: CanonicalEvidence, approved_pages: Sequence[int]) -> Dict[int, PageState]:
    approved = {int(n) for n in approved_pages}
    states: Dict[int, PageState] = {}
    for page in evidence.pages:
        machine_failed = bool(page.needs_review) or str(page.quality_verdict or "") in _MACHINE_FAILED_VERDICTS
        unresolved = str(page.status or "") in _UNRESOLVED_PAGE_STATUSES
        withheld = bool(page.text_withheld)
        if withheld or unresolved:
            cap: Optional[str] = "uncertain"
        elif machine_failed:
            cap = "human_approved" if page.page_number in approved else "uncertain"
        else:
            cap = None
        failed = [
            str(check.get("name"))
            for check in page.quality_checks or []
            if isinstance(check, Mapping)
            and str(check.get("verdict") or "") in _MACHINE_FAILED_VERDICTS
        ]
        states[page.page_number] = PageState(
            page.page_number, cap, machine_failed, page.page_number in approved, withheld, unresolved, failed
        )
    return states


def _apply_page_cap(status: str, state: Optional[PageState]) -> str:
    if state is None or state.cap is None:
        return status
    return weakest([status, state.cap]) if state.cap == "uncertain" else state.cap


# --- the pack ---------------------------------------------------------------------


@dataclass
class FactPack:
    facts: List[GroundedFact]
    party: str
    header: Dict[str, Optional[str]]
    page_states: Dict[int, PageState]
    review_resolution: Optional[Dict[str, Any]]
    metadata_quality: Dict[str, Any]
    canonical_char_count: int

    def by_type(self, *types: str) -> List[GroundedFact]:
        return [fact for fact in self.facts if fact.type in types]

    def get(self, fact_id: str) -> Optional[GroundedFact]:
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        return None


class _Builder:
    def __init__(self, evidence: CanonicalEvidence, document: Mapping[str, Any], review: Optional[Mapping[str, Any]]):
        self.evidence = evidence
        self.document = document
        self.text = evidence.text or ""
        self.locator = _Locator(self.text)
        self.review = dict(review) if review else None
        approved = list((self.review or {}).get("approved_pages") or [])
        self.states = _page_states(evidence, approved)
        self.facts: List[GroundedFact] = []
        self.counters: Dict[str, int] = {}
        self.human_edited = set(effective_human_edited_fields(document))
        self.withheld_spans = [
            (page.start, page.end) for page in evidence.pages if page.text_withheld
        ]

    # -- helpers --
    def _id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}-{self.counters[kind]}"

    def _page(self, offset: int) -> Optional[int]:
        return self.evidence.page_at(offset)

    def _span(self, start: int, end: int) -> Optional[SourceSpan]:
        page = self._page(start)
        if page is None:
            return None
        return SourceSpan(page_number=page, start_char=start, end_char=end)

    def _add(self, *, kind: str, label: str, status: str, origin: str, start: Optional[int] = None,
             end: Optional[int] = None, **extra: Any) -> GroundedFact:
        spans: List[SourceSpan] = []
        pages: List[int] = list(extra.pop("page_numbers", None) or [])
        if start is not None and end is not None:
            span = self._span(start, end)
            if span is not None:
                spans.append(span)
                if span.page_number not in pages:
                    pages.append(span.page_number)
                status = _apply_page_cap(status, self.states.get(span.page_number))
        fact = GroundedFact(
            id=self._id(kind), type=kind, label=label, verification_status=status,  # type: ignore[arg-type]
            origin=origin, page_numbers=pages, source_spans=spans, **extra,
        )
        self.facts.append(fact)
        return fact

    def _line(self, offset: int) -> Tuple[int, int]:
        start = self.text.rfind("\n", 0, offset) + 1
        end = self.text.find("\n", offset)
        return start, (len(self.text) if end < 0 else end)

    def _sentence_at(self, sentences: List[Sentence], offset: int) -> Optional[Sentence]:
        for sentence in sentences:
            if sentence.start <= offset < sentence.end:
                return sentence
        return None

    # -- header --
    def header(self) -> Dict[str, Optional[str]]:
        doc = self.document
        out: Dict[str, Optional[str]] = {}
        for key, kind, label in (
            ("letterNo", "letter_number", "Letter number"),
            ("subject", "subject", "Subject"),
            ("from", "from_party", "From"),
            ("to", "to_party", "To"),
        ):
            value = doc.get(key)
            if not value or not str(value).strip():
                out[key] = None
                continue
            value = _collapse(str(value))
            found = self.locator.find(value)
            if kind == "letter_number" and not found:
                found = self._find_code(value)
            if found:
                status, origin = "verified", "structured_metadata"
            elif key in self.human_edited:
                status, origin = "not_checkable", "human_edit"
            else:
                status, origin = "uncertain", "structured_metadata"
            self._add(
                kind=kind, label=label, value=value, status=status, origin=origin,
                start=found[0] if found else None, end=found[1] if found else None,
                role="source_fact",
                evidence_checks=[{"name": "located_in_canonical_text", "verdict": "pass" if found else "fail"}],
            )
            out[key] = value
        out["date"] = self._letter_date()
        return out

    def _find_code(self, code: str) -> Optional[Tuple[int, int]]:
        wanted = normalize_letter_code(code)
        if not wanted or wanted == "unknown":
            return None
        for match in _CODE_TOKEN.finditer(self.text):
            if normalize_letter_code(match.group(0)) == wanted:
                return match.start(), match.end()
        return None

    def _letter_date(self) -> Optional[str]:
        stored = _as_date(self.document.get("date"))
        if stored is None:
            return None
        mentions = [m for m in find_dates(self.text) if stored in m.readings]
        checks: List[Dict[str, Any]] = []
        if not mentions:
            status = "not_checkable" if "date" in self.human_edited else "uncertain"
            origin = "human_edit" if "date" in self.human_edited else "structured_metadata"
            checks.append({"name": "located_in_canonical_text", "verdict": "fail"})
            mention = None
        else:
            unambiguous = [m for m in mentions if not m.ambiguous]
            mention = unambiguous[0] if unambiguous else mentions[0]
            origin = "structured_metadata"
            checks.append({"name": "located_in_canonical_text", "verdict": "pass"})
            if not mention.ambiguous:
                status = "verified"
            elif mention.day_first == stored:
                status = "source_stated"
                checks.append({
                    "name": "day_month_order", "verdict": "not_checkable",
                    "detail": f"'{mention.stated}' read day-first, the corpus convention",
                })
            else:
                status = "uncertain"
                checks.append({
                    "name": "day_month_order", "verdict": "fail",
                    "detail": f"'{mention.stated}' matches the stored date only if read month-first",
                })
        self._add(
            kind="letter_date", label="Letter date", value=mention.stated if mention else stored.isoformat(),
            normalized_value=stored.isoformat(), status=status, origin=origin,
            start=mention.start if mention else None, end=mention.end if mention else None,
            role="source_fact", evidence_checks=checks,
        )
        return stored.strftime("%d %B %Y")

    # -- statements --
    def statements(self, sentences: List[Sentence], party: str) -> None:
        header_spans = [
            (span.start_char, span.end_char)
            for fact in self.facts
            if fact.type in {"subject", "letter_number", "to_party", "from_party"}
            for span in fact.source_spans
        ]
        count = 0
        for sentence in sentences:
            if count >= MAX_STATEMENTS:
                break
            if len(sentence.text) < 25 or _HEADER_LINE.search(sentence.text):
                continue
            if not _is_prose(sentence.text):
                continue
            if any(s < sentence.end and sentence.start < e for s, e in header_spans):
                continue
            role = classify_statement(sentence.text)
            if role is None:
                continue
            kind = {
                "background": "background_statement",
                "party_position": "position_statement",
            }.get(role, f"{role}_statement")
            self._add(
                kind=kind, label=role.replace("_", " ").title(), value=_quote(sentence.text),
                quote=_quote(sentence.text), status="source_stated", origin="canonical_text",
                start=sentence.start, end=sentence.end, party=party,
                role="party_position" if role == "background" else role,  # type: ignore[arg-type]
            )
            count += 1

    # -- dates --
    def dates(self, sentences: List[Sentence]) -> None:
        letter_spans = {
            (span.start_char, span.end_char) for fact in self.by_type("letter_date") for span in fact.source_spans
        }
        # A date on a reference's own line is that reference's date; it is
        # shown with the reference, not again as a material date.
        reference_lines = {
            self._line(span.start_char) for fact in self.by_type("reference") for span in fact.source_spans
        }
        seen: set = set()
        for mention in find_dates(self.text):
            if len([f for f in self.facts if f.type == "date"]) >= MAX_DATES:
                break
            if (mention.start, mention.end) in letter_spans or self._line(mention.start) in reference_lines:
                continue
            key = (mention.stated, self._page(mention.start))
            if key in seen:
                continue
            seen.add(key)
            sentence = self._sentence_at(sentences, mention.start)
            checks: List[Dict[str, Any]] = []
            if mention.ambiguous:
                status = "uncertain"
                normalized = None
                checks.append({
                    "name": "day_month_order", "verdict": "indeterminate",
                    "detail": "numeric date whose day/month order the text does not settle",
                })
            else:
                status = "source_stated"
                normalized = mention.readings[0].isoformat()
            self._add(
                kind="date", label="Date stated", value=mention.stated, normalized_value=normalized,
                status=status, origin="canonical_text", start=mention.start, end=mention.end,
                quote=_quote(sentence.text) if sentence else None, evidence_checks=checks,
            )

    def by_type(self, *types: str) -> List[GroundedFact]:
        return [fact for fact in self.facts if fact.type in types]

    # -- amounts / quantities --
    def amounts(self, sentences: List[Sentence]) -> None:
        seen: set = set()
        for match in _CURRENCY_AMOUNT.finditer(self.text):
            if len(self.by_type("amount")) >= MAX_AMOUNTS:
                break
            base = _decimal(match.group(1))
            if base is None or base == 0:
                continue
            unit_word = (match.group(2) or "").lower()
            value = base * _MULTIPLIERS.get(unit_word, Decimal(1))
            key = (self._page(match.start()), value)
            if key in seen:
                continue
            seen.add(key)
            self._amount_fact(match.start(), match.end(), match.group(0).strip(), value, "INR", sentences)
        # Values the quality gate checked and passed, stated in a table without
        # a currency marker: only those, never every number on the page.
        for evidence_page in self.evidence.pages:
            passed = {
                _decimal(str(check.get("candidate_value")))
                for check in evidence_page.quality_checks or []
                if isinstance(check, Mapping)
                and str(check.get("verdict") or "") == "pass"
                and check.get("candidate_value") not in (None, "")
            }
            passed.discard(None)
            if not passed:
                continue
            for match in _NUMBER_TOKEN.finditer(self.text, evidence_page.start, evidence_page.end):
                if len(self.by_type("amount")) >= MAX_AMOUNTS:
                    break
                cell = _decimal(match.group(1))
                if cell is None or cell not in passed or (evidence_page.page_number, cell) in seen:
                    continue
                seen.add((evidence_page.page_number, cell))
                self._amount_fact(match.start(), match.end(), match.group(0), cell, None, sentences)

    def _amount_fact(self, start: int, end: int, stated: str, value: Decimal, currency: Optional[str],
                     sentences: List[Sentence]) -> None:
        page_number = self._page(start)
        page = self.evidence.page(page_number) if page_number is not None else None
        checks: List[Dict[str, Any]] = []
        verified = False
        if page is not None:
            for check in page.quality_checks or []:
                if not isinstance(check, Mapping) or check.get("candidate_value") in (None, ""):
                    continue
                if _decimal(str(check.get("candidate_value"))) == value:
                    checks.append({
                        "name": check.get("name"), "verdict": check.get("verdict"),
                        "detail": (str(check.get("detail"))[:200] if check.get("detail") else None),
                    })
                    if str(check.get("verdict") or "") == "pass":
                        verified = True
            for repair in page.applied_repairs or []:
                if not isinstance(repair, Mapping):
                    continue
                after = str(repair.get("after") or "")
                if after and _decimal(after) == value:
                    checks.append({
                        "name": "deterministic_repair", "verdict": "applied",
                        "detail": f"read as '{repair.get('before')}', repaired to '{after}'",
                    })
        sentence = self._sentence_at(sentences, start)
        if currency is None:
            # A checked table value: its row is the context, not the run of
            # cells the sentence splitter joined around it.
            line_start, line_end = self._line(start)
            quote: Optional[str] = _quote(self.text[line_start:line_end])
        else:
            quote = _quote(sentence.text) if sentence else None
        self._add(
            kind="amount", label="Amount" if currency else "Amount (checked table value)",
            value=_collapse(stated), unit=currency, normalized_value=_json_number(value),
            status="verified" if verified else "source_stated", origin="canonical_text",
            start=start, end=end, evidence_checks=checks, quote=quote,
        )

    def quantities(self, sentences: List[Sentence]) -> None:
        amount_spans = [(s.start_char, s.end_char) for f in self.by_type("amount") for s in f.source_spans]
        seen: set = set()
        for match in _QUANTITY.finditer(self.text):
            if len(self.by_type("quantity")) >= MAX_QUANTITIES:
                break
            if any(s <= match.start() < e for s, e in amount_spans):
                continue
            stated = _collapse(match.group(0))
            key = (stated.lower(), self._page(match.start()))
            if key in seen:
                continue
            seen.add(key)
            number = _decimal(match.group(1))
            sentence = self._sentence_at(sentences, match.start())
            self._add(
                kind="quantity", label="Quantity", value=stated, unit=match.group(2).lower(),
                normalized_value=_json_number(number) if number is not None else None,
                status="source_stated", origin="canonical_text", start=match.start(), end=match.end(),
                quote=_quote(sentence.text) if sentence else None,
            )

    # -- clauses --
    def clauses(self, sentences: List[Sentence]) -> None:
        seen: Dict[str, GroundedFact] = {}
        for match in _CLAUSE.finditer(self.text):
            label = _collapse(match.group(0))
            key = _Locator.normalize(label)
            if key in seen:
                continue
            if len(seen) >= MAX_CLAUSES:
                break
            sentence = self._sentence_at(sentences, match.start())
            seen[key] = self._add(
                kind="clause", label="Contract clause cited", value=label,
                normalized_value=re.sub(r"\s+", "", match.group(1)), status="source_stated",
                origin="canonical_text", start=match.start(), end=match.end(),
                quote=_quote(sentence.text) if sentence else None,
            )
        stated_numbers = {str(fact.normalized_value) for fact in seen.values()}
        for raw in self.document.get("contractual_clauses") or []:
            numbers = re.findall(r"\d+(?:\.\d+)*", str(raw))
            if numbers and any(number in stated_numbers for number in numbers):
                continue
            self._add(
                kind="unlocated_metadata_clause", label="Extracted clause not stated in the text",
                value=_collapse(str(raw))[:120], status="uncertain", origin="structured_metadata",
            )

    # -- references --
    def references(self) -> None:
        linked: Dict[str, str] = {}
        for entry in self.document.get("references") or []:
            if not isinstance(entry, Mapping):
                continue
            target, code = entry.get("documentId"), entry.get("letterNo")
            if target and code:
                linked.setdefault(normalize_letter_code(str(code)), str(target))
        count = 0
        seen: set = set()
        for entry in self.document.get("reference") or []:
            if count >= MAX_REFERENCES:
                break
            if isinstance(entry, Mapping):
                code = entry.get("letterNo") or entry.get("letter_no") or entry.get("text")
                stated_date = entry.get("date")
            else:
                code, stated_date = entry, None
            if not code:
                continue
            normalized = normalize_letter_code(str(code))
            if normalized in seen:
                continue
            seen.add(normalized)
            found = self._find_code(str(code)) or self.locator.find(str(code))
            label = _collapse(str(code)) + (f", dated {stated_date}" if stated_date else "")
            self._add(
                kind="reference", label="Reference stated in the letter", value=label,
                normalized_value=normalized, status="source_stated" if found else "uncertain",
                origin="structured_metadata", start=found[0] if found else None,
                end=found[1] if found else None, role="reference",
                resolved_document_id=linked.get(normalized),
                evidence_checks=[
                    {"name": "located_in_canonical_text", "verdict": "pass" if found else "fail"},
                    {"name": "resolved_in_dms", "verdict": "pass" if linked.get(normalized) else "not_checkable"},
                ],
            )
            count += 1

    # -- qualifications --
    def qualifications(self) -> None:
        for state in sorted(self.states.values(), key=lambda s: s.page_number):
            if state.withheld:
                self._add(
                    kind="page_qualification", label="Page text withheld",
                    value=f"Page {state.page_number}: the extracted text was unusable and is withheld; "
                          "nothing on this page is summarised.",
                    status="uncertain", origin="page_quality", page_numbers=[state.page_number],
                )
            elif state.unresolved:
                self._add(
                    kind="page_qualification", label="Page not read",
                    value=f"Page {state.page_number} could not be read by extraction; its content is missing.",
                    status="uncertain", origin="page_quality", page_numbers=[state.page_number],
                )
            elif state.machine_failed and state.approved:
                checks = ", ".join(state.failed_checks) or "quality gate"
                resolved_at = (self.review or {}).get("resolved_at")
                when = f" on {resolved_at:%d %B %Y}" if isinstance(resolved_at, datetime) else ""
                self._add(
                    kind="page_qualification", label="Machine-failed page, human-approved",
                    value=f"Page {state.page_number} failed automated quality checks ({checks}) and was "
                          f"approved as-is by a reviewer{when} for this evidence revision.",
                    status="human_approved", origin="page_quality", page_numbers=[state.page_number],
                    evidence_checks=[{"name": name, "verdict": "fail"} for name in state.failed_checks],
                )
            elif state.machine_failed:
                self._add(
                    kind="page_qualification", label="Page awaiting review",
                    value=f"Page {state.page_number} failed automated quality checks and has not been "
                          "reviewed; facts read from it are uncertain.",
                    status="uncertain", origin="page_quality", page_numbers=[state.page_number],
                )
        quality = self.document.get("metadata_quality")
        if isinstance(quality, Mapping):
            if quality.get("references_authoritative") is False:
                self._add(
                    kind="metadata_qualification", label="Reference list may be incomplete",
                    value="Reference extraction was degraded; the letter may cite correspondence not listed here.",
                    status="uncertain", origin="structured_metadata",
                )
            missing = [str(name) for name in quality.get("missing_core_fields") or []]
            if missing:
                self._add(
                    kind="metadata_qualification", label="Header fields not extracted",
                    value="Not extracted from the letter: " + ", ".join(missing) + ".",
                    status="uncertain", origin="structured_metadata",
                )
        ambiguous = [f for f in self.by_type("date") if f.normalized_value is None]
        if ambiguous:
            self._add(
                kind="date_qualification", label="Ambiguous numeric dates",
                value=f"{len(ambiguous)} date(s) are written in a numeric form whose day/month order the "
                      "text does not settle; they are shown as written and not converted.",
                status="uncertain", origin="canonical_text",
                page_numbers=sorted({p for f in ambiguous for p in f.page_numbers}),
            )
        for fact in self.facts:
            if fact.type in {"letter_number", "subject", "from_party", "to_party", "letter_date"} and (
                fact.verification_status == "uncertain"
            ):
                self._add(
                    kind="metadata_qualification", label=f"{fact.label} not found in the text",
                    value=f"{fact.label} '{fact.value}' was extracted but is not stated verbatim in the "
                          "canonical text of this revision.",
                    status="uncertain", origin="structured_metadata",
                )


def build_fact_pack(
    evidence: CanonicalEvidence,
    document: Mapping[str, Any],
    *,
    review_resolution: Optional[Mapping[str, Any]] = None,
) -> FactPack:
    """Every fact the summaries may use, each with its provenance.

    ``review_resolution`` applies only when it names this exact revision and
    checksum; any other resolution is ignored (it approved other evidence).
    """
    review = None
    if review_resolution and (
        review_resolution.get("canonical_revision") == evidence.canonical_revision
        and review_resolution.get("canonical_sha256") == evidence.manifest.get("canonical_sha256")
    ):
        review = review_resolution
    builder = _Builder(evidence, document, review)
    header = builder.header()
    from_fact = next(iter(builder.by_type("from_party")), None)
    party = (
        str(from_fact.value)
        if from_fact is not None and from_fact.verification_status != "uncertain"
        else "The author"
    )
    sentences = split_sentences(evidence)
    builder.statements(sentences, party)
    builder.clauses(sentences)
    builder.amounts(sentences)
    builder.quantities(sentences)
    builder.references()
    builder.dates(sentences)
    builder.qualifications()
    quality = document.get("metadata_quality")
    return FactPack(
        facts=builder.facts,
        party=party,
        header=header,
        page_states=builder.states,
        review_resolution=dict(review) if review else None,
        metadata_quality=dict(quality) if isinstance(quality, Mapping) else {},
        canonical_char_count=len(evidence.text or ""),
    )


__all__ = [
    "FactPack",
    "STATUS_ORDER",
    "build_fact_pack",
    "classify_statement",
    "find_dates",
    "split_sentences",
    "weakest",
]
