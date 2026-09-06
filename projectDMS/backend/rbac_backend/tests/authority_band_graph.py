"""Disposable Falkor graph namespace for Authority Band integration tests.

The Authority Band's Falkor slices are **explicitly authorised** to create and
delete graphs on the local test instance. What they are not authorised to do is
touch a business graph, or leave residue behind when they fail.

Both of those used to be possible. Graph names were `g31_<uuid>` — which
collided with the *gate* name G31 in runtime logs — and teardown was:

    try:
        client.execute_command("GRAPH.DELETE", name)
    except Exception:
        pass

so a failed delete left a graph on a shared instance with nothing recording it.
An inventory of `localhost:6380` during the handoff-hardening phase found exactly
that: `g31_letterdraft_d0fdb2ac7c`, orphaned by some earlier run.

Three rules follow, and each has a tempting weaker version:

* **Names carry their run.** `authority_band_<run>_<uuid>` identifies not just
  "a test graph" but *which run* created it, so residue is attributable instead
  of merely suspicious. `g31_*` is still recognised for legacy detection only.
* **A failed delete is reported, not swallowed.** It is retried once, verified
  against `GRAPH.LIST`, and only then raised. Silence is what let residue
  accumulate unnoticed.
* **Nothing outside the sanctioned prefixes is ever deleted.** The delete helper
  refuses a name it did not mint. A cleanup that could reach `contraclaim` is not
  a cleanup, it is an outage waiting for a typo.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, List

from rbac_backend.tests import staging_gate
from rbac_backend.tests.required_test_paths import matches_required

#: Current sanctioned prefix. Includes a per-run token so residue is attributable.
AUTHORITY_BAND_GRAPH_PREFIX = "authority_band_"

#: Legacy prefixes, recognised for *detection only*. Never auto-deleted: such a
#: graph may predate this run and belong to somebody else's session.
#:
#: `g31_` is the historical band prefix. The other three are still minted TODAY
#: by the four Falkor slices that carry their own teardown rather than using
#: this module — `g29_` (stale containment, invalidation), `g32_` (letter
#: ownership) and `gscope_` (project scope). Leaving them out did not make them
#: safe, it made their residue invisible: a crashed run of those suites looked
#: exactly like a clean instance.
#:
#: Recognition is not ownership. Deletion is gated by :func:`owned_by_this_run`
#: alone, which none of these can satisfy.
LEGACY_GRAPH_PREFIXES = ("g31_", "g29_", "g32_", "gscope_")

#: Retained for callers that still reference the single historical prefix.
LEGACY_GRAPH_PREFIX = LEGACY_GRAPH_PREFIXES[0]

#: One token per pytest process, so every graph this run makes is identifiable.
RUN_TOKEN = uuid.uuid4().hex[:8]


class DisposableGraphSafetyError(RuntimeError):
    """A cleanup was asked to touch something it did not create."""


#: Developer defaults. They address THIS machine, which is the whole hazard:
#: on the development host `localhost:6380` is the container holding the
#: business graphs `G` and `contraclaim`. Convenient for development, and
#: catastrophic for a staging Gate-2 run that forgot the override - the
#: evidence would be measured against the dev engine and the run would write
#: graphs into it. `staging_gate` turns both defaults off under
#: `CONTRACLAIM_STAGING_GATE`; see that module for why it is a single flag.
FALKOR_HOST_ENV = "FALKOR_TEST_HOST"
FALKOR_PORT_ENV = "FALKOR_TEST_PORT"
FALKOR_DEV_HOST_DEFAULT = "localhost"
FALKOR_DEV_PORT_DEFAULT = 6380

def falkor_host() -> str:
    return staging_gate.resolve_host(FALKOR_HOST_ENV, FALKOR_DEV_HOST_DEFAULT)


def falkor_port() -> int:
    return staging_gate.resolve_port(FALKOR_PORT_ENV, FALKOR_DEV_PORT_DEFAULT)


def disposable_graph_name(label: str = "") -> str:
    """Mint a graph name owned unambiguously by this run."""
    suffix = f"{label}_" if label else ""
    return f"{AUTHORITY_BAND_GRAPH_PREFIX}{RUN_TOKEN}_{suffix}{uuid.uuid4().hex[:10]}"


def is_disposable_test_graph(name: str) -> bool:
    """Recognise a disposable test graph, current or legacy."""
    return name.startswith(AUTHORITY_BAND_GRAPH_PREFIX) or name.startswith(LEGACY_GRAPH_PREFIXES)


def owned_by_this_run(name: str) -> bool:
    """Only graphs this process minted. The delete helper's actual gate."""
    return name.startswith(f"{AUTHORITY_BAND_GRAPH_PREFIX}{RUN_TOKEN}_")


def list_graphs(client: Any) -> List[str]:
    try:
        return [str(n) for n in client.execute_command("GRAPH.LIST")]
    except Exception:
        # An instance that cannot be listed cannot be verified either; the
        # caller decides whether that is fatal.
        return []


def drop_disposable_graph(client: Any, name: str) -> None:
    """Delete a graph this run created, and prove it is gone.

    Refuses any name outside this run's namespace, so a mistyped or reused
    variable cannot reach a business graph.
    """
    if not owned_by_this_run(name):
        raise DisposableGraphSafetyError(
            f"refusing to delete {name!r}: not created by this Authority Band run "
            f"(expected prefix {AUTHORITY_BAND_GRAPH_PREFIX}{RUN_TOKEN}_)"
        )

    first_error: Exception | None = None
    for _ in range(2):
        try:
            client.execute_command("GRAPH.DELETE", name)
            break
        except Exception as exc:  # noqa: BLE001 - re-raised below if it persists
            first_error = exc

    if name in list_graphs(client):
        raise AssertionError(
            f"disposable graph {name!r} survived teardown on "
            f"{falkor_host()}:{falkor_port()} and is now residue on a shared "
            f"instance. Delete it manually. Original error: {first_error}"
        )


def assert_no_residue_from_this_run(client: Any) -> None:
    """No graph minted by this run may remain. Legacy residue is reported only."""
    remaining = [n for n in list_graphs(client) if owned_by_this_run(n)]
    assert not remaining, (
        "Authority Band left disposable graphs behind: " + ", ".join(sorted(remaining))
    )


# ---------------------------------------------------------------------------
# G31 writer certification mode (GRAPH-GATES.md U5)
# ---------------------------------------------------------------------------
#
# Every Falkor suite calls `pytest.skip` when the engine is unreachable, so an
# unreachable FalkorDB produces a GREEN run carrying zero real-infrastructure
# evidence. That is fine for ordinary development and fatal for certification:
# the run that is offered as G31 evidence must not be able to prove nothing.
#
# The mechanism is deliberately small and opt-in. Nothing changes unless
# `G31_WRITER_CERTIFICATION=1` is set, and when it is, a SKIP inside one of the
# required writer suites below is reported as a FAILURE by
# `backend/rbac_backend/tests/conftest.py`. No accepted regression file is
# edited, no skip block is removed, and the ordinary developer workflow keeps
# skipping when Falkor is absent.
#
# Membership is the contract here, exactly as in AUTHORITY_BAND.txt. This tuple
# is not a count and not a band: it is the set of suites whose EXECUTION is
# required before anyone may describe G31 writer evidence as complete.

#: Environment variable that turns G31 certification mode on.
G31_CERTIFICATION_ENV = "G31_WRITER_CERTIFICATION"

#: The suites that must actually execute for G31 writer evidence.
#:
#: Real-engine (the ones that carry ownership, retraction, retry and
#: shared-node property behaviour, which GRAPH-GATES.md says a fake cannot):
#:   test_graph_writer_authority_falkor.py       blocked source cannot write;
#:                                               resync does not reintroduce.
#:   test_graph_letter_ownership_falkor.py       ownerless edge refused; node-only
#:                                               upsert allowed; peer isolation;
#:                                               shared node is identity only.
#:   test_graph_stale_containment_falkor.py      retraction holds; the `source`
#:                                               tag is not retraction authority.
#:   test_graph_project_scope_falkor.py          an unrepresentable identity is
#:                                               refused, not MERGEd on null.
#:   test_letterdraft_graph_writer_authority_red.py  the LangGraph pipeline that
#:                                               writes through the LOW-LEVEL
#:                                               service, incl. one real-engine
#:                                               physical-assertion proof.
#:   test_clause_graph_writer_authority_red.py   the clause-graph writer rechecks
#:                                               CURRENT authority before writing.
#:
#: Supplementary (fake boundaries, final-state assertions, not sole evidence):
#:   test_clause_chunking_authority_red.py       the clause writer's entry gate.
#:   test_contract_ingestor_authority_red.py     ContractGraphService's caller.
#:   test_document_graph_evidence_authority_red.py  DocumentService graph
#:                                               publication re-resolves per store.
#:   test_falkor_ro_query.py                     ownership, not the `source` tag,
#:                                               scopes retraction.
G31_REQUIRED_WRITER_FILES = (
    "backend/rbac_backend/tests/integration/test_graph_writer_authority_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_letter_ownership_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_stale_containment_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_project_scope_falkor.py",
    "backend/rbac_backend/tests/test_letterdraft_graph_writer_authority_red.py",
    "backend/rbac_backend/tests/test_clause_graph_writer_authority_red.py",
    "backend/rbac_backend/tests/test_clause_chunking_authority_red.py",
    "backend/rbac_backend/tests/test_contract_ingestor_authority_red.py",
    "backend/rbac_backend/tests/test_document_graph_evidence_authority_red.py",
    "backend/rbac_backend/tests/test_falkor_ro_query.py",
)


def g31_certification_mode() -> bool:
    """Is this run being offered as G31 writer certification evidence?"""
    return os.environ.get(G31_CERTIFICATION_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_g31_required_file(path: str) -> bool:
    """Does this test file belong to the required G31 writer set?

    Matched on the package-relative identity so it works from any rootdir, on
    Windows separators, and inside the backend container - where the `backend/`
    segment the entries above carry does not exist. A copy of the file in another
    package still cannot satisfy the requirement by name alone.
    """
    return _matches_required(path, G31_REQUIRED_WRITER_FILES)


def g31_skip_is_certification_failure(path: str, outcome: str) -> bool:
    """The whole decision, as one pure function.

    Kept pure so the mechanism is testable without a nested pytest run, and so
    the conftest hook stays too small to hide a mistake.
    """
    if outcome != "skipped":
        return False
    if not g31_certification_mode():
        return False
    return is_g31_required_file(path)


# ---------------------------------------------------------------------------
# G30 reader certification mode (GRAPH-GATES.md U5, consumer side)
# ---------------------------------------------------------------------------
#
# The same trap on the other side of the seam, and deliberately NOT the same
# flag. G30 and G31 are independent gates with different required sets: one
# switch over both would mean a run offered as reader evidence silently demanded
# the writer suites too, and a run offered as writer evidence claimed reader
# coverage it never executed. A certification claim must be exactly as wide as
# the evidence behind it, so each gate gets its own switch over its own set and
# they share only the mechanism.

#: Environment variable that turns G30 certification mode on.
G30_CERTIFICATION_ENV = "G30_READER_CERTIFICATION"

#: The suites that must actually execute for G30 reader/consumer evidence.
#:
#: Real engine (G30 field 12 rejects fake-only proof for exactly these):
#:   integration/test_graph_end_to_end_material_influence_falkor.py
#:       real contaminated node -> ReportPreview / CSV / SourceEvidence, plus the
#:       capacity property: an inadmissible candidate may not spend a slot.
#:
#: Final-object and static-guard suites (supplementary, never sole evidence for
#: the engine behaviours above):
#:   test_graph_consumer_authority.py            blocked / quarantined /
#:       unresolvable codes never become drafting SourceEvidence; the include
#:       list is not an authority bypass.
#:   test_graph_letter_reader_properties.py      no production Cypher reads a
#:       Letter property the writer no longer maintains, under ANY variable name.
#:   test_contract_graph_retrieval.py            contract graph expansion is
#:       fenced by the canonical eligible set and denies an absent document.
#:   test_graph_reader_content_authority_red.py  raw `Clause.text_content` and
#:       `section_type` never become material text; an unresolvable graph
#:       candidate never spends a contract-search slot.
#:   test_report_linked_chain_scope_authority_red.py   the linked-chain report's
#:       row visibility is the caller's scope, not the request's.
#:   test_letterdraft_graph_thread_identity_authority_red.py  a published graph
#:       thread carries identity, not republished node content.
#:
#: `test_graph_stale_containment_falkor.py` is deliberately ABSENT. It is a real
#: engine suite and it is about material influence, but its consumer assertion
#: terminates at `graph_codes_denied` against an in-file fake - the helper-level
#: proof G30 field 12 names as invalid closure evidence. Requiring it would add
#: execution time and no G30 evidence; the end-to-end suite is what replaced it.
G30_REQUIRED_READER_FILES = (
    "backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py",
    "backend/rbac_backend/tests/test_graph_consumer_authority.py",
    "backend/rbac_backend/tests/test_graph_letter_reader_properties.py",
    "backend/rbac_backend/tests/test_contract_graph_retrieval.py",
    "backend/rbac_backend/tests/test_graph_reader_content_authority_red.py",
    "backend/rbac_backend/tests/test_report_linked_chain_scope_authority_red.py",
    "backend/rbac_backend/tests/test_letterdraft_graph_thread_identity_authority_red.py",
)


def g30_certification_mode() -> bool:
    """Is this run being offered as G30 reader certification evidence?"""
    return os.environ.get(G30_CERTIFICATION_ENV, "").strip().lower() in {"1", "true", "yes"}


def _matches_required(path: str, required_files: Any) -> bool:
    """Package-relative identity match - one definition, shared with Gate 2.

    Rootdir, separators and the `backend/` prefix all drop out, and a same-named
    file in another package still cannot satisfy a requirement by basename.
    """
    return matches_required(path, required_files)


def is_g30_required_file(path: str) -> bool:
    """Does this test file belong to the required G30 reader set?"""
    return _matches_required(path, G30_REQUIRED_READER_FILES)


def g30_skip_is_certification_failure(path: str, outcome: str) -> bool:
    """The whole decision, as one pure function - same shape as G31's."""
    if outcome != "skipped":
        return False
    if not g30_certification_mode():
        return False
    return is_g30_required_file(path)


def legacy_residue(client: Any) -> List[str]:
    """Disposable-looking graphs this run did not create.

    Reported, never deleted: ownership cannot be established, and deleting
    somebody else's in-flight test graph is worse than leaving a stale one.
    """
    return sorted(
        n for n in list_graphs(client) if is_disposable_test_graph(n) and not owned_by_this_run(n)
    )


# ---------------------------------------------------------------------------
# G32-STATE rehearsal mode (GRAPH-GATES.md U5, state/migration side)
# ---------------------------------------------------------------------------
#
# The third instance of the same trap, and again a SEPARATE switch. G32-STATE is
# not G31 and not G30: its required set is the migration rehearsal, and a run
# offered as migration-readiness evidence must not silently demand — or silently
# claim — the writer and reader sets. One flag over three sets would make every
# certification claim exactly as wide as the widest set anybody ever ran.
#
# The name is deliberately not `G32_WRITER_*` or `G32_CERTIFICATION`: G32 is two
# certifications (CODE and STATE), G32-CODE is carried by the ownership and
# reader-property suites already required elsewhere, and this flag governs only
# the STATE half — the migration and its validator against a real engine.

#: Environment variable that turns G32-STATE rehearsal mode on.
G32_STATE_REHEARSAL_ENV = "G32_STATE_REHEARSAL"

#: The suites that must actually execute for G32-STATE migration/validation
#: evidence.
#:
#: Real engine (a fake cannot carry any of it — dry-run purity is a statement
#: about what the ENGINE observed, idempotency is a statement about MERGE/SET/
#: REMOVE convergence, and interruption recovery is a statement about restarting
#: against real partial state):
#:   integration/test_graph_letter_state_migration_falkor.py
#:
#: Fake-level rule pins (supplementary, never sole evidence): the R11 rules
#: themselves, in test_graph_letter_state_migration_authority_red.py.
G32_STATE_REQUIRED_FILES = (
    "backend/rbac_backend/tests/integration/test_graph_letter_state_migration_falkor.py",
    "backend/rbac_backend/tests/test_graph_letter_state_migration_authority_red.py",
)


def g32_state_rehearsal_mode() -> bool:
    """Is this run being offered as G32-STATE migration/validation evidence?"""
    return os.environ.get(G32_STATE_REHEARSAL_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_g32_state_required_file(path: str) -> bool:
    """Does this test file belong to the required G32-STATE set?"""
    return _matches_required(path, G32_STATE_REQUIRED_FILES)


def g32_state_skip_is_rehearsal_failure(path: str, outcome: str) -> bool:
    """The whole decision, as one pure function — same shape as G30's and G31's."""
    if outcome != "skipped":
        return False
    if not g32_state_rehearsal_mode():
        return False
    return is_g32_state_required_file(path)


# ---------------------------------------------------------------------------
# G29 umbrella certification mode (GRAPH-GATES.md U5, end-to-end side)
# ---------------------------------------------------------------------------
#
# The fourth instance of the same trap, and again a SEPARATE switch. G29 is the
# umbrella: its required set is deliberately WIDER than any predecessor's,
# because the thing it asserts - that stale, invalid, unowned, foreign or
# retracted graph state has zero unauthorised material influence - is a property
# of the four subsystems COMPOSED, not of any one of them.
#
# It is not `G31_WRITER_CERTIFICATION | G30_READER_CERTIFICATION |
# G32_STATE_REHEARSAL` for the reason each of those is its own switch: a
# certification claim must be exactly as wide as the evidence behind it, and one
# flag over four sets would make every gate's claim as wide as the widest set
# anybody happened to run. Running the umbrella set does NOT constitute G30,
# G31 or G32 evidence, and running theirs does not constitute G29's.

#: Environment variable that turns G29 umbrella certification mode on.
G29_UMBRELLA_CERTIFICATION_ENV = "G29_UMBRELLA_CERTIFICATION"

#: The suites that must actually execute for G29 umbrella evidence.
#:
#: Every entry is REAL ENGINE, because G29's own field 12 names a mock-based
#: proof as invalid closure evidence in terms ("Mocks cannot close this gap.
#: The whole difficulty is FalkorDB's own MERGE semantics") and names a skipped
#: integration test counted as a pass as the other way to be green while
#: proving nothing.
#:
#:   test_g29_umbrella_material_influence_falkor.py
#:       the composition itself: real writer -> real migration -> real
#:       retraction -> real consumer -> final report / CSV / SourceEvidence.
#:   test_g29_clause_state_material_influence_falkor.py
#:       R12-state: physical `(:Clause)` residue may neither disclose nor
#:       SUPPRESS, proven at the contract-QA prompt and the search candidate set.
#:   test_graph_end_to_end_material_influence_falkor.py
#:       the G-A17 capacity class at a final material object.
#:   test_graph_stale_containment_falkor.py
#:       stale artefact present, zero influence, and sync does not reintroduce.
#:   test_graph_invalidation_falkor.py
#:       one document's retraction may not destroy another's - or another
#:       tenant's - support on the globally shared node.
#:   test_graph_letter_ownership_falkor.py
#:       the shared node is identity + topology only; peer isolation.
#:   test_graph_writer_authority_falkor.py
#:       the writer resolves CURRENT authority before it writes.
#:   test_graph_project_scope_falkor.py
#:       an unrepresentable identity is refused rather than MERGEd on null -
#:       the silent-success failure mode this gate's field 6 records.
#:   test_graph_letter_state_migration_falkor.py
#:       the migrated-state model G29 composes with.
#:
#: `test_graph_consumer_authority.py` is deliberately ABSENT: it asserts on
#: final objects, which is right, but its graph is a dict-returning fake, so it
#: carries no real-engine evidence. It stays required for G30, where that is
#: exactly the correct scope.
G29_UMBRELLA_REQUIRED_FILES = (
    "backend/rbac_backend/tests/integration/test_g29_umbrella_material_influence_falkor.py",
    "backend/rbac_backend/tests/integration/test_g29_clause_state_material_influence_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_end_to_end_material_influence_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_stale_containment_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_invalidation_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_letter_ownership_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_writer_authority_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_project_scope_falkor.py",
    "backend/rbac_backend/tests/integration/test_graph_letter_state_migration_falkor.py",
)


def g29_umbrella_certification_mode() -> bool:
    """Is this run being offered as G29 umbrella certification evidence?"""
    return os.environ.get(G29_UMBRELLA_CERTIFICATION_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def is_g29_umbrella_required_file(path: str) -> bool:
    """Does this test file belong to the required G29 umbrella set?"""
    return _matches_required(path, G29_UMBRELLA_REQUIRED_FILES)


def g29_umbrella_skip_is_certification_failure(path: str, outcome: str) -> bool:
    """The whole decision, as one pure function - same shape as the other three."""
    if outcome != "skipped":
        return False
    if not g29_umbrella_certification_mode():
        return False
    return is_g29_umbrella_required_file(path)
