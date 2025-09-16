"""Unit tests for blenderkit_server_utils.paths."""

from __future__ import annotations

import os
import unittest

from helpers.testutils import ensure_src_on_path

# Ensure repo src is on sys.path before importing target module
ensure_src_on_path()
from blenderkit_server_utils import paths  # type: ignore  # noqa: E402


class PathsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Module already imported at top-level
        pass

    def test_slugify_basic(self) -> None:
        self.assertEqual(paths.slugify("Hello World"), "hello_world")

    def test_extract_filename_from_url(self) -> None:
        self.assertEqual(paths.extract_filename_from_url("http://x/y/z.png?sig=1"), "z.png")
        self.assertEqual(paths.extract_filename_from_url(None), "")

    def test_round_to_closest_resolution(self) -> None:
        self.assertEqual(paths.round_to_closest_resolution(2100) in paths.resolutions, True)

    def test_get_texture_directory_suffix(self) -> None:
        self.assertTrue(paths.get_texture_directory({}, "resolution_2K").endswith(f"{os.sep}"))

    def test_get_res_file_prefers_exact(self) -> None:
        asset = {
            "name": "Foo",
            "files": [
                {"fileType": "blend", "fileName": "foo.blend"},
                {"fileType": "resolution_1K", "fileName": "foo_1k.zip"},
            ],
        }
        f, key = paths.get_res_file(asset, "resolution_1K")
        self.assertEqual(key, "resolution_1K")
        self.assertEqual(f["fileName"], "foo_1k.zip")

    def test_server_2_local_filename(self) -> None:
        local = paths.server_2_local_filename({"name": "X Y"}, "blend_resolution_1K_img.png")
        self.assertIn("x_y", local)

    def test_get_download_filepaths_fallback(self) -> None:
        asset = {"id": "123", "assetType": "model", "files": []}
        out = paths.get_download_filepaths(asset)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
