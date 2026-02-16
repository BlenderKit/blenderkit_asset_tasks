"""Validate addon.

- Check syntax errors.
- Detect OS dependencies.
- Check calling of subprocess and eval.
- Try to install in blender and check for errors in the console.

"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from blenderkit_server_utils import api_nice, concurrency, config, datetime_utils, log, search, utils
from blenderkit_server_utils.asset_validation import addon_validation

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(
    ["BLENDERKIT_API_KEY"],
)
COMMENT_API_KEY = os.environ.get("TEXTYBOT_API_KEY", config.BLENDERKIT_API_KEY)

SKIP_UPDATE: bool = config.SKIP_UPDATE

PAGE_SIZE_LIMIT: int = 500

ASSET_LOG_PREVIEW: int = 20


def _fetch_assets() -> list[dict[str, Any]]:
    """Fetch assets targeted for addon validation."""
    temp_dir = tempfile.mkdtemp(prefix="bk_addon_cleanup_")
    params = _build_search_params()
    assets: list[dict[str, Any]] = []
    try:
        assets = search.get_search_paginated(
            params,
            page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
            max_results=config.MAX_ASSET_COUNT,
            api_key=config.BLENDERKIT_API_KEY,
        )

        logger.info("Assets to be processed: %d", len(assets))
        for asset in assets[:ASSET_LOG_PREVIEW]:
            logger.debug("%s ||| %s", asset.get("name"), asset.get("assetType"))
    finally:
        utils.cleanup_temp(temp_dir)
    return assets


def addon_validation_thread(asset_data: dict[str, Any]) -> None:
    """Worker for a single addon."""
    # basic guards
    if not asset_data:
        logger.warning("Skipping empty or invalid asset entry")
        return
    # double check if we have addon type, to avoid confusion with the validation result parameters
    if asset_data.get("assetType") != "addon":
        logger.warning(
            "Skipping non-addon asset '%s' of type '%s'", asset_data.get("name"), asset_data.get("assetType"),
        )
        return

    today = datetime_utils.today_date_iso()

    result = addon_validation.validate_addon.validate(asset_data)
    if not result:
        logger.warning("Addon validator failed successfully for '%s'", asset_data.get("name"))
        return
    status, reason, captured_data = result

    # no patching just simple comment for now,
    # to gather data and feedback before any automation of patching
    comment = (
        f"Addon validation result for {today}:\nStatus: {status}\nReason: {reason}\nCaptured Data: {captured_data}"
    )
    if SKIP_UPDATE:
        logger.info("SKIP_UPDATE is True, not uploading comment. Validation result:\n%s", comment)
        return

    api_nice.create_comment(
        comment=comment,
        asset_base_id=asset_data.get("assetBaseId", ""),
        # prefer KEY for account of specialized commenting bot
        api_key=COMMENT_API_KEY,
        server_url=config.SERVER,
    )
    logger.info("Comment uploaded")


def iterate_assets(
    assets: list[dict[str, Any]],
) -> None:
    """Iterate assets and dispatch tag validation threads.

    Args:
        assets: List of asset dictionaries to process.

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=addon_validation_thread,
        asset_arg_position=0,
        max_concurrency=config.MAX_VALIDATION_THREADS,
        logger=logger,
    )


def _build_search_params() -> dict[str, Any]:
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {"asset_base_id": asset_base_id}

    # only not validated manufacturers
    out = {
        "order": "-created",
        "asset_type": "addon",
        "verification_status": "uploaded",
    }
    return out


def main(_argv: list[str] | None = None) -> None:
    """Fetch assets, validate addon, patch results."""
    assets: list[dict[str, Any]] = []
    assets = _fetch_assets()

    if assets:
        iterate_assets(assets)


if __name__ == "__main__":
    main()
