"""A script to generate GLTF files for models that do not have them yet.

It downloads the asset, starts a background Blender process to generate the GLTF file,
and uploads the result.
"""

import datetime
import json
import os
import tempfile

from blenderkit_server_utils import download, search, send_to_bg, upload

results = []
page_size = 100


def generate_gltf(asset_data: dict, api_key: str, binary_path: str) -> bool:
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
        target_format="gltf",
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
    elif len(files) > 0:
        # there are no actual resolutions
        print("Files are:", files)
        upload.upload_resolutions(files, asset_data, api_key=api_key)
        today = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        param = "gltfGeneratedDate"
        upload.patch_individual_parameter(asset_data["id"], param_name=param, param_value=today, api_key=api_key)
        upload.get_individual_parameter(asset_data["id"], param_name=param, api_key=api_key)
        print(f"---> Asset parameter {param} successfully patched with value {today}")
        # TODO: Remove gltfGeneratedError if it was filled by previous runs
        return True
    else:
        error += f" len(files)={len(files)}"

    print("---> GLTF generation failed")
    param = "gltfGeneratedError"
    value = error.strip()
    upload.patch_individual_parameter(asset_data["id"], param_name=param, param_value=value, api_key=api_key)
    upload.get_individual_parameter(asset_data["id"], param_name=param, api_key=api_key)
    print(f"--> Asset parameter {param} patched with value {value} to signal GLTF generation FAILURE")

    return False


def iterate_assets(assets: list[dict], api_key: str = "", binary_path: str = ""):
    """Iterate assets and run GLTF generation for each.

    Args:
        assets: List of asset metadata dicts.
        api_key: BlenderKit API key forwarded to generation.
        binary_path: Path to the Blender binary for background export.

    Returns:
        None
    """
    for i, asset_data in enumerate(assets):
        print(f"\n\n=== {i + 1} downloading and generating GLTF files for {asset_data['name']}")
        if asset_data is None:
            print("---> skipping, asset_data are None")
            continue
        ok = generate_gltf(asset_data, api_key, binary_path=binary_path)
        if ok:
            print("===> GLTF SUCCESS")
        else:
            print("===> GLTF FAILED")


def main():
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
