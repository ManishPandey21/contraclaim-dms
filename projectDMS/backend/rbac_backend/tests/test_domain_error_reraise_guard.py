"""A router must not re-raise one domain error and swallow its siblings.

The shape that keeps reappearing::

    except (DocumentError, HTTPException):
        raise
    except Exception:
        raise HTTPException(500, "... temporarily unavailable")

Every domain error carries its own ``http_status`` and ``handle_exceptions``
renders it faithfully, so naming a single subclass in the re-raise tuple sends
every *other* domain error -- ``AuthorizationError`` at 403, ``RateLimitError``
at 429, ``ValidationError`` at 422 -- into the catch-all, where a deliberate
refusal is reported to the client as a service outage.

Catching ``BaseDomainError`` covers the whole family and is a strict superset of
whatever single class was named before, so widening can never narrow behaviour.

This is a static guard rather than a behavioural test because the defect is a
shape, not a value: it costs one AST walk to make the whole class un-mergeable.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from rbac_backend.utils import error_handler

ROUTERS = pathlib.Path(error_handler.__file__).resolve().parent.parent / "routers"

# Names that are BaseDomainError subclasses; catching any one of them alone is
# the defect. Resolved from the module rather than hardcoded so a newly added
# domain error is covered the day it is defined.
DOMAIN_ERROR_NAMES = {
    name
    for name, obj in vars(error_handler).items()
    if isinstance(obj, type)
    and issubclass(obj, error_handler.BaseDomainError)
    and obj is not error_handler.BaseDomainError
}


def _narrow_handlers(path: pathlib.Path):
    """Yield (lineno, caught-name) for each too-narrow re-raise tuple."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not isinstance(node.type, ast.Tuple):
            continue
        names = {e.id for e in node.type.elts if isinstance(e, ast.Name)}
        if "HTTPException" not in names:
            continue
        if "BaseDomainError" in names:
            continue
        for offender in names & DOMAIN_ERROR_NAMES:
            yield node.lineno, offender


def test_domain_error_names_were_actually_discovered():
    """Guard the guard: an empty name set would make this test vacuous."""
    assert "DocumentError" in DOMAIN_ERROR_NAMES
    assert "AuthorizationError" in DOMAIN_ERROR_NAMES
    assert len(DOMAIN_ERROR_NAMES) >= 5


@pytest.mark.parametrize(
    "router", sorted(p.name for p in ROUTERS.glob("*.py") if p.name != "__init__.py")
)
def test_router_reraises_the_whole_domain_error_family(router):
    offenders = list(_narrow_handlers(ROUTERS / router))
    assert not offenders, "\n".join(
        f"{router}:{line} catches ({name}, HTTPException) -- other domain errors "
        f"fall through to the catch-all and are reported as 500. "
        f"Use (BaseDomainError, HTTPException)."
        for line, name in offenders
    )
