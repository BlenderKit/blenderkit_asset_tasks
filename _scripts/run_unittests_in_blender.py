"""Run repository unittests inside Blender's Python runtime.

This script is intended to be executed by Blender in background mode, e.g.:

Windows (PowerShell):
    blender.exe -b -noaudio -P blenderkit_asset_tasks/_scripts/run_unittests_in_blender.py -- \
        -s blenderkit_asset_tasks/_test/unittests -p "test_*.py"

Linux/macOS:
    blender -b -noaudio -P blenderkit_asset_tasks/_scripts/run_unittests_in_blender.py -- \
        -s blenderkit_asset_tasks/_test/unittests -p "test_*.py"

If no arguments are provided, sensible defaults for this repository are used.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import platform
import sys
import time
import unittest


def _compute_paths() -> tuple[str, str, str]:
    """Compute repository, package, and tests root paths.

    Returns:
        Tuple of (repo_root, package_root, tests_root).
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, ".."))
    package_root = repo_root
    tests_root = os.path.join(repo_root, "_test")
    return repo_root, package_root, tests_root


def _ensure_sys_path(paths: list[str]) -> None:
    """Prepend paths to sys.path if missing.

    Args:
        paths: List of absolute paths to inject.
    """
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments passed after -- in Blender.

    Args:
        argv: Optional raw args list, defaults to sys.argv after --.

    Returns:
        Parsed arguments with start-dir and pattern.
    """
    repo_root, _package_root, tests_root = _compute_paths()
    default_start = os.path.join(tests_root, "unittests")

    parser = argparse.ArgumentParser(description="Run unittests inside Blender")
    parser.add_argument(
        "-s",
        "--start-dir",
        default=default_start,
        help="Directory to discover tests from (default: repository tests/unittests)",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        default="test_*.py",
        help='Pattern to match test files (default: "test_*.py")',
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("BK_TEST_LOG_LEVEL", "INFO"),
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level (default: INFO or BK_TEST_LOG_LEVEL)",
    )
    parser.add_argument(
        "--log-file",
        default=os.getenv("BK_TEST_LOG_FILE"),
        help="Optional path to write logs in addition to stdout",
    )
    parser.add_argument(
        "--runner-stream",
        choices=["stdout", "stderr", "none"],
        default=os.getenv("BK_TEST_RUNNER_STREAM", "stdout"),
        help="Where unittest's own prints go: stdout, stderr, or none (suppressed)",
    )
    return parser.parse_args(argv)


def _setup_logging(level_name: str, log_file: str | None = None) -> logging.Logger:
    """Configure and return a module logger.

    Args:
        level_name: String name for logging level.
        log_file: Optional file path for a FileHandler.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("blender_tests")
    level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(level)

    # Add a handler if none configured (respect external configs)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.setLevel(level)
        logger.addHandler(handler)

    if log_file and not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
        logger.addHandler(fh)

    # Prevent double logging propagation to root
    logger.propagate = False

    return logger


class LoggingTestResult(unittest.TextTestResult):
    """TestResult that logs clear separators and timings per test."""

    def __init__(self, stream, descriptions, verbosity, logger: logging.Logger):  # type: ignore[no-untyped-def]
        super().__init__(stream, descriptions, verbosity)
        self.logger = logger
        self._start_times: dict[unittest.case.TestCase, float] = {}
        self._sep_major = "=" * 70
        self._sep_minor = "-" * 70

    def startTest(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        self._start_times[test] = time.perf_counter()
        self.logger.info(self._sep_major)
        self.logger.info("TEST START: %s", test.id())
        super().startTest(test)

    def _finish(self, label: str, test: unittest.case.TestCase, level: int = logging.INFO) -> None:
        start = self._start_times.pop(test, None)
        dur = (time.perf_counter() - start) if start is not None else 0.0
        self.logger.log(level, "TEST %s: %s (%.2fs)", label, test.id(), dur)
        self.logger.info(self._sep_minor)

    def addSuccess(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self._finish("PASS", test, logging.INFO)

    def addFailure(self, test: unittest.case.TestCase, err) -> None:  # type: ignore[override] # noqa: N802
        super().addFailure(test, err)
        self._finish("FAIL", test, logging.ERROR)

    def addError(self, test: unittest.case.TestCase, err) -> None:  # type: ignore[override] # noqa: N802
        super().addError(test, err)
        self._finish("ERROR", test, logging.ERROR)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:  # noqa: N802
        super().addSkip(test, reason)
        self._finish(f"SKIP ({reason})", test, logging.WARNING)

    def addExpectedFailure(self, test: unittest.case.TestCase, err) -> None:  # type: ignore[override] # noqa: N802
        super().addExpectedFailure(test, err)
        self._finish("XFAIL", test, logging.INFO)

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        super().addUnexpectedSuccess(test)
        self._finish("XPASS", test, logging.WARNING)


class LoggingTextTestRunner(unittest.TextTestRunner):
    """TextTestRunner that produces LoggingTestResult with our logger."""

    def __init__(self, *args, logger: logging.Logger, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._logger = logger

    def _makeResult(self) -> unittest.TextTestResult:  # type: ignore[override]  # noqa: N802
        return LoggingTestResult(self.stream, self.descriptions, self.verbosity, logger=self._logger)


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for running tests in Blender.

    Args:
        argv: Optional args list after --.

    Returns:
        Exit code 0 on success, 1 on any test failure.
    """
    repo_root, package_root, tests_root = _compute_paths()
    _ensure_sys_path([repo_root, package_root, tests_root, os.path.join(tests_root, "unittests")])

    args = parse_args(argv)
    logger = _setup_logging(args.log_level, args.log_file)

    start_dir = os.path.abspath(args.start_dir)
    pattern = args.pattern

    logger.info("Starting unittest discovery")
    logger.info("Repo root: %s", repo_root)
    logger.info("Start dir: %s | Pattern: %s", start_dir, pattern)
    logger.debug("sys.path: %s", sys.path)
    logger.info(
        "Python: %s | Executable: %s | Platform: %s",
        platform.python_version(),
        sys.executable,
        platform.platform(),
    )

    t0 = time.perf_counter()
    try:
        suite = unittest.defaultTestLoader.discover(start_dir=start_dir, pattern=pattern)
        discovered = suite.countTestCases()
        logger.info("Discovered %d test(s)", discovered)
        # Configure unittest runner stream to avoid duplicated output
        if args.runner_stream == "stdout":
            stream = sys.stdout
        elif args.runner_stream == "stderr":
            stream = sys.stderr
        else:
            stream = io.StringIO()

        runner = LoggingTextTestRunner(verbosity=2, logger=logger, stream=stream)
        result = runner.run(suite)
    except (OSError, ImportError, RuntimeError):
        logger.exception("Failed running tests")
        return 1
    finally:
        dt = time.perf_counter() - t0
        logger.info("Test run completed in %.2fs", dt)

    if not result.wasSuccessful():
        logger.warning(
            "Failures: %d | Errors: %d | Skipped: %d",
            len(result.failures),
            len(result.errors),
            len(getattr(result, "skipped", [])),
        )
        return 1

    logger.info("All tests passed")
    return 0


if __name__ == "__main__":
    # When Blender invokes a script with -P, arguments before -- are Blender's.
    # Everything after -- are ours. VS Code or direct Python will pass all in sys.argv.
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        user_argv = sys.argv[idx + 1 :]
    else:
        user_argv = sys.argv[1:]
    code = main(user_argv)

    # Exit behavior depends on whether running inside Blender background
    # Determine if we're inside Blender's Python; avoid using find_spec on bpy which may have __spec__ None
    try:
        import bpy  # type: ignore
    except ModuleNotFoundError:
        # Not running inside Blender; normal Python exit
        sys.exit(code)
    else:
        if getattr(bpy.app, "background", False):
            sys.exit(code)
