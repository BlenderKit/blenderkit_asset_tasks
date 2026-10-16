"""PNG text metadata must go before Blender 2.93 writes the texture as JPEG.

Blender 2.93 crashed (SIGSEGV) on the JPEG size probe of every PNG carrying EXIF
or XMP text: 'Marble Polished Slab 001a', 'Wood Rough 001a' and both 'Ivy Plant'
models never got resolutions in the October 2026 re-run.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import image_utils  # noqa: E402


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


HEADER = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
PIXELS = _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
END = _chunk(b"IEND", b"")
EXIF = _chunk(b"zTXt", b"Raw profile type exif\x00\x00" + zlib.compress(b"exif" * 5000))
XMP = _chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta/>")


class StripPngTextTests(unittest.TestCase):
    def _write(self, data: bytes) -> str:
        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def _read(self, path: str) -> bytes:
        with open(path, "rb") as file:
            return file.read()

    def test_text_chunks_go_and_the_image_stays(self) -> None:
        path = self._write(image_utils.PNG_SIGNATURE + HEADER + EXIF + PIXELS + XMP + END)

        removed = image_utils.strip_png_text(path)

        self.assertEqual(removed, len(EXIF) + len(XMP))
        self.assertEqual(self._read(path), image_utils.PNG_SIGNATURE + HEADER + PIXELS + END)

    def test_a_png_without_text_is_not_rewritten(self) -> None:
        original = image_utils.PNG_SIGNATURE + HEADER + PIXELS + END
        path = self._write(original)

        self.assertEqual(image_utils.strip_png_text(path), 0)
        self.assertEqual(self._read(path), original)

    def test_other_files_are_left_alone(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"zTXt" * 10
        path = self._write(jpeg)

        self.assertEqual(image_utils.strip_png_text(path), 0)
        self.assertEqual(self._read(path), jpeg)


if __name__ == "__main__":
    unittest.main()
