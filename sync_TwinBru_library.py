"""Script to sync twinbru library to blenderkit. 
Required environment variables:
BLENDERKIT_API_KEY - API key to be used
BLENDERS_PATH - path to the folder with blender versions

"""

import csv
import json
import requests
import os
import tempfile
import time
from datetime import datetime
import pathlib
import re
import threading
import zipfile
from blenderkit_server_utils import download, search, paths, upload, send_to_bg

results = []
page_size = 100

MAX_ASSETS = int(os.environ.get("MAX_ASSET_COUNT", "100"))
SKIP_UPLOAD = os.environ.get("SKIP_UPLOAD", False) == "True"


def read_csv_file(file_path):
    """
    Read a CSV file and return a list of dictionaries.
    """
    try:
        with open(file_path, "r", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            return [row for row in reader]
    except UnicodeDecodeError:
        # If UTF-8 fails, try with ISO-8859-1 encoding
        with open(file_path, "r", encoding="iso-8859-1") as file:
            reader = csv.DictReader(file)
            return [row for row in reader]
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return []


def download_file(url, filepath):
    """
    Download a file from a URL to a filepath.
    Write progress to console.
    """
    response = requests.get(url, stream=True)
    total_length = int(response.headers.get("content-length"))
    with open(filepath, "wb") as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
            progress = int(file.tell() / total_length * 100)
            print(f"Downloading: {progress}%", end="\r")
    print()


def build_description_text(twinbru_asset):
    """
    Build a description text for the asset.
    """
    description = f"Physical material that renders exactly as in real life."
    description += f"Brand: {twinbru_asset['brand']}\n"
    description += f"Weight: {twinbru_asset['weight_g_per_m_squared']}\n"
    description += f"End Use: {twinbru_asset['cat_end_use']}\n"
    description += f"Usable Width: {twinbru_asset['selvedge_useable_width_cm']}\n"
    description += f"Design Type: {twinbru_asset['cat_design_type']}\n"
    description += f"Colour Type: {twinbru_asset['cat_colour']}\n"
    description += f"Characteristics: {twinbru_asset['cat_characteristics']}\n"
    description += f"Composition: {twinbru_asset['total_composition']}\n"
    return description


def slugify_text(text):
    """
    Slugify a text.
    Remove special characters, replace spaces with underscores and make it lowercase.
    """
    text = re.sub(r"[()/#-]", "", text)
    text = re.sub(r"\s", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.lower()


def build_tags_list(twinbru_asset):
    """
    Create a list of tags for the asset.
    """
    tags = []
    tags.extend(twinbru_asset["cat_end_use"].split(","))
    tags.extend(twinbru_asset["cat_design_type"].split(","))
    # tags.append(twinbru_asset["cat_colour"])
    tags.extend(twinbru_asset["cat_characteristics"].split(","))
    # remove duplicates
    tags = list(set(tags))
    # shorten to max 5 tags
    tags = tags[:5]
    # make tags contain only alphanumeric characters and underscores
    # there are these characters to be replaced: ()/#- and gaps
    tags = [slugify_text(tag) for tag in tags]

    return tags


def dict_to_params(inputs):
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

        param = {"parameterType": k, "value": value}
        parameters.append(param)
    return parameters


def get_thumbnail_path(temp_folder, twinbru_asset):
    """
    Get the thumbnail path for the asset.
    Thumbnails are stored in the /renders directory of the asset
    """
    # Get the path to the renders directory
    renders_dir = os.path.join(temp_folder, "renders")

    # Check if the renders directory exists
    if not os.path.exists(renders_dir):
        print(f"Renders directory not found for asset {twinbru_asset['name']}")
        return None

    # List all files in the renders directory
    render_files = os.listdir(renders_dir)

    # Filter for image files (assuming they are jpg or png)
    image_files = [
        f for f in render_files if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    # If no image files found, return None
    if not image_files:
        print(f"No thumbnail images found for asset {twinbru_asset['name']}")
        return None

    # Sort the image files to get the first one (assuming it's the main thumbnail)
    image_files.sort()
    thumbnail_file = image_files[0]

    # Return the full path to the thumbnail
    return os.path.join(renders_dir, thumbnail_file)


def generate_upload_data(twinbru_asset):
    """
    Generate the upload data for the asset.
    """
    # convert name - remove _ and remove the number that comes last in name
    readable_name = twinbru_asset["name"].split("_")
    # capitalize the first letter of each word
    readable_name = " ".join(word.capitalize() for word in readable_name[:-1])

    upload_data = {
        "assetType": "material",
        "sourceAppName": "blender",
        "sourceAppVersion": "4.2.0",
        "addonVersion": "3.12.3",
        "name": readable_name,
        "displayName": readable_name,
        "description": build_description_text(twinbru_asset),
        "tags": build_tags_list(twinbru_asset),
        "category": "fabric",
        "license": "royalty_free",
        "isFree": True,
        "isPrivate": False,
        "parameters": {
            # twinBru specific parameters
            "twinbruReference": twinbru_asset["reference"],
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


def sync_TwinBru_library(file_path):
    """
    Sync the TwinBru library to blenderkit.
    1. Read the CSV file
    2. For each asset:
      2.1. Search for the asset on blenderkit, if it exists, skip it, if it doesn't, upload it.
      2.2. Download the asset
      2.3. Unpack the asset
      2.4. Create blenderkit upload metadata
      2.5. Make an upload request to the blenderkit API, to uplod metadata and to get asset_base_id.
      2.6. run a pack_twinbru_material.py script to create a material in Blender 3D,
      write the asset_base_id and other blenderkit props on the material.
      2.7. Upload the material to blenderkit
      2.8. Patch the asset data with a new parameter.
    """

    assets = read_csv_file(file_path)
    current_dir = pathlib.Path(__file__).parent.resolve()
    for twinbru_asset in assets:
        bk_assets = search.get_search_simple(
            parameters={
                "twinbruReference": twinbru_asset["reference"],
                "verification_status": "uploaded,validated",
            },
            filepath=None,
            page_size=10,
            max_results=1,
            api_key=paths.API_KEY,
        )
        if len(bk_assets) > 0:
            print(f"Asset {twinbru_asset['name']} already exists on blenderkit")
            continue
        else:
            print(f"Asset {twinbru_asset['name']} does not exist on blenderkit")
            # Download the asset into temp folder
            temp_folder = os.path.join(tempfile.gettempdir(), twinbru_asset["name"])
            # create the folder if it doesn't exist
            if not os.path.exists(temp_folder):
                os.makedirs(temp_folder)

            # check if the file exists
            asset_file_name = twinbru_asset["url_texture"].split("/")[-1]
            asset_file_path = os.path.join(temp_folder, asset_file_name)
            if not os.path.exists(asset_file_path):
                download_file(twinbru_asset["url_texture"], asset_file_path)
                # Unzip the asset file
                with zipfile.ZipFile(asset_file_path, "r") as zip_ref:
                    zip_ref.extractall(temp_folder)

            # Create blenderkit upload metadata
            upload_data = generate_upload_data(twinbru_asset)

            # upload metadata and get result
            print("uploading metadata")
            # print json structured
            print(json.dumps(upload_data, indent=4))
            asset_data = upload.upload_asset_metadata(upload_data, paths.API_KEY)
            if asset_data.get("statusCode") == 400:
                print(asset_data)
                return

            # Run the _bg.py script to create a material in Blender 3D
            send_to_bg.send_to_bg(
                asset_data=asset_data,
                template_file_path=os.path.join(
                    current_dir, "blend_files", "empty.blend"
                ),
                result_path=os.path.join(temp_folder, "material.blend"),
                script="pack_twinbru_material.py",
                binary_type="NEWEST",
                temp_folder=temp_folder,
                verbosity_level=2,
            )
            # Upload the asset to blenderkit
            thumbnail_path = get_thumbnail_path(temp_folder, twinbru_asset)
            files = [
                {
                    "type": "thumbnail",
                    "index": 0,
                    "file_path": thumbnail_path,
                },
                {
                    "type": "blend",
                    "index": 0,
                    "file_path": os.path.join(temp_folder, "material.blend"),
                },
            ]
            upload_data = {
                "name": asset_data["name"],
                "displayName": upload_data["displayName"],
                "token": paths.API_KEY,
                "id": asset_data["id"],
            }
            uploaded = upload.upload_files(upload_data, files)

            if uploaded:
                print(f"Successfully uploaded asset: {asset_data['name']}")
            else:
                print(f"Failed to upload asset: {asset_data['name']}")
            # mark asset as uploaded
            upload.patch_asset_metadata(
                asset_data["id"], paths.API_KEY, data={"verificationStatus": "uploaded"}
            )


def iterate_assets(filepath, thread_function=None, process_count=12, api_key=""):
    """iterate through all assigned assets, check for those which need generation and send them to res gen"""
    assets = search.load_assets_list(filepath)
    threads = []
    for asset_data in assets:
        if asset_data is not None:
            print("downloading and generating resolution for  %s" % asset_data["name"])
            thread = threading.Thread(
                target=thread_function, args=(asset_data, api_key)
            )
            thread.start()
            threads.append(thread)
            while len(threads) > process_count - 1:
                for t in threads:
                    if not t.is_alive():
                        threads.remove(t)
                    break
                time.sleep(0.1)  # wait for a bit to finish all threads


def main():
    dpath = tempfile.gettempdir()

    sync_TwinBru_library("twinBru_test_library.csv")


if __name__ == "__main__":
    main()
