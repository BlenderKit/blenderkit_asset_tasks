"""Generate material validation renders for uploaded BlenderKit assets.

This module downloads material assets, renders validation images/turnarounds in
background Blender, and uploads results to Cloudflare storage.
"""

from __future__ import annotations

import logging
import os
import pathlib
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

from blenderkit_server_utils import download, paths, search, send_to_bg
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage

logger = logging.getLogger(__name__)

# Constants
MAX_ASSETS: int = int(os.environ.get("MAX_ASSET_COUNT", "100"))
MAX_THREADS: int = int(os.environ.get("MAX_VALIDATION_THREADS", "12"))
BUCKET_VALIDATION: str = "validation-renders"

# Configure basic logging only if root has no handlers (script usage)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


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


def render_material_validation_thread(asset_data: dict[str, Any], api_key: str) -> None:
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
    f_exists = cloudflare_storage.folder_exists(BUCKET_VALIDATION, upload_id)
    if f_exists:
        logger.info("Validation already exists for upload %s; skipping.", upload_id)
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
    # result_path should be inside result_folder; avoid duplicating temp_folder
    result_path = os.path.join(result_folder, result_file_name)

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
    # Use existing turnaround blend file from repository
    template_file_path = os.path.join(current_dir, "blend_files", "material_turnaround.blend")

    result_path = os.path.join(result_folder, upload_id + "_turnaround.mkv")

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
    try:
        cloudflare_storage.upload_folder(result_folder, BUCKET_VALIDATION, upload_id)
        logger.info("Uploaded validation renders for upload %s", upload_id)
    except Exception:
        # Underlying client already logs; keep a top-level trace for the thread
        logger.exception("Failed to upload validation renders for upload %s", upload_id)
    return


def iterate_assets(
    filepath: str,
    thread_function: Callable[[dict[str, Any], str], None] | None = None,
    process_count: int = MAX_THREADS,
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
    if thread_function is None:
        thread_function = render_material_validation_thread

    threads: list[threading.Thread] = []
    for asset_data in assets:
        if not asset_data:
            logger.warning("Skipping empty asset entry")
            continue
        logger.info("Queueing validation render for %s", asset_data.get("name"))
        thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
        thread.start()
        threads.append(thread)
        # throttle by max concurrent threads
        while len([t for t in threads if t.is_alive()]) >= process_count:
            # prune finished threads periodically
            threads = [t for t in threads if t.is_alive()]
            time.sleep(0.1)

    # Wait for remaining threads to finish
    for t in threads:
        t.join()


def main() -> None:
    """Fetch assets and run material validation renders."""
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_resolutions.json")
    params = {
        "order": "created",
        "asset_type": "material",
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
    logger.info("Assets to be processed: %s", len(assets))
    for a in assets:
        logger.debug("%s ||| %s", a.get("name"), a.get("assetType"))

    iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_material_validation_thread)


if __name__ == "__main__":
    main()
