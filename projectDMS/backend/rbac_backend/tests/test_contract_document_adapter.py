"""ContractDocument as a generic relationship target — project-scoped only.

Owned by implementation ticket 06. It extends the A-27 / A-28 characterization
line into the new target type.

The invariant: a **project-scoped** contract instrument may carry supporting
documents through the existing relationship service; an **organisation-scoped**
one may not — and the refusal comes from the generic service unchanged, not from
a Contract-specific bypass.

Why organisation-scoped is refused even when it is currently applicable to
exactly one project: legal applicability and generic document-link ownership are
different questions. Applicability asks *"does this instrument govern this
contract?"*; the relationship service asks *"may this entity own links inside
this project's document graph?"*. Borrowing a project from applicability would
make the target's project time-varying while link identity assumes it fixed — so
withdrawing that applicability would leave the link's ownership undefined.

Assertions are on the service's final outcome — the refusal a caller actually
receives, the context the service actually loads — never on whether an adapter
helper returned a particular value.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.models.contract_document import ContractScopeLevel
from rbac_backend.services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from rbac_backend.services.entity_adapter_registry import EntityAdapterRegistry

ORG = "org-1"
PROJECT = "project-1"
TARGET_TYPE = "contract_document"


# --------------------------------------------------------------------------- #
# Minimal fakes — only what the adapter and the DRS guard actually read
# --------------------------------------------------------------------------- #


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


class _Database:
    def __init__(self, contract_documents: List[Dict[str, Any]]) -> None:
        self.contract_documents = _Collection(contract_documents)

    def __getitem__(self, name: str) -> _Collection:
        if name == "contract_documents":
            return self.contract_documents
        return _Collection([])


class _Policy:
    """Permissive by design: a permission grant must never be what saves us here.

    If the refusal depended on authorisation rather than on structural scope,
    this stub would let the illegal case through and the test would fail — which
    is the point.
    """

    async def authorize(self, *a: Any, **k: Any) -> None:
        return None

    async def authorize_document(self, *a: Any, **k: Any) -> None:
        return None


def _instrument(
    contract_document_id: str,
    *,
    scope: ContractScopeLevel,
    scope_project_id: Optional[str],
    organization_id: str = ORG,
) -> Dict[str, Any]:
    return {
        "_id": contract_document_id,
        "organization_id": organization_id,
        "document_id": "doc-canonical",
        "scope_level": scope.value,
        "scope_project_id": scope_project_id,
        "contract_document_type": "general_conditions",
        "classification_revision": 1,
    }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _adapter():
    return EntityAdapterRegistry().get(TARGET_TYPE)


# --------------------------------------------------------------------------- #
# Registration and role vocabulary
# --------------------------------------------------------------------------- #


def test_contract_document_target_type_is_registered() -> None:
    adapter = _adapter()
    assert adapter.target_type == TARGET_TYPE


def test_adapter_does_not_support_freeze() -> None:
    assert _adapter().supports_freeze is False


def test_only_supporting_document_role_is_allowed() -> None:
    """`primary_document` must not reappear.

    Canonical instrument identity is ContractDocument.document_id — a direct
    field. A second identity expressed as a link row would be a second thing to
    keep in sync.
    """
    db = _Database([_instrument("cd-1", scope=ContractScopeLevel.PROJECT, scope_project_id=PROJECT)])
    context = _run(_adapter().load(db, "cd-1"))
    assert context is not None
    assert set(context.allowed_roles) == {"supporting_document"}
    assert "primary_document" not in context.allowed_roles


# --------------------------------------------------------------------------- #
# Project-scoped: permitted, with the STRUCTURAL anchor
# --------------------------------------------------------------------------- #


def test_project_scoped_instrument_loads_with_its_structural_anchor() -> None:
    db = _Database([_instrument("cd-1", scope=ContractScopeLevel.PROJECT, scope_project_id=PROJECT)])
    context = _run(_adapter().load(db, "cd-1"))

    assert context is not None
    assert context.organization_id == ORG
    assert context.project_id == PROJECT
    assert context.target_type == TARGET_TYPE
    assert context.target_id == "cd-1"


def test_project_scoped_instrument_passes_the_generic_scope_guard() -> None:
    """Final-state: the real service accepts the target and moves past the guard.

    It later fails for an unrelated reason (no such document), which is exactly
    what proves the scope guard was cleared rather than skipped.
    """
    db = _Database([_instrument("cd-1", scope=ContractScopeLevel.PROJECT, scope_project_id=PROJECT)])
    service = DocumentRelationshipService(db, policy=_Policy())

    with pytest.raises(DocumentRelationshipError) as excinfo:
        _run(_link(service, "cd-1"))

    assert "organization and project scope" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Organisation-scoped: refused by the GENERIC guard, unchanged
# --------------------------------------------------------------------------- #


def test_organisation_scoped_instrument_loads_without_a_project() -> None:
    db = _Database(
        [_instrument("cd-org", scope=ContractScopeLevel.ORGANIZATION, scope_project_id=None)]
    )
    context = _run(_adapter().load(db, "cd-org"))
    assert context is not None
    assert context.project_id == ""


def test_organisation_scoped_instrument_is_refused_by_the_generic_guard() -> None:
    """The refusal is the EXISTING generic project-less refusal, 409.

    No Contract-specific gate was added, and none was needed.
    """
    db = _Database(
        [_instrument("cd-org", scope=ContractScopeLevel.ORGANIZATION, scope_project_id=None)]
    )
    service = DocumentRelationshipService(db, policy=_Policy())

    with pytest.raises(DocumentRelationshipError) as excinfo:
        _run(_link(service, "cd-org"))

    assert excinfo.value.status_code == 409
    assert "organization and project scope" in str(excinfo.value)


@pytest.mark.parametrize("applicability_count", [1, 2, 5])
def test_organisation_scoped_is_refused_at_any_applicability_count(
    applicability_count: int,
) -> None:
    """The load-bearing negative, including the tempting count of exactly one.

    "It applies to precisely one project, so just use that project" is the
    borrowing this design refuses: the applicability can be withdrawn, and the
    link's ownership would then be undefined.
    """
    record = _instrument("cd-org", scope=ContractScopeLevel.ORGANIZATION, scope_project_id=None)
    record["applicability_count_hint"] = applicability_count
    db = _Database([record])
    service = DocumentRelationshipService(db, policy=_Policy())

    with pytest.raises(DocumentRelationshipError) as excinfo:
        _run(_link(service, "cd-org"))

    assert excinfo.value.status_code == 409


def test_organisation_scoped_does_not_borrow_a_project_even_if_one_is_stored() -> None:
    """A stray project value on an organisation-scoped record is not an anchor.

    Scope level is the discriminator; a leftover field must not become ownership.
    """
    db = _Database(
        [
            _instrument(
                "cd-org", scope=ContractScopeLevel.ORGANIZATION, scope_project_id=PROJECT
            )
        ]
    )
    context = _run(_adapter().load(db, "cd-org"))
    assert context is not None
    assert context.project_id == "", (
        "an organisation-scoped instrument borrowed a project anchor"
    )


# --------------------------------------------------------------------------- #
# Server-loaded scope wins over anything a caller supplies
# --------------------------------------------------------------------------- #


def test_target_scope_comes_from_the_canonical_record_not_the_caller() -> None:
    """The adapter is given only an id. There is no caller-supplied scope to
    trust, by construction."""
    db = _Database(
        [
            _instrument(
                "cd-1",
                scope=ContractScopeLevel.PROJECT,
                scope_project_id="project-A",
                organization_id="org-A",
            )
        ]
    )
    context = _run(_adapter().load(db, "cd-1"))
    assert context is not None
    assert context.organization_id == "org-A"
    assert context.project_id == "project-A"


def test_unknown_target_id_loads_nothing() -> None:
    db = _Database([])
    assert _run(_adapter().load(db, "cd-missing")) is None


# --------------------------------------------------------------------------- #
# The adapter writes nothing
# --------------------------------------------------------------------------- #


def test_loading_a_target_does_not_mutate_the_instrument() -> None:
    """Linking is not a legal act: it must not touch applicability,
    classification, or the canonical document pointer."""
    record = _instrument("cd-1", scope=ContractScopeLevel.PROJECT, scope_project_id=PROJECT)
    db = _Database([record])
    before = dict(record)

    _run(_adapter().load(db, "cd-1"))

    assert db.contract_documents.rows[0] == before
    assert db.contract_documents.rows[0]["document_id"] == "doc-canonical"
    assert db.contract_documents.rows[0]["classification_revision"] == 1


# --------------------------------------------------------------------------- #


async def _link(service: DocumentRelationshipService, target_id: str) -> Any:
    from rbac_backend.models.document_relationship import DocumentRelationshipInput

    actor = type("Actor", (), {"id": "user-1", "roles": ["orgadmin"]})()
    return await service.link_batch(
        actor,
        TARGET_TYPE,
        target_id,
        [DocumentRelationshipInput(document_id="doc-support", relationship_role="supporting_document")],
        source="manual",
    )
