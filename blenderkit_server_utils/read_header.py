"""Search for PolyHeaven assets.

Check if sourceAppVersion is 1.0 or 3.0 and set 4.5.

If binary file try to detect real version from header,
We need to download the file for that.
"""

from __future__ import annotations

import gzip
import re
from typing import Dict  # noqa: UP035

try:
    import zstandard as zstd

    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False


BLENDER_MAGIC = b"BLENDER"
MAX_HEADER_SCAN = 64

GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


class BlendHeaderError(RuntimeError):
    """Custom exception for Blender header reading errors."""


def _read_bytes(stream, size: int) -> bytes:
    data = stream.read(size)
    if not data:
        raise BlendHeaderError("Empty or truncated file")
    return data


def _extract_version(header: bytes) -> str:
    """Extract Blender version from arbitrary-length Blender header.

    Supports legacy and modern formats.

    Args:
        header: Byte string containing Blender file header.

    Returns:
        Blender version as 'major.minor' string.

    Raises:
        BlendHeaderError: If the header is invalid or version cannot be found.
    """
    if BLENDER_MAGIC not in header:
        raise BlendHeaderError("Missing BLENDER magic")

    match = re.search(rb"v(\d{3,4})", header)
    if not match:
        raise BlendHeaderError("Version tag not found in header")

    raw = match.group(1).decode("ascii")

    # v0500 → 5.00
    # v360  → 3.60
    if len(raw) == 3:  # noqa: PLR2004
        major = raw[0]
        minor = raw[1:]
    else:
        major = raw[:-2].lstrip("0") or "0"
        minor = raw[-2:]

    return f"{int(major)}.{minor}"


def detect_blender_version(path: str) -> Dict[str, str]:  # noqa: UP006
    """Detect Blender version and compression type from .blend file.

    Args:
        path: Path to .blend file.

    Returns:
        Dict with 'version' (str) and 'compression' (str) keys.

    Raises:
        BlendHeaderError: If the file cannot be read or is invalid.
    """
    with open(path, "rb") as f:
        magic = _read_bytes(f, 4)
        f.seek(0)

        # ------------------------
        # GZIP
        # ------------------------
        if magic.startswith(GZIP_MAGIC):
            with gzip.GzipFile(fileobj=f) as gz:
                header = _read_bytes(gz, MAX_HEADER_SCAN)
            return {
                "version": _extract_version(header),
                "compression": "gzip",
            }

        # ------------------------
        # ZSTANDARD
        # ------------------------
        if magic == ZSTD_MAGIC:
            if not HAS_ZSTD:
                raise BlendHeaderError("Zstandard compression detected but 'zstandard' module not installed")
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(f) as reader:
                header = _read_bytes(reader, MAX_HEADER_SCAN)
            return {
                "version": _extract_version(header),
                "compression": "zstd",
            }

        # ------------------------
        # UNCOMPRESSED
        # ------------------------
        header = _read_bytes(f, MAX_HEADER_SCAN)
        return {
            "version": _extract_version(header),
            "compression": "none",
        }
