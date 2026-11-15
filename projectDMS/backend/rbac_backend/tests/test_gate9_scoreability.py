"""Structural guards for Gate 9's scoreability semantics.

The question these guards settle, once, so it does not have to be re-litigated:
**may an individual Gate 9 bullet earn readiness points before the other gates
are complete?**

The Launch Gates preamble says "Production promotion is blocked until all gates
are checked". Read as an entry condition on Gate 9 it would make Gate 9's own
bullets unscoreable until every other gate is full - and the route to the 85
target runs through two of them, so the difference is not academic.

Read from the two sources that actually decide it:

* the sentence is scoped to **promotion**, not to scoring or to checking a box;
* `scripts/production_readiness_score.py` implements no entry condition, no gate
  ordering and no cross-gate blocking. It counts `- [x]` per gate section and
  weights the ratio.

So the answer is yes, and `test_the_scorer_applies_no_entry_conditions` pins it
against the scorer rather than against a reading of it. If a later phase decides
Gate 9 *should* be entry-gated, that test fails and the change has to be made in
the scorer and the prose together, which is the point.

**Bullet 5 is the exception, and it is a real defect if left unguarded.** It
asserts the readiness figure itself. Reproduced in R-A8T: from raw 84.000 - the
value the shortest projected route reaches - ticking bullet 5 alone yields raw
85.333 and verdict Ready. The bullet that asserts the threshold is the bullet
that crosses it. `test_gate9_bullet_5_is_not_self_fulfilling` refuses that, and
`test_the_self_fulfilling_tick_is_caught` is its negative control: without one,
a guard that checked nothing would pass just as quietly.

Every validator here is a pure function over document text and is exercised
against a synthetic document that breaks exactly one rule.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

TESTS = Path(__file__).resolve().parent
RBAC_BACKEND = TESTS.parent
PROJECT = RBAC_BACKEND.parents[1]
GATE_FILE = PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"
SCORE_SCRIPT = PROJECT / "scripts" / "production_readiness_score.py"

GATE_9_HEADING = "### Gate 9: Final Production Readiness Review"

BULLET_RE = re.compile(r"^- \[([ xX])\] (.+)$")


# --------------------------------------------------------------------------- #
# Loading the scorer, and asking it questions
# --------------------------------------------------------------------------- #


def _scorer():
    spec = importlib.util.spec_from_file_location("_gate9_readiness_score", SCORE_SCRIPT)
    assert spec and spec.loader, f"cannot load {SCORE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_text(text: str, tmp_path: Path, name: str = "gate.md") -> Dict:
    """Run the real scorer over an arbitrary gate document."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return _scorer().calculate(path)


@pytest.fixture(scope="module")
def gate_text() -> str:
    return GATE_FILE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Section and bullet parsing
# --------------------------------------------------------------------------- #


def gate_section(text: str, heading: str) -> str:
    """The lines under `heading` up to the next `### ` or `## ` heading."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:  # pragma: no cover - asserted by the callers
        raise AssertionError(f"heading not found: {heading}")
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("### ") or stripped.startswith("## "):
            end = idx
            break
    return "\n".join(lines[start + 1 : end])


def bullets(section: str) -> List[Tuple[bool, str]]:
    found = []
    for line in section.splitlines():
        match = BULLET_RE.match(line.strip())
        if match:
            found.append((match.group(1).lower() == "x", match.group(2)))
    return found


#: The score bullet, recognised by what it *means* rather than by its position.
#: Renaming it out of this guard would take deleting both words.
SCORE_BULLET_RE = re.compile(r"readiness score.*\b(\d+)\b|score target is (\d+)", re.I)


def score_bullet(section: str) -> Tuple[bool, str]:
    matches = [entry for entry in bullets(section) if SCORE_BULLET_RE.search(entry[1])]
    assert len(matches) == 1, (
        "Gate 9 must contain exactly one bullet asserting the readiness-score "
        f"threshold; found {len(matches)}: {[m[1] for m in matches]}"
    )
    return matches[0]


def with_score_bullet_untucked(text: str) -> str:
    """The document as it reads with Gate 9's score bullet forced unchecked.

    This is the only honest denominator for the question "is the score already
    at target?", because the bullet being tested is the one that asserts it.
    """
    checked, bullet = score_bullet(gate_section(text, GATE_9_HEADING))
    if not checked:
        return text
    ticked = f"- [x] {bullet}"
    assert text.count(ticked) == 1, f"score bullet is not uniquely locatable: {ticked}"
    return text.replace(ticked, f"- [ ] {bullet}", 1)


# --------------------------------------------------------------------------- #
# 1. What the scorer actually does
# --------------------------------------------------------------------------- #

SYNTHETIC_GATES = """# Synthetic

## Launch Gates

Production promotion is blocked until all gates are checked.

### Gate 1: CI And Local Test Baseline

- [ ] one.
- [ ] two.

### Gate 3: Browser E2E Coverage

- [ ] one.

### Gate 9: Final Production Readiness Review

- [ ] Critical blockers closed.
- [{smoke}] Staging smoke test passes after deploy.
- [ ] Release owner signs off.

## Something Else
"""


def test_the_scorer_applies_no_entry_conditions(tmp_path: Path) -> None:
    """A Gate 9 bullet scores with every other gate empty.

    This is the affirmative half of the R-A8T audit, pinned against the scorer
    instead of against a reading of the preamble. Gate 9 bullets 3 and 4 are
    staging measurements with their own evidence; they are legitimate readiness
    points before Gate 9 is signed off, and the route to 85 depends on that
    being true.

    If a later phase decides Gate 9 must be entry-gated, this test goes RED and
    the scorer and the prose have to move together.
    """
    empty = score_text(SYNTHETIC_GATES.format(smoke=" "), tmp_path, "empty.md")
    one = score_text(SYNTHETIC_GATES.format(smoke="x"), tmp_path, "one.md")

    gate9_empty = next(g for g in empty["gate_scores"] if g["gate"].startswith("Gate 9"))
    gate9_one = next(g for g in one["gate_scores"] if g["gate"].startswith("Gate 9"))

    assert gate9_empty["points"] == 0.0
    assert gate9_one["points"] > 0.0, (
        "a Gate 9 bullet earned nothing while the other gates were empty; the "
        "scorer has acquired an entry condition that no gate prose states"
    )
    assert one["raw_score"] > empty["raw_score"]


def test_the_scorer_has_no_cross_gate_conditioning_in_its_source() -> None:
    """The complement of the behavioural test: no ordering logic to go stale.

    `calculate` iterates the parsed gate sections and multiplies weight by
    checked/total. Any dependency between gates would have to appear here.
    """
    source = SCORE_SCRIPT.read_text(encoding="utf-8")
    assert "GATE_WEIGHTS.get(gate[" in source
    assert 'ratio = (gate["checked"] / gate["total"]) if gate["total"] else 0.0' in source
    for forbidden in ("prerequisite", "depends_on", "entry_condition", "blocked_by"):
        assert forbidden not in source, (
            f"the scorer now references {forbidden!r}; if Gate 9 has become "
            "entry-gated, the gate document's scoreability note must say so too"
        )


def test_the_blocking_sentence_is_scoped_to_promotion(gate_text: str) -> None:
    """The sentence the whole question turns on. It is about promotion.

    Checked two ways: the preamble under `## Launch Gates` still carries it as
    its own statement, and no occurrence anywhere in the document has had its
    subject changed to something other than production promotion. The second
    half is what would catch "Gate 9 is blocked until all gates are checked"
    being introduced beside it - prose the scorer does not implement.
    """
    lines = [line.strip() for line in gate_text.splitlines()]
    occurrences = [line for line in lines if "blocked until all gates are checked" in line]
    assert occurrences, "the Launch Gates blocking sentence is gone from the document"

    assert "Production promotion is blocked until all gates are checked." in lines, (
        "the preamble no longer states the blocking sentence on its own line"
    )
    for line in occurrences:
        assert "production promotion is blocked until all gates are checked" in line.lower(), (
            "the blocking sentence has been rescoped away from production "
            f"promotion, which changes what Gate 9 bullets may earn: {line!r}"
        )


def test_the_document_states_the_scoreability_rule(gate_text: str) -> None:
    """Otherwise the scorer's semantics live only in a receipt nobody clones."""
    section = gate_section(gate_text, GATE_9_HEADING)
    assert "Gate 9 scoreability" in section, (
        "Gate 9 no longer states how its bullets interact with the preamble's "
        "blocking sentence; that question cost a phase to settle once"
    )
    assert "self-referential" in section
    assert "test_gate9_scoreability.py" in section, (
        "the note must name the guard that enforces it, or the two drift apart"
    )


# --------------------------------------------------------------------------- #
# 2. Bullet 5 must not be self-fulfilling
# --------------------------------------------------------------------------- #


def test_gate9_bullet_5_is_not_self_fulfilling(gate_text: str, tmp_path: Path) -> None:
    """The score bullet may only be ticked once the score without it is at target.

    Reproduced in R-A8T against the real document: from raw 84.000 - the value
    the shortest projected route reaches - ticking this bullet alone produced
    raw 85.333 and verdict Ready. The assertion would have been made true by
    asserting it.
    """
    checked, bullet = score_bullet(gate_section(gate_text, GATE_9_HEADING))
    if not checked:
        pytest.skip("Gate 9's score bullet is not ticked; nothing to certify yet")

    without = score_text(with_score_bullet_untucked(gate_text), tmp_path, "without.md")
    target = without["target"]
    assert without["score"] >= target, (
        f"Gate 9's score bullet ({bullet!r}) is ticked, but with that bullet "
        f"excluded the score is {without['score']}/100 against a target of "
        f"{target}. Counting it towards reaching the threshold it asserts is "
        "circular - earn the points elsewhere first"
    )


def test_the_self_fulfilling_tick_is_caught(tmp_path: Path) -> None:
    """Negative control. Without it the guard above passes by checking nothing.

    A document engineered to sit one bullet below target: ticking the score
    bullet is what carries it over, which is exactly the shape R-A8T reproduced.
    """
    below = """# Synthetic

## Launch Gates

### Gate 1: CI And Local Test Baseline

- [x] one.
- [x] two.
- [x] three.
- [x] four.
- [ ] five.

### Gate 9: Final Production Readiness Review

- [ ] Critical blockers closed.
- [{score}] Readiness score target is 85 or higher.

## Something Else
"""
    unticked = below.format(score=" ")
    ticked = below.format(score="x")

    without = score_text(unticked, tmp_path, "below.md")
    with_it = score_text(ticked, tmp_path, "above.md")

    assert without["score"] < without["target"], (
        "this control is anchored on the document sitting below target with the "
        "score bullet unticked; re-anchor it rather than deleting it"
    )
    assert with_it["score"] > without["score"], (
        "the scorer no longer counts the score bullet, so this control proves "
        "nothing; re-derive the guard against the new behaviour"
    )
    # and the guard's own rule rejects it
    assert score_text(with_score_bullet_untucked(ticked), tmp_path, "x.md")["score"] < (
        with_it["target"]
    )


def test_the_score_bullet_names_the_target_the_scorer_uses(gate_text: str) -> None:
    """A bullet asserting a threshold the scorer does not use asserts nothing."""
    _, bullet = score_bullet(gate_section(gate_text, GATE_9_HEADING))
    target = _scorer().calculate(GATE_FILE)["target"]
    assert str(target) in bullet, (
        f"Gate 9's score bullet reads {bullet!r} but the scorer's target is {target}"
    )


def test_gate9_still_has_six_scored_bullets(gate_text: str) -> None:
    """The fix for the circularity must not be to delete the circular bullet.

    Gate 9 weighs 8. At six bullets each is 1.333; at five each is 1.600, so
    removing bullet 5 would hand every remaining Gate 9 bullet 0.267 points it
    had not earned. A denominator change that pays points is the failure mode
    this programme has refused twice.
    """
    scored = bullets(gate_section(gate_text, GATE_9_HEADING))
    assert len(scored) == 6, (
        f"Gate 9 has {len(scored)} scored bullets; it had 6. If a bullet was "
        "genuinely withdrawn, say so here and price the denominator change"
    )


def test_every_gate_checked_is_a_score_of_one_hundred(gate_text: str, tmp_path: Path) -> None:
    """What the preamble's promotion condition actually costs, priced.

    "Production promotion is blocked until all gates are checked" and "readiness
    score target is 85 or higher" are not the same bar, and the gap is easy to
    misread in the optimistic direction. The gate weights sum to 100 and each
    gate scores its own checked/total, so *all gates checked* is exactly
    100/100. The 85 target is therefore a milestone on the way to promotion, and
    never the binding constraint at promotion time.

    Kept as a test rather than a sentence because it is derived from the weight
    table, and a future re-weighting that broke it would otherwise be silent.
    """
    lines = gate_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "## Launch Gates")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")),
        len(lines),
    )
    ticked = list(lines)
    for idx in range(start, end):
        if BULLET_RE.match(ticked[idx].strip()):
            ticked[idx] = ticked[idx].replace("- [ ]", "- [x]", 1)

    result = score_text("\n".join(ticked), tmp_path, "all.md")
    assert result["score"] == 100, (
        "with every launch-gate bullet checked the scorer reports "
        f"{result['score']}/100, not 100. The gate weights no longer sum to 100, "
        "so 'all gates checked' and the score target have come apart"
    )
    assert result["raw_score"] == 100.0


# --------------------------------------------------------------------------- #
# 3. Bullet 1 inherits the constraint through the blocker register
# --------------------------------------------------------------------------- #

BLOCKER_ROW = re.compile(r"^\|\s*(P\d-\d+)\s*\|\s*([^|]+?)\s*\|")


def open_blockers(text: str) -> List[str]:
    return [
        match.group(1)
        for match in (BLOCKER_ROW.match(line.strip()) for line in text.splitlines())
        if match and match.group(2).strip().lower() == "open"
    ]


def test_gate9_bullet_1_is_not_ticked_while_a_blocker_is_open(gate_text: str) -> None:
    """"Critical blockers closed" is checkable against the register, so check it.

    P0-008's required resolution names the readiness rerun and the owner
    sign-off, so this bullet is downstream of bullet 5 as well as of the
    register. Ticking it early would import bullet 5's circularity by the back
    door.
    """
    scored = bullets(gate_section(gate_text, GATE_9_HEADING))
    blockers_closed = [
        entry for entry in scored if "critical blocker" in entry[1].lower()
    ]
    assert len(blockers_closed) == 1, "Gate 9 no longer has a critical-blockers bullet"
    if not blockers_closed[0][0]:
        return
    still_open = open_blockers(gate_text)
    assert not still_open, (
        "Gate 9 bullet 1 says critical blockers are closed, but the Current "
        f"Critical Blocker Register still lists these as Open: {still_open}"
    )


#: A register carrying every status the real one has ever used, so the classifier
#: can be exercised independently of what the real register happens to say today.
SYNTHETIC_REGISTER = """## Current Critical Blocker Register

| ID | Status | Blocker | Evidence | Required resolution |
| --- | --- | --- | --- | --- |
| P0-001 | Resolved | something | e | r |
| P0-002 | Open | something else | e | r |
| P0-003 | Partially mitigated | a third thing | e | r |
| P0-004 | Accepted debt - Owner approved for initial cutover | a fourth thing | e | r |
| P0-005 | Resolved (code); staging proof captured | a fifth thing | e | r |
"""

#: The same register with every row disposed. A legitimate end state, not an error.
SYNTHETIC_REGISTER_FULLY_DISPOSED = SYNTHETIC_REGISTER.replace(
    "| P0-002 | Open |",
    "| P0-002 | Accepted debt - Owner approved for initial cutover |",
)

#: A row whose status cell was emptied. The one way to defeat the Open rule by
#: editing the register rather than by resolving anything.
SYNTHETIC_REGISTER_BLANK_STATUS = SYNTHETIC_REGISTER.replace(
    "| P0-002 | Open |", "| P0-002 |  |"
)


def blank_status_rows(text: str) -> List[str]:
    """Blocker rows whose status cell is empty or a placeholder dash."""
    found = []
    for line in text.splitlines():
        match = BLOCKER_ROW.match(line.strip())
        if match and match.group(2).strip() in {"", "-", "--", "—"}:
            found.append(match.group(1))
    return found


def test_the_open_blocker_rule_reads_the_real_register(gate_text: str) -> None:
    """Anti-vacuity: the register exists, is parsed, and is classified correctly.

    A guard pointed at a table it cannot parse is a guard that always passes.

    **Re-anchored 2026-09-20 (R-A9G-0) under narrowly scoped owner
    authorisation, exactly as the previous version of this docstring
    instructed.** It proved non-vacuity by asserting that the real register
    still listed *something* as Open - true for as long as P0-002 and P0-008
    were. The release owner then disposed P0-002 as dated accepted debt
    (follow-up 2026-10-15) and P0-008 closed on the Gate 9 bullet 6 sign-off, so
    the real register legitimately holds **zero** Open rows and that assertion
    would fire for the one reason it was never meant to: success. Keeping it
    would make this guard contradict the gate state it exists to validate.

    Nothing is weakened. What moved is only *where* parser non-vacuity is
    proven: onto synthetic registers the classifier has to get right, which no
    operational state can invalidate. The real register is still parsed, still
    required to have rows, and now additionally required to carry a status in
    every row - so emptying a status cell to dodge the Open rule fails here
    instead of passing silently. The production rule itself is untouched and
    lives next door in
    `test_gate9_bullet_1_is_not_ticked_while_a_blocker_is_open`: Gate 9 bullet 1
    must not be ticked while any real row parses as Open.
    """
    rows = [
        (match.group(1), match.group(2).strip())
        for match in (BLOCKER_ROW.match(line.strip()) for line in gate_text.splitlines())
        if match
    ]
    assert len(rows) >= 5, f"the blocker register parsed to {len(rows)} rows"

    blank = blank_status_rows(gate_text)
    assert not blank, (
        "these real blocker rows carry no status, so the Open rule cannot "
        "classify them and Gate 9 bullet 1 would pass by default. A row is "
        f"disposed by recording a disposition, never by emptying the cell: {blank}"
    )

    # --- the classifier, exercised against known input ---------------------- #
    classified = open_blockers(SYNTHETIC_REGISTER)
    assert classified == ["P0-002"], (
        "the Open-row classifier no longer reads a register it is handed: it "
        f"returned {classified} for a table with exactly one Open row. Gate 9 "
        "bullet 1's guard is only ever as good as this"
    )
    # Each disposition, named, so a regression says which one broke.
    assert "P0-001" not in classified, "a Resolved row is being read as Open"
    assert "P0-003" not in classified, "a Partially mitigated row is being read as Open"
    assert "P0-004" not in classified, (
        "an accepted-debt disposition is being read as Open; formally disposing "
        "a row and leaving it open are different things, and the guard has to "
        "tell them apart"
    )
    assert "P0-005" not in classified, (
        "a qualified Resolved status is being read as Open; the rule matches the "
        "whole status cell, not a substring of it"
    )

    # --- zero Open rows is a legitimate state, not a parse failure ----------- #
    assert open_blockers(SYNTHETIC_REGISTER_FULLY_DISPOSED) == [], (
        "a fully disposed register must classify as zero Open rows. If this "
        "returns anything, the release can never legitimately close Gate 9 "
        "bullet 1 and the guard has become unsatisfiable"
    )

    # --- and the blank-status detector is not vacuous either ----------------- #
    assert blank_status_rows(SYNTHETIC_REGISTER_BLANK_STATUS) == ["P0-002"], (
        "the blank-status detector no longer sees an emptied status cell, so "
        "the check against the real register above proves nothing"
    )
    assert blank_status_rows(SYNTHETIC_REGISTER) == [], (
        "the blank-status detector fires on a register where every row has a "
        "status; it would fail the real document for no reason"
    )
