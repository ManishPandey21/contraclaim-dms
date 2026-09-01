"""The disposable-graph helper must see every prefix the band actually mints.

`authority_band_graph` deletes only what this run created, and *reports* the
rest. Reporting is the half that decays quietly: four of the five Falkor slices
still mint `g29_` / `g32_` / `gscope_` names with their own teardown, so residue
from a crashed run of those suites was invisible to `legacy_residue` — it looked
exactly like a clean instance.

These tests pin detection, and pin that widening detection did not widen
deletion. Nothing here is a publication-authority regression, so this file is
deliberately NOT an Authority Band entry; it guards the band's harness.
"""

from __future__ import annotations

import pytest

from rbac_backend.tests import authority_band_graph as abg


class _Client:
    def __init__(self, names):
        self.names = list(names)
        self.deleted = []

    def execute_command(self, command, *args):
        if command == "GRAPH.LIST":
            return list(self.names)
        if command == "GRAPH.DELETE":
            self.deleted.append(args[0])
            self.names = [n for n in self.names if n != args[0]]
            return "OK"
        raise AssertionError(f"unexpected command {command}")


BUSINESS_GRAPHS = ("contraclaim", "G")


def test_legacy_residue_reports_every_prefix_the_band_mints() -> None:
    minted_by_band = [
        "g31_letterdraft_d0fdb2ac7c",   # test_letterdraft_graph_writer_authority_red (historical)
        "g29_0123456789",               # test_graph_stale_containment / invalidation
        "g32_0123456789",               # test_graph_letter_ownership
        "gscope_0123456789",            # test_graph_project_scope
    ]
    client = _Client([*BUSINESS_GRAPHS, *minted_by_band])
    assert abg.legacy_residue(client) == sorted(minted_by_band)


def test_a_business_graph_is_never_reported_as_residue() -> None:
    client = _Client([*BUSINESS_GRAPHS, "nulltest_bc84df34"])
    residue = abg.legacy_residue(client)
    assert not [name for name in residue if name in BUSINESS_GRAPHS]


def test_widening_detection_did_not_widen_deletion() -> None:
    """Recognising a prefix is not permission to delete it."""
    client = _Client(["g29_0123456789", "contraclaim"])
    for name in ("g29_0123456789", "g31_letterdraft_d0fdb2ac7c", "contraclaim", "G"):
        with pytest.raises(abg.DisposableGraphSafetyError):
            abg.drop_disposable_graph(client, name)
    assert client.deleted == []


def test_this_runs_own_graphs_are_still_deletable() -> None:
    name = abg.disposable_graph_name("probe")
    client = _Client([name, "contraclaim"])
    abg.drop_disposable_graph(client, name)
    assert client.deleted == [name]
    abg.assert_no_residue_from_this_run(client)
