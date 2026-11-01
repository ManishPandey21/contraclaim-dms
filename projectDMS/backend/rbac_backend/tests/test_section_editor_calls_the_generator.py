"""Scoped section editing must actually reach the model.

`ScopedSectionEditor.edit` called `LLMGenerator.generate(..., strict=True)`.
`generate` has no `strict` parameter - it never has in this tree, and this is
the only call site that passes one - so every edit raised TypeError. The method
catches `Exception`, keeps the original text and appends a warning, so the
feature reported itself as degraded on every single section instead of failing
loudly, and no test noticed because none of them ran the editor.

mypy found it as `call-arg` the moment the hook's file scope was repaired.

These tests pin the two halves of the contract: an edit that succeeds replaces
the section, and an edit that genuinely fails still falls back to the source
text with a warning.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.letter_drafting.section_editor import ScopedSectionEditor


class _StubGenerator:
    """Accepts exactly the signature LLMGenerator.generate really has."""

    def __init__(self, reply: str = "Rewritten section text.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def generate(self, prompt: str, max_tokens: int = 512, model=None) -> str:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "model": model})
        return self.reply


class _FailingGenerator:
    async def generate(self, prompt: str, max_tokens: int = 512, model=None) -> str:
        raise RuntimeError("model unavailable")


@pytest.mark.asyncio
async def test_a_successful_edit_replaces_the_section():
    editor = ScopedSectionEditor()
    stub = _StubGenerator()
    editor.generator = stub

    edited, warnings = await editor.edit({1: "Original section text."}, "polish")

    assert stub.calls, "the editor never called the generator"
    assert edited[1] == "Rewritten section text."
    assert warnings == []


@pytest.mark.asyncio
async def test_a_failing_edit_keeps_the_source_text_and_warns():
    editor = ScopedSectionEditor()
    editor.generator = _FailingGenerator()

    edited, warnings = await editor.edit({2: "Original section text."}, "polish")

    assert edited[2] == "Original section text."
    assert len(warnings) == 1
    assert "section_edit_llm[2]" in warnings[0]
