"""The Authority Band cannot silently shrink.

Formerly the "G31 Band". Renamed so that G29/G30/G31/G32 mean graph
certification gates and nothing else: this suite is a regression harness,
and a green run is not a certified gate.

`AUTHORITY_BAND.txt` names the accepted regressions that pin document/graph
publication authority. Every ticket touching a production seam that intersects
that authority runs the band BEFORE and AFTER its change, and the two runs must
be comparable — which is only true if the band itself is stable.

A text file alone cannot enforce that. These tests do:

* a listed file that is deleted or renamed fails :func:`test_every_manifest_entry_exists`;
* a NEW authority regression that nobody registered fails
  :func:`test_every_discovered_authority_regression_is_registered`, so the band
  grows with the codebase instead of drifting behind it;
* deleting a line to make a run pass fails
  :func:`test_required_vertical_slices_are_all_present`, which pins the thirteen
  accepted slices by name, or
  :func:`test_required_graph_gate_regressions_are_all_present`, which pins the
  graph certification-gate regressions the same way.

Two categories are pinned, and they are kept apart on purpose:

* **REQUIRED_SLICE_FILES** — the thirteen vertical slices of the publication
  authority programme (implementation ticket 01);
* **REQUIRED_GRAPH_GATE_FILES** — the regressions that the G29/G30/G31/G32 gate
  register (`.claude/context/contract-master/GRAPH-GATES.md`) names as carrying
  each gate's assertions.

Pinning a file here means "this accepted regression may not disappear". It does
NOT mean the corresponding gate is closed; the gates are all OPEN and a green
band is evidence, never certification.

Four entries used to sit outside both mechanisms — `test_graph_consumer_authority`,
`test_graph_letter_reader_properties`, `test_contract_graph_retrieval` and
`test_falkor_ro_query` matched neither discovery pattern and no required name, so
deleting their lines was silent. :func:`test_no_manifest_entry_can_be_deleted_silently`
now makes that class of hole a failure rather than something to rediscover.

Together those make band shrinkage a test failure rather than a silent loss.
Owned by implementation ticket 01; no production code is involved.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
MANIFEST = TESTS_DIR / "AUTHORITY_BAND.txt"

#: The thirteen accepted vertical slices, by the file that carries each one.
#: Slices 8 and 9 (DocumentService graph publication and evidence publication)
#: share a single file, so thirteen slices map to twelve names here.
REQUIRED_SLICE_FILES = (
    "test_storage_resync_authority_red.py",
    "test_vector_reconciler_authority_red.py",
    "test_clause_chunking_authority_red.py",
    "test_normal_document_embedding_authority_red.py",
    "test_deferred_document_embedding_authority_red.py",
    "test_ingestion_pipeline_authority_red.py",
    "test_contract_ingestor_authority_red.py",
    "test_document_graph_evidence_authority_red.py",
    "test_evidence_graph_backfill_authority_red.py",
    "test_chronology_evidence_authority_red.py",
    "test_letterdraft_graph_writer_authority_red.py",
    "test_evidence_register_document_authority_red.py",
)


def _manifest_entries() -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    return [
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith("#")
    ]


def test_manifest_exists_and_is_not_empty() -> None:
    assert MANIFEST.is_file(), f"missing Authority Band manifest: {MANIFEST}"
    assert _manifest_entries(), "Authority Band manifest lists no tests"


def test_every_manifest_entry_exists() -> None:
    """A listed regression that was deleted or renamed must fail loudly."""
    missing = [entry for entry in _manifest_entries() if not (REPO_ROOT / entry).is_file()]
    assert not missing, (
        "Authority Band manifest references files that no longer exist: "
        f"{missing}. An accepted regression may not disappear — restore the file, "
        "or if it genuinely moved, update the manifest in the same change."
    )


def test_manifest_has_no_duplicate_entries() -> None:
    entries = _manifest_entries()
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    assert not duplicates, f"duplicate entries in the Authority Band manifest: {duplicates}"


def test_required_vertical_slices_are_all_present() -> None:
    """The thirteen accepted slices are pinned by name, not by count.

    Pinning names rather than a total means the band may GROW freely while a
    deletion still fails, which is the asymmetry the programme wants.
    """
    registered = {Path(entry).name for entry in _manifest_entries()}
    missing = [name for name in REQUIRED_SLICE_FILES if name not in registered]
    assert not missing, (
        f"accepted Authority Band vertical slices missing from the manifest: {missing}"
    )


def test_every_discovered_authority_regression_is_registered() -> None:
    """A new authority regression must join the band, not sit outside it.

    Without this the band quietly falls behind the codebase: someone adds a
    writer-authority regression, nobody registers it, and later tickets run a
    band that no longer covers the seam they are touching.
    """
    discovered = {path.name for path in TESTS_DIR.glob("*authority_red*.py")}
    discovered |= {
        path.name for path in (TESTS_DIR / "integration").glob("test_graph_*_falkor.py")
    }
    registered = {Path(entry).name for entry in _manifest_entries()}
    unregistered = sorted(discovered - registered)
    assert not unregistered, (
        "authority regressions exist but are not in the Authority Band manifest: "
        f"{unregistered}. Add them to AUTHORITY_BAND.txt so later tickets actually run them."
    )


# ---------------------------------------------------------------------------
# Adversarial guard model
# ---------------------------------------------------------------------------
#
# The three tests above each answer one question. Neither of them answers the
# one that actually matters: *if somebody deletes a line, does anything fail?*
#
# Two mechanisms can catch a deleted line, and they cover different ground:
#
#   * name pinning (REQUIRED_BAND_FILES) — catches deletion of the line AND of
#     the file, because the name is asserted independently of the filesystem;
#   * discovery — catches deletion of the line only while the file still exists
#     on disk under one of the two discovered patterns.
#
# So a manifest entry that is in neither is unprotected: removing its line is
# silent. The tests below make that a failure instead of a hole.

#: Graph certification-gate regressions, pinned by name.
#:
#: These are NOT among the thirteen vertical slices above and must never be
#: described as such — they come from the graph-remediation effort of
#: 2026-08-16..19, which predates the Contract Master programme, and they carry
#: the regressions that the G29/G30/G31/G32 gate register names as theirs
#: (`.claude/context/contract-master/GRAPH-GATES.md`, field 10 of each gate).
#: Pinning them here says only that they are accepted regressions the band may
#: not silently lose. It says nothing about any gate being closed.
#:
#:   test_graph_invalidation_falkor.py       G29  one document's invalidation
#:                                                must not destroy a peer's — or
#:                                                another tenant's — knowledge.
#:   test_graph_stale_containment_falkor.py  G29  umbrella: a stale artifact has
#:                                                zero material influence and is
#:                                                not reintroduced by resync.
#:   test_graph_project_scope_falkor.py      G29  a null project_id is not
#:                                                representable; MERGE-on-null
#:                                                failed silently ("completed"
#:                                                for a document absent from the
#:                                                graph). Gate attribution is
#:                                                RECONSTRUCTED — its docstring
#:                                                names none.
#:   test_graph_writer_authority_falkor.py   G31  sync resolves authority before
#:                                                the write; no ownerless edge.
#:   test_graph_letter_ownership_falkor.py   G32  the shared Letter node carries
#:                                                no document/tenant-owned
#:                                                property (write side).
#:   test_graph_letter_reader_properties.py  G32  the reader half: no production
#:                                                Cypher reads a property the
#:                                                writer no longer maintains.
#:                                                The gate register calls this
#:                                                "the standing guard against the
#:                                                reader-side regression
#:                                                re-appearing".
#:   test_graph_consumer_authority.py        G30  graph-derived drafting evidence
#:                                                resolves CURRENT source
#:                                                authority; unresolvable
#:                                                provenance fails closed; no
#:                                                node value becomes content.
#:   test_contract_graph_retrieval.py        G29/G30  the only pinned assertion
#:                                                that a Clause node outliving
#:                                                its canonical document cannot
#:                                                reach the prompt. Nothing
#:                                                deletes Clause nodes, so this
#:                                                path fails closed or the text
#:                                                stays permanently servable.
#:   test_falkor_ro_query.py                 G31/G32  the only unit-level pin of
#:                                                ownership-scoped retraction:
#:                                                cleanup matches
#:                                                `e.owner_document_id` and must
#:                                                NOT be gated on the open-set
#:                                                `source` tag, which would leave
#:                                                unlisted tags unretractable.
#:
#: The last two are ordinary test files that acquired a graph-authority
#: regression; only part of each file carries it. The manifest pins whole files,
#: so they are pinned whole — losing the file loses the regression either way.
REQUIRED_GRAPH_GATE_FILES = (
    "test_graph_invalidation_falkor.py",
    "test_graph_stale_containment_falkor.py",
    "test_graph_project_scope_falkor.py",
    "test_graph_writer_authority_falkor.py",
    "test_graph_letter_ownership_falkor.py",
    "test_graph_letter_reader_properties.py",
    "test_graph_consumer_authority.py",
    "test_contract_graph_retrieval.py",
    "test_falkor_ro_query.py",
    # G-A21. Pinned BY NAME rather than left to discovery: both are real-engine
    # graph regressions, but neither matches `test_graph_*_falkor.py`, so
    # deleting the manifest line would otherwise cost nothing. They are the
    # G29 umbrella's own evidence - the composition, and R12-state.
    "test_g29_umbrella_material_influence_falkor.py",
    "test_g29_clause_state_material_influence_falkor.py",
)

#: Every name the guard pins independently of the filesystem.
#:
#: Two categories, deliberately separate: the thirteen accepted vertical slices
#: of the publication-authority programme, and the graph certification-gate
#: regressions. A manifest entry in NEITHER tuple is still a legitimate band
#: member — it is simply one whose loss the discovery rule is expected to catch,
#: which is what lets the band grow without editing anything here.
REQUIRED_BAND_FILES = REQUIRED_SLICE_FILES + REQUIRED_GRAPH_GATE_FILES


def _discovered_regressions() -> set[str]:
    """Authority regressions the guard can find on disk by pattern."""
    discovered = {path.name for path in TESTS_DIR.glob("*authority_red*.py")}
    discovered |= {
        path.name for path in (TESTS_DIR / "integration").glob("test_graph_*_falkor.py")
    }
    return discovered


def _guard_failures(entries: list[str], discovered: set[str]) -> list[str]:
    """What the guard would report for a hypothetical band. Pure, so the tests
    below can ask "what if this entry were gone?" without touching the manifest."""
    registered = {Path(entry).name for entry in entries}
    failures = [f"required entry missing: {name}" for name in REQUIRED_BAND_FILES if name not in registered]
    failures += [f"unregistered authority regression: {name}" for name in sorted(discovered - registered)]
    return failures


def test_no_manifest_entry_can_be_deleted_silently() -> None:
    """Rule 1 of AUTHORITY_BAND.txt is "never delete a line from this file".

    A rule nothing enforces is a comment. For every registered entry, removing
    its line must produce at least one guard failure — via name pinning, or via
    discovery for the two patterns that grow on their own.
    """
    entries = _manifest_entries()
    discovered = _discovered_regressions()
    unprotected = sorted(
        {
            Path(entry).name
            for entry in entries
            if not _guard_failures([e for e in entries if e != entry], discovered)
        }
    )
    assert not unprotected, (
        "these Authority Band entries can be removed from the manifest without "
        f"any guard failing: {unprotected}. Pin each one by name in "
        "REQUIRED_BAND_FILES, or establish that it is not an accepted regression "
        "and say so explicitly — silence is the failure mode this guard exists for."
    )


def test_a_required_regression_cannot_be_deleted_with_its_file() -> None:
    """Deleting the line AND the file is the realistic silent-loss path.

    Discovery cannot catch it — a file that is gone is not discovered — so a
    required regression must be pinned by name, not merely by pattern.
    """
    entries = _manifest_entries()
    discovered = _discovered_regressions()
    undetected = [
        name
        for name in REQUIRED_BAND_FILES
        if not _guard_failures(
            [e for e in entries if Path(e).name != name], discovered - {name}
        )
    ]
    assert not undetected, (
        f"required Authority Band regressions that vanish silently when the file "
        f"and its manifest line are removed together: {undetected}"
    )


def test_required_graph_gate_regressions_are_all_present() -> None:
    """The graph-gate regressions are pinned by name, like the slices.

    They protect the reader and writer halves of G29/G30/G31/G32. A green run of
    them is still not a certified gate — see GRAPH-GATES.md.
    """
    registered = {Path(entry).name for entry in _manifest_entries()}
    missing = [name for name in REQUIRED_GRAPH_GATE_FILES if name not in registered]
    assert not missing, (
        f"accepted graph certification-gate regressions missing from the manifest: {missing}"
    )


def test_the_two_required_categories_stay_distinct() -> None:
    """A vertical slice is not a graph-gate regression and vice versa.

    Keeping them apart is the whole point: conflating "the band is green" with
    "the gate is closed" is the confusion the rename was meant to end.
    """
    overlap = sorted(set(REQUIRED_SLICE_FILES) & set(REQUIRED_GRAPH_GATE_FILES))
    assert not overlap, f"a file is claimed by both required categories: {overlap}"
