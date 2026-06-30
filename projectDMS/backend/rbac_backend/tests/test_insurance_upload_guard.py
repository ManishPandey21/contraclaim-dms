"""Security: the insurance file-serving token must not allow path traversal."""

from __future__ import annotations

import pytest

try:
    from fastapi import HTTPException

    from rbac_backend.routers.insurance import _insurance_dir, _insurance_file_path

    _IMPORTS_OK = True
except Exception:  # pragma: no cover - app import unavailable
    _IMPORTS_OK = False

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="insurance router unavailable")


@pytest.mark.parametrize(
    "token",
    ["../secret", "..\\secret", "a/b", "a\\b", "", None, "sub/../../etc/passwd"],
)
def test_rejects_traversal_tokens(token):
    with pytest.raises(HTTPException) as exc:
        _insurance_file_path(token)
    assert exc.value.status_code == 400


def test_accepts_plain_token_within_dir():
    path = _insurance_file_path("abc123.pdf")
    assert path.parent == _insurance_dir().resolve()
    assert path.name == "abc123.pdf"
