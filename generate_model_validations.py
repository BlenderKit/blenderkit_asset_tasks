"""Generate model validation renders for uploaded BlenderKit assets.

This script downloads model assets, renders validation videos/images via a
background Blender process, and uploads the results to Cloudflare R2 storage.

It can process assets in parallel using threads and includes helpers to fetch
asset lists and orchestrate per-asset rendering.
"""

from __future__ import annotations

import logging
import os
import pathlib
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from blenderkit_server_utils import download, paths, search, send_to_bg

# Assuming necessary imports are done at the top of the script
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage

logger = logging.getLogger(__name__)

# Constants
MAX_ASSETS: int = int(os.environ.get("MAX_ASSET_COUNT", "100"))
MAX_THREADS: int = int(os.environ.get("MAX_VALIDATION_THREADS", "8"))
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
    f_exists = cloudflare_storage.folder_exists(BUCKET_VALIDATION, item_id)
    # let's not skip now.
    if f_exists:
        # check if the result folder is empty only with index.json, if yes, purge it and continue. Otherwise skip
        files = cloudflare_storage.list_folder_contents(BUCKET_VALIDATION, item_id)

        # Files are dicts from S3 list_objects_v2; expect Keys like '<prefix>/index.json'
        if len(files) == 1 and str(files[0].get("Key", "")).endswith("/index.json"):
            try:
                cloudflare_storage.delete_folder_contents(BUCKET_VALIDATION, item_id)
                logger.info("Purged the folder: %s", item_id)
            except Exception:
                logger.exception("Failed to purge folder for %s", item_id)
        else:
            logger.info("Directory %s exists with content; skipping", item_id)
            return True
    return False


def _extract_upload_id(asset_data: dict[str, Any]) -> str | None:
    """Extract the upload ID from asset data.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        The upload ID string or None if it cannot be extracted.
    """
    try:
        files = asset_data.get("files", [])
        if not files:
            return None
        download_url: str = files[0]["downloadUrl"]
        upload_id = download_url.split("/")[-2]
    except Exception:  # narrow exceptions are not practical here due to varied asset shapes
        logger.exception("Failed to extract upload_id for asset %s", asset_data.get("name"))
        return None
    else:
        return upload_id


def _get_template_path() -> str:
    """Return absolute path to the validation template blend file."""
    current_dir = pathlib.Path(__file__).parent.resolve()
    template_file_path = os.path.join(current_dir, "blend_files", "model_validation_static_renders.blend")
    return template_file_path


def _prepare_paths(upload_id: str) -> tuple[str, str, str]:
    """Create a temp folder and result paths for processing.

    Args:
        upload_id: The Cloudflare folder name (asset upload ID).

    Returns:
        A tuple of (temp_folder, result_folder, result_path).
    """
    temp_folder = tempfile.mkdtemp()
    result_folder = os.path.join(temp_folder, upload_id)
    os.makedirs(result_folder, exist_ok=True)
    predicted_filename = f"{upload_id}.mkv"
    result_path = os.path.join(result_folder, predicted_filename)
    return temp_folder, result_folder, result_path


def _run_unpack(asset_data: dict[str, Any], asset_file_path: str) -> None:
    """Unpack the asset in a background Blender process."""
    send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path, script="unpack_asset_bg.py")


@dataclass
class RenderContext:
    """Context for a single validation render job."""

    asset_file_path: str
    template_file_path: str
    result_path: str
    result_folder: str
    temp_folder: str


def _run_validation_render(asset_data: dict[str, Any], ctx: RenderContext) -> None:
    """Run the model validation render via background Blender."""
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=ctx.asset_file_path,
        template_file_path=ctx.template_file_path,
        result_path=ctx.result_path,
        result_folder=ctx.result_folder,
        temp_folder=ctx.temp_folder,
        script="model_validation_bg_render.py",
        binary_type="NEWEST",
        verbosity_level=2,
    )


def _run_gltf_export(asset_data: dict[str, Any], asset_file_path: str, temp_folder: str) -> str:
    """Export GLTF/GLB via background Blender and return path to the GLB file."""
    result_path = os.path.join(temp_folder, asset_data["assetBaseId"] + "_resdata.json")
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        result_path=result_path,
        script="gltf_bg_blender.py",
    )
    gltf_path = asset_file_path.replace(".blend", ".glb")
    return gltf_path


def _move_file_safely(src_path: str, dst_dir: str) -> None:
    """Move a file to a directory, logging common exceptions."""
    try:
        shutil.move(src_path, dst_dir)
    except (FileNotFoundError, PermissionError, shutil.Error, OSError):
        logger.exception("Error while moving %s to %s", src_path, dst_dir)


def _collect_render_outputs(temp_folder: str, result_folder: str) -> None:
    """Move all files from temp/tmp/Render into the result folder."""
    render_folder = os.path.join(temp_folder, "tmp", "Render")
    try:
        file_names = os.listdir(render_folder)
        for file_name in file_names:
            _move_file_safely(os.path.join(render_folder, file_name), result_folder)
    except (FileNotFoundError, NotADirectoryError, PermissionError, shutil.Error, OSError):
        logger.exception("Error while moving files from %s to %s", render_folder, result_folder)


def _upload_results(
    cloudflare_storage: CloudflareStorage,
    result_folder: str,
    result_file_name: str,
    upload_id: str,
) -> None:
    """Upload the result folder to Cloudflare and log outcome."""
    try:
        cloudflare_storage.upload_folder(
            result_folder,
            bucket_name=BUCKET_VALIDATION,
            cloudflare_folder_prefix=result_file_name,
        )
        logger.info("Uploaded validation folder for %s", upload_id)
    except Exception:
        logger.exception("Failed to upload validation folder for %s", upload_id)


def _cleanup_temp(temp_folder: str) -> None:
    """Delete the temporary working folder if possible."""
    try:
        shutil.rmtree(temp_folder)
    except (FileNotFoundError, PermissionError, OSError):
        logger.exception("Error while deleting temp folder %s", temp_folder)


def render_model_validation_thread(asset_data: dict[str, Any], api_key: str) -> None:
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
    upload_id = _extract_upload_id(asset_data)
    if not upload_id:
        logger.warning("No files for asset %s", asset_data.get("name"))
        return

    result_file_name = upload_id
    cloudflare_storage = cloudflare_setup()

    # Skip if the Cloudflare folder exists with content; purge if only index.json
    if cloudflare_validate_empty_folder(upload_id, cloudflare_storage):
        return

    # Download and unpack asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)
    _run_unpack(asset_data, asset_file_path)

    # Prepare rendering
    template_file_path = _get_template_path()
    temp_folder, result_folder, result_path = _prepare_paths(upload_id)
    ctx = RenderContext(
        asset_file_path=asset_file_path,
        template_file_path=template_file_path,
        result_path=result_path,
        result_folder=result_folder,
        temp_folder=temp_folder,
    )

    # Render validation media
    _run_validation_render(asset_data, ctx)

    # Export GLB and collect outputs
    gltf_path = _run_gltf_export(asset_data, asset_file_path, temp_folder)
    _move_file_safely(gltf_path, ctx.result_folder)
    _collect_render_outputs(ctx.temp_folder, ctx.result_folder)

    # Upload results and cleanup
    _upload_results(cloudflare_storage, ctx.result_folder, result_file_name, upload_id)
    _cleanup_temp(ctx.temp_folder)
    return


def iterate_assets(
    filepath: str,
    thread_function: Callable[[dict[str, Any], str], None] | None = None,
    process_count: int = MAX_THREADS,
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
    threads: list[threading.Thread] = []
    for asset_data in assets:
        if not asset_data:
            logger.warning("Skipping empty asset entry")
            continue
        logger.info("Queueing model validation for %s", asset_data.get("name"))
        thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
        thread.start()
        threads.append(thread)
        # throttle by max concurrent threads
        while len([t for t in threads if t.is_alive()]) >= process_count:
            # prune finished threads periodically
            threads = [t for t in threads if t.is_alive()]
            time.sleep(0.1)

    # Wait for all threads to finish
    for t in threads:
        t.join()


def cloudflare_cleanup() -> None:
    """Cleanup old files from Cloudflare Storage.

    Removes files older than a configured threshold and recent temp files to
    keep the bucket tidy.

    Returns:
        None
    """
    # Initialize Cloudflare Storage with your credentials
    cloudflare_storage = cloudflare_setup()
    logger.info("Deleting old files in validation bucket")
    cloudflare_storage.delete_old_files(bucket_name=BUCKET_VALIDATION, x_days=30)


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
    logger.info("Assets to be processed: %s", len(assets))
    for a in assets:
        logger.debug("%s ||| %s", a.get("name"), a.get("assetType"))

    iterate_assets(
        filepath,
        process_count=1,
        api_key=paths.API_KEY,
        thread_function=render_model_validation_thread,
    )


if __name__ == "__main__":
    main()
