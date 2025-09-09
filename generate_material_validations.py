"""Generate material validation renders for uploaded BlenderKit assets.

This module downloads material assets, renders validation images/turnarounds in
background Blender, and uploads results to Cloudflare storage.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import threading
import time

from blenderkit_server_utils import download, paths, search, send_to_bg
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage

results = []
page_size = 100

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


def render_material_validation_thread(asset_data: dict, api_key: str) -> None:
    """Validate a single material asset in a background Blender process.

    Steps:
    1. Download the asset .blend file.
    2. Render validation image and turnaround.
    3. Upload results to Cloudflare under the asset's upload ID.

    Args:
        asset_data: Asset metadata (expects keys like "files", "name").
        api_key: BlenderKit API key for authenticated download.

    Returns:
        None
    """
    destination_directory = tempfile.gettempdir()

    upload_id = asset_data["files"][0]["downloadUrl"].split("/")[-2]

    # Check if the asset has already been processed
    # stop using author folder
    # Check if the asset has already been processed
    result_file_name = f"Render{upload_id}.webp"

    # check if the directory exists on the drive
    # we check file by file, since the comparison with folder contents is not reliable and would potentially
    # compare with a very long list. main issue was what to set the page size for the search request...
    cloudflare_storage = cloudflare_setup()
    f_exists = cloudflare_storage.folder_exists("validation-renders", upload_id)
    if f_exists:
        print("file exists, skipping")
        return

    # Download asset
    file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # find template file
    current_dir = pathlib.Path(__file__).parent.resolve()
    template_file_path = os.path.join(current_dir, "blend_files", "material_validator_mix.blend")

    # Send to background to generate resolutions
    temp_folder = tempfile.mkdtemp()

    # result folder where the stuff for upload to drive goes
    result_folder = os.path.join(temp_folder, upload_id)
    os.makedirs(result_folder, exist_ok=True)

    # local file path of main rendered image
    result_path = os.path.join(temp_folder, result_folder, result_file_name)

    # send to background to render
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=file_path,
        template_file_path=template_file_path,
        result_path=result_path,
        result_folder=result_folder,
        temp_folder=temp_folder,
        script="material_validation_bg.py",
        binary_type="NEWEST",
        verbosity_level=2,
    )

    # send to background to render turnarounds
    template_file_path = os.path.join(current_dir, "blend_files", "material_turnaround_validation.blend")

    result_path = os.path.join(temp_folder, result_folder, upload_id + "_turnaround.mkv")

    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=file_path,
        template_file_path=template_file_path,
        result_path=result_path,
        result_folder=result_folder,
        temp_folder=temp_folder,
        script="material_turnaround_bg.py",
        binary_type="NEWEST",
        verbosity_level=2,
    )

    # Upload result
    cloudflare_storage = cloudflare_setup()
    cloudflare_storage.upload_folder(result_folder, "validation-renders", upload_id)
    return


def iterate_assets(
    filepath: str,
    thread_function: callable[[dict, str], None] | None = None,
    process_count: int = 12,
    api_key: str = "",
) -> None:
    """Iterate assets and dispatch validation rendering threads.

    Args:
        filepath: Path to JSON file with the asset list.
        thread_function: Callable executed per asset; defaults to
            ``render_material_validation_thread``.
        process_count: Maximum number of concurrent threads.
        api_key: BlenderKit API key forwarded to the thread function.

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
    """Fetch assets and run material validation renders."""
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_resolutions.json")
    params = {"order": "created", "asset_type": "material", "verification_status": "uploaded"}
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

    iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_material_validation_thread)


if __name__ == "__main__":
    main()
