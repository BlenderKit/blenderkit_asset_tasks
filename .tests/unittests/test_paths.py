"""Unit tests for blenderkit_server_utils.paths."""

from __future__ import annotations

import os
import tempfile
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
        # The server stem already contains the asset name -> no duplication,
        # '+' is sanitized, UUID hyphens are preserved, .blend extension kept.
        local = paths.server_2_local_filename(
            {"name": "bentley batur+interior"},
            "blend_bentley_batur+interior_4272cece-7ff1-4904-8416-057cd6e7892e.blend",
        )
        self.assertNotIn("+", local)
        self.assertTrue(local.endswith(".blend"))
        self.assertIn("bentley_batur_interior", local)
        self.assertIn("4272cece-7ff1-4904-8416-057cd6e7892e", local)
        # No doubled prefix.
        self.assertEqual(local.count("bentley_batur_interior"), 1)

    def test_server_2_local_filename_prepends_when_missing(self) -> None:
        # When the server stem does NOT contain the asset name, the slug is
        # still prepended so the local file is identifiable.
        local = paths.server_2_local_filename(
            {"name": "My Asset"},
            "blend_resolution_2K_abc.png",
        )
        self.assertTrue(local.startswith("my_asset_"))
        self.assertTrue(local.endswith(".png"))

    def test_slugify_strips_plus(self) -> None:
        # Regression: legacy slugify left '+' untouched. The fixed version
        # sanitizes it to '_'.
        self.assertNotIn("+", paths.slugify("bentley batur+interior"))

    def test_get_download_filepaths_fallback(self) -> None:
        asset = {"id": "123", "assetType": "model", "files": []}
        out = paths.get_download_filepaths(asset)
        self.assertEqual(len(out), 1)

    def test_safe_folder_name_ascii_matches_slugify(self) -> None:
        # Backward compat: simple ASCII names produce the same slug as the
        # legacy slugify() so existing local caches stay valid.
        self.assertEqual(paths.safe_folder_name("Hello World"), "hello_world")
        self.assertEqual(paths.safe_folder_name("MyAssetNameV2"), "myassetnamev2")

    def test_safe_folder_name_handles_non_ascii(self) -> None:
        # Accented and non-Latin scripts get transliterated to safe ASCII.
        self.assertEqual(paths.safe_folder_name("Müller"), "muller")
        self.assertEqual(paths.safe_folder_name("café"), "cafe")
        # Cyrillic should not collapse to empty.
        self.assertNotEqual(paths.safe_folder_name("Москва"), "")

    def test_safe_folder_name_truncates(self) -> None:
        out = paths.safe_folder_name("a" * 100, max_length=16)
        self.assertEqual(len(out), 16)

    def test_safe_asset_folder_name_appends_id(self) -> None:
        folder = paths.safe_asset_folder_name({"name": "Müller's Café", "id": "abc-123"})
        self.assertTrue(folder.endswith("_abc-123"))
        # Must be ASCII-only and contain no path separators or whitespace.
        self.assertEqual(folder.encode("ascii", "ignore").decode("ascii"), folder)
        for bad in (" ", "/", "\\", "'"):
            self.assertNotIn(bad, folder)

    def test_verify_path_creatable_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "new_subdir")
            self.assertTrue(paths.verify_path_creatable(target))
            self.assertTrue(os.path.isdir(target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
