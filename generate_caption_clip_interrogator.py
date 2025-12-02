# ruff: noqa: I001
"""Generate image captions using CLIP Interrogator for BlenderKit assets.

Fetch recently validated assets without a generated caption and use
CLIP Interrogator to produce captions from each asset's thumbnail image.
Patch a parameter on the server with the generated caption.

Notes:
- This script assumes all dependencies are already installed in the environment.
- No dynamic package installation is performed.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any


from blenderkit_server_utils import config, search, upload, log, utils


logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY"])

utils.ensure_installed(
    package="torch",
    to_install=["torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu126"],
)

utils.ensure_installed(package="clip_interrogator", to_install=["clip-interrogator"])
utils.ensure_installed(package="requests", to_install=["requests"])
utils.ensure_installed(package="PIL", to_install=["Pillow"])


from clip_interrogator import Config, Interrogator  # noqa: E402
from PIL import Image  # noqa: E402
import requests  # noqa: E402
import torch  # noqa: E402


# Constants
PAGE_SIZE_LIMIT: int = 100
REQUEST_TIMEOUT: int = 15
CLIP_MODEL_NAME: str = "ViT-L-14/openai"
CAPTION_MODEL_NAME: str = "blip-large"  # or blip-small
IMAGE_FILENAME: str = "image_name.jpg"

PARAM_NAME_TARGET: str = "imageCaptionInterrogator"

SKIP_UPDATE: bool = config.SKIP_UPDATE


def log_torch_info() -> None:
    """Log basic PyTorch environment information."""
    try:
        cuda_available = torch.cuda.is_available()
        logger.info("Torch %s (CUDA available: %s)", torch.__version__, cuda_available)
    except (AttributeError, RuntimeError):
        logger.debug("Could not query torch environment", exc_info=True)


def process_asset(ci: Interrogator, asset_data: dict[str, Any], dpath: str, param_name: str) -> None:
    """Download thumbnail, interrogate via CLIP, and patch caption.

    Args:
        ci: Initialized CLIP Interrogator instance.
        asset_data: Asset dictionary containing id, name, and thumbnail URL.
        dpath: Temporary directory for image storage.
        param_name: Name of the server parameter to patch with the caption.
    """
    start_time = time.time()
    asset_id = asset_data.get("id")
    asset_name = asset_data.get("name")
    thumb_url = asset_data.get("thumbnailXlargeUrl")

    logger.info("Interrogating asset %s: %s", asset_id, asset_name)
    if not isinstance(thumb_url, str) or not thumb_url:
        logger.warning("Asset %s has no thumbnail URL; skipping.", asset_id)
        return

    # Download thumbnail
    try:
        response = requests.get(thumb_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        img_bytes = response.content
    except requests.exceptions.RequestException:
        logger.exception("Failed to download thumbnail for asset %s", asset_id)
        return

    img_path = os.path.join(dpath, IMAGE_FILENAME)
    try:
        with open(img_path, "wb") as handler:
            handler.write(img_bytes)
    except OSError:
        logger.exception("Failed to write image to %s for asset %s", img_path, asset_id)
        return

    # Open image and interrogate
    try:
        image = Image.open(img_path).convert("RGB")
        param_value: str = ci.interrogate_classic(image)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Interrogation failed for asset %s", asset_id)
        return

    logger.info("Caption result for %s: %s", asset_id, param_value)

    if SKIP_UPDATE:
        logger.info("Processed in %.3f s", time.time() - start_time)
        logger.info("SKIP_UPDATE is set, not patching the asset.")
        return

    # Patch parameter
    try:
        upload.patch_individual_parameter(
            asset_id=asset_id,
            param_name=param_name,
            param_value=param_value,
            api_key=config.BLENDERKIT_API_KEY,
        )
        upload.get_individual_parameter(
            asset_id=asset_id,
            param_name=param_name,
            api_key=config.BLENDERKIT_API_KEY,
        )
    except requests.exceptions.RequestException:
        logger.exception("Failed to patch parameter for asset %s", asset_id)
        return

    logger.info("Processed in %.3f s", time.time() - start_time)


def main() -> None:
    """Generate captions for assets using CLIP Interrogator.

    Steps:
    1. Fetch recently validated assets without an existing caption.
    2. Download the asset thumbnail and run CLIP Interrogator.
    3. Patch the generated caption as a parameter on the server.
    """
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:  # Single asset handling - for asset validation hook
        params = {
            "asset_base_id": asset_base_id,
        }
    else:  # None asset specified - will run on 100 unprocessed assets - for nightly jobs
        params = {
            "order": "-created",
            "verification_status": "validated",
            PARAM_NAME_TARGET + "_isnull": True,
        }

    dpath: str = tempfile.gettempdir()
    filepath: str = os.path.join(dpath, "assets_for_resolutions.json")

    # Log torch details
    log_torch_info()

    # Query assets to process
    assets: list[dict[str, Any]] = search.get_search_simple(
        params,
        filepath=filepath,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    if not assets:
        logger.info("No assets found to process.")
        utils.cleanup_temp(dpath)
        return

    # Initialize CLIP Interrogator once
    cfg = Config(
        clip_model_name=CLIP_MODEL_NAME,
        caption_model_name=CAPTION_MODEL_NAME,
        flavor_intermediate_count=512,  # fewer tags checked
    )
    ci = Interrogator(cfg)

    for asset_data in assets:
        process_asset(ci=ci, asset_data=asset_data, dpath=dpath, param_name=PARAM_NAME_TARGET)

    utils.cleanup_temp(dpath)

if __name__ == "__main__":
    main()
