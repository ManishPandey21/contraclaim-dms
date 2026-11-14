"""F-A8M-5 - a seeder with no resolved target must refuse, not proceed.

R-A8M called ``initialize_all_data()`` with no argument. It returned

    {'permissions': 0, 'roles': 0, 'users': 0, 'organizations': 0, 'projects': 0}

and logged ``Failed to create project proj1: 'NoneType' object has no attribute
'projects'`` once per record. ``create_data_initializer(await get_database())``
then seeded 14 roles, 3 organizations and 3 projects from the same tree, so the
data was fine and the entry point was not.

Two mechanisms, and both had to go:

* ``if database is None: from ..core.database import database`` binds the module
  global **at import time** - ``None`` before anything connects, and still
  ``None`` afterwards, because the local name is a snapshot rather than a
  reference. It could not have worked at any point in the process's life.
* ``DataInitializer.__init__`` accepted that ``None`` and carried it to every
  write, where the per-record ``except Exception: continue`` swallowed the
  AttributeError. The run then reported success having seeded nothing - the
  house pattern the Qdrant 401 incident is named for.

Startup does not use the broken entry point, so no deployment was affected. That
is the reason to fix it rather than a reason not to: the next caller would have
been an operator running a repair by hand against production.

The contract now: an explicit handle is used exactly as given, and ``None`` is
**refused**. Resolving ``None`` through ``get_database()`` would work, and on a
production host it would work by seeding production - which is the outcome the
finding says must be impossible ("do not let None silently select a default
production DB"). A caller who wants this process's own connection writes

    await initialize_all_data(await get_database())

which is auditable in a way an omitted argument is not, and nothing depended on
the old behaviour because the old behaviour never worked.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import rbac_backend.core.database as database_module
from rbac_backend.core.database import UnresolvedDatabaseError, resolve_database
from rbac_backend.services.data_initialization import (
    DataInitializer,
    create_data_initializer,
)
from rbac_backend.services.setup_service import SetupService, create_setup_service

PACKAGE_ROOT = Path(database_module.__file__).resolve().parents[1]


class _Collection:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    async def find_one(self, query):
        return None

    async def insert_one(self, document):
        self.owner.writes.append((self.name, document))
        return SimpleNamespace(inserted_id=document.get("_id"))

    async def update_one(self, query, update, upsert: bool = False):
        self.owner.writes.append((self.name, update))


class _NamedDB:
    """A database handle that knows which database it is."""

    def __init__(self, name: str):
        self.name = name
        self.writes: list = []

    def __getitem__(self, collection: str):
        return _Collection(self, collection)

    def __getattr__(self, collection: str):
        if collection.startswith("_"):
            raise AttributeError(collection)
        return _Collection(self, collection)


@pytest.fixture()
def application_connection(monkeypatch):
    """Stand in for `get_database()`, the application's own resolver."""
    resolved = _NamedDB("configured-for-this-process")

    async def _get_database():
        return resolved

    monkeypatch.setattr(database_module, "get_database", _get_database)
    return resolved


# --- 1. None is refused, everywhere it used to be accepted -----------------


def test_the_initializer_refuses_an_unresolved_target():
    with pytest.raises(ValueError, match="requires a database handle"):
        DataInitializer(None)


def test_the_factory_refuses_an_unresolved_target():
    with pytest.raises(ValueError, match="requires a database handle"):
        create_data_initializer(None)


def test_the_setup_service_refuses_an_unresolved_target():
    with pytest.raises(ValueError, match="cannot be None"):
        create_setup_service(None)


async def test_an_omitted_target_is_refused_and_never_guessed(application_connection):
    """The load-bearing control.

    A configured connection IS available here - `application_connection` is
    installed - and the refusal fires anyway. That is the point: the failure
    mode being closed is not "no database exists", it is "a database exists and
    nobody said it was the one they meant". On a production host that database
    is production.
    """
    with pytest.raises(UnresolvedDatabaseError, match="will not pick one for you"):
        await resolve_database(None)

    assert application_connection.writes == [], "an omitted target still reached a database"


# --- 2. The handle that is given is the handle that is used ----------------


async def test_an_explicit_handle_is_used_exactly_as_given(application_connection):
    """A staging or test database passed in must be the one written to, and the
    application's own connection must not be consulted at all."""
    explicit = _NamedDB("contraclaim_staging")

    resolved = await resolve_database(explicit)

    assert resolved is explicit
    assert resolved.name == "contraclaim_staging"
    assert application_connection.writes == []


async def test_the_production_connection_is_not_reachable_by_omission(monkeypatch):
    """The named negative control: production DB accidental fallback -> RED.

    The configured connection is made to look like production. An omitted
    argument must not reach it, and must not reach anything else either.
    """
    production = _NamedDB("contraclaim")
    touched = []

    async def _get_database():
        touched.append("get_database")
        return production

    monkeypatch.setattr(database_module, "get_database", _get_database)

    with pytest.raises(UnresolvedDatabaseError):
        await resolve_database(None)

    assert production.writes == []
    assert touched == [], "the helper consulted a connection it should not have chosen"


async def test_the_module_level_helper_seeds_the_named_database_and_no_other(
    application_connection, monkeypatch
):
    """A named handle reaches the writes, and reaches only that handle."""
    from rbac_backend.services import data_initialization

    seen: list = []

    class _Recording(DataInitializer):
        async def initialize_roles(self):  # type: ignore[override]
            seen.append(self.database)
            await self.database.roles.insert_one({"_id": "role-1"})
            return 1

    monkeypatch.setattr(data_initialization, "DataInitializer", _Recording)

    created = await data_initialization.initialize_roles(application_connection)

    assert created == 1
    assert seen == [application_connection]
    assert application_connection.writes == [("roles", {"_id": "role-1"})]


async def test_the_module_level_helper_refuses_when_no_target_is_named(
    application_connection, monkeypatch
):
    """The entry point R-A8M measured. It used to return all zeros and report
    success; it must now refuse rather than choose."""
    from rbac_backend.services import data_initialization

    with pytest.raises(UnresolvedDatabaseError):
        await data_initialization.initialize_all_data()

    assert application_connection.writes == []


async def test_a_test_database_passed_explicitly_is_never_swapped_for_another(
    application_connection, monkeypatch
):
    """The negative control that matters. A caller who names a disposable
    database must not have the process's configured connection substituted for
    it - that is how a seed run lands somewhere nobody chose."""
    from rbac_backend.services import data_initialization

    disposable = _NamedDB("ra8n_disposable")
    seen: list = []

    class _Recording(DataInitializer):
        async def initialize_roles(self):  # type: ignore[override]
            seen.append(self.database)
            await self.database.roles.insert_one({"_id": "role-1"})
            return 1

    monkeypatch.setattr(data_initialization, "DataInitializer", _Recording)

    await data_initialization.initialize_roles(disposable)

    assert seen == [disposable]
    assert [name for name, _ in disposable.writes] == ["roles"]
    assert application_connection.writes == [], (
        "the configured connection was written to despite an explicit target"
    )


# --- 3. The mechanism cannot come back -------------------------------------


def _snapshot_importers() -> list:
    """Modules that import the `database` global by value.

    An AST walk rather than a text search: this file and `core/database.py`
    both quote the offending line in prose, and a grep-based gate would either
    flag the documentation or be weakened until it stopped flagging anything.
    """
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        parts = path.relative_to(PACKAGE_ROOT).parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = (node.module or "").split(".")[-1]
            if module != "database":
                continue
            for alias in node.names:
                if alias.name == "database":
                    offenders.append(
                        f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: "
                        "imports the `database` global by value"
                    )
    return offenders


def test_nothing_imports_the_database_global_by_value():
    """`from ...database import database` captures whatever the global held at
    import time. Before anything connects that is `None`, and it stays `None`
    for the life of the process however many times something else reassigns the
    global. Resolve through `get_database()` / `resolve_database()` instead."""
    offenders = _snapshot_importers()

    assert not offenders, (
        "these modules bind a snapshot of the connection global (F-A8M-5):\n  "
        + "\n  ".join(offenders)
    )


def test_the_snapshot_gate_reads_the_tree_and_can_still_see_the_shape():
    """Self-check plus negative control: the walker has to find modules at all,
    and it has to flag the exact import it exists to forbid."""
    modules = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(PACKAGE_ROOT).parts
    ]
    assert len(modules) > 100, "the gate is no longer reading the package"

    tree = ast.parse("from ..core.database import database\n")
    found = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("database")
        for alias in node.names
    ]
    assert found == ["database"], "the detector no longer recognises the forbidden import"


def test_a_docstring_quoting_the_shape_is_not_flagged():
    """`core/database.py` documents the defect by quoting the import. A gate that
    could not tell prose from code would force the explanation to be deleted."""
    source = (PACKAGE_ROOT / "core" / "database.py").read_text(encoding="utf-8")

    assert "from ..core.database import database" in source, (
        "the explanation of why this is forbidden has been removed"
    )
    assert not _snapshot_importers()
