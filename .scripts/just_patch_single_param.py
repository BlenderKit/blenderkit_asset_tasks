"""Download only asset zip file to temp and return the path."""

from __future__ import annotations

from typing import Any

from blenderkit_server_utils import config, log, search, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
])

PAGE_SIZE_LIMIT = 200


TRY_PARAM: str = "manufacturer"
REM_PARAM: str = "manufacturer"

def build_params() -> dict[str, Any]:
    """Build search parameters based on environment variables."""
    return {
        # custom test asset base id "bronze_elephant" asset
        "asset_base_id": "fd7b271b-bb38-4984-b51a-c85287cd3913",
    }


def try_patch(asset_data: dict[str, Any], api_key: str) -> None:
    """Try to patch a single asset's parameter."""
    asset_id = asset_data["id"]
    logger.info("Patching asset %s (%s)", asset_data.get("name"), asset_id)

    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=TRY_PARAM,
        param_value="MONGO",
        api_key=api_key,
    )

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

        try_patch(asset, api_key=config.BLENDERKIT_API_KEY)

if __name__ == "__main__":
    main()
