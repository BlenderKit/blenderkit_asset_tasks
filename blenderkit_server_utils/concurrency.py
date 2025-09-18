"""Concurrency utilities for BlenderKit asset tasks."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

DEFAULT_POLL_INTERVAL = 0.1


def run_asset_threads(  # noqa: PLR0913
    assets: Iterable[dict[str, Any]],
    worker: Callable[[dict[str, Any], str], None],
    api_key: str,
    *,
    max_concurrency: int,
    logger: logging.Logger,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> None:
    """Run worker function on assets with controlled concurrency.

    Args:
        assets: Iterable of asset metadata dicts.
        worker: Function to execute per asset; takes asset dict and api_key as arguments.
        api_key: BlenderKit API key forwarded to the worker function.
        max_concurrency: Maximum number of concurrent threads.
        logger: Logger for logging messages.
        poll_interval: Time in seconds to wait between concurrency checks.

    Returns:
        None
    """
    threads: list[threading.Thread] = []
    for asset in assets:
        if not asset:
            logger.warning("Skipping empty asset entry")
            continue
        t = threading.Thread(target=worker, args=(asset, api_key))
        t.start()
        threads.append(t)
        while sum(1 for th in threads if th.is_alive()) >= max_concurrency:
            threads = [th for th in threads if th.is_alive()]
            time.sleep(poll_interval)
    for t in threads:
        t.join()
