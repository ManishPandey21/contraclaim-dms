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

RBAC_BACKEND = Path(__file__).resolve().parents[1]
PROJECT = RBAC_BACKEND.parents[1]
REQUIREMENTS = RBAC_BACKEND / "requirements.txt"

#: The exact functions named in the advisory: TransitionParser.train/.parse,
#: AveragedPerceptron.save/.load, PerceptronTagger.save_to_json and
#: save_maxent_params.
VULNERABLE_APIS = (
    "TransitionParser",
    "AveragedPerceptron",
    "PerceptronTagger",
    "save_to_json",
    "save_maxent_params",
)

IMPORT_RX = re.compile(r"^\s*(?:import\s+nltk|from\s+nltk[\s.])", re.M)

#: Trees that ship. Only `tests/` is excluded, because this file names the APIs
#: deliberately. `manual/` is NOT excluded: those operator scripts are run by
#: hand against real environments, so NLTK reaching them is exactly as
#: interesting as NLTK reaching a router.
PRODUCTION_TREES = (
    RBAC_BACKEND,
    PROJECT / "services",
    PROJECT / "scripts",
)
EXCLUDED_PARTS = {"tests", "__pycache__", ".venv", "node_modules"}

#: Every backend requirements file, not only the one CI audits. A direct pin in
#: any of them is a statement that somebody intends to use NLTK.
BACKEND_REQUIREMENTS = (
    RBAC_BACKEND / "requirements.txt",
    RBAC_BACKEND.parent / "requirements.txt",
)


def _production_sources() -> list[Path]:
    files: list[Path] = []
    for tree in PRODUCTION_TREES:
        # Never skip a missing tree. `BACKEND / "rbac_backend"` used to resolve
        # to backend/rbac_backend/rbac_backend, which does not exist, so this
        # loop quietly scanned only projectDMS/services and the whole backend
        # went unchecked: synthetic `import nltk` and `PerceptronTagger` lines
        # planted in a production service did not fail a single case.
        assert tree.is_dir(), f"production tree {tree} does not exist"
        found = [
            path
            for path in tree.rglob("*.py")
            if not (EXCLUDED_PARTS & set(path.parts))
        ]
        assert found, f"production tree {tree} matched no Python file"
        files.extend(found)
    return files


def test_the_scan_actually_covers_the_backend_application() -> None:
    """The guard is worthless if its file set is empty or wrong.

    Every assertion below is a search over `_production_sources()`; a search
    over nothing passes. This pins that the set is real and includes the tree
    that actually serves requests.
    """
    sources = _production_sources()

    # The backend package alone is several hundred modules; anything near zero
    # means the tree list is wrong again rather than that the code shrank.
    assert len(sources) > 100, f"only {len(sources)} production files found"
    served = [p for p in sources if "rbac_backend" in p.parts]
    assert served, "the rbac_backend application tree is not being scanned"
    assert any(p.name == "llamaindex_service.py" for p in served), (
        "the service that imports llama_index - the one package that pulls in "
        "NLTK - is not in the scanned set"
    )


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


@pytest.mark.parametrize("requirements", BACKEND_REQUIREMENTS, ids=lambda p: p.parent.name)
def test_nltk_is_not_a_direct_backend_dependency(requirements: Path) -> None:
    """It must stay transitive, so a direct pin is a deliberate, reviewed act.

    The legacy `projectDMS/requirements.txt` does pin `nltk==3.9.2`. CI does not
    audit that file and it is not installed by the backend image, so it is
    recorded in the acceptance document rather than asserted here - but neither
    backend requirements file may grow a pin.
    """
    assert requirements.is_file(), f"{requirements} is missing"
    pinned = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*nltk\b", line, re.I)
    ]

    assert not pinned, (
        f"NLTK is now pinned directly in {requirements}: {pinned}. It "
        f"reaches this tree only through llama-index-core's `nltk>=3.9.3`; a "
        f"direct pin means somebody intends to use it, which the no-fix "
        f"acceptance does not cover."
    )
