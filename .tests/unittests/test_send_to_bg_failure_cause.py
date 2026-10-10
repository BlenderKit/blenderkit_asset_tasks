"""Tests for how send_to_bg reports why a background Blender run failed.

Blender exits 0 when a --python script raises unless --python-exit-code is set,
so on 2026-10-01, 4,602 gltfGeneratedError values said only that the result
file was missing, with the exception that caused it lost in the job log.
"""

from __future__ import annotations

import sys
import unittest
from collections import deque

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import send_to_bg  # noqa: E402

# What Blender writes to stderr when a --python script raises: the script's own
# log lines, the traceback, then Blender's exit notice (lines arrive stripped).
BLENDER_STDERR_TAIL = (
    "2026-09-29 04:31:00.123 | ERROR | bake_all_procedural_textures | 2290 | Bake failed for Bonsai",
    "Traceback (most recent call last):",
    'File "/app/blender_bg_scripts/gltf_bg_blender.py", line 2623, in <module>',
    "generate_gltf(json_result_path, target_format)",
    'File "/opt/blender/5.0/scripts/modules/bpy/ops.py", line 109, in __call__',
    "ret = _op_call(self.idname_py(), kw, C_exec, C_undo)",
    "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^",
    "RuntimeError: Error: No active UV map found on Bonsai_Leaves",
    "",
    "Error: script failed, file: '/app/blender_bg_scripts/gltf_bg_blender.py', exiting.",
)


class BuildCommandTests(unittest.TestCase):
    def test_a_raising_script_makes_blender_exit_non_zero(self) -> None:
        command = send_to_bg._build_command("blender", "asset.blend", "script.py", "data.json", addons="")

        python_at = command.index("--python")
        self.assertEqual(
            command[python_at - 2 : python_at],
            ["--python-exit-code", str(send_to_bg.SCRIPT_EXCEPTION_RETURNCODE)],
        )


class ExceptionLineTests(unittest.TestCase):
    def test_the_final_exception_line_of_a_blender_traceback_is_found(self) -> None:
        cause = send_to_bg.exception_line(deque(BLENDER_STDERR_TAIL))

        self.assertEqual(cause, "RuntimeError: Error: No active UV map found on Bonsai_Leaves")

    def test_a_qualified_exception_class_is_recognised(self) -> None:
        cause = send_to_bg.exception_line(deque(["json.decoder.JSONDecodeError: Expecting value: line 1 column 1"]))

        self.assertEqual(cause, "json.decoder.JSONDecodeError: Expecting value: line 1 column 1")

    def test_output_without_a_traceback_has_no_cause(self) -> None:
        cause = send_to_bg.exception_line(deque(["Error: Not freed memory blocks: 2, total unfreed memory 0.0004 MB"]))

        self.assertEqual(cause, "")


class RunBlenderFailureCauseTests(unittest.TestCase):
    def test_a_raising_process_reports_its_exception(self) -> None:
        command = [sys.executable, "-c", "raise RuntimeError('Error: Cannot bake')"]

        run = send_to_bg._run_blender(command, verbosity_level=0)

        self.assertEqual(run.failure, "bg_returncode=1: RuntimeError: Error: Cannot bake")

    def test_a_successful_run_has_no_failure(self) -> None:
        command = [sys.executable, "-c", "import sys; print('Traceback (most recent call last):', file=sys.stderr)"]

        run = send_to_bg._run_blender(command, verbosity_level=0)

        self.assertEqual(run.failure, "")

    def test_a_failure_without_a_traceback_reports_its_exit_code(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.exit(101)"]

        run = send_to_bg._run_blender(command, verbosity_level=0)

        self.assertEqual(run.failure, "bg_returncode=101")


# When Blender itself refuses a file, the reason is a report line on stdout, not a
# traceback: 11 materials recorded only "bg_returncode=70" in October 2026.
UNREADABLE_BLEND_5X = (
    "00:00.153  reports          | ERROR Failed to read blend file '/tmp/a/x.blend': Missing DNA block"
)
UNREADABLE_BLEND_3X = "Error: Failed to read blend file '/tmp/a/x.blend': Missing DNA block"
NOISE = (
    "Error: script failed, file: '/app/blender_bg_scripts/resolutions_bg_blender.py', exiting.",
    "Error: Not freed memory blocks: 2, total unfreed memory 0.0004 MB",
    "Error: Python: Traceback (most recent call last):",
)


class BlenderErrorLineTests(unittest.TestCase):
    def test_a_blender_5_report_is_found(self) -> None:
        cause = send_to_bg.blender_error_line(deque([UNREADABLE_BLEND_5X, *NOISE]))

        self.assertEqual(cause, "Failed to read blend file '/tmp/a/x.blend': Missing DNA block")

    def test_a_blender_3_report_is_found(self) -> None:
        cause = send_to_bg.blender_error_line(deque([UNREADABLE_BLEND_3X, *NOISE]))

        self.assertEqual(cause, "Failed to read blend file '/tmp/a/x.blend': Missing DNA block")

    def test_the_last_report_wins(self) -> None:
        lines = ["Error: Image 'a.png' has no data", "Error: Unable to pack file, source path '/d/x.jpg' not found"]

        self.assertEqual(
            send_to_bg.blender_error_line(deque(lines)),
            "Unable to pack file, source path '/d/x.jpg' not found",
        )

    def test_blender_notices_are_not_a_cause(self) -> None:
        self.assertEqual(send_to_bg.blender_error_line(deque(NOISE)), "")


class RunBlenderReportedErrorTests(unittest.TestCase):
    def test_a_blender_report_on_stdout_is_the_cause_without_a_traceback(self) -> None:
        script = f"import sys; print({UNREADABLE_BLEND_5X!r}); sys.exit(1)"

        run = send_to_bg._run_blender([sys.executable, "-c", script], verbosity_level=0)

        self.assertEqual(run.failure, "bg_returncode=1: Failed to read blend file '/tmp/a/x.blend': Missing DNA block")

    def test_a_traceback_still_wins_over_a_report(self) -> None:
        script = f"print({UNREADABLE_BLEND_3X!r}); raise RuntimeError('Error: Cannot bake')"

        run = send_to_bg._run_blender([sys.executable, "-c", script], verbosity_level=0)

        self.assertEqual(run.failure, "bg_returncode=1: RuntimeError: Error: Cannot bake")


if __name__ == "__main__":
    unittest.main()
