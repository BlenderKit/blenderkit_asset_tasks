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


# GLTF OPTIONS - check the default values on https://docs.blender.org/api/current/bpy.ops.export_scene.html
dracoMeshCompression = {"export_draco_mesh_compression_enable": True} # default False, Draco compresses mesh, making the file smaller
minimalGLTF = {
    "export_format": "GLB", # We want single GLTF binary file - the .GLB
    "export_apply": True, # We want to apply modifiers - added by Andy
}
maximalGLTF = minimalGLTF | { # Original settings by Vilem
    "export_image_format": "WEBP", # default AUTO
    "export_image_add_webp": True, # default False
    "export_jpeg_quality": 50, # default 75
    "export_image_quality": 50, # default 75 
    }


# ACCEPT different formats - like WEB (compressed GLTF), GODOT (uncompressed)
def generate_gltf(json_result_path: str, target_format: str):
    """
    Generates GLTF file for an asset. In case of success the metadata about the task will be stored in json_result_path defined by calling script.
    The calling script will be then able to open this JSON and check for location of the generated file. In case of error, no JSON is written. Calling script handles this. 
    """
    filepath = bpy.data.filepath.replace('.blend', '.glb')
    
    # PREPARE ASSET (do things which cannot be set in GLTF export options)
    # Disable Subdivision Surface modifiers
    for obj in bpy.context.selected_objects:
        if obj.type != 'MESH':
            continue
        for mod in obj.modifiers:
            if mod.type != 'SUBSURF':
                continue    
            mod.show_viewport = False
    
    # CHOOSE EXPORT OPTIONS - based on target_format (gltf/gltf-godot)
    print(f"Gonna generate GLTF for target format: {target_format}")
    if target_format == "gltf": # Optimize for web presentation - adding draco compression
        options = [["maximal",maximalGLTF | dracoMeshCompression], ["minimal", minimalGLTF | dracoMeshCompression]]
    elif target_format == "gltf-godot": # Optimize for use in Godot
        options = [["maximal",maximalGLTF], ["minimal", minimalGLTF]]
    else:
        print("target_format needs to be gltf/gltf-godot!")
        exit(10)

    # TRY EXPORT - go from ideal to minimal export settings
    success = False
    for option in options:
        options_name = option[0]
        GLTF_options = option[1]
        try:
            bpy.ops.export_scene.gltf(filepath=filepath, **GLTF_options)
            success = True
            break # No need to continue
        except Exception as e:
            print(f'\n\n>>>> ERROR! during {options_name} GLTF export: ', e)

    # FAILURE - Exit now, calling script will detect missing JSON and react properly
    if not success:
        exit(101)

    # SUCCESS - Write results data to a JSON file
    files = [{"type": target_format, "index": 0, "file_path": filepath}]
    with open(json_result_path, 'w') as f:
        json.dump(files, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    addon_utils.enable("io_scene_gltf2")
    datafile = sys.argv[-1]
    print('>>> Background GLTF generator has started <<<')

    with open(datafile, 'r', encoding='utf-8') as f:
        data = json.load(f) # Input data are passed via JSON

    json_result_path = data.get('result_filepath') # Output data JSON
    if not json_result_path:
        print("You need to specify json_result_path (gltf/gltf-godot) for GLTF generation")
        exit(10)

    target_format = data.get('target_format')
    if not target_format:
        print("You need to specify target_format (gltf/gltf-godot) for GLTF generation")
        exit(10)

    generate_gltf(json_result_path, target_format)
