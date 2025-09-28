"""Generate validation renders for BlenderKit assets (model/material).

Args:
- validation_mode: The mode to use for validation (default: "model").

Modes:
- model:   unpack, render validation media, export GLB, upload to Cloudflare
- material: unpack, render validation media, upload to Cloudflare

Select mode via env VALIDATION_MODE = "model" | "material" (default: model).
ASSET_BASE_ID limits processing to a single asset.

Required env:
- BLENDERKIT_API_KEY, BLENDERS_PATH, CF_ACCESS_KEY, CF_ACCESS_SECRET, CF_ENDPOINT_URL

Optional env:
- MAX_VALIDATION_THREADS: int (defaults to config.MAX_VALIDATION_THREADS)
- CLOUDFLARE_CLEANUP: "1" to purge all validation folders then exit
- (override BG scripts/templates if your names differ)
  MODEL_TEMPLATE:     blend_files/model_validation_static_renders.blend
  MODEL_BG_SCRIPT:    model_validation_bg_render.py
  MATERIAL_TEMPLATE:  blend_files/material_validation_static_renders.blend
  MATERIAL_BG_SCRIPT: material_validation_bg_render.py
- SKIP_UPDATE: "1" to skip uploading results (for testing)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import tempfile
from typing import Any

from blenderkit_server_utils import concurrency, config, download, log, search, send_to_bg, utils
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage, cloudflare_cleanup, cloudflare_setup

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(
    ["BLENDERKIT_API_KEY", "BLENDERS_PATH", "CF_ACCESS_KEY", "CF_ACCESS_SECRET", "CF_ENDPOINT_URL"],
)

# Config
_AVAILABLE_MODES = ("model", "material")

args = argparse.ArgumentParser()
args.add_argument("--validation_mode", type=str, default="model", help="Target validation mode 'model' or 'material'")


# Constants
VALIDATION_MODE = args.parse_args().validation_mode

if VALIDATION_MODE not in _AVAILABLE_MODES:
    logger.exception(
        "Invalid VALIDATION_MODE '%s', must be one of %s. Defaulting to 'model'.",
        VALIDATION_MODE,
        _AVAILABLE_MODES,
    )
    raise SystemExit(1)

BUCKET_VALIDATION: str = "validation-renders"

# Script/template overrides
THIS_DIR = pathlib.Path(__file__).parent.resolve()
DEFAULT_MODEL_TEMPLATE = THIS_DIR / "blend_files" / "model_validation_static_renders.blend"
DEFAULT_MATERIAL_TEMPLATE = THIS_DIR / "blend_files" / "material_validation_static_renders.blend"

MODEL_TEMPLATE = os.getenv("MODEL_TEMPLATE", str(DEFAULT_MODEL_TEMPLATE))
MATERIAL_TEMPLATE = os.getenv("MATERIAL_TEMPLATE", str(DEFAULT_MATERIAL_TEMPLATE))

MODEL_BG_SCRIPT = os.getenv("MODEL_BG_SCRIPT", "model_validation_bg_render.py")
MATERIAL_BG_SCRIPT = os.getenv("MATERIAL_BG_SCRIPT", "material_validation_bg_render.py")

SKIP_UPDATE: bool = config.SKIP_UPDATE


def cloudflare_validate_empty_folder(item_id: str, cloudflare_storage: CloudflareStorage) -> bool:
    """Return True to skip if folder exists with content; purge if only index.json."""
    f_exists = cloudflare_storage.folder_exists(BUCKET_VALIDATION, item_id)
    if f_exists:
        files = cloudflare_storage.list_folder_contents(BUCKET_VALIDATION, item_id)
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
    """Extract upload_id from the first file's downloadUrl."""
    try:
        files = asset_data.get("files", [])
        if not files:
            return None
        download_url: str = files[0]["downloadUrl"]
        upload_id = download_url.split("/")[-2]
    except Exception:
        logger.exception("Failed to extract upload_id for asset %s", asset_data.get("name"))
        return None
    else:
        return upload_id


def _prepare_paths(upload_id: str) -> tuple[str, str]:
    """Create temp folder and result folder for this upload."""
    temp_folder = tempfile.mkdtemp()
    result_folder = os.path.join(temp_folder, upload_id)
    os.makedirs(result_folder, exist_ok=True)
    return temp_folder, result_folder


def _move_file_safely(src_path: str, dst_dir: str) -> None:
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


def _download_and_unpack(asset_data: dict[str, Any], api_key: str) -> str | None:
    """Download asset and run unpack in BG Blender. Return .blend path."""
    destination_directory = tempfile.gettempdir()
    try:
        asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)
    except Exception:
        logger.exception("Download failed for asset %s", asset_data.get("name"))
        return None

    try:
        send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path, script="unpack_asset_bg.py")
    except Exception:
        logger.exception("Unpack (BG) failed for asset %s", asset_data.get("name"))
        return None

    return asset_file_path


def _render_validation(  # noqa: PLR0913
    mode: str,
    asset_data: dict[str, Any],
    asset_file_path: str,
    result_path: str,
    result_folder: str,
    temp_folder: str,
) -> None:
    """Dispatch the background render according to mode."""
    if mode == "model":
        template = MODEL_TEMPLATE
        script = MODEL_BG_SCRIPT
    else:  # material
        template = MATERIAL_TEMPLATE
        script = MATERIAL_BG_SCRIPT

    try:
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            template_file_path=template,
            result_path=result_path,
            result_folder=result_folder,
            temp_folder=temp_folder,
            script=script,
            binary_type="NEWEST",
            verbosity_level=2,
        )
    except Exception:
        logger.exception("Validation render failed for asset %s (%s)", asset_data.get("name"), mode)
        raise


def _export_glb(asset_data: dict[str, Any], asset_file_path: str, temp_folder: str) -> str:
    """Export GLB for model assets. Returns the GLB path (may not exist if export failed)."""
    try:
        result_path = os.path.join(temp_folder, f"{asset_data['assetBaseId']}_resdata.json")
        send_to_bg.send_to_bg(
            asset_data,
            asset_file_path=asset_file_path,
            result_path=result_path,
            script="gltf_bg_blender.py",
        )
        return asset_file_path.replace(".blend", ".glb")
    except Exception:
        logger.exception("GLB export failed for asset %s", asset_data.get("name"))
        return asset_file_path.replace(".blend", ".glb")


def _upload_result(result_folder: str, upload_id: str) -> None:
    try:
        cloudflare_storage = cloudflare_setup()
        cloudflare_storage.upload_folder(result_folder, BUCKET_VALIDATION, upload_id)
        logger.info("Uploaded validation renders for upload %s", upload_id)
    except Exception:
        logger.exception("Failed to upload validation renders for upload %s", upload_id)


def render_validation_thread(asset_data: dict[str, Any], api_key: str, mode: str = "model") -> None:
    """Worker for a single asset; mode selects model/material pipeline."""
    # basic guards
    if not asset_data or not asset_data.get("files"):
        logger.warning("Skipping empty or invalid asset entry")
        return

    upload_id = _extract_upload_id(asset_data)
    if not upload_id:
        logger.warning("No files for asset %s", asset_data.get("name"))
        return

    cf = cloudflare_setup()
    if cloudflare_validate_empty_folder(upload_id, cf):
        return

    asset_file_path = _download_and_unpack(asset_data, api_key)
    if not asset_file_path:
        return

    # prepare render output
    temp_folder, result_folder = _prepare_paths(upload_id)
    # per-mode output filename (container may differ by your BG script)
    out_name = f"{upload_id}.mkv" if mode == "model" else f"{upload_id}.mp4"
    result_path = os.path.join(result_folder, out_name)

    try:
        _render_validation(
            mode=mode,
            asset_data=asset_data,
            asset_file_path=asset_file_path,
            result_path=result_path,
            result_folder=result_folder,
            temp_folder=temp_folder,
        )

        # Model-specific GLB export
        if mode == "model":
            gltf_path = _export_glb(asset_data, asset_file_path, temp_folder)
            _move_file_safely(gltf_path, result_folder)

        # Gather frames or other outputs produced by BG script
        _collect_render_outputs(temp_folder, result_folder)

        if SKIP_UPDATE:
            logger.warning("SKIP_UPDATE==True -> skipping upload")
            logger.info("Results for asset %s in %s", asset_data.get("id"), result_folder)
            opened = utils.open_folder(result_folder)
            if not opened:
                utils.cleanup_temp(temp_folder)
            return

        _upload_result(result_folder, upload_id)

    finally:
        utils.cleanup_temp(temp_folder)


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
    mode: str = "model",
) -> None:
    """Iterate assets and dispatch validation rendering threads.

    Args:
        assets: List of asset dictionaries to process.
        api_key: BlenderKit API key forwarded to the thread function.
        mode: Validation mode, either 'model' or 'material'.

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=render_validation_thread,
        worker_kwargs={
            "api_key": api_key,
            "mode": mode,
        },
        asset_arg_position=0,
        max_concurrency=config.MAX_VALIDATION_THREADS,
        logger=logger,
    )


def _build_search_params(mode: str) -> dict[str, Any]:
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {"asset_base_id": asset_base_id}

    if mode == "model":
        return {"order": "-last_blend_upload", "asset_type": "model", "verification_status": "uploaded"}
    # conservative defaults for materials; adjust filters as needed
    return {"order": "created", "asset_type": "material", "verification_status": "uploaded"}


def main() -> None:
    """Fetch assets and run model/material validation renders."""
    if os.getenv("CLOUDFLARE_CLEANUP", "0") == "1":
        cloudflare_cleanup(BUCKET_VALIDATION)
        return

    mode = VALIDATION_MODE
    logger.info("Validation mode: %s", mode)

    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, f"assets_for_{mode}_validation.json")

    params = _build_search_params(mode)
    search.get_search_simple(
        params,
        filepath=filepath,
        page_size=min(config.MAX_ASSET_COUNT, 100),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )

    assets = search.load_assets_list(filepath)
    logger.info("Assets to be processed (%s): %s", mode, len(assets))
    for a in assets:
        logger.debug("%s ||| %s", a.get("name"), a.get("assetType"))

    iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY, mode=mode)

    utils.cleanup_temp(dpath)


if __name__ == "__main__":
    main()
