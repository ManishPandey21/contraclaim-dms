"""LLM-backed arbitration draft generator (audit item 6).

Produces filing-quality prose behind the *same* source-ledger + validator
contract as the deterministic generator:

- The deterministic generator runs first and supplies the canonical, fully
  grounded section skeleton (headings, ``[S#: citation]`` labels, and
  ``[Evidence required]`` markers).
- The LLM is asked only to rewrite each section into formal pleading prose,
  preserving every citation token and evidence marker verbatim and adding no
  new amounts, dates, parties, or citations.
- The output is assembled through the deterministic markdown builder and then
  handed back to the caller, which runs the unchanged validator. Any fabricated
  citation/amount/date is therefore blocked exactly as for a deterministic draft.
- Per-section fallback: if the model omits a section or returns nothing usable,
  the deterministic body is kept. No API key -> full deterministic fallback with
  a context warning.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from ...config.document_processing_config import DocumentProcessingConfig
from ...retrieval.generator import LLMGenerator
from .generator import ArbitrationDraftGenerator

logger = logging.getLogger(__name__)

LLM_DRAFT_PROMPT_VERSION = "arbitration_pleadings_llm.v1"
DEFAULT_DRAFT_MODEL = "gpt-4o"

# Sections whose body is structural/verbatim and must not be reworded by the LLM.
_VERBATIM_SECTIONS = {"caption", "index", "annexures", "verification"}


def resolve_draft_model(options: Optional[Dict[str, Any]] = None) -> str:
    return str(
        (options or {}).get("draft_model")
        or os.getenv("ARBITRATION_DRAFT_MODEL")
        or DEFAULT_DRAFT_MODEL
    )


class LLMDraftGenerator:
    def __init__(
        self,
        *,
        generator: Optional[LLMGenerator] = None,
        model: Optional[str] = None,
    ) -> None:
        self.model_name = model or resolve_draft_model()
        self.generator = generator or LLMGenerator(DocumentProcessingConfig(openai_model=self.model_name))
        self._deterministic = ArbitrationDraftGenerator()

    @property
    def available(self) -> bool:
        return bool(getattr(self.generator, "available", False))

    async def generate(
        self,
        context: Dict[str, Any],
        *,
        section_key: Optional[str] = None,
        additional_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        base = self._deterministic.generate(
            context,
            section_key=section_key,
            additional_instruction=additional_instruction,
        )
        sections: List[Dict[str, str]] = base["sections"]
        rewritable = [s for s in sections if s["key"] not in _VERBATIM_SECTIONS and (s.get("body") or "").strip()]
        if not rewritable:
            return base

        prompt = self._build_prompt(context, rewritable, additional_instruction)
        try:
            raw = await self.generator.generate(prompt, max_tokens=3500, model=self.model_name)
            rewrites = self._parse_sections(raw)
        except Exception as exc:  # pragma: no cover - external call / parse guard
            logger.warning("LLM draft generation failed, using deterministic body: %s", exc)
            context.setdefault("context_warnings", []).append(
                "LLM draft generation failed; the deterministic source-grounded draft was used instead."
            )
            return base

        new_sections: List[Dict[str, str]] = []
        rewritten = 0
        for section in sections:
            body = section.get("body") or ""
            candidate = rewrites.get(section["key"])
            if candidate and section["key"] not in _VERBATIM_SECTIONS:
                cleaned = self._reconcile(candidate, body)
                if cleaned:
                    body = cleaned
                    rewritten += 1
            new_sections.append({**section, "body": body})

        if not rewritten:
            context.setdefault("context_warnings", []).append(
                "LLM draft returned no usable prose; the deterministic source-grounded draft was used instead."
            )
            return base

        markdown = self._deterministic._markdown(
            context["draft"], new_sections, context, additional_instruction=additional_instruction
        )
        structured = {
            **base["structured_output"],
            "prompt_version": LLM_DRAFT_PROMPT_VERSION,
            "generation_mode": "llm",
            "llm_rewritten_sections": rewritten,
            "model": self.model_name,
        }
        return {
            **base,
            "sections": new_sections,
            "full_markdown": markdown,
            "structured_output": structured,
            "ai_prompt_version": LLM_DRAFT_PROMPT_VERSION,
            "model": self.model_name,
        }

    # --- prompt / parse -------------------------------------------------------

    def _build_prompt(
        self,
        context: Dict[str, Any],
        sections: List[Dict[str, str]],
        additional_instruction: Optional[str],
    ) -> str:
        draft = context.get("draft") or {}
        pleading_plan = context.get("pleading_plan") or {}
        allowed = sorted(self._citation_tokens(sections))
        lines = [
            "You are an arbitration counsel drafting a construction pleading.",
            f"Pleading type: {draft.get('draft_type')}. Title: {draft.get('title')}.",
            "",
            "Rewrite each section below into formal, persuasive but neutral pleading prose.",
            "HARD RULES (a violation makes the draft unfilable):",
            "- Preserve EVERY bracketed citation token (e.g. [S1: CPL/2025/0142]) exactly as written, in place.",
            "- Preserve EVERY [Evidence required] marker exactly; never remove one and never invent facts to replace it.",
            "- Do NOT introduce any new amount, date, party name, clause number, or citation token.",
            "- Only these citation tokens may appear: " + (", ".join(allowed) if allowed else "(none)") + ".",
            "- Keep numbered lists numbered. Do not merge separate claims.",
            "- Return ONLY a JSON object mapping section_key -> rewritten body. No prose, no fences.",
        ]
        if pleading_plan:
            planned_sections = [
                str(item.get("key"))
                for item in pleading_plan.get("section_structure") or []
                if item.get("key")
            ]
            lines.extend(
                [
                    f"- Approved pleading plan hash: {pleading_plan.get('plan_hash')}.",
                    "- Approved planned sections: " + (", ".join(planned_sections) if planned_sections else "(none)"),
                    "- Follow the approved plan decisions; do not expand them or add a new theory.",
                ]
            )
        if additional_instruction:
            lines.append(f"- Additional user instruction (style only, no new facts): {additional_instruction}")
        lines.append("")
        lines.append("Sections (JSON keys are the section_key values):")
        for section in sections:
            lines.append(f"### section_key: {section['key']} — {section['heading']}")
            lines.append(section.get("body") or "")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _citation_tokens(sections: List[Dict[str, str]]) -> set[str]:
        tokens: set[str] = set()
        for section in sections:
            for match in re.finditer(r"\[S\d+:[^\]]+\]", section.get("body") or ""):
                tokens.add(match.group(0))
        return tokens

    def _parse_sections(self, raw: str) -> Dict[str, str]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return {str(key): str(value) for key, value in parsed.items() if isinstance(value, (str, int, float))}
        raise ValueError("LLM draft response is not a parseable JSON object of sections.")

    @staticmethod
    def _reconcile(candidate: str, deterministic_body: str) -> Optional[str]:
        """Keep the rewrite only if it preserves the grounding tokens of the source body.

        The rewrite must contain every citation token and every [Evidence required]
        marker the deterministic body had, and must not add citation tokens that
        were not present. This is belt-and-braces; the validator is the backstop.
        """
        candidate = (candidate or "").strip()
        if not candidate:
            return None
        det_tokens = set(re.findall(r"\[S\d+:[^\]]+\]", deterministic_body))
        cand_tokens = set(re.findall(r"\[S\d+:[^\]]+\]", candidate))
        if cand_tokens - det_tokens:
            return None  # rewrite invented a citation token — reject, keep deterministic
        if det_tokens and not det_tokens.issubset(cand_tokens):
            return None  # rewrite dropped grounded citations — reject
        det_markers = deterministic_body.count("[Evidence required]")
        if det_markers and candidate.count("[Evidence required]") < det_markers:
            return None  # rewrite silently dropped a missing-evidence marker
        return candidate


__all__ = ["LLMDraftGenerator", "LLM_DRAFT_PROMPT_VERSION", "resolve_draft_model"]
