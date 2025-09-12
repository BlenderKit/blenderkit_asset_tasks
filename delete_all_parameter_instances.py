"""Delete a specific parameter from all matching assets.

This utility queries assets that contain a given parameter and removes that
parameter from each asset. It's intended for DB cleanup and can be run
manually. Provide the parameter name in ``param_name`` and ensure the
``BLENDERKIT_API_KEY`` environment variable is available.
"""

import os
import tempfile
import time
from typing import Any

from blenderkit_server_utils import paths, search, upload

param_name: str = ""
params: dict[str, Any] = {
    param_name + "_isnull": False,
}
dpath: str = tempfile.gettempdir()
filepath: str = os.path.join(dpath, "assets_for_resolutions.json")
MAX_ASSETS: int = 10000

assets: list[dict[str, Any]] = search.get_search_simple(
    params,
    filepath,
    page_size=min(MAX_ASSETS, 100),
    max_results=MAX_ASSETS,
    api_key=paths.API_KEY,
)

for asset_data in assets:
    start_time = time.time()

    asset_id = asset_data["id"]
    param_value: str = ""

    upload.delete_individual_parameter(
        asset_id=asset_id,
        param_name=param_name,
        param_value=param_value,
        api_key=paths.API_KEY,
    )
    upload.patch_asset_empty(asset_id=asset_id, api_key=paths.API_KEY)
    # ------------------------------------------------------------------------------------------------------------------
    print("--- %s seconds ---" % (time.time() - start_time))
    # ------------------------------------------------------------------------------------------------------------------
