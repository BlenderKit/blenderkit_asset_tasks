"""Unit tests for blender_bg_scripts.unpack_asset_bg.

We mock the bpy API and test the pure helper functions and high-level flow calls.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path, install_bpy_mock

# Ensure path and install bpy mock before importing module under test
ROOT = ensure_src_on_path()
bpy = install_bpy_mock()
try:
    from blender_bg_scripts import unpack_asset_bg as u_mod  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import importlib.util as _importlib_util
    import sys as _sys

    _mod_path = os.path.join(ROOT, "blender_bg_scripts", "unpack_asset_bg.py")
    _spec = _importlib_util.spec_from_file_location("unpack_asset_bg", _mod_path)
    assert _spec and _spec.loader
    u_mod = _importlib_util.module_from_spec(_spec)  # type: ignore
    _sys.modules["unpack_asset_bg"] = u_mod
    _spec.loader.exec_module(u_mod)  # type: ignore


class UnpackAssetBgTests(unittest.TestCase):
    # Bind module-level handles for use in tests
    bpy = bpy  # type: ignore
    U = u_mod  # type: ignore

    @classmethod
    def setUpClass(cls) -> None:
        # Nothing needed, attributes are set at class definition time
        pass

    def test_ensure_tex_dir_creates_directory(self):
        asset_data = {"id": "x"}
        with (
            mock.patch.object(self.U.paths, "get_texture_directory", return_value=os.path.join("//", "tex")),
            mock.patch.object(self.bpy.path, "abspath", side_effect=lambda _p: os.path.join("C:", "work", "tex")),
            mock.patch("os.path.exists", return_value=False),
            mock.patch("os.makedirs") as m_mk,
        ):
            out = self.U._ensure_tex_dir(asset_data, "blend")
        self.assertTrue(out.endswith(os.path.join("//", "tex")))
        m_mk.assert_called_once()

    def test_unpack_images_to_unpacks_and_repaths(self):
        # Set up images with packed files
        image = mock.Mock()
        image.name = "img"
        image.packed_files = [mock.Mock()]
        self.bpy.data.images = [image]

        with (
            mock.patch.object(self.U.paths, "get_texture_filepath", return_value="//tex/img.png"),
        ):
            self.U._unpack_images_to("//tex", "blend")

        image.unpack.assert_called()
        self.assertEqual(image.filepath, "//tex/img.png")

    def test_mark_assets_model_visible_objects(self):
        # NOTE: passing parent=None to Mock constructor does not create an attribute
        # `.parent` that is None for truth testing; instead it's used internally by
        # the mocking machinery. We explicitly assign after creation so the code
        # under test (`_mark_model_assets`) sees `ob.parent is None` and treats it
        # as a top-level object.
        ob = mock.Mock()
        ob.parent = None
        self.bpy.context.visible_objects = [ob]
        self.bpy.data.objects = [ob]
        self.U._mark_model_assets()
        ob.asset_mark.assert_called_once()

    def test_set_asset_tags_safely_handles_none(self):
        # Should early return quietly
        self.U._set_asset_tags(None, {"tags": ["a"]})

    def test_unload_saves_and_removes_backup(self):
        data = {"asset_data": {"assetType": "material", "resolution": "blend"}}
        self.bpy.data.filepath = os.path.join("C:", "file.blend")
        with (
            mock.patch.object(self.U, "_ensure_tex_dir", return_value="//tex"),
            mock.patch.object(self.U, "_unpack_images_to"),
            mock.patch.object(self.U, "_mark_assets"),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.remove") as m_rm,
        ):
            self.U.unpack_asset(data)
        self.U.bpy.ops.wm.save_as_mainfile.assert_called()
        m_rm.assert_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
