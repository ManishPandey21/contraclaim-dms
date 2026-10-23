"""Every audit call site in the tree must bind against the real signature.

F-A8M-6 was one instance of a class. ``AuditLogger`` exposes ~50 convenience
wrappers whose tails are ``**details``; routers call them positionally; and every
one of those calls sits inside a broad ``except Exception`` on a *success* path.
So a wrapper that grows a parameter, or a caller that passes one extra
positional argument, produces a ``TypeError`` **after** the request has already
mutated state, and the handler answers 500 for work that succeeded.

The instance R-A8M measured was ``POST /api/refresh``. Binding every call site
in this tree against ``inspect.signature`` found **nine**: the refresh call and
eight more on create/send/clear paths that no test exercised either.

This is a static gate on purpose. Covering these paths with request tests would
be better and is not what closes the class — the class is closed by making an
unbindable call impossible to commit, whether or not anyone writes a test for
the handler that carries it.

Deliberately conservative: a call with ``*args`` or ``**kwargs`` splatted in is
skipped, because its shape is not knowable statically, and a name that is not an
``AuditLogger`` attribute is reported rather than assumed to be someone else's
logger.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

import pytest

from rbac_backend.utils.audit_logger import AuditLogger

#: Package root, walked for production modules. `tests/` is excluded: a test may
#: legitimately call a stand-in with a different shape.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: Attribute/variable names that hold an ``AuditLogger``. Narrow by design — a
#: wider net would start binding unrelated ``log_*`` methods against the wrong
#: signature and report failures that are not failures.
AUDIT_HOLDER_NAMES = frozenset(
    {"audit_logger", "audit", "_audit_logger", "_audit", "audit_log"}
)

#: The factory, when a call site skips the attribute entirely.
AUDIT_FACTORY_NAMES = frozenset({"get_audit_logger"})

#: Floor for the self-check below. The tree carried 64 bindable sites when this
#: gate was written; the floor is deliberately lower so ordinary deletions do
#: not fail the build, but high enough that a detector which silently stops
#: matching cannot pass vacuously.
MINIMUM_EXPECTED_CALL_SITES = 40


class CallSite(NamedTuple):
    path: Path
    lineno: int
    method: str
    positional: int
    keywords: tuple


def _audit_method_name(node: ast.Call) -> Optional[str]:
    """Return the audit method this call targets, or None if it is not one."""
    func = node.func
    if not isinstance(func, ast.Attribute) or not func.attr.startswith("log_"):
        return None

    owner = func.value
    if isinstance(owner, ast.Attribute) and owner.attr in AUDIT_HOLDER_NAMES:
        return func.attr
    if isinstance(owner, ast.Name) and owner.id in AUDIT_HOLDER_NAMES:
        return func.attr
    if isinstance(owner, ast.Call):
        factory = owner.func
        if isinstance(factory, ast.Name) and factory.id in AUDIT_FACTORY_NAMES:
            return func.attr
        if isinstance(factory, ast.Attribute) and factory.attr in AUDIT_FACTORY_NAMES:
            return func.attr
    return None


def _production_modules() -> Iterator[Path]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        parts = path.relative_to(PACKAGE_ROOT).parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        yield path


def _read_source(path: Path) -> str:
    # `utf-8-sig` rather than `utf-8`: several modules in this tree carry a BOM,
    # and reading them as plain utf-8 makes them look unparseable, which would
    # silently drop their call sites from this gate.
    return path.read_text(encoding="utf-8-sig")


def collect_call_sites(root_source: Optional[str] = None) -> list:
    """Every statically-shaped audit call in the package.

    ``root_source`` is the negative-control seam: pass a module body and the
    same detector runs over it alone.
    """
    sites: list = []
    sources = (
        [(Path("<synthetic>"), root_source)]
        if root_source is not None
        else [(p, _read_source(p)) for p in _production_modules()]
    )
    for path, source in sources:
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method = _audit_method_name(node)
            if method is None:
                continue
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue
            sites.append(
                CallSite(
                    path=path,
                    lineno=node.lineno,
                    method=method,
                    positional=len(node.args),
                    keywords=tuple(kw.arg for kw in node.keywords),
                )
            )
    return sites


def binding_failure(site: CallSite) -> Optional[str]:
    """The reason this call would raise at runtime, or None if it binds."""
    method = getattr(AuditLogger, site.method, None)
    if method is None:
        return f"AuditLogger has no method {site.method!r}"
    signature = inspect.signature(method)
    # +1 for the bound `self` that an attribute call supplies.
    positional = [object()] * (site.positional + 1)
    keywords = {name: object() for name in site.keywords}
    try:
        signature.bind(*positional, **keywords)
    except TypeError as exc:
        return str(exc)
    return None


# --- The gate --------------------------------------------------------------


def test_every_audit_call_site_binds_against_the_real_signature():
    failures = [
        f"{site.path.relative_to(PACKAGE_ROOT)}:{site.lineno}: "
        f"{site.method}({site.positional} positional, "
        f"{list(site.keywords)}) -> {reason}"
        for site in collect_call_sites()
        if (reason := binding_failure(site)) is not None
    ]
    assert not failures, (
        "audit call sites that raise TypeError at runtime — each one turns a "
        "completed request into a 500:\n  " + "\n  ".join(failures)
    )


def test_every_production_module_is_parseable_by_this_gate():
    """A module this gate cannot read contributes nothing and says nothing.

    This is the property that made the first draft of the scan under-report:
    seven BOM-prefixed modules raised ``SyntaxError`` and were skipped.
    """
    unparseable = []
    for path in _production_modules():
        try:
            ast.parse(_read_source(path), filename=str(path))
        except SyntaxError as exc:
            unparseable.append(f"{path.relative_to(PACKAGE_ROOT)}: {exc}")
    assert not unparseable, "modules this gate cannot inspect:\n  " + "\n  ".join(
        unparseable
    )


def test_the_detector_actually_finds_call_sites():
    """Self-check: a detector that matches nothing passes every assertion."""
    sites = collect_call_sites()
    assert len(sites) >= MINIMUM_EXPECTED_CALL_SITES, (
        f"only {len(sites)} audit call sites detected; the gate is no longer "
        "reading this tree"
    )
    assert any("routers" in site.path.parts for site in sites)


# --- Negative controls -----------------------------------------------------


@pytest.mark.parametrize(
    "source, expected_fragment",
    [
        pytest.param(
            "await self.audit_logger.log_token_refreshed(user.id)\n",
            "session_id",
            id="the-F-A8M-6-shape-one-argument-short",
        ),
        pytest.param(
            "await self.audit_logger.log_cache_cleared(user.id, entries)\n",
            "too many positional arguments",
            id="one-positional-argument-too-many",
        ),
        pytest.param(
            "await self.audit_logger.log_no_such_event(user.id)\n",
            "no method",
            id="a-wrapper-that-does-not-exist",
        ),
    ],
)
def test_a_bad_call_is_caught(source, expected_fragment):
    body = "async def handler(self, user, entries):\n    " + source
    sites = collect_call_sites(root_source=body)
    assert len(sites) == 1
    reason = binding_failure(sites[0])
    assert reason is not None, "the gate accepted a call that raises at runtime"
    assert expected_fragment in reason


def test_a_correct_call_is_not_flagged():
    body = (
        "async def handler(self, user, session_id):\n"
        "    await self.audit_logger.log_token_refreshed(user.id, session_id)\n"
    )
    sites = collect_call_sites(root_source=body)
    assert len(sites) == 1
    assert binding_failure(sites[0]) is None


def test_unknowable_shapes_are_skipped_rather_than_guessed():
    body = (
        "async def handler(self, args, kwargs):\n"
        "    await self.audit_logger.log_user_created(*args)\n"
        "    await self.audit_logger.log_user_created(**kwargs)\n"
    )
    assert collect_call_sites(root_source=body) == []


def test_an_unrelated_logger_is_not_bound_against_audit_signatures():
    body = (
        "async def handler(self, user):\n"
        "    await self.metrics_logger.log_user_created(user.id)\n"
        "    logger.log_user_created(user.id)\n"
    )
    assert collect_call_sites(root_source=body) == []
