from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path


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
