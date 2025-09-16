"""Generate GLTF files for models that do not have them yet.

This script downloads the asset, starts a background Blender process to
generate the GLTF file, and uploads the result. It also patches success or
error parameters on the asset for traceability.
"""

import datetime
import json
import logging
import os
import tempfile
from typing import Any

from blenderkit_server_utils import download, search, send_to_bg, upload

logger = logging.getLogger(__name__)

# Constants
PAGE_SIZE_LIMIT: int = 100
PARAM_SUCCESS: str = "gltfGeneratedDate"
PARAM_ERROR: str = "gltfGeneratedError"

# Configure basic logging only if root has no handlers (script usage)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def generate_gltf(asset_data: dict[str, Any], api_key: str, binary_path: str) -> bool:
    """Generate GLTF for a single asset via background Blender and upload it.

    Steps:
    1. Download the asset file (.blend).
    2. Run background Blender to export GLTF and produce a result JSON.
    3. Upload generated files and patch asset parameters accordingly.

    Args:
        asset_data: Asset metadata dict (expects keys like "id", "assetBaseId", "name", "files").
        api_key: BlenderKit API key used for authenticated API operations.
        binary_path: Path to the Blender binary to use for background operations.

    Returns:
        bool: True if GLTF generation and upload succeeded; False otherwise.
    """
    error = ""
    destination_directory = tempfile.gettempdir()

    # Download asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # Unpack asset
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        script="unpack_asset_bg.py",
        binary_path=binary_path,
    )

    if not asset_file_path:
        logger.error("Asset file not found on path %s", asset_file_path)
        return False

    # Send to background to generate GLTF
    temp_folder = tempfile.mkdtemp()
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")

    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        result_path=result_path,
        script="gltf_bg_blender.py",
        binary_path=binary_path,
        target_format="gltf",
    )

    files: list[dict[str, Any]] | None = None
    try:
        with open(result_path, encoding="utf-8") as f:
            files = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError) as exc:
        logger.exception("Error reading result JSON %s", result_path)
        error += f" {exc}"

    if files is None:
        error += " Files are None"
    elif len(files) > 0:
        logger.info("Generated files: %s", files)
        try:
            upload.upload_resolutions(files, asset_data, api_key=api_key)
            today = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")
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
        except Exception:
            logger.exception(
                "Failed to upload resolutions or patch success parameter for asset %s",
                asset_data.get("id"),
            )
            error += " upload/patch failed"
        else:
            # Success path
            return True
    else:
        error += f" len(files)={len(files)}"

    logger.error("GLTF generation failed for asset %s: %s", asset_data.get("id"), error.strip())
    value = error.strip()
    try:
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


def iterate_assets(assets: list[dict[str, Any]], api_key: str = "", binary_path: str = "") -> None:
    """Iterate assets and run GLTF generation for each.

    Args:
        assets: List of asset metadata dicts.
        api_key: BlenderKit API key forwarded to generation.
        binary_path: Path to the Blender binary for background export.

    Returns:
        None
    """
    for i, asset_data in enumerate(assets):
        logger.info("=== %s/%s generating GLTF for %s", i + 1, len(assets), asset_data.get("name"))
        if not asset_data:
            logger.warning("Skipping empty asset entry at index %s", i)
            continue
        ok = generate_gltf(asset_data, api_key, binary_path=binary_path)
        if ok:
            logger.info("===> GLTF SUCCESS for %s", asset_data.get("id"))
        else:
            logger.warning("===> GLTF FAILED for %s", asset_data.get("id"))


def main() -> None:
    """Entry point to fetch assets and generate GLTFs where missing."""
    blender_path = os.environ.get("BLENDER_PATH", "")
    api_key = os.environ.get("BLENDERKIT_API_KEY", "")
    asset_base_id = os.environ.get("ASSET_BASE_ID")
    max_assets = int(os.environ.get("MAX_ASSET_COUNT", "100"))

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
            # Assets which does not have generated GLTF
            "gltfGeneratedDate_isnull": True,
            # Assets which does not have error from previously failed GLTF generation
            "gltfGeneratedError_isnull": True,
        }

    assets = search.get_search_without_bullshit(
        params,
        page_size=min(max_assets, PAGE_SIZE_LIMIT),
        max_results=max_assets,
        api_key=api_key,
    )
    logger.info("Found %s assets for GLTF conversion", len(assets))
    for i, asset in enumerate(assets):
        logger.debug("%s %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"))

    iterate_assets(assets, api_key=api_key, binary_path=blender_path)


if __name__ == "__main__":
    main()
