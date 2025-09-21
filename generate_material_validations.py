"""Generate material validation renders for uploaded BlenderKit assets.

This module downloads material assets, renders validation images/turnarounds in
background Blender, and uploads results to Cloudflare storage.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Any

from blenderkit_server_utils import concurrency, config, download, log, search, send_to_bg, utils
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage

logger = log.create_logger(__name__)


utils.raise_on_missing_env_vars([
    "BLENDERKIT_API_KEY",
    "BLENDERS_PATH",
    "CF_ACCESS_KEY",
    "CF_ACCESS_SECRET",
    "CF_ENDPOINT_URL",
])


# Constants
BUCKET_VALIDATION: str = "validation-renders"


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
    # skip empty assets
    if not asset_data or not asset_data.get("files"):
        logger.warning("Skipping empty or invalid asset entry")
        return

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
    assets: list[dict[str, Any]],
    process_count: int = config.MAX_VALIDATION_THREADS,
    api_key: str = "",
) -> None:
    """Iterate assets and dispatch validation rendering threads.

    Args:
        assets: List of asset dictionaries to process.
        process_count: Maximum number of concurrent threads.
        api_key: BlenderKit API key forwarded to the thread function.

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=render_material_validation_thread,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=process_count,
        logger=logger,
    )


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
        page_size=min(config.MAX_ASSET_COUNT, 100),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )

    assets = search.load_assets_list(filepath)
    logger.info("Assets to be processed: %s", len(assets))
    for a in assets:
        logger.debug("%s ||| %s", a.get("name"), a.get("assetType"))

    iterate_assets(assets, process_count=1, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
