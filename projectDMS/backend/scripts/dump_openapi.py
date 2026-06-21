"""Dump the backend OpenAPI schema to client/openapi.json.

Source of truth for the generated frontend types (see docs/SHARED_TYPES.md).
Run from the repo root:

    python backend/scripts/dump_openapi.py

Requires the same env the app needs (SECRET_KEY, AWS_* may be placeholders for a
schema-only dump). Does not start the server or touch the database.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

# Make `backend.rbac_backend...` importable when run as a file from anywhere.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Allow a schema-only dump without real secrets.
os.environ.setdefault("SECRET_KEY", "dev-secret-0123456789")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "x")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "y")
os.environ.setdefault("AWS_BUCKET_NAME", "b")
os.environ.setdefault("ENVIRONMENT", "development")


def main() -> None:
    from backend.rbac_backend.main import app

    out = pathlib.Path(__file__).resolve().parents[2] / "client" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
