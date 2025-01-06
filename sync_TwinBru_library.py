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
    renders_dir = os.path.join(temp_folder, "Samples")

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

    # get the largest image file assuming it's the best quality thumbnail
    image_files.sort(key=lambda f: os.path.getsize(os.path.join(renders_dir, f)))

    thumbnail_file = image_files[-1]

    # If there's a thumbnail ending with _CU.jpg, use that one, since that seems to be the nicest
    for image_file in image_files:
        if image_file.endswith("_CU.jpg"):
            thumbnail_file = image_file
            break

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

    upload_data = {
        "assetType": "material",
        "sourceAppName": "blender",
        "sourceAppVersion": "4.2.0",
        "addonVersion": "3.12.3",
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


import tempfile
import os
from blenderkit_server_utils import paths


# def generate_material_thumbnail(
#     asset_name,
#     thumbnail_generator_type,
#     thumbnail_scale,
#     thumbnail_background,
#     thumbnail_background_lightness,
#     thumbnail_resolution,
#     thumbnail_samples,
#     thumbnail_denoising,
#     adaptive_subdivision,
#     texture_size_meters,
# ):
#     asset = bpy.context.active_object.active_material
#     tempdir = tempfile.mkdtemp()
#     filepath = os.path.join(tempdir, "material_thumbnailer_cycles.blend")

#     # if this isn't here, blender crashes.
#     if bpy.app.version >= (3, 0, 0):
#         bpy.context.preferences.filepaths.file_preview_type = "NONE"

#     # save a copy of actual scene but don't interfere with the users models
#     bpy.ops.wm.save_as_mainfile(filepath=filepath, compress=False, copy=True)

#     path_can_be_relative = True
#     thumb_dir = os.path.dirname(bpy.data.filepath)
#     if thumb_dir == "":  # file not saved
#         thumb_dir = tempdir
#         path_can_be_relative = False
#     an_slug = paths.slugify(asset_name)

#     thumb_path = os.path.join(thumb_dir, an_slug)

#     if path_can_be_relative:
#         rel_thumb_path = os.path.join("//", an_slug)
#     else:
#         rel_thumb_path = thumb_path

#     # auto increase number of the generated thumbnail.
#     i = 0
#     while os.path.isfile(thumb_path + ".png"):
#         thumb_path = os.path.join(thumb_dir, an_slug + "_" + str(i).zfill(4))
#         rel_thumb_path = os.path.join("//", an_slug + "_" + str(i).zfill(4))
#         i += 1

#     asset.blenderkit.thumbnail = rel_thumb_path + ".png"
#     bkit = asset.blenderkit

#     args_dict = {
#         "type": "material",
#         "asset_name": asset_name,
#         "filepath": filepath,
#         "thumbnail_path": thumb_path,
#         "tempdir": tempdir,
#     }

#     thumbnail_args = {
#         "thumbnail_type": thumbnail_generator_type,
#         "thumbnail_scale": thumbnail_scale,
#         "thumbnail_background": thumbnail_background,
#         "thumbnail_background_lightness": thumbnail_background_lightness,
#         "thumbnail_resolution": thumbnail_resolution,
#         "thumbnail_samples": thumbnail_samples,
#         "thumbnail_denoising": thumbnail_denoising,
#         "adaptive_subdivision": adaptive_subdivision,
#         "texture_size_meters": texture_size_meters,
#     }
#     args_dict.update(thumbnail_args)


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
    i = 0
    for twinbru_asset in assets:
        if (
            i >= MAX_ASSETS
        ):  # this actually counts only the assets that are not already on blenderkit
            break
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
            i += 1
            print(f"Asset {twinbru_asset['name']} does not exist on blenderkit")
            # Download the asset into temp folder
            temp_folder = os.path.join(tempfile.gettempdir(), twinbru_asset["name"])
            # create the folder if it doesn't exist
            if not os.path.exists(temp_folder):
                os.makedirs(temp_folder)

            # check if the file exists
            asset_file_name = twinbru_asset["url_texture_source"].split("/")[-1]
            # crop any data behind first ? in the string
            asset_file_name = asset_file_name.split("?")[0]
            asset_file_path = os.path.join(temp_folder, asset_file_name)
            if not os.path.exists(asset_file_path):
                download_file(twinbru_asset["url_texture_source"], asset_file_path)
                # Unzip the asset file
                with zipfile.ZipFile(asset_file_path, "r") as zip_ref:
                    zip_ref.extractall(temp_folder)

            # skip assets that don't have the same suffix as originally
            # let's assume all have at least  texture with "_NRM." in the folder
            # switched this to lower case, as the files are not always consistent
            if not any("_nrm." in f.lower() for f in os.listdir(temp_folder)):
                print(f"Asset {twinbru_asset['name']} isn't expected configuration")
                continue

            # Create blenderkit upload metadata
            upload_data = generate_upload_data(twinbru_asset)

            # upload metadata and get result
            print("uploading metadata")
            # print json structure

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
            # render the thumbnail as in blenderkit.
            # Example usage
            # generate_material_thumbnail(
            #     asset_name="ExampleMaterial",
            #     thumbnail_generator_type="FABRIC    ",
            #     thumbnail_scale=1.0,
            #     thumbnail_background=True,
            #     thumbnail_background_lightness=1.0,
            #     thumbnail_resolution=512,
            #     thumbnail_samples=128,
            #     thumbnail_denoising=True,
            #     adaptive_subdivision=True,
            #     texture_size_meters=1.0,
            # )
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

            # Add a delay noto to overwhelm the server
            time.sleep(60)


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

    sync_TwinBru_library("converted twinbreu.csv")


if __name__ == "__main__":
    main()
