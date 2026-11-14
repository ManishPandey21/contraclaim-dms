"""Run one skill step.

A step = one skill version applied, for one role, to one target, producing one
structured output. The executor:

- renders the task template, THEN attaches the compiled skill block,
- calls the model in strict mode (a model failure raises, it never returns a
  canned sentence that could be mistaken for content),
- parses and validates the reply against the output schema we own,
- on any failure uses the caller's deterministic fallback and marks the step
  ``degraded`` so the gap reaches a human.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from pydantic import BaseModel, ValidationError

from ...models.letter_bench import OUTPUT_SCHEMAS
from ...models.skill import SkillVersion
from ..letter_drafting.prompts import PromptRegistry, render_prompt
from .compiler import CompiledSkill, attach_skill_block, compile_skill

logger = logging.getLogger(__name__)

LLMCallable = Callable[[str, int], Awaitable[str]]
FALLBACK_SENTINEL = "Answer unavailable"
DEFAULT_SKILL_MODEL = "gpt-4o"


class SkillStepError(RuntimeError):
    pass


@dataclass
class ExecutionResult:
    output: Dict[str, Any]
    degraded: bool
    error: Optional[str]
    model: Optional[str]
    compiled: CompiledSkill
    prompt_version: int


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def step_input_hash(
    *, skill_key: str, skill_hash: str, role: str, overlay_key: str, schema_key: str, inputs: Dict[str, Any]
) -> str:
    """Identity of a step's inputs; equal hashes mean the cached step can be reused."""
    return canonical_hash(
        {
            "skill_key": skill_key,
            "skill_hash": skill_hash,
            "role": role,
            "overlay_key": overlay_key,
            "schema_key": schema_key,
            "inputs": inputs,
        }
    )


def parse_json_object(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SkillStepError("Model reply did not contain a JSON object")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SkillStepError(f"Model reply was not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise SkillStepError("Model reply was not a JSON object")
    return value


def output_keys_text(schema: type[BaseModel]) -> str:
    lines = []
    for name, info in schema.model_fields.items():
        annotation = str(info.annotation).replace("typing.", "")
        lines.append(f"- {name}: {annotation}")
    return "\n".join(lines)


async def _default_llm(prompt: str, max_tokens: int) -> str:
    # Imported lazily: constructing the generator builds an API client.
    from ...config.document_processing_config import DocumentProcessingConfig
    from ...retrieval.generator import LLMGenerator

    generator = LLMGenerator(DocumentProcessingConfig(openai_model=DEFAULT_SKILL_MODEL))
    reply = await generator.generate(prompt, max_tokens=max_tokens, model=DEFAULT_SKILL_MODEL)
    if not reply or reply.startswith(FALLBACK_SENTINEL):
        # LLMGenerator swallows provider errors into a canned sentence. For
        # drafting content that sentence must never be treated as output.
        raise SkillStepError("Language model unavailable")
    return reply


class SkillExecutor:
    def __init__(
        self,
        prompt_registry: PromptRegistry,
        llm: Optional[LLMCallable] = None,
        model_name: str = DEFAULT_SKILL_MODEL,
    ) -> None:
        self.prompt_registry = prompt_registry
        self.llm = llm or _default_llm
        self.model_name = model_name

    async def run(
        self,
        *,
        version: SkillVersion,
        role: str,
        prompt_key: str,
        payload: Dict[str, str],
        fallback: Callable[[], BaseModel],
        schema_key: Optional[str] = None,
        overlay_text: Optional[str] = None,
        max_tokens: int = 1600,
    ) -> ExecutionResult:
        schema_key = schema_key or version.manifest.output_schema_key
        schema = OUTPUT_SCHEMAS.get(schema_key)
        if schema is None:
            raise SkillStepError(f"Unknown output schema {schema_key}")
        compiled = compile_skill(version, role, overlay_text=overlay_text)
        record = await self.prompt_registry.get_enabled(prompt_key)
        payload = {**payload, "role": role, "output_keys": output_keys_text(schema)}
        error: Optional[str] = None
        output: Optional[BaseModel] = None
        try:
            prompt = attach_skill_block(render_prompt(record, payload), compiled)
            raw = await self.llm(prompt, max_tokens)
            output = schema.model_validate(parse_json_object(raw))
        except (SkillStepError, ValidationError, KeyError, IndexError) as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        except Exception as exc:  # provider/network failure
            logger.warning("skill step %s failed: %s", version.skill_key, exc)
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        degraded = output is None
        if output is None:
            output = fallback()
        return ExecutionResult(
            output=output.model_dump(mode="json"),
            degraded=degraded,
            error=error,
            model=None if degraded else self.model_name,
            compiled=compiled,
            prompt_version=record.version,
        )


__all__ = [
    "ExecutionResult",
    "LLMCallable",
    "SkillExecutor",
    "SkillStepError",
    "canonical_hash",
    "parse_json_object",
    "step_input_hash",
]
