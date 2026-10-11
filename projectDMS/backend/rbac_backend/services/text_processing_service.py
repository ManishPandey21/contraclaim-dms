# services/text_processing_service.py
import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models.document_metadata import ParsedDocumentMetadata, build_parsed_metadata
from ..utils.date_parser import format_date_ddmmyyyy
from .reference_parser import parse_legacy_reference_text

logger = logging.getLogger(__name__)

#: Item 6. Must not match item 20, "Reference Chain", whose content is a
#: description ("reply to previous letter"), not a reference.
_REFERENCES_LABEL = r"^References?(?!\s*Chain)\b"

#: A summary line's own list marker ("- ", "* ", "1. ", "2) "). Only the
#: marker: the summary is a chronology, and a line that opens with its date
#: ("- 01-08-2024: ...", "12.09.2024 ...") must keep it. The earlier
#: character-class strip ate every leading digit, dot and hyphen.
_SUMMARY_LINE_MARKER = re.compile(r"^\s*(?:[-*•]+\s*)?(?:\(?\d{1,3}[.)]\s+)?")

#: Report item number -> the label(s) that item may carry: the current
#: prompt (services/openai_service.py) and the older 7-11 layout. A numbered
#: line with any other label is text, not an item.
_ITEM_LABELS: Dict[int, str] = {
    1: r"^Date\b",
    2: r"^Letter\s*(?:No|Number)\b",
    3: r"^From\b",
    4: r"^To\b",
    5: r"^Subject\b",
    6: _REFERENCES_LABEL,
    7: r"^(?:Asset\s*Type|Summary)\b",
    8: r"^(?:Location|Key\s*Words)\b",
    9: r"^(?:Specific\s*Area|Contractual\s*Clauses)\b",
    10: r"^(?:Key\s*Reply\s*Points|Full\s*content)\b",
    11: r"^(?:Chainage\s*From|Full\s*content)\b",
    12: r"^Chainage\s*To\b",
    13: r"^Work\s*Type\b",
    14: r"^Issue\s*Nature\b",
    15: r"^Claim\s*Category\b",
    16: r"^Alleged\s*Responsibility\b",
    17: r"^Priority\b",
    18: r"^Key\s*Words\b",
    19: r"^Linked\s*Event\s*Suggested\b",
    20: r"^Reference\s*Chain\b",
    21: r"^(?:Additional\s*)?Key\s*Words\b|^Additional\s*Keywords\b",
    22: r"^Summary\b",
    23: r"^Contractual\s*Clauses\b",
    24: r"^Key\s*Reply\s*Points\b",
    25: r"^Full\s*Content\b",
    26: r"^(?:extracted[_\s-]*)?tags\b",
    27: r"^(?:extracted[_\s-]*)?sub\s*tags\b|^extracted_subTags\b|^subTags\b",
}

#: Report item number (current prompt) -> ParsedDocumentMetadata field.
_FIELD_BY_ITEM: Dict[int, str] = {
    1: "date",
    2: "letter_no",
    3: "from_company",
    4: "to_company",
    5: "subject",
    6: "references",
    7: "asset_type",
    8: "location",
    9: "specific_area",
    11: "chainage_from",
    12: "chainage_to",
    13: "work_type",
    14: "issue_nature",
    15: "claim_category",
    16: "alleged_responsibility",
    17: "priority",
    18: "keywords",
    19: "linked_event_suggested",
    20: "reference_chain",
    21: "additional_keywords",
    22: "summary",
    23: "contractual_clauses",
    24: "key_reply_points",
    25: "full_content",
    26: "tags",
    27: "sub_tags",
}


class TextProcessingService:
    """Service for text processing and metadata parsing"""

    def __init__(self, config):
        self.config = config

    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into chunks with overlap.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        text = text.strip()
        if not text:
            return []

        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                separators=["\n\n", "\n", ". ", " "],
            )
            langchain_chunks = [
                chunk.strip()
                for chunk in splitter.split_text(text)
                if chunk and chunk.strip()
            ]
            if langchain_chunks:
                logger.info("LangChain splitter produced %d chunks", len(langchain_chunks))
                return langchain_chunks
        except ImportError:
            logger.debug("langchain-text-splitters not available; using fallback chunker")
        except Exception as exc:
            logger.warning("LangChain splitter failed: %s", exc)

        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(text_length, start + self.config.chunk_size)
            chunk = text[start:end]
            chunks.append(chunk)

            if end == text_length:
                break

            # Move start position with overlap
            start = max(start + self.config.chunk_size - self.config.chunk_overlap, start + 1)

        logger.info(f"Split text into {len(chunks)} chunks")
        return chunks

    def _parse_numbered_blocks(
        self,
        text: str,
        unrecognised: Optional[Dict[int, str]] = None,
        header_lines: Optional[List[int]] = None,
    ) -> Dict[int, Dict[str, str]]:
        """Parse report lines like ``14) Issue Nature: ...`` into blocks.

        A numbered line opens a block only when its label is the one the report
        schema expects for that number, and that number has not been seen.

        Inside References (a list) or inside Full Content (the letter's own
        text), any other numbered line is content: "1) Our letter AAA/1 dated:
        ...", "2) Letter No. of Engineer: ENG/9", a paragraph "26) Payment
        claim: ...". Once Full Content starts, only later items can follow.

        Elsewhere in the header, a numbered item whose label is not the
        expected one ("3) Sender (Company): ...") is set aside and reported in
        ``unrecognised`` (number -> label). It is never glued onto the previous
        field, which would corrupt that field's value.

        Markdown emphasis around labels ("1) **Date:** 01-08-2024") is ignored.

        ``header_lines``, when given, receives the index (in
        ``text.splitlines()``) of every line that opened an item.
        """
        blocks: Dict[int, Dict[str, str]] = {}
        current: Optional[int] = None
        full_content_number: Optional[int] = None
        set_aside = False
        for line_index, raw_line in enumerate((text or "").splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(
                r"^(\d+)\)\s*(?:\*\*|__)?\s*([^:\n]+?)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*)$",
                line,
            )
            # Only a numbered line can open or set aside an item; every other
            # line is content. Scoping the item logic to a real match keeps
            # the number and label it reads from ever being absent.
            if match is not None:
                number = int(match.group(1))
                label = match.group(2).strip().strip("*").strip()
                label_expected = (
                    re.search(_ITEM_LABELS.get(number, r"(?!)"), label, flags=re.I) is not None
                )
                if (
                    label_expected
                    and number not in blocks
                    and (full_content_number is None or number > full_content_number)
                ):
                    current = number
                    set_aside = False
                    if unrecognised is not None:
                        unrecognised.pop(number, None)
                    if re.search(r"Full\s*content", label, flags=re.I):
                        full_content_number = number
                    if header_lines is not None:
                        header_lines.append(line_index)
                    content = match.group(3).strip()
                    if content.endswith("**"):
                        content = content[:-2].rstrip()
                    blocks[current] = {"label": label, "content": content}
                    continue
                if (
                    number in _ITEM_LABELS
                    and number not in blocks
                    and full_content_number is None
                    and current != 6
                    # Only a number past the current item is header drift. A lower
                    # number ("10) Reserve rights: ..." inside Summary or Key
                    # Reply Points) is a sub-list line of the current item.
                    and (current is None or number > current)
                ):
                    # A header item with a label we do not recognise: its value is
                    # unknown, and must not become part of the previous field.
                    set_aside = True
                    if unrecognised is not None:
                        unrecognised.setdefault(number, label)
                    continue
            if set_aside:
                continue
            if current is not None:
                existing = blocks[current].get("content", "")
                blocks[current]["content"] = f"{existing}\n{line}".strip()
        return blocks

    def trim_incomplete_report(self, report: str) -> tuple[str, List[int]]:
        """Keep only the items a cut-off report finished writing.

        A reply that stopped at the output-token limit stopped inside its last
        item, so that item - whichever it is - is partial and is dropped, with
        everything after it. Every earlier item is followed by the next item's
        header and was therefore written out in full.

        Items are recognised exactly as :meth:`parse_extraction_report`
        recognises them, so a numbered paragraph inside the letter text is not
        mistaken for a header. Returns the trimmed report and the item numbers
        it still contains.
        """
        lines = [line.strip() for line in (report or "").splitlines() if line.strip()]
        headers: List[int] = []
        blocks = self._parse_numbered_blocks("\n".join(lines), header_lines=headers)
        if not headers:
            return "", []
        last = headers[-1]
        kept_text = "\n".join(lines[:last])
        # Blocks are recorded in header order; the last one is the cut item.
        return kept_text, sorted(list(blocks)[:-1])

    def _block_by_number(self, blocks: Dict[int, Dict[str, str]], number: int, label_pattern: str) -> Optional[str]:
        block = blocks.get(number)
        if not block:
            return None
        if re.search(label_pattern, block.get("label", ""), flags=re.I):
            return self._null_if_placeholder(block.get("content"))
        return None

    def _block_by_label(self, blocks: Dict[int, Dict[str, str]], label_pattern: str) -> Optional[str]:
        for block in blocks.values():
            if re.search(label_pattern, block.get("label", ""), flags=re.I):
                return self._null_if_placeholder(block.get("content"))
        return None

    @staticmethod
    def _references_item_content(
        blocks: Dict[int, Dict[str, str]], header_text: str
    ) -> Optional[str]:
        """Raw content of the References item, or None when there is none."""
        for block in blocks.values():
            if re.search(_REFERENCES_LABEL, block.get("label", ""), flags=re.I):
                return block.get("content") or ""
        match = re.search(
            r"(?im)^\s*(?:-\s*)?References?\s*(?:\(Ref\.?\))?\s*[:\-]\s*(.*)$", header_text
        )
        return match.group(1) if match else None

    @staticmethod
    def _is_explicit_none(value: str) -> bool:
        cleaned = value.strip().strip("[]").strip()
        return bool(
            re.fullmatch(
                r"(null|'null'|\"null\"|not\s+found|none|n/?a|not\s+applicable|nil|-|--)",
                cleaned,
                flags=re.I,
            )
        )

    def _null_if_placeholder(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().strip("[]").strip()
        if re.search(r"^(null|'null'|\"null\"|not\s+found|none|n/?a|not\s+applicable|-|--)$", cleaned, re.I):
            return None
        return cleaned or None

    def _first_block(self, blocks: Dict[int, Dict[str, str]], candidates: List[tuple[int, str]], fallback_label: Optional[str] = None) -> Optional[str]:
        for number, label_pattern in candidates:
            value = self._block_by_number(blocks, number, label_pattern)
            if value:
                return value
        if fallback_label:
            return self._block_by_label(blocks, fallback_label)
        return None

    def _list_from_block(self, block: Optional[str], *, parse_references: bool = False) -> List[Any]:
        if not block:
            return []
        parsed = self._parse_list_block(block, parse_references=parse_references)
        return [item for item in parsed if item]

    def _summary_from_block(self, block: Optional[str]) -> Optional[str]:
        if not block:
            return None
        lines_clean = []
        for line in block.splitlines():
            cleaned = _SUMMARY_LINE_MARKER.sub("", line).strip()
            if cleaned:
                lines_clean.append(f"- {cleaned}")
        return "\n".join(lines_clean) if lines_clean else self._null_if_placeholder(block)

    def parse_extraction_report(self, report: str) -> ParsedDocumentMetadata:
        """
        Parse structured extraction report into metadata object.

        Field isolation: a field that fails to parse or validate is dropped
        and recorded in ``field_failures``; every other field is kept. There is
        deliberately no whole-object fallback. Returning an empty model on any
        error once let one reference-shaped keyword ("letter no. XYZ/12 dated
        01.08.2024") erase date, letter number, parties, subject, summary and
        references while the document was still reported as extracted (DI-B3).

        Args:
            report: Structured text report from LLM

        Returns:
            ParsedDocumentMetadata object
        """
        logger.info("Legacy regex metadata parser engaged (report length=%d)", len(report or ""))
        # The report carries document-derived content (incl. the full letter
        # text); flag injection phrasing so a malicious upload is visible to
        # reviewers even when extraction itself succeeds.
        from .ai_guardrails import scan_document_text_for_injection

        scan_document_text_for_injection(report, origin="extraction_report_parse")
        # Normalize text for parsing
        lines = (report or "").splitlines()
        text = "\n".join(line.strip() for line in lines if line.strip())

        unrecognised_items: Dict[int, str] = {}
        numbered_blocks = self._parse_numbered_blocks(text, unrecognised_items)
        field_failures: Dict[str, str] = {}
        # Label-regex fallbacks read only the report header. The letter's own
        # text (Full Content) routinely contains "Letter No.:", "Ref:",
        # "Subject:" lines of *other* letters.
        full_content_start = re.search(r"(?im)^\s*(?:\d+\)\s*|-\s*)?Full\s*content\b", text)
        header_text = text[: full_content_start.start()] if full_content_start else text

        # Extract individual fields. Prefer exact numbered blocks for the
        # expanded prompt, then fall back to legacy regex labels.
        date_raw = self._first_block(numbered_blocks, [(1, r"^Date$")], r"^Date$")
        date_str = date_raw or self._extract_field(header_text, [
            r"^\s*(?:1\)|-)?.*?Date\s*[:\-]\s*(.+)$",
            r"^\s*Date\s*\.\s*(.+)$",
            r"^\s*Dated?\s*[:\-]\s*(.+)$",
        ])
        date_str = format_date_ddmmyyyy(date_str)

        subject = self._first_block(numbered_blocks, [(5, r"Subject")], r"Subject") or self._extract_field(header_text, [
            r"^\s*(?:5\)|-)?.*?Subject\s*[:\-]\s*(.+)$",
            r"^\s*Re\s*[:\-]\s*(.+)$",
        ])

        letter_no = self._first_block(numbered_blocks, [(2, r"Letter\s*No")], r"Letter\s*(No|Number)") or self._extract_field(header_text, [
            r"^\s*(?:2\)|-)?.*?Letter\s*No\.?\s*[:\-]\s*(.+)$",
            r"^\s*Letter\s*Number\s*[:\-]\s*(.+)$",
            r"^\s*Ref(?:erence)?\s*No\.?\s*[:\-]\s*(.+)$",
        ])

        from_company = self._first_block(numbered_blocks, [(3, r"From")], r"^From") or self._extract_field(header_text, [
            r"^\s*(?:3\)|-)?.*?From\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
            r"^\s*Sender\s*[:\-]\s*(.+)$",
            r"^\s*From\s*[:\-]\s*(.+)$",
        ])

        to_company = self._first_block(numbered_blocks, [(4, r"To")], r"^To") or self._extract_field(header_text, [
            r"^\s*(?:4\)|-)?.*?To\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
            r"^\s*Recipient\s*[:\-]\s*(.+)$",
            r"^\s*To\s*[:\-]\s*(.+)$",
        ])

        asset_type = self._first_block(numbered_blocks, [(7, r"Asset\s*Type")])
        location = self._first_block(numbered_blocks, [(8, r"Location")])
        specific_area = self._first_block(numbered_blocks, [(9, r"Specific\s*Area")])
        chainage_from = self._first_block(numbered_blocks, [(11, r"Chainage\s*From")])
        chainage_to = self._first_block(numbered_blocks, [(12, r"Chainage\s*To")])
        work_type = self._first_block(numbered_blocks, [(13, r"Work\s*Type")])
        issue_nature = self._first_block(numbered_blocks, [(14, r"Issue\s*Nature")])
        claim_category = self._first_block(numbered_blocks, [(15, r"Claim\s*Category")])
        alleged_responsibility = self._first_block(numbered_blocks, [(16, r"Alleged\s*Responsibility")])
        priority = self._first_block(numbered_blocks, [(17, r"Priority")])
        linked_event_suggested = self._first_block(numbered_blocks, [(19, r"Linked\s*Event\s*Suggested")])
        reference_chain = self._first_block(numbered_blocks, [(20, r"Reference\s*Chain")])

        # References are the only field run through the reference parser, and
        # a failure there is contained to the references field.
        references: List[Any] = []
        try:
            references_block = self._first_block(
                numbered_blocks, [(6, _REFERENCES_LABEL)], _REFERENCES_LABEL
            )
            references = self._list_from_block(references_block, parse_references=True)
            has_numbered_item = any(
                re.search(_REFERENCES_LABEL, block.get("label", ""), flags=re.I)
                for block in numbered_blocks.values()
            )
            # The label regex is for reports without numbered items. With a
            # numbered References item, its content is the answer: running
            # the regex on an empty item captured the *next* item
            # ("7) Asset Type: Station") as a reference.
            if not references and not has_numbered_item:
                references = self._extract_list_field(header_text, [
                    r"(?ims)^\s*(?:6\)|-)?.*?References?\s*(?:\(Ref\.?\))?\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)\s*[A-Z]|Summary|Key\s*Words|Contractual\s*Clauses|Full\s*content|$))"
                ], parse_references=True)
        except Exception as exc:
            references = []
            field_failures["references"] = f"reference parsing failed: {exc.__class__.__name__}"
            logger.warning("Reference parsing failed; other metadata fields kept: %s", exc)
        # An empty list is "cites nothing" only when the References item says
        # so explicitly ("null", "none", "not found" - the prompt asks for
        # that). A missing item, an empty one, or content that yielded no
        # reference is "not read", and must not clear existing links.
        if not references and "references" not in field_failures:
            raw_item = self._references_item_content(numbered_blocks, header_text)
            if raw_item is None:
                field_failures["references"] = "references item absent from extraction output"
            elif not self._is_explicit_none(raw_item):
                field_failures["references"] = "references item present but no reference could be read"

        summary = self._summary_from_block(
            self._first_block(numbered_blocks, [(22, r"Summary"), (7, r"Summary")])
        ) or self._extract_summary(header_text)

        keywords = self._list_from_block(
            self._first_block(numbered_blocks, [(18, r"Key\s*Words"), (8, r"Key\s*Words")])
        )
        if not keywords:
            keywords = self._extract_list_field(header_text, [
                r"(?ims)^.*?Key Words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))",
                r"(?ims)^.*?Key contractual words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))"
            ])

        additional_keywords = self._list_from_block(
            self._first_block(numbered_blocks, [(21, r"Key\s*Words|Additional\s*Keywords")])
        )

        contractual_clauses = self._list_from_block(
            self._first_block(numbered_blocks, [(23, r"Contractual\s*Clauses"), (9, r"Contractual\s*Clauses")])
        )
        if not contractual_clauses:
            contractual_clauses = self._extract_list_field(header_text, [
                r"(?ims)^.*?Contractual Clauses[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Key\s*Reply\s*Points|Full content|$))"
            ])

        # Filter out "Not found" entries
        if contractual_clauses and len(contractual_clauses) == 1:
            if re.search(r"^\s*not\s*found\s*$", contractual_clauses[0], re.IGNORECASE):
                contractual_clauses = []

        # Key Reply Points: points to address while responding. The prompt
        # labels this "Key Reply Points — Points to be Addressed While
        # Responding:", so tolerate any descriptive text before the
        # delimiting colon via [^:\n]* so the subtitle is not captured.
        key_reply_points = self._list_from_block(
            self._first_block(numbered_blocks, [(24, r"Key\s*Reply\s*Points"), (10, r"Key\s*Reply\s*Points")])
        )
        if not key_reply_points:
            key_reply_points = self._extract_list_field(header_text, [
                r"(?ims)^.*?Key\s*Reply\s*Points[^:\n]*[:\-]\s*(.+?)(?=\n\s*(?:\d+\)|Full content|$))"
            ])

        if key_reply_points and len(key_reply_points) == 1:
            if re.search(r"^\s*not\s*found\s*$", key_reply_points[0], re.IGNORECASE):
                key_reply_points = []

        full_content = self._first_block(
            numbered_blocks,
            [(25, r"Full\s*Content"), (11, r"Full\s*content"), (10, r"Full\s*content")],
            r"Full\s*content",
        ) or self._extract_field(text, [
            r"(?is)^\s*(?:11\)|10\)|-)?.*?Full\s*content\s*[:\-]?\s*(.+)$"
        ])

        tags = self._list_from_block(
            self._first_block(numbered_blocks, [(26, r"(?:extracted[_\s-]*)?tags")])
        )
        sub_tags = self._list_from_block(
            self._first_block(
                numbered_blocks,
                [(27, r"(?:extracted[_\s-]*)?sub\s*tags|extracted_subTags|subTags")],
            )
        )

        metadata = build_parsed_metadata(
            {
                "date": date_str,
                "subject": subject,
                "letter_no": letter_no,
                "from_company": from_company,
                "to_company": to_company,
                "references": references or [],
                "asset_type": asset_type,
                "location": location,
                "specific_area": specific_area,
                "chainage_from": chainage_from,
                "chainage_to": chainage_to,
                "work_type": work_type,
                "issue_nature": issue_nature,
                "claim_category": claim_category,
                "alleged_responsibility": alleged_responsibility,
                "priority": priority,
                "summary": summary,
                "keywords": keywords or [],
                "linked_event_suggested": linked_event_suggested,
                "reference_chain": reference_chain,
                "additional_keywords": additional_keywords or [],
                "contractual_clauses": contractual_clauses or [],
                "key_reply_points": key_reply_points or [],
                "full_content": full_content,
                "tags": tags or [],
                "sub_tags": sub_tags or [],
            },
            field_failures=field_failures,
        )

        # An item whose label was not recognised is unknown, not empty - unless
        # a label fallback recovered it.
        for number, label in unrecognised_items.items():
            field = _FIELD_BY_ITEM.get(number)
            if field and getattr(metadata, field, None) in (None, "", []):
                metadata.field_failures.setdefault(
                    field, f"item {number} has an unrecognised label {label!r}"
                )

        if metadata.field_failures:
            logger.warning(
                "Parsed document metadata with field failures (kept all other fields): %s",
                sorted(metadata.field_failures),
            )
        else:
            logger.info("Successfully parsed document metadata")
        return metadata

    def _extract_field(self, text: str, patterns: List[str]) -> Optional[str]:
        """Extract single field using regex patterns"""
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip().rstrip(":")
                return value if value else None
        return None

    def _extract_list_field(
        self, text: str, patterns: List[str], *, parse_references: bool = False
    ) -> List[Any]:
        """Extract list field using regex patterns
        Note: Search against (text + "\
End:") so '$' can match properly without forcing End: immediately after."""
        for pattern in patterns:
            match = re.search(pattern, text + "\nEnd:", flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
            if match:
                block = match.group(1).strip()
                return self._parse_list_block(block, parse_references=parse_references)
        return []

    def _parse_list_block(self, block: str, *, parse_references: bool = False) -> List[Any]:
        """Parse block of text into list items.

        Only the references field may turn an item into a structured
        reference. Every other list field is ``List[str]``: a keyword such as
        "letter no. XYZ/12 dated 01.08.2024" is kept as the string it is.
        """
        if not block:
            return []

        def _item(value: str) -> Any:
            if parse_references:
                return parse_legacy_reference_text(value) or value
            return value

        items = []
        for line in block.splitlines():
            # Remove bullet prefixes and clean up
            cleaned = re.sub(r"^[\-\u2013\u2022\*\d\.\)\s]+", "", line).strip()
            if cleaned:
                items.append(_item(cleaned))

        # Handle comma-separated single line
        if len(items) == 1 and isinstance(items[0], str) and "," in items[0]:
            comma_items = [item.strip() for item in items[0].split(",") if item.strip()]
            if len(comma_items) > 1:
                items = [_item(item) for item in comma_items]

        # Clean up punctuation
        cleaned_items = []
        for item in items:
            if isinstance(item, str):
                cleaned = re.sub(r"[;\.\s]+$", "", item).strip()
                if cleaned:
                    cleaned_items.append(_item(cleaned))
            elif item:
                cleaned_items.append(item)

        return cleaned_items

    def _extract_summary(self, text: str) -> Optional[str]:
        """Extract summary with special formatting.
        Note: Search against (text + "\
End:") so '$' can match the artificial end marker."""
        pattern = r"(?ims)^\s*(?:7\)|-)?.*?Summary\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Contractual Clauses|Full content|$))"
        match = re.search(pattern, text + "\nEnd:")

        if match:
            raw_summary = match.group(1).strip()

            # Format as bullet points
            lines_clean = []
            for line in raw_summary.splitlines():
                cleaned = _SUMMARY_LINE_MARKER.sub("", line).strip()
                if cleaned:
                    lines_clean.append(f"- {cleaned}")

            return "\n".join(lines_clean) if lines_clean else raw_summary

        return None

    def parse_date_safe(self, date_str: Optional[str]) -> Optional[datetime]:
        """Safely parse date string to datetime object"""
        if not date_str:
            return None

        cleaned = date_str.strip()
        cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", cleaned, flags=re.IGNORECASE)

        patterns = [
            "%Y-%m-%d",              # 2024-01-15
            "%d-%m-%Y",              # 15-01-2024
            "%d/%m/%Y",              # 15/01/2024
            "%m/%d/%Y",              # 01/15/2024
            "%Y/%m/%d",              # 2024/01/15
            "%d-%b-%Y",              # 15-Jan-2024
            "%d %b %Y",              # 15 Jan 2024
            "%b %d, %Y",             # Jan 15, 2024
            "%dth %b %Y",            # 15th Jan 2024
            "%dst %b %Y",            # 1st Jan 2024
            "%dnd %b %Y",            # 2nd Jan 2024
            "%drd %b %Y",            # 3rd Jan 2024
            "%dth %B %Y",            # 15th January 2024
            "%dst %B %Y",            # 1st January 2024
            "%dnd %B %Y",            # 2nd January 2024
            "%drd %B %Y",            # 3rd January 2024
            "%dth %b, %Y",           # 15th Jan, 2024
            "%dst %b, %Y",           # 1st Jan, 2024
            "%dnd %b, %Y",           # 2nd Jan, 2024
            "%drd %b, %Y",           # 3rd Jan, 2024
            "%B %dth, %Y",           # January 15th, 2024
            "%B %dst, %Y",           # January 1st, 2024
            "%B %dnd, %Y",           # January 2nd, 2024
            "%B %drd, %Y",           # January 3rd, 2024
            "%b %dth, %Y",           # Jan 15th, 2024
            "%b %dst, %Y",           # Jan 1st, 2024
            "%b %dnd, %Y",           # Jan 2nd, 2024
            "%b %drd, %Y",           # Jan 3rd, 2024
            "%Y-%m-%dT%H:%M:%S",     # 2024-01-15T13:45:00
            "%Y-%m-%d %H:%M:%S",     # 2024-01-15 13:45:00
            "%Y-%m-%dT%H:%M:%S.%f",  # 2024-01-15T13:45:00.123456
            "%Y-%m-%d %H:%M:%S.%f",  # 2024-01-15 13:45:00.123456
            "%d.%m.%Y",              # 15.01.2024 (common in your documents)
            "%dth %b. %Y",           # 15th Jan. 2024
            "%dst %b. %Y",           # 1st Jan. 2024
            "%dnd %b. %Y",           # 2nd Jan. 2024
            "%drd %b. %Y",           # 3rd Jan. 2024
        ]

        for pattern in patterns:
            try:
                return datetime.strptime(cleaned, pattern)
            except ValueError:
                continue

        logger.warning(f"Unable to parse date: {date_str}")
        return None
