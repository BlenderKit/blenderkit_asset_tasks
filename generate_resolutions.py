"""Script to generate resolutions for assets that don't have them yet.

Required environment variables:
BLENDERKIT_API_KEY - API key to be used
BLENDERS_PATH - path to the folder with multiple blender versions
or BLENDER_PATH - to one executable, this will force this one version

For single asset processing, set ASSET_BASE_ID to the asset_base_id.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time

from blenderkit_server_utils import download, paths, search, send_to_bg, upload

results = []
page_size = 100

ASSET_BASE_ID = os.environ.get("ASSET_BASE_ID", None)
MAX_ASSETS = int(os.environ.get("MAX_ASSET_COUNT", "100"))
SKIP_UPLOAD = os.environ.get("SKIP_UPLOAD", False) == "True"  # noqa: PLW1508


def generate_resolution_thread(asset_data: dict, api_key: str) -> None:
    """Thread to generate resolutions for a single asset.

    A thread that:
     1.downloads file
     2.starts an instance of Blender that generates the resolutions
     3.uploads files that were prepared
     4.patches asset data with a new parameter.

    Args:
        asset_data (dict): Asset data dictionary.
        api_key (str): API key for authentication.

    Returns:
        None
    """
    # data gets saved into the default temp directory
    destination_directory = tempfile.gettempdir()
    blender_binary_path = os.environ.get("BLENDER_PATH", "")

    # Download asset into temp directory
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # Unpack asset
    if asset_file_path and asset_data["assetType"] != "hdr":
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            script="unpack_asset_bg.py",
            binary_path=blender_binary_path,
        )

    if not asset_file_path:
        # this could probably happen when wrong api_key with wrong plan was submitted,
        # or e.g. a private asset was submitted.
        # fail message?
        return

    # Send to background to generate resolutions
    temp_folder = tempfile.mkdtemp()
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")

    if asset_data["assetType"] == "hdr":
        # >asset_file_path = 'empty.blend'
        # HDRs have a different script, and are open inside an empty blend file.
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

    files = None
    try:
        with open(result_path, encoding="utf-8") as f:
            files = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(e)

    if SKIP_UPLOAD:
        print("----- SKIP_UPLOAD==True -> skipping upload -----")
        sys.exit()
    if files is None:
        # this means error
        result_state = "error"
    elif len(files) > 0:
        # there are no actual resolutions
        upload.upload_resolutions(files, asset_data, api_key=api_key)
        result_state = "success"
    else:
        # zero files, consider skipped
        result_state = "skipped"
    print(f"result state: {result_state}")
    print("changing asset variable")
    resgen_param = {"resolutionsGenerated": result_state}

    today = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
    upload.patch_asset_empty(asset_data["assetBaseId"], api_key=api_key)

    # delete the temp folder
    try:
        shutil.rmtree(temp_folder)
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(f"Error while deleting temp folder {temp_folder}: {e}")
    try:
        os.remove(asset_file_path)
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(f"Error while deleting asset file {asset_file_path}: {e}")
    print("deleted temp folder")

    # return,no param patching for now - no need for it by now?
    return

    upload.patch_individual_parameter(
        asset_data["id"],
        param_name=resgen_param,
        api_key=api_key,
    )
    upload.patch_individual_parameter(
        asset_data["id"],
        param_name="resolutionsGeneratedDate",
        param_value=today,
        api_key=api_key,
    )


def iterate_assets(
    filepath: str,
    thread_function: callable | None = None,
    process_count: int = 12,
    api_key: str = "",
) -> None:
    """Iterate through all assigned assets, check for those which need generation and send them to res gen.

    Args:
        filepath (str): Path to the JSON file with asset data.
        thread_function (callable, optional): Function to process each asset in a thread.
        process_count (int, optional): Number of concurrent processes to run.
        api_key (str, optional): API key for authentication.

    Returns:
        None
    """
    assets = search.load_assets_list(filepath)
    threads = []
    for asset_data in assets:
        if asset_data is not None:
            print(f"downloading and generating resolution for  {asset_data['name']}")
            thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
            thread.start()
            threads.append(thread)
            while len(threads) > process_count - 1:
                for t in threads:
                    if not t.is_alive():
                        threads.remove(t)
                    break
                time.sleep(0.1)  # wait for a bit to finish all threads


def main():
    """Main function to generate resolutions for assets."""
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_resolutions.json")
    # search for assets if assets were not provided with these parameters

    # this selects specific assets
    # only material, model and hdr are supported currently. We can do scenes in future potentially
    # only validated public assets are processed
    # only files from a certain size are processed (over 1 MB)
    # TODO: Fix the parameter last_resolution_upload - currently searches for assets that were never processed,
    # TODO: but we need to process all updated assets too, and write a specific parameter too
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
    if ASSET_BASE_ID is not None:
        params = {
            "asset_base_id": ASSET_BASE_ID,
        }

    assets = search.get_search_simple(
        params,
        filepath,
        page_size=min(MAX_ASSETS, 100),
        max_results=MAX_ASSETS,
        api_key=paths.API_KEY,
    )

    print("COUNT OF ASSETS TO BE PROCESSED ", len(assets))
    for a in assets:
        print(a["name"], a["assetType"])

    iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=generate_resolution_thread)


if __name__ == "__main__":
    main()
