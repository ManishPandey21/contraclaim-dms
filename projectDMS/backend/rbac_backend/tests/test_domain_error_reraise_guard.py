"""A router must not let a domain error fall into a catch-all that reports 500.

R-A8W Stage B, F-A8W-B3. An org admin reading a foreign organisation by id got
HTTP 500 instead of a refusal. `AuthorizationService.check_organization_access`
raised `AuthorizationError` (403), and `OrganizationController.get_organization`
read::

    except (OrganizationError, HTTPException):
        raise
    except Exception:
        raise HTTPException(500, "Organization service temporarily unavailable")

`AuthorizationError` is a sibling of `OrganizationError`, not a subclass, so it
went to the catch-all. No row leaked; the refusal contract broke, and a client
cannot tell "you may not" from "we are down".

`CLAUDE.md` names this guard, but on this branch it did not exist: it lived only
in an orphan commit (`1d5a7ea`) that no branch contains. That version matched one
shape - a tuple holding a single domain error beside `HTTPException` - and the
release branch has 145 sites across 28 routers, many of them spelled
`except HTTPException: raise`, which lets *every* domain error through to the
catch-all and which that guard could not see. This one is re-derived from the
property rather than copied from the shape:

    In `routers/`, a `try` whose broad handler raises a new exception must
    re-raise the whole `BaseDomainError` family first, whenever it already
    re-raises anything by name (it has declared that statuses matter) or its
    body calls an authorisation seam directly (it can raise a refusal).

`handle_exceptions` - and, as a backstop, the application's own exception
handler - render any `BaseDomainError` at its `http_status`, so catching the base
class is a strict superset of whatever was named and cannot narrow behaviour.
An AST walk rather than a regex, because the defect is a shape.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Iterator

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
ROUTERS = PACKAGE / "routers"
ERROR_HANDLER = PACKAGE / "utils" / "error_handler.py"

BROAD = {"Exception", "BaseException"}
BASE = "BaseDomainError"

#: A call to one of these, directly inside a `try` body, can raise a refusal.
AUTHORIZATION_SEAM = re.compile(
    r"^(authorize\w*|check_\w*access\w*|require_\w+|build_\w+_query"
    r"|ensure_\w*(access|scope|permission)\w*|assert_\w*(access|scope)\w*)$"
)


def _domain_error_names() -> set[str]:
    """Every class in the package that is a `BaseDomainError`, by name.

    Resolved from source rather than from an import, so a subclass declared in
    a service module is covered without importing every router.
    """
    bases: dict[str, set[str]] = {}
    for path in PACKAGE.rglob("*.py"):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                    for base in node.bases
                )
    family = {BASE}
    grew = True
    while grew:
        grew = False
        for name, parents in bases.items():
            if name not in family and parents & family:
                family.add(name)
                grew = True
    return family


DOMAIN_ERRORS = _domain_error_names()


def _caught(handler: ast.ExceptHandler) -> set[str]:
    node = handler.type
    if node is None:
        return {"<bare>"}
    elements = node.elts if isinstance(node, ast.Tuple) else [node]
    return {ast.unparse(element).rsplit(".", 1)[-1] for element in elements}


def _reraises(handler: ast.ExceptHandler) -> bool:
    """The handler ends by re-raising what it caught, whatever it did first."""
    last = handler.body[-1]
    return isinstance(last, ast.Raise) and last.exc is None


def _raises_new(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(node, ast.Raise) and node.exc is not None for node in ast.walk(handler))


def _direct_calls(statements: list[ast.stmt]) -> set[str]:
    """Names called in these statements, not inside nested function bodies."""
    found: set[str] = set()
    stack: list[ast.AST] = list(statements)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                found.add(func.attr)
            elif isinstance(func, ast.Name):
                found.add(func.id)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _direct_domain_raises(statements: list[ast.stmt]) -> list[str]:
    """Domain errors raised directly in these statements.

    Nested functions and nested `try` blocks are skipped: an inner handler may
    legitimately catch what its own body raises.
    """
    found: list[str] = []
    stack: list[ast.AST] = list(statements)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.Try)):
            continue
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in DOMAIN_ERRORS:
                found.append(name)
        stack.extend(ast.iter_child_nodes(node))
    return found


def violations(source: str) -> Iterator[tuple[int, str]]:
    """Yield (line, reason) for every `try` that can turn a refusal into a 500."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        handlers = node.handlers
        broad_at = next(
            (
                index
                for index, handler in enumerate(handlers)
                if (_caught(handler) & (BROAD | {"<bare>"})) and not _reraises(handler)
            ),
            None,
        )
        if broad_at is None or not _raises_new(handlers[broad_at]):
            continue
        preserved: set[str] = set()
        for handler in handlers[:broad_at]:
            if _reraises(handler):
                preserved |= _caught(handler)
        if BASE in preserved:
            continue
        named = preserved & ((DOMAIN_ERRORS - {BASE}) | {"HTTPException"})
        if named:
            yield (
                handlers[broad_at].lineno,
                f"re-raises only {sorted(named)} before a catch-all that raises a new "
                f"exception, so every other domain error is reported as that exception",
            )
            continue
        seams = sorted(call for call in _direct_calls(node.body) if AUTHORIZATION_SEAM.match(call))
        if seams:
            yield (
                handlers[broad_at].lineno,
                f"calls {seams} inside a try whose catch-all raises a new exception and "
                f"re-raises nothing first, so a refusal is reported as that exception",
            )
            continue
        raised = sorted(set(_direct_domain_raises(node.body)) - preserved)
        if raised:
            yield (
                handlers[broad_at].lineno,
                f"raises {raised} in its own body and the catch-all rewrites it as a new "
                f"exception, so that domain error never reaches the client at its status",
            )


# --------------------------------------------------------------------------- #
# Guard the guard
# --------------------------------------------------------------------------- #


def test_the_domain_error_family_was_actually_discovered() -> None:
    """An empty family would make every assertion below vacuous."""
    for name in ("AuthorizationError", "OrganizationError", "RateLimitError", "ValidationError"):
        assert name in DOMAIN_ERRORS, f"{name} is not recognised as a BaseDomainError"
    assert len(DOMAIN_ERRORS) >= 10


NARROW = """
async def f():
    try:
        await auth.check_organization_access(user, org, "read")
    except (OrganizationError, HTTPException):
        raise
    except Exception:
        raise HTTPException(500, "unavailable")
"""

HTTP_ONLY = """
async def f():
    try:
        await service.work()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "unavailable")
"""

AUTH_CALL_NO_RERAISE = """
async def f():
    try:
        await self.policy_service.authorize(user, perm)
    except Exception:
        raise HTTPException(500, "unavailable")
"""

WIDENED = """
async def f():
    try:
        await auth.check_organization_access(user, org, "read")
    except (BaseDomainError, HTTPException):
        raise
    except Exception:
        raise HTTPException(500, "unavailable")
"""

COMPENSATING = """
async def f():
    try:
        await self.policy_service.authorize(user, perm)
    except (BaseDomainError, HTTPException):
        await compensate()
        raise
    except Exception:
        raise HTTPException(500, "unavailable")
"""

BODY_DOMAIN_RAISE = """
async def f():
    try:
        if missing:
            raise DocumentError("Missing required columns", 400)
    except pd.errors.EmptyDataError:
        raise DocumentError("CSV file is empty", 400)
    except Exception as e:
        raise DocumentError(f"CSV processing failed: {e}", 500)
"""

SWALLOWING = """
async def f():
    try:
        await service.work()
    except HTTPException:
        raise
    except Exception:
        logger.warning("degraded")
        return []
"""


@pytest.mark.parametrize(
    "source, expected",
    [
        (NARROW, 1),
        (HTTP_ONLY, 1),
        (AUTH_CALL_NO_RERAISE, 1),
        (WIDENED, 0),
        (COMPENSATING, 0),
        (SWALLOWING, 0),
        (BODY_DOMAIN_RAISE, 1),
    ],
    ids=[
        "narrow-tuple",
        "http-only",
        "auth-call-no-reraise",
        "widened",
        "compensating",
        "swallowing",
        "body-domain-raise",
    ],
)
def test_the_guard_recognises_each_shape(source: str, expected: int) -> None:
    """Synthetic controls: each shape the property names, and each it excludes.

    A swallowing catch-all is a different defect (silent degradation) and is
    not this guard's claim; it is excluded so the guard does not overreach.
    """
    assert len(list(violations(source))) == expected


# --------------------------------------------------------------------------- #
# The real routers
# --------------------------------------------------------------------------- #

ROUTER_FILES = sorted(path.name for path in ROUTERS.glob("*.py") if path.name != "__init__.py")


@pytest.mark.parametrize("router", ROUTER_FILES)
def test_router_reraises_the_whole_domain_error_family(router: str) -> None:
    offenders = list(violations((ROUTERS / router).read_text(encoding="utf-8")))
    assert not offenders, "\n".join(
        f"routers/{router}:{line} {reason}. Re-raise (BaseDomainError, HTTPException) first."
        for line, reason in offenders
    )


def test_the_walk_reaches_the_sites_it_protects() -> None:
    """Anti-vacuity: the routers really do carry the widened re-raise.

    If a refactor moved every handler somewhere this guard does not look, the
    parametrised test above would pass over nothing.
    """
    widened = 0
    for name in ROUTER_FILES:
        tree = ast.parse((ROUTERS / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _reraises(node) and BASE in _caught(node):
                widened += 1
    assert widened >= 140, f"only {widened} widened re-raise handlers found under routers/"


def test_reverting_the_organization_fix_is_caught() -> None:
    """Mutation control on the exact site R-A8W measured.

    Narrow `get_organization`'s re-raise back to what it was, and the guard must
    go red on that file.
    """
    source = (ROUTERS / "organizations.py").read_text(encoding="utf-8")
    marker = 'raise OrganizationError(\n                    "Organization not found",'
    start = source.index("async def get_organization(")
    end = source.index("async def update_organization(")
    method = source[start:end]
    assert marker in method and "except (BaseDomainError, HTTPException):" in method
    mutated = source[:start] + method.replace(
        "except (BaseDomainError, HTTPException):", "except (OrganizationError, HTTPException):", 1
    ) + source[end:]
    assert list(violations(mutated)), "narrowing get_organization's re-raise went unnoticed"
