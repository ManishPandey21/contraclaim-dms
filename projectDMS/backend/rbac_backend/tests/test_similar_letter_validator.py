from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rbac_backend.models.ai_models import SimilarLetter


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (
            datetime(2025, 8, 10, 20, 53, 58, 760000),
            datetime(2025, 8, 10, 20, 53, 58, 760000),
        ),
        (
            "2025-08-10T20:53:58.760000",
            datetime(2025, 8, 10, 20, 53, 58, 760000),
        ),
        (
            1699999999,
            datetime.fromtimestamp(1699999999, tz=timezone.utc),
        ),
        (None, None),
    ],
)
def test_similar_letter_created_at_normalizes_supported_inputs(
    raw_value: datetime | str | int | None,
    expected: datetime | None,
) -> None:
    result = SimilarLetter(
        letter_id="ltr-123",
        score=0.91,
        subject="Example subject",
        snippet="Example snippet",
        created_at=raw_value,
    )

    assert result.created_at == expected
