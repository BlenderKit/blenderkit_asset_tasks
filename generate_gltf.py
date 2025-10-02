"""Generate GLTF files tailored for the Godot engine.

This variant disables Draco compression and updates dedicated parameters on
the asset to indicate success or failure of the Godot-optimized export.
"""

import argparse
import json
import os
import sys
import tempfile
from typing import Any

from blenderkit_server_utils import (
    concurrency,
    config,
    datetime_utils,
    download,
    log,
    search,
    send_to_bg,
    upload,
    utils,
)

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY"])

# if BLENDER_PATH is not defined but we have BLENDERS_PATH
# get latest version from there
if not config.BLENDER_PATH and config.BLENDERS_PATH:
    config.BLENDER_PATH = utils.get_latest_blender_path(config.BLENDERS_PATH)

if not config.BLENDER_PATH:
    logger.error("At least one of BLENDER_PATH & BLENDERS_PATH must be set.")
    sys.exit(1)

args = argparse.ArgumentParser()
args.add_argument("--target_format", type=str, default="gltf_godot", help="Target export format")


# Constants
TARGET_FORMAT = args.parse_args().target_format
if TARGET_FORMAT:
    logger.info("Using target format from args: %s", TARGET_FORMAT)
if not TARGET_FORMAT:
    # use env var or default
    TARGET_FORMAT = os.getenv("TARGET_FORMAT", None)
    if not TARGET_FORMAT:
        logger.info("No target format specified, defaulting to 'gltf_godot'")

if not TARGET_FORMAT:
    logger.error("No target format specified, defaulting to 'gltf_godot'")
    TARGET_FORMAT = "gltf_godot"

# target mode can be only one of these 2 now
if TARGET_FORMAT not in ["gltf", "gltf_godot"]:
    logger.error("Invalid target format specified: %s", TARGET_FORMAT)
    sys.exit(1)

PAGE_SIZE_LIMIT: int = 100
PARAM_SUCCESS: str = "gltfGeneratedDate"
PARAM_ERROR: str = "gltfGeneratedError"

if "godot" in TARGET_FORMAT:
    PARAM_SUCCESS = "gltfGodotGeneratedDate"
    PARAM_ERROR = "gltfGodotGeneratedError"

SKIP_UPDATE: bool = config.SKIP_UPDATE


def generate_gltf(asset_data: dict[str, Any], api_key: str, binary_path: str, target_format: str) -> bool:  # noqa: C901, PLR0915
    """Generate and upload a Godot-optimized GLTF for a single asset.

    Steps:
    1. Download the asset archive.
    2. Unpack the asset via a background Blender process.
    3. Run a background Blender export to produce a Godot-optimized GLTF.
    4. Upload the generated files and patch an asset parameter on success.

    Args:
        asset_data: Asset metadata returned from the search API.
        api_key: API key used for authenticated operations.
        binary_path: Absolute path to the Blender binary used for background operations.
        target_format: The target export format, e.g., 'gltf_godot'.

    Returns:
        True when the GLTF was generated and uploaded successfully; False otherwise.
    """
    error = ""
    destination_directory = tempfile.gettempdir()

    # Download asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    if not asset_file_path:
        logger.error("Asset file not found on path %s", asset_file_path)
        return False

    # Unpack asset
    send_to_bg.send_to_bg(
        asset_data=asset_data,
        asset_file_path=asset_file_path,
        script="unpack_asset_bg.py",
        binary_path=binary_path,
    )

    # Send to background to generate GLTF
    temp_folder = tempfile.mkdtemp()
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")
    # should we remove the temp folder after use? yes, we do it in send_to_bg
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        result_path=result_path,
        script="gltf_bg_blender.py",
        binary_path=binary_path,
        target_format=target_format,
    )

    files: list[dict[str, Any]] | None = None
    try:
        with open(result_path, encoding="utf-8") as f:
            files = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError) as exc:
        logger.exception("Error reading result JSON %s", result_path)
        error += f" {exc}"

    logger.info("Cleaning up temporary folder %s", temp_folder)
    if files:
        logger.info("Generated files: %s", files)

    if files is None:
        error += " Files are None"
    elif len(files) == 0:
        error += " len(files)=0"
    else:
        logger.info("Generated files: %s", files)

        if SKIP_UPDATE:
            logger.info("SKIP_UPDATE is set, not patching the asset.")
            opened = utils.open_folder(os.path.dirname(files[0]["path"]))
            if not opened:
                logger.error("Failed to open folder %s", os.path.dirname(files[0]["path"]))
                utils.cleanup_temp(temp_folder)

            return False

        try:
            upload.upload_resolutions(files, asset_data, api_key=api_key)
            today = datetime_utils.today_date_iso()
            upload.patch_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_SUCCESS,
                param_value=today,
                api_key=api_key,
            )
            upload.get_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_SUCCESS,
                api_key=api_key,
            )

            logger.info("Patched %s=%s for asset %s", PARAM_SUCCESS, today, asset_data.get("id"))
        except Exception:  # upload module uses requests; narrow errors are internal
            logger.exception(
                "Failed to upload resolutions or patch success parameter for asset %s",
                asset_data.get("id"),
            )
            error += " upload/patch failed"
        else:
            # Success path
            utils.cleanup_temp(temp_folder)

            return True

    # Failure path: patch error parameter
    logger.error(
        "GLTF format '%s' generation failed for asset %s: %s",
        target_format,
        asset_data.get("id"),
        error.strip(),
    )

    utils.cleanup_temp(temp_folder)

    try:
        if SKIP_UPDATE:
            logger.info("SKIP_UPDATE is set, not patching the asset.")
            return False

        value = error.strip()
        upload.patch_individual_parameter(
            asset_id=asset_data["id"],
            param_name=PARAM_ERROR,
            param_value=value,
            api_key=api_key,
        )
        upload.get_individual_parameter(asset_data["id"], param_name=PARAM_ERROR, api_key=api_key)
        logger.info("Patched %s='%s' for asset %s", PARAM_ERROR, value, asset_data.get("id"))
    except Exception:
        logger.exception("Failed to patch error parameter for asset %s", asset_data.get("id"))
    return False


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
    binary_path: str = "",
    target_format: str = "",
) -> None:
    """Iterate over assets and generate Godot GLTF outputs for each.

    Args:
        assets: A list of asset dictionaries to process.
        api_key: API key for authenticated server operations.
        binary_path: Absolute path to the Blender executable for background tasks.
        target_format: The target export format, e.g., 'gltf' or 'gltf_godot'.
    """
    concurrency.run_asset_threads(
        assets,
        worker=generate_gltf,
        worker_kwargs={
            "api_key": api_key,
            "binary_path": binary_path,
            "target_format": target_format,
        },
        asset_arg_position=0,
        max_concurrency=2,
        logger=logger,
    )


def main() -> None:
    """Entry point for running the Godot GLTF generation workflow.

    Reads configuration from environment variables, searches for assets, and
    triggers generation for each asset.
    """
    asset_base_id = config.ASSET_BASE_ID

    if asset_base_id is not None:  # Single asset handling - for asset validation hook
        params = {
            "asset_base_id": asset_base_id,
            "asset_type": "model",
        }
    else:  # None asset specified - will run on 100 unprocessed assets - for nightly jobs
        params = {
            "asset_type": "model",
            "order": "-created",
            "verification_status": "validated",
            # Assets which do not have generated GLTF for Godot
            f"{PARAM_SUCCESS}_isnull": True,
            # Assets without an error from a previously failed Godot GLTF generation
            f"{PARAM_ERROR}_isnull": True,
        }

    assets = search.get_search_paginated(
        params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Found %s assets for GLTF (Godot) conversion", len(assets))
    for i, asset in enumerate(assets):
        logger.info("%s %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"))

    iterate_assets(
        assets,
        api_key=config.BLENDERKIT_API_KEY,
        binary_path=config.BLENDER_PATH,
        target_format=TARGET_FORMAT,
    )


if __name__ == "__main__":
    main()
