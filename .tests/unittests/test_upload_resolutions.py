"""Tests for how upload_resolutions reports a failed upload.

Its callers count an upload as done unless it raises, so a failed upload set
gltfGeneratedDate on an asset that never got the GLTF file.
"""

from __future__ import annotations

import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import upload  # noqa: E402

ASSET = {"name": "Bonsai", "displayName": "Japanese Bonsai Tree", "id": "asset-id"}
FILES = [{"type": "gltf", "index": 0, "file_path": "asset.glb"}]


class UploadResolutionsTests(unittest.TestCase):
    def test_a_failed_file_upload_raises(self) -> None:
        with (
            mock.patch.object(upload, "upload_files", return_value=False),
            self.assertRaisesRegex(RuntimeError, "asset-id"),
        ):
            upload.upload_resolutions(FILES, ASSET, api_key="test-key")

    def test_a_complete_upload_returns_normally(self) -> None:
        with mock.patch.object(upload, "upload_files", return_value=True) as upload_files:
            upload.upload_resolutions(FILES, ASSET, api_key="test-key")

        upload_files.assert_called_once()


if __name__ == "__main__":
    unittest.main()
