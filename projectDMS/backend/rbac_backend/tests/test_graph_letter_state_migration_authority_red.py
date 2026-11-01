"""G32-STATE: the legacy-graph migration may not invent ownership (R11).

G32-CODE stopped the writer putting document-, tenant- and perspective-owned
properties on the globally shared ``(:Letter {normCode})`` node. That froze the
contamination in place; it removed none of it. The measured deployed corpus is
215 nodes still carrying ``code``/``direction``/``subject``/``date``/``project``/
``organization_id``/``project_id``, and 307 of 310 ``CITES`` edges with no
``owner_document_id`` at all.

This suite pins the rules a migration over that state must obey. Each one has a
tempting, cheaper, wrong version, and every one of those was proposed at some
point in this programme's history:

* **Empty-string owner.** ``SET e.owner_document_id = coalesce(e.owner_document_id, '')``
  reads like "mark it unattributed". It is a *concrete* owner key - the exact
  value ``upsert_letter_with_refs`` synthesizes when a caller omits ownership -
  so it parks every legacy tenant's edges under one owner that a single
  ownerless ``cleanup=True`` sync then wipes. An independent review reproduced
  it: 3 legacy tenant edges to 0 after one call.
* **First match on ambiguity.** Two documents can carry the same ``letterNo``;
  the Letter node is MERGEd on ``normCode`` alone and is therefore shared across
  tenants. "Pick the first candidate" attributes one tenant's edge to another
  tenant's document, and ownership is *retraction authority*.
* **Any non-NULL owner is a valid owner.** An owner naming no canonical document
  is INVALID - a third state, neither VALID nor UNRESOLVED - and blessing it as
  valid is how a malformed corpus passes validation.
* **A migration entry point that can reach production.** ``G`` and
  ``contraclaim`` are live business graphs on the same engine as the disposable
  test graphs. Operator discipline is not a structural refusal.

R11 (`.claude/context/contract-master/GRAPH-GATES.md`, section *R11 / U3 - owner
amendment*) is the accepted rule set these tests encode. Nothing here certifies
G32, G32-STATE or any other gate: the real-engine rehearsal lives in
``tests/integration/test_graph_letter_state_migration_falkor.py``, and even a
green rehearsal against a disposable graph proves nothing about deployed state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rbac_backend.services import graph_letter_state_migration as migration
from rbac_backend.services import graph_letter_state_validation as validation
from rbac_backend.services.falkor_graph_service import GLOBAL_LETTER_PROPERTIES


# ---------------------------------------------------------------------------
# canonical evidence fixtures
# ---------------------------------------------------------------------------


def _doc(document_id: str, letter_no: str, refs=(), **extra):
    record = {"_id": document_id, "letterNo": letter_no, "references": list(refs)}
    record.update(extra)
    return record


def _index(*documents):
    return migration.CanonicalReferenceIndex.from_mongo_documents(documents)


def _edge(src, dst, owner=None, rel_type="CITES", source="parser", edge_id=1):
    return migration.EdgeState(
        edge_id=edge_id,
        src_norm=src,
        dst_norm=dst,
        rel_type=rel_type,
        owner_document_id=owner,
        source=source,
    )


# ---------------------------------------------------------------------------
# production graphs are structurally unreachable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("graph_name", ["G", "contraclaim"])
def test_a_migration_entry_point_refuses_a_business_graph(graph_name):
    """Not "the operator should not do that" - the call must not be expressible."""
    with pytest.raises(migration.MigrationTargetRefused):
        migration.assert_graph_is_migratable(graph_name)


def test_an_unrecognised_graph_name_is_refused_rather_than_assumed_disposable():
    with pytest.raises(migration.MigrationTargetRefused):
        migration.assert_graph_is_migratable("letters_backup_2026")


def test_a_run_owned_disposable_graph_is_accepted():
    migration.assert_graph_is_migratable("authority_band_deadbeef_seed_0123456789")


def test_production_authorization_requires_a_recorded_reference():
    """A boolean flag is not an authorization; nor is an empty reference."""
    with pytest.raises(ValueError):
        migration.ProductionMigrationAuthorization(
            authorization_reference="", approved_by="x"
        )
    with pytest.raises(ValueError):
        migration.ProductionMigrationAuthorization(
            authorization_reference="TICKET-1", approved_by=" "
        )


def test_an_explicitly_authorized_production_run_may_target_a_business_graph():
    """The refusal is fail-closed, not a permanent ban: G32-STATE must run one day."""
    auth = migration.ProductionMigrationAuthorization(
        authorization_reference="G32-STATE-PROD-001", approved_by="gate owner"
    )
    migration.assert_graph_is_migratable("contraclaim", authorization=auth)


# ---------------------------------------------------------------------------
# R11-A1 / R11-A4 - owner resolution and ambiguity
# ---------------------------------------------------------------------------


def test_a_uniquely_derivable_owner_is_resolved():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2"), index)
    assert outcome.classification is migration.OwnerClass.RESOLVABLE
    assert outcome.resolved_owner == "docA"


def test_two_candidate_owners_leave_the_edge_unresolved_and_never_pick_one():
    """The shared node is cross-tenant by construction; first-match crosses tenants."""
    index = _index(
        _doc("docA", "L-1", refs=[{"letterNo": "L-2"}]),
        _doc("docB", "L-1", refs=[{"letterNo": "L-2"}]),
    )
    outcome = migration.classify_edge(_edge("l-1", "l-2"), index)
    assert outcome.classification is migration.OwnerClass.AMBIGUOUS
    assert outcome.resolved_owner is None
    assert sorted(outcome.candidate_owners) == ["docA", "docB"]


def test_no_canonical_evidence_leaves_the_edge_unresolved_under_r11_a2():
    index = _index(_doc("docA", "L-9", refs=[{"letterNo": "L-8"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2"), index)
    assert outcome.classification is migration.OwnerClass.UNRESOLVED
    assert outcome.resolved_owner is None


def test_the_relationship_type_is_part_of_the_evidence():
    """A CITES reference is not evidence of ownership of a REPLIES_TO edge."""
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2", "type": "CITES"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2", rel_type="REPLIES_TO"), index)
    assert outcome.classification is migration.OwnerClass.UNRESOLVED


def test_previous_letter_id_is_canonical_replies_to_evidence():
    index = _index(_doc("docA", "L-1", previous_letter_id="L-0"))
    outcome = migration.classify_edge(_edge("l-1", "l-0", rel_type="REPLIES_TO"), index)
    assert outcome.classification is migration.OwnerClass.RESOLVABLE
    assert outcome.resolved_owner == "docA"


def test_a_source_tag_is_never_owner_evidence():
    """`source` is an open set of metadata tags. It carries no authority."""
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    outcome = migration.classify_edge(_edge("l-9", "l-2", source="docA"), index)
    assert outcome.classification is migration.OwnerClass.UNRESOLVED
    assert outcome.resolved_owner is None


def test_a_non_consumable_document_is_not_a_valid_owner():
    """The writer refuses to publish for a blocked source; backfill may not either."""
    index = _index(
        _doc("docA", "L-1", refs=[{"letterNo": "L-2"}], lifecycle_state="deleted")
    )
    outcome = migration.classify_edge(_edge("l-1", "l-2"), index)
    assert outcome.classification is migration.OwnerClass.UNRESOLVED


# ---------------------------------------------------------------------------
# R11-A3 / R11-A5 - empty and malformed owners
# ---------------------------------------------------------------------------


def test_an_empty_string_owner_is_never_produced_by_the_migration():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    plan = migration.plan_edge_actions([_edge("l-1", "l-2")], index)
    assert plan
    for action in plan:
        assert action.owner_document_id != ""


def test_an_existing_empty_string_owner_is_repaired_not_left_in_place():
    """`''` is a live hazard, not untidiness: one ownerless cleanup wipes the bucket."""
    index = _index(_doc("docA", "L-9", refs=[{"letterNo": "L-8"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2", owner=""), index)
    assert outcome.classification is migration.OwnerClass.OWNED_EMPTY

    (action,) = migration.plan_edge_actions([_edge("l-1", "l-2", owner="")], index)
    assert action.kind is migration.EdgeActionKind.CLEAR_OWNER


def test_an_empty_string_owner_that_is_uniquely_resolvable_gets_a_real_owner():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    (action,) = migration.plan_edge_actions([_edge("l-1", "l-2", owner="")], index)
    assert action.kind is migration.EdgeActionKind.SET_OWNER
    assert action.owner_document_id == "docA"


def test_an_owner_naming_no_canonical_document_is_invalid_not_valid():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2", owner="ghost"), index)
    assert outcome.classification is migration.OwnerClass.OWNED_INVALID


def test_an_invalid_owner_is_never_silently_replaced_with_a_derived_one():
    """Repairing it by guessing moves a real tenant's edge under another owner."""
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    assert migration.plan_edge_actions([_edge("l-1", "l-2", owner="ghost")], index) == []


def test_an_already_valid_owner_is_left_alone():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    outcome = migration.classify_edge(_edge("l-1", "l-2", owner="docA"), index)
    assert outcome.classification is migration.OwnerClass.OWNED_VALID
    assert migration.plan_edge_actions([_edge("l-1", "l-2", owner="docA")], index) == []


# ---------------------------------------------------------------------------
# duplicate support
# ---------------------------------------------------------------------------


def test_backfill_does_not_duplicate_support_that_already_exists():
    """Setting the owner on a legacy edge beside an identical owned edge asserts one
    fact twice; the legacy edge is dropped instead."""
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    edges = [
        _edge("l-1", "l-2", owner="docA", edge_id=1),
        _edge("l-1", "l-2", owner=None, edge_id=2),
    ]
    (action,) = migration.plan_edge_actions(edges, index)
    assert action.edge_id == 2
    assert action.kind is migration.EdgeActionKind.DELETE_DUPLICATE


# ---------------------------------------------------------------------------
# shared-node property stripping
# ---------------------------------------------------------------------------


def test_only_non_allowlisted_letter_properties_are_removed():
    node = migration.LetterNodeState(
        norm_code="l-1",
        properties=("normCode", "createdAt", "lastUpdated", "subject", "organization_id"),
    )
    assert node.forbidden_properties == ("organization_id", "subject")
    action = migration.plan_node_action(node)
    assert action is not None
    assert set(action.properties) == {"subject", "organization_id"}
    assert not set(action.properties) & GLOBAL_LETTER_PROPERTIES


def test_a_clean_letter_node_produces_no_action():
    node = migration.LetterNodeState(
        norm_code="l-1", properties=("normCode", "createdAt", "lastUpdated")
    )
    assert node.forbidden_properties == ()
    assert migration.plan_node_action(node) is None


def test_a_property_name_that_is_not_a_plain_identifier_is_refused():
    """Property names reach Cypher as text; `REMOVE l[$p]` does not exist."""
    node = migration.LetterNodeState(
        norm_code="l-1", properties=("normCode", "x`) DELETE l //")
    )
    with pytest.raises(migration.MigrationUnsafeProperty):
        migration.plan_node_action(node)


def test_normcode_is_never_a_removal_candidate():
    node = migration.LetterNodeState(norm_code="l-1", properties=("normCode", "code"))
    action = migration.plan_node_action(node)
    assert action is not None
    assert "normCode" not in action.properties


# ---------------------------------------------------------------------------
# the validator must be able to catch the migration
# ---------------------------------------------------------------------------


def _node(norm, *props):
    return migration.LetterNodeState(norm_code=norm, properties=("normCode",) + props)


def _validate(nodes, edges, index, before=None):
    return validation.validate_observed_state(
        nodes=nodes, edges=edges, index=index, before_edges=before
    )


def test_validator_fails_on_a_forbidden_shared_property():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    result = _validate([_node("l-1", "subject")], [], index)
    assert not result.ok
    assert "R1_no_forbidden_letter_property" in result.rules_violated()


def test_validator_fails_when_a_uniquely_resolvable_edge_is_still_unowned():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    result = _validate([_node("l-1"), _node("l-2")], [_edge("l-1", "l-2")], index)
    assert "R2_resolvable_edge_has_owner" in result.rules_violated()


def test_validator_fails_on_an_empty_string_owner():
    index = _index(_doc("docA", "L-9"))
    result = _validate([_node("l-1")], [_edge("l-1", "l-2", owner="")], index)
    violated = result.rules_violated()
    assert "R3_no_empty_string_owner" in violated
    assert "R10_every_owner_is_retractable" in violated


def test_validator_does_not_bless_a_malformed_owner():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    result = _validate([_node("l-1")], [_edge("l-1", "l-2", owner="ghost")], index)
    assert "R4_no_owner_naming_no_canonical_document" in result.rules_violated()


def test_validator_rejects_ownership_taken_from_the_source_tag():
    index = _index(
        _doc("docA", "L-1", refs=[{"letterNo": "L-2"}]),
        _doc("docB", "L-7"),
    )
    result = _validate(
        [_node("l-1")], [_edge("l-1", "l-2", owner="docB", source="docB")], index
    )
    assert "R5_no_owner_without_canonical_evidence" in result.rules_violated()


def test_validator_fails_on_duplicate_owned_support():
    index = _index(_doc("docA", "L-1", refs=[{"letterNo": "L-2"}]))
    edges = [
        _edge("l-1", "l-2", owner="docA", edge_id=1),
        _edge("l-1", "l-2", owner="docA", edge_id=2),
    ]
    result = _validate([_node("l-1")], edges, index)
    assert "R7_no_duplicate_owned_support" in result.rules_violated()


def test_validator_accepts_an_unresolved_legacy_edge_under_r11_a2():
    """R11-A2: NULL is a valid terminal state, not a deferred failure."""
    index = _index(_doc("docA", "L-9", refs=[{"letterNo": "L-8"}]))
    result = _validate([_node("l-1"), _node("l-2")], [_edge("l-1", "l-2")], index)
    assert result.ok, result.summary()
    assert result.counts["unresolved_edges"] == 1


def test_validator_reports_differential_rules_as_unchecked_without_a_before_snapshot():
    """Silence about a rule it cannot check would read as a pass."""
    index = _index(_doc("docA", "L-9"))
    result = _validate([_node("l-1")], [], index)
    assert set(result.unchecked_rules) == set(validation.DIFFERENTIAL_RULES)


def test_validator_catches_an_ambiguous_owner_assignment_against_the_before_snapshot():
    index = _index(
        _doc("docA", "L-1", refs=[{"letterNo": "L-2"}]),
        _doc("docB", "L-1", refs=[{"letterNo": "L-2"}]),
    )
    before = [_edge("l-1", "l-2", owner=None, edge_id=5)]
    after = [_edge("l-1", "l-2", owner="docA", edge_id=5)]
    result = _validate([_node("l-1")], after, index, before=before)
    assert "R8_no_ambiguous_owner_assigned" in result.rules_violated()


def test_validator_catches_a_peer_owner_whose_support_disappeared():
    index = _index(
        _doc("docA", "L-1", refs=[{"letterNo": "L-2"}]),
        _doc("docB", "L-1", refs=[{"letterNo": "L-2"}]),
    )
    before = [
        _edge("l-1", "l-2", owner="docA", edge_id=1),
        _edge("l-1", "l-2", owner="docB", edge_id=2),
    ]
    after = [_edge("l-1", "l-2", owner="docA", edge_id=1)]
    result = _validate([_node("l-1")], after, index, before=before)
    assert "R6_canonical_support_survives" in result.rules_violated()


def test_the_validator_does_not_reuse_the_migration_classifier():
    """An independent check that calls the thing it is checking is not independent."""
    source = Path(validation.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    for borrowed in ("classify_edge", "plan_edge_actions", "plan_node_action"):
        assert borrowed not in body, (
            f"the validator calls {borrowed}; it must re-derive its judgement from "
            "canonical evidence, not trust the migration's own verdict"
        )
