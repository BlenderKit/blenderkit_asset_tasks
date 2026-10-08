"""Unpack and mark runs must end within UNPACK_JOB_TIMEOUT_SECONDS, and a failed mark is not uploaded.

On 7 Oct 2026 two Blender 3.5 runs of unpack_asset_bg.py ('Rusty Metal', 'Mossy Stone
Wall') logged "Failed to save marked blend file" and never exited: with no time limit,
each held its GitHub job until the six-hour cap and the other assets of the batch never ran.
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

from blenderkit_server_utils import config, send_to_bg, upload  # noqa: E402


def _import(module: str) -> Any:
    """Import a job module, which validates env and parses argv at import time."""
    with (
        mock.patch.dict(os.environ, {"BLENDERKIT_API_KEY": "test-key"}),
        mock.patch.object(config, "BLENDER_PATH", "/opt/blender/blender"),
        mock.patch.object(sys, "argv", [f"{module}.py"]),
    ):
        return importlib.import_module(module)


process_asset = _import("process_asset")
generate_gltf = _import("generate_gltf")
generate_resolutions = _import("generate_resolutions")

MODEL = {"id": "asset-id", "assetBaseId": "base-id", "assetType": "model", "dictParameters": {}}
TIMED_OUT = send_to_bg.BlenderRun(send_to_bg.TIMEOUT_RETURNCODE, "timed out after 900 s")


def _unpack_calls(send: mock.MagicMock) -> list[dict[str, Any]]:
    return [c.kwargs for c in send.call_args_list if c.kwargs.get("script") == "unpack_asset_bg.py"]


class MarkRunTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [mock.patch.object(process_asset, "SKIP_UPDATE", new=False)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        reupload = mock.patch.object(upload, "reupload_main_blend", return_value=True)
        self.reupload = reupload.start()
        self.addCleanup(reupload.stop)

    def test_the_mark_run_has_a_time_limit(self) -> None:
        with mock.patch.object(send_to_bg, "send_to_bg", return_value=send_to_bg.BlenderRun(0)) as send:
            marked = process_asset._mark_and_reupload(MODEL, "test-key", "/opt/blender/blender", "asset.blend")

        self.assertTrue(marked)
        self.assertEqual([c["timeout_seconds"] for c in _unpack_calls(send)], [config.UNPACK_JOB_TIMEOUT_SECONDS])

    def test_a_failed_mark_run_is_not_uploaded(self) -> None:
        with mock.patch.object(send_to_bg, "send_to_bg", return_value=TIMED_OUT):
            marked = process_asset._mark_and_reupload(MODEL, "test-key", "/opt/blender/blender", "asset.blend")

        self.assertFalse(marked)
        self.reupload.assert_not_called()


class JobUnpackTests(unittest.TestCase):
    def test_the_gltf_unpack_has_a_time_limit(self) -> None:
        with (
            mock.patch.object(generate_gltf, "SKIP_UPDATE", new=True),
            mock.patch.object(send_to_bg, "send_to_bg", return_value=send_to_bg.BlenderRun(0)) as send,
        ):
            generate_gltf.generate_gltf(MODEL, "test-key", "/opt/blender/blender", ("gltf",), "asset.blend")

        self.assertEqual([c["timeout_seconds"] for c in _unpack_calls(send)], [config.UNPACK_JOB_TIMEOUT_SECONDS])

    def test_the_resolutions_unpack_has_a_time_limit(self) -> None:
        with mock.patch.object(send_to_bg, "send_to_bg", return_value=send_to_bg.BlenderRun(0)) as send:
            generate_resolutions._maybe_unpack_asset(MODEL, "asset.blend", blender_binary_path="/opt/blender/blender")

        self.assertEqual([c["timeout_seconds"] for c in _unpack_calls(send)], [config.UNPACK_JOB_TIMEOUT_SECONDS])


if __name__ == "__main__":
    unittest.main()
