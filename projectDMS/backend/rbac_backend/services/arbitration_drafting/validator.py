from __future__ import annotations

import re
from typing import Any, Dict, List


class ArbitrationDraftValidator:
    def validate_generated(self, context: Dict[str, Any], generated: Dict[str, Any]) -> List[str]:
        warnings: List[str] = []
        source_ledger = context.get("source_ledger") or []
        markdown = generated.get("full_markdown") or ""
        if not source_ledger and "[Evidence required]" not in markdown:
            warnings.append("Generated draft has no sources and no missing-evidence marker.")
        if context.get("draft", {}).get("draft_type") == "rejoinder":
            paragraphs = context.get("paragraph_responses") or []
            if not paragraphs:
                warnings.append("Rejoinder requires imported SoD paragraph responses.")
            if self._looks_like_new_claim(markdown):
                warnings.append("Rejoinder may introduce a new claim; mark for legal review before filing.")
        return warnings

    def _looks_like_new_claim(self, markdown: str) -> bool:
        text = markdown.lower()
        return bool(re.search(r"\bnew claim\b|\bfresh claim\b|\badditional claim\b", text))

