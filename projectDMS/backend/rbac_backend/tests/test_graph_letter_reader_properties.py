"""G32 reader-side gate: no production Cypher may READ a Letter property that
the writer no longer maintains.

The original G32 test asserted only what `upsert_letter_with_refs` WRITES. That
is half a gate, and the missing half caused a live cross-tenant leak: the
linked-chain report filtered tenants with
`node.organization_id IS NULL OR node.organization_id = $orgId`, and when G32
stopped writing `organization_id` every node became NULL, so the clause admitted
every tenant's letters. The property removal the passing test certified is
exactly what turned that filter into a tautology.

A filter reading a property nothing writes is not a weak guard - it is no guard.
This test walks production source for `node.<prop>` / `l.<prop>` style reads on
Letter-bearing Cypher and fails on anything outside GLOBAL_LETTER_PROPERTIES.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from rbac_backend.services.falkor_graph_service import GLOBAL_LETTER_PROPERTIES

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Properties that were removed from the shared node by G32. Reading any of them
# means the reader believes in a fact the shared node cannot carry.
REMOVED_LETTER_PROPERTIES = {
    "subject",
    "date",
    "direction",
    "organization_id",
    "project_id",
    "project",
    "code",
}

# Cypher variables bound to a :Letter in production queries.
LETTER_VARIABLES = ("node", "l", "src", "dst", "root", "letter")


#: Cypher keywords that identify a string as a query rather than prose.
CYPHER_KEYWORDS = ("MATCH", "MERGE", "RETURN", "WHERE", "SET ", "ORDER BY")


def _production_files():
    """Every non-test Python file under the backend package.

    `migrations` and `scripts` are NOT skipped. They were, and that is exactly
    where the G32 state migration and the graph maintenance jobs live - the
    code most likely to reach for a property the shared node no longer carries.
    A guard that exempts the places a rule is hardest to follow is not a guard.
    """
    for path in BACKEND_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        yield path


def _cypher_literals(text: str):
    """Yield `(lineno, query_text)` for every Cypher string literal in a module.

    Line-at-a-time matching missed the live regression this file exists to
    catch. A multi-line `RETURN` projection puts each field on its own
    continuation line::

        RETURN DISTINCT
            letter.normCode AS normCode,
            letter.subject AS subject,      <- no Cypher keyword on this line

    so the "does this line contain a Cypher keyword?" filter skipped all five
    removed-property reads in `FalkorGraphService.get_thread`, and the band
    stayed green while the read was live. The literal, not the line, is the
    unit of Cypher.

    f-strings are handled by joining their constant parts, so an interpolated
    depth or filter cannot hide the projection around it.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - a file that cannot parse is no reader
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            joined = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            yield node.lineno, joined


def _record(violations: list, message: str) -> None:
    if message not in violations:
        violations.append(message)


def test_no_production_cypher_reads_a_removed_letter_property() -> None:
    pattern = re.compile(
        r"\b(" + "|".join(LETTER_VARIABLES) + r")\.(" +
        "|".join(sorted(REMOVED_LETTER_PROPERTIES)) + r")\b"
    )

    # An f-string yields both its JoinedStr and its inner constants, so the
    # same read is reachable twice; the finding is the read, not the walk.
    violations: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ":Letter" not in text:
            continue
        for lineno, query in _cypher_literals(text):
            # Only Cypher: `letter.subject` on a Python object is unrelated.
            if not any(kw in query for kw in CYPHER_KEYWORDS):
                continue
            for match in pattern.finditer(query):
                _record(
                    violations,
                    f"{path.relative_to(BACKEND_ROOT)}:{lineno}: reads "
                    f"`{match.group(0)}` - the shared Letter node does not "
                    f"carry `{match.group(2)}` (GLOBAL_LETTER_PROPERTIES = "
                    f"{sorted(GLOBAL_LETTER_PROPERTIES)}); resolve it from the "
                    f"canonical Mongo document instead.",
                )

    assert not violations, (
        "Production Cypher reads Letter properties the writer no longer "
        "maintains. Such a read silently yields NULL, and a filter built on it "
        "becomes a tautology:\n\n" + "\n".join(violations)
    )


def test_global_letter_properties_is_identity_plus_write_metadata() -> None:
    """Pins the contract the reader test is derived from."""
    assert GLOBAL_LETTER_PROPERTIES == frozenset({"normCode", "createdAt", "lastUpdated"})
    assert not (GLOBAL_LETTER_PROPERTIES & REMOVED_LETTER_PROPERTIES)


def test_a_multiline_projection_is_scanned_as_one_literal() -> None:
    """The blind spot itself is pinned, not just its one known victim.

    This is the exact shape that stayed green while `get_thread` read five
    removed properties: a continuation line inside a `RETURN` projection with
    no Cypher keyword of its own. If anyone reverts the scanner to
    line-at-a-time matching, this test fails.
    """
    module = """
def q():
    return _execute(
        QUERY
    )


QUERY = '''
    MATCH (root:Letter {normCode:$norm})
    RETURN DISTINCT
        letter.normCode AS normCode,
        letter.subject AS subject
'''
"""

    cypher = [
        query
        for _lineno, query in _cypher_literals(module)
        if any(keyword in query for keyword in CYPHER_KEYWORDS)
    ]
    assert cypher, "the multi-line query literal was not extracted at all"
    assert any("letter.subject" in query for query in cypher), (
        "a removed-property read on a continuation line escaped the scanner - "
        "this is exactly the blind spot that let the live read stay green"
    )


def test_migrations_and_scripts_are_in_scope() -> None:
    """The skip that hid the places the rule is hardest to follow is gone."""
    scanned = {path.name for path in _production_files()}
    assert "migrate_database.py" in scanned


# ---------------------------------------------------------------------------
# G-A19 / G30 assertion D — the guard's own variable-name blind spot
# ---------------------------------------------------------------------------
#
# `LETTER_VARIABLES` above is a fixed allowlist of the names production Cypher
# HAPPENED to use when this file was written. That is not the rule. The rule is
# "no production Cypher reads a property the shared Letter node does not carry",
# and a query is free to bind its Letter to any identifier it likes:
#
#     MATCH (n:Letter) RETURN n.organization_id AS org, ...
#
# reads exactly the property G32 removed and matches none of `node`/`l`/`src`/
# `dst`/`root`/`letter`, so the original scanner walked straight past it. A
# guard whose coverage depends on a naming convention nobody enforces is a
# guard against tidy code, not against the defect.
#
# The binding is IN the query. Deriving it there makes the scan complete by
# construction: rename the variable and the guard follows.


def _letter_bound_variables(query: str) -> set[str]:
    """Every identifier this Cypher literal binds to a `:Letter`.

    Covers `(n:Letter)`, `(n :Letter)` and `(n:Letter {normCode:$x})`. An
    anonymous `(:Letter)` binds nothing and is correctly ignored.
    """
    return {
        match.group(1)
        for match in re.finditer(r"\(\s*([A-Za-z_]\w*)\s*:Letter\b", query)
    }


def test_no_production_cypher_reads_a_removed_letter_property_under_any_variable_name() -> None:
    """The same rule as above, with the variable set derived from the query."""
    violations: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ":Letter" not in text:
            continue
        for lineno, query in _cypher_literals(text):
            if not any(kw in query for kw in CYPHER_KEYWORDS):
                continue
            bound = _letter_bound_variables(query)
            if not bound:
                continue
            pattern = re.compile(
                r"\b(" + "|".join(sorted(re.escape(name) for name in bound)) + r")\.(" +
                "|".join(sorted(REMOVED_LETTER_PROPERTIES)) + r")\b"
            )
            for match in pattern.finditer(query):
                _record(
                    violations,
                    f"{path.relative_to(BACKEND_ROOT)}:{lineno}: reads "
                    f"`{match.group(0)}` on a variable this query binds to "
                    f"`:Letter` - the shared node does not carry "
                    f"`{match.group(2)}` (GLOBAL_LETTER_PROPERTIES = "
                    f"{sorted(GLOBAL_LETTER_PROPERTIES)}); resolve it from the "
                    f"canonical Mongo document instead.",
                )

    assert not violations, (
        "Production Cypher reads Letter properties the writer no longer "
        "maintains, under a variable name the fixed allowlist does not "
        "contain. Such a read silently yields NULL, so a filter built on it "
        "becomes a tautology and a GROUP BY built on it becomes fiction:\n\n"
        + "\n".join(violations)
    )


def test_the_variable_allowlist_is_a_floor_and_not_the_rule() -> None:
    """Pins the blind spot itself, so nobody reverts to the fixed list alone.

    `n` is not in `LETTER_VARIABLES`, and this is the exact shape that let
    `routers/storage_sync.py` group FalkorDB nodes by `organization_id` - a
    property nothing has written since G32 - while the band stayed green.
    """
    query = "MATCH (n:Letter) RETURN n.organization_id AS org, count(n) AS nodes"

    assert _letter_bound_variables(query) == {"n"}
    assert "n" not in LETTER_VARIABLES, (
        "if `n` is added to the fixed allowlist this test stops proving "
        "anything - the point is that the derived scan does not need it"
    )
