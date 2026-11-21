"""The contract-scope rule reads linked records from the Delay Lab's collections.

It names them itself so that it does not import the Delay Lab (see the comment
in ``services/claim_contract_scope.py``). This pins the two together.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Imported first: the Delay Lab's import chain is circular unless the prompt registry is loaded.
import rbac_backend.services.letter_drafting.prompts  # noqa: F401
from rbac_backend.services import claim_contract_scope, claim_delay_lab


def test_the_record_collections_are_the_delay_labs() -> None:
    assert claim_contract_scope._RECORD_COLLECTIONS == {
        "hindrance": claim_delay_lab.HINDRANCES,
        "eot_submission": claim_delay_lab.SUBMISSIONS,
        "eot_determination": claim_delay_lab.DETERMINATIONS,
    }


def test_the_rule_does_not_import_the_delay_lab_or_key_date_services() -> None:
    tree = ast.parse(Path(claim_contract_scope.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not {name for name in imported if "claim_delay_lab" in name or "key_date" in name}
