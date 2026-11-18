"""A texture EXR cut short must fail the asset as unreadable before Blender reads it.

Blender 5.0 crashed (SIGSEGV) inside OpenEXR on 16K Poly Haven textures that end
before their own chunk table says they should: 'Grass Ground' and both 'Checkered
Pavement Tiles' were recorded as a bare bg_returncode=-11, not as files only a
re-upload fixes (October 2026).

Some writers leave the chunk offset table zeroed and OpenEXR rebuilds it by walking
the chunks; 'Layered Cliff Rock' (November 2026) was failed as truncated although its
roughness EXR was complete and Blender read it.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import image_utils  # noqa: E402

LINES = 64
ZIP = 3  # 16 scanlines per chunk
CHUNKS = LINES // 16


def _attribute(name: str, kind: str, value: bytes) -> bytes:
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<I", len(value)) + value


def _exr(*, version_flags: int = 0, cut: int = 0) -> bytes:
    header = (
        image_utils.EXR_MAGIC
        + struct.pack("<I", 2 | version_flags)
        + _attribute("compression", "compression", bytes([ZIP]))
        + _attribute("dataWindow", "box2i", struct.pack("<4i", 0, 0, 15, LINES - 1))
        + b"\0"
    )
    chunk = b"x" * 100
    first = len(header) + 8 * CHUNKS
    table = b"".join(struct.pack("<Q", first + i * len(chunk)) for i in range(CHUNKS))
    data = header + table + chunk * CHUNKS
    return data[: len(data) - cut] if cut else data


def _exr_with_zeroed_table(*, cut: int = 0) -> bytes:
    """An uncompressed scanline EXR whose writer left the offset table zeroed."""
    header = (
        image_utils.EXR_MAGIC
        + struct.pack("<I", 2)
        + _attribute("compression", "compression", b"\0")
        + _attribute("dataWindow", "box2i", struct.pack("<4i", 0, 0, 3, LINES - 1))
        + b"\0"
    )
    pixels = b"p" * 48
    chunks = b"".join(struct.pack("<ii", y, len(pixels)) + pixels for y in range(LINES))
    data = header + b"\0" * 8 * LINES + chunks
    return data[: len(data) - cut] if cut else data


class ExrIsTruncatedTests(unittest.TestCase):
    def _path(self, data: bytes) -> str:
        handle, path = tempfile.mkstemp(suffix=".exr")
        os.close(handle)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def test_a_complete_file_is_not_truncated(self) -> None:
        self.assertFalse(image_utils.exr_is_truncated(self._path(_exr())))

    def test_a_file_cut_before_its_last_chunk_is_truncated(self) -> None:
        self.assertTrue(image_utils.exr_is_truncated(self._path(_exr(cut=150))))

    def test_a_file_cut_inside_its_chunk_table_is_truncated(self) -> None:
        data = _exr()
        self.assertTrue(image_utils.exr_is_truncated(self._path(data[: data.index(b"x" * 100) - 12])))

    def test_a_complete_file_with_a_zeroed_offset_table_is_not_truncated(self) -> None:
        self.assertFalse(image_utils.exr_is_truncated(self._path(_exr_with_zeroed_table())))

    def test_a_cut_file_with_a_zeroed_offset_table_is_truncated(self) -> None:
        self.assertTrue(image_utils.exr_is_truncated(self._path(_exr_with_zeroed_table(cut=20))))

    def test_tiled_files_and_other_formats_are_not_checked(self) -> None:
        self.assertFalse(image_utils.exr_is_truncated(self._path(_exr(version_flags=0x200, cut=150))))
        self.assertFalse(image_utils.exr_is_truncated(self._path(b"\x89PNG\r\n\x1a\nrest")))


if __name__ == "__main__":
    unittest.main()
