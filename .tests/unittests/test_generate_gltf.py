"""Tests for the asset parameters generate_gltf leaves behind.

On 2026-10-01, 132 models carried a gltfGeneratedError older than their
gltfGeneratedDate (147 for Godot): a later success never cleared the earlier
failure, and the failure itself rarely said what went wrong.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from typing import Any
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import config, send_to_bg, upload  # noqa: E402

CAUSE = "RuntimeError: Error: No active UV map found on Bonsai_Leaves"


def _import_generate_gltf() -> Any:
    """Import generate_gltf, which validates env and parses argv at import time."""
    with (
        mock.patch.dict(os.environ, {"BLENDERKIT_API_KEY": "test-key"}),
        mock.patch.object(config, "BLENDER_PATH", "/opt/blender/blender"),
        mock.patch.object(sys, "argv", ["generate_gltf.py", "--target_format", "gltf"]),
    ):
        return importlib.import_module("generate_gltf")


generate_gltf = _import_generate_gltf()


def _asset(**dict_parameters: str) -> dict[str, Any]:
    return {"id": "asset-id", "assetBaseId": "base-id", "dictParameters": dict_parameters}


def _export_succeeds(*_args: Any, **kwargs: Any) -> send_to_bg.BlenderRun:
    if kwargs.get("script") == "gltf_bg_blender.py":
        with open(kwargs["result_path"], "w", encoding="utf-8") as f:
            json.dump([{"type": "gltf", "index": 0, "file_path": "asset.glb"}], f)
    return send_to_bg.BlenderRun(0)


def _export_raises(*_args: Any, **kwargs: Any) -> send_to_bg.BlenderRun:
    if kwargs.get("script") == "gltf_bg_blender.py":
        return send_to_bg.BlenderRun(send_to_bg.SCRIPT_EXCEPTION_RETURNCODE, CAUSE)
    return send_to_bg.BlenderRun(0)


class GenerateGltfParameterTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            mock.patch.object(generate_gltf, "SKIP_UPDATE", new=False),
            mock.patch.object(upload, "upload_resolutions"),
            mock.patch.object(upload, "get_individual_parameter"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.patch_parameter = self._patch(upload, "patch_individual_parameter")
        self.delete_parameter = self._patch(upload, "delete_individual_parameter", return_value=True)

    def _patch(self, target: Any, name: str, **kwargs: Any) -> mock.MagicMock:
        patch = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patch.stop)
        return patch.start()

    def _generate(self, asset: dict[str, Any], bg_run: Any) -> bool:
        with mock.patch.object(send_to_bg, "send_to_bg", side_effect=bg_run):
            return generate_gltf.generate_gltf(asset, "test-key", "/opt/blender/blender", "gltf", "asset.blend")

    def test_a_failed_export_records_the_exception_that_caused_it(self) -> None:
        succeeded = self._generate(_asset(), _export_raises)

        self.assertFalse(succeeded)
        written = self.patch_parameter.call_args.kwargs
        self.assertEqual(written["param_name"], "gltfGeneratedError")
        self.assertTrue(
            written["param_value"].startswith(f"bg_returncode={send_to_bg.SCRIPT_EXCEPTION_RETURNCODE}: {CAUSE}"),
            written["param_value"],
        )

    def test_a_success_clears_the_error_of_an_earlier_failure(self) -> None:
        succeeded = self._generate(_asset(gltfGeneratedError="bg_returncode=1: MemoryError"), _export_succeeds)

        self.assertTrue(succeeded)
        self.delete_parameter.assert_called_once_with(
            asset_id="asset-id",
            param_name="gltfGeneratedError",
            api_key="test-key",
        )

    def test_a_success_leaves_the_other_format_error_alone(self) -> None:
        succeeded = self._generate(_asset(gltfGodotGeneratedError="bg_returncode=1: MemoryError"), _export_succeeds)

        self.assertTrue(succeeded)
        self.delete_parameter.assert_not_called()

    def test_a_refused_delete_fails_the_job_loudly(self) -> None:
        self.delete_parameter.return_value = False

        with self.assertRaisesRegex(RuntimeError, "gltfGeneratedError"):
            self._generate(_asset(gltfGeneratedError="bg_returncode=1: MemoryError"), _export_succeeds)


if __name__ == "__main__":
    unittest.main()
