"""Validate and optionally clean manufacturer metadata for BlenderKit assets.

The script fetches candidate assets, runs the field-validation heuristics (with
optional AI backing), patches validation status parameters, and can remove
invalid manufacturer/designer/collection/year entries when configured.

Environment variables:
    Required:
        BLENDERKIT_API_KEY: Auth token for asset API calls.
    Optional:
        ASSET_BASE_ID: Restrict processing to a single asset.
        MAX_VALIDATION_THREADS: Override concurrency level (defaults to config).
        SKIP_UPDATE: Set to "1" to perform dry runs without patching assets.
"""

from __future__ import annotations

import argparse
import datetime
import os
from typing import Any

from blenderkit_server_utils import concurrency, config, log, search, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(
    ["BLENDERKIT_API_KEY"],
)

SKIP_UPDATE: bool = config.SKIP_UPDATE

PAGE_SIZE_LIMIT: int = 200

PARAM_BOOL: str = "validatedManufacturer"
PARAM_DATE: str = "validatedManufacturerDate"
PARAM_RESULT: str = "validatedManufacturerOutput"
PARAM_ACTOR: str = "validatedManufacturerActor"

# manufacturer parameters, inside "dictParameters"
MAN_PARAM_MANUFACTURER: str = "manufacturer"
MAN_PARAM_DESIGNER: str = "designer"
MAN_PARAM_COLLECTION: str = "designCollection"
MAN_PARAM_VARIANT: str = "designVariant"  # should we permit this ?
MAN_PARAM_YEAR: str = "designYear"

ALL_MAN_PARAMS: list[str] = [
    MAN_PARAM_MANUFACTURER,
    MAN_PARAM_DESIGNER,
    MAN_PARAM_COLLECTION,
    MAN_PARAM_VARIANT,
    MAN_PARAM_YEAR,
]

ASSET_LOG_PREVIEW: int = 20

ValidationStat = dict[str, Any]


def correct_date_to_iso(date_str: str) -> str:
    """Convert a date string to ISO 8601 format if possible.

    From 2025-12-05 to 2025-12-05T00:00:00Z
    """
    if not date_str:
        return ""
    date_str = date_str.strip()
    try:
        # If it's already an ISO timestamp, return as-is
        if "T" in date_str:
            return date_str
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            return None
    except Exception:  # noqa: BLE001
        return date_str


def correct_dates(
    asset_data: dict[str, Any],
    api_key: str,
) -> None:
    """Worker for a single asset; mode selects model/material pipeline."""
    # basic guards
    if not asset_data:
        logger.warning("Skipping empty or invalid asset entry")
        return

    # capture data that may be removed
    validation_date = asset_data.get("dictParameters", {}).get(PARAM_DATE, "")
    if not validation_date:
        return

    corrected_date = correct_date_to_iso(validation_date)
    if validation_date == corrected_date:
        return
    if SKIP_UPDATE:
        logger.info(
            "SKIP_UPDATE set, would update %s for asset %s: %s -> %s",
            PARAM_DATE,
            asset_data.get("id"),
            validation_date,
            corrected_date,
        )
        return

    logger.info(
        "Updating %s for asset %s: %s -> %s",
        PARAM_DATE,
        asset_data.get("id"),
        validation_date,
        corrected_date,
    )
    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=PARAM_DATE,
        param_value=corrected_date,
        api_key=api_key,
    )


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
) -> None:
    """Iterate assets and dispatch tag validation threads.

    Args:
        assets: List of asset dictionaries to process.
        api_key: BlenderKit API key forwarded to the thread function.

    Returns:
        Collected per-asset validation statistics.
    """
    concurrency.run_asset_threads(
        assets,
        worker=correct_dates,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=config.MAX_VALIDATION_THREADS,
        logger=logger,
    )


def _base_params() -> dict[str, Any]:
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {"asset_base_id": asset_base_id}
    return {
        PARAM_DATE + "_isnull": "false",
    }


def _iter_assets(limit: int | None):
    """Yield assets using paginated search without accumulating all results."""
    params = _base_params()
    max_results = limit if limit is not None else search.DEFAULT_MAX_RESULTS
    total_yielded = 0
    for page in search.iter_search_pages(
        params,
        page_size=PAGE_SIZE_LIMIT,
        max_results=max_results,
        api_key=config.BLENDERKIT_API_KEY,
    ):
        if not page:
            continue
        yield from page
        total_yielded += len(page)
        if total_yielded >= max_results:
            break


def main(_argv: list[str] | None = None) -> None:
    """Fetch assets, validate manufacturer metadata, and patch results."""
    parser = argparse.ArgumentParser(description="Correct validatedManufacturerDate to full ISO timestamps.")
    parser.add_argument(
        "--max-assets",
        type=int,
        default=None,
        help="Maximum assets to process; omit or set to 0/negative for all matches.",
    )
    args = parser.parse_args(_argv or [])

    env_limit = None
    if "MAX_ASSET_COUNT" in os.environ:
        env_limit = config.MAX_ASSET_COUNT if config.MAX_ASSET_COUNT > 0 else None
    cli_limit = args.max_assets if args.max_assets and args.max_assets > 0 else None
    fetch_limit = cli_limit if cli_limit is not None else env_limit

    logger.info("Fetch limit: %s (page_size=%s)", fetch_limit or "all", PAGE_SIZE_LIMIT)

    asset_buffer: list[dict[str, Any]] = []
    processed = 0

    for asset in _iter_assets(fetch_limit):
        asset_buffer.append(asset)
        if len(asset_buffer) >= PAGE_SIZE_LIMIT:
            iterate_assets(asset_buffer, api_key=config.BLENDERKIT_API_KEY)
            processed += len(asset_buffer)
            asset_buffer.clear()

    if asset_buffer:
        iterate_assets(asset_buffer, api_key=config.BLENDERKIT_API_KEY)
        processed += len(asset_buffer)

    logger.info("Processed %d assets for date correction", processed)


if __name__ == "__main__":
    main()
