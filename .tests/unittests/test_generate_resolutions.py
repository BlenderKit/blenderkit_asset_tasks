"""Tests for what generate_resolutions records on an asset.

Assets the sweep had processed could still have no resolution files with
nothing on the asset saying why: a procedural material, textures already at
the smallest resolution, textures that did not shrink and a failed save all
looked the same.
"""

from __future__ import annotations

import importlib
import json
import os
import unittest
from typing import Any
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import send_to_bg, upload  # noqa: E402


def _import_generate_resolutions() -> Any:
    """Import generate_resolutions, which validates env at import time."""
    with mock.patch.dict(os.environ, {"BLENDERKIT_API_KEY": "test-key"}):
        return importlib.import_module("generate_resolutions")


generate_resolutions = _import_generate_resolutions()

FILES = [{"type": "resolution_1K", "index": 0, "file_path": "asset_1k.blend"}]


def _asset(**dict_parameters: str) -> dict[str, Any]:
    return {
        "id": "asset-id",
        "assetBaseId": "base-id",
        "assetType": "model",
        "files": [{"fileType": "blend"}],
        "dictParameters": dict_parameters,
    }


def _bg_writes(result: dict[str, Any] | None, run: send_to_bg.BlenderRun | None = None) -> Any:
    """Fake send_to_bg: the resolutions script writes ``result`` (if any) and ends as ``run``."""

    def fake(*_args: Any, **kwargs: Any) -> send_to_bg.BlenderRun:
        if kwargs.get("script") != "resolutions_bg_blender.py":
            return send_to_bg.BlenderRun(0)
        if result is not None:
            with open(kwargs["result_path"], "w", encoding="utf-8") as f:
                json.dump(result, f)
        return run or send_to_bg.BlenderRun(0)

    return fake


class GenerateResolutionsOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._patch(generate_resolutions, "SKIP_UPDATE", new=False)
        self.put = self._patch(upload, "patch_individual_parameter", return_value=True)
        self.delete = self._patch(upload, "delete_individual_parameter", return_value=True)
        self.upload_files = self._patch(upload, "upload_resolutions")
        self._patch(upload, "patch_asset_empty")

    def _patch(self, target: Any, name: str, **kwargs: Any) -> Any:
        patch = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patch.stop)
        return patch.start()

    def _generate(self, asset: dict[str, Any], bg: Any) -> None:
        with mock.patch.object(send_to_bg, "send_to_bg", side_effect=bg):
            generate_resolutions.generate_resolution_thread(asset, "test-key", asset_file_path="asset.blend")

    def _written(self) -> dict[str, str]:
        return {c.kwargs["param_name"]: c.kwargs["param_value"] for c in self.put.call_args_list}

    def _deleted(self) -> list[str]:
        return [c.kwargs["param_name"] for c in self.delete.call_args_list]

    def test_a_procedural_asset_is_recorded_as_not_applicable(self) -> None:
        self._generate(
            _asset(resolutionsGeneratedError="no-size-gain"),
            _bg_writes({"not_applicable": "procedural"}),
        )

        self.assertEqual(self._written(), {"resolutionsNotApplicable": "procedural"})
        self.assertEqual(self._deleted(), ["resolutionsGeneratedError"])
        self.upload_files.assert_not_called()

    def test_textures_at_the_smallest_resolution_are_recorded_as_not_applicable(self) -> None:
        self._generate(_asset(), _bg_writes({"not_applicable": "smallest-resolution"}))

        self.assertEqual(self._written(), {"resolutionsNotApplicable": "smallest-resolution"})
        self.assertEqual(self._deleted(), [])

    def test_textures_that_do_not_shrink_are_recorded_as_an_error(self) -> None:
        self._generate(
            _asset(resolutionsNotApplicable="smallest-resolution"),
            _bg_writes({"error": "no-size-gain"}),
        )

        self.assertEqual(self._written(), {"resolutionsGeneratedError": "no-size-gain"})
        self.assertEqual(self._deleted(), ["resolutionsNotApplicable"])

    def test_a_crashed_script_is_recorded_with_its_cause(self) -> None:
        run = send_to_bg.BlenderRun(send_to_bg.SCRIPT_EXCEPTION_RETURNCODE, "RuntimeError: Error: Cannot save")

        self._generate(_asset(), _bg_writes(None, run))

        self.assertEqual(self._written(), {"resolutionsGeneratedError": run.failure})

    def test_uploaded_resolutions_clear_both_outcome_parameters(self) -> None:
        self._generate(
            _asset(resolutionsGeneratedError="no-size-gain", resolutionsNotApplicable="procedural"),
            _bg_writes({"files": FILES}),
        )

        self.upload_files.assert_called_once()
        self.assertEqual(self.upload_files.call_args.args[0], FILES)
        self.assertEqual(self._written(), {})
        self.assertEqual(sorted(self._deleted()), ["resolutionsGeneratedError", "resolutionsNotApplicable"])

    def test_a_failed_upload_is_recorded_as_an_error(self) -> None:
        self.upload_files.side_effect = RuntimeError("Upload of resolutions failed for asset asset-id")

        self._generate(_asset(), _bg_writes({"files": FILES}))

        self.assertEqual(self._written(), {"resolutionsGeneratedError": "upload failed"})

    def test_skip_update_records_nothing(self) -> None:
        with mock.patch.object(generate_resolutions, "SKIP_UPDATE", new=True):
            self._generate(_asset(), _bg_writes({"not_applicable": "procedural"}))

        self.put.assert_not_called()
        self.delete.assert_not_called()

    def test_a_refused_parameter_fails_the_job_loudly(self) -> None:
        self.put.return_value = False

        with self.assertRaisesRegex(RuntimeError, "resolutionsNotApplicable"):
            self._generate(_asset(), _bg_writes({"not_applicable": "procedural"}))


if __name__ == "__main__":
    unittest.main()
