"""Cold-start readiness for the backfill transaction.

The backfill commits the canonical relationship AND its audit event in one
transaction. MongoDB will create a missing namespace implicitly inside a
transaction, but two transactions cannot create the SAME namespace at once: the
loser aborts with a WriteConflict carrying `TransientTransactionError`. On a
database where one of those collections has never been written, two concurrent
first-use backfills can therefore lose a migration to a namespace race that has
nothing whatever to do with the data.

A real deployment already has the guarantee — `core.database.ensure_indexes`
creates the `audit_events` indexes, and migration v20260820_0001 creates
`entity_document_links`. The real-Mongo harness runs only the single
relationship migration, so it declares the participants itself.

These tests keep that declaration honest from both directions:

  * it may not name a collection the real bootstrap does not own (otherwise the
    harness would be inventing infrastructure production lacks); and
  * it must name every collection the transaction actually writes (otherwise the
    race comes back through whichever one was forgotten).

Neither direction is hand-maintained: both are derived from the source.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from rbac_backend.tests.integration.test_insurance_document_relationships_mongo import (
    TRANSACTION_PARTICIPANT_COLLECTIONS,
)

BACKEND = Path(__file__).resolve().parent.parent
BOOTSTRAP = BACKEND / "core" / "database.py"
MIGRATIONS = BACKEND / "migrations"
RELATIONSHIP_SERVICE = BACKEND / "services" / "document_relationship_service.py"
AUDIT_SERVICE = BACKEND / "services" / "audit_event_service.py"


def _bootstrapped_collections() -> set[str]:
    """Collections the repository's own initialization creates.

    `create_index` creates the namespace, which is exactly the property that
    matters here, so index creation counts as ownership.
    """
    owned: set[str] = set()

    def scan(path: Path) -> None:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"create_index", "create_indexes", "create_collection"}:
                continue
            owner = func.value
            # db.<name>.create_index(...)
            if isinstance(owner, ast.Attribute):
                owned.add(owner.attr)
            # db["<name>"].create_index(...)
            elif isinstance(owner, ast.Subscript):
                index = owner.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    owned.add(index.value)
        # `COLLECTION = "name"` module constants used with db[COLLECTION]
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in {"COLLECTION"}
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                owned.add(node.value.value)

    scan(BOOTSTRAP)
    for path in sorted(MIGRATIONS.glob("v*.py")):
        scan(path)
    return owned


#: Only writes create a namespace. A `find_one(..., session=session)` inside the
#: transaction reads an absent collection quite happily, so counting reads here
#: would demand pre-creation of collections that never race.
WRITE_OPERATIONS = frozenset(
    {
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "find_one_and_update",
        "find_one_and_replace",
        "find_one_and_delete",
        "bulk_write",
    }
)


def _transaction_write_targets() -> set[str]:
    """Collections WRITTEN with `session=session` inside the relationship
    transaction, derived from the service rather than remembered."""
    targets: set[str] = set()
    tree = ast.parse(io.open(RELATIONSHIP_SERVICE, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(keyword.arg == "session" for keyword in node.keywords):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        # self.db.<collection>.<write>(..., session=...)
        if (
            func.attr in WRITE_OPERATIONS
            and isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Attribute)
            and owner.value.attr == "db"
        ):
            targets.add(owner.attr)
        # self.audit.emit(..., session=...) writes the audit collection
        if isinstance(owner, ast.Attribute) and owner.attr == "audit":
            targets.add(_audit_collection_name())
    return targets


def _audit_collection_name() -> str:
    """The collection the audit service inserts into, read from its source."""
    tree = ast.parse(io.open(AUDIT_SERVICE, encoding="utf-8").read())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value.startswith("audit")
        ):
            return node.args[1].value
    raise AssertionError("could not determine the audit collection from source")


def test_the_audit_collection_is_resolved_from_source() -> None:
    """Guard the derivation: a silent failure here would make the coverage
    check below pass while measuring nothing."""
    assert _audit_collection_name() == "audit_events"


def test_transaction_write_targets_are_discoverable() -> None:
    targets = _transaction_write_targets()
    assert "entity_document_links" in targets, targets
    assert "audit_events" in targets, targets


def test_every_transaction_participant_is_owned_by_the_real_bootstrap() -> None:
    """The harness may not invent infrastructure production does not have."""
    owned = _bootstrapped_collections()
    unowned = [
        name for name in TRANSACTION_PARTICIPANT_COLLECTIONS if name not in owned
    ]
    assert unowned == [], (
        "The real-Mongo harness pre-creates collections the repository's own "
        f"bootstrap never creates: {unowned}"
    )


def test_the_harness_covers_every_transaction_participant() -> None:
    """And it may not forget one, or the namespace race returns through it."""
    missing = sorted(
        _transaction_write_targets() - set(TRANSACTION_PARTICIPANT_COLLECTIONS)
    )
    assert missing == [], (
        "These collections are written inside the relationship transaction but "
        f"are not guaranteed to exist before concurrent first use: {missing}"
    )
