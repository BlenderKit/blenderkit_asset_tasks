"""Script to sync twinbru library to blenderkit.

Required environment variables:
BLENDERKIT_API_KEY - API key to be used
BLENDERS_PATH - path to the folder with blender versions

"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import tempfile
import time
import zipfile
from typing import Any

import requests

from blenderkit_server_utils import concurrency, config, log, search, send_to_bg, upload, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY", "BLENDERS_PATH"])


RESPONSE_STATUS_BAD_REQUEST = 400


def read_csv_file(file_path: str) -> list[dict[str, str]]:
    """Read a CSV file and return a list of dictionaries.

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        list: List of dictionaries representing the CSV rows.
    """
    try:
        with open(file_path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            return list(reader)
    except UnicodeDecodeError:
        # If UTF-8 fails, try with ISO-8859-1 encoding
        try:
            with open(file_path, encoding="iso-8859-1") as file:
                reader = csv.DictReader(file)
                return list(reader)
        except (OSError, csv.Error):
            logger.exception("Error reading CSV file with ISO-8859-1: %s", file_path)
            return []
    except (OSError, csv.Error):
        logger.exception("Error reading CSV file: %s", file_path)
        return []


def download_file(url: str, filepath: str) -> None:
    """Download a file from a URL to a filepath.

    Write progress to console.

    Args:
        url (str): URL of the file to download.
        filepath (str): Path to save the downloaded file.

    Returns:
        None
    """
    logger.info("Downloading %s -> %s", url, filepath)
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            total_length_str = response.headers.get("content-length")
            total_length = int(total_length_str) if total_length_str and total_length_str.isdigit() else None
            with open(filepath, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    file.write(chunk)
            if total_length is not None and os.path.getsize(filepath) != total_length:
                logger.warning("Downloaded size mismatch for %s", filepath)
    except requests.RequestException:
        logger.exception("Failed to download %s", url)
        raise


def build_description_text(twinbru_asset: dict[str, Any]) -> str:
    """Build a description text for the asset.

    Args:
        twinbru_asset (dict): Dictionary containing asset data.

    Returns:
        str: Description text for the asset.
    """
    description = "Physical material that renders exactly as in real life.\n"
    description += f"\tBrand: {twinbru_asset['brand']}\n"
    description += f"\tWeight: {twinbru_asset['weight_g_per_m_squared']}\n"
    description += f"\tEnd Use: {twinbru_asset['cat_end_use']}\n"
    description += f"\tUsable Width: {twinbru_asset['selvedge_useable_width_cm']}\n"
    description += f"\tDesign Type: {twinbru_asset['cat_design_type']}\n"
    description += f"\tColour Type: {twinbru_asset['cat_colour']}\n"
    description += f"\tCharacteristics: {twinbru_asset['cat_characteristics']}\n"
    description += f"\tComposition: {twinbru_asset['total_composition']}\n"
    return description


def slugify_text(text: str) -> str:
    """Slugify a text.

    Remove special characters, replace spaces with underscores and make it lowercase.

    Args:
        text (str): Text to slugify.

    Returns:
        str: Slugified text.
    """
    text = re.sub(r"[()/#-]", "", text)
    text = re.sub(r"\s", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.lower()


def build_tags_list(twinbru_asset: dict[str, Any]) -> list[str]:
    """Create a list of tags for the asset.

    Args:
        twinbru_asset (dict): Dictionary containing asset data.

    Returns:
        list[str]: List of tags for the asset.
    """
    tags = []
    tags.extend(twinbru_asset["cat_end_use"].split(","))
    tags.extend(twinbru_asset["cat_design_type"].split(","))
    # >tags.append(twinbru_asset["cat_colour"])
    tags.extend(twinbru_asset["cat_characteristics"].split(","))

    # remove duplicates
    tags = list(set(tags))

    # shorten to max 5 tags
    tags = tags[:5]

    # make tags contain only alphanumeric characters and underscores
    # there are these characters to be replaced: ()/#- and gaps
    tags = [slugify_text(tag) for tag in tags]

    return tags


def dict_to_params(inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Convert a dictionary to a list of parameters.

    Args:
        inputs (dict): Dictionary to convert.

    Returns:
        list: List of parameters.
    """
    parameters = []
    for k, v in inputs.items():
        value = ""
        if isinstance(v, list):
            value = ",".join(str(item) for item in v)
        elif isinstance(v, bool):
            value = str(v).lower()
        elif isinstance(v, (int, float)):
            value = f"{v:f}".rstrip("0").rstrip(".")
        else:
            value = str(v)

        param: dict[str, str] = {"parameterType": k, "value": value}
        parameters.append(param)
    return parameters


def get_thumbnail_path(temp_folder: str, twinbru_asset: dict[str, Any]) -> str | None:
    """Get the thumbnail path for the asset.

    Thumbnails are stored in the /renders directory of the asset

    Args:
        temp_folder (str): Path to the temporary folder.
        twinbru_asset (dict): Dictionary containing asset data.

    Returns:
        str: Path to the thumbnail image or None if not found.
    """
    # Get the path to the renders directory
    renders_dir = os.path.join(temp_folder, "Samples")

    # Check if the renders directory exists
    if not os.path.exists(renders_dir):
        logger.error("Renders directory not found for asset %s", twinbru_asset.get("name"))
        return None

    # List all files in the renders directory
    render_files = os.listdir(renders_dir)

    # Filter for image files (assuming they are jpg or png)
    image_files = [f for f in render_files if f.lower().endswith((".jpg", ".jpeg", ".png"))]

    # If no image files found, return None
    if not image_files:
        logger.error("No thumbnail images found for asset %s", twinbru_asset.get("name"))
        return None

    # get the largest image file assuming it's the best quality thumbnail
    image_files.sort(key=lambda f: os.path.getsize(os.path.join(renders_dir, f)))

    thumbnail_file = image_files[-1]

    # If there's a thumbnail ending with _CU.jpg, use that one, since that seems to be the nicest
    for image_file in image_files:
        if image_file.endswith("_CU.jpg"):
            thumbnail_file = image_file
            break

    # Return the full path to the thumbnail
    result = os.path.join(renders_dir, thumbnail_file)
    return result


def generate_upload_data(twinbru_asset: dict[str, Any]) -> dict[str, Any]:
    """Generate the upload data for the asset.

    Args:
        twinbru_asset (dict): Dictionary containing asset data.

    Returns:
        dict: Dictionary containing the upload data.
    """
    # convert name - remove _ and remove the number that comes last in name
    readable_name = twinbru_asset["name"].split("_")
    # capitalize the first letter of each word
    readable_name = " ".join(word.capitalize() for word in readable_name[:-1])

    match_category = {
        "Blackout": "blackout",
        "Chenille": "chenille",
        "Dimout": "dimout",
        "Embroidery": "embroidery",
        "Flat weave": "flat-weave",
        "Jacquard": "jacquard",
        "Print": "print",
        "Sheer": "sheer",
        "Suede": "suede",
        "Texture": "texture",
        "Velvet": "velvet",
        "Vinyl / Imitation leather": "vinyl-imitation-leather",
    }

    upload_data: dict[str, Any] = {
        "assetType": "material",
        "sourceAppName": "blender",
        "sourceAppVersion": "4.2.0",  # IDEA: this should be read from blender
        "addonVersion": "3.12.3",  # IDEA: this should be read from addon
        "name": readable_name,
        "displayName": readable_name,
        "description": build_description_text(twinbru_asset),
        "tags": build_tags_list(twinbru_asset),
        "category": match_category.get(twinbru_asset["cat_characteristics"], "fabric"),
        "license": "royalty_free",
        "isFree": True,
        "isPrivate": False,
        "parameters": {
            # twinBru specific parameters
            "twinbruReference": int(twinbru_asset["reference"]),
            "twinBruCatEndUse": twinbru_asset["cat_end_use"],
            "twinBruColourType": twinbru_asset["cat_colour"],
            "twinBruCharacteristics": twinbru_asset["cat_characteristics"],
            "twinBruDesignType": twinbru_asset["cat_design_type"],
            "productLink": twinbru_asset["url_info"],
            # blenderkit specific parameters
            "material_style": "realistic",
            "engine": "cycles",
            "shaders": ["principled"],
            "uv": True,
            "animated": False,
            "purePbr": True,
            "textureSizeMeters": float(twinbru_asset["texture_width_cm"]) * 0.01,
            "procedural": False,
            "nodeCount": 7,
            "textureCount": 5,
            "megapixels": 5 * 4 * 4,
            "pbrType": "metallic",
            "textureResolutionMax": 4096,
            "textureResolutionMin": 4096,
            "manufacturer": twinbru_asset["brand"],
            "designCollection": twinbru_asset["collection_name"],
        },
    }
    upload_data["parameters"] = dict_to_params(upload_data["parameters"])
    return upload_data


def sync_twin_bru_library(file_path: str) -> None:
    """Sync the TwinBru library to blenderkit.

    1. Read the CSV file
    2. For each asset:
      2.1. Search for the asset on blenderkit, if it exists, skip it, if it doesn't, upload it.
      2.2. Download the asset
      2.3. Unpack the asset
      2.4. Create blenderkit upload metadata
      2.5. Make an upload request to the blenderkit API, to upload metadata and to get asset_base_id.
      2.6. run a pack_twinbru_material.py script to create a material in Blender 3D,
      write the asset_base_id and other blenderkit props on the material.
      2.7. Upload the material to blenderkit
      2.8. Mark the asset for thumbnail generation

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        None
    """
    assets = read_csv_file(file_path)
    current_dir = pathlib.Path(__file__).parent.resolve()
    i = 0
    for twinbru_asset in assets:
        if i >= config.MAX_ASSET_COUNT:  # counts only assets not already present
            break
        if _asset_exists(twinbru_asset):
            logger.info("Asset %s already exists on blenderkit", twinbru_asset.get("name"))
            continue
        i += 1
        _process_twinbru_asset(twinbru_asset, current_dir)


def _asset_exists(twinbru_asset: dict[str, Any]) -> bool:
    """Check if the TwinBru asset already exists on BlenderKit.

    Args:
        twinbru_asset: TwinBru asset row.

    Returns:
        True if the asset is already present, False otherwise.
    """
    bk_assets = search.get_search_simple(
        parameters={
            "twinbruReference": twinbru_asset["reference"],
            "verification_status": "uploaded,validated",
        },
        filepath=None,
        page_size=10,
        max_results=1,
        api_key=config.BLENDERKIT_API_KEY,
    )
    return len(bk_assets) > 0


def _process_twinbru_asset(twinbru_asset: dict[str, Any], current_dir: pathlib.Path) -> None:
    """Process a single TwinBru asset: download, pack, upload, mark, and patch.

    Args:
        twinbru_asset: Asset data from CSV.
        current_dir: Directory of this script for template path resolution.
    """
    # skip assets without a source URL
    if not twinbru_asset.get("url_texture_source"):
        logger.warning("Skipping asset %s without source URL", twinbru_asset.get("name"))
        return
    logger.info("Asset %s does not exist on blenderkit", twinbru_asset.get("name"))
    temp_folder = os.path.join(tempfile.gettempdir(), str(twinbru_asset.get("name")))
    os.makedirs(temp_folder, exist_ok=True)

    asset_file_name = str(twinbru_asset.get("url_texture_source", "")).split("/")[-1].split("?")[0]
    asset_file_path = os.path.join(temp_folder, asset_file_name)
    if not os.path.exists(asset_file_path):
        download_file(str(twinbru_asset.get("url_texture_source")), asset_file_path)
        try:
            with zipfile.ZipFile(asset_file_path, "r") as zip_ref:
                zip_ref.extractall(temp_folder)
        except (zipfile.BadZipFile, OSError):
            logger.exception("Failed to unzip asset file %s", asset_file_path)
            return

    if not any("_nrm." in f.lower() for f in os.listdir(temp_folder)):
        logger.warning("Asset %s isn't expected configuration", twinbru_asset.get("name"))
        return

    upload_data = generate_upload_data(twinbru_asset)
    logger.info("Uploading metadata for %s", upload_data.get("name"))
    logger.debug("Upload metadata payload: %s", json.dumps(upload_data, indent=2))
    asset_data = upload.upload_asset_metadata(upload_data, config.BLENDERKIT_API_KEY)
    if asset_data.get("statusCode") == RESPONSE_STATUS_BAD_REQUEST:
        logger.error("Bad request while uploading metadata: %s", asset_data)
        return

    send_to_bg.send_to_bg(
        asset_data=asset_data,
        template_file_path=os.path.join(current_dir, "blend_files", "empty.blend"),
        result_path=os.path.join(temp_folder, "material.blend"),
        script="pack_twinbru_material.py",
        binary_type="NEWEST",
        temp_folder=temp_folder,
        verbosity_level=2,
    )

    files: list[dict[str, Any]] = [
        {
            "type": "blend",
            "index": 0,
            "file_path": os.path.join(temp_folder, "material.blend"),
        },
    ]
    files_upload_data = {
        "name": asset_data["name"],
        "displayName": upload_data["name"],
        "token": config.BLENDERKIT_API_KEY,
        "id": asset_data["id"],
    }
    uploaded = upload.upload_files(files_upload_data, files)

    if uploaded:
        logger.info("Successfully uploaded asset: %s", asset_data.get("name"))
        ok = upload.mark_for_thumbnail(
            asset_id=asset_data["id"],
            api_key=config.BLENDERKIT_API_KEY,
            use_gpu=True,
            samples=100,
            resolution=2048,
            denoising=True,
            background_lightness=0.5,
            thumbnail_type="CLOTH",
            scale=2 * float(twinbru_asset["texture_width_cm"]) * 0.01,
            background=False,
            adaptive_subdivision=False,
        )
        if ok:
            logger.info("Marked asset for thumbnail generation: %s", asset_data.get("name"))
        else:
            logger.error("Failed to mark asset for thumbnail generation: %s", asset_data.get("name"))
    else:
        logger.error("Failed to upload asset: %s", asset_data.get("name"))

    upload.patch_asset_metadata(asset_data["id"], config.BLENDERKIT_API_KEY, data={"verificationStatus": "uploaded"})
    time.sleep(10)


def iterate_assets(
    assets: list[dict[str, Any]],
    process_count: int = 12,
    api_key: str = "",
) -> None:
    """Iterate through all assigned assets, check for those which need generation and send them to res gen.

    Args:
        assets (list[dict[str, Any]]): List of asset dictionaries to process
        thread_function (function, optional): function to run in thread. Defaults to None.
        process_count (int, optional): number of parallel processes. Defaults to 12.
        api_key (str, optional): API key to use. Defaults to "".

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=sync_twin_bru_library,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=process_count,
        logger=logger,
    )


def main() -> None:
    """Main entry point for the script.

    Reads the CSV file path from TWINBRU_CSV_PATH environment variable.
    If not set, prints an error message and exits.
    """
    csv_path = os.getenv("TWINBRU_CSV_PATH")
    if not csv_path:
        logger.error("TWINBRU_CSV_PATH environment variable not set")
        return

    if not os.path.exists(csv_path):
        logger.error("CSV file not found at path: %s", csv_path)
        return

    logger.info("Processing TwinBru CSV file: %s", csv_path)

    assets = search.load_assets_list(csv_path)
    # maybe this ?
    for asset in assets:
        logger.debug("%s (%s)", asset.get("name"), asset.get("assetType"))
    iterate_assets(assets)


if __name__ == "__main__":
    main()
