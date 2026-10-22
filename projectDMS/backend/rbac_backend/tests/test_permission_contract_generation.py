from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from rbac_backend.core.permissions import (
    CANONICAL_PERMISSIONS,
    PERMISSION_COMPATIBILITY_ALIASES,
    PERMISSION_CONTRACT_VERSION,
)


ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "scripts" / "generate_permission_contract.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("permission_contract_generator", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_permission_contract_is_generated_from_backend_source() -> None:
    generator = _load_generator()
    expected = generator.build_contract()
    backend_contract = json.loads(
        (ROOT / "backend" / "rbac_backend" / "contracts" / "permission_contract.json").read_text(
            encoding="utf-8"
        )
    )
    frontend_contract = (
        ROOT / "client" / "src" / "config" / "generatedPermissionContract.ts"
    ).read_text(encoding="utf-8")

    assert expected["version"] == PERMISSION_CONTRACT_VERSION
    assert expected["canonical_permissions"] == sorted(CANONICAL_PERMISSIONS)
    assert expected["permission_aliases"] == {
        key: sorted(values)
        for key, values in sorted(PERMISSION_COMPATIBILITY_ALIASES.items())
    }
    assert backend_contract == expected
    assert frontend_contract == generator.render_typescript(expected)


def test_every_route_has_explicit_permission_and_entitlement_shape() -> None:
    routes = _load_generator().build_contract()["routes"]
    assert routes
    for path, rule in routes.items():
        assert path.startswith("/")
        assert set(rule) == {
            "required_any_permissions",
            "required_all_permissions",
            "required_all_features",
            "open_authenticated",
        }
        if not rule["open_authenticated"]:
            assert rule["required_any_permissions"], path
        for feature in rule["required_all_features"]:
            assert feature.startswith("feature."), (path, feature)
