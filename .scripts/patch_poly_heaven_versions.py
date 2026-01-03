"""Search for PolyHeaven assets.

Check if sourceAppVersion is 1.0 or 3.0 and set 4.5.

If binary file try to detect real version from header,
We need to download the file for that.

# THIS DOES NOT WORK because we cannot patch sourceAppVersion
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

from blenderkit_server_utils import config, download, log, read_header, search, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
])

config.MAX_ASSET_COUNT = 3000
PAGE_SIZE_LIMIT = 200

MINIMUM_HDR_VERSION = 3.0
MINIMUM_VERSION = 4.5

VERSION_PARAM: str = "sourceAppVersion"


def build_params() -> dict[str, Any]:
    """Build search parameters based on environment variables."""
    return {
        "author_id": "118455",  # poly heaven ID
        "order": "-created",
        "asset_type": "model,scene,material,hdr,printable",
        "verification_status": "validated,uploaded",
    }


def path_hdr(asset_data: dict, api_key: str) -> None:
    """Set some meaningful minimum version for hdr assets.

    Args:
        asset_data: The binary data of the .blend file.
        api_key: API key for authentication.
    """
    asset_id = asset_data["id"]

    # get data and check if at least 3.0
    asset_ver_source = asset_data.get("sourceAppVersion", "1.0")
    asset_ver_float = float(asset_ver_source)
    if asset_ver_float >= MINIMUM_HDR_VERSION:
        return

    asset_ver_float = MINIMUM_HDR_VERSION
    asset_ver_source = str(MINIMUM_HDR_VERSION)

    logger.info("Patching asset %s (%s)", asset_data.get("name"), asset_id)

    upload.patch_asset_metadata(
        asset_id=asset_data["id"],
        api_key=api_key,
        data={VERSION_PARAM: asset_ver_source},
    )


def download_asset(asset_data: dict[str, Any], api_key: str) -> str | None:
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


def path_scene(asset_data: dict, api_key: str) -> None:
    """Parse the header of a .blend file to get the version string.

    Args:
        asset_data: The binary data of the .blend file.
        api_key: API key for authentication.
    """
    asset_id = asset_data["id"]

    # get data and check if at least 4.5
    asset_ver_source = asset_data.get("sourceAppVersion", "1.0")
    asset_ver_float = float(asset_ver_source.split(".")[0] + "." + asset_ver_source.split(".")[1])
    if asset_ver_float >= MINIMUM_VERSION:
        return

    asset_ver_float = MINIMUM_VERSION
    asset_ver_source = str(MINIMUM_VERSION)

    logger.info("Patching asset %s (%s)", asset_data.get("name"), asset_id)
    # download the binary to check the header
    ass_path = download_asset(asset_data, api_key=api_key)
    if not ass_path:
        return

    # get version
    head_version = read_header.detect_blender_version(ass_path)

    # remove folder
    shutil.rmtree(os.path.dirname(ass_path))

    logger.info("Header version detected: %s", head_version)

    if not head_version:
        return

    head_version = head_version["version"]

    upload.patch_asset_metadata(
        asset_id=asset_data["id"],
        api_key=api_key,
        data={VERSION_PARAM: asset_ver_source},
    )


def _quick_version_compare(v1_str, min_v_str):
    # convert to version tuples
    v1_tuple = tuple(map(int, str(v1_str).split(".")))
    min_tuple = tuple(map(int, str(min_v_str).split(".")))
    return v1_tuple >= min_tuple


def main() -> None:
    """Search only assets and log results."""
    params = build_params()

    assets = search.get_search_paginated(
        params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    # filter assets with version bigger then min version
    assets = [
        ass for ass in assets if not _quick_version_compare(ass.get("sourceAppVersion", "1.0"), MINIMUM_HDR_VERSION)
    ]
    amount = len(assets)
    logger.info("Found %s assets", amount)
    for i, asset in enumerate(assets):
        logger.info("%s/%s %s ||| %s", i + 1, amount, asset.get("name"), asset.get("assetType"))
        if asset.get("assetType") in {"hdr"}:
            path_hdr(asset, api_key=config.BLENDERKIT_API_KEY)
        else:
            path_scene(asset, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
