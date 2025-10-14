"""Download only asset zip file to temp and return the path."""

from __future__ import annotations

import tempfile
from typing import Any

from blenderkit_server_utils import concurrency, config, download, log, search, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
    "ASSET_BASE_ID",
])

PAGE_SIZE_LIMIT = 200


def download_asset(asset_data: dict[str, Any], api_key: str) -> str:
    """Download asset zip file to temp and return the path.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authenticated server operations.

    Returns:
        The path to the downloaded asset zip file.
    """
    destination_directory = tempfile.gettempdir()

    # Download asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    logger.info("Downloaded asset to: %s", asset_file_path)
    return asset_file_path


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
) -> None:
    """Iterate over assets and generate Godot GLTF outputs for each.

    Args:
        assets: A list of asset dictionaries to process.
        api_key: API key for authenticated server operations.
    """
    concurrency.run_asset_threads(
        assets,
        worker=download_asset,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=2,
        logger=logger,
    )


def main() -> None:
    """Download only asset zip file to temp and return the path."""
    asset_base_id = config.ASSET_BASE_ID

    params = {
        "asset_base_id": asset_base_id,
    }

    assets = search.get_search_paginated(
        params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Found %s assets", len(assets))
    for i, asset in enumerate(assets):
        logger.info("%s %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"))

    iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
