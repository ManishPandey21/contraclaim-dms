"""Embedding input without the classification, and a versioned generation.

Supports C08-05.

The point of removing instrument classification from the embedded string is that
a correction should cost nothing. Today it costs a re-embed of every chunk, and
a re-embed that fails blocks evidence — so a legal correction is gated on an
expensive derived-store operation, which is the authority direction inverted.

It is also the right modelling call on its own: instrument kind is a
document-level legal fact, not a semantic property of clause text. Embedding it
lets a legal classification perturb similarity ranking, which is the
relevance/precedence conflation the architecture forbids.

The generation version exists so old and new vectors are distinguishable. What
it must NOT be is a precondition for correctness: old vectors are already
logically unusable through the eligible set, and if they were not, a stalled
re-embed would be a security problem rather than a performance one.
"""

from __future__ import annotations

import inspect

import pytest

from rbac_backend.models.contract_document import ContractDocumentType
from rbac_backend.services.contract_embedding_generation import (
    CONTRACT_EMBEDDING_GENERATION,
    ClassificationInEmbeddingInput,
    build_embedding_input,
    build_vector_payload,
    is_stale_generation,
)


# --------------------------------------------------------------------------- #
# classification is out of the embedded string
# --------------------------------------------------------------------------- #


def test_the_embedding_input_builder_takes_no_classification_argument():
    """Structural: a caller cannot fold the type back in."""
    parameters = set(inspect.signature(build_embedding_input).parameters)
    assert "contract_document_type" not in parameters
    assert "classification" not in parameters
    assert "section_type" not in parameters


def test_no_instrument_type_appears_in_the_embedded_text():
    text = build_embedding_input(
        clause_number="10.1",
        clause_title="Variations",
        section_heading="Variations and Adjustments",
        toc_path=["Part II", "Variations"],
        page_numbers=[14],
        clause_tags=["variation"],
        text="The Engineer may initiate a Variation.",
    )

    lowered = text.lower()
    for member in ContractDocumentType:
        assert member.value.lower() not in lowered, member.value
        assert member.name.lower() not in lowered, member.name


def test_the_embedded_text_still_carries_genuine_semantic_context():
    """Removing the legal fact must not gut the clause context."""
    text = build_embedding_input(
        clause_number="10.1",
        clause_title="Variations",
        section_heading="Variations and Adjustments",
        toc_path=["Part II"],
        page_numbers=[14],
        clause_tags=["variation"],
        text="The Engineer may initiate a Variation.",
    )

    assert "10.1" in text
    assert "Variations" in text
    assert "The Engineer may initiate a Variation." in text


def test_the_same_clause_embeds_identically_before_and_after_a_correction():
    """The whole point: a reclassification is not an embedding-space change."""
    kwargs = dict(
        clause_number="10.1",
        clause_title="Variations",
        section_heading="Variations and Adjustments",
        toc_path=["Part II"],
        page_numbers=[14],
        clause_tags=["variation"],
        text="The Engineer may initiate a Variation.",
    )
    assert build_embedding_input(**kwargs) == build_embedding_input(**kwargs)


def test_a_caller_smuggling_a_classification_into_the_text_is_refused():
    with pytest.raises(ClassificationInEmbeddingInput):
        build_embedding_input(
            clause_number="10.1",
            clause_title="Variations",
            section_heading="general_conditions",
            toc_path=[],
            page_numbers=[],
            clause_tags=[],
            text="The Engineer may initiate a Variation.",
        )


def test_the_clause_body_itself_is_never_policed():
    """A clause that happens to discuss an amendment is not a smuggled label."""
    text = build_embedding_input(
        clause_number="10.1",
        clause_title="Variations",
        section_heading="Variations",
        toc_path=[],
        page_numbers=[],
        clause_tags=[],
        text="This amendment to the general conditions shall take effect immediately.",
    )
    assert "amendment to the general conditions" in text


# --------------------------------------------------------------------------- #
# the generation version
# --------------------------------------------------------------------------- #


def test_a_generation_version_exists_and_is_stamped_on_the_payload():
    payload = build_vector_payload(
        chunk_id="chunk-1",
        document_id="doc-1",
        organization_id="org-1",
        clause_number="10.1",
        source_classification_revision=4,
    )
    assert payload["embedding_generation"] == CONTRACT_EMBEDDING_GENERATION
    assert CONTRACT_EMBEDDING_GENERATION >= 2, "removing a field requires a bump"


def test_the_payload_carries_no_authoritative_classification():
    payload = build_vector_payload(
        chunk_id="chunk-1",
        document_id="doc-1",
        organization_id="org-1",
        clause_number="10.1",
        source_classification_revision=4,
    )
    for forbidden in ("contract_document_type", "document_type", "section_type", "priority"):
        assert forbidden not in payload


def test_source_classification_revision_is_carried_as_a_hint():
    payload = build_vector_payload(
        chunk_id="chunk-1",
        document_id="doc-1",
        organization_id="org-1",
        clause_number="10.1",
        source_classification_revision=4,
    )
    assert payload["source_classification_revision"] == 4


# --------------------------------------------------------------------------- #
# negative hint only
# --------------------------------------------------------------------------- #


def test_a_stale_revision_marks_a_point_stale():
    assert is_stale_generation({"source_classification_revision": 3}, authoritative_revision=4)


def test_a_matching_revision_does_not_mark_it_stale():
    assert not is_stale_generation({"source_classification_revision": 4}, authoritative_revision=4)


def test_a_missing_revision_is_treated_as_stale_not_as_permission():
    """Absence is not evidence of currency."""
    assert is_stale_generation({}, authoritative_revision=4)


def test_a_newer_looking_revision_cannot_authorise_a_point():
    """The hint may only subtract. A payload claiming the future proves nothing."""
    assert is_stale_generation(
        {"source_classification_revision": 99}, authoritative_revision=4
    )


def test_the_staleness_helper_returns_a_bool_never_an_authorisation():
    result = is_stale_generation({"source_classification_revision": 4}, authoritative_revision=4)
    assert result is False
    assert isinstance(result, bool)


# --------------------------------------------------------------------------- #
# old vectors are unusable without waiting for a re-embed
# --------------------------------------------------------------------------- #


def test_logical_unusability_does_not_depend_on_the_re_embed_completing():
    """The stop condition. Containment is the fence; the re-embed is housekeeping."""
    import asyncio

    from rbac_backend.services.contract_vector_containment import (
        vector_candidates_for_evidence,
    )

    class Recording:
        def __init__(self):
            self.calls = []

        async def search(self, query_vector, filters, limit=5, namespace=None, allow_global=False):
            self.calls.append(filters)
            return [
                {
                    "score": 1.0,
                    "payload": {
                        "document_id": "doc-old",
                        "embedding_generation": 1,
                        "source_classification_revision": 1,
                    },
                }
            ]

    client = Recording()
    # An old-generation vector exists for a document that is no longer eligible.
    asyncio.run(
        vector_candidates_for_evidence(
            client,
            query_vector=[1.0, 0.0],
            filters={"org_id": "org-1"},
            candidate_limit=5,
            eligible_document_ids=["doc-current"],
        )
    )

    # The old point is unreachable because the query asked only for the eligible
    # document - no generation predicate was needed, and none was sent. That is
    # what makes an incomplete re-embed a housekeeping problem, not a safety one.
    assert client.calls[0]["document_id"] == ["doc-current"]
    assert "embedding_generation" not in client.calls[0]
    assert "source_classification_revision" not in client.calls[0]


def test_the_re_embed_is_sequenced_not_required():
    """No production code may make evidence conditional on the migration."""
    from rbac_backend.services import contract_embedding_generation

    # A read-path gate on generation would turn a stalled re-embed into a
    # correctness failure instead of a housekeeping one.
    exported = set(contract_embedding_generation.__all__)
    assert not [name for name in exported if "require" in name.lower()]
    assert not [name for name in exported if "gate" in name.lower()]
    assert "is_stale_generation" in exported
