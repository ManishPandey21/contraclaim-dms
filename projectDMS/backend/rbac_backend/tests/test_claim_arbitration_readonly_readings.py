"""4B-01: the Phase 4b production readings are read-only by construction.

The reader runs inside the production backend container. These checks pin
that it cannot write, whatever a future edit adds to it, and that the shell
section around it uses only the docker verbs the script's header promises.
The real-Mongo proof (database byte-for-byte unchanged, nothing personal in
the output) is tests/integration/test_claim_arbitration_readings_mongo.py.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

PROJECT = Path(__file__).resolve().parents[3]
READER = PROJECT / "scripts" / "claim_arbitration_readonly_readings.py"
CHECKS = PROJECT / "scripts" / "post_cutover_readonly_checks.sh"


def _reader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claim_arbitration_readonly_readings", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_write_operation_is_called_anywhere_in_the_reader() -> None:
    reader = _reader()
    tree = ast.parse(READER.read_text())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert not (called & reader._REFUSED), sorted(called & reader._REFUSED)
    # $out / $merge appear only in the guard's refusal tuple.
    source = READER.read_text()
    assert source.count('"$out"') == 1 and source.count('"$merge"') == 1


class _Collection:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def find(self, *args, **kwargs):
        self.calls.append("find")
        return []

    def aggregate(self, pipeline, *args, **kwargs):
        self.calls.append("aggregate")
        return []

    def insert_one(self, *args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("the guard let a write through")


class _Database:
    def __init__(self) -> None:
        self.collection = _Collection()

    def list_collection_names(self):
        return ["claims"]

    def __getitem__(self, name):
        return self.collection

    def command(self, *args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("the guard let a command through")


@pytest.mark.parametrize("method", sorted(_reader()._REFUSED - {"command"}))
def test_the_guard_refuses_every_write_method_on_a_collection(method: str) -> None:
    reader = _reader()
    guarded = reader.ReadOnlyDatabase(_Database())["claims"]
    with pytest.raises(PermissionError):
        getattr(guarded, method)


@pytest.mark.parametrize("method", ["command", "drop_collection", "create_collection", "client"])
def test_the_guard_refuses_database_level_operations(method: str) -> None:
    reader = _reader()
    with pytest.raises(PermissionError):
        getattr(reader.ReadOnlyDatabase(_Database()), method)


@pytest.mark.parametrize("stage", [{"$out": "copy"}, {"$merge": {"into": "copy"}}])
def test_the_guard_refuses_an_aggregation_that_writes(stage) -> None:
    reader = _reader()
    guarded = reader.ReadOnlyDatabase(_Database())["claims"]
    with pytest.raises(PermissionError):
        guarded.aggregate([{"$match": {}}, stage])


def test_a_reading_that_fails_is_reported_as_not_read_never_as_zero() -> None:
    reader = _reader()

    class Broken(_Database):
        def list_collection_names(self):
            raise RuntimeError("connection refused")

    rows = reader.collect(Broken(), environ={})
    by_code = {row["reading"]: row for row in rows}
    assert by_code["DB"]["status"] == "not_read" and "connection refused" in by_code["DB"]["reason"]
    assert by_code["P-03"]["status"] == "not_read" and "total" not in by_code["P-03"]
    # A reading that needs no database is still taken.
    assert by_code["P-10"]["status"] == "read"


def test_an_empty_or_wrong_database_is_never_read_as_an_empty_production() -> None:
    """Owner review B.7: absence of data that was never read is not evidence."""
    reader = _reader()

    class Empty(_Database):
        def list_collection_names(self):
            return []

    rows = reader.collect(Empty(), environ={}, database_name="contraclaim")
    by_code = {row["reading"]: row for row in rows}
    assert by_code["DB"] == {
        "reading": "DB", "status": "read", "database": "contraclaim", "collections": 0, "initialised": False,
    }
    for code in [f"P-{n:02d}" for n in range(2, 16) if n != 10]:
        assert by_code[code]["status"] == "not_read", code
        assert "not evidence of an empty production" in by_code[code]["reason"]
        assert "total" not in by_code[code] and "users_total" not in by_code[code]


def test_the_reviewer_matrix_setting_is_reported_set_or_not_set() -> None:
    reader = _reader()
    assert reader.read_reviewer_matrix_setting({})["arbitration_reviewer_role_matrix_set"] is False
    row = reader.read_reviewer_matrix_setting({"ARBITRATION_REVIEWER_ROLE_MATRIX": "readiness=senior_legal_approver"})
    assert row["arbitration_reviewer_role_matrix_set"] is True


def _section_f() -> str:
    text = CHECKS.read_text()
    start = text.index('section "F. Phase 4b readings')
    return text[start : text.index("SUMMARY", start)]


def test_section_f_uses_only_read_only_docker_verbs() -> None:
    verbs = set(re.findall(r"\bdocker\s+([a-z]+)", _section_f()))
    assert verbs <= {"exec", "ps", "logs", "inspect"}, verbs
    for forbidden in ("compose", "up", "start", "stop", "restart", "rm", "run", "kill", "update", "rename"):
        assert not re.search(rf"\bdocker\s+{forbidden}\b", _section_f()), forbidden


def test_section_f_executes_only_the_guarded_reader() -> None:
    execs = re.findall(r"docker exec[^\n]*", _section_f())
    assert execs and all('python - <"$READER"' in line for line in execs), execs


def test_the_script_header_names_section_f_and_the_logs_verb() -> None:
    header = CHECKS.read_text().split("set -u", 1)[0]
    assert "F. Phase 4b readings" in header
    assert "`logs`" in header


def test_p16_restates_the_key_date_formula_exactly() -> None:
    # The reader runs on images without the service, so it restates calculate_key_date;
    # the two must agree on both week bases.
    from datetime import datetime

    from rbac_backend.services.key_date_service import calculate_key_date

    reader = _reader()
    start = datetime(2026, 1, 5)
    for basis in ("loa_plus_weeks", "loa_plus_weeks_minus_1"):
        for week in (1, 4, 52, 104):
            assert reader._kd_calc(start, week, basis) == calculate_key_date(start, week, basis), (basis, week)
    assert reader._kd_calc(start, "not a week", "loa_plus_weeks") is None


def test_def4_inline_grants_and_alias_reach_match_the_application() -> None:
    """The P-05 billing reading runs on old images, so it inlines the DEF-4 target grants and
    the alias reach; they must equal the application's definitions."""
    import importlib.util
    from pathlib import Path

    from rbac_backend.core.permissions import equivalent_permissions
    from rbac_backend.services.billing_permission_plan import TARGET_ROLE_GRANTS

    path = Path(__file__).resolve().parents[3] / "scripts" / "claim_arbitration_readonly_readings.py"
    spec = importlib.util.spec_from_file_location("readings_def4", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {k: set(v) for k, v in module.DEF4_TARGET_ROLE_GRANTS.items()} == {k: set(v) for k, v in TARGET_ROLE_GRANTS.items()}
    for legacy, reach in module.DEF4_ALIAS_REACH.items():
        billing = {name for name in equivalent_permissions(legacy) if name.startswith(("billing.", "subscription."))}
        assert billing == set(reach), (legacy, billing)


def test_m1_inline_built_in_role_keys_match_the_application() -> None:
    """The P-05 M1 reading runs on old images, so it inlines the built-in role keys; they
    must equal services.role_grants.BUILT_IN_ROLE_KEYS."""
    import importlib.util
    from pathlib import Path

    from rbac_backend.services.role_grants import BUILT_IN_ROLE_KEYS

    path = Path(__file__).resolve().parents[3] / "scripts" / "claim_arbitration_readonly_readings.py"
    spec = importlib.util.spec_from_file_location("readings_m1", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.M1_BUILT_IN_ROLE_KEYS) == set(BUILT_IN_ROLE_KEYS)


def test_decision4_inline_standing_roles_and_views_match_the_application() -> None:
    import importlib.util
    from pathlib import Path

    from rbac_backend.services.policy_service import (
        ORGANISATION_FINANCIAL_VIEW_PERMISSIONS,
        ORGANISATION_LEVEL_BILLING_ROLE_KEYS,
    )

    path = Path(__file__).resolve().parents[3] / "scripts" / "claim_arbitration_readonly_readings.py"
    spec = importlib.util.spec_from_file_location("readings_d4", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.D4_STANDING_ROLE_KEYS) == set(ORGANISATION_LEVEL_BILLING_ROLE_KEYS)
    assert set(module.D4_FINANCIAL_VIEWS) == set(ORGANISATION_FINANCIAL_VIEW_PERMISSIONS)
