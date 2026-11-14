"""Run a short script in a fresh interpreter and read back its JSON verdict.

For tests about interpreter-global state - chiefly OCRmyPDF's pdfminer
monkeypatch, which an ``import ocrmypdf`` installs for the whole process. A
test that imported it inside the pytest process would make every test collected
after it order-dependent, so such tests run their subject in a child instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def run_python_child(code: str, *args: str, cwd: Path) -> Any:
    """Run `code` with `args`; return the JSON on its last stdout line.

    The child gets the backend root on PYTHONPATH so it can import
    ``rbac_backend``, and UTF-8 stdio so non-Latin text survives on Windows.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(BACKEND_ROOT), *filter(None, [env.get("PYTHONPATH")])]
    )
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        env=env,
        cwd=str(cwd),
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    # The last line is the payload; libraries may log above it.
    return json.loads(completed.stdout.strip().splitlines()[-1])
