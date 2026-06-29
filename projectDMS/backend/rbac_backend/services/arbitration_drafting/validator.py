from __future__ import annotations

import re
from typing import Any, Dict, List


_SOURCE_CITATION_RE = re.compile(r"\[(S\d+):[^\]]+\]")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_AMOUNT_RE = re.compile(
    r"\b(?:INR|USD|EUR|GBP|AED|SAR|QAR|\u20b9|\$|\u20ac|\u00a3)\s*[0-9][0-9,]*(?:\.\d+)?\b",
    flags=re.IGNORECASE,
)


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _source_keys(source_ledger: List[Dict[str, Any]]) -> set[str]:
    return {str(row.get("source_key")) for row in source_ledger if row.get("source_key")}


class ArbitrationDraftValidator:
    def validate_generated(self, context: Dict[str, Any], generated: Dict[str, Any]) -> List[str]:
        return self.validation_report(context, generated.get("full_markdown") or "").get("warnings", [])

    def validation_report(self, context: Dict[str, Any], markdown: str) -> Dict[str, Any]:
        warnings: List[str] = []
        approval_blockers: List[str] = []
        source_ledger = context.get("source_ledger") or []
        source_keys = _source_keys(source_ledger)

        if not source_ledger and "[Evidence required]" not in markdown:
            message = "Generated draft has no sources and no missing-evidence marker."
            warnings.append(message)
            approval_blockers.append(message)

        cited_keys = {match.group(1) for match in _SOURCE_CITATION_RE.finditer(markdown)}
        unknown_keys = sorted(cited_keys - source_keys)
        if unknown_keys:
            message = f"Draft cites sources not present in the source ledger: {', '.join(unknown_keys)}."
            warnings.append(message)
            approval_blockers.append(message)

        source_text = _flatten_text(source_ledger)
        draft_text = _flatten_text(context.get("draft") or {})
        supported_text = f"{source_text} {draft_text}"
        unsupported_amounts = sorted({item.group(0) for item in _AMOUNT_RE.finditer(markdown) if item.group(0) not in supported_text})
        if unsupported_amounts:
            message = f"Draft includes monetary amounts not present in selected evidence or draft inputs: {', '.join(unsupported_amounts[:5])}."
            warnings.append(message)
            approval_blockers.append(message)

        date_matches = {item.group(0) for item in _ISO_DATE_RE.finditer(markdown)}
        date_matches.update(item.group(0) for item in _SLASH_DATE_RE.finditer(markdown))
        unsupported_dates = sorted(date for date in date_matches if date not in supported_text)
        if unsupported_dates:
            message = f"Draft includes dates not present in selected evidence or draft inputs: {', '.join(unsupported_dates[:5])}."
            warnings.append(message)
            approval_blockers.append(message)

        draft_type = context.get("draft", {}).get("draft_type")
        paragraphs = context.get("paragraph_responses") or []
        if draft_type == "statement_of_defence" and not paragraphs:
            warnings.append("Statement of Defence requires imported SoC paragraph responses.")
        if context.get("draft", {}).get("draft_type") == "rejoinder":
            if not paragraphs:
                warnings.append("Rejoinder requires imported SoD paragraph responses.")
            if self._looks_like_new_claim(markdown):
                message = "Rejoinder may introduce a new claim; mark for legal review before filing."
                warnings.append(message)
                approval_blockers.append(message)

        for response in paragraphs:
            if response.get("response_type") in {"deny", "part_admit_part_deny", "not_admitted", "misconceived", "incorrect", "misleading"}:
                if not response.get("supporting_source_ids") and "[Evidence required]" not in str(response.get("response_text") or response.get("response_reason") or ""):
                    warnings.append(
                        f"Paragraph {response.get('source_paragraph_number')} denial requires supporting evidence or an [Evidence required] marker."
                    )

        return {
            "warnings": warnings,
            "approval_blockers": approval_blockers,
            "legal_review_required": bool(warnings),
            "source_count": len(source_ledger),
        }

    def _looks_like_new_claim(self, markdown: str) -> bool:
        text = markdown.lower()
        return bool(re.search(r"\bnew claim\b|\bfresh claim\b|\badditional claim\b", text))
