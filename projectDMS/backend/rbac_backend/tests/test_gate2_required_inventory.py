"""Gate 2's required-test inventory must match the architecture the gate approved.

R-A8Q's staging run measured 45 collected, 45 executed, **44 passed**. The one
failure was `test_falkordb_vector_service_round_trip_live`, which implements a
criterion the gate document formally SUPERSEDED on 2026-09-04: a FalkorDB
*vector* round trip, which `FalkorDBVectorService._create_index` performs with
the RediSearch `FT.CREATE` command that the production pin
`falkordb/falkordb:v4.0.8` does not provide.

The withdrawal was recorded in prose and enforced nowhere. Required membership
(`staging_gate.GATE2_REQUIRED_LIVE_FILES`) is decided per **file**, and the
withdrawn test lives inside a required file, so the module carried a test for a
requirement that no longer exists and Gate 2 could not exit 0 while it did.
That is F-A8Q-1: the test outlived the requirement.

The fix is not to delete or silence the test. It is to say, in the inventory
itself, which test nodes inside a required module are **not** Gate 2 evidence,
and to make that statement checkable. This file is the check.

Six rules, each with a synthetic mutation that breaks exactly one of them:

* the withdrawn criterion may not become required again while Qdrant is canonical;
* the Qdrant vector round trip may not stop being required;
* the FalkorDB `GRAPH.*` round trip may not stop being required;
* a withdrawal may not name a test that does not exist;
* a withdrawal may not name a test outside the required set;
* a withdrawal may not remove coverage another bullet is scored from.

The validator is a pure function over (inventory, module sources), so the
mutations exercise the rules rather than the document.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pytest

from rbac_backend.tests import staging_gate
from rbac_backend.tests.required_test_paths import canonical_test_path

TESTS = Path(__file__).resolve().parent
PROJECT = TESTS.parents[2]

VECTOR_SERVICE = "FalkorDBVectorService"
GRAPH_SERVICE = "FalkorGraphService"
LIVE_MARKER = "live_external_service"


# --------------------------------------------------------------------------- #
# Capability derivation - read from the modules, not from their filenames
# --------------------------------------------------------------------------- #


def _read(relative: str) -> Optional[str]:
    path = PROJECT / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _test_functions(source: str) -> List[Tuple[str, str, Sequence[str]]]:
    """(name, body source, decorator sources) for every test function in a module."""
    tree = ast.parse(source)
    out: List[Tuple[str, str, Sequence[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
        out.append((node.name, ast.get_source_segment(source, node) or "", decorators))
    return out


def _test_names(source: str) -> set:
    return {name for name, _, _ in _test_functions(source)}


def _is_live(name: str, decorators: Sequence[str]) -> bool:
    """Is this a test against a real engine, rather than a hermetic contract test?

    Two independent signals, either of which is enough. The marker is the
    explicit one - `pytest.ini` registers `live_external_service` as "opt-in
    smoke test against real OpenAI, Qdrant, or Redis/Falkor infrastructure" -
    and the `_live` suffix is the convention the two required modules without
    the marker follow. Requiring both would let a test drop the marker and stop
    being seen by every rule below.
    """
    return name.endswith("_live") or any(LIVE_MARKER in decorator for decorator in decorators)


def module_provides_qdrant_vector_round_trip(source: str) -> bool:
    low = source.lower()
    return "qdrant" in low and any(token in low for token in ("vector", "upsert", "search"))


def module_provides_falkor_graph_round_trip(source: str) -> bool:
    return GRAPH_SERVICE in source or "GRAPH." in source


def live_falkor_vector_tests(source: str) -> List[str]:
    """Test names implementing the withdrawn FalkorDB vector criterion.

    The hermetic `..._contract` sibling monkeypatches Redis and asserts against
    a fake. It implements no gate criterion, blocks no gate, and is deliberately
    not returned: a rule that demanded its withdrawal would be an obstacle
    rather than a control.
    """
    return [
        name
        for name, body, decorators in _test_functions(source)
        if VECTOR_SERVICE in body and _is_live(name, decorators)
    ]


def live_qdrant_vector_tests(source: str) -> List[str]:
    return [
        name
        for name, body, decorators in _test_functions(source)
        if "qdrant" in body.lower() and _is_live(name, decorators)
    ]


def live_falkor_graph_tests(source: str) -> List[str]:
    return [
        name
        for name, body, decorators in _test_functions(source)
        if module_provides_falkor_graph_round_trip(body) and _is_live(name, decorators)
    ]


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #


def validate_gate2_inventory(
    required_files: Sequence[str],
    withdrawn_tests: Sequence[str],
    *,
    read: Callable[[str], Optional[str]] = _read,
) -> List[str]:
    """One message per violated rule. Empty means the inventory is sound."""
    errors: List[str] = []

    sources: Dict[str, str] = {}
    for relative in required_files:
        source = read(relative)
        if source is None:
            errors.append(f"required Gate 2 module does not exist: {relative!r}")
            continue
        sources[canonical_test_path(relative)] = source

    # 1/2. The two capabilities the amendment rests on must still be required.
    if not any(module_provides_qdrant_vector_round_trip(s) for s in sources.values()):
        errors.append(
            "vector coverage lost: no required Gate 2 module exercises a Qdrant "
            "vector round trip, which is where the production vector path lives"
        )
    if not any(module_provides_falkor_graph_round_trip(s) for s in sources.values()):
        errors.append(
            "graph coverage lost: no required Gate 2 module exercises a FalkorDB "
            "GRAPH.* round trip"
        )

    withdrawn = {staging_gate.canonical_test_node(node) for node in withdrawn_tests}

    # 3. The withdrawn criterion must stay withdrawn.
    for canonical, source in sources.items():
        for name in live_falkor_vector_tests(source):
            node = f"{canonical}::{name}"
            if node not in withdrawn:
                errors.append(
                    "obsolete criterion is required again: "
                    f"{node} exercises FalkorDBVectorService against a live engine, "
                    "which needs RediSearch FT.CREATE. The deployed pin "
                    "falkordb/falkordb:v4.0.8 loads only the `graph` module, and the "
                    "gate document withdrew that criterion on 2026-09-04. Either "
                    "withdraw the test from Gate 2 membership or reinstate the "
                    "requirement in docs/PRODUCTION_READINESS_RELEASE_GATE.md"
                )

    # 4/5/6. A withdrawal is a claim about a real test, in the required set,
    #        that carries no other bullet's evidence.
    for node in withdrawn_tests:
        canonical = staging_gate.canonical_test_node(node)
        module, _, name = canonical.partition("::")
        if not name:
            errors.append(f"withdrawn entry is not a test node id: {node!r}")
            continue
        source = sources.get(module)
        if source is None:
            errors.append(
                f"withdrawal outside the required set: {node!r} names a module that "
                "Gate 2 does not require, so withdrawing it changes nothing and "
                "reads as a control that is not there"
            )
            continue
        if name not in _test_names(source):
            errors.append(f"withdrawn entry names a test that does not exist: {node!r}")
            continue
        if name in live_qdrant_vector_tests(source):
            errors.append(
                f"withdrawal removes required coverage: {node!r} is a live Qdrant "
                "vector round trip, which Gate 2 bullet 3 is scored from"
            )
        if name in live_falkor_graph_tests(source):
            errors.append(
                f"withdrawal removes required coverage: {node!r} is a live FalkorDB "
                "graph round trip, which Gate 2 bullet 4 is scored from"
            )

    return errors


# --------------------------------------------------------------------------- #
# The real inventory
# --------------------------------------------------------------------------- #


def test_the_real_inventory_is_sound() -> None:
    assert (
        validate_gate2_inventory(
            staging_gate.GATE2_REQUIRED_LIVE_FILES,
            staging_gate.GATE2_WITHDRAWN_LIVE_TESTS,
        )
        == []
    )


def test_every_required_module_still_exists() -> None:
    missing = [
        relative
        for relative in staging_gate.GATE2_REQUIRED_LIVE_FILES
        if not (PROJECT / relative).is_file()
    ]
    assert missing == [], missing


def test_the_withdrawn_test_is_still_in_the_tree() -> None:
    """Withdrawn from the gate, not deleted from the repository.

    The gate document says the test "remains in the tree and is not gate
    evidence for any bullet". Deleting it would make that sentence false and
    would throw away the only executable record of what the old criterion asked
    for - which is where a future reinstatement would have to start.
    """
    for node in staging_gate.GATE2_WITHDRAWN_LIVE_TESTS:
        module, _, name = node.partition("::")
        source = _read(module)
        assert source is not None, f"withdrawn test module is gone: {module}"
        assert name in _test_names(source), f"withdrawn test {name} is gone from {module}"


def test_the_withdrawal_is_scoped_to_gate2_certification_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside the gate the test runs exactly as before.

    A withdrawal that suppressed the test everywhere would be a deletion with
    extra steps, and would take the legacy coverage with it.
    """
    node = staging_gate.GATE2_WITHDRAWN_LIVE_TESTS[0]

    monkeypatch.delenv(staging_gate.STAGING_GATE_ENV, raising=False)
    assert staging_gate.is_gate2_withdrawn_test(node) is True
    assert staging_gate.gate2_withdrawn_from_certification(node) is False

    monkeypatch.setenv(staging_gate.STAGING_GATE_ENV, "1")
    assert staging_gate.gate2_withdrawn_from_certification(node) is True


def test_withdrawal_is_decided_from_any_root() -> None:
    """The container reports `/app/rbac_backend/...`; the repository does not.

    Same defect class as R-A8I, where a `backend/`-prefixed suffix match was
    inert inside the image that produces the evidence.
    """
    node = staging_gate.GATE2_WITHDRAWN_LIVE_TESTS[0]
    module, _, name = node.partition("::")
    container = f"/app/{canonical_test_path(module)}::{name}"
    assert staging_gate.is_gate2_withdrawn_test(container) is True
    assert staging_gate.is_gate2_withdrawn_test(f"{PROJECT / module}::{name}") is True


def test_a_sibling_test_in_the_same_module_is_not_withdrawn() -> None:
    """Withdrawal is per test, not per module - the module still carries bullets 1-3."""
    module = "backend/rbac_backend/tests/integration/test_external_services_integration.py"
    assert (
        staging_gate.is_gate2_withdrawn_test(
            f"{module}::test_openai_service_document_round_trip_live"
        )
        is False
    )
    assert (
        staging_gate.is_gate2_withdrawn_test(
            f"{module}::test_qdrant_langchain_vector_round_trip_live"
        )
        is False
    )
    assert staging_gate.is_gate2_required_file(module) is True


def test_a_bare_module_path_is_not_a_withdrawn_node() -> None:
    """Withdrawing a whole required module is what file-level membership already means.

    If `is_gate2_withdrawn_test` accepted a module path, one entry could silently
    remove three bullets' evidence.
    """
    module = "backend/rbac_backend/tests/integration/test_external_services_integration.py"
    assert staging_gate.is_gate2_withdrawn_test(module) is False


# --------------------------------------------------------------------------- #
# One synthetic mutation per rule
# --------------------------------------------------------------------------- #

_EXTERNAL = "backend/rbac_backend/tests/integration/test_external_services_integration.py"
_QDRANT = "backend/rbac_backend/tests/integration/test_qdrant_containment_live.py"
_FALKOR = "backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py"
_REDIS = "backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py"

_SYNTHETIC: Dict[str, str] = {
    _EXTERNAL: (
        "import pytest\n"
        "\n"
        "@pytest.mark.live_external_service\n"
        "async def test_qdrant_langchain_vector_round_trip_live():\n"
        "    qdrant_client.upsert(vector)\n"
        "\n"
        "@pytest.mark.live_external_service\n"
        "async def test_falkordb_vector_service_round_trip_live():\n"
        "    FalkorDBVectorService(config)\n"
        "\n"
        "async def test_falkordb_vector_service_round_trip_contract(monkeypatch):\n"
        "    FalkorDBVectorService(config)\n"
    ),
    _QDRANT: (
        "def test_qdrant_containment_live():\n"
        "    qdrant_client.upsert(vector)\n"
        "    qdrant_client.search(vector)\n"
    ),
    _FALKOR: ("def test_graph_round_trip_live():\n    FalkorGraphService(config)\n"),
    _REDIS: ("def test_runtime_state_write_read_delete_live():\n    redis_client.set('k', 'v')\n"),
}

_SYNTHETIC_REQUIRED = (_EXTERNAL, _QDRANT, _FALKOR, _REDIS)
_SYNTHETIC_WITHDRAWN = (f"{_EXTERNAL}::test_falkordb_vector_service_round_trip_live",)


def _synthetic_read(relative: str) -> Optional[str]:
    return _SYNTHETIC.get(relative)


def test_the_sound_synthetic_inventory_passes() -> None:
    """Anchor: each mutation below must fail for its own reason, not by accident."""
    assert (
        validate_gate2_inventory(_SYNTHETIC_REQUIRED, _SYNTHETIC_WITHDRAWN, read=_synthetic_read)
        == []
    )


def test_the_hermetic_contract_sibling_is_not_treated_as_a_criterion() -> None:
    assert live_falkor_vector_tests(_SYNTHETIC[_EXTERNAL]) == [
        "test_falkordb_vector_service_round_trip_live"
    ]


@pytest.mark.parametrize(
    "required, withdrawn, expected",
    [
        pytest.param(
            _SYNTHETIC_REQUIRED,
            (),
            "obsolete criterion is required again",
            id="legacy-vector-test-re-added-to-required-membership",
        ),
        pytest.param(
            (_FALKOR, _REDIS),
            (),
            "vector coverage lost",
            id="qdrant-vector-coverage-removed",
        ),
        pytest.param(
            (_EXTERNAL, _QDRANT, _REDIS),
            _SYNTHETIC_WITHDRAWN,
            "graph coverage lost",
            id="falkor-graph-coverage-removed",
        ),
        pytest.param(
            _SYNTHETIC_REQUIRED,
            _SYNTHETIC_WITHDRAWN + (f"{_EXTERNAL}::test_no_such_test",),
            "names a test that does not exist",
            id="phantom-withdrawal",
        ),
        pytest.param(
            _SYNTHETIC_REQUIRED,
            _SYNTHETIC_WITHDRAWN
            + ("backend/rbac_backend/tests/integration/test_not_required.py::test_x",),
            "withdrawal outside the required set",
            id="withdrawal-of-something-not-required",
        ),
        pytest.param(
            _SYNTHETIC_REQUIRED,
            _SYNTHETIC_WITHDRAWN
            + (f"{_EXTERNAL}::test_qdrant_langchain_vector_round_trip_live",),
            "withdrawal removes required coverage",
            id="withdrawal-of-qdrant-vector-coverage",
        ),
        pytest.param(
            _SYNTHETIC_REQUIRED,
            _SYNTHETIC_WITHDRAWN + (f"{_FALKOR}::test_graph_round_trip_live",),
            "withdrawal removes required coverage",
            id="withdrawal-of-falkor-graph-coverage",
        ),
    ],
)
def test_each_rule_rejects_its_own_breakage(
    required: Sequence[str], withdrawn: Sequence[str], expected: str
) -> None:
    errors = validate_gate2_inventory(required, withdrawn, read=_synthetic_read)
    assert any(expected in error for error in errors), (
        f"expected an error containing {expected!r}; got {errors}"
    )


# --------------------------------------------------------------------------- #
# The precondition the withdrawal depends on
# --------------------------------------------------------------------------- #


def test_the_withdrawal_is_only_legitimate_while_qdrant_is_canonical() -> None:
    """The inventory's exclusion and the gate document's supersession are one claim.

    `test_release_gate_specification.py` proves the architectural half - that no
    production module reaches `FalkorDBVectorService`. This asserts the document
    still records the withdrawal and still names the excluded test, so the
    inventory cannot quietly outlive the decision that authorised it.
    """
    gate = (PROJECT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md").read_text(encoding="utf-8")
    assert "Superseded requirement: FalkorDB vector round trip" in gate
    assert "SUPERSEDED" in gate
    assert "APPROVED FOR `release/contraclaim-rc1`" in gate
    for node in staging_gate.GATE2_WITHDRAWN_LIVE_TESTS:
        _, _, name = node.partition("::")
        assert name in gate, (
            f"{name} is withdrawn from Gate 2 membership but the gate document does "
            "not name it. A withdrawal nobody can find in the gate is a suppression."
        )


def test_the_conftest_actually_applies_the_withdrawal() -> None:
    """The inventory is inert unless a collection hook reads it.

    Scoped to the hook rather than to the module, for the reason
    `test_staging_gate_preflight.py` records about the certification-gate table:
    deleting the call while leaving the import in place is the natural shape of
    this regression, and a module-wide identifier check passes straight through
    it.
    """
    conftest = TESTS / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"))

    hook = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems"
        ),
        None,
    )
    assert hook is not None, (
        "the collection hook is gone, so GATE2_WITHDRAWN_LIVE_TESTS deselects nothing "
        "and a withdrawn criterion runs as required Gate 2 evidence again"
    )
    called = {node.id for node in ast.walk(hook) if isinstance(node, ast.Name)}
    assert "gate2_withdrawn_from_certification" in called, (
        "the collection hook no longer consults the withdrawal decision"
    )
    assert "staging_gate_mode" in called, (
        "the collection hook no longer scopes the deselection to certification mode, "
        "so an ordinary run would lose the legacy coverage too"
    )
