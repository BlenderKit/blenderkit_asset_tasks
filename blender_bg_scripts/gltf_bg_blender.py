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


# MARK: GLTF OPTS
# check the default values on https://docs.blender.org/api/current/bpy.ops.export_scene.html
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

# MARK: MODIFIERS
def disable_subsurf_modifiers(obj):
    for mod in obj.modifiers:
        if mod.type != 'SUBSURF':
            continue
        mod.show_viewport = False
        mod.show_render = False
        print(f"----- disabled Subdivision Surface modifier for '{obj.name}'")

# MARK: UV LAYERS
def make_UV_active(obj, name):
    uvs = obj.data.uv_layers
    for uv in uvs:
        if uv.name == name:
            uvs.active = uv
            return
    print("Could not find:", name, "\n(this should never happen)")

def move_UV_to_bottom(obj, index):
    """This is a hack, because we cannot directly modify the obj.data.uv_layers.
    So instead we copy inactive UVs (added to last position) and delete their original.
    This effectively moves them on last position."""
    uvs = obj.data.uv_layers
    uvs.active_index = index
    new_name = uvs.active.name

    # set the context, so later we can add uv_texture
    bpy.context.view_layer.objects.active = obj 
    obj.select_set(True)
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.mesh.uv_texture_add()
    make_UV_active(obj, new_name)
    bpy.ops.mesh.uv_texture_remove()

    uvs.active_index = len(uvs) - 1
    uvs.active.name = new_name


def make_active_UV_first(obj):
    """Hacky function to change the order of UVs. Effectively copies UV one by one (to last position).
    Skips the active UV, so in the end the active is on first place and all other UVs are after.
    Keeps the order of all UVs, just the active is on first place.
    """
    uvs = obj.data.uv_layers
    orig_name = uvs.active.name
    orig_index = uvs.active_index
    if orig_index == 0:
        return

    print(f"----- UVs before order: {[uv for uv in uvs]} ({uvs.active.name} is active)")
    for i in range(len(uvs)):
        if i == orig_index:
            continue # do not move the active UV to bottom, keep it on top
        elif i < orig_index:
            move_UV_to_bottom(obj, 0)
        else: # at this point active is on first place, so moving the second element to keep original order of other UVs
            move_UV_to_bottom(obj, 1)

    make_UV_active(obj, orig_name) #move_UV_to_bottom() plays with active, so we set it here to originally active UV
    print(f"----- UVs before order: {[uv for uv in uvs]} ({uvs.active.name} is active)")

    
# MARK: GENERATE
# ACCEPT different formats - like WEB (compressed GLTF), GODOT (uncompressed)
def generate_gltf(json_result_path: str, target_format: str):
    """
    Generates GLTF file for an asset. In case of success the metadata about the task will be stored in json_result_path defined by calling script.
    The calling script will be then able to open this JSON and check for location of the generated file. In case of error, no JSON is written. Calling script handles this. 
    """
    filepath = bpy.data.filepath.replace('.blend', '.glb')
    
    # PREPARE ASSET (do things which cannot be set in GLTF export options)
    print("=== ASSET PRE-PROCESSING ===")
    for i, obj in enumerate(bpy.data.objects):
        print(f"---- {i} {obj.name}")
        if obj.type != 'MESH':
            continue        
        disable_subsurf_modifiers(obj)
        #remove_inactive_uv_maps(obj)
        make_active_UV_first(obj)

    print("=== PRE-PROCESSING FINISHED ===")
    
    # CHOOSE EXPORT OPTIONS - based on target_format (gltf/gltf_godot)
    print(f"Gonna generate GLTF for target format: {target_format}")
    if target_format == "gltf": # Optimize for web presentation - adding draco compression
        options = [
            ["maximal", maximalGLTF | dracoMeshCompression],
            ["minimal", minimalGLTF | dracoMeshCompression]
            ]
    elif target_format == "gltf_godot": # Optimize for use in Godot
        options = [
            ["maximal", maximalGLTF],
            ["minimal", minimalGLTF]
            ]
    else:
        print("target_format needs to be gltf/gltf_godot!")
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

# MARK: MAIN
if __name__ == "__main__":
    addon_utils.enable("io_scene_gltf2")
    datafile = sys.argv[-1]
    print('>>> Background GLTF generator has started <<<')

    with open(datafile, 'r', encoding='utf-8') as f:
        data = json.load(f) # Input data are passed via JSON

    json_result_path = data.get('result_filepath') # Output data JSON
    if not json_result_path:
        print("You need to specify json_result_path (gltf/gltf_godot) for GLTF generation")
        exit(10)

    target_format = data.get('target_format')
    if not target_format:
        print("You need to specify target_format (gltf/gltf_godot) for GLTF generation")
        exit(10)

    generate_gltf(json_result_path, target_format)
