"""get_texture_filepath must keep an unpacked texture inside the texture folder.

A texture packed on Windows keeps its original path, e.g. ..\\..\\Users\\x\\tex.jpg.
bpy.path.basename splits on "/" only on the Linux builder, so the whole path was
appended to //textures/ and the unpack climbed out of it: every image of such an
asset failed to unpack, and the resolution script then found no pixels at all.
"""

from __future__ import annotations

import os
import types
import unittest
from typing import Any
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import paths  # noqa: E402


def _blender_basename(path: str) -> str:
    """Mirror bpy.path.basename on Linux: drop Blender's '//' prefix, split on '/'."""
    return os.path.basename(path[2:] if path.startswith("//") else path)


def _packed_image(packed_path: str) -> Any:
    return types.SimpleNamespace(
        name="texture",
        filepath=packed_path,
        packed_files=[types.SimpleNamespace(filepath=packed_path)],
    )


class TextureFilepathTests(unittest.TestCase):
    def _texture_filepath(self, image: Any) -> str:
        fake_bpy = types.SimpleNamespace(
            path=types.SimpleNamespace(basename=_blender_basename),
            data=types.SimpleNamespace(images=[image]),
        )
        with mock.patch.object(paths, "bpy", fake_bpy):
            return paths.get_texture_filepath("//textures/", image)

    def test_a_path_saved_on_windows_keeps_only_the_file_name(self) -> None:
        image = _packed_image("..\\..\\Users\\hawma\\Desktop\\texture\\wood_0031_ao_2k.jpg")

        self.assertEqual(self._texture_filepath(image), "//textures/wood_0031_ao_2k.jpg")

    def test_a_blender_relative_path_keeps_only_the_file_name(self) -> None:
        self.assertEqual(self._texture_filepath(_packed_image("//old_textures/wood.png")), "//textures/wood.png")


if __name__ == "__main__":
    unittest.main()
