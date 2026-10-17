"""Class A at the persistence boundary: the model silently drops the denial.

`_rehydrate_direct_reference` computes `authority_denied` on a hydrated
reference. When that reference is persisted as an `ArbitrationSelectedReference`
(`service.py:173` → `repo.replace_references`), the model had no
`authority_denied` field and Pydantic's default `extra="ignore"` dropped it. So
even the one path that carries the decision loses it the moment it is stored,
and `GET /drafts/{id}` never shows the client that a selected source is blocked.

A safety decision that does not survive `model_validate` → `model_dump` is not
persisted state; it is a build-time coincidence. These round-trip tests pin the
fields the downstream safety logic relies on.
"""

from __future__ import annotations

from rbac_backend.models.arbitration_drafting import ArbitrationSelectedReference

BASE = {
    "source_type": "document",
    "source_id": "doc-1",
    "label": "Delay notice",
}


def _roundtrip(**extra):
    ref = ArbitrationSelectedReference(**{**BASE, **extra})
    dumped = ref.model_dump(by_alias=True)
    # Reload from the persisted shape, exactly as a Mongo read would.
    return ArbitrationSelectedReference(**dumped).model_dump(by_alias=True)


def test_authority_denied_survives_persistence() -> None:
    out = _roundtrip(authority_denied=True)

    assert out.get("authority_denied") is True, (
        "authority_denied was dropped at the model boundary, so a blocked "
        "selected source persisted as if it were clean"
    )


def test_authority_reason_survives_persistence() -> None:
    out = _roundtrip(authority_denied=True, authority_reason="adverse_quality_judgement")

    assert out.get("authority_reason") == "adverse_quality_judgement"


def test_a_clean_reference_defaults_to_not_denied() -> None:
    out = _roundtrip()

    assert out.get("authority_denied") in (False, None)


def test_source_document_provenance_survives_persistence() -> None:
    """The originating document id is what downstream re-resolution needs."""
    out = _roundtrip(source_document_id="doc-1")

    assert out.get("source_document_id") == "doc-1"
