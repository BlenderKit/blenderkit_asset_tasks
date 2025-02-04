"""Background script for generating GLTF files. Can be used to generate GLTFs optimized for web and Godot."""

import bpy
import sys
import os
import json
import addon_utils

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)


def install_addon(zip_path: str) -> str:
    print(f"Installing {zip_path}")
    bpy.ops.extensions.package_install_files(
        filepath=zip_path,
        repo="user_default",
        enable_on_install=False,
        overwrite=True  # Overwrites any existing version of the same extension
    )
    return ""


def enable_addon(extension_id: str) -> str:
    module_name = f"bl_ext.user_default.{extension_id}"
    print(f"Enabling {module_name}")
    module = addon_utils.enable(module_name, default_set=True, persistent=True, handle_error=None)
    if module is None:
        return "Addon enabling failed: None module returned"
    return ""


def disable_addon(extension_id: str) -> str:
    module_name = f"bl_ext.user_default.{extension_id}"
    print(f"Disabling {module_name}")
    addon_utils.disable(module_name, default_set=True, handle_error=None)
    return ""


# MARK: ENTRYPOINT
if __name__ == "__main__":
    datafile = sys.argv[-1]
    results: dict[str, str] = {}
    print('>>> Background addon test has started <<<')
    with open(datafile, 'r', encoding='utf-8') as f:
        data = json.load(f) # Input data are passed via JSON

    addon_name = data['asset_data']['name']
    zip_path = data['file_path']
    extension_id = data['asset_data']['dictParameters']['extensionId']

    print(f"Testing addon {addon_name} (extid={extension_id}), zip at: {zip_path}")
    results["install"] = install_addon(zip_path)
    results["enabling"] = enable_addon(extension_id)
    results["disabling"] = disable_addon(extension_id)

    json_result_path = data.get('result_filepath')
    with open(json_result_path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    print('>>> Background addon test has finished <<<')
