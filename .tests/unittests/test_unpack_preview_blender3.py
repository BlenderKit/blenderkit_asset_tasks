"""Blender 3.x must not try to render an asset preview in background mode.

On 7 Oct 2026 every unpack run on Blender 3.0-3.6 (the thumbnail download fails
there) fell back to bpy.ops.ed.lib_id_generate_preview, logged success, then
aborted with "Unable to open a display" (exit -6) before the marked .blend was
saved: marking failed, and the resolution job measured the still-packed textures
as 0 bytes and recorded "no-size-gain".
"""

from __future__ import annotations

import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path, install_bpy_mock

ensure_src_on_path()
install_bpy_mock()

from blender_bg_scripts import unpack_asset_bg  # noqa: E402

# The bpy the module imported; another test module may have installed it first.
bpy = unpack_asset_bg.bpy


class GeneratedPreviewTests(unittest.TestCase):
    def _apply_without_thumbnail(self, version: tuple[int, int, int]) -> mock.Mock:
        with (
            mock.patch.object(bpy.app, "version", version),
            mock.patch.object(bpy.app, "background", new=True, create=True),
            mock.patch.object(bpy.ops, "ed", mock.Mock(), create=True),
            mock.patch.object(unpack_asset_bg, "_resolve_thumbnail_url", return_value=""),
            mock.patch.object(unpack_asset_bg, "_op_poll", return_value=True),
            mock.patch.object(unpack_asset_bg, "_op_call") as op_call,
        ):
            unpack_asset_bg._apply_asset_preview(mock.Mock(), {})
        return op_call

    def test_blender_3_does_not_render_a_preview_in_background(self) -> None:
        self.assertFalse(self._apply_without_thumbnail((3, 2, 2)).called)

    def test_blender_4_still_renders_one(self) -> None:
        self.assertTrue(self._apply_without_thumbnail((4, 2, 0)).called)


if __name__ == "__main__":
    unittest.main()
