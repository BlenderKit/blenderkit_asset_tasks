"""A bulk process run stops starting assets once its time budget is used up.

Every nightly PROCESS from 9 to 19 Oct 2026 ran into GitHub's six-hour job cap and was
cancelled with 18 to 206 of its 500 assets done; the asset in progress lost its work.
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

from blenderkit_server_utils import config  # noqa: E402


def _import(module: str) -> Any:
    with (
        mock.patch.dict(os.environ, {"BLENDERKIT_API_KEY": "test-key"}),
        mock.patch.object(config, "BLENDER_PATH", "/opt/blender/blender"),
        mock.patch.object(sys, "argv", [f"{module}.py"]),
    ):
        return importlib.import_module(module)


process_asset = _import("process_asset")

ASSETS = [{"id": f"asset-{i}"} for i in range(4)]


class TimeBudgetTests(unittest.TestCase):
    def _run(self, budget: int, clock: list[float]) -> list[str]:
        with (
            mock.patch.object(config, "PROCESS_TIME_BUDGET_SECONDS", budget),
            mock.patch.object(process_asset.time, "monotonic", side_effect=clock),
            mock.patch.object(process_asset, "process_asset") as process,
        ):
            process_asset.iterate_assets(ASSETS, api_key="test-key", binary_path="/opt/blender/blender")
        return [c.args[0]["id"] for c in process.call_args_list]

    def test_no_asset_starts_after_the_budget(self) -> None:
        started = self._run(budget=100, clock=[0, 10, 99, 101, 500])

        self.assertEqual(started, ["asset-0", "asset-1"])

    def test_without_a_budget_every_asset_runs(self) -> None:
        self.assertEqual(self._run(budget=0, clock=[0]), [a["id"] for a in ASSETS])


if __name__ == "__main__":
    unittest.main()
