"""Delete a specific parameter from all matching assets.

This utility queries assets that contain a given parameter and removes that
parameter from each asset. Intended for DB cleanup and can be run manually.

Notes:
- Set ``param_name`` below to the parameter you want to delete.
- Ensure the API key is available (config.BLENDERKIT_API_KEY).
"""

import os
import tempfile
import time
from typing import Any

from blenderkit_server_utils import config, log, search, upload, utils

# Module logger
logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["API_KEY"])

param_name: str = ""

MAX_ASSETS: int = 10000


def main() -> None:
    """Run the deletion job for all assets matching the parameter.

    The function fetches assets that contain ``param_name`` (not null) and then
    deletes that parameter for each asset. It logs progress and per-asset timing.
    """
    if not param_name:
        raise ValueError("param_name must be set to the parameter you want to delete.")

    params: dict[str, Any] = {f"{param_name}_isnull": False}
    dpath: str = tempfile.gettempdir()
    filepath: str = os.path.join(dpath, "assets_for_resolutions.json")

    logger.info("Fetching assets with param '%s' (max=%d)", param_name, MAX_ASSETS)
    assets: list[dict[str, Any]] = search.get_search_simple(
        params,
        filepath,
        page_size=min(MAX_ASSETS, 100),
        max_results=MAX_ASSETS,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Found %d assets to process", len(assets))

    for asset_data in assets:
        start_time = time.time()
        asset_id = asset_data["id"]
        param_value: str = ""

        upload.delete_individual_parameter(
            asset_id=asset_id,
            param_name=param_name,
            param_value=param_value,
            api_key=config.BLENDERKIT_API_KEY,
        )
        upload.patch_asset_empty(asset_id=asset_id, api_key=config.BLENDERKIT_API_KEY)

        duration_s: float = time.time() - start_time
        logger.info("Asset %s processed in %.3f s", asset_id, duration_s)


if __name__ == "__main__":
    main()
