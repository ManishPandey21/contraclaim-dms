import pytest
from fastapi import HTTPException

from rbac_backend.routers.ai_assistant import _require_platform_admin


class _User:
    def __init__(self, roles):
        self.roles = roles


def test_ai_admin_guard_allows_superadmin():
    _require_platform_admin(_User(["superadmin"]))


def test_ai_admin_guard_rejects_non_superadmin():
    with pytest.raises(HTTPException) as exc:
        _require_platform_admin(_User(["orgadmin"]))

    assert exc.value.status_code == 403
