"""Structural guards for the Gate 2 section of the release-gate document.

Gate 2 required a "FalkorDB graph/vector round trip". The vector half named a
capability the deployed engine does not have: `FalkorDBVectorService._create_index`
issues RediSearch `FT.CREATE`, and the production pin `falkordb/falkordb:v4.0.8`
loads only the `graph` module, so the command does not exist. A gate criterion that
cannot be satisfied by the supported architecture is not a high bar, it is an
unfalsifiable one - it can never go green and never tells anyone anything.

The amendment withdrew the vector half and kept the graph half. These guards exist so
that the correction cannot rot in either direction:

* the obsolete criterion cannot silently return;
* the replacement criterion cannot silently disappear;
* Gate 2 cannot stop being a staging gate;
* a bullet cannot be ticked without naming executable evidence;
* and the architectural fact the amendment rests on - that nothing in production calls
  the FalkorDB vector path - is re-checked on every run, so the day somebody wires it
  back up, this file fails and the requirement has to be reinstated.

The validators are pure functions over text, and each is tested against a synthetic
section that breaks exactly one rule. Asserting only against the real document would
pass just as happily if the validator checked nothing at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, List

import pytest

TESTS = Path(__file__).resolve().parent
RBAC_BACKEND = TESTS.parent
PROJECT = RBAC_BACKEND.parents[1]
GATE_FILE = PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"

#: Trees that ship. `tests/` is excluded because this file and the integration
#: module name the withdrawn service deliberately.
PRODUCTION_TREES = (RBAC_BACKEND, PROJECT / "services", PROJECT / "scripts")
EXCLUDED_PARTS = {"tests", "__pycache__", ".venv", "node_modules"}

#: The one module that may still import the dead vector service. It is itself
#: uncalled - `test_data_sync_has_no_caller` is what stops that being an excuse.
DEAD_VECTOR_ENTRYPOINT = RBAC_BACKEND / "services" / "data_sync.py"

VECTOR_SERVICE = "FalkorDBVectorService"


# --------------------------------------------------------------------------- #
# Gate section parsing
# --------------------------------------------------------------------------- #

BULLET_RE = re.compile(r"^- \[([ xX])\] (.+)$")


def gate_section(text: str, heading: str) -> str:
    """The lines under `heading` up to the next `### ` heading."""
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        raise AssertionError(f"{heading!r} is missing from {GATE_FILE.name}")

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("### "):
            end = idx
            break
    return "\n".join(lines[start:end])


def bullets(section: str) -> List[tuple[bool, str]]:
    """(checked, text) for each scored checkbox bullet in a gate section.

    This mirrors the regex in `scripts/production_readiness_score.py`, so a line
    this function ignores is a line that scores nothing.
    """
    out = []
    for line in section.splitlines():
        match = BULLET_RE.match(line.strip())
        if match:
            out.append((match.group(1).lower() == "x", match.group(2)))
    return out


# --------------------------------------------------------------------------- #
# The validator - pure, so it can be run against synthetic sections
# --------------------------------------------------------------------------- #

def validate_gate2(section: str, path_exists=None) -> List[str]:
    """Return one message per violated rule. Empty means the section is sound."""
    if path_exists is None:
        path_exists = lambda rel: (PROJECT / rel).exists()  # noqa: E731

    errors: List[str] = []
    entries = bullets(section)
    texts = [text for _, text in entries]

    def mentions(text: str, *needles: str) -> bool:
        low = text.lower()
        return all(needle in low for needle in needles)

    # 1. The obsolete criterion must not come back, in any wording that pairs
    #    FalkorDB with a vector claim.
    for text in texts:
        if mentions(text, "falkor", "vector"):
            errors.append(
                "obsolete criterion returned: a Gate 2 bullet requires a FalkorDB "
                f"vector capability, which the deployed engine does not provide: {text!r}"
            )

    # 2. The replacement must be present.
    if not any(mentions(t, "falkor", "graph") for t in texts):
        errors.append("replacement criterion missing: no bullet requires a FalkorDB graph round trip")

    # 3. The vector round trip must still be required - of Qdrant.
    if not any(mentions(t, "qdrant", "vector") for t in texts):
        errors.append("vector coverage lost: no bullet requires a Qdrant vector round trip")

    # 4. Gate 2 must remain a staging gate.
    if not any(mentions(t, "staging") and "run_external_integration_tests" in t.lower() for t in texts):
        errors.append(
            "staging requirement lost: no bullet requires the live run with "
            "RUN_EXTERNAL_INTEGRATION_TESTS=1 against staging"
        )

    # 5. A ticked bullet must name executable evidence, and that evidence must exist.
    for checked, text in entries:
        if not checked:
            continue
        match = re.search(r"evidence:\s*(\S+)", text)
        if match is None:
            errors.append(f"bullet is checked with no evidence reference: {text!r}")
            continue
        reference = match.group(1).strip("`.,;")
        if "/" in reference and not path_exists(reference):
            errors.append(f"evidence reference does not exist in the repository: {reference!r}")

    return errors


# --------------------------------------------------------------------------- #
# The real document
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def gate_text() -> str:
    return GATE_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gate2(gate_text: str) -> str:
    return gate_section(gate_text, "### Gate 2: Live Integration Baseline")


def test_gate2_specification_is_sound(gate2: str) -> None:
    assert validate_gate2(gate2) == []


def test_gate2_still_has_six_scored_bullets(gate2: str) -> None:
    """The amendment replaced a bullet; it must not have added or removed one.

    The readiness score is `checked / total` per gate, so changing the denominator
    would move the score without any new evidence being captured.
    """
    assert len(bullets(gate2)) == 6


def test_the_superseded_wording_is_preserved(gate2: str) -> None:
    """Supersession keeps the history; deletion would hide that a bar was lowered."""
    assert "Verify FalkorDB graph/vector round trip" in gate2, (
        "the old requirement's verbatim wording must remain recorded under the "
        "supersession heading"
    )
    assert "SUPERSEDED" in gate2
    assert "APPROVED FOR `release/contraclaim-rc1`" in gate2, (
        "an amendment without a recorded owner approval is an undocumented change"
    )


def test_local_preconditions_are_not_scored(gate2: str) -> None:
    """The local block must not smuggle in checkbox bullets.

    A `- [ ]` line inside the local-precondition section would be counted by the
    scoring script exactly like a staging requirement.
    """
    marker = "#### Gate 2 local preconditions"
    assert marker in gate2
    local_block = gate2.split(marker, 1)[1]
    assert bullets(local_block) == [], (
        "the local-precondition block contains scored checkbox bullets; local runs "
        "must never earn Gate 2 points"
    )


# --------------------------------------------------------------------------- #
# The validator itself - one synthetic breakage per rule
# --------------------------------------------------------------------------- #

SOUND = "\n".join(
    [
        "- [ ] Run live external tests with `RUN_EXTERNAL_INTEGRATION_TESTS=1` against staging.",
        "- [ ] Verify OpenAI document round trip.",
        "- [ ] Verify Qdrant vector round trip.",
        "- [ ] Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only).",
        "- [ ] Verify Redis queue/runtime-state paths.",
        "- [ ] Record all required live integration env vars used for the run.",
    ]
)


def test_the_sound_synthetic_section_passes() -> None:
    """Anchor: the mutations below must fail for their own reason, not by accident."""
    assert validate_gate2(SOUND) == []


@pytest.mark.parametrize(
    "mutation, expected",
    [
        pytest.param(
            SOUND.replace(
                "- [ ] Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only).",
                "- [ ] Verify FalkorDB graph/vector round trip.",
            ),
            "obsolete criterion returned",
            id="obsolete-criterion-restored",
        ),
        pytest.param(
            SOUND.replace(
                "- [ ] Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only).",
                "- [ ] Verify FalkorDB vector index creation.",
            ),
            "obsolete criterion returned",
            id="obsolete-criterion-reworded",
        ),
        pytest.param(
            SOUND.replace(
                "- [ ] Verify FalkorDB graph round trip (`GRAPH.*`, disposable graph namespace only).\n",
                "",
            ),
            "replacement criterion missing",
            id="replacement-deleted",
        ),
        pytest.param(
            SOUND.replace("- [ ] Verify Qdrant vector round trip.\n", ""),
            "vector coverage lost",
            id="qdrant-vector-deleted",
        ),
        pytest.param(
            SOUND.replace(
                "- [ ] Run live external tests with `RUN_EXTERNAL_INTEGRATION_TESTS=1` against staging.",
                "- [ ] Run live external tests locally.",
            ),
            "staging requirement lost",
            id="staging-requirement-dropped",
        ),
        pytest.param(
            SOUND.replace(
                "- [ ] Verify Redis queue/runtime-state paths.",
                "- [x] Verify Redis queue/runtime-state paths.",
            ),
            "checked with no evidence reference",
            id="ticked-without-evidence",
        ),
        pytest.param(
            SOUND.replace(
                "- [ ] Verify Redis queue/runtime-state paths.",
                "- [x] Verify Redis queue/runtime-state paths. evidence: backend/nope/missing_test.py",
            ),
            "evidence reference does not exist",
            id="ticked-with-phantom-evidence",
        ),
    ],
)
def test_each_rule_rejects_its_own_breakage(mutation: str, expected: str) -> None:
    errors = validate_gate2(mutation)
    assert any(expected in error for error in errors), (
        f"expected an error containing {expected!r}; got {errors}"
    )


def test_real_evidence_reference_is_accepted() -> None:
    """The evidence rule must accept a real path, or it would just ban ticking."""
    section = SOUND.replace(
        "- [ ] Verify Redis queue/runtime-state paths.",
        "- [x] Verify Redis queue/runtime-state paths. "
        "evidence: backend/rbac_backend/tests/test_release_gate_specification.py",
    )
    assert validate_gate2(section) == []


# --------------------------------------------------------------------------- #
# The architectural fact the amendment rests on
# --------------------------------------------------------------------------- #

def _production_sources() -> Iterable[Path]:
    seen = set()
    for tree in PRODUCTION_TREES:
        if not tree.is_dir():
            continue
        for path in tree.rglob("*.py"):
            if EXCLUDED_PARTS & set(path.parts):
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


def test_the_production_source_set_is_not_empty() -> None:
    """An earlier guard in this repository scanned a directory that did not exist.

    It passed while checking almost nothing. Prove the file set is real and that it
    includes the backend application before trusting anything derived from it.
    """
    sources = list(_production_sources())
    assert len(sources) > 200, f"production source set is implausibly small: {len(sources)}"
    assert any(p.name == "main.py" and p.parent == RBAC_BACKEND for p in sources), (
        "the backend application entrypoint is not in the scanned set"
    )
    assert DEAD_VECTOR_ENTRYPOINT in sources, "data_sync.py is not in the scanned set"


def test_falkordb_vector_service_has_no_production_caller() -> None:
    """The load-bearing fact: nothing production reaches the withdrawn capability.

    If this fails, the Gate 2 vector requirement must be reinstated - the amendment's
    second criterion ("not required by a live production caller") has stopped holding.
    """
    defining_module = RBAC_BACKEND / "services" / "falkordb_vector_service.py"
    offenders = []
    for path in _production_sources():
        if path in (DEAD_VECTOR_ENTRYPOINT, defining_module):
            continue
        if VECTOR_SERVICE in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(PROJECT)))

    assert not offenders, (
        f"{VECTOR_SERVICE} is referenced by production code: {offenders}. Gate 2's "
        "FalkorDB vector requirement was withdrawn because nothing called it; that "
        "is no longer true, so the requirement must be reinstated."
    )


def test_data_sync_has_no_caller() -> None:
    """`data_sync` is the only importer, and it is itself dead.

    Without this, the exemption above would be a loophole: anything could reach the
    vector service by calling `sync_data`.
    """
    importers = []
    for path in _production_sources():
        if path == DEAD_VECTOR_ENTRYPOINT:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bdata_sync\b|\bsync_data\b", source):
            importers.append(str(path.relative_to(PROJECT)))

    assert not importers, (
        f"data_sync/sync_data is now referenced by {importers}. It was treated as an "
        "unreferenced manual script when Gate 2's FalkorDB vector requirement was "
        "withdrawn."
    )


def test_falkor_graph_service_still_has_production_callers() -> None:
    """The replacement must cover something real.

    A graph round-trip requirement would be as empty as the one it replaced if the
    graph service had no callers either.
    """
    callers = []
    for path in _production_sources():
        if path.name == "falkor_graph_service.py":
            continue
        if "FalkorGraphService" in path.read_text(encoding="utf-8"):
            callers.append(str(path.relative_to(PROJECT)))

    assert len(callers) >= 5, (
        f"expected FalkorGraphService to have several production callers; found {callers}"
    )


# --------------------------------------------------------------------------- #
# The stated score must be the computed score
# --------------------------------------------------------------------------- #

def _computed_score() -> int:
    import importlib.util

    script = PROJECT / "scripts" / "production_readiness_score.py"
    spec = importlib.util.spec_from_file_location("_readiness_score", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.calculate(GATE_FILE)["score"])


def test_the_document_states_the_score_the_script_computes(gate_text: str) -> None:
    """A hardcoded score drifts away from the checkbox state that produces it.

    It had: the document read 40/100 while the script reported 37, three points
    granted to evidence nobody had captured.
    """
    computed = _computed_score()
    stated = re.findall(r"launch-readiness score is \*?\*?(\d+)/100", gate_text)
    stated += re.findall(r"launch-readiness score is \*\*(\d+)/100\*\*", gate_text)
    assert stated, "the document no longer states a launch-readiness score"
    for value in set(stated):
        assert int(value) == computed, (
            f"the document states {value}/100 but "
            f"scripts/production_readiness_score.py computes {computed}/100"
        )


def test_the_vector_service_really_does_need_redisearch() -> None:
    """Read it from the source rather than from the acceptance note.

    The whole amendment rests on this service needing `FT.*`. If it were rewritten to
    use `GRAPH.*` or FalkorDB's own vector sets, the supersession reasoning would be
    stale and this test is where that surfaces.
    """
    source = (RBAC_BACKEND / "services" / "falkordb_vector_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports_redisearch = any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("redis.commands.search")
        for node in ast.walk(tree)
    )
    calls_ft = ".ft(" in source and "create_index" in source

    assert imports_redisearch and calls_ft, (
        "falkordb_vector_service no longer depends on RediSearch. The Gate 2 "
        "supersession reasoning cites FT.CREATE specifically and must be re-derived."
    )
