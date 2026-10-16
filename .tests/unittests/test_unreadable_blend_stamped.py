"""A .blend Blender cannot open is stamped as processed, so it is neither retried nor left unprocessed.

'Flame folded Napkin' (2.79) fails to open with "A critical error happened (the blend
file is likely corrupted)". Marking failed, so processingDate was never stamped: every
sweep retried it, and the server listed it as waiting for the builder instead of as a
.blend only its creator can fix (October 2026).
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from typing import Any
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import config, download, send_to_bg  # noqa: E402


def _import(module: str) -> Any:
    with (
        mock.patch.dict(os.environ, {"BLENDERKIT_API_KEY": "test-key"}),
        mock.patch.object(config, "BLENDER_PATH", "/opt/blender/blender"),
        mock.patch.object(sys, "argv", [f"{module}.py"]),
    ):
        return importlib.import_module(module)


process_asset = _import("process_asset")

MODEL = {"id": "asset-id", "assetBaseId": "base-id", "assetType": "model", "name": "Napkin", "files": [{}]}
CORRUPTED = send_to_bg.BlenderRun(
    -11,
    "A critical error happened (the blend file is likely corrupted): "
    "No Screen data-block should ever have a NULL `scene` pointer",
)
UNREADABLE = send_to_bg.BlenderRun(1, "Failed to read blend file '/tmp/x.blend': Missing DNA block")
CRASHED = send_to_bg.BlenderRun(-11)


class UnreadableBlendTests(unittest.TestCase):
    def test_only_blenders_refusal_to_open_the_file_counts(self) -> None:
        self.assertTrue(CORRUPTED.unreadable_blend)
        self.assertTrue(UNREADABLE.unreadable_blend)
        self.assertFalse(CRASHED.unreadable_blend)
        self.assertFalse(send_to_bg.BlenderRun(0, "Failed to read blend file").unreadable_blend)

    def test_marking_an_unreadable_file_reports_it_as_such(self) -> None:
        with mock.patch.object(send_to_bg, "send_to_bg", return_value=CORRUPTED):
            marked = process_asset._mark_and_reupload(MODEL, "test-key", "/opt/blender/blender", "asset.blend")

        self.assertIs(marked, process_asset.Mark.UNREADABLE)

    def _process(self, mark: Any) -> mock.MagicMock:
        with (
            mock.patch.object(download, "download_asset", return_value="asset.blend"),
            mock.patch.object(process_asset, "_blender_version_label", return_value="2.93"),
            mock.patch.object(process_asset, "_mark_and_reupload", return_value=mark),
            mock.patch.object(process_asset, "_run_generation_jobs", return_value=True),
            mock.patch.object(process_asset, "_patch_processing_date") as stamp,
        ):
            process_asset.process_asset(MODEL, "test-key", "/opt/blender/blender")
        return stamp

    def test_an_unreadable_file_is_stamped_so_the_sweep_moves_on(self) -> None:
        self.assertEqual(self._process(process_asset.Mark.UNREADABLE).call_count, 1)

    def test_a_failed_mark_is_left_unstamped_for_a_retry(self) -> None:
        self._process(process_asset.Mark.FAILED).assert_not_called()

    def test_a_marked_file_is_stamped(self) -> None:
        self.assertEqual(self._process(process_asset.Mark.OK).call_count, 1)


if __name__ == "__main__":
    unittest.main()
