from typing import Optional


# Simple magic-bytes based MIME sniffing without external dependencies
# Supported:
# - PDF: %PDF-
# - PNG: 89 50 4E 47 0D 0A 1A 0A
# - JPEG: FF D8 FF
# - Text: heuristic (no NUL bytes, mostly printable UTF-8)
#
# Returns canonical MIME strings or "application/octet-stream" when unknown.
def sniff_mime_from_bytes(data: bytes, filename: Optional[str] = None) -> str:
    if not data:
        return "application/octet-stream"

    # PDF
    if data.startswith(b"%PDF-"):
        return "application/pdf"

    # DOCX (zip container with extension hint)
    if filename and str(filename).lower().endswith(".docx") and data.startswith(b"PK"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    # RAR: v4 signature is "Rar!\x1a\x07\x00", v5 is "Rar!\x1a\x07\x01\x00".
    if data.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"

    # ZIP container. Checked after the DOCX branch above, which is also a zip
    # and is disambiguated by its filename.
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"

    # PNG
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"

    # JPEG (JFIF/EXIF) - typically starts with 0xFF 0xD8 0xFF
    if len(data) >= 3 and data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
        return "image/jpeg"

    # GIF - "GIF87a" or "GIF89a". Added in R-A8T with WebP below, because
    # `routers/profiles.py` allows both and had no way to check them: it
    # trusted the client's declared `Content-Type` instead, which is the
    # extension-trust bypass in a different field. Neither type appears in any
    # other allowlist in this backend, so recognising them here widens nothing
    # except the surface that asked for them.
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"

    # WebP - a RIFF container whose form type at offset 8 is "WEBP".
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    # Heuristic text detector: reject if any NUL bytes,
    # then try decode and ensure mostly printable characters.
    if b"\x00" not in data:
        sample = data[:4096]  # small sample
        try:
            decoded = sample.decode("utf-8", errors="ignore")
            if not decoded:
                # try latin-1 as fallback
                decoded = sample.decode("latin-1", errors="ignore")
            if decoded:
                # fraction of printable chars
                import string
                printable = set(string.printable)
                printable_count = sum(1 for ch in decoded if ch in printable)
                ratio = printable_count / max(len(decoded), 1)
                if ratio > 0.9:
                    return "text/plain"
        except Exception:
            pass

    # Unknown/unsupported
    return "application/octet-stream"


def is_allowed_mime(mime: Optional[str], allowed: set[str]) -> bool:
    if not mime:
        return False
    return mime in allowed
