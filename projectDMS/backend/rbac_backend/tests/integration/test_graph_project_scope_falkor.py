"""The contract graph is project-scoped; a document with no project is not
representable in it, and that must be said out loud.

FalkorDB rejects `MERGE` with a null property value, so before this suite a
document with `project_id=None` died on its first MERGE and was swallowed by
two nested warning handlers - ingestion reported "completed" for a document
entirely absent from the graph. That is the house silent-success pattern.

These tests pin BOTH halves:
  1. the real engine's NULL-property behaviour, so the precondition below is
     never "simplified" away on the assumption that null just stores null;
  2. that a sentinel project_id is not the fix - a synthesized value becomes a
     shared identity key that reads and ownership-scoped cleanup cannot tell
     from a real project.
"""

from __future__ import annotations

import uuid

import pytest

from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
    falkor_host,
    falkor_password,
    falkor_port,
    falkor_unreachable,
)

# Resolved through the shared seam, not from os.environ: under
# CONTRACLAIM_STAGING_GATE the localhost default is refused outright, so a
# staging run cannot silently measure the development FalkorDB.
FALKOR_HOST = falkor_host()
FALKOR_PORT = falkor_port()

pytestmark = pytest.mark.integration


@pytest.fixture()
def falkor():
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    # G-A21 / GRAPH-GATES U9. This suite is REQUIRED evidence for the G29
    # umbrella, and it used to mint its own `gscope_*` namespace and tear down with a
    # swallowed `GRAPH.DELETE`. That is exactly the pattern that left
    # `g31_letterdraft_d0fdb2ac7c` on a shared instance: a failed delete was
    # silent, so a crashed run looked like a clean one. It now mints through the
    # sanctioned helper, which names the graph after THIS run, refuses to delete
    # anything it did not create, verifies the deletion against `GRAPH.LIST`, and
    # raises rather than leaving residue. No assertion in this file changed.
    name = disposable_graph_name("projectscope")
    svc = FalkorGraphService(
        FalkorGraphConfig(host=FALKOR_HOST, port=FALKOR_PORT, graph_name=name,
                          password=falkor_password(), enabled=True, cleanup=False)
    )
    try:
        svc._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        falkor_unreachable(exc)
    yield svc
    drop_disposable_graph(svc._get_client(), name)


def test_falkordb_refuses_to_merge_on_a_null_property(falkor) -> None:
    """Pins the engine behaviour the precondition is built on."""
    from rbac_backend.services.falkor_graph_service import FalkorGraphError

    with pytest.raises(FalkorGraphError) as excinfo:
        falkor._execute(
            "MERGE (c:Clause {clause_node_id: 'c1', project_id: $p}) "
            "SET c.text_content = 'x'",
            {"p": None},
        )
    assert "null property value" in str(excinfo.value).lower()

    result = falkor._execute("MATCH (c:Clause) RETURN count(c)")
    rows = result[1] if len(result) > 1 else []
    assert (int(rows[0][0][1]) if rows else 0) == 0, "no partial node was written"


def test_upsert_refuses_a_document_with_no_project_scope() -> None:
    """The boundary rejects it explicitly instead of emitting doomed Cypher."""
    from rbac_backend.services.contract_graph_service import (
        ClauseGraphPayload,
        ContractGraphIdentityError,
        ContractGraphService,
        DocumentGraphPayload,
    )

    service = ContractGraphService()
    if not service.enabled:
        pytest.skip("contract graph disabled in this environment")

    payload = DocumentGraphPayload(
        doc_id="doc-orgscoped",
        title="Org-wide policy",
        version=None,
        organization_id="org-A",
        project_id=None,
        section_type="contract",
        priority=1,
    )
    clause = ClauseGraphPayload(
        clause_id="doc-orgscoped:1.1",
        clause_number="1.1",
        title="Scope",
        text_content="ORG_SCOPED_CLAUSE_TEXT",
        page_number=1,
        section_type="contract",
        priority=1,
        is_active=True,
    )

    with pytest.raises(ContractGraphIdentityError) as excinfo:
        service.upsert_contract_graph(payload, [clause])

    message = str(excinfo.value)
    assert "project_id" in message
    # The error must name the real reason, so a future reader does not "fix" it
    # by inventing a sentinel project.
    assert "project-scoped" in message
