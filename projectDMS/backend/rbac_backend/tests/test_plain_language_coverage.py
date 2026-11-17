"""4B-11: the client's plain-language map covers every status and error code the server sends.

The client cannot see the server's enums, so a status added here and not to
``client/src/lib/plain-language.ts`` would reach the user as a raw key. This
test reads the TypeScript map and fails on any value left unmapped.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Set

from rbac_backend.models.arbitration_drafting import ArbitrationCaseStatus, ArbitrationDraftStatus
from rbac_backend.models.claim import ClaimStatus

ROOT = Path(__file__).resolve().parents[3]
PLAIN = ROOT / "client" / "src" / "lib" / "plain-language.ts"
BACKEND = ROOT / "backend" / "rbac_backend"


def _keys(const: str) -> Set[str]:
    text = PLAIN.read_text(encoding="utf-8")
    match = re.search(rf"export const {const} = \{{(.*?)\}} as const;", text, re.S)
    assert match, f"{const} not found in {PLAIN}"
    return set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.M))


def test_every_claim_status_has_a_plain_label() -> None:
    assert {item.value for item in ClaimStatus} - _keys("CLAIM_STATUS_LABELS") == set()


def test_every_arbitration_case_status_has_a_plain_label() -> None:
    assert {item.value for item in ArbitrationCaseStatus} - _keys("ARBITRATION_CASE_STATUS_LABELS") == set()


def test_every_arbitration_draft_status_has_a_plain_label() -> None:
    assert {item.value for item in ArbitrationDraftStatus} - _keys("ARBITRATION_DRAFT_STATUS_LABELS") == set()


def _structured_codes(path: Path) -> Set[str]:
    """String values of a ``"code"`` key in dict literals, plus codes passed to ``_refusal``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    codes: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "code" and isinstance(value, ast.Constant):
                    if isinstance(value.value, str):
                        codes.add(value.value)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"_refusal", "_refuse"} and len(node.args) >= 2:
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                codes.add(arg.value)
    return codes


def test_every_structured_error_code_from_the_phase_4b_routes_has_a_message() -> None:
    routes = [
        BACKEND / "routers" / "pickers.py",
        BACKEND / "routers" / "claims.py",
        # Phase 4b services that raise structured refusals (4B-13, 4B-14, 4B-16, 4B-17).
        BACKEND / "services" / "claim_signals.py",
        BACKEND / "services" / "claim_notice_requirements.py",
        BACKEND / "services" / "claim_bench.py",
        BACKEND / "services" / "claim_escalation.py",
        # Reviewer role assignments (4B-07).
        BACKEND / "services" / "reviewer_assignments.py",
        # The party a project acts for (4B-15).
        BACKEND / "services" / "project_perspective.py",
        # The contract a claim is made under (4B-18).
        BACKEND / "services" / "claim_contracts.py",
        # No cross-contract evidence on a claim (4B-23).
        BACKEND / "services" / "claim_contract_scope.py",
        # Quantum in paise and computed formulas (4B-24).
        BACKEND / "services" / "claim_quantum.py",
        # Determination four-eyes (4B-26).
        BACKEND / "services" / "claim_determinations.py",
    ]
    codes: Set[str] = set()
    for path in routes:
        codes |= _structured_codes(path)
    assert codes, "no structured codes found: the scan is broken"
    # PolicyService refusals the client renders from "Not authorized: <reason>".
    codes |= {"explicit_grant_required", "missing_permission", "no_active_subscription"}
    assert codes - _keys("ERROR_CODE_MESSAGES") == set()


def test_no_message_code_is_defined_twice() -> None:
    # A repeated key in the object literal silently replaces the earlier message (it
    # happened: OD-1 reused 4B-18's claim_contract_required). CI does not type-check the
    # client, so this catches it.
    import collections

    text = PLAIN.read_text(encoding="utf-8")
    for block in re.findall(r"^export const \w+ = \{\n(.*?)^\} as const;", text, re.M | re.S):
        keys = re.findall(r"^  ([a-z][a-z0-9_]*):", block, re.M)
        repeated = sorted(key for key, count in collections.Counter(keys).items() if count > 1)
        assert repeated == [], repeated
