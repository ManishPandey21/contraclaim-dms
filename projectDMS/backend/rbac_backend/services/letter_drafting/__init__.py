"""Letter drafting.

``DraftRunService`` is loaded on first use, not when the package is imported.
Leaf modules here (``prompts`` above all) are imported by lower layers such as
``services.skills.executor``; an eager ``from .service import DraftRunService``
made importing ``letter_drafting.prompts`` load the drafting service, which
loads ``bench``, which imports ``skills.executor`` again: a cycle that broke
``import rbac_backend.services.skills.executor`` (and every Claim Bench module
above it) in a fresh process. tests/test_import_order.py pins this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .service import DraftRunService

__all__ = ["DraftRunService"]


def __getattr__(name: str) -> Any:
    if name == "DraftRunService":
        from .service import DraftRunService

        return DraftRunService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
