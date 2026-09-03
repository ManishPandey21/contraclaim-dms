"""NLTK's unfixed advisory stays unreachable from this codebase.

PYSEC-2026-3740 / CVE-2026-81726 / GHSA-8mgp-746c-j5xp is a path-sandbox bypass
in NLTK's model-artifact APIs — `TransitionParser.train`, `TransitionParser.parse`,
`AveragedPerceptron.save`, `AveragedPerceptron.load`, `PerceptronTagger.save_to_json`
and `save_maxent_params` — which use raw `open()` on caller-controlled paths even
when `pathsec` enforcement is on. **No fixed release exists.**

It reaches this repository only because `llama-index-core` requires
`nltk>=3.9.3`; nothing here pins or imports NLTK, and llama-index uses it purely
for tokenization. The advisory's own precondition — an application that enables
`pathsec` enforcement and lets untrusted workflows choose model import or export
paths — is not met, because no NLTK model path exists to choose.

That is a statement about today's code, and code changes. These tests make it a
standing property: if production ever imports NLTK, touches one of the six
vulnerable APIs, or pins NLTK directly, this fails and the no-fix acceptance has
to be revisited rather than silently outlived.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parents[1]
REQUIREMENTS = BACKEND / "requirements.txt"

#: The exact functions named in the advisory.
VULNERABLE_APIS = (
    "TransitionParser",
    "AveragedPerceptron",
    "PerceptronTagger",
    "save_maxent_params",
)

IMPORT_RX = re.compile(r"^\s*(?:import\s+nltk|from\s+nltk[\s.])", re.M)

#: Trees that ship. `tests/` is excluded because this file names the APIs, and
#: `manual/` holds operator scripts that are not part of the served application.
PRODUCTION_TREES = (
    BACKEND / "rbac_backend",
    PROJECT / "services",
)
EXCLUDED_PARTS = {"tests", "manual", "__pycache__", ".venv", "node_modules"}


def _production_sources() -> list[Path]:
    files: list[Path] = []
    for tree in PRODUCTION_TREES:
        if not tree.is_dir():
            continue
        for path in tree.rglob("*.py"):
            if EXCLUDED_PARTS & set(path.parts):
                continue
            files.append(path)
    return files


def test_production_code_does_not_import_nltk() -> None:
    importers = [
        str(p.relative_to(PROJECT))
        for p in _production_sources()
        if IMPORT_RX.search(p.read_text(encoding="utf-8", errors="replace"))
    ]

    assert not importers, (
        f"NLTK is imported by production code: {importers}. Its advisory "
        f"PYSEC-2026-3740 has no fixed release, and the acceptance for it rests "
        f"on this codebase never calling NLTK at all."
    )


@pytest.mark.parametrize("api", VULNERABLE_APIS)
def test_no_production_reference_to_the_vulnerable_apis(api: str) -> None:
    users = [
        str(p.relative_to(PROJECT))
        for p in _production_sources()
        if api in p.read_text(encoding="utf-8", errors="replace")
    ]

    assert not users, (
        f"{api} is named in {users}. It is one of the model-artifact APIs "
        f"PYSEC-2026-3740 leaves unpatched; using it makes an unfixed sandbox "
        f"bypass reachable."
    )


def test_nltk_is_not_a_direct_production_dependency() -> None:
    """It must stay transitive, so a direct pin is a deliberate, reviewed act."""
    pinned = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*nltk\b", line, re.I)
    ]

    assert not pinned, (
        f"NLTK is now pinned directly in {REQUIREMENTS.name}: {pinned}. It "
        f"reaches this tree only through llama-index-core's `nltk>=3.9.3`; a "
        f"direct pin means somebody intends to use it, which the no-fix "
        f"acceptance does not cover."
    )
