"""G32 against a real FalkorDB: a shared Letter node must not carry
document-, tenant-, or perspective-owned properties.

`falkor_graph_service.upsert_letter_with_refs` MERGEs a Letter on `normCode`
alone, then `ON MATCH SET src.subject/direction/organization_id/project_id/...`.
Two documents that cite the same code - including two documents in DIFFERENT
tenants - collapse onto one node, and the later writer overwrites the earlier
document's subject, direction, and TENANT ids.

Only `normCode` is globally canonical. Everything else is per-document. This
suite proves the contamination against the real engine (Phase 2), and pins the
corrected ownership model once the writer stops putting owned properties on the
shared node (Phases 4-6).

Run with a local FalkorDB:
    FALKOR_TEST_HOST=localhost FALKOR_TEST_PORT=6380 \
      pytest backend/rbac_backend/tests/integration/test_graph_letter_ownership_falkor.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

FALKOR_HOST = os.environ.get("FALKOR_TEST_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKOR_TEST_PORT", "6380"))

from rbac_backend.tests.authority_band_graph import (
    disposable_graph_name,
    drop_disposable_graph,
)

pytestmark = pytest.mark.integration


def _service(graph_name: str):
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    config = FalkorGraphConfig(
        host=FALKOR_HOST,
        port=FALKOR_PORT,
        graph_name=graph_name,
        password=None,
        enabled=True,
        cleanup=False,
    )
    service = FalkorGraphService(config)
    try:
        service._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable at {FALKOR_HOST}:{FALKOR_PORT}: {exc}")
    return service


@pytest.fixture()
def service():
    # G-A21 / GRAPH-GATES U9. This suite is REQUIRED evidence for the G29
    # umbrella, and it used to mint its own `g32_*` namespace and tear down with a
    # swallowed `GRAPH.DELETE`. That is exactly the pattern that left
    # `g31_letterdraft_d0fdb2ac7c` on a shared instance: a failed delete was
    # silent, so a crashed run looked like a clean one. It now mints through the
    # sanctioned helper, which names the graph after THIS run, refuses to delete
    # anything it did not create, verifies the deletion against `GRAPH.LIST`, and
    # raises rather than leaving residue. No assertion in this file changed.
    name = disposable_graph_name("ownership")
    svc = _service(name)
    yield svc
    drop_disposable_graph(svc._get_client(), name)


def _letter(norm: str, **extra: Any) -> Dict[str, Any]:
    letter = {"normCode": norm, "code": norm, "subject": "", "direction": "incoming"}
    letter.update(extra)
    return letter


# Properties we probe for ownership leakage on the shared node.
_PROBE_PROPS = ("subject", "date", "code", "direction", "organization_id", "project_id", "project")


def _node_props(service, norm: str) -> Dict[str, Any]:
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    keys = list(_PROBE_PROPS)
    returns = ", ".join(f"l.{k}" for k in keys)
    result = service._execute(
        f"MATCH (l:Letter {{normCode: $normCode}}) RETURN {returns}",
        {"normCode": normalize_letter_code(norm)},
    )
    rows = result[1] if len(result) > 1 else []
    if not rows:
        return {}
    # Each scalar comes back as [type, value]; a NULL is type 1.
    values = [cell[1] if isinstance(cell, list) and len(cell) >= 2 and cell[0] != 1 else None
              for cell in rows[0]]
    return dict(zip(keys, values))


# --- Phase 2: the contamination, proven against the real engine ----------------


def test_two_tenants_sharing_a_normcode_collapse_onto_one_node(service) -> None:
    """The premise: normCode alone is the identity, so the node is global."""
    service.upsert_letter_with_refs(_letter("LTR-001", organization_id="org-A"), [])
    service.upsert_letter_with_refs(_letter("LTR-001", organization_id="org-B"), [])

    from rbac_backend.services.falkor_graph_service import normalize_letter_code
    result = service._execute("MATCH (l:Letter {normCode: $n}) RETURN count(l)", {"n": normalize_letter_code("LTR-001")})
    count = result[1][0][0][1]  # scalar cell is [type, value]
    assert int(count) == 1, "one shared node per normCode"


def test_a_shared_letter_node_carries_no_tenant_owned_property(service) -> None:
    """G32: organization_id/project_id must not live on the global node.

    Today document B's sync overwrites document A's org id on the shared node,
    so a cross-tenant reader sees the wrong tenant. The corrected writer must
    not place tenant ids on the shared Letter at all.
    """
    service.upsert_letter_with_refs(
        _letter("LTR-002", organization_id="org-A", project_id="proj-A", subject="A subject"),
        [],
    )
    service.upsert_letter_with_refs(
        _letter("LTR-002", organization_id="org-B", project_id="proj-B", subject="B subject"),
        [],
    )

    props = _node_props(service, "LTR-002")

    assert props.get("organization_id") in (None, "", "null"), (
        f"shared Letter node carries a tenant id: {props.get('organization_id')!r}"
    )
    assert props.get("project_id") in (None, "", "null"), (
        f"shared Letter node carries a project id: {props.get('project_id')!r}"
    )


def test_a_shared_letter_node_carries_no_document_owned_property(service) -> None:
    """subject/date/direction are per-document; they must not be on the node."""
    service.upsert_letter_with_refs(_letter("LTR-003", subject="A subject", direction="outgoing"), [])
    service.upsert_letter_with_refs(_letter("LTR-003", subject="B subject", direction="incoming"), [])

    props = _node_props(service, "LTR-003")

    assert props.get("subject") in (None, "", "null"), (
        f"shared Letter node carries a document subject: {props.get('subject')!r}"
    )


def test_only_normcode_survives_on_the_shared_node(service) -> None:
    """The whole ownership rule as one property-set assertion."""
    from rbac_backend.services.falkor_graph_service import GLOBAL_LETTER_PROPERTIES

    service.upsert_letter_with_refs(
        _letter("LTR-004", organization_id="org-A", project_id="proj-A", subject="s", direction="outgoing"),
        [],
    )
    props = _node_props(service, "LTR-004")

    non_global = {k for k, v in props.items() if v not in (None, "", "null")} - set(GLOBAL_LETTER_PROPERTIES)
    assert not non_global, (
        f"shared Letter node carries non-global properties: {sorted(non_global)}"
    )


# --- CRITICAL 1 (review): one tenant's sync must not delete another's edges ----


def _edges(service, norm: str):
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    result = service._execute(
        "MATCH (s:Letter {normCode: $n})-[e]->(d:Letter) RETURN type(e), d.normCode, e.source",
        {"n": normalize_letter_code(norm)},
    )
    rows = result[1] if len(result) > 1 else []
    return sorted(
        (r[0][1], r[1][1], r[2][1] if r[2][0] != 1 else None) for r in rows
    )


def test_a_peer_sync_does_not_delete_another_documents_edges(service) -> None:
    """Reviewer CRITICAL: `src` is the GLOBAL node but `$desiredNormCodes` is
    ONE document's reference list, so tenant B's routine sync deleted tenant A's
    edges on the shared code - no block, no delete, just two tenants ingesting."""
    shared = "LTR-SHARED-EDGE"

    service.upsert_letter_with_refs(
        _letter(shared, organization_id="org-A"),
        [{"code": "ABC-001", "normCode": "abc-001", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="doc-A",
    )
    after_a = _edges(service, shared)
    assert ("CITES", "abc-001", "parser") in after_a

    # Tenant B syncs the SAME code with its own, different reference.
    service.upsert_letter_with_refs(
        _letter(shared, organization_id="org-B"),
        [{"code": "XYZ-900", "normCode": "xyz-900", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="doc-B",
    )
    after_b = _edges(service, shared)

    assert ("CITES", "abc-001", "parser") in after_b, (
        "tenant B's sync deleted tenant A's edge on the shared node"
    )
    assert ("CITES", "xyz-900", "parser") in after_b


def test_a_document_still_reconciles_its_own_stale_edges(service) -> None:
    """Cleanup must still work - scoped, not disabled."""
    shared = "LTR-OWN-CLEANUP"
    service.upsert_letter_with_refs(
        _letter(shared), [{"code": "OLD-1", "normCode": "old-1", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="doc-A",
    )
    assert ("CITES", "old-1", "parser") in _edges(service, shared)

    # Same document re-syncs with a different reference: its own stale edge goes.
    service.upsert_letter_with_refs(
        _letter(shared), [{"code": "NEW-1", "normCode": "new-1", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="doc-A",
    )
    edges = _edges(service, shared)

    assert ("CITES", "old-1", "parser") not in edges
    assert ("CITES", "new-1", "parser") in edges


# --- CRITICAL (re-review): overlapping citations are two assertions, not one ---


def test_two_documents_citing_the_same_target_keep_separate_edges(service) -> None:
    """The edge MERGE keyed only on (src,dst), so when two documents citing the
    SAME target shared a normCode they shared ONE edge - and the later writer
    stole its `owner_document_id`. Then that document dropping the reference
    deleted the peer's citation too. Overlapping citations between documents
    sharing a code are the EXPECTED case, not an exotic one."""
    shared = "LTR-OVERLAP"
    common = {"code": "COMMON-Y", "normCode": "common-y", "type": "CITES", "source": "parser"}

    service.upsert_letter_with_refs(
        _letter(shared), [common, {"code": "A-ONLY", "normCode": "a-only", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docA",
    )
    service.upsert_letter_with_refs(
        _letter(shared), [common, {"code": "B-ONLY", "normCode": "b-only", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docB",
    )

    # B drops the shared target and re-syncs. A's assertion of it must remain.
    service.upsert_letter_with_refs(
        _letter(shared), [{"code": "B-ONLY", "normCode": "b-only", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docB",
    )
    edges = _edges(service, shared)
    to_common = [e for e in edges if e[1] == "common-y"]

    assert to_common, "document B's re-sync deleted document A's citation of the shared target"
    assert ("CITES", "a-only", "parser") in edges


def test_an_ownerless_edge_write_is_refused(service) -> None:
    """A blank owner is a wildcard, not an identity.

    Skipping only the CLEANUP was not enough: the ownerless call still WROTE
    its edge, with `owner_document_id=''`, and no cleanup path can ever match
    that - the reconcile pass is skipped for a blank owner and scoped to an
    exact owner otherwise. The result was a permanently unretractable edge, and
    on the live graph 307 of 325 CITES edges are in exactly that state. Two
    independent reviews reached this same site from opposite directions.

    So the write itself is refused. Nothing unretractable can enter the graph.
    """
    from rbac_backend.services.falkor_graph_service import FalkorGraphError

    shared = "LTR-NOOWNER"
    service.upsert_letter_with_refs(
        _letter(shared), [{"code": "KEEP-1", "normCode": "keep-1", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docA",
    )

    with pytest.raises(FalkorGraphError) as excinfo:
        service.upsert_letter_with_refs(
            _letter(shared), [{"code": "OTHER-1", "normCode": "other-1", "type": "CITES", "source": "parser"}],
            cleanup=True,
        )
    assert "owner_document_id" in str(excinfo.value)

    # Assert FINAL graph state: the peer's edge survives and no ownerless edge
    # was created.
    edges = _edges(service, shared)
    assert ("CITES", "keep-1", "parser") in edges, (
        "an ownerless cleanup wiped a document's edges"
    )
    assert not [e for e in edges if e[1] == "other-1"], (
        "the refused write still landed an unretractable edge"
    )


def test_a_node_only_upsert_without_an_owner_is_still_allowed(service) -> None:
    """A bare Letter node is pure shared identity and asserts nothing owned,
    so it must not require attribution - only edges do."""
    service.upsert_letter_with_refs(_letter("LTR-NODEONLY"), [])


# --- CRITICAL (re-review): deletion must not damage a peer that CITES the code -


def test_deleting_a_letter_preserves_a_peer_citation(service) -> None:
    """The Mongo guards count OWNERS of a code, never dependents, so a peer that
    merely cites it was invisible - and DETACH DELETE took its edge as
    collateral."""
    service.upsert_letter_with_refs(
        _letter("OWNED-A"), [{"code": "A-SUP", "normCode": "a-sup", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docA",
    )
    service.upsert_letter_with_refs(
        _letter("CITER-B"), [{"code": "OWNED-A", "normCode": "owned-a", "type": "CITES", "source": "parser"}],
        cleanup=True, owner_document_id="docB",
    )
    assert _edges(service, "CITER-B"), "precondition: B cites A"

    # A is blocked; containment removes A's contribution.
    service.delete_letter("OWNED-A")

    assert _edges(service, "CITER-B"), (
        "tenant B's citation was destroyed as collateral by tenant A's containment"
    )


def test_deleting_a_letter_with_no_dependents_still_removes_the_node(service) -> None:
    """Preservation must not become a blanket refusal to clean up."""
    from rbac_backend.services.falkor_graph_service import normalize_letter_code

    service.upsert_letter_with_refs(_letter("SOLO-DEL"), [], cleanup=False, owner_document_id="docA")
    service.delete_letter("SOLO-DEL")

    result = service._execute(
        "MATCH (l:Letter {normCode: $n}) RETURN count(l)",
        {"n": normalize_letter_code("SOLO-DEL")},
    )
    rows = result[1] if len(result) > 1 else []
    assert (int(rows[0][0][1]) if rows else 0) == 0
