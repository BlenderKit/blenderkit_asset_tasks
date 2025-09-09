"""Generate model validation renders for uploaded BlenderKit assets.

This script downloads model assets, renders validation videos/images via a
background Blender process, and uploads the results to Cloudflare R2 storage.

It can process assets in parallel using threads and includes helpers to fetch
asset lists and orchestrate per-asset rendering.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import threading
import time

from blenderkit_server_utils import download, paths, search, send_to_bg

# Assuming necessary imports are done at the top of the script
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage

MAX_ASSETS = int(os.environ.get("MAX_ASSET_COUNT", "100"))


def cloudflare_setup() -> CloudflareStorage:
    """Setup Cloudflare Storage bucket if needed.

    Returns:
         CloudflareStorage: Configured CloudflareStorage instance.
    """
    # Initialize Cloudflare Storage with your credentials
    cloudflare_storage = CloudflareStorage(
        access_key=os.getenv("CF_ACCESS_KEY"),
        secret_key=os.getenv("CF_ACCESS_SECRET"),
        endpoint_url=os.getenv("CF_ENDPOINT_URL"),
    )
    return cloudflare_storage


def cloudflare_validate_empty_folder(item_id: str, cloudflare_storage: CloudflareStorage) -> bool:
    """Check and optionally purge a Cloudflare folder; signal whether to skip.

    If the folder exists and contains only the "index.json" file, the folder is
    purged and processing can continue. If the folder exists and contains other
    files, the function returns True to signal the caller should skip further
    processing for this item.

    Args:
        item_id: The ID (folder name) to check in the bucket.
        cloudflare_storage: Configured Cloudflare storage client.

    Returns:
        True if the folder exists and is not empty (skip processing), otherwise
        False.
    """
    f_exists = cloudflare_storage.folder_exists("validation-renders", item_id)
    # let's not skip now.
    if f_exists:
        # check if the result folder is empty only with index.json, if yes, purge it and continue. Otherwise skip
        files = cloudflare_storage.list_folder_contents("validation-renders", item_id)

        if len(files) == 1 and files[0] == "index.json":
            # purge the folder
            cloudflare_storage.delete_folder_contents("validation-renders", item_id)
            print(f"Purged the folder: {item_id}")
        else:
            print(f"directory {item_id} exists, skipping")
            return True
    return False


def render_model_validation_thread(asset_data: dict, api_key: str) -> None:
    """Worker to validate a single model asset.

    The worker performs the following steps:
    1. Download the asset archive.
    2. Unpack and render validation media via background Blender.
    3. Generate a GLB via Blender (gltf export) and move results to a temp folder.
    4. Upload the resulting files to Cloudflare R2 under the asset upload ID.

    Args:
        asset_data: Asset metadata dict returned by search API (must contain
            keys like "files", "name", "assetBaseId").
        api_key: BlenderKit API key for authenticated download.

    Returns:
        None
    """
    destination_directory = tempfile.gettempdir()
    if len(asset_data["files"]) == 0:
        print(f"no files for asset {asset_data['name']}")
        return
    upload_id = asset_data["files"][0]["downloadUrl"].split("/")[-2]

    # Check if the asset has already been processed
    # stop using author folder
    result_file_name = f"{upload_id}"
    predicted_filename = f"{result_file_name}.mkv"  # let's try to super simplify now.

    # check if the directory exists on the drive
    # we check file by file, since the comparison with folder contents is not reliable and would potentially
    # compare with a very long list. main issue was what to set the page size for the search request...
    # Initialize Cloudflare Storage with your credentials

    cloudflare_storage = cloudflare_setup()

    if cloudflare_validate_empty_folder(upload_id, cloudflare_storage):
        return

    f_exists = cloudflare_storage.folder_exists("validation-renders", upload_id)
    # let's not skip now.
    if f_exists:
        # check if the result folder is empty only with index.json, if yes, purge it and continue. Otherwise skip
        files = cloudflare_storage.list_folder_contents("validation-renders", upload_id)

        if len(files) == 1 and files[0] == "index.json":
            # purge the folder
            cloudflare_storage.delete_folder_contents("validation-renders", upload_id)
            print(f"Purged the folder: {upload_id}")
        else:
            print(f"directory {upload_id} exists, skipping")
            return

    # Download asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # Unpack asset
    send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path, script="unpack_asset_bg.py")

    # find template file
    current_dir = pathlib.Path(__file__).parent.resolve()
    template_file_path = os.path.join(current_dir, "blend_files", "model_validation_static_renders.blend")

    # Send to background to generate resolutions
    # generated temp folder
    # .blend gets resaved there and also /tmp renders of images
    temp_folder = tempfile.mkdtemp()

    # result folder where the stuff for upload to drive goes
    result_folder = os.path.join(temp_folder, upload_id)
    os.makedirs(result_folder, exist_ok=True)

    # local file path of rendered image
    result_path = os.path.join(temp_folder, result_folder, predicted_filename)

    # send to background to render
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        template_file_path=template_file_path,
        result_path=result_path,
        result_folder=result_folder,
        temp_folder=temp_folder,
        script="model_validation_bg_render.py",
        binary_type="NEWEST",
        verbosity_level=2,
    )

    # generate gltf:
    # result is a json...
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")

    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        result_path=result_path,
        script="gltf_bg_blender.py",
    )

    # gltf is a .glb in the same dir as the .blend asset file
    gltf_path = asset_file_path.replace(".blend", ".glb")
    # move gltf to result folder
    try:
        shutil.move(gltf_path, result_folder)
    except (FileNotFoundError, PermissionError, shutil.Error, OSError) as e:
        print(f"Error while moving {gltf_path} to {result_folder}: {e}")

    # part of the results is in temfolder/tmp/Render, so let's move all of it's files to the result folder,
    # so that there are no subdirectories and everything is in one folder.
    # and then upload the result folder to drive

    render_folder = os.path.join(temp_folder, "tmp", "Render")
    try:
        file_names = os.listdir(render_folder)
        for file_name in file_names:
            shutil.move(os.path.join(render_folder, file_name), result_folder)
    except (FileNotFoundError, NotADirectoryError, PermissionError, shutil.Error, OSError) as e:
        print(f"Error while moving files from {render_folder} to {result_folder}: {e}")

    # Upload result
    # # Instead of using Google Drive for upload, use Cloudflare Storage
    # Initialize the CloudFlare service
    cloudflare_storage = cloudflare_setup()
    cloudflare_storage.upload_folder(
        result_folder,
        bucket_name="validation-renders",
        cloudflare_folder_prefix=result_file_name,
    )

    # cleanup
    try:
        shutil.rmtree(temp_folder)
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(f"Error while deleting temp folder {temp_folder}: {e}")

    return


def iterate_assets(
    filepath: str,
    thread_function: callable[[dict, str], None] | None = None,
    process_count: int = 12,
    api_key: str = "",
) -> None:
    """Iterate assets and dispatch validation render threads.

    Args:
        filepath: Path to JSON file with the asset list (created by search helper).
        thread_function: Callable to execute per-asset. Defaults to
            :func:`render_model_validation_thread`.
        process_count: Maximum number of concurrent threads.
        api_key: BlenderKit API key passed to the thread function.
    """
    if thread_function is None:
        thread_function = render_model_validation_thread
    assets = search.load_assets_list(filepath)
    threads = []
    for asset_data in assets:
        if asset_data is not None:
            print(f"downloading and generating validation render for  {asset_data['name']}")
            thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
            thread.start()
            threads.append(thread)
            while len(threads) > process_count - 1:
                for t in threads:
                    if not t.is_alive():
                        threads.remove(t)
                    break
                time.sleep(0.1)  # wait for a bit to finish all threads


def cloudflare_cleanup() -> None:
    """Cleanup old files from Cloudflare Storage.

    Removes files older than a configured threshold and recent temp files to
    keep the bucket tidy.

    Returns:
        None
    """
    # Initialize Cloudflare Storage with your credentials
    cloudflare_storage = cloudflare_setup()
    print("deleting old files")
    cloudflare_storage.delete_old_files(bucket_name="validation-renders", x_days=30)
    cloudflare_storage.delete_new_files(bucket_name="validation-renders", x_days=30)


def main() -> None:
    """Entry point: fetch assets and render model validations."""
    # cleanup the drive folder
    if os.getenv("CLOUDFLARE_CLEANUP", "0") == "1":
        cloudflare_cleanup()
        return

    # Get os temp directory
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_validation.json")
    params = {
        "order": "-last_blend_upload",
        "asset_type": "model",
        "verification_status": "uploaded",
    }
    search.get_search_simple(
        params,
        filepath=filepath,
        page_size=min(MAX_ASSETS, 100),
        max_results=MAX_ASSETS,
        api_key=paths.API_KEY,
    )

    assets = search.load_assets_list(filepath)
    print("ASSETS TO BE PROCESSED")
    for a in assets:
        print(a["name"], a["assetType"])

    iterate_assets(
        filepath,
        process_count=1,
        api_key=paths.API_KEY,
        thread_function=render_model_validation_thread,
    )


if __name__ == "__main__":
    main()
