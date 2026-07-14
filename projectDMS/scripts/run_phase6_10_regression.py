#!/usr/bin/env python
"""Run the Phase 6-10 hardening regression suite.

This suite is intentionally narrower than the full CI matrix. It covers the
role-management, frontend access-control, background-worker, audit/admin-review,
billing-webhook, and arbitration regressions changed during Phases 6-10.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
SYSTEM_PYTHON = Path(sys.executable)


BACKEND_COMPILE_TARGETS = [
    "backend/rbac_backend/core/config.py",
    "backend/rbac_backend/core/permissions.py",
    "backend/rbac_backend/models/arbitration_drafting.py",
    "backend/rbac_backend/routers/reports.py",
    "backend/rbac_backend/routers/roles.py",
    "backend/rbac_backend/services/arbitration_drafting/context.py",
    "backend/rbac_backend/services/arbitration_drafting/validator.py",
    "backend/rbac_backend/services/audit_event_service.py",
    "backend/rbac_backend/services/billing_webhook_service.py",
    "backend/rbac_backend/services/contract_ingest_queue.py",
    "backend/rbac_backend/services/document_service.py",
    "backend/rbac_backend/services/observability.py",
    "backend/rbac_backend/services/policy_service.py",
    "backend/rbac_backend/services/role_service.py",
]


BACKEND_PHASE_TESTS = [
    "backend/rbac_backend/tests/test_role_management_phase6.py",
    "backend/rbac_backend/tests/test_rbac_policy_hardening.py",
    "backend/rbac_backend/tests/test_rbac_subscription_phase0_baseline.py",
    "backend/rbac_backend/tests/test_role_permission_catalog_drift.py",
    "backend/rbac_backend/tests/test_contract_ingest_queue_visibility.py",
    "backend/rbac_backend/tests/test_document_processing_jobs.py",
    "backend/rbac_backend/tests/test_langchain_vector_service.py",
    "backend/rbac_backend/tests/test_audit_admin_review.py",
    "backend/rbac_backend/tests/test_audit_export.py",
    "backend/rbac_backend/tests/test_observability.py",
    "backend/rbac_backend/tests/test_billing_webhooks.py",
    "backend/rbac_backend/tests/test_arbitration_drafting.py",
    "backend/rbac_backend/tests/test_route_inventory.py",
]


FRONTEND_PHASE_TESTS = [
    "src/config/__tests__/rolePermissions.sidebar.test.ts",
    "src/config/__tests__/routeInventory.test.ts",
    "src/pages/__tests__/ArbitrationDraftingPage.test.tsx",
    "src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx",
]


@dataclass
class StepResult:
    name: str
    command: list[str]
    cwd: str
    exit_code: int
    duration_seconds: float


def _python() -> Path:
    return BACKEND_PYTHON if BACKEND_PYTHON.exists() else SYSTEM_PYTHON


def _tool(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if os.name == "nt":
        resolved = shutil.which(f"{name}.cmd")
        if resolved:
            return resolved
    return name


def _run(name: str, command: Sequence[str], cwd: Path, *, env: dict[str, str] | None = None) -> StepResult:
    started = time.monotonic()
    print(f"\n=== {name} ===")
    print(f"cwd: {cwd}")
    print("cmd:", " ".join(command))
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        env={**os.environ, **(env or {})},
    )
    elapsed = round(time.monotonic() - started, 2)
    result = StepResult(
        name=name,
        command=list(command),
        cwd=str(cwd),
        exit_code=completed.returncode,
        duration_seconds=elapsed,
    )
    print(f"exit: {completed.returncode} ({elapsed}s)")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 6-10 regression checks.")
    parser.add_argument("--skip-frontend", action="store_true", help="Run backend checks only.")
    parser.add_argument("--skip-build", action="store_true", help="Skip the frontend production build.")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary at the end.")
    args = parser.parse_args()

    py = str(_python())
    results: list[StepResult] = []
    results.append(
        _run(
            "backend compile",
            [py, "-m", "py_compile", *BACKEND_COMPILE_TARGETS],
            ROOT,
        )
    )
    results.append(
        _run(
            "backend phase tests",
            [py, "-m", "pytest", *BACKEND_PHASE_TESTS],
            ROOT,
        )
    )

    if not args.skip_frontend:
        client_dir = ROOT / "client"
        results.append(
            _run(
                "frontend phase tests",
                [_tool("npm"), "test", "--", "--run", *FRONTEND_PHASE_TESTS],
                client_dir,
                env={"CI": "1", "NODE_OPTIONS": "--max-old-space-size=4096"},
            )
        )
        if not args.skip_build:
            results.append(
                _run(
                    "frontend build",
                    [_tool("npm"), "run", "build"],
                    client_dir,
                    env={"NODE_OPTIONS": "--max-old-space-size=4096"},
                )
            )

    failed = [result for result in results if result.exit_code != 0]
    summary = {
        "suite": "phase6-10-regression",
        "status": "failed" if failed else "passed",
        "steps": [asdict(result) for result in results],
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("\n=== summary ===")
        for result in results:
            status = "PASS" if result.exit_code == 0 else "FAIL"
            print(f"{status}: {result.name} ({result.duration_seconds}s)")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
