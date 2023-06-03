import bpy
import sys
import os
import json
import addon_utils

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)


def generate_gltf(data):
    '''
    Generates GLTF file for an asset.

    Parameters
    ----------
    asset_data
    asset_file_path
    result_path

    Returns
    -------

    '''

    filepath = bpy.data.filepath.replace('.blend', '.glb')
    bpy.ops.export_scene.gltf(filepath=filepath)
    files = [{
        "type": 'gltf',
        "index": 0,
        "file_path": filepath
    }, ]

    with open(data['result_filepath'], 'w') as f:
        json.dump(files, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    addon_utils.enable("io_scene_gltf2")
    print('background gltf generator started')
    datafile = sys.argv[-1]
    with open(datafile, 'r', encoding='utf-8') as f:
        data = json.load(f)
    generate_gltf(data)
