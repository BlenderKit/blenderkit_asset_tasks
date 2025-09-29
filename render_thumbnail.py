"""Script to rerender of thumbnail for materials and models.

This script handles the automated process of generating new thumbnails for BlenderKit assets.
It supports both materials and models, with configurable rendering parameters.

Required environment variables:
BLENDERKIT_API_KEY - API key to be used
BLENDERS_PATH - path to the folder with blender versions
ASSET_BASE_ID - (optional) if set, only this asset will be processed

Optional environment variables for thumbnail parameters:
THUMBNAIL_USE_GPU - (bool) Use GPU for rendering
THUMBNAIL_SAMPLES - (int) Number of render samples
THUMBNAIL_RESOLUTION - (int) Resolution of render
THUMBNAIL_DENOISING - (bool) Use denoising
THUMBNAIL_BACKGROUND_LIGHTNESS - (float) Background lightness (0-1)

# Wireframe-specific (new):
THUMBNAIL_WIREFRAME - (bool) Render wireframe thumbnail
THUMBNAIL_WIREFRAME_OVERLAY - (bool) Overlay wireframe over shaded base (otherwise pure wireframe)
THUMBNAIL_WIREFRAME_THICKNESS - (float) Line thickness (implementation-specific)
THUMBNAIL_WIREFRAME_COLOR - (str) Color (e.g. "#FFFFFF" or "1,1,1")

For materials:
THUMBNAIL_TYPE - Type of material preview (BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR)
THUMBNAIL_SCALE - (float) Scale of preview object
THUMBNAIL_BACKGROUND - (bool) Use background for transparent materials
THUMBNAIL_ADAPTIVE_SUBDIVISION - (bool) Use adaptive subdivision

For models:
THUMBNAIL_ANGLE - Camera angle (DEFAULT, FRONT, SIDE, TOP)
THUMBNAIL_SNAP_TO - Object placement (GROUND, WALL, CEILING, FLOAT)

The script workflow:
1. Fetches assets that need thumbnail regeneration
2. For each asset:
   - Downloads the asset file
   - Renders a new thumbnail using Blender
   - Uploads the new thumbnail
   - Updates the asset metadata
3. Handles multiple assets concurrently using threading
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from blenderkit_server_utils import concurrency, config, download, log, search, send_to_bg, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY", "BLENDERS_PATH"])


# Required environment variables
PAGE_SIZE_LIMIT = 100

SKIP_UPDATE = config.SKIP_UPDATE


# Thumbnail default parameters
DEFAULT_THUMBNAIL_PARAMS: dict[str, Any] = {
    "thumbnail_use_gpu": True,
    "thumbnail_samples": 100,
    "thumbnail_resolution": 2048,
    "thumbnail_denoising": True,
    "thumbnail_background_lightness": 0.9,
    # Wireframe defaults (new)
    "additional_outputs": "wireframe",  # this includes also separate wireframe render
    "thumbnail_wireframe_thickness": 1.0,
    "thumbnail_wireframe_color": None,  # if None, default color will be random
}

# Material-specific defaults
DEFAULT_MATERIAL_PARAMS: dict[str, Any] = {
    "thumbnail_type": "BALL",
    "thumbnail_scale": 1.0,
    "thumbnail_background": False,
    "thumbnail_adaptive_subdivision": False,
}

# Model-specific defaults
DEFAULT_MODEL_PARAMS: dict[str, Any] = {
    "thumbnail_angle": "DEFAULT",
    "thumbnail_snap_to": "GROUND",
}


def _env_bool(name: str, *, default: bool) -> bool:
    """Return boolean env var value using string comparison with a sensible default.

    Args:
        name: Environment variable name.
        default: Default boolean value when env var is not set.

    Returns:
        Boolean parsed from environment ("True"/"False").
    """
    default_str = "True" if default else "False"
    return os.getenv(name, default_str).lower() == "true"


def parse_json_params(json_str: str | None) -> dict[str, Any]:
    """Parse the markThumbnailRender JSON parameter.

    Args:
        json_str: JSON string containing thumbnail parameters.

    Returns:
        Parsed parameters or empty dict if invalid or missing.
    """
    if not json_str:
        return {}

    try:
        params: dict[str, Any] = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        logger.exception("Invalid JSON for markThumbnailRender: %s", json_str)
        return {}

    # Normalize string params (ensure they are strings)
    string_params = [
        "thumbnail_type",
        "thumbnail_angle",
        "thumbnail_snap_to",
        # new string params
        "thumbnail_wireframe_color",
    ]
    for param in string_params:
        if param in params and not isinstance(params[param], str):
            params[param] = str(params[param])

    # Convert string boolean values to actual booleans
    bool_params = [
        "thumbnail_use_gpu",
        "thumbnail_denoising",
        "thumbnail_background",
        "thumbnail_adaptive_subdivision",
    ]
    for param in bool_params:
        if param in params and isinstance(params[param], str):
            params[param] = params[param].lower() == "true"

    # Convert numeric values
    numeric_params = [
        "thumbnail_samples",
        "thumbnail_resolution",
        "thumbnail_background_lightness",
        "thumbnail_scale",
    ]
    for param in numeric_params:
        if param in params:
            try:
                value_str = str(params[param])
                params[param] = float(value_str) if "." in value_str else int(value_str)
            except (ValueError, TypeError):
                logger.warning("Invalid numeric parameter %s: %s", param, params[param])
                params.pop(param, None)

    logger.debug("Parsed thumbnail params: %s", params)
    return params


def get_thumbnail_params(asset_type: str, mark_thumbnail_render: str | None = None) -> dict[str, Any]:
    """Get thumbnail parameters from environment variables or defaults.

    This function consolidates all thumbnail rendering parameters, combining values
    from different sources in order of priority:
    1. Environment variables (highest priority)
    2. markThumbnailRender JSON parameter
    3. Default values (lowest priority)

    Args:
        asset_type: Type of asset ('material' or 'model')
        mark_thumbnail_render: JSON string from markThumbnailRender parameter (optional)

    Returns:
        dict: Combined dictionary of all thumbnail parameters
    """
    # Start with default parameters
    params: dict[str, Any] = DEFAULT_THUMBNAIL_PARAMS.copy()

    # Add type-specific defaults
    if asset_type == "material":
        params.update(DEFAULT_MATERIAL_PARAMS)
    elif asset_type == "model":
        params.update(DEFAULT_MODEL_PARAMS)

    # Update with markThumbnailRender parameters if available
    json_params = parse_json_params(mark_thumbnail_render)
    if json_params:
        params.update(json_params)

    # Update with environment variables (highest priority)
    env_updates = {
        "thumbnail_use_gpu": _env_bool("THUMBNAIL_USE_GPU", default=bool(params["thumbnail_use_gpu"])),
        "thumbnail_samples": int(os.getenv("THUMBNAIL_SAMPLES", params["thumbnail_samples"])),
        "thumbnail_resolution": int(os.getenv("THUMBNAIL_RESOLUTION", params["thumbnail_resolution"])),
        "thumbnail_denoising": _env_bool("THUMBNAIL_DENOISING", default=bool(params["thumbnail_denoising"])),
        "thumbnail_background_lightness": float(
            os.getenv("THUMBNAIL_BACKGROUND_LIGHTNESS", params["thumbnail_background_lightness"]),
        ),
    }

    # Add type-specific environment variables
    if asset_type == "material":
        env_updates.update(
            {
                "thumbnail_type": os.getenv("THUMBNAIL_TYPE", params["thumbnail_type"]),
                "thumbnail_scale": float(os.getenv("THUMBNAIL_SCALE", params["thumbnail_scale"])),
                "thumbnail_background": _env_bool(
                    "THUMBNAIL_BACKGROUND",
                    default=bool(params["thumbnail_background"]),
                ),
                "thumbnail_adaptive_subdivision": _env_bool(
                    "THUMBNAIL_ADAPTIVE_SUBDIVISION",
                    default=bool(params["thumbnail_adaptive_subdivision"]),
                ),
            },
        )
    elif asset_type == "model":
        env_updates.update(
            {
                "thumbnail_angle": os.getenv("THUMBNAIL_ANGLE", params["thumbnail_angle"]),
                "thumbnail_snap_to": os.getenv("THUMBNAIL_SNAP_TO", params["thumbnail_snap_to"]),
                # Wireframe env (new)
                "thumbnail_wireframe_thickness": float(
                    os.getenv("THUMBNAIL_WIREFRAME_THICKNESS", params["thumbnail_wireframe_thickness"]),
                ),
                "thumbnail_wireframe_color": os.getenv(
                    "THUMBNAIL_WIREFRAME_COLOR",
                    params["thumbnail_wireframe_color"],
                ),
            },
        )

    # Only update with environment variables that are actually set
    params.update({k: v for k, v in env_updates.items() if k in params})

    return params


def _select_script_template(asset_type: str) -> tuple[str, Path]:
    """Return background script and template path for the given asset type.

    Args:
        asset_type: Type of asset ('material' or 'model')

    Returns:
        A tuple of (script_name, template_path)

    Raises:
        ValueError: If the asset type is unsupported.
    """
    mapping: dict[str, tuple[str, Path]] = {
        "material": (
            "autothumb_material_bg.py",
            Path(__file__).parent / "blend_files" / "material_thumbnailer_cycles.blend",
        ),
        "model": (
            "autothumb_model_bg.py",
            Path(__file__).parent / "blend_files" / "model_thumbnailer.blend",
        ),
    }
    selected = mapping.get(asset_type)
    if not selected:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    return selected


def _run_bg_render(
    asset_data: dict[str, Any],
    asset_file_path: str,
    template_path: Path,
    result_filepath: str,
    script_name: str,
) -> None:
    """Run the background Blender render, raising on failure."""
    ret = send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        template_file_path=str(template_path),
        result_path=result_filepath,
        script=script_name,
    )
    if ret:
        raise RuntimeError(f"BG render failed (script={script_name}, ret={ret})")


def _maybe_preview_and_return(result_filepath: str) -> bool:
    """Preview result and return True when SKIP_UPDATE is enabled."""
    if not SKIP_UPDATE:
        return False
    try:
        from PIL import Image  # lazy import

        Image.open(result_filepath).show()
    except OSError:
        logger.debug("Preview failed for %s", result_filepath, exc_info=True)
    opened = utils.open_folder(result_filepath)
    if not opened:
        logger.error("Failed to open folder %s", os.path.dirname(result_filepath))
    return True


def _upload_and_clear(asset_data: dict[str, Any], api_key: str, result_filepath: str) -> None:
    """Upload the thumbnail and clear the markThumbnailRender flag, raising on failures."""
    files_to_upload: list[dict[str, Any]] = [{"type": "thumbnail", "index": 0, "file_path": result_filepath}]
    upload_data: dict[str, Any] = {
        "name": asset_data.get("name"),
        "displayName": asset_data.get("displayName"),
        "token": api_key,
        "id": asset_data.get("id"),
    }
    ok = upload.upload_files(upload_data, files_to_upload)
    if not ok:
        raise RuntimeError(f"Upload failed for {asset_data.get('name')}")

    logger.info("Uploaded new thumbnail for %s", asset_data.get("name"))
    cleared = upload.delete_individual_parameter(
        asset_id=str(asset_data.get("id")),
        param_name="markThumbnailRender",
        param_value="",
        api_key=api_key,
    )
    if not cleared:
        raise RuntimeError(f"Failed to clear markThumbnailRender for {asset_data.get('name')}")
    logger.info("Cleared markThumbnailRender for %s", asset_data.get("name"))


def render_thumbnail_thread(asset_data: dict[str, Any], api_key: str) -> None:
    """Process a single asset's thumbnail in a separate thread."""
    if not asset_data or not asset_data.get("files"):
        logger.warning("Skipping empty or invalid asset entry")
        return

    asset_file_path: str | None = None
    temp_folder: str | None = None

    try:
        destination_directory = tempfile.gettempdir()
        asset_type = str(asset_data.get("assetType", "")).lower()
        mark_json = (asset_data.get("dictParameters") or {}).get("markThumbnailRender")

        # Resolve params and download asset
        thumbnail_params = get_thumbnail_params(asset_type, mark_thumbnail_render=mark_json)
        asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

        # Prepare output
        temp_folder = tempfile.mkdtemp()
        ext = {"model": "jpg", "material": "png"}.get(asset_type, "png")
        result_filepath = os.path.join(temp_folder, f"{asset_data['assetBaseId']}_thumb.{ext}")

        # Merge params and select BG job
        asset_data.update(thumbnail_params)
        script_name, template_path = _select_script_template(asset_type)

        # Render and upload
        _run_bg_render(asset_data, asset_file_path, template_path, result_filepath, script_name)
        if _maybe_preview_and_return(result_filepath):
            return
        _upload_and_clear(asset_data, api_key, result_filepath)

    except Exception:
        logger.exception("Error processing thumbnail for %s", asset_data.get("name"))
    finally:
        try:
            if asset_file_path and os.path.isfile(asset_file_path):
                os.remove(asset_file_path)
        except OSError:
            logger.debug("Failed to remove asset file %s", asset_file_path, exc_info=True)
        try:
            if temp_folder and not SKIP_UPDATE:
                utils.cleanup_temp(temp_folder)
        except OSError:
            logger.debug("Failed to cleanup temp folder %s", temp_folder, exc_info=True)


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str,
    process_count: int = 1,
) -> None:
    """Process multiple assets concurrently using threading.

    Manages a pool of worker threads to process multiple assets simultaneously.
    Limits the number of concurrent processes to avoid system overload.

    Args:
        assets: List of asset dictionaries to process
        api_key: BlenderKit API key for authentication
        process_count: Maximum number of concurrent thumbnail generations
    """
    concurrency.run_asset_threads(
        assets,
        worker=render_thumbnail_thread,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=process_count,
        logger=logger,
    )


def main() -> None:
    """Main entry point for the thumbnail generation script.

    Sets up the initial conditions for thumbnail generation:
    1. Creates a temporary directory for asset processing
    2. Configures search parameters to find assets needing thumbnails
    3. Fetches the list of assets to process
    4. Initiates the thumbnail generation process

    The script can either process a specific asset (if ASSET_BASE_ID is set)
    or process multiple assets based on search criteria.
    """
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_thumbnails.json")

    # Set up search parameters
    if config.ASSET_BASE_ID:
        params = {"asset_base_id": config.ASSET_BASE_ID}
    else:
        params = {
            "asset_type": "model,material",
            "order": "created",
            "markThumbnailRender_isnull": False,
        }

    # Get assets to process
    assets = search.get_search_simple(
        params,
        filepath,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )

    assets = search.load_assets_list(filepath)

    logger.info("Found %s assets to process", len(assets))
    for asset in assets:
        logger.debug("%s (%s)", asset.get("name"), asset.get("assetType"))

    iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
