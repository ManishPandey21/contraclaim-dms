"""F-A8M-1 - `--fail-on-warning` must be satisfiable by a healthy tree.

`CLAUDE.md` and `docs/OPERATIONS.md` both present

    python -m rbac_backend.scripts.migrate_database --fail-on-warning

as a pre-deploy gate. R-A8M ran it against staging and measured **exit 2** with
19 entries and no error, because `20260721_0001` declares two documentary notes
and the runner failed on any migration that declared warnings at all. A gate no
healthy tree can pass is not a gate; it is a step operators learn to skip.

The distinction the fix rests on is **provenance, not severity**:

* a **warning** is a property of the database in front of the migration - it
  names a row or a change this deployment carries, and it is absent when the
  data is clean;
* a **notice** is a property of the migration - the same sentence on every
  database, recording a decision its author already made.

Nothing here suppresses anything. Notices are printed on every run, and a
warning still fails the gate. What this module defends is the boundary: a
notice must be a literal written at the call site, so a finding *derived from
the data* cannot be filed as one, and `--fail-on-warning` cannot be quietened by
moving a real finding across the line.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from rbac_backend.migrations import MIGRATIONS, Migration, MigrationResult, MigrationRunner
from rbac_backend.migrations.v20260721_0001_arbitration_phase0_containment import (
    upgrade as upgrade_phase0_containment,
)
from rbac_backend.migrations.v20260906_0001_permission_name_unique import (
    upgrade as upgrade_permission_unique,
)

MIGRATIONS_DIR = Path(MIGRATIONS[0].upgrade.__code__.co_filename).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The migration whose documentary notes made the documented gate unsatisfiable.
DOCUMENTARY_MIGRATION = "20260721_0001"


# --- Fakes ----------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self._docs]


class _Collection:
    def __init__(self):
        self.docs: Dict[Any, Dict[str, Any]] = {}
        self.indexes: List[tuple] = []

    def find(self, query=None):
        return _Cursor(list(self.docs.values()))

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name") or "idx"

    async def update_one(self, query, update, upsert: bool = False):
        doc_id = query.get("_id")
        doc = self.docs.get(doc_id)
        if doc is None and upsert:
            doc = {"_id": doc_id}
            doc.update(update.get("$setOnInsert") or {})
            self.docs[doc_id] = doc
        if doc is not None:
            doc.update(update.get("$set") or {})

    async def update_many(self, query, update):
        return None

    async def delete_one(self, query):
        self.docs.pop(query.get("_id"), None)


class _DB:
    def __init__(self):
        self.collections: Dict[str, _Collection] = {}

    def __getitem__(self, name: str):
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name: str):
        return self[name]


# --- 1. The classification, measured on the real migrations ----------------


async def test_the_documentary_migration_emits_notices_and_no_warnings():
    """`20260721_0001` says what it deliberately does not do. That is a
    property of the migration, identical on every database."""
    result = await upgrade_phase0_containment(_DB(), dry_run=True)

    assert result.warnings == []
    assert len(result.notices) == 2
    assert any("not fabricated" in notice for notice in result.notices)
    assert any("does not restore legacy approvals" in notice for notice in result.notices)


async def test_a_data_derived_finding_is_still_a_warning():
    """`20260906_0001` names the duplicate rows it found. That is a property of
    the database, and it must keep failing the gate."""
    db = _DB()
    db["permissions"].docs = {
        1: {"_id": 1, "name": "dms.contract.clause.read"},
        2: {"_id": 2, "name": "dms.contract.clause.read"},
        3: {"_id": 3, "name": "dms.contract.clause.write"},
    }

    result = await upgrade_permission_unique(db, dry_run=True)

    assert result.notices == []
    assert len(result.warnings) == 1
    assert "duplicate permission row" in result.warnings[0]


async def test_the_same_migration_is_silent_on_a_clean_database():
    """The property that makes it a warning: it goes away when the data is
    clean. A notice never does."""
    db = _DB()
    db["permissions"].docs = {1: {"_id": 1, "name": "dms.contract.clause.read"}}

    result = await upgrade_permission_unique(db, dry_run=True)

    assert result.warnings == []


# --- 2. The gate ------------------------------------------------------------


def _migration(version: str, warnings=(), notices=()):
    async def _upgrade(db, dry_run: bool):
        return MigrationResult(
            version=version,
            name=f"synthetic-{version}",
            status="dry_run" if dry_run else "applied",
            operations=[],
            warnings=list(warnings),
            notices=list(notices),
        )

    return Migration(version, f"synthetic-{version}", "synthetic", _upgrade)


def _gate(results) -> int:
    """The exact decision `migrate_database.py --fail-on-warning` makes."""
    return 2 if any(result.warnings for result in results) else 0


async def test_a_notice_only_catalogue_passes_the_gate():
    runner = MigrationRunner(_DB(), [_migration("29990101_0001", notices=["by design"])])

    results = await runner.run(apply=False)

    assert _gate(results) == 0
    assert results[0].notices == ["by design"]


async def test_an_injected_blocking_warning_still_fails_the_gate():
    """The negative control the demotion has to survive. If a real warning ever
    stops failing here, the gate has been turned off rather than fixed."""
    runner = MigrationRunner(
        _DB(),
        [
            _migration("29990101_0001", notices=["by design"]),
            _migration("29990101_0002", warnings=["4 rows in this database will be rewritten"]),
        ],
    )

    results = await runner.run(apply=False)

    assert _gate(results) == 2


async def test_notices_survive_the_runner_and_reach_the_ledger():
    """Demoting a notice out of the gate is not the same as hiding it. It has
    to be readable in the run output and in the applied-migration ledger, or an
    operator loses the sentence the migration wrote for them."""
    db = _DB()
    runner = MigrationRunner(db, [_migration("29990101_0001", notices=["by design"])])

    results = await runner.run(apply=True)

    assert results[0].to_dict()["notices"] == ["by design"]
    assert db["schema_migrations"].docs["29990101_0001"]["notices"] == ["by design"]


# --- 3. The boundary cannot be crossed by accident --------------------------


MUTATING_METHODS = frozenset({"append", "extend", "insert", "__iadd__"})


def _mutated_names(tree: ast.AST) -> set:
    """Names this module changes after binding them.

    `notices = []` followed by `notices.append(...)` in a loop resolves to an
    empty list literal, which the naive check below would happily accept. What
    makes a notice constant is that nothing writes to it, so the writes are
    what has to be looked for.
    """
    mutated = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            mutated.add(node.target.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and node.func.attr in MUTATING_METHODS:
                mutated.add(owner.id)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                mutated.add(node.value.id)
    return mutated


def _notice_offenders(source: str, label: str) -> list:
    """Every `notices=` argument in `source` that is not a written constant."""
    tree = ast.parse(source)
    mutated = _mutated_names(tree)
    bindings: Dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "notices":
                continue
            value = keyword.value
            where = f"{label}:{node.lineno}"
            if isinstance(value, ast.Name):
                # `notices=notices` is the common shape. It is only acceptable
                # when the name is bound exactly once, to a literal, and never
                # written to afterwards.
                if value.id in mutated:
                    offenders.append(f"{where}: {value.id!r} is mutated after it is bound")
                    continue
                bound = bindings.get(value.id, [])
                if len(bound) != 1:
                    offenders.append(f"{where}: {value.id!r} is not bound exactly once")
                    continue
                value = bound[0]
            if not isinstance(value, ast.List):
                offenders.append(f"{where}: notices= is not a list literal")
                continue
            for element in value.elts:
                if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                    offenders.append(f"{where}: a notice is built from a value, not written")
    return offenders


def _notice_arguments():
    """Every `notices=` keyword in every migration module, with its module."""
    for path in sorted(MIGRATIONS_DIR.glob("v*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "notices":
                        yield path, node.lineno, keyword.value


def test_a_notice_must_be_a_literal_written_at_the_call_site():
    """The load-bearing constraint.

    A notice is only meaningful because it cannot depend on the data. An
    f-string, a `.append` in a loop, or a value derived from a query is a
    finding wearing a notice's label - and filing findings as notices is how
    `--fail-on-warning` would quietly stop meaning anything.
    """
    offenders = []
    for path in sorted(MIGRATIONS_DIR.glob("v*.py")):
        offenders.extend(
            _notice_offenders(path.read_text(encoding="utf-8-sig"), path.name)
        )

    assert not offenders, (
        "a notice must be a constant property of the migration; these are "
        "derived from something:\n  " + "\n  ".join(offenders)
    )


def test_the_boundary_check_actually_reads_the_migrations():
    """Self-check: a walker that finds nothing satisfies the assertion above."""
    found = list(_notice_arguments())
    assert found, "no notices= arguments found; this gate has stopped reading the tree"
    assert any(DOCUMENTARY_MIGRATION in path.name for path, _, _ in found)


@pytest.mark.parametrize(
    "source, reason",
    [
        pytest.param(
            "MigrationResult(version=v, name=n, status=s, notices=[f'{n} rows found'])",
            "an f-string",
            id="f-string",
        ),
        pytest.param(
            "notices = []\n"
            "for row in rows:\n"
            "    notices.append('x')\n"
            "MigrationResult(version=v, name=n, status=s, notices=notices)",
            "a list built in a loop",
            id="built-in-a-loop",
        ),
        pytest.param(
            "MigrationResult(version=v, name=n, status=s, notices=list(record['issues']))",
            "a value read from data",
            id="read-from-data",
        ),
    ],
)
def test_the_boundary_check_rejects_a_derived_notice(source, reason):
    """Negative controls for the checker itself, over the same function the
    real gate runs. A checker proved on a copy of itself proves nothing."""
    offenders = _notice_offenders(source, "synthetic")

    assert offenders, f"{reason} was accepted as a constant notice"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "MigrationResult(version=v, name=n, status=s, notices=['by design'])",
            id="inline-literal",
        ),
        pytest.param(
            "notices = ['by design', 'and this']\n"
            "MigrationResult(version=v, name=n, status=s, notices=notices)",
            id="bound-once-to-a-literal",
        ),
    ],
)
def test_the_boundary_check_accepts_a_written_notice(source):
    """The positive control. Without it every assertion above is satisfied by a
    checker that rejects everything."""
    assert _notice_offenders(source, "synthetic") == []


# --- 4. The documented command, end to end ----------------------------------


def test_the_documented_gate_reports_notices_without_failing_on_them():
    """The runner's decision is one thing; the command's is what the runbook
    invokes. This drives `migrate_database.py` itself with a stubbed database,
    so `--fail-on-warning`'s exit code is observed rather than reasoned about.
    """
    script = (
        "import asyncio, sys, types\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'backend')!r})\n"
        "import rbac_backend.core.database as database\n"
        "from rbac_backend.migrations import Migration, MigrationResult\n"
        "import rbac_backend.migrations.catalog as catalog\n"
        "\n"
        "class _C:\n"
        "    def __init__(self): self.docs = {}\n"
        "    def find(self, q=None):\n"
        "        class _Cur:\n"
        "            async def to_list(self, length=None): return []\n"
        "        return _Cur()\n"
        "    async def create_index(self, keys, **kw): return 'i'\n"
        "    async def update_one(self, q, u, upsert=False): return None\n"
        "class _DB:\n"
        "    def __init__(self): self.c = {}\n"
        "    def __getitem__(self, n): return self.c.setdefault(n, _C())\n"
        "    def __getattr__(self, n): return self[n]\n"
        "\n"
        "async def _upgrade(db, dry_run):\n"
        "    return MigrationResult(version='29990101_0001', name='synthetic',\n"
        "                           status='dry_run', notices=['by design'])\n"
        "catalog.MIGRATIONS = [Migration('29990101_0001', 'synthetic', 'd', _upgrade)]\n"
        "async def _get_database(): return _DB()\n"
        "async def _disconnect(): return None\n"
        "database.get_database = _get_database\n"
        "database.disconnect = _disconnect\n"
        "import rbac_backend.scripts.migrate_database as m\n"
        "m.get_database = _get_database\n"
        "m.disconnect = _disconnect\n"
        "sys.argv = ['migrate_database', '--fail-on-warning']\n"
        "m.main()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )

    assert result.returncode == 0, result.stderr
    assert "NOTICE" in result.stderr
    assert "by design" in result.stderr
