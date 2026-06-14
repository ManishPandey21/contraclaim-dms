# Security and Validation Utilities
"""
Comprehensive security utilities for input validation, sanitization, and file handling.
"""

import re
import os
import mimetypes
from pathlib import Path
from typing import Optional, List, Dict, Any, NamedTuple
import hashlib
import magic
from fastapi import HTTPException, status
import logging

logger = logging.getLogger(__name__)

class ValidationResult(NamedTuple):
    """Result of file validation."""
    is_valid: bool
    error: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class SecurityError(Exception):
    """Security-related error."""
    pass


def sanitize_text(text: Optional[str], max_length: int = 10000) -> str:
    """
    Sanitize text input to prevent injection attacks.
    
    Args:
        text: Input text to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized text
        
    Raises:
        SecurityError: If text is too long or contains suspicious content
    """
    if not text:
        return ""
    
    text = str(text).strip()
    
    # Length validation
    if len(text) > max_length:
        raise SecurityError(f"Text too long (max {max_length} characters)")
    
    # Remove potentially dangerous characters and sequences
    dangerous_patterns = [
        r'<script[^>]*>.*?</script>',  # Script tags
        r'javascript:',  # JavaScript URLs
        r'on\w+\s*=',  # Event handlers
        r'<iframe[^>]*>.*?</iframe>',  # Iframes
        r'<object[^>]*>.*?</object>',  # Objects
        r'<embed[^>]*>.*?</embed>',  # Embeds
        r'<meta[^>]*>',  # Meta tags
        r'<link[^>]*>',  # Link tags
    ]
    
    for pattern in dangerous_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Remove null bytes and control characters (except newlines and tabs)
    text = ''.join(char for char in text if ord(char) >= 32 or char in ['\n', '\t'])
    
    return text


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal and other attacks.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
        
    Raises:
        SecurityError: If filename is invalid or suspicious
    """
    if not filename:
        raise SecurityError("Filename cannot be empty")
    
    # Remove path components
    filename = os.path.basename(filename)
    
    # Check for suspicious patterns
    suspicious_patterns = [
        r'\.\./',  # Path traversal
        r'\\',     # Windows path separators
        r'[<>:"|?*]',  # Invalid filename characters
        r'^\.',    # Hidden files
        r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$',  # Reserved Windows names
    ]
    
    for pattern in suspicious_patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            raise SecurityError(f"Invalid filename: {filename}")
    
    # Sanitize characters
    filename = re.sub(r'[^\w\-_\.]', '_', filename)
    
    # Ensure reasonable length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:251-len(ext)] + ext
    
    # Ensure we have a valid filename
    if not filename or filename == '.':
        raise SecurityError("Invalid filename after sanitization")
    
    return filename


async def validate_file(
    content: bytes,
    filename: str,
    allowed_mimes: set,
    max_size: int = 50 * 1024 * 1024  # 50MB default
) -> ValidationResult:
    """
    Comprehensive file validation.
    
    Args:
        content: File content bytes
        filename: Sanitized filename
        allowed_mimes: Set of allowed MIME types
        max_size: Maximum file size in bytes
        
    Returns:
        ValidationResult with validation status
    """
    try:
        # Size validation
        file_size = len(content)
        if file_size == 0:
            return ValidationResult(False, "Empty file")
        
        if file_size > max_size:
            return ValidationResult(False, f"File too large (max {max_size // (1024*1024)}MB)")
        
        # MIME type detection using python-magic for security
        mime_type = magic.from_buffer(content, mime=True)
        
        # Validate against allowed types
        if mime_type not in allowed_mimes:
            return ValidationResult(
                False, 
                f"Unsupported file type: {mime_type}. Allowed: {', '.join(allowed_mimes)}"
            )
        
        # Additional security checks for specific file types
        if mime_type == 'application/pdf':
            if not content.startswith(b'%PDF-'):
                return ValidationResult(False, "Invalid PDF file")
        
        elif mime_type in ['image/jpeg', 'image/png']:
            # Check for valid image headers
            if mime_type == 'image/jpeg' and not content.startswith((b'\xff\xd8\xff')):
                return ValidationResult(False, "Invalid JPEG file")
            elif mime_type == 'image/png' and not content.startswith(b'\x89PNG\r\n\x1a\n'):
                return ValidationResult(False, "Invalid PNG file")
        
        # Check for embedded executables or suspicious content
        suspicious_signatures = [
            b'MZ',  # Windows executable
            b'\x7fELF',  # Linux executable
            b'\xca\xfe\xba\xbe',  # Java class file
            b'PK\x03\x04',  # ZIP archive (could be dangerous)
        ]
        
        for sig in suspicious_signatures:
            if content.startswith(sig) and mime_type != 'application/zip':
                return ValidationResult(False, "Suspicious file content detected")
        
        return ValidationResult(True, None, mime_type, file_size)
        
    except Exception as e:
        logger.error(f"File validation error: {str(e)}")
        return ValidationResult(False, f"Validation error: {str(e)}")


def validate_input(
    value: Any,
    min_length: int = 0,
    max_length: int = 1000,
    pattern: Optional[str] = None,
    required: bool = True
) -> str:
    """
    Generic input validation with pattern matching.
    
    Args:
        value: Input value to validate
        min_length: Minimum string length
        max_length: Maximum string length
        pattern: Regex pattern to match
        required: Whether the field is required
        
    Returns:
        Validated string value
        
    Raises:
        HTTPException: If validation fails
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Required field missing"
            )
        return ""
    
    str_value = str(value).strip()
    
    # Length validation
    if len(str_value) < min_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input too short (minimum {min_length} characters)"
        )
    
    if len(str_value) > max_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input too long (maximum {max_length} characters)"
        )
    
    # Pattern validation
    if pattern and not re.match(pattern, str_value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Input format invalid"
        )
    
    return str_value


def secure_path_join(*parts: str, base_dir: str) -> Path:
    """
    Securely join path components and ensure they stay within base directory.
    
    Args:
        parts: Path components to join
        base_dir: Base directory to restrict to
        
    Returns:
        Secure path within base directory
        
    Raises:
        SecurityError: If path traversal is detected
    """
    # Normalize base directory
    base_path = Path(base_dir).resolve()
    
    # Join and normalize the full path
    try:
        full_path = Path(base_dir)
        for part in parts:
            if not part:
                continue
            # Sanitize each path component
            clean_part = re.sub(r'[<>:"|?*]', '', part)
            full_path = full_path / clean_part
        
        resolved_path = full_path.resolve()
        
        # Ensure the resolved path is within the base directory
        if not str(resolved_path).startswith(str(base_path)):
            raise SecurityError(f"Path traversal detected: {full_path}")
        
        return resolved_path
        
    except (ValueError, OSError) as e:
        raise SecurityError(f"Invalid path: {e}")


def calculate_file_hash(content: bytes, algorithm: str = 'sha256') -> str:
    """
    Calculate cryptographic hash of file content.
    
    Args:
        content: File content bytes
        algorithm: Hash algorithm to use
        
    Returns:
        Hexadecimal hash string
    """
    hasher = hashlib.new(algorithm)
    hasher.update(content)
    return hasher.hexdigest()


def is_safe_redirect_url(url: str, allowed_hosts: List[str]) -> bool:
    """
    Check if a redirect URL is safe (prevents open redirects).
    
    Args:
        url: URL to validate
        allowed_hosts: List of allowed host domains
        
    Returns:
        True if URL is safe for redirection
    """
    if not url:
        return False
    
    # Check for obvious dangerous URLs
    if url.startswith(('javascript:', 'data:', 'vbscript:', 'file:')):
        return False
    
    # Handle relative URLs (generally safe)
    if url.startswith(('/', './')):
        return True
    
    # Parse absolute URLs
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.hostname in allowed_hosts
    except:
        return False