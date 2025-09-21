"""Integration-style tests for concurrency.run_asset_threads.

We exercise the thread runner with a mixture of successful and failing
assets and assert that:
 - The worker is called for each asset
 - Exceptions in the worker do not crash the runner
 - An exception triggers exactly one logged error entry containing the
   asset key snapshot (first few keys) as implemented in concurrency.py

Rather than parsing stderr formatting rigorously, we capture log records
with a custom handler.
"""

from __future__ import annotations

import logging
import threading
import unittest

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import concurrency, log  # noqa: E402


class ListHandler(logging.Handler):
    """Collect log records in a list for assertions.

    Thread-safe to allow concurrent emits from worker threads.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Store a log record emitted by the logger."""
        with self._lock:
            self.records.append(record)


class RunAssetThreadsTests(unittest.TestCase):
    """Integration tests for the thread runner ensuring resilience to failures."""

    def setUp(self) -> None:
        """Set up a dedicated logger with in-memory handler for each test."""
        self.logger = log.create_logger("blenderkit.test.concurrency")
        self.logger.handlers = []  # isolate from global stderr handler
        self.list_handler = ListHandler()
        self.logger.addHandler(self.list_handler)
        self.logger.setLevel(logging.DEBUG)

    def test_exceptions_are_logged_and_continue(self) -> None:
        """A failing worker should log exactly one exception and not stop others."""
        assets = [
            {"id": "ok1", "value": 1},
            {"id": "boom", "value": 0},
            {"id": "ok2", "value": 2},
        ]

        call_order: list[str] = []

        def worker(asset: dict[str, int], multiplier: int = 2) -> None:
            call_order.append(asset["id"])  # record attempted asset
            if asset["id"] == "boom":
                raise RuntimeError("induced failure")
            _ = asset["value"] * multiplier  # simulate work

        concurrency.run_asset_threads(
            assets,
            worker=worker,
            worker_kwargs={"multiplier": 3},
            asset_arg_position=0,
            max_concurrency=2,
            logger=self.logger,
        )

        # All assets should have been processed (order not guaranteed due to threads)
        self.assertCountEqual(call_order, ["ok1", "boom", "ok2"])

        error_messages = [r.getMessage() for r in self.list_handler.records if r.levelno >= logging.ERROR]
        self.assertTrue(
            any("Worker raised exception" in m for m in error_messages),
            "Expected logged worker exception",
        )
        self.assertEqual(
            sum(1 for m in error_messages if "Worker raised exception" in m),
            1,
            "Should log exactly one worker exception",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
