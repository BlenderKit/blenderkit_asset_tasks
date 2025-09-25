"""Script to generate resolutions for assets that don't have them yet.

For single asset processing, set ASSET_BASE_ID to the asset_base_id.

BLENDER_PATH may be defined in environment or config.py for version of Blender to use.
Otherwise, BLENDERS_PATH must be set to a folder with Blender versions.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
from typing import Any

from blenderkit_server_utils import concurrency, config, download, log, search, send_to_bg, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY", "BLENDERS_PATH"])

# Constants
PAGE_SIZE_LIMIT: int = 100

SKIP_UPLOAD = os.getenv("SKIP_UPLOAD", False) == "True"  # noqa: FBT003, PLW1508


def _maybe_unpack_asset(asset_data: dict[str, Any], asset_file_path: str, blender_binary_path: str) -> None:
    """Unpack asset in Blender when needed.

    Args:
        asset_data: Asset data dictionary.
        asset_file_path: Path to the downloaded asset file.
        blender_binary_path: Path to Blender binary.
    """
    if asset_file_path and asset_data.get("assetType") != "hdr":
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            script="unpack_asset_bg.py",
            binary_path=blender_binary_path,
        )


def _send_to_bg_for_resolutions(
    asset_data: dict[str, Any],
    asset_file_path: str,
    blender_binary_path: str,
) -> tuple[str, str]:
    """Dispatch Blender background job to generate resolutions.

    Creates a temp folder for results and calls Blender with the right script
    depending on asset type.

    Args:
        asset_data: Asset data dictionary.
        asset_file_path: Path to the downloaded asset file.
        blender_binary_path: Path to Blender binary.

    Returns:
        A tuple of (temp_folder, result_path).
    """
    temp_folder = tempfile.mkdtemp()
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")

    if asset_data.get("assetType") == "hdr":
        current_dir = pathlib.Path(__file__).parent.resolve()
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            template_file_path=os.path.join(current_dir, "blend_files", "empty.blend"),
            result_path=result_path,
            script="resolutions_bg_blender_hdr.py",
            binary_path=blender_binary_path,
        )
    else:
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            result_path=result_path,
            script="resolutions_bg_blender.py",
            binary_path=blender_binary_path,
        )
    return temp_folder, result_path


def _read_result_files(result_path: str) -> list[dict[str, Any]] | None:
    """Read JSON results from Blender background process.

    Args:
        result_path: Path to the JSON results file.

    Returns:
        A list of file dicts or None on error.
    """
    try:
        with open(result_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        logger.exception("Error reading result JSON %s", result_path)
        return None


def _determine_result_and_upload(
    files: list[dict[str, Any]] | None,
    asset_data: dict[str, Any],
    api_key: str,
) -> str:
    """Upload results when present and return operation state.

    Args:
        files: List of generated files, None on error, empty if skipped.
        asset_data: Asset data dictionary.
        api_key: API key.

    Returns:
        One of "success", "error", or "skipped".
    """
    if files is None:
        return "error"
    if not files:
        return "skipped"
    try:
        upload.upload_resolutions(files, asset_data, api_key=api_key)
    except Exception:
        logger.exception("Upload resolutions failed for asset %s", asset_data.get("id"))
        return "error"
    else:
        return "success"


def _cleanup(temp_folder: str, asset_file_path: str, asset_id: str | None) -> None:
    """Delete temporary artifacts from disk.

    Args:
        temp_folder: Temporary directory path.
        asset_file_path: Path to the downloaded asset file.
        asset_id: Optional asset ID for logging.
    """
    try:
        shutil.rmtree(temp_folder)
    except (FileNotFoundError, PermissionError, OSError):
        logger.exception("Error while deleting temp folder %s", temp_folder)
    try:
        os.remove(asset_file_path)
    except (FileNotFoundError, PermissionError, OSError):
        logger.exception("Error while deleting asset file %s", asset_file_path)
    logger.debug("Deleted temp folder and asset file for %s", asset_id)


def generate_resolution_thread(asset_data: dict[str, Any], api_key: str) -> None:
    """Thread to generate resolutions for a single asset.

    A thread that:
     1.downloads file
     2.starts an instance of Blender that generates the resolutions
     3.uploads files that were prepared
     4.patches asset data with a new parameter.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.

    Returns:
        None
    """
    # skip empty assets
    if not asset_data or not asset_data.get("files"):
        logger.warning("Skipping empty or invalid asset entry")
        return

    destination_directory = tempfile.gettempdir()

    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)
    if not asset_file_path:
        # wrong api key/plan, or private asset submitted
        return

    _maybe_unpack_asset(asset_data, asset_file_path, config.BLENDER_PATH)
    temp_folder, result_path = _send_to_bg_for_resolutions(asset_data, asset_file_path, config.BLENDER_PATH)

    files = _read_result_files(result_path)
    if SKIP_UPLOAD:
        logger.warning("SKIP_UPLOAD==True -> skipping upload")
        _cleanup(temp_folder, asset_file_path, asset_data.get("id"))
        return

    result_state = _determine_result_and_upload(files, asset_data, api_key)
    logger.info("Result state for asset %s: %s", asset_data.get("id"), result_state)
    upload.patch_asset_empty(asset_data["assetBaseId"], api_key=api_key)
    _cleanup(temp_folder, asset_file_path, asset_data.get("id"))
    return


def iterate_assets(
    assets: list[dict[str, Any]],
    process_count: int = 12,
    api_key: str = "",
) -> None:
    """Iterate through all assigned assets, check for those which need generation and send them to res gen.

    Args:
        assets: List of asset dictionaries to process.
        process_count: Number of concurrent processes to run. (optional)
        api_key: API key for authentication. (optional)

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=generate_resolution_thread,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=process_count,
        logger=logger,
    )


def main() -> None:
    """Main function to generate resolutions for assets."""
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_resolutions.json")
    # search for assets if assets were not provided with these parameters

    # this selects specific assets
    # only material, model and hdr are supported currently. We can do scenes in future potentially
    # only validated public assets are processed
    # only files from a certain size are processed (over 1 MB)
    # Note: The parameter last_resolution_upload currently searches for assets that were never processed.
    # Note: We should also process updated assets and record a specific parameter for updates.
    params = {
        "asset_type": "model,material,hdr",
        # >'asset_type': 'hdr',
        "order": "-created",
        "verification_status": "validated",
        # >'textureResolutionMax_gte': '1024',
        "files_size_gte": "1024000",
        # >
        "last_resolution_upload_isnull": True,
    }

    # if ASSET_BASE_ID was provided, get just a single asset
    if config.ASSET_BASE_ID is not None:
        params = {
            "asset_base_id": config.ASSET_BASE_ID,
        }

    assets = search.get_search_simple(
        params,
        filepath,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )

    assets = search.load_assets_list(filepath)

    logger.info("Count of assets to be processed: %s", len(assets))
    for a in assets:
        logger.debug("%s ||| %s", a.get("name"), a.get("assetType"))

    iterate_assets(assets, process_count=1, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
