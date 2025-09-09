"""Generate image captions using CLIP Interrogator for BlenderKit assets.

This script installs required dependencies (PyTorch with CUDA, Gradio,
open-clip, and clip-interrogator), fetches recently validated assets, and
uses CLIP Interrogator to produce captions from each asset's thumbnail image.
It then patches a parameter on the server with the generated caption.
"""

# ----------------------------------------------------------------------------------------------------------------------
# Installing PyTorch GPU version
# https://pytorch.org/get-started/locally/
# & C:\Users\blend\AppData\Local\Programs\Python\Python310\python.exe -m pip install \
#     torch torchvision torchaudio \
#     --index-url https://download.pytorch.org/whl/cu117

# ----------------------------------------------------------------------------------------------------------------------
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.request import urlopen

import requests

# upgrade pip
subprocess.call([sys.executable, "-m", "ensurepip"])
subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])


def check_and_install(to_import: list[str], to_install: list[str]) -> None:
    """Check if packages can be imported, and install them if not.

    Args:
        to_import: List of package names to attempt to import.
        to_install: List of package names to install via pip if import fails.

    Returns:
        None
    """
    failed_to_import = False
    for package in to_import:
        try:
            __import__(package)
        except ImportError:
            failed_to_import = True

    if failed_to_import:
        for package in to_install:
            print(f"Package {package} not found. Installing...")
            subprocess.call([sys.executable, "-m", "pip", "install", package])


# install required packages
subprocess.call(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "torch",
        "torchvision",
        "torchaudio",
        "--index-url",
        "https://download.pytorch.org/whl/cu126",
    ],
)

check_and_install(to_import=["gradio"], to_install=["gradio"])
check_and_install(to_import=["open_clip_torch"], to_install=["open_clip_torch"])
check_and_install(to_import=["clip_interrogator"], to_install=["clip_interrogator"])


import torch  # noqa: E402
from clip_interrogator import Config, Interrogator  # noqa: E402
from PIL import Image  # noqa: E402

from blenderkit_server_utils import paths, search, upload  # noqa: E402

torch.cuda.is_available()
print(torch.__version__)


# --------------------------------------------------------------------------------------------------------------------
def read_json(json_url: str) -> Any:
    """Read and parse JSON from a URL.

    Args:
      json_url: The URL that returns a JSON document.

    Returns:
      The parsed JSON payload loaded from the given URL.
    """
    print(f"Reading json from link {json_url}")

    # store the response of URL
    response = urlopen(json_url)  # noqa: S310

    # storing the JSON response from url
    data_json = json.loads(response.read())

    # return the json response
    return data_json


def main() -> None:
    """Main function to generate captions for assets using CLIP Interrogator.

    Steps:
    1. Fetch recently validated assets without an existing caption.
    """
    param_name: str = "imageCaptionInterrogator"
    params: dict[str, Any] = {
        "order": "-created",
        "verification_status": "validated",
        param_name + "_isnull": True,
    }
    dpath: str = tempfile.gettempdir()
    filepath: str = os.path.join(dpath, "assets_for_resolutions.json")
    max_assets: int = int(os.environ.get("MAX_ASSET_COUNT", "100"))

    assets = search.get_search_simple(
        params,
        filepath,
        page_size=min(max_assets, 100),
        max_results=max_assets,
        api_key=paths.API_KEY,
    )

    ci = Interrogator(Config(clip_model_name="ViT-L-14/openai"))

    for asset_data in assets:
        start_time = time.time()

        asset_id = asset_data["id"]

        print(asset_data["thumbnailXlargeUrl"])
        print(f"Interrogating asset {asset_id} {asset_data['name']}")
        img_data = requests.get(asset_data["thumbnailXlargeUrl"], timeout=10).content
        img_path = os.path.join(dpath, "image_name.jpg")
        with open(img_path, "wb") as handler:
            handler.write(img_data)

        # upload image
        image = Image.open(img_path).convert("RGB")

        param_value = ci.interrogate(image)
        print(param_value)

        upload.patch_individual_parameter(
            asset_id=asset_id,
            param_name=param_name,
            param_value=param_value,
            api_key=paths.API_KEY,
        )
        upload.get_individual_parameter(
            asset_id=asset_id,
            param_name=param_name,
            api_key=paths.API_KEY,
        )

        print(f"--- {time.time() - start_time} seconds ---")


if __name__ == "__main__":
    main()
