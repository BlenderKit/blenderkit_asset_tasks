import bpy
import sys
import os
import json

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
  bpy.ops.export_scene.gltf(filepath=filepath, check_existing=False, export_format='GLB', ui_tab='GENERAL',
                            export_copyright="", export_image_format='AUTO', export_texture_dir="",
                            export_keep_originals=False, export_texcoords=True, export_normals=True,
                            export_draco_mesh_compression_enable=False, export_draco_mesh_compression_level=6,
                            export_draco_position_quantization=14, export_draco_normal_quantization=10,
                            export_draco_texcoord_quantization=12, export_draco_color_quantization=10,
                            export_draco_generic_quantization=12, export_tangents=False, export_materials='EXPORT',
                            export_original_specular=False, export_colors=True, use_mesh_edges=False,
                            use_mesh_vertices=False, export_cameras=False, use_selection=False, use_visible=False,
                            use_renderable=False, use_active_collection=False, use_active_scene=False,
                            export_extras=False, export_yup=True, export_apply=False, export_animations=True,
                            export_frame_range=True, export_frame_step=1, export_force_sampling=True,
                            export_nla_strips=True, export_nla_strips_merged_animation_name="Animation",
                            export_def_bones=False, optimize_animation_size=False, export_anim_single_armature=True,
                            export_current_frame=False, export_skins=True, export_all_influences=False,
                            export_morph=True, export_morph_normal=True, export_morph_tangent=False,
                            export_lights=False, will_save_settings=False, filter_glob="*.glb;*.gltf")
  files = [{
    "type": 'gltf',
    "index": 0,
    "file_path": filepath
  }, ]

  with open(data['result_filepath'], 'w') as f:
    json.dump(files, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
  print('background gltf generator started')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)
  generate_gltf(data)

