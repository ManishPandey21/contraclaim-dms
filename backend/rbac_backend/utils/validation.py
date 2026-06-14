"""Validation helpers shared across routers/services."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Set

import bleach
from email_validator import EmailNotValidError, validate_email as _lib_validate_email

from .file_validation import sniff_mime_from_bytes
from ..models.document import FileValidationResult

FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
OBJECT_ID_RE = re.compile(r"^[a-f\d]{24}$", re.IGNORECASE)


@dataclass
class PasswordValidationResult:
    is_valid: bool
    error: Optional[str] = None


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe filename."""
    if not name:
        return "upload"
    base = os.path.basename(name)
    base = FILENAME_SAFE_RE.sub("_", base)
    base = base.strip().strip(".")
    return base or "upload"


def sanitize_text(value: Optional[str], *, max_length: int = 2000) -> str:
    if value is None:
        return ""
    text = str(value)
    text = "".join(ch for ch in text if ch.isprintable() or ch in {"\n", "\r", "\t"})
    text = text.strip()
    if max_length and len(text) > max_length:
        text = text[:max_length]
    return text


def sanitize_html(content: Optional[str], allowed_tags: Optional[Set[str]] = None) -> str:
    if not content:
        return ""
    if allowed_tags is None:
        allowed_tags = {
            "p",
            "br",
            "strong",
            "em",
            "b",
            "i",
            "u",
            "ul",
            "ol",
            "li",
            "a",
            "span",
            "div",
            "h1",
            "h2",
            "h3",
        }
    allowed_attributes = {"a": ["href", "title"], "span": ["style"], "div": ["style"]}
    return bleach.clean(
        content,
        tags=list(allowed_tags),
        attributes=allowed_attributes,
        strip=True,
    )


def validate_email(email: str) -> str:
    """
    Normalize and validate an email address.

    Important: We disable DNS deliverability checks (check_deliverability=False)
    so that well-formed emails on example/test domains are accepted for dev/test
    accounts (e.g., superadmin@example.com). This avoids 500s during login flows
    when using seeded users.
    """
    if not email or not isinstance(email, str):
        raise ValueError("Email address is required")
    email = email.strip()
    if not email:
        raise ValueError("Email address cannot be empty")
    try:
        return _lib_validate_email(email, check_deliverability=False).email
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email address: {exc}") from exc


def validate_phone(phone: str) -> str:
    if not phone:
        raise ValueError("Phone number cannot be empty")
    digits = re.sub(r"\D", "", phone)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Phone number must be between 10 and 15 digits")
    return phone.strip()


def validate_pan_number(pan: str) -> str:
    if not pan:
        raise ValueError("PAN number cannot be empty")
    pan = pan.strip().upper()
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan):
        raise ValueError("Invalid PAN number format. Format should be ABCDE1234F")
    return pan


def validate_gst_number(gst: str) -> str:
    if not gst:
        raise ValueError("GST number cannot be empty")
    gst = gst.strip().upper()
    if not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gst):
        raise ValueError("Invalid GST number format. Format should be 12ABCDE1234F1Z5")
    return gst


def validate_input(
    value: Any,
    *,
    required: bool = False,
    max_length: Optional[int] = None,
    min_length: Optional[int] = None,
    pattern: Optional[str] = None,
) -> str:
    if value is None:
        if required:
            raise ValueError("Field is required")
        return ""
    text = str(value).strip()
    if required and not text:
        raise ValueError("Field cannot be empty")
    if max_length and len(text) > max_length:
        raise ValueError(f"Field exceeds maximum length of {max_length}")
    if min_length and len(text) < min_length:
        raise ValueError(f"Field must be at least {min_length} characters")
    if pattern and not re.fullmatch(pattern, text):
        raise ValueError("Field format is invalid")
    return text


def validate_object_id(value: str) -> str:
    if not isinstance(value, str) or not OBJECT_ID_RE.fullmatch(value.strip()):
        raise ValueError("Invalid ObjectId format")
    return value.strip()


def validate_password_strength(password: str) -> PasswordValidationResult:
    if not isinstance(password, str):
        return PasswordValidationResult(False, "Password must be a string")
    pw = password.strip()
    if len(pw) < 8:
        return PasswordValidationResult(False, "Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", pw):
        return PasswordValidationResult(False, "Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", pw):
        return PasswordValidationResult(False, "Password must contain at least one lowercase letter")
    if not re.search(r"\d", pw):
        return PasswordValidationResult(False, "Password must contain at least one digit")
    return PasswordValidationResult(True, None)


def secure_path_join(*paths: str, base_dir: str = "") -> Path:
    if not paths:
        raise ValueError("No paths provided")
    result_path = Path(base_dir) if base_dir else Path()
    for segment in paths:
        if not segment:
            continue
        normalized = str(segment).strip().replace("..", "").lstrip("/\\")
        if normalized:
            result_path /= normalized
    if base_dir:
        base_resolved = Path(base_dir).resolve()
        try:
            result_path.resolve().relative_to(base_resolved)
        except ValueError as exc:
            raise ValueError("Path traversal attempt detected") from exc
    return result_path


async def validate_file(
    content: bytes,
    filename: str,
    allowed_mime_types: Optional[Sequence[str]] = None,
) -> FileValidationResult:
    safe_name = sanitize_filename(filename)
    size = len(content)
    if size == 0:
        return FileValidationResult(
            is_valid=False,
            filename=safe_name,
            file_size=size,
            file_type="unknown",
            mime_type="application/octet-stream",
            error="File is empty",
        )
    mime_type = sniff_mime_from_bytes(content)
    if allowed_mime_types and mime_type not in set(allowed_mime_types):
        return FileValidationResult(
            is_valid=False,
            filename=safe_name,
            file_size=size,
            file_type=os.path.splitext(safe_name)[1].lstrip("."),
            mime_type=mime_type,
            error=f"File type '{mime_type}' is not allowed",
        )
    executable_signatures = [b"\x4d\x5a", b"\x7fELF", b"\xfe\xed\xfa"]
    if any(content.startswith(sig) for sig in executable_signatures):
        return FileValidationResult(
            is_valid=False,
            filename=safe_name,
            file_size=size,
            file_type=os.path.splitext(safe_name)[1].lstrip("."),
            mime_type=mime_type,
            error="Executable files are not allowed",
        )
    return FileValidationResult(
        is_valid=True,
        filename=safe_name,
        file_size=size,
        file_type=os.path.splitext(safe_name)[1].lstrip("."),
        mime_type=mime_type,
    )


def validate_path_security(path: str, base_paths: Sequence[str]) -> bool:
    try:
        target = Path(path).resolve()
        for base in base_paths:
            try:
                target.relative_to(Path(base).resolve())
                return True
            except ValueError:
                continue
    except Exception:
        return False
    return False


__all__ = [
    "sanitize_filename",
    "sanitize_text",
    "sanitize_html",
    "validate_email",
    "validate_phone",
    "validate_pan_number",
    "validate_gst_number",
    "validate_input",
    "validate_object_id",
    "validate_password_strength",
    "secure_path_join",
    "validate_file",
    "validate_path_security",
    "PasswordValidationResult",
]
