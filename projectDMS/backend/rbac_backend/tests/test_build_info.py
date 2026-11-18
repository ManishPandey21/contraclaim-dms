"""JG-6: the backend says which commit it was built from, and only that."""

from __future__ import annotations

import pytest

from rbac_backend.routers.build_info import release_sha


@pytest.mark.parametrize(
    "value, expected",
    [
        ("05a162c3b9d4e1f2a3b4c5d6e7f8091a2b3c4d5e", "05a162c3b9d4e1f2a3b4c5d6e7f8091a2b3c4d5e"),
        ("379E98F", "379e98f"),
        ("  379e98f\n", "379e98f"),
        ("unknown", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("379e98f; rm -rf /", "unknown"),
        ("main", "unknown"),
        ("abc12", "unknown"),
        ("a" * 41, "unknown"),
    ],
)
def test_only_a_commit_id_is_reported(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("RELEASE_SHA", raising=False)
    else:
        monkeypatch.setenv("RELEASE_SHA", value)
    assert release_sha() == expected


def test_the_route_is_mounted_under_api_and_needs_a_signed_in_user() -> None:
    import inspect

    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.routers import build_info

    paths = set()
    for mounted in app.routes:
        candidates = getattr(mounted, "effective_candidates", None)
        for route in (candidates() if callable(candidates) else [mounted]):
            paths.add(getattr(route, "path", ""))
    assert "/api/build" in paths
    parameter = inspect.signature(build_info.get_build).parameters["current_user"]
    assert parameter.default.dependency is get_current_user
