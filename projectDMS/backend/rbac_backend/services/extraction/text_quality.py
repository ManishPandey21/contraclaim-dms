"""Is a page's extracted text actually text?

pdfminer (and pdfplumber above it) writes ``(cid:N)`` for every glyph it cannot
map to Unicode. A page set in such a font extracts as a long string of
placeholders, which the character threshold reads as a healthy text layer: six
glyphs already clear 40 characters. Two measured sources:

* a composite (Type0) font with no ``/ToUnicode`` - placeholders in any
  interpreter; and
* a simple font with neither ``/Encoding`` nor ``/ToUnicode`` once OCRmyPDF has
  been imported anywhere in the process, because ``ocrmypdf.pdfinfo.layout``
  replaces ``PDFSimpleFont.__init__`` interpreter-wide (OCRmyPDF 16.10.4).

A page's native text is *unusable* on either of two narrow triggers:

* placeholders outnumber the readable characters around them; or
* somewhere on the page, at least ``UNREADABLE_RUN_GLYPHS`` placeholders run
  together (whitespace between them allowed, since pdfplumber inserts word
  spaces by x-gap). That is a whole word or more that nobody can read - the
  case a readable letterhead over an unreadable body hides from a page-wide
  ratio.

One unmapped symbol in a page of good text - a bullet, a currency sign in a
symbol font - trips neither. Placeholders are never stripped: the page's text
stays what the text layer held, and the caller decides the page's status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: pdfminer's exact rendering of an unmapped glyph (``PDFLayoutAnalyzer.handle_undefined_char``).
UNDEFINED_GLYPH = re.compile(r"\(cid:\d+\)")
_UNDEFINED_RUN = re.compile(r"(?:\(cid:\d+\)\s*)+")

#: Consecutive placeholders that make a page unusable however much readable
#: text surrounds them - longer than any isolated symbol, shorter than a line.
UNREADABLE_RUN_GLYPHS = 8


@dataclass(frozen=True)
class NativeTextQuality:
    #: ``(cid:N)`` placeholders - one per glyph pdfminer could not map.
    undefined_glyphs: int
    #: Non-whitespace characters outside those placeholders.
    readable_chars: int
    #: The most placeholders found together, whitespace between them allowed.
    longest_undefined_run: int = 0

    @property
    def cid_dominated(self) -> bool:
        """Strictly more unmapped glyphs than readable characters."""
        return self.undefined_glyphs > self.readable_chars

    @property
    def unusable(self) -> bool:
        """The text layer cannot stand in for the page's content."""
        return self.cid_dominated or self.longest_undefined_run >= UNREADABLE_RUN_GLYPHS

    def describe(self) -> str:
        """A customer-text-free account of why, safe to persist as an error."""
        total = self.undefined_glyphs + self.readable_chars
        return (
            f"{self.undefined_glyphs} of {total} glyphs are undefined (cid:N) "
            f"placeholders, longest run {self.longest_undefined_run}"
        )


def assess_native_text_quality(text: str) -> NativeTextQuality:
    """Count unmapped-glyph placeholders and the readable text around them."""
    body = text or ""
    undefined = len(UNDEFINED_GLYPH.findall(body))
    residue = UNDEFINED_GLYPH.sub("", body)
    readable = sum(1 for character in residue if not character.isspace())
    longest = max(
        (len(UNDEFINED_GLYPH.findall(run.group())) for run in _UNDEFINED_RUN.finditer(body)),
        default=0,
    )
    return NativeTextQuality(
        undefined_glyphs=undefined,
        readable_chars=readable,
        longest_undefined_run=longest,
    )
