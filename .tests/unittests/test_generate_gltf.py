"""Tests for the asset parameters generate_gltf leaves behind.

On 2026-10-01, 132 models carried a gltfGeneratedError older than their
gltfGeneratedDate (147 for Godot): a later success never cleared the earlier
failure, and the failure itself rarely said what went wrong.

Both formats now come from a single bake, so each still needs its own outcome:
on 2026-10-05, 1006 models had a Godot GLTF but a failed Draco-compressed one.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from collections.abc import Callable
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


BOTH = ("gltf", "gltf_godot")
WEB = {"type": "gltf", "index": 0, "file_path": "gltf/asset.glb"}
GODOT = {"type": "gltf_godot", "index": 0, "file_path": "gltf_godot/asset.glb"}
DRACO_ERROR = "export failed: OSError: libbf_intern_draco_bridge.so: cannot open shared object file"


def _asset(**dict_parameters: str) -> dict[str, Any]:
    return {"id": "asset-id", "assetBaseId": "base-id", "dictParameters": dict_parameters}


def _export(*outcomes: dict[str, Any], returncode: int = 0, cause: str = "") -> Callable[..., send_to_bg.BlenderRun]:
    """Fake send_to_bg: the GLTF export reports ``outcomes`` (if any), then exits with ``returncode``."""

    def run(*_args: Any, **kwargs: Any) -> send_to_bg.BlenderRun:
        if kwargs.get("script") != "gltf_bg_blender.py":
            return send_to_bg.BlenderRun(0)
        if outcomes:
            with open(kwargs["result_path"], "w", encoding="utf-8") as f:
                json.dump(list(outcomes), f)
        return send_to_bg.BlenderRun(returncode, cause)

    return run


class GenerateGltfParameterTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            mock.patch.object(generate_gltf, "SKIP_UPDATE", new=False),
            mock.patch.object(upload, "get_individual_parameter"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.upload = self._patch(upload, "upload_resolutions")
        self.patch_parameter = self._patch(upload, "patch_individual_parameter")
        self.delete_parameter = self._patch(upload, "delete_individual_parameter", return_value=True)
        self.send_to_bg = self._patch(send_to_bg, "send_to_bg")

    def _patch(self, target: Any, name: str, **kwargs: Any) -> mock.MagicMock:
        patch = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patch.stop)
        return patch.start()

    def _generate(
        self,
        asset: dict[str, Any],
        bg_run: Callable[..., send_to_bg.BlenderRun],
        formats: tuple[str, ...] = ("gltf",),
    ) -> bool:
        self.send_to_bg.side_effect = bg_run
        return generate_gltf.generate_gltf(asset, "test-key", "/opt/blender/blender", formats, "asset.blend")

    def _patched(self) -> dict[str, str]:
        return {c.kwargs["param_name"]: c.kwargs["param_value"] for c in self.patch_parameter.call_args_list}

    def test_one_blender_run_exports_every_format(self) -> None:
        succeeded = self._generate(_asset(), _export(WEB, GODOT), BOTH)

        self.assertTrue(succeeded)
        exports = [c.kwargs for c in self.send_to_bg.call_args_list if c.kwargs.get("script") == "gltf_bg_blender.py"]
        self.assertEqual([export["target_formats"] for export in exports], [BOTH])
        self.assertEqual([c.args[0] for c in self.upload.call_args_list], [[WEB], [GODOT]])
        self.assertEqual(set(self._patched()), {"gltfGeneratedDate", "gltfGodotGeneratedDate"})

    def test_a_failed_run_records_its_cause_for_every_format(self) -> None:
        succeeded = self._generate(
            _asset(),
            _export(returncode=send_to_bg.SCRIPT_EXCEPTION_RETURNCODE, cause=CAUSE),
            BOTH,
        )

        self.assertFalse(succeeded)
        failure = f"bg_returncode={send_to_bg.SCRIPT_EXCEPTION_RETURNCODE}: {CAUSE}"
        self.assertEqual(self._patched(), {"gltfGeneratedError": failure, "gltfGodotGeneratedError": failure})

    def test_a_format_that_failed_to_export_records_its_own_error(self) -> None:
        succeeded = self._generate(_asset(), _export({"type": "gltf", "error": DRACO_ERROR}, GODOT), BOTH)

        self.assertFalse(succeeded)
        patched = self._patched()
        self.assertEqual(set(patched), {"gltfGeneratedError", "gltfGodotGeneratedDate"})
        self.assertEqual(patched["gltfGeneratedError"], DRACO_ERROR)
        self.assertEqual([c.args[0] for c in self.upload.call_args_list], [[GODOT]])

    def test_a_crash_after_an_export_keeps_the_exported_format(self) -> None:
        succeeded = self._generate(_asset(), _export(WEB, returncode=-11), BOTH)

        self.assertFalse(succeeded)
        patched = self._patched()
        self.assertEqual(set(patched), {"gltfGeneratedDate", "gltfGodotGeneratedError"})
        self.assertEqual(patched["gltfGodotGeneratedError"], "bg_returncode=-11")

    def test_a_run_that_reported_nothing_says_so(self) -> None:
        succeeded = self._generate(_asset(), _export())

        self.assertFalse(succeeded)
        self.assertEqual(self._patched(), {"gltfGeneratedError": "no export outcome reported"})

    def test_a_failed_upload_spares_the_other_format(self) -> None:
        self.upload.side_effect = [RuntimeError("Upload of resolutions failed for asset asset-id"), None]

        succeeded = self._generate(_asset(), _export(WEB, GODOT), BOTH)

        self.assertFalse(succeeded)
        patched = self._patched()
        self.assertEqual(set(patched), {"gltfGeneratedError", "gltfGodotGeneratedDate"})
        self.assertEqual(patched["gltfGeneratedError"], "upload/patch failed")

    def test_a_success_clears_the_error_of_an_earlier_failure(self) -> None:
        succeeded = self._generate(_asset(gltfGeneratedError="bg_returncode=1: MemoryError"), _export(WEB))

        self.assertTrue(succeeded)
        self.delete_parameter.assert_called_once_with(
            asset_id="asset-id",
            param_name="gltfGeneratedError",
            api_key="test-key",
        )

    def test_a_success_leaves_the_other_format_error_alone(self) -> None:
        succeeded = self._generate(_asset(gltfGodotGeneratedError="bg_returncode=1: MemoryError"), _export(WEB))

        self.assertTrue(succeeded)
        self.delete_parameter.assert_not_called()

    def test_a_refused_delete_fails_the_job_loudly(self) -> None:
        self.delete_parameter.return_value = False

        with self.assertRaisesRegex(RuntimeError, "gltfGeneratedError"):
            self._generate(_asset(gltfGeneratedError="bg_returncode=1: MemoryError"), _export(WEB))


if __name__ == "__main__":
    unittest.main()
