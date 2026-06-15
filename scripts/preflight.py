#!/usr/bin/env python3
"""Pre-deploy preflight (Week 4.5).

Validates runtime configuration the same way the backend does at startup, then
runs best-effort connectivity checks against the data stores. Exits non-zero if
configuration is invalid (the hard gate); connectivity issues are reported as
warnings so this can run before the data plane is fully up.

    python scripts/preflight.py
"""

from __future__ import annotations

import os
import pathlib
import sys
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

failures = 0
warnings = 0


def _pass(msg: str) -> None:
    print(f"PASS: {msg}")


def _warn(msg: str) -> None:
    global warnings
    warnings += 1
    print(f"WARN: {msg}")


def _fail(msg: str) -> None:
    global failures
    failures += 1
    print(f"FAIL: {msg}")


def check_config():
    try:
        from rbac_backend.core.config import settings
    except Exception as exc:  # noqa: BLE001
        _fail(f"could not import settings: {exc}")
        return None
    try:
        settings.validate_runtime_configuration()
        _pass(f"runtime configuration valid (ENVIRONMENT={settings.ENVIRONMENT})")
    except Exception as exc:  # noqa: BLE001
        _fail(f"invalid runtime configuration: {exc}")
    return settings


def check_qdrant(settings) -> None:
    url = os.getenv("QDRANT_URL") or os.getenv("VECTORDB_URL")
    if not url:
        _warn("QDRANT_URL not set; skipping vector store check")
        return
    try:
        with urlopen(f"{url.rstrip('/')}/readyz", timeout=5) as resp:
            if resp.status == 200:
                _pass(f"Qdrant reachable at {url}")
            else:
                _warn(f"Qdrant returned HTTP {resp.status}")
    except (URLError, HTTPError, TimeoutError) as exc:
        _warn(f"Qdrant not reachable: {exc}")


def check_mongo(settings) -> None:
    try:
        import asyncio

        import motor.motor_asyncio
    except Exception:  # noqa: BLE001
        _warn("motor not installed; skipping MongoDB check")
        return

    async def _ping() -> None:
        client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.DATABASE_URL, serverSelectionTimeoutMS=3000
        )
        try:
            await client.admin.command("ping")
        finally:
            client.close()

    try:
        asyncio.run(_ping())
        _pass("MongoDB reachable")
    except Exception as exc:  # noqa: BLE001
        _warn(f"MongoDB not reachable: {exc}")


def main() -> int:
    print("== ContraclaimDMS preflight ==")
    settings = check_config()
    if settings is not None:
        check_mongo(settings)
        check_qdrant(settings)
    print(f"\nSummary: {failures} failure(s), {warnings} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
