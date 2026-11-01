"""Contract Master v1 — characterization safety net (Wayfinder CM-01).

Pins the CURRENT behaviour that Contract Master implementation could break.
This suite changes no production behaviour and asserts no desired future
behaviour. Three kinds of fact live here, deliberately labelled so they are
never confused:

  * ``CURRENT INVARIANT``  — good behaviour today; a permanent regression
    contract. Breaking one of these is a defect.
  * ``KNOWN LIMITATION``   — real current deficiency, reproduced honestly so a
    later ticket can fix it. NOT an accepted invariant.
  * ``ABSENCE``            — a seam Contract Master must add deliberately, so a
    future implementation cannot quietly reuse a weaker unrelated one.

Correction pinned by C09-C12: an earlier architecture note claimed publication
authority ran *after* fusion/count/topK in `ContractService`. That was wrong.
`blocked_document_ids` is applied at `contract_service.py:1001-1016`, before
RRF fusion, ranking, `total_count` and pagination. These tests pin that good
behaviour so it cannot regress.
"""

from __future__ import annotations

import ast
import inspect
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

BACKEND = Path(__file__).resolve().parent.parent
CONTRACT_SERVICE_SRC = BACKEND / "services" / "contract_service.py"
CONTRACTS_INGEST_SRC = BACKEND / "services" / "contracts_ingest.py"


# --------------------------------------------------------------------------
# Faithful fakes.
#
# The shared `_Collection` in test_claim_document_relationships exposes
# `sort(field, direction)`, but `_assemble_results` calls
# `.find(...).sort([("chunk_index", 1)])` with a LIST of pairs. Bending the
# shared double to fit would weaken it for every other suite, so this file
# carries a small fake that models the calls this seam actually makes.
# --------------------------------------------------------------------------


class _Cursor:
    """Models both cursor idioms this seam uses.

    `_assemble_results` calls `.sort([...]).to_list(...)`, while
    `blocked_document_ids` iterates with `async for`. A fake supporting only one
    would make the authority tests pass without ever running the real filter.
    """

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def sort(self, spec):
        for field, direction in reversed(list(spec)):
            self.rows.sort(key=lambda r: r.get(field) or 0, reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(list(self.rows))
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _ClauseCollection:
    """Models `contract_clauses`/`document_vectors` for the hydration read."""

    def __init__(self, rows: List[Dict[str, Any]], database: Any) -> None:
        self.rows = rows
        self.database = database

    def find(self, query: Dict[str, Any]):
        clauses = query.get("$or") or [query]
        matched = [
            row
            for row in self.rows
            if any(all(row.get(k) == v for k, v in c.items()) for c in clauses)
        ]
        return _Cursor(matched)


class _DocumentsCollection:
    """Only what `blocked_document_ids` actually reads."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any], *args: Any, **kwargs: Any):
        ids = (query.get("_id") or {}).get("$in")
        matched = [row for row in self.rows if ids is None or row.get("_id") in ids]
        return _Cursor([dict(row) for row in matched])


class _Database:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self.documents = _DocumentsCollection(documents)


def _document(document_id: str, *, consumable: bool) -> Dict[str, Any]:
    row = {
        "_id": document_id,
        "organization_id": "org-1",
        "project_id": "project-1",
        "lifecycle_state": "active",
        "duplicate_status": "unique",
        "processing_status": "metadata_extracted",
    }
    if not consumable:
        # human_review_required is the ONLY adverse processing state today
        # (publication_policy.ADVERSE_STATES).
        row["processing_status"] = "human_review_required"
    return row


def _clause_row(document_id: str, clause_number: str, position: int) -> Dict[str, Any]:
    return {
        "uploadType": "contract",
        "document_id": document_id,
        "clause_number": clause_number,
        "clause_start_position": position,
        "chunk_index": position,
        "text": f"clause {clause_number} of {document_id}",
        "organization_id": "org-1",
        "project_id": "project-1",
    }


def _lexical(document_id: str, clause_number: str, position: int, score: float):
    return {
        "document_id": document_id,
        "clause_number": clause_number,
        "clause_start_position": position,
        "best_score": score,
    }


def _vector(document_id: str, clause_number: str, position: int, score: float):
    return {
        "payload": {
            "document_id": document_id,
            "clause_number": clause_number,
            "clause_start_position": position,
        },
        "score": score,
    }


def _graph(document_id: str, clause_number: str, position: int, score: float):
    return {
        "document_id": document_id,
        "clause_number": clause_number,
        "clause_start_position": position,
        "score": score,
        "graph_relation": "references",
    }


def _service():
    from rbac_backend.services.contract_service import ContractService

    return ContractService.__new__(ContractService)


async def _assemble(service, collection, lexical, vector, graph, page_size=10, skip=0):
    return await service._assemble_results(
        collection, lexical, vector, graph, page_size, skip
    )


# ==========================================================================
# C01 / C02 — CURRENT INVARIANT: Contract identity is (project_id, contract_id)
# ==========================================================================


@pytest.mark.asyncio
async def test_c01_contract_master_writes_are_scoped_by_project_and_contract() -> None:
    """CURRENT INVARIANT. `contract_id` is never a global key.

    Behavioural, not structural: an earlier AST version of this test passed
    while the real scope query had dropped `contract_id`, because some other
    dict in the class still mentioned both names. This captures the query the
    service actually issues.
    """
    from rbac_backend.services.contract_master_service import ContractMasterService

    seen: Dict[str, Any] = {}

    class _Collection:
        async def update_one(self, query, update, *args, **kwargs):
            seen.update(query)
            return SimpleNamespace(matched_count=1, modified_count=1)

    class _Db:
        contract_master = _Collection()

    service = ContractMasterService.__new__(ContractMasterService)

    async def _get_db():
        return _Db()

    service._get_db = _get_db

    await service.sync_current_value("org-1", "project-a", "package-2", 1234.0)

    assert seen.get("project_id") == "project-a"
    assert seen.get("contract_id") == "package-2", (
        "Contract Master scope query no longer carries contract_id; Contract "
        "identity has collapsed to the Project"
    )
    assert seen.get("organization_id") == "org-1"


def test_c01_sibling_projects_do_not_share_a_primary_contract() -> None:
    """CURRENT INVARIANT. Two Projects may each hold `contract_id='primary'`
    and remain independent — the pair is the identity, not the suffix."""
    from rbac_backend.models.contract_master import ContractMasterBase

    a = ContractMasterBase(organization_id="org-1", project_id="project-a")
    b = ContractMasterBase(organization_id="org-1", project_id="project-b")

    assert a.contract_id == b.contract_id == "primary"
    assert (a.project_id, a.contract_id) != (b.project_id, b.contract_id)


def test_c02_contract_id_is_a_default_not_a_uniqueness_assumption() -> None:
    """CURRENT INVARIANT. One Project may carry several contract ids."""
    from rbac_backend.models.contract_master import ContractMasterBase

    primary = ContractMasterBase(organization_id="org-1", project_id="project-a")
    second = ContractMasterBase(
        organization_id="org-1", project_id="project-a", contract_id="package-2"
    )

    assert primary.contract_id == "primary"
    assert second.contract_id == "package-2"
    assert primary.project_id == second.project_id


def test_c02_contract_master_create_still_requires_a_project() -> None:
    """CURRENT INVARIANT. Contract is project-scoped at creation (v1 keeps it)."""
    from pydantic import ValidationError

    from rbac_backend.models.contract_master import ContractMasterCreate

    with pytest.raises(ValidationError):
        ContractMasterCreate(organization_id="org-1", project_id="")


# ==========================================================================
# C03 — CURRENT INVARIANT: canonical Document cannot express project-null
# ==========================================================================


def test_c03_document_project_id_defaults_to_empty_string_and_rejects_none() -> None:
    """CURRENT INVARIANT. `Document.project_id` is `str` with default `""` and
    REJECTS None. Contract Master must not assume a nullable project on the
    canonical model."""
    from datetime import datetime

    from pydantic import ValidationError

    from rbac_backend.models.document import Document

    base = dict(
        organization_id="org-1",
        filename="GCC.pdf",
        filetype="application/pdf",
        filesize=1,
        uploadType="contract",
        date=datetime.utcnow(),
        subject="GCC",
        status="queued",
        createdBy="user-1",
    )

    assert Document(**base).project_id == ""
    assert Document(**base, project_id="").project_id == ""
    with pytest.raises(ValidationError):
        Document(**base, project_id=None)


# ==========================================================================
# C04 / C05 — CURRENT INVARIANT: generic fail-closed boundaries
# ==========================================================================


def test_c04_relationship_service_refuses_project_less_documents() -> None:
    """CURRENT INVARIANT. The accepted relationship/backfill programme depends
    on this refusal catching null, "" and missing alike."""
    from rbac_backend.services.document_relationship_service import (
        DocumentRelationshipService,
    )

    source = inspect.getsource(DocumentRelationshipService._document)
    assert "if not project_id:" in source
    assert "project-null Documents" in source
    # The coercion is what makes null / "" / missing behave identically.
    assert 'or ""' in source


def test_c05_linkable_document_search_excludes_null_and_empty_project() -> None:
    """CURRENT INVARIANT. Linkable search fails closed on BOTH representations."""
    from rbac_backend.routers.documents import _apply_linkable_document_constraints

    constrained = _apply_linkable_document_constraints({})

    assert constrained["project_id"] == {"$nin": [None, ""]}
    assert constrained["duplicate_status"] == {"$ne": "duplicate"}
    assert "human_review_required" in constrained["processing_status"]["$nin"]


# ==========================================================================
# C06 / C07 / C08 — CURRENT premise for future migration evidence
# ==========================================================================


def test_c06_organisation_level_contract_upload_path_exists() -> None:
    """CURRENT INVARIANT (premise, not a defect). An organisation-role actor
    with no project assignment resolves to NO project — the deliberate
    organisation-level upload path."""
    service = _service()
    actor = SimpleNamespace(
        id="org-admin", organization_id="org-1", roles=["orgadmin"], projects=[]
    )

    org, project, filters = service._resolve_scope(actor, "org-1", None)

    assert org == "org-1"
    assert project is None
    assert filters is None


def test_c06_project_role_actor_cannot_reach_the_org_level_path() -> None:
    """CURRENT INVARIANT. Only org-authorised actors reach project-less scope."""
    from rbac_backend.services.contract_service import ContractError

    service = _service()
    actor = SimpleNamespace(
        id="proj-user", organization_id="org-1", roles=["projectuser"], projects=[]
    )

    with pytest.raises(ContractError):
        service._resolve_scope(
            actor, "org-1", None, require_project_for_project_roles=True
        )


def test_c07_contract_created_audit_passes_the_raw_project_scope() -> None:
    """CURRENT INVARIANT — LOAD-BEARING MIGRATION EVIDENCE.

    `_create_document` writes `project_id or ""` onto the Document, collapsing
    None and "". The audit emit must keep the RAW value, because that null is
    the only durable positive signal that the organisation-level path was taken
    (see ADR 0005). Coercing it here would destroy the migration evidence.
    """
    source = inspect.getsource(CONTRACT_SERVICE_SRC.read_text(encoding="utf-8").__class__) if False else CONTRACT_SERVICE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    emits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            kw.arg == "event_type"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "contract.created"
            for kw in node.keywords
        )
    ]
    assert emits, "contract.created audit emit not found"

    project_kwargs = [
        kw for emit in emits for kw in emit.keywords if kw.arg == "project_id"
    ]
    assert project_kwargs, "contract.created no longer records project scope"
    for kw in project_kwargs:
        assert isinstance(kw.value, ast.Name) and kw.value.id == "project_id", (
            "contract.created must pass the RAW project_id; a coerced value "
            "(e.g. `project_id or \"\"`) would destroy ADR 0005 migration evidence"
        )


def test_c08_upload_sessions_are_ttl_backed_and_not_durable_provenance() -> None:
    """CURRENT INVARIANT (a limitation of the source, pinned as a premise).

    Upload sessions self-delete, so they can never be durable scope provenance
    for migration. Pinned so a future migration cannot be designed around them.
    """
    source = CONTRACT_SERVICE_SRC.read_text(encoding="utf-8")
    assert "expireAfterSeconds=0" in source
    assert '"expiresAt"' in source
    # And the session stores the SAME collapsed value, so even a live session
    # cannot distinguish deliberate org scope from unknown project.
    assert '"project_id": effective_project or ""' in source


# ==========================================================================
# C09 - C12 — CURRENT INVARIANT: publication authority precedes fusion,
# ranking, count and pagination. This is the corrected claim.
# ==========================================================================


@pytest.mark.asyncio
async def test_c09_blocked_candidate_is_removed_before_fusion_influence() -> None:
    """CURRENT INVARIANT. A blocked Document contributes no fused result."""
    database = _Database(
        [_document("doc-ok", consumable=True), _document("doc-blocked", consumable=False)]
    )
    collection = _ClauseCollection(
        [_clause_row("doc-ok", "4.2", 10), _clause_row("doc-blocked", "13.3", 20)],
        database,
    )
    service = _service()

    rows, total, _ = await _assemble(
        service,
        collection,
        [_lexical("doc-ok", "4.2", 10, 1.0), _lexical("doc-blocked", "13.3", 20, 9.0)],
        [_vector("doc-blocked", "13.3", 20, 9.0)],
        [_graph("doc-blocked", "13.3", 20, 9.0)],
    )

    returned = {row.get("document_id") for row in rows}
    assert "doc-blocked" not in returned
    assert returned == {"doc-ok"}
    assert total == 1


@pytest.mark.asyncio
async def test_c10_blocked_candidate_does_not_inflate_total_count() -> None:
    """CURRENT INVARIANT. Directly protects the previously fixed defect where a
    governing clause yielded total_count=7 and ZERO rows in the prompt."""
    database = _Database(
        [_document("doc-ok", consumable=True), _document("doc-blocked", consumable=False)]
    )
    collection = _ClauseCollection(
        [_clause_row("doc-ok", "4.2", 10), _clause_row("doc-blocked", "13.3", 20)],
        database,
    )
    service = _service()

    rows, total, has_more = await _assemble(
        service,
        collection,
        [_lexical("doc-ok", "4.2", 10, 1.0), _lexical("doc-blocked", "13.3", 20, 9.0)],
        [],
        [],
    )

    assert total == 1, "total_count counted a blocked Document"
    assert len(rows) == 1
    assert has_more is False


@pytest.mark.asyncio
async def test_c11_blocked_candidate_does_not_distort_pagination() -> None:
    """CURRENT INVARIANT. With authority applied before slicing, page 1 of size
    1 returns the authorised clause, not an empty page behind a blocked one.

    Scores are set so the blocked candidate would rank FIRST if it survived,
    which is what makes this test about ordering rather than luck.
    """
    database = _Database(
        [
            _document("doc-ok-1", consumable=True),
            _document("doc-ok-2", consumable=True),
            _document("doc-blocked", consumable=False),
        ]
    )
    collection = _ClauseCollection(
        [
            _clause_row("doc-ok-1", "1.1", 10),
            _clause_row("doc-ok-2", "2.2", 20),
            _clause_row("doc-blocked", "9.9", 30),
        ],
        database,
    )
    service = _service()

    rows, total, has_more = await _assemble(
        service,
        collection,
        [
            _lexical("doc-blocked", "9.9", 30, 99.0),
            _lexical("doc-ok-1", "1.1", 10, 5.0),
            _lexical("doc-ok-2", "2.2", 20, 4.0),
        ],
        [],
        [],
        page_size=1,
        skip=0,
    )

    assert total == 2
    assert has_more is True
    assert len(rows) == 1
    assert rows[0]["document_id"] == "doc-ok-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["lexical", "vector", "graph"])
async def test_c12_every_candidate_path_shares_the_authority_filter(source) -> None:
    """CURRENT INVARIANT. Lexical, vector and graph candidates are filtered by
    the SAME call — the three stacks cannot drift apart."""
    database = _Database([_document("doc-blocked", consumable=False)])
    collection = _ClauseCollection([_clause_row("doc-blocked", "13.3", 20)], database)
    service = _service()

    lexical = [_lexical("doc-blocked", "13.3", 20, 9.0)] if source == "lexical" else []
    vector = [_vector("doc-blocked", "13.3", 20, 9.0)] if source == "vector" else []
    graph = [_graph("doc-blocked", "13.3", 20, 9.0)] if source == "graph" else []

    rows, total, _ = await _assemble(service, collection, lexical, vector, graph)

    assert rows == []
    assert total == 0, f"{source} candidates bypassed the publication authority filter"


def test_c12_post_hydration_defence_in_depth_pass_is_present() -> None:
    """CURRENT INVARIANT — STRUCTURAL PIN, deliberately not behavioural.

    `_assemble_results` re-checks authority on the hydrated rows after the
    pre-fusion filter, to contain a chunk whose `document_id` the candidate
    metadata did not carry. That drift arises from `_coerce_id` normalising
    ObjectId and string forms, so reproducing it faithfully needs real bson
    identity semantics rather than a dict fake.

    An earlier attempt at a behavioural test for this was VACUOUS: the fake
    collection matched hydration on `document_id`, so the drifted row was never
    returned and the assertion passed by construction. It was removed rather
    than fabricated into something that looked like proof.

    COVERAGE GAP, recorded for a later ticket: the post-hydration pass has no
    behavioural coverage in this suite. This structural pin catches accidental
    deletion only. Closing it properly needs real Mongo with ObjectId ids.
    """
    source = CONTRACT_SERVICE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    authority_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "blocked_document_ids"
    ]
    assert len(authority_calls) >= 2, (
        "the post-hydration defence-in-depth authority pass has been removed; "
        "only the pre-fusion filter remains"
    )


# ==========================================================================
# KNOWN LIMITATION — blocked candidates consume bounded candidate capacity.
# NOT an accepted invariant. Owned by Wayfinder tickets 06/07.
# ==========================================================================


def test_known_limitation_candidate_limit_is_computed_before_authority() -> None:
    """KNOWN LIMITATION — NOT DESIRED BEHAVIOUR. Owned by tickets 06/07.

    `candidate_limit` bounds the lexical/vector/graph fetches BEFORE
    `blocked_document_ids` runs, so a blocked Document occupies a candidate slot
    and can crowd out an authorised clause that would otherwise have entered the
    set. Authority ordering after retrieval is correct (C09-C12); candidate
    GENERATION is not authority-aware.

    This test asserts the CURRENT ordering so the day someone fixes it, this
    test fails and is deleted deliberately — it must never be read as approval.
    """
    source = CONTRACT_SERVICE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    limit_line = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "candidate_limit" for t in node.targets
        )
    )
    authority_line = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "blocked_document_ids"
    )

    assert limit_line < authority_line, (
        "Candidate generation appears to have become authority-aware. If that is "
        "intended, delete this KNOWN LIMITATION test and close the gap in "
        "Wayfinder tickets 06/07."
    )


# ==========================================================================
# ABSENCE — Contract Master seams that do not exist yet
# ==========================================================================


def test_contract_target_is_exactly_one_deliberate_adapter() -> None:
    """REPLACED by implementation ticket 06, as this test's own ABSENCE version
    instructed once a contract target appeared.

    The original pinned that no contract target existed, and warned that Contract
    Master "must add a `contract_document` target deliberately; it must not reuse
    an unrelated existing adapter". Ticket 06 added exactly that adapter, so the
    absence is retired — but the guard it carried is kept, in a stronger positive
    form: there is exactly ONE contract target, it is named `contract_document`,
    and it permits only the supporting-document role.

    That still catches the failure the absence was watching for: a second contract
    target, a renamed one, or an instrument-identity role sneaking back in.
    """
    from rbac_backend.services.entity_adapter_registry import EntityAdapterRegistry

    registry = EntityAdapterRegistry()
    registered = set(getattr(registry, "_adapters", {}).keys())

    assert registered, "adapter registry is empty — fixture is wrong, not the code"
    contract_targets = {name for name in registered if "contract" in name}
    assert contract_targets == {"contract_document"}, (
        f"expected exactly one deliberate contract target, found {contract_targets}"
    )

    adapter = registry.get("contract_document")
    assert adapter.supports_freeze is False
    from rbac_backend.services.entity_adapter_registry import CONTRACT_DOCUMENT_ROLES

    assert set(CONTRACT_DOCUMENT_ROLES) == {"supporting_document"}, (
        "canonical instrument identity is ContractDocument.document_id; a second "
        "identity role must not reappear"
    )


def test_absence_no_applicability_or_scope_resolver_exists() -> None:
    """ABSENCE. Pins that the Contract Master vocabulary is genuinely new.

    NARROWED by implementation ticket 05, which owns
    ``services/contract_scope_resolver.py``. Two names it legitimately retires:

    * ``ContractScopeResolver`` — the module ticket 05 exists to build;
    * ``ContractDocumentType`` — consumed there to construct ApplicableInstrument.

    The remaining three are still absent from ``services/`` and stay pinned, so
    this test keeps catching the ones no ticket has built yet. It is narrowed
    rather than deleted: deleting it would stop guarding the other three.
    """
    names = [
        "ContractDocumentApplicability",
        "ContractDocumentLegalEffect",
        "ContractAnalysisProvenance",
    ]
    services = BACKEND / "services"
    found = {
        name: [
            path.name
            for path in services.rglob("*.py")
            if name in io.open(path, encoding="utf-8", errors="ignore").read()
        ]
        for name in names
    }
    assert all(not hits for hits in found.values()), f"already present: {found}"


# ==========================================================================
# Axis-2 classification: CURRENT technical debt, pinned as a premise
# ==========================================================================


def test_current_state_contract_uploads_carry_a_generic_document_type() -> None:
    """KNOWN LIMITATION (premise). Contract uploads store the generic
    `document_type="contract"`; there is no controlled instrument vocabulary,
    so future implementation cannot assume one exists."""
    source = CONTRACT_SERVICE_SRC.read_text(encoding="utf-8")
    assert 'uploadType="contract"' in source

    from rbac_backend.models.contract_clause import ContractClause

    field = ContractClause.model_fields["document_type"]
    assert field.default is None
    # Free text: no Literal / enum constraint today.
    assert "Literal" not in str(field.annotation)


def test_current_state_no_migration_constrains_instrument_vocabulary() -> None:
    """KNOWN LIMITATION (premise). Nothing enforces a controlled type."""
    migrations = BACKEND / "migrations"
    offenders = [
        path.name
        for path in migrations.glob("v*.py")
        if "ContractDocumentType" in io.open(path, encoding="utf-8", errors="ignore").read()
    ]
    assert offenders == []


# ==========================================================================
# Falkor priority premise — reproducible ground for Wayfinder ticket 11.
# KNOWN HIGH IMPLEMENTATION DEBT — OWNED BY TICKET 11. Not solved here.
# ==========================================================================


def test_falkor_premise_section_type_and_priority_are_filename_derived() -> None:
    """KNOWN HIGH IMPLEMENTATION DEBT — OWNED BY TICKET 11.

    Pins only the PREMISE so ticket 11's containment tracer is reproducible:
    contractual section type and precedence rank are derived from a filename
    substring and handed to the graph projection. This test does NOT assert that
    the priority is authoritative — the frozen architecture says it must not be.
    """
    from rbac_backend.services.contracts_ingest import ContractIngestor

    detect = ContractIngestor._detect_section_type
    assert detect("Volume 2 SCC particular conditions.pdf") == ("SCC", 1)
    assert detect("Volume 1 GCC general conditions.pdf") == ("GCC", 2)
    assert detect("unrelated annex.pdf") == (None, 5)

    source = CONTRACTS_INGEST_SRC.read_text(encoding="utf-8")
    assert "_detect_section_type(filename)" in source
    assert "DocumentGraphPayload(" in source
    assert "priority=priority" in source
