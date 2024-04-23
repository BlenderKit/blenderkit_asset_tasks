import bpy
import sys
import os
import json
import addon_utils

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)

def export_gltf_detailed_settings(filepath):
    try:
        bpy.ops.export_scene.gltf(filepath=filepath, export_format='GLB', export_copyright="",
                                  export_image_format='WEBP', export_image_add_webp=True, export_image_webp_fallback=False,
                                  export_texture_dir="", export_jpeg_quality=50, export_image_quality=50,
                                  export_keep_originals=False, export_texcoords=True, export_normals=True,
                                  export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
                                  export_draco_position_quantization=14, export_draco_normal_quantization=10,
                                  export_draco_texcoord_quantization=12, export_draco_color_quantization=10,
                                  export_draco_generic_quantization=12, export_tangents=False, export_materials='EXPORT',
                                  export_colors=True, export_attributes=False, use_mesh_edges=False,
                                  use_mesh_vertices=False,
                                  export_cameras=False, use_selection=False, use_visible=False, use_renderable=False,
                                  use_active_collection_with_nested=True, use_active_collection=False,
                                  use_active_scene=False, export_extras=False, export_yup=True, export_apply=False,
                                  export_animations=True, export_frame_range=False, export_frame_step=1,
                                  export_force_sampling=True, export_animation_mode='ACTIONS',
                                  export_nla_strips_merged_animation_name="Animation", export_def_bones=False,
                                  export_hierarchy_flatten_bones=False, export_optimize_animation_size=True,
                                  export_optimize_animation_keep_anim_armature=True,
                                  export_optimize_animation_keep_anim_object=False, export_negative_frame='SLIDE',
                                  export_anim_slide_to_zero=False, export_bake_animation=False,
                                  export_anim_single_armature=True, export_reset_pose_bones=True,
                                  export_current_frame=False,
                                  export_rest_position_armature=True, export_anim_scene_split_object=True,
                                  export_skins=True,
                                  export_influence_nb=4, export_all_influences=False, export_morph=True,
                                  export_morph_normal=True, export_morph_tangent=False, export_morph_animation=True,
                                  export_morph_reset_sk_data=True, export_lights=False, export_try_sparse_sk=True,
                                  export_try_omit_sparse_sk=False, export_gpu_instances=False, export_nla_strips=True,
                                  export_original_specular=False, will_save_settings=False, filter_glob="*.glb")
    except Exception as e:
        # try export with much simpler settings when the complex settings fail
        print('Error during export: ', e)
        bpy.ops.export_scene.gltf(filepath=filepath, export_format='GLB', export_copyright="")

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
    export_gltf_detailed_settings(filepath)
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
