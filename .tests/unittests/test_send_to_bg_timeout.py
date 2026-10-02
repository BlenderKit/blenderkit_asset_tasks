"""Tests for the time limit on Blender runs started by send_to_bg.

One asset whose GLTF export never finished held the whole six-hour nightly run
every night from 2026-09-22; a bounded run fails that asset and moves on.
"""

from __future__ import annotations

import sys
import time
import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import send_to_bg  # noqa: E402


class RunBlenderTimeoutTests(unittest.TestCase):
    def test_process_over_the_limit_is_killed_and_reported(self) -> None:
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        started = time.monotonic()

        run = send_to_bg._run_blender(command, verbosity_level=0, timeout_seconds=1)

        self.assertEqual(run.returncode, send_to_bg.TIMEOUT_RETURNCODE)
        self.assertEqual(run.failure, f"bg_returncode={send_to_bg.TIMEOUT_RETURNCODE}: timed out after 1 s")
        self.assertLess(time.monotonic() - started, 10)

    def test_process_within_the_limit_keeps_its_exit_code(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.exit(3)"]

        run = send_to_bg._run_blender(command, verbosity_level=0, timeout_seconds=30)

        self.assertEqual(run.returncode, 3)

    def test_without_a_limit_the_runner_waits_for_the_process(self) -> None:
        command = [sys.executable, "-c", "import time; time.sleep(1.5)"]

        run = send_to_bg._run_blender(command, verbosity_level=0)

        self.assertEqual(run.returncode, 0)


class SendToBgTimeoutTests(unittest.TestCase):
    def test_the_limit_reaches_the_runner(self) -> None:
        with (
            mock.patch.object(send_to_bg, "_select_binary_path", return_value="blender"),
            mock.patch.object(send_to_bg, "_run_blender", return_value=send_to_bg.BlenderRun(0)) as run,
        ):
            send_to_bg.send_to_bg({}, asset_file_path="asset.blend", script="script.py", timeout_seconds=5)

        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
