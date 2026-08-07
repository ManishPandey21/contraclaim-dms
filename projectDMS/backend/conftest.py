from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def pytest_pyfunc_call(pyfuncitem):
    """Run coroutine tests without requiring pytest-asyncio."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    testargs = {
        arg: pyfuncitem.funcargs[arg]
        for arg in pyfuncitem._fixtureinfo.argnames
        if arg in pyfuncitem.funcargs
    }
    asyncio.run(pyfuncitem.obj(**testargs))
    return True


@pytest.fixture(autouse=True)
def _reset_cached_mongo_client():
    """Stop a cached Motor client outliving the loop that created it.

    ``pytest_pyfunc_call`` gives every coroutine test its own ``asyncio.run``
    loop, but ``core.database`` caches its client and database in module
    globals. Without this reset the second test to reach Mongo reuses a client
    bound to the previous test's *closed* loop and dies with "Event loop is
    closed" -- so tests pass alone and fail in a suite, which hides real
    regressions behind ordering noise.

    The globals are cleared rather than closed: ``close()`` would touch the old
    loop, and a per-test client is cheap in a test process.
    """
    yield
    database_module = sys.modules.get("rbac_backend.core.database")
    if database_module is None:
        return
    database_module.client = None
    database_module.database = None
    database_module._index_task = None
