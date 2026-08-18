"""A registry that names a nonexistent collection silently disables its check.

`DERIVED_SOURCE_COLLECTIONS["event_link"]` named `evidence_event_links`. The
real collection - created and indexed in `core/database.py:611` and written by
`evidence_graph_service` - is `event_links`. The wrong name would never match,
so any authority resolution routed through it would fail to find the record and
mis-decide, and the mistake is invisible because a typo'd collection name reads
like any other string.

This guards the registry against that class: every collection it names must be
one the application actually uses.
"""

from __future__ import annotations

from rbac_backend.services.publication_policy import DERIVED_SOURCE_COLLECTIONS

# Collections the backend actually reads/writes for these source types. Kept
# explicit so a rename on either side has to be reconciled here on purpose.
KNOWN_COLLECTIONS = {
    "documents",
    "letters",
    "document_vectors",
    "contract_clauses",
    "claims",
    "variations",
    "ipc_bills",
    "bank_guarantees",
    "matter_chronology_events",
    "project_events",
    "event_links",
    "key_dates",
    "arbitration_issue_matrix",
    "arbitration_jurisdiction_matrix",
}


def test_event_link_names_the_real_collection() -> None:
    assert DERIVED_SOURCE_COLLECTIONS["event_link"] == ("event_links",), (
        "the registry named evidence_event_links, which does not exist"
    )


def test_every_registered_collection_is_a_real_collection() -> None:
    named = {name for collections in DERIVED_SOURCE_COLLECTIONS.values() for name in collections}
    unknown = named - KNOWN_COLLECTIONS

    assert not unknown, (
        f"DERIVED_SOURCE_COLLECTIONS names collections the app does not use: "
        f"{sorted(unknown)}. A nonexistent name silently disables the check."
    )
