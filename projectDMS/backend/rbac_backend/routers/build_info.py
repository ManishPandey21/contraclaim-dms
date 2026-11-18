"""Which build of the backend is answering (JG-6).

The backend image records the commit it was built from as ``RELEASE_SHA``
(``backend/Dockerfile``: build argument, environment variable and the
``org.opencontainers.image.revision`` label). Until now nothing exposed it, so a
deployed-stack run could not record which backend it measured. This route does.

It sits under ``/api`` because the public gateway proxies only ``/api`` to the
backend (``config/httpd.conf`` answers ``/health`` itself). It needs a signed-in
user, so the build is not disclosed to anonymous callers. Only a commit id is
returned; anything else in the variable is reported as ``unknown``.
"""

from __future__ import annotations

import os
import re
from typing import Dict

from fastapi import APIRouter, Depends

from ..core.security import CurrentUser, get_current_user

router = APIRouter(tags=["build"])

_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def release_sha() -> str:
    """The commit the running image was built from, or ``unknown``."""
    value = str(os.environ.get("RELEASE_SHA") or "").strip().lower()
    return value if _COMMIT.match(value) else "unknown"


@router.get("/build")
async def get_build(current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, str]:
    """The backend's release commit, for deployment evidence."""
    return {"service": "backend", "release_sha": release_sha()}
