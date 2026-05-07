"""Download only asset zip file to temp and return the path."""

from __future__ import annotations

from typing import Any

import generate_proxors
from blenderkit_server_utils import config, log, search, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
])

PAGE_SIZE_LIMIT = 200


def build_params() -> dict[str, Any]:
    """Build search parameters based on environment variables."""
    # query=%2Bauthor_id%3A2+asset_type%3Amodel
    return {
        "order": "-created",
        "asset_type": "model,printable",
        "author_id": "2",
        "verification_status": "validated",
        "last_prxc_upload_isnull": True,
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
        # run proxor generation for each asset, which will download the zip file as part of the process
        config.ASSET_BASE_ID = asset.get("assetBaseId")
        generate_proxors.main()
        logger.info("%s/%s %s ||| %s ||| %s", i + 1, len(assets), asset.get("name"), asset.get("assetType"), asset.get("assetBaseId"))


if __name__ == "__main__":
    main()
