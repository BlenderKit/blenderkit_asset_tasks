"""Download only asset zip file to temp and return the path."""

from __future__ import annotations

from typing import Any

from blenderkit_server_utils import config, log, search, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
])

PAGE_SIZE_LIMIT = 200


def build_params() -> dict[str, Any]:
    """Build search parameters based on environment variables."""
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {
            "asset_base_id": asset_base_id,
        }
    return {
        "order": "-created",
        "asset_type": "model,scene,material,printable",
        "verification_status": "validated,uploaded",
        "validatedManufacturer_isnull": "true",
    }


def main() -> None:
    """Search only assets and log results."""
    params = build_params()

    assets = search.get_search_paginated(
        params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Found %s assets", len(assets))
    for i, asset in enumerate(assets):
        logger.info("%s %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"))


if __name__ == "__main__":
    main()
