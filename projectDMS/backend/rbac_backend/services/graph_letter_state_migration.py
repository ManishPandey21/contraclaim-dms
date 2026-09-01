"""G32-STATE: migrate the legacy shared-Letter graph state, without guessing.

G32-CODE stopped the writer from putting document-, tenant- and
perspective-owned properties onto the globally shared ``(:Letter {normCode})``
node, and stopped every production reader from reading one. Neither change
touches state that already exists. The measured deployed corpus is 215 nodes
still carrying ``code``/``direction``/``subject``/``date``/``project``/
``organization_id``/``project_id``, and 307 of 310 ``CITES`` edges with no
``owner_document_id`` - edges no cleanup path can ever retract, because
retraction matches on ownership.

This module is the migration over that state. It is deliberately small and
deliberately timid:

* **Owner evidence is canonical Mongo evidence, and nothing else.** An edge is
  attributed to a document only when that document's own letter code is the
  edge's source, that document's ``references`` (or ``previous_letter_id``)
  derive the edge's target with the edge's relationship type, and no other
  document does the same. A ``source`` tag, a ``normCode``, an
  ``organization_id`` left on a node, a neighbour, the current request - none of
  those are evidence. See :func:`classify_edge`.
* **Unresolvable is a first-class outcome, not a failure to be papered over**
  (R11-A2). NULL stays NULL: an unowned edge is unreconcilable but inert, which
  is the correct direction for data whose provenance is genuinely unknown.
* **``owner_document_id = ''`` is never an outcome** (R11-A3), and a
  pre-existing ``''`` is repaired rather than left in place. It is a *concrete*
  owner key, so it parks every legacy tenant's edges under one owner that a
  single ownerless ``cleanup=True`` sync then wipes.
* **The production graphs are structurally unreachable** without an explicit
  recorded authorization. ``G`` and ``contraclaim`` live on the same engine as
  the disposable test graphs; a typo must not be able to reach them.
* **Dry run means zero writes.** The inventory pass that decides whether to
  mutate cannot itself mutate.

The accepted rule set is R11 in
``.claude/context/contract-master/GRAPH-GATES.md`` (*R11 / U3 - owner
amendment*), which repairs the contradiction between the migration plan's
Validation section ("every edge has ``owner_document_id``") and its own Stage 3
("leave the remainder NULL").

Nothing here certifies any gate, and running it against a fresh disposable graph
proves nothing about deployed state.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .falkor_graph_service import GLOBAL_LETTER_PROPERTIES, normalize_letter_code
from .publication_policy import is_consumable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# target safety
# ---------------------------------------------------------------------------

#: Live business graphs. Named explicitly so the refusal reads as a rule rather
#: than as a side effect of some prefix convention.
PROTECTED_GRAPH_NAMES = frozenset({"G", "contraclaim"})

#: The only namespace an unauthorized run may touch. This is the prefix minted by
#: ``rbac_backend.tests.authority_band_graph.disposable_graph_name``; the pairing
#: is pinned by a test rather than by importing the test helper into production
#: code. Legacy disposable prefixes (``g29_``/``g31_``/``g32_``/``gscope_``) are
#: deliberately NOT accepted: a graph this run did not mint may be somebody
#: else's in-flight state.
SANCTIONED_DISPOSABLE_PREFIX = "authority_band_"

#: Property names must be interpolated into ``REMOVE`` - Cypher has no
#: ``REMOVE l[$name]`` - so anything that is not a plain identifier is refused
#: rather than escaped.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The relationship types whose edges carry write/retraction authority.
AUTHORITY_CONTROLLED_RELATIONSHIPS = ("CITES", "REPLIES_TO")


class MigrationTargetRefused(RuntimeError):
    """The migration was pointed at a graph it may not touch."""


class MigrationUnsafeProperty(RuntimeError):
    """A property name could not be safely expressed in Cypher."""


class MigrationInterrupted(RuntimeError):
    """A deliberate interruption, used to prove restartability."""


@dataclass(frozen=True, slots=True)
class ProductionMigrationAuthorization:
    """An explicit, recorded authorization to touch a business graph.

    A boolean flag would be enough to make the code run and not enough to make
    the run accountable, so both fields are mandatory and neither may be blank.
    """

    authorization_reference: str
    approved_by: str

    def __post_init__(self) -> None:
        if not str(self.authorization_reference or "").strip():
            raise ValueError("production migration authorization needs a reference")
        if not str(self.approved_by or "").strip():
            raise ValueError("production migration authorization needs an approver")


def assert_graph_is_migratable(
    graph_name: str,
    authorization: Optional[ProductionMigrationAuthorization] = None,
) -> None:
    """Refuse anything that is not a run-owned disposable graph.

    Fail-closed on the *allow* side rather than the *deny* side: a deny-list of
    known business graphs would admit every graph nobody thought to list.
    """
    name = str(graph_name or "").strip()
    if not name:
        raise MigrationTargetRefused("refusing to migrate an unnamed graph")

    if authorization is not None:
        logger.warning(
            "G32-STATE migration authorized against graph %r (reference=%s, approved_by=%s)",
            name,
            authorization.authorization_reference,
            authorization.approved_by,
        )
        return

    if name in PROTECTED_GRAPH_NAMES:
        raise MigrationTargetRefused(
            f"refusing to migrate business graph {name!r}: a production migration "
            "requires an explicit ProductionMigrationAuthorization"
        )
    if not name.startswith(SANCTIONED_DISPOSABLE_PREFIX):
        raise MigrationTargetRefused(
            f"refusing to migrate {name!r}: only run-owned disposable graphs "
            f"(prefix {SANCTIONED_DISPOSABLE_PREFIX!r}) may be migrated without an "
            "explicit ProductionMigrationAuthorization"
        )


# ---------------------------------------------------------------------------
# observed graph state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LetterNodeState:
    """One shared ``(:Letter)`` node as it currently exists."""

    norm_code: str
    properties: Tuple[str, ...]

    @property
    def forbidden_properties(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.properties) - set(GLOBAL_LETTER_PROPERTIES)))


@dataclass(frozen=True, slots=True)
class EdgeState:
    """One authority-controlled edge as it currently exists.

    ``owner_document_id`` is ``None`` for both an absent property and an
    explicit NULL - R11 treats them as the same unresolved state - and ``""``
    for the forbidden empty-string placeholder, which is a different thing
    entirely.
    """

    edge_id: int
    src_norm: str
    dst_norm: str
    rel_type: str
    owner_document_id: Optional[str]
    source: Optional[str]

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.src_norm, self.dst_norm, self.rel_type)


# ---------------------------------------------------------------------------
# canonical evidence
# ---------------------------------------------------------------------------


class OwnerClass(str, Enum):
    """What canonical evidence says about one edge's ownership.

    Five states, not two. Collapsing INVALID into VALID is how a malformed
    corpus passes validation; collapsing AMBIGUOUS into RESOLVABLE is how one
    tenant acquires retraction authority over another's edge.
    """

    OWNED_VALID = "owned_valid"
    OWNED_INVALID = "owned_invalid"
    OWNED_EMPTY = "owned_empty"
    RESOLVABLE = "resolvable"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class EdgeClassification:
    edge: EdgeState
    classification: OwnerClass
    resolved_owner: Optional[str]
    candidate_owners: Tuple[str, ...]


class CanonicalReferenceIndex:
    """Mongo's view of which document asserts which reference.

    Built from the canonical ``documents`` records, using the SAME derivation
    the ingest writer uses (``GraphIngestionService._build_falkor_payload``):
    the document's own letter code is the edge source, each entry of its
    ``references`` is a target, and ``previous_letter_id`` is a ``REPLIES_TO``
    target. Deriving ownership any other way would attribute edges the writer
    will not re-derive, and the next sync would then delete them.

    A document that is not currently consumable is indexed as *known* but never
    offered as an owner: the writer refuses to publish support for a blocked
    source, and a backfill must not accomplish by the back door what the writer
    refuses at the front.
    """

    def __init__(self) -> None:
        self._known_documents: Dict[str, bool] = {}
        self._owners: Dict[Tuple[str, str, str], set] = {}

    # -- construction ------------------------------------------------------

    @classmethod
    def from_mongo_documents(
        cls, documents: Iterable[Mapping[str, Any]]
    ) -> "CanonicalReferenceIndex":
        index = cls()
        for document in documents:
            index.add_document(document)
        return index

    def add_document(self, document: Mapping[str, Any]) -> None:
        document_id = str(document.get("_id") or document.get("id") or "")
        if not document_id:
            return

        consumable = bool(is_consumable(document))
        self._known_documents[document_id] = consumable

        src_norm = normalize_letter_code(
            str(
                document.get("letterNo")
                or document.get("letter_no")
                or document.get("code")
                or document_id
            )
        )
        if not src_norm or not consumable:
            return

        for rel_type, dst_norm in _canonical_references(document):
            if not dst_norm or dst_norm == src_norm:
                continue
            self._owners.setdefault((src_norm, dst_norm, rel_type), set()).add(document_id)

    # -- queries -----------------------------------------------------------

    def owners_for(self, src_norm: str, dst_norm: str, rel_type: str) -> Tuple[str, ...]:
        return tuple(sorted(self._owners.get((src_norm, dst_norm, rel_type), ())))

    def is_known_document(self, document_id: str) -> bool:
        return str(document_id) in self._known_documents

    def is_consumable_document(self, document_id: str) -> bool:
        return bool(self._known_documents.get(str(document_id), False))

    @property
    def document_count(self) -> int:
        return len(self._known_documents)


def _canonical_references(document: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """(relationship type, normalized target) pairs this document asserts."""
    pairs: List[Tuple[str, str]] = []

    raw_refs = document.get("references")
    if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes)):
        for ref in raw_refs:
            code = None
            rel_type = "CITES"
            if isinstance(ref, Mapping):
                code = (
                    ref.get("letterNo")
                    or ref.get("letter_no")
                    or ref.get("code")
                    or ref.get("text")
                    or ref.get("documentId")
                )
                rel_type = str(ref.get("type") or "CITES")
            elif ref is not None:
                code = str(ref).strip()
            if not code:
                continue
            rel_type = "REPLIES_TO" if str(rel_type).upper() == "REPLIES_TO" else "CITES"
            pairs.append((rel_type, normalize_letter_code(str(code))))

    previous = document.get("previous_letter_id")
    if previous:
        pairs.append(("REPLIES_TO", normalize_letter_code(str(previous))))

    return pairs


def classify_edge(edge: EdgeState, index: CanonicalReferenceIndex) -> EdgeClassification:
    """Where this edge stands under R11. Pure; no graph access, no writes."""
    candidates = index.owners_for(edge.src_norm, edge.dst_norm, edge.rel_type)
    owner = edge.owner_document_id

    if owner is not None and owner != "":
        if index.is_known_document(owner) and index.is_consumable_document(owner):
            classification = OwnerClass.OWNED_VALID
        elif index.is_known_document(owner):
            # Known but not currently publishable. The edge is still owned and
            # still retractable by that document, so it is NOT malformed - it is
            # a containment question for G29/G31, reported and left alone.
            classification = OwnerClass.OWNED_VALID
        else:
            classification = OwnerClass.OWNED_INVALID
        return EdgeClassification(edge, classification, owner, candidates)

    # NULL or '' - the two legacy states. Both look to canonical evidence.
    if len(candidates) == 1:
        resolved: Optional[str] = candidates[0]
        classification = OwnerClass.RESOLVABLE
    elif len(candidates) > 1:
        resolved = None
        classification = OwnerClass.AMBIGUOUS
    else:
        resolved = None
        classification = OwnerClass.UNRESOLVED

    if owner == "":
        # The empty string is its own reported class regardless of whether a real
        # owner can be found, because leaving it is never acceptable.
        return EdgeClassification(edge, OwnerClass.OWNED_EMPTY, resolved, candidates)

    return EdgeClassification(edge, classification, resolved, candidates)


# ---------------------------------------------------------------------------
# planned actions
# ---------------------------------------------------------------------------


class EdgeActionKind(str, Enum):
    SET_OWNER = "set_owner"
    CLEAR_OWNER = "clear_owner"
    DELETE_DUPLICATE = "delete_duplicate"


@dataclass(frozen=True, slots=True)
class EdgeAction:
    edge_id: int
    kind: EdgeActionKind
    src_norm: str
    dst_norm: str
    rel_type: str
    owner_document_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class NodeAction:
    norm_code: str
    properties: Tuple[str, ...]


def plan_node_action(node: LetterNodeState) -> Optional[NodeAction]:
    """What to strip from one shared node, or ``None`` when it is already clean."""
    forbidden = node.forbidden_properties
    if not forbidden:
        return None
    for name in forbidden:
        if not _IDENTIFIER_RE.match(name):
            raise MigrationUnsafeProperty(
                f"refusing to build a REMOVE clause for property {name!r} on "
                f"(:Letter {{normCode: {node.norm_code!r}}}): not a plain identifier"
            )
    return NodeAction(norm_code=node.norm_code, properties=forbidden)


def plan_edge_actions(
    edges: Sequence[EdgeState], index: CanonicalReferenceIndex
) -> List[EdgeAction]:
    """The complete, deterministic edge plan for one pass.

    Duplicate suppression is part of the plan rather than a post-hoc cleanup:
    backfilling an owner onto a legacy edge that sits beside an identical
    already-owned edge would assert one fact twice, and a validator that only
    counted owners would call the result correct.
    """
    existing_owned: set = {
        (edge.src_norm, edge.dst_norm, edge.rel_type, edge.owner_document_id)
        for edge in edges
        if edge.owner_document_id
    }

    actions: List[EdgeAction] = []
    for edge in sorted(edges, key=lambda e: e.edge_id):
        outcome = classify_edge(edge, index)

        if outcome.classification in (OwnerClass.OWNED_VALID, OwnerClass.OWNED_INVALID):
            continue

        if outcome.classification is OwnerClass.OWNED_EMPTY:
            if outcome.resolved_owner:
                actions.append(
                    _own_or_dedupe(edge, outcome.resolved_owner, existing_owned)
                )
            else:
                actions.append(
                    EdgeAction(
                        edge_id=edge.edge_id,
                        kind=EdgeActionKind.CLEAR_OWNER,
                        src_norm=edge.src_norm,
                        dst_norm=edge.dst_norm,
                        rel_type=edge.rel_type,
                    )
                )
            continue

        if outcome.classification is OwnerClass.RESOLVABLE and outcome.resolved_owner:
            actions.append(_own_or_dedupe(edge, outcome.resolved_owner, existing_owned))
            continue

        # AMBIGUOUS and UNRESOLVED: R11-A2/R11-A4. No write, ever.

    return actions


def _own_or_dedupe(edge: EdgeState, owner: str, existing_owned: set) -> EdgeAction:
    signature = (edge.src_norm, edge.dst_norm, edge.rel_type, owner)
    if signature in existing_owned:
        return EdgeAction(
            edge_id=edge.edge_id,
            kind=EdgeActionKind.DELETE_DUPLICATE,
            src_norm=edge.src_norm,
            dst_norm=edge.dst_norm,
            rel_type=edge.rel_type,
            owner_document_id=owner,
        )
    existing_owned.add(signature)
    return EdgeAction(
        edge_id=edge.edge_id,
        kind=EdgeActionKind.SET_OWNER,
        src_norm=edge.src_norm,
        dst_norm=edge.dst_norm,
        rel_type=edge.rel_type,
        owner_document_id=owner,
    )


# ---------------------------------------------------------------------------
# graph transport
# ---------------------------------------------------------------------------


class FalkorStateClient:
    """The smallest Cypher transport this migration needs.

    Deliberately NOT ``FalkorGraphService``: that class is the subject of
    G32-CODE and is kept byte-identical through this phase, and a migration that
    reads the world through the component it is migrating around cannot detect a
    defect in it.
    """

    def __init__(self, redis_client: Any, graph_name: str) -> None:
        self._client = redis_client
        self.graph_name = graph_name

    def query(self, cypher: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        rendered = _render_cypher(cypher, params)
        return self._client.execute_command(
            "GRAPH.QUERY", self.graph_name, rendered, "--compact"
        )

    def rows(self, cypher: str, params: Optional[Mapping[str, Any]] = None) -> List[dict]:
        return _parse_rows(self.query(cypher, params))


def _render_cypher(cypher: str, params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return cypher
    header = " ".join(f"{key}={_literal(value)}" for key, value in params.items())
    return f"CYPHER {header} {cypher}"


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return "[" + ",".join(_literal(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _decode_cell(cell: Any) -> Any:
    """Decode one ``--compact`` value, recursively for arrays."""
    if isinstance(cell, (list, tuple)) and len(cell) == 2 and isinstance(cell[0], int):
        value = cell[1]
        if isinstance(value, list):
            return [_decode_cell(item) for item in value]
        return value
    return cell


def _parse_rows(response: Any) -> List[dict]:
    if not response or len(response) < 2:
        return []
    header, rows = response[0], response[1]
    if not header or not rows:
        return []
    columns = [h[1] if isinstance(h, (list, tuple)) and len(h) == 2 else h for h in header]
    parsed: List[dict] = []
    for row in rows:
        parsed.append({col: _decode_cell(cell) for col, cell in zip(columns, row)})
    return parsed


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


_REL_PATTERN = "|".join(AUTHORITY_CONTROLLED_RELATIONSHIPS)


def read_letter_nodes(client: FalkorStateClient) -> List[LetterNodeState]:
    rows = client.rows(
        "MATCH (l:Letter) RETURN l.normCode AS normCode, keys(l) AS props "
        "ORDER BY l.normCode"
    )
    nodes: List[LetterNodeState] = []
    for row in rows:
        props = row.get("props") or []
        nodes.append(
            LetterNodeState(
                norm_code=str(row.get("normCode") or ""),
                properties=tuple(str(p) for p in props),
            )
        )
    return nodes


def read_authority_edges(client: FalkorStateClient) -> List[EdgeState]:
    rows = client.rows(
        f"MATCH (s:Letter)-[e:{_REL_PATTERN}]->(d:Letter) "
        "RETURN ID(e) AS edgeId, s.normCode AS src, d.normCode AS dst, "
        "type(e) AS relType, e.owner_document_id AS owner, e.source AS source "
        "ORDER BY edgeId"
    )
    edges: List[EdgeState] = []
    for row in rows:
        owner = row.get("owner")
        edges.append(
            EdgeState(
                edge_id=int(row.get("edgeId")),
                src_norm=str(row.get("src") or ""),
                dst_norm=str(row.get("dst") or ""),
                rel_type=str(row.get("relType") or "CITES"),
                owner_document_id=None if owner is None else str(owner),
                source=None if row.get("source") is None else str(row.get("source")),
            )
        )
    return edges


# ---------------------------------------------------------------------------
# receipt
# ---------------------------------------------------------------------------


@dataclass
class MigrationReceipt:
    """Everything a reviewer needs to judge one run, including what it refused.

    ``environment`` is stamped LOCAL DISPOSABLE REHEARSAL unless a production
    authorization was supplied, so a receipt can never be mistaken for evidence
    of a production migration it did not perform.
    """

    graph_name: str
    run_id: str
    dry_run: bool
    environment: str
    started_at: str
    completed_at: Optional[str] = None
    authorization_reference: Optional[str] = None

    canonical_document_count: int = 0
    shared_letter_nodes: int = 0
    nodes_with_forbidden_properties: int = 0
    forbidden_property_instances: int = 0
    authority_edges: int = 0

    owned_valid: int = 0
    owned_invalid: int = 0
    owned_empty: int = 0
    resolvable: int = 0
    ambiguous: int = 0
    unresolved: int = 0

    nodes_changed: int = 0
    nodes_unchanged: int = 0
    edges_owner_set: int = 0
    edges_owner_cleared: int = 0
    edges_duplicates_removed: int = 0
    edges_unchanged: int = 0
    failed_operations: int = 0

    interrupted: bool = False
    validation: Optional[str] = None
    idempotency: Optional[str] = None
    interruption_recovery: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return (
            self.nodes_changed
            + self.edges_owner_set
            + self.edges_owner_cleared
            + self.edges_duplicates_removed
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in (
                "graph_name",
                "run_id",
                "dry_run",
                "environment",
                "started_at",
                "completed_at",
                "authorization_reference",
                "canonical_document_count",
                "shared_letter_nodes",
                "nodes_with_forbidden_properties",
                "forbidden_property_instances",
                "authority_edges",
                "owned_valid",
                "owned_invalid",
                "owned_empty",
                "resolvable",
                "ambiguous",
                "unresolved",
                "nodes_changed",
                "nodes_unchanged",
                "edges_owner_set",
                "edges_owner_cleared",
                "edges_duplicates_removed",
                "edges_unchanged",
                "failed_operations",
                "interrupted",
                "validation",
                "idempotency",
                "interruption_recovery",
                "notes",
            )
        } | {"changed": self.changed}


LOCAL_REHEARSAL_ENVIRONMENT = "LOCAL DISPOSABLE REHEARSAL"
AUTHORIZED_PRODUCTION_ENVIRONMENT = "AUTHORIZED PRODUCTION MIGRATION"


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_migration(
    *,
    client: FalkorStateClient,
    index: CanonicalReferenceIndex,
    dry_run: bool = True,
    authorization: Optional[ProductionMigrationAuthorization] = None,
    run_id: Optional[str] = None,
    interrupt_after: Optional[int] = None,
) -> MigrationReceipt:
    """Inventory, classify, and (unless ``dry_run``) apply.

    The whole pass is derived from a fresh read of graph state, so it is
    restartable by construction: there is no cursor to lose and no partial
    state a re-run cannot recompute. ``interrupt_after`` exists to prove that
    against a real engine rather than to assert it in a docstring.

    No cross-store transaction is claimed or possible. The canonical Mongo view
    is a snapshot taken by the caller; if it changes mid-run, the run's
    attributions are as of that snapshot and the receipt says so.
    """
    assert_graph_is_migratable(client.graph_name, authorization=authorization)

    receipt = MigrationReceipt(
        graph_name=client.graph_name,
        run_id=run_id or uuid.uuid4().hex[:12],
        dry_run=dry_run,
        environment=(
            AUTHORIZED_PRODUCTION_ENVIRONMENT
            if authorization is not None
            else LOCAL_REHEARSAL_ENVIRONMENT
        ),
        started_at=datetime.now(timezone.utc).isoformat(),
        authorization_reference=(
            authorization.authorization_reference if authorization else None
        ),
        canonical_document_count=index.document_count,
    )
    receipt.notes.append(
        "Canonical ownership is resolved from a Mongo snapshot supplied by the "
        "caller. No cross-store transaction exists between Mongo and FalkorDB, so "
        "attributions are as of that snapshot."
    )

    nodes = read_letter_nodes(client)
    edges = read_authority_edges(client)

    receipt.shared_letter_nodes = len(nodes)
    receipt.authority_edges = len(edges)

    node_actions = [action for action in map(plan_node_action, nodes) if action is not None]
    receipt.nodes_with_forbidden_properties = len(node_actions)
    receipt.forbidden_property_instances = sum(len(a.properties) for a in node_actions)
    receipt.nodes_unchanged = len(nodes) - len(node_actions)

    for edge in edges:
        outcome = classify_edge(edge, index)
        setattr(
            receipt,
            outcome.classification.value,
            getattr(receipt, outcome.classification.value) + 1,
        )

    edge_actions = plan_edge_actions(edges, index)
    receipt.edges_unchanged = len(edges) - len(edge_actions)

    if dry_run:
        receipt.nodes_changed = 0
        receipt.edges_owner_set = sum(
            1 for a in edge_actions if a.kind is EdgeActionKind.SET_OWNER
        )
        receipt.edges_owner_cleared = sum(
            1 for a in edge_actions if a.kind is EdgeActionKind.CLEAR_OWNER
        )
        receipt.edges_duplicates_removed = sum(
            1 for a in edge_actions if a.kind is EdgeActionKind.DELETE_DUPLICATE
        )
        receipt.nodes_changed = len(node_actions)
        receipt.notes.append(
            "DRY RUN: counts describe what an apply pass WOULD change. Zero graph "
            "writes were issued."
        )
        receipt.completed_at = datetime.now(timezone.utc).isoformat()
        return receipt

    applied = 0
    for action in node_actions:
        applied = _guard_interrupt(applied, interrupt_after, receipt)
        removals = ", ".join(f"l.{name}" for name in action.properties)
        client.query(
            f"MATCH (l:Letter {{normCode: $norm}}) REMOVE {removals}",
            {"norm": action.norm_code},
        )
        receipt.nodes_changed += 1

    for action in edge_actions:
        applied = _guard_interrupt(applied, interrupt_after, receipt)
        if action.kind is EdgeActionKind.SET_OWNER:
            client.query(
                f"MATCH ()-[e:{_REL_PATTERN}]->() WHERE ID(e) = $edgeId "
                "SET e.owner_document_id = $owner",
                {"edgeId": action.edge_id, "owner": action.owner_document_id},
            )
            receipt.edges_owner_set += 1
        elif action.kind is EdgeActionKind.CLEAR_OWNER:
            client.query(
                f"MATCH ()-[e:{_REL_PATTERN}]->() WHERE ID(e) = $edgeId "
                "REMOVE e.owner_document_id",
                {"edgeId": action.edge_id},
            )
            receipt.edges_owner_cleared += 1
        else:
            client.query(
                f"MATCH ()-[e:{_REL_PATTERN}]->() WHERE ID(e) = $edgeId DELETE e",
                {"edgeId": action.edge_id},
            )
            receipt.edges_duplicates_removed += 1

    receipt.completed_at = datetime.now(timezone.utc).isoformat()
    return receipt


def _guard_interrupt(
    applied: int, interrupt_after: Optional[int], receipt: MigrationReceipt
) -> int:
    if interrupt_after is not None and applied >= interrupt_after:
        receipt.interrupted = True
        raise MigrationInterrupted(
            f"deliberate interruption after {applied} write operation(s) on "
            f"{receipt.graph_name}"
        )
    return applied + 1


__all__ = [
    "AUTHORITY_CONTROLLED_RELATIONSHIPS",
    "AUTHORIZED_PRODUCTION_ENVIRONMENT",
    "CanonicalReferenceIndex",
    "EdgeAction",
    "EdgeActionKind",
    "EdgeClassification",
    "EdgeState",
    "FalkorStateClient",
    "LOCAL_REHEARSAL_ENVIRONMENT",
    "LetterNodeState",
    "MigrationInterrupted",
    "MigrationReceipt",
    "MigrationTargetRefused",
    "MigrationUnsafeProperty",
    "NodeAction",
    "OwnerClass",
    "PROTECTED_GRAPH_NAMES",
    "ProductionMigrationAuthorization",
    "SANCTIONED_DISPOSABLE_PREFIX",
    "assert_graph_is_migratable",
    "classify_edge",
    "plan_edge_actions",
    "plan_node_action",
    "read_authority_edges",
    "read_letter_nodes",
    "run_migration",
]
