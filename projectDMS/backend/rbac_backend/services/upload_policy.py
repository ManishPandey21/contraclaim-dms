"""The canonical supported-file policy.

The backend is the single source of truth. The client fetches this at runtime
rather than shipping its own list, because the allowlists are env-overridable
and a build-time copy would reflect defaults instead of deployed config.

The drift this replaces was real: the picker offered .doc/.docx/.gif while the
backend's allowlist rejected all three, so a user could select a file and only
learn it was unsupported after the upload finished with a 415.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from ..core.config import settings

MIME_EXTENSIONS: Dict[str, List[str]] = {
    "application/pdf": [".pdf"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "text/plain": [".txt"],
    "application/zip": [".zip"],
    "application/vnd.rar": [".rar"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [
        ".docx"
    ],
}


class UploadPolicyService:
    @staticmethod
    def _entry(mimes: Set[str], max_size_mb: int) -> Dict[str, Any]:
        ordered = sorted(mimes)
        extensions: List[str] = []
        for mime in ordered:
            for extension in MIME_EXTENSIONS.get(mime, []):
                if extension not in extensions:
                    extensions.append(extension)
        return {
            "mimes": ordered,
            "extensions": sorted(extensions),
            "max_size_mb": int(max_size_mb),
        }

    @classmethod
    def get_policy(cls) -> Dict[str, Dict[str, Any]]:
        general_max = int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)
        return {
            "document": cls._entry(set(settings.ALLOWED_DOCUMENT_MIMES), general_max),
            "enclosure": cls._entry(set(settings.ALLOWED_ENCLOSURE_MIMES), general_max),
            "contract": cls._entry(set(settings.ALLOWED_CONTRACT_MIMES), general_max),
            "version": cls._entry(set(settings.ALLOWED_DOCUMENT_MIMES), general_max),
        }
