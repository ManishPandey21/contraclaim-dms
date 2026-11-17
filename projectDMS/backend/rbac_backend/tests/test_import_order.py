"""Import order must not matter (owner review 2026-11-17, technical debt).

``skills.executor`` imports the leaf ``letter_drafting.prompts``. While the
``letter_drafting`` package imported its drafting service eagerly, that pulled
in ``letter_drafting.bench``, which imports ``skills.executor`` again: in a fresh
process, importing the executor, the Delay Lab, Quantum, determinations,
submission or escalation first raised ImportError. Tests hid it by importing
``claim_bench`` first. Each case here starts a new interpreter, so nothing an
earlier test imported can mask a cycle.
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

MODULES = [
    "rbac_backend.services.skills.executor",
    "rbac_backend.services.letter_drafting.prompts",
    "rbac_backend.services.letter_drafting",
    "rbac_backend.services.letter_drafting.bench",
    "rbac_backend.services.letter_drafting.service",
    "rbac_backend.services.claim_delay_lab",
    "rbac_backend.services.claim_quantum",
    "rbac_backend.services.claim_bench",
    "rbac_backend.services.claim_determinations",
    "rbac_backend.services.claim_submission",
    "rbac_backend.services.claim_escalation",
    "rbac_backend.services.claim_contract_scope",
    "rbac_backend.routers.claim_findings",
    "rbac_backend.routers.letter_drafting",
]

# The chain the cycle ran through, in every order.
CHAIN = [
    "rbac_backend.services.skills.executor",
    "rbac_backend.services.letter_drafting",
    "rbac_backend.services.claim_delay_lab",
    "rbac_backend.services.claim_quantum",
]


def _import_in_fresh_process(names: list[str]) -> subprocess.CompletedProcess:
    code = "import importlib, sys\nfor name in sys.argv[1:]:\n    importlib.import_module(name)\n"
    return subprocess.run(
        [sys.executable, "-c", code, *names], cwd=BACKEND, capture_output=True, text=True, timeout=180
    )


@pytest.mark.parametrize("module", MODULES)
def test_each_module_imports_first_in_a_fresh_process(module: str) -> None:
    result = _import_in_fresh_process([module])
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("order", list(itertools.permutations(CHAIN)), ids=lambda order: ">".join(n.rsplit(".", 1)[-1] for n in order))
def test_the_chain_imports_in_any_order(order: tuple[str, ...]) -> None:
    result = _import_in_fresh_process(list(order))
    assert result.returncode == 0, result.stderr[-2000:]


def test_the_package_still_offers_the_drafting_service() -> None:
    result = _import_in_fresh_process(["rbac_backend.services.letter_drafting"])
    assert result.returncode == 0
    check = subprocess.run(
        [sys.executable, "-c",
         "from rbac_backend.services.letter_drafting import DraftRunService\n"
         "from rbac_backend.services.letter_drafting.service import DraftRunService as D\n"
         "assert DraftRunService is D"],
        cwd=BACKEND, capture_output=True, text=True, timeout=180,
    )
    assert check.returncode == 0, check.stderr[-2000:]
