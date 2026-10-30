"""G32-STATE: independently validate migrated shared-Letter graph state.

A validator that asks the migration what it did validates nothing. This module
therefore re-reads the graph itself and re-derives every judgement from
canonical Mongo evidence; it shares the migration's *data* structures
(:class:`~rbac_backend.services.graph_letter_state_migration.CanonicalReferenceIndex`
is source evidence, not migration output) and none of its decisions - it never
calls ``classify_edge`` or ``plan_edge_actions``.

Two of the rules cannot be checked from the final state alone, and pretending
otherwise would be the interesting kind of wrong:

* *"no arbitrary ambiguous-owner assignment"* - once an edge whose key had two
  candidate owners has been assigned to one of them, the result is
  indistinguishable from an edge that document legitimately wrote. The only
  honest check is differential, against the pre-migration edge snapshot.
* *"peer-owner isolation"* - an owner that vanished is only visible by
  comparison.

So :func:`validate_graph_state` accepts an optional ``before_edges`` snapshot
and reports the differential rules as NOT CHECKED when it is absent, rather
than reporting them as passed.

R11 (`.claude/context/contract-master/GRAPH-GATES.md`, *R11 / U3 - owner
amendment*) is the accepted rule set. A green result here is evidence about one
graph at one moment; it certifies no gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .falkor_graph_service import GLOBAL_LETTER_PROPERTIES
from .graph_letter_state_migration import (
    CanonicalReferenceIndex,
    EdgeState,
    FalkorStateClient,
    LetterNodeState,
    read_authority_edges,
    read_letter_nodes,
)


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    rule: str
    subject: str
    detail: str


@dataclass
class ValidationResult:
    findings: List[ValidationFinding] = field(default_factory=list)
    checked_rules: List[str] = field(default_factory=list)
    unchecked_rules: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.findings

    def rules_violated(self) -> List[str]:
        return sorted({finding.rule for finding in self.findings})

    def summary(self) -> str:
        if self.ok:
            suffix = (
                f" ({len(self.unchecked_rules)} differential rule(s) NOT CHECKED)"
                if self.unchecked_rules
                else ""
            )
            return f"PASS: {len(self.checked_rules)} rule(s) checked{suffix}"
        return "FAIL: " + ", ".join(
            f"{finding.rule} [{finding.subject}] {finding.detail}"
            for finding in self.findings
        )


#: Every rule this validator knows how to check, in the order it checks them.
VALIDATION_RULES = (
    "R1_no_forbidden_letter_property",
    "R2_resolvable_edge_has_owner",
    "R3_no_empty_string_owner",
    "R4_no_owner_naming_no_canonical_document",
    "R5_no_owner_without_canonical_evidence",
    "R6_canonical_support_survives",
    "R7_no_duplicate_owned_support",
    "R8_no_ambiguous_owner_assigned",
    "R9_letter_nodes_remain_consumable",
    "R10_every_owner_is_retractable",
)

#: The rules that need a pre-migration snapshot to mean anything.
DIFFERENTIAL_RULES = ("R6_canonical_support_survives", "R8_no_ambiguous_owner_assigned")


def validate_graph_state(
    client: FalkorStateClient,
    index: CanonicalReferenceIndex,
    *,
    before_edges: Optional[Sequence[EdgeState]] = None,
) -> ValidationResult:
    """Read the graph and judge it against R11. Never trusts the migration."""
    return validate_observed_state(
        nodes=read_letter_nodes(client),
        edges=read_authority_edges(client),
        index=index,
        before_edges=before_edges,
    )


def validate_observed_state(
    *,
    nodes: Sequence[LetterNodeState],
    edges: Sequence[EdgeState],
    index: CanonicalReferenceIndex,
    before_edges: Optional[Sequence[EdgeState]] = None,
) -> ValidationResult:
    """The whole judgement as a pure function over observed state."""
    result = ValidationResult()
    result.counts = {
        "letter_nodes": len(nodes),
        "authority_edges": len(edges),
        "owned_edges": 0,
        "unresolved_edges": 0,
        "blocked_owner_edges": 0,
    }

    # -- R1 / R9: the shared node is identity + topology only ---------------
    for node in nodes:
        for name in sorted(set(node.properties) - set(GLOBAL_LETTER_PROPERTIES)):
            result.findings.append(
                ValidationFinding(
                    rule="R1_no_forbidden_letter_property",
                    subject=f"(:Letter {{normCode: {node.norm_code!r}}})",
                    detail=(
                        f"carries {name!r}, which is owned by some document and is "
                        "one tenant's value served to every tenant sharing this node"
                    ),
                )
            )
        if "normCode" not in node.properties or not node.norm_code:
            result.findings.append(
                ValidationFinding(
                    rule="R9_letter_nodes_remain_consumable",
                    subject=f"(:Letter {{normCode: {node.norm_code!r}}})",
                    detail="lost normCode, so no consumer can resolve it to Mongo",
                )
            )

    # -- per-edge rules ------------------------------------------------------
    owned_signatures: Dict[Tuple[str, str, str, str], int] = {}
    for edge in edges:
        owner = edge.owner_document_id
        candidates = index.owners_for(edge.src_norm, edge.dst_norm, edge.rel_type)
        subject = f"{edge.src_norm} -[{edge.rel_type}]-> {edge.dst_norm} (id={edge.edge_id})"

        if owner is None:
            result.counts["unresolved_edges"] += 1
            if len(candidates) == 1:
                result.findings.append(
                    ValidationFinding(
                        rule="R2_resolvable_edge_has_owner",
                        subject=subject,
                        detail=(
                            f"canonical evidence uniquely names {candidates[0]!r} as "
                            "owner, but the edge is unowned and therefore permanently "
                            "unretractable"
                        ),
                    )
                )
            continue

        if owner == "":
            result.findings.append(
                ValidationFinding(
                    rule="R3_no_empty_string_owner",
                    subject=subject,
                    detail=(
                        "owner_document_id is the empty string, a concrete owner key "
                        "that one ownerless cleanup=True sync wipes wholesale"
                    ),
                )
            )
            result.findings.append(
                ValidationFinding(
                    rule="R10_every_owner_is_retractable",
                    subject=subject,
                    detail="an empty owner is a wildcard, not an identity",
                )
            )
            continue

        result.counts["owned_edges"] += 1

        if not index.is_known_document(owner):
            result.findings.append(
                ValidationFinding(
                    rule="R4_no_owner_naming_no_canonical_document",
                    subject=subject,
                    detail=(
                        f"owner_document_id={owner!r} names no canonical document; "
                        "non-NULL is not the same as valid"
                    ),
                )
            )
        elif not index.is_consumable_document(owner):
            # Owned by a document that exists but may not currently publish. The
            # edge is still attributable and still retractable, so this is a
            # containment question for G29/G31 - counted, not failed.
            result.counts["blocked_owner_edges"] += 1
        elif owner not in candidates:
            result.findings.append(
                ValidationFinding(
                    rule="R5_no_owner_without_canonical_evidence",
                    subject=subject,
                    detail=(
                        f"owner_document_id={owner!r} is not derived by any canonical "
                        "reference for this edge"
                        + (
                            f"; it equals the edge's source tag {edge.source!r}, and a "
                            "source tag is metadata, never authority"
                            if edge.source is not None and str(edge.source) == owner
                            else ""
                        )
                    ),
                )
            )

        signature = (edge.src_norm, edge.dst_norm, edge.rel_type, owner)
        owned_signatures[signature] = owned_signatures.get(signature, 0) + 1

    for signature, count in sorted(owned_signatures.items()):
        if count > 1:
            src, dst, rel, owner = signature
            result.findings.append(
                ValidationFinding(
                    rule="R7_no_duplicate_owned_support",
                    subject=f"{src} -[{rel}]-> {dst}",
                    detail=f"{count} edges assert the same support for owner {owner!r}",
                )
            )

    checked = [rule for rule in VALIDATION_RULES if rule not in DIFFERENTIAL_RULES]

    # -- differential rules --------------------------------------------------
    if before_edges is None:
        result.checked_rules = checked
        result.unchecked_rules = list(DIFFERENTIAL_RULES)
        return result

    before_owned = {
        (e.src_norm, e.dst_norm, e.rel_type, e.owner_document_id)
        for e in before_edges
        if e.owner_document_id
    }
    after_owned = {
        (e.src_norm, e.dst_norm, e.rel_type, e.owner_document_id)
        for e in edges
        if e.owner_document_id
    }
    for signature in sorted(before_owned - after_owned):
        src, dst, rel, owner = signature
        result.findings.append(
            ValidationFinding(
                rule="R6_canonical_support_survives",
                subject=f"{src} -[{rel}]-> {dst}",
                detail=f"support owned by {owner!r} existed before the migration and is gone",
            )
        )

    before_by_id = {e.edge_id: e for e in before_edges}
    for edge in edges:
        previous = before_by_id.get(edge.edge_id)
        if previous is None or previous.owner_document_id:
            continue
        if not edge.owner_document_id:
            continue
        candidates = index.owners_for(edge.src_norm, edge.dst_norm, edge.rel_type)
        if len(candidates) > 1:
            result.findings.append(
                ValidationFinding(
                    rule="R8_no_ambiguous_owner_assigned",
                    subject=f"{edge.src_norm} -[{edge.rel_type}]-> {edge.dst_norm} (id={edge.edge_id})",
                    detail=(
                        f"was unowned and is now owned by {edge.owner_document_id!r}, "
                        f"but canonical evidence names {len(candidates)} possible owners "
                        f"({', '.join(candidates)}); ownership is retraction authority "
                        "and may not be guessed"
                    ),
                )
            )

    result.checked_rules = list(VALIDATION_RULES)
    result.unchecked_rules = []
    return result


def semantic_snapshot(client: FalkorStateClient) -> Dict[str, object]:
    """A comparable picture of graph state, for proving a pass changed nothing.

    Edge ids are deliberately included: a migration that deleted and recreated an
    edge would otherwise look identical to one that left it alone.
    """
    nodes = read_letter_nodes(client)
    edges = read_authority_edges(client)
    return {
        "nodes": sorted(
            (node.norm_code, tuple(sorted(node.properties))) for node in nodes
        ),
        "edges": sorted(
            (
                edge.edge_id,
                edge.src_norm,
                edge.dst_norm,
                edge.rel_type,
                edge.owner_document_id,
                edge.source,
            )
            for edge in edges
        ),
    }


__all__ = [
    "DIFFERENTIAL_RULES",
    "VALIDATION_RULES",
    "ValidationFinding",
    "ValidationResult",
    "semantic_snapshot",
    "validate_graph_state",
    "validate_observed_state",
]
