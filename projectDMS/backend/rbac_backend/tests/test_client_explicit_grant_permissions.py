"""T-2: the client's explicit-grant list is the server's (``SUPERADMIN_EXCLUDED_PERMISSIONS``).

``client/src/hooks/useRBAC.ts`` never lets ``*`` or the Super Admin role pass these
permissions; if the two lists drift, the UI would offer (or hide) what the backend
decides differently.
"""

from __future__ import annotations

import re
from pathlib import Path

from rbac_backend.core.permissions import SUPERADMIN_EXCLUDED_PERMISSIONS

CLIENT_FILE = Path(__file__).resolve().parents[3] / "client" / "src" / "config" / "rolePermissions.ts"


def test_client_explicit_grant_permissions_match_the_server() -> None:
    source = CLIENT_FILE.read_text(encoding="utf-8")
    block = re.search(r"EXPLICIT_GRANT_PERMISSIONS[^=]*=\s*new Set\(\[(.*?)\]\)", source, re.S)
    assert block, "EXPLICIT_GRANT_PERMISSIONS not found in the client"
    client = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert client == set(SUPERADMIN_EXCLUDED_PERMISSIONS)
