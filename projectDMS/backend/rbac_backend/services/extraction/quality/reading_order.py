"""Detect text that was extracted in the wrong order, or not at all.

Two failure modes, both measured:

* Reading order (companion 3.5): a multi-column page whose narrative is
  interleaved with the adjacent table's header cells, e.g. a sentence broken by
  "Depth Thickness Length Volume" and unit rows like "(mtr) (mtr) (m3)". A
  chunk built from that stream is unusable for Q&A and unsafe for drafting.
* Text density (companion 2.1): a page whose classification says it carries
  content but whose extracted text is empty - the silent page loss this whole
  programme exists to fix.

Both lean toward NOT_CHECKABLE where the structure gives no signal, because a
false escalation costs a paid model call.
"""

from __future__ import annotations

import re
from typing import List

from ..models import PageClass, PageClassification
from .models import CheckResult, Verdict

_WORD = re.compile(r"[A-Za-z]{2,}")
_PROSE_WORD = re.compile(r"[A-Za-z]{4,}")
_UNIT_FRAGMENT = re.compile(r"\(\s*(?:mtr|m3|m2|nos|kg|cum|sqm|rmt|no)\s*\)", re.I)

#: A line long enough that prose and header tokens could plausibly collide.
_MIN_PROSE_CHARS = 40
#: How much of a line must be trailing short tokens to look like a header run.
_HEADER_RUN_MIN = 3


#: Function words are what make prose prose. A run of nouns containing none of
#: them is a column header, not a sentence.
_FUNCTION_WORDS = frozenset(
    {
        "the", "of", "and", "in", "at", "by", "to", "for", "as", "is", "are",
        "with", "from", "on", "a", "an", "its", "our", "we", "be", "been",
        "that", "this", "which", "shall", "was", "were", "or", "per",
    }
)


#: The vocabulary an adjacent column actually contributes. This check exists to
#: catch "a narrative shredded by an adjacent column's cells", and what a column
#: splices in is its *header*: measure and quantity nouns.
#:
#: Capitalisation alone is not evidence and must never be the trigger. Ordinary
#: contractual prose ends in capitalised proper nouns constantly - a company, a
#: JV, a drawing title, a station - and treating that as corruption escalated 5
#: of 7 realistic sentences, every one of which then needed a human because the
#: fallback ladder is disabled.
#: Deliberately narrow: dimensional and quantity terms only. Ordinary business
#: nouns are excluded even though they head real columns, because they also
#: appear in ordinary titles - "the Revised Cost Estimate" must not escalate,
#: so "cost", "total", "amount", "item", "description" and "sum" are NOT here.
#: A false negative on an oddly-headed column is recoverable; a false positive
#: sends correspondence to a human who has nothing to fix.
_COLUMN_HEADER_TERMS = frozenset(
    {
        "depth", "thickness", "volume", "dia", "diameter", "qty", "nos",
        "rmt", "cum", "sqm", "girth", "spacing",
    }
)


def _looks_like_column_headers(tokens: List[str]) -> bool:
    """True when a trailing run carries the vocabulary of a table header.

    One measure noun is enough: a sentence that genuinely ends in a proper-noun
    name does not contain "Thickness" or "Qty" in its final run, while a
    spliced header row almost always does.
    """
    for token in tokens:
        cleaned = token.strip(",.;:&()").lower()
        if cleaned in _COLUMN_HEADER_TERMS:
            return True
    return False


def _is_header_token(token: str) -> bool:
    """A capitalised noun or a unit fragment - the stuff of table headers."""
    cleaned = token.strip(",.;:&()")
    if not cleaned:
        return False
    if _UNIT_FRAGMENT.fullmatch(token):
        return True
    if cleaned.lower() in _FUNCTION_WORDS:
        return False
    return cleaned[0].isupper() and cleaned.isalpha()


def _trailing_header_run(tokens: List[str]) -> int:
    """Length of the run of header-like tokens at the end of the line."""
    run = 0
    for token in reversed(tokens):
        if _is_header_token(token) or _UNIT_FRAGMENT.fullmatch(token):
            run += 1
            continue
        break
    return run


def _line_is_interleaved(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < _MIN_PROSE_CHARS:
        return False

    tokens = stripped.split()
    if len(tokens) < 8:
        return False

    # A unit row spliced into a prose line is the clearest signal: "(mtr)
    # (mtr) (m3)" never belongs inside a sentence.
    units = _UNIT_FRAGMENT.findall(stripped)
    if len(units) >= 2 and len(_PROSE_WORD.findall(stripped)) >= 3:
        return True

    # Otherwise: prose, then a run of capitalised nouns with no function words
    # among them. Ordinary prose always carries function words in its tail.
    run = _trailing_header_run(tokens)
    if run < _HEADER_RUN_MIN:
        return False

    # Corroboration, not capitalisation. Without this the check fires on any
    # sentence ending in a proper-noun name - see _COLUMN_HEADER_TERMS.
    if not _looks_like_column_headers(tokens[len(tokens) - run :]):
        return False

    head = tokens[: len(tokens) - run]
    head_prose = [
        token for token in head if _PROSE_WORD.fullmatch(token.strip(",.;:&()"))
    ]
    return len(head_prose) >= 3


def check_reading_order(
    text: str, classification: PageClassification
) -> CheckResult:
    """Detect a narrative shredded by an adjacent column's cells."""
    name = "reading_order"
    body = (text or "").strip()

    if not body:
        return CheckResult(
            name=name, verdict=Verdict.NOT_CHECKABLE, detail="no text to inspect"
        )

    prose_words = _PROSE_WORD.findall(body)
    if len(prose_words) < 8 or classification.page_class is PageClass.TABLE_HEAVY and len(
        _WORD.findall(body)
    ) < 20:
        # Nothing prose-like to be out of order. A table's own cell stream is
        # not a reading-order failure.
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="no continuous prose on this page to assess",
        )

    interleaved = [line for line in body.splitlines() if _line_is_interleaved(line)]
    if interleaved:
        return CheckResult(
            name=name,
            verdict=Verdict.FAIL,
            detail=(
                f"reading order looks interleaved on {len(interleaved)} line(s): "
                "prose is broken by adjacent table cells"
            ),
            evidence_refs=["page.text"],
        )

    return CheckResult(
        name=name,
        verdict=Verdict.PASS,
        detail="prose reads continuously",
        evidence_refs=["page.text"],
    )


def check_text_density(
    text: str, classification: PageClassification
) -> CheckResult:
    """Detect a page whose class promises content it did not deliver."""
    name = "text_density"
    body = (text or "").strip()

    if classification.page_class is PageClass.UNRENDERABLE:
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="page could not be rendered",
        )

    if classification.page_class is PageClass.BLANK:
        return CheckResult(
            name=name,
            verdict=Verdict.PASS,
            detail="blank page with no text, as expected",
        )

    if not body:
        return CheckResult(
            name=name,
            verdict=Verdict.FAIL,
            detail=(
                f"page classified {classification.page_class.value} carries "
                "content but extracted no text"
            ),
            evidence_refs=["page.text", "page.classification"],
        )

    return CheckResult(
        name=name,
        verdict=Verdict.PASS,
        detail=f"{len(body)} characters extracted",
        evidence_refs=["page.text"],
    )
