"""Native text dominated by pdfminer's undefined-glyph placeholders is not text."""

from __future__ import annotations

from rbac_backend.services.extraction.text_quality import (
    assess_native_text_quality,
    withhold_unusable,
)

_GARBAGE = "".join(f"(cid:{ord(character)})" for character in "Claim summary page 3")


def test_plain_text_is_usable() -> None:
    quality = assess_native_text_quality("Claim summary page 3\nPage 1 of 2")

    assert quality.undefined_glyphs == 0
    assert quality.readable_chars == len("Claimsummarypage3Page1of2")
    assert quality.cid_dominated is False


def test_a_page_of_placeholders_is_cid_dominated() -> None:
    quality = assess_native_text_quality(_GARBAGE + "\n" + _GARBAGE)

    assert quality.undefined_glyphs == 2 * len("Claim summary page 3")
    assert quality.readable_chars == 0
    assert quality.cid_dominated is True
    assert quality.unusable is True


def test_readable_header_over_an_unreadable_body_is_cid_dominated() -> None:
    # A running header in a mapped font does not rescue a body set in an
    # unmapped one: most of the page's glyphs are still unreadable.
    text = "Page 4\n" + _GARBAGE * 5

    quality = assess_native_text_quality(text)

    assert quality.readable_chars == len("Page4")
    assert quality.cid_dominated is True


def test_one_isolated_placeholder_in_real_text_is_not_dominated() -> None:
    # A single symbol glyph (a bullet, a rupee sign in a symbol font) that
    # pdfminer could not map must not discard a page of good text.
    text = (
        "The Contractor hereby submits its monthly interim payment application "
        "(cid:127) in respect of the permanent works executed at the station."
    )

    quality = assess_native_text_quality(text)

    assert quality.undefined_glyphs == 1
    assert quality.cid_dominated is False
    assert quality.unusable is False


def test_an_even_split_is_not_dominated() -> None:
    # Dominated means strictly more placeholders than readable characters.
    quality = assess_native_text_quality("abcd" + "(cid:1)" * 4)

    assert quality.undefined_glyphs == 4
    assert quality.readable_chars == 4
    assert quality.cid_dominated is False


def test_empty_text_is_not_cid_dominated() -> None:
    # Thin or empty text is the character threshold's business, not this one's.
    for text in ("", "   \n  "):
        assert assess_native_text_quality(text).cid_dominated is False


def test_the_literal_word_cid_without_the_placeholder_shape_is_text() -> None:
    quality = assess_native_text_quality("cid:12 and (cid) and (cid:x) are words")

    assert quality.undefined_glyphs == 0
    assert quality.cid_dominated is False


def test_describe_reports_the_counts() -> None:
    quality = assess_native_text_quality(_GARBAGE)

    assert quality.describe() == (
        f"{len('Claim summary page 3')} of {len('Claim summary page 3')} glyphs "
        f"are undefined (cid:N) placeholders, longest run {len('Claim summary page 3')}"
    )


def test_an_unreadable_body_under_readable_letterhead_is_unusable() -> None:
    # Measured in review: three readable letterhead lines over a two-line body
    # set in an unmapped font - 128 placeholders against 168 readable
    # characters. Not a majority, but the entire body is lost, so a whole-page
    # ratio alone would report this page COMPLETE.
    letterhead = (
        "Gulermak-Sam India Kanpur Metro JV\n"
        "Kanpur Metro Rail Project, Package KNPCC-05\n"
        "Ref: CC/UPMRC/0412 dated 14 March 2025\n"
    )
    body = "".join(f"(cid:{ord(character)})" for character in "Subject: Notice of claim " * 2)

    quality = assess_native_text_quality(letterhead + body)

    assert quality.cid_dominated is False
    assert quality.longest_undefined_run >= 8
    assert quality.unusable is True


def test_a_run_of_placeholders_counts_across_inserted_word_spaces() -> None:
    # pdfplumber inserts real spaces between words by x-gap, so an unreadable
    # line comes out as placeholder runs broken only by whitespace.
    words = " ".join("(cid:1)(cid:2)(cid:3)" for _ in range(3))
    text = "A long paragraph of perfectly readable text surrounding it. " * 3 + words

    quality = assess_native_text_quality(text)

    assert quality.longest_undefined_run == 9
    assert quality.unusable is True


def test_seven_consecutive_placeholders_in_good_text_are_tolerated() -> None:
    text = "A long paragraph of perfectly readable text surrounding it. " * 3 + "(cid:1)" * 7

    quality = assess_native_text_quality(text)

    assert quality.longest_undefined_run == 7
    assert quality.unusable is False


def test_isolated_placeholders_scattered_through_text_are_tolerated() -> None:
    text = " (cid:127) ".join(["Readable clause text continues here"] * 6)

    quality = assess_native_text_quality(text)

    assert quality.undefined_glyphs == 5
    assert quality.longest_undefined_run == 1
    assert quality.unusable is False


def test_withhold_unusable_keeps_usable_text_published() -> None:
    text = "Readable clause text (cid:127) with one symbol"

    assert withhold_unusable(text) == (text, None)


def test_withhold_unusable_moves_unusable_text_to_evidence_whole() -> None:
    # Not a regex strip: the readable remainder is not published either,
    # because a page that is mostly unreadable does not say what it says.
    text = "Page 4\n" + _GARBAGE * 3

    assert withhold_unusable(text) == ("", text)


def test_withhold_unusable_on_empty_text_withholds_nothing() -> None:
    assert withhold_unusable("") == ("", None)
