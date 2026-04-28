"""Concurrency utilities for BlenderKit asset tasks."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from . import exceptions

DEFAULT_POLL_INTERVAL = 0.1


def run_asset_threads(  # noqa: PLR0913, C901
    assets: Iterable[dict[str, Any]],
    worker: Callable[..., Any],
    *,
    max_concurrency: int = 2,
    logger: logging.Logger,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    worker_args: Sequence[Any] | None = None,
    worker_kwargs: dict[str, Any] | None = None,
    asset_arg_position: int = 0,
    fatal_exceptions: tuple[type[BaseException], ...] = (),
) -> None:
    """Run a worker callable over assets with controlled concurrency.

    This is a generalized version allowing arbitrary extra positional and keyword
    arguments for the worker. The current asset dict is injected at
    ``asset_arg_position`` among the positional args (default 0 = first).

    Args:
        assets: Iterable of asset metadata dicts.
        worker: Callable invoked per asset.
        max_concurrency: Maximum number of concurrent threads.
        logger: Logger for logging messages.
        poll_interval: Sleep duration between concurrency checks.
        worker_args: Additional positional arguments (excluding the per-asset dict).
        worker_kwargs: Keyword arguments passed to worker each invocation.
        asset_arg_position: Index in positional args where the asset dict will be inserted.
        fatal_exceptions: Exception types that should abort the whole run. When
            a worker raises one of these, no further assets are dispatched, the
            already-running threads are awaited, and the original exception is
            re-raised from the main thread so calling scripts (and CI) fail.

    Notes:
        Backward compatibility: previous signature expected (asset, api_key). To
        replicate, pass worker_args=(api_key,) with asset_arg_position=0 (default),
        which produces the call worker(asset, api_key).
    """
    threads: list[threading.Thread] = []
    static_args: Sequence[Any] = worker_args or ()
    static_kwargs: dict[str, Any] = worker_kwargs or {}

    abort_event = threading.Event()
    fatal_lock = threading.Lock()
    fatal_holder: dict[str, BaseException] = {}

    for asset in assets:
        if abort_event.is_set():
            logger.error("Fatal worker error detected; aborting remaining assets")
            break
        if not asset:
            logger.warning("Skipping empty asset entry")
            continue

        if asset_arg_position < 0 or asset_arg_position > len(static_args):
            logger.error("Invalid asset_arg_position %s (len=%s)", asset_arg_position, len(static_args))
            continue

        call_args = list(static_args)
        call_args.insert(asset_arg_position, asset)

        def _thread_target(
            a_args: tuple[Any, ...],
            a_kwargs: dict[str, Any],
            asset_keys_snapshot: tuple[str, ...],
        ) -> None:
            try:
                worker(*a_args, **a_kwargs)
                # raise exception to test error handling
            except fatal_exceptions as fatal_exc:  # type: ignore[misc]
                logger.critical(
                    "Fatal worker error (asset keys=%s): %s",
                    asset_keys_snapshot,
                    fatal_exc,
                )
                with fatal_lock:
                    if "error" not in fatal_holder:
                        fatal_holder["error"] = fatal_exc
                abort_event.set()
            except Exception as e:
                logger.exception("Worker raised exception (asset keys=%s)", asset_keys_snapshot)
                # complete traceback
                logger.error(traceback.format_exc())  # noqa: TRY400

                # reraise to mark thread as failed if needed
                raise exceptions.ProcessingError(
                    f"Error processing asset {asset_keys_snapshot}: {e}",
                ) from e

        asset_keys_snapshot = (
            asset.get("asset_base_id", ""),
            asset.get("assetType", ""),
            asset.get("name", "N/A"),
        )

        try:
            logger.debug("Starting thread for asset %s", asset_keys_snapshot)

            t = threading.Thread(
                target=_thread_target,
                args=(tuple(call_args), static_kwargs, asset_keys_snapshot),
            )
            t.start()
        except Exception:  # Thread creation or start failure
            logger.exception("Failed to start thread for asset: %s", asset_keys_snapshot)

            # FUTURE: decide if we want to continue processing other assets
            # FUTURE: write processing error to database

            continue

        threads.append(t)

        while sum(1 for th in threads if th.is_alive()) >= max_concurrency:
            threads = [th for th in threads if th.is_alive()]
            time.sleep(poll_interval)

    for t in threads:
        t.join()

    fatal_error = fatal_holder.get("error")
    if fatal_error is not None:
        raise fatal_error
