"""Generate GLTF files tailored for the Godot engine.

This variant disables Draco compression and updates a dedicated parameter on
the asset to indicate success or failure of the Godot-optimized export.
"""

import datetime
import json
import os
import tempfile
from typing import Any

from blenderkit_server_utils import download, search, send_to_bg, upload

results = []
page_size = 100


def generate_gltf(asset_data: dict[str, Any], api_key: str, binary_path: str) -> bool:
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

    Returns:
        True when the GLTF was generated and uploaded successfully; False otherwise.
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
        print(f"Asset file not found on path {asset_file_path}")
        # fail message?
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
        target_format="gltf_godot",
    )

    files = None
    try:
        with open(result_path, encoding="utf-8") as f:
            files = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError) as e:
        print(f"---> Error reading result JSON {result_path}: {e}")
        error += f" {e}"

    if files is None:
        error += " Files are None"
    elif len(files) == 0:
        error += f" len(files)={len(files)}"
    else:
        # there are no actual resolutions
        print("Files are:", files)
        upload.upload_resolutions(files, asset_data, api_key=api_key)
        today = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        param = "gltfGodotGeneratedDate"
        upload.patch_individual_parameter(asset_data["id"], param_name=param, param_value=today, api_key=api_key)
        upload.get_individual_parameter(asset_data["id"], param_name=param, api_key=api_key)
    print(f"---> Asset parameter {param} successfully patched with value {today}")
    # Note: Remove gltfGodotGeneratedError if it was filled by previous runs
    return True

    print("---> GLTF generation failed")
    param = "gltfGodotGeneratedError"
    value = error.strip()
    upload.patch_individual_parameter(asset_data["id"], param_name=param, param_value=value, api_key=api_key)
    upload.get_individual_parameter(asset_data["id"], param_name=param, api_key=api_key)
    print(f"--> Asset parameter {param} patched with value {value} to signal GLTF generation FAILURE")

    return False


def iterate_assets(assets: list[dict[str, Any]], api_key: str = "", binary_path: str = "") -> None:
    """Iterate over assets and generate Godot GLTF outputs for each.

    Args:
        assets: A list of asset dictionaries to process.
        api_key: API key for authenticated server operations.
        binary_path: Absolute path to the Blender executable for background tasks.
    """
    for i, asset_data in enumerate(assets):
        print(f"\n\n=== {i + 1} downloading and generating GLTF files for {asset_data['name']}")
        if asset_data is None:
            print("---> skipping, asset_data are None")
            continue
        ok = generate_gltf(asset_data, api_key, binary_path=binary_path)
        if ok:
            print("===> GLTF GODOT SUCCESS")
        else:
            print("===> GLTF GODOT FAILED")


def main() -> None:
    """Entry point for running the Godot GLTF generation workflow.

    Reads configuration from environment variables, searches for assets, and
    triggers generation for each asset.

    Environment Variables:
        BLENDER_PATH: Absolute path to the Blender binary used for background processing.
        BLENDERKIT_API_KEY: API key used to access and modify assets.
        ASSET_BASE_ID: Optional ID to run against a single asset (validation hook mode).
        MAX_ASSET_COUNT: Upper bound of assets to process in one run (default 100).
    """
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
            # Assets which do not have generated GLTF
            "gltfGeneratedDate_isnull": True,
            # Assets without an error from a previously failed GLTF generation
            "gltfGeneratedError_isnull": True,
        }

    assets = search.get_search_without_bullshit(
        params,
        page_size=min(max_assets, 100),
        max_results=max_assets,
        api_key=api_key,
    )
    print(f"--- Found {len(assets)} for GLTF conversion: ---")
    for i, asset in enumerate(assets):
        print(f"{i + 1} {asset['name']} ||| {asset['assetType']}")

    iterate_assets(assets, api_key=api_key, binary_path=blender_path)


if __name__ == "__main__":
    main()
