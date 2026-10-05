"""Tests for the outcome the resolution Blender scripts report.

Both scripts returned an empty list for a procedural asset, for textures
already at the smallest resolution, for textures that did not shrink and,
in the non-HDR script, for a failed save, so the caller could not tell an
asset that needs no resolutions from one whose generation broke.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from typing import Any
from unittest import mock

from helpers.testutils import ensure_src_on_path, install_bpy_mock

ensure_src_on_path()
bpy = install_bpy_mock()


def _load_bg_script(name: str) -> Any:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "blender_bg_scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


resolutions_bg = _load_bg_script("resolutions_bg_blender")
resolutions_bg_hdr = _load_bg_script("resolutions_bg_blender_hdr")

DATA = {"file_path": "asset.blend", "result_filepath": "result.json", "asset_data": {}}


def _image(width: int, height: int) -> Any:
    return types.SimpleNamespace(name="texture", size=(width, height), buffers_free=mock.Mock())


class ResolutionsOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        bpy.ops.wm.open_mainfile = mock.Mock()
        self.addCleanup(setattr, bpy.data, "images", [])

    def _generate_with_sizes(self, original: int, generated: int) -> dict[str, Any]:
        bpy.data.images = [_image(2048, 2048)]
        with (
            mock.patch.object(resolutions_bg, "_compute_original_textures_size", return_value=original),
            mock.patch.object(resolutions_bg, "_prepare_texture_dir", return_value="//textures/"),
            mock.patch.object(resolutions_bg, "_process_images_for_resolution", return_value=generated),
            mock.patch.object(resolutions_bg, "_save_resolution_blend"),
        ):
            return resolutions_bg.generate_lower_resolutions(DATA)

    def test_an_asset_without_images_is_procedural(self) -> None:
        self.assertEqual(resolutions_bg.generate_lower_resolutions(DATA), {"not_applicable": "procedural"})

    def test_textures_at_512_px_are_already_the_smallest_resolution(self) -> None:
        bpy.data.images = [_image(512, 512)]

        outcome = resolutions_bg.generate_lower_resolutions(DATA)

        self.assertEqual(outcome, {"not_applicable": "smallest-resolution"})

    def test_images_without_pixel_data_are_an_error_not_procedural(self) -> None:
        bpy.data.images = [_image(0, 0)]

        with self.assertRaisesRegex(RuntimeError, "no pixel data"):
            resolutions_bg.generate_lower_resolutions(DATA)

    def test_textures_that_never_shrink_are_an_error(self) -> None:
        self.assertEqual(self._generate_with_sizes(original=1000, generated=1000), {"error": "no-size-gain"})

    def test_smaller_textures_are_reported_as_files(self) -> None:
        outcome = self._generate_with_sizes(original=1000, generated=400)

        self.assertEqual(
            [f["type"] for f in outcome["files"]],
            ["resolution_2K", "resolution_1K", "resolution_0_5K"],
        )

    def test_a_failed_save_is_raised_instead_of_reported_as_no_files(self) -> None:
        bpy.data.images = [_image(2048, 2048)]
        with (
            mock.patch.object(resolutions_bg, "_compute_original_textures_size", return_value=1000),
            mock.patch.object(resolutions_bg, "_prepare_texture_dir", return_value="//textures/"),
            mock.patch.object(resolutions_bg, "_process_images_for_resolution", return_value=400),
            mock.patch.object(
                bpy.ops.wm,
                "save_as_mainfile",
                side_effect=RuntimeError("Error: Cannot open file for writing"),
            ),
            self.assertRaisesRegex(RuntimeError, "Cannot open file for writing"),
        ):
            resolutions_bg.generate_lower_resolutions(DATA)

    def test_a_texture_directory_that_cannot_be_created_is_raised(self) -> None:
        bpy.data.images = [_image(2048, 2048)]
        with (
            mock.patch.object(resolutions_bg, "_compute_original_textures_size", return_value=1000),
            mock.patch.object(os, "makedirs", side_effect=PermissionError("Permission denied")),
            mock.patch.object(os.path, "exists", return_value=False),
            self.assertRaises(PermissionError),
        ):
            resolutions_bg.generate_lower_resolutions(DATA)


class HdrResolutionsOutcomeTests(unittest.TestCase):
    def test_an_hdr_that_cannot_be_loaded_is_raised(self) -> None:
        load = mock.Mock(side_effect=RuntimeError("Error: Cannot read 'sky.hdr'"))
        with (
            mock.patch.object(bpy.data, "images", types.SimpleNamespace(load=load)),
            self.assertRaisesRegex(RuntimeError, "sky.hdr"),
        ):
            resolutions_bg_hdr.generate_lower_resolutions({**DATA, "file_path": "sky.hdr"})

    def test_an_hdr_whose_variants_never_shrink_is_an_error(self) -> None:
        hdr = types.SimpleNamespace(size=(1024, 1024))
        with (
            mock.patch.object(bpy.data, "images", types.SimpleNamespace(load=mock.Mock(return_value=hdr))),
            mock.patch.object(resolutions_bg_hdr.image_utils, "img_save_as"),
            mock.patch.object(resolutions_bg_hdr.image_utils, "downscale"),
            mock.patch.object(os.path, "getsize", return_value=1000),
            mock.patch.object(os.path, "exists", return_value=True),
        ):
            outcome = resolutions_bg_hdr.generate_lower_resolutions({**DATA, "file_path": "sky.hdr"})

        self.assertEqual(outcome, {"error": "no-size-gain"})


if __name__ == "__main__":
    unittest.main()
