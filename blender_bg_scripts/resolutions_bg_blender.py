import bpy
import sys
import os
import json

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)
from blenderkit_server_utils import paths, image_utils

# import paths


def get_current_resolution():
  """finds maximum image resolution in the .blend file"""
  actres = 0
  for i in bpy.data.images:
    if i.name != 'Render Result':
      actres = max(actres, i.size[0], i.size[1])
  return actres


def generate_lower_resolutions(data) -> list[dict]:
  '''Generate resolutions.
  1. get current resolution of the asset
  2. round it to the closest resolution like 4k, 2k, 1k, 512,
  3. generate lower resolutions by downscaling the textures and saving in files with suffixes like _2k, _1k, _512
  4. dumps a json file with the paths to the generated files, so they can be uploaded by the main thread.
  '''
  files = []
  asset_data = data['asset_data']
  base_fpath = bpy.data.filepath

  actual_resolution = get_current_resolution()
  print(f'current resolution of the asset is: {actual_resolution}')
  if actual_resolution <= 0: # first let's skip procedural assets
    print(f"resolution<=0, probably procedural asset -> skipping")
    return []

  p2res = paths.round_to_closest_resolution(actual_resolution)
  orig_res = p2res
  print(p2res)
  
  if p2res == [0]:
    print(f"asset has lowest possible resolution already -> skipping")
    return []
  
  original_textures_filesize = 0
  for i in bpy.data.images:
    abspath = bpy.path.abspath(i.filepath)
    if os.path.exists(abspath):
      original_textures_filesize += os.path.getsize(abspath)

  finished = False
  while not finished:
    blend_file_name = os.path.basename(base_fpath)

    dirn = os.path.dirname(base_fpath)
    fn_strip, ext = os.path.splitext(blend_file_name)

    fn = fn_strip + paths.resolution_suffix[p2res] + ext
    fpath = os.path.join(dirn, fn)

    tex_dir_path = paths.get_texture_directory(asset_data, resolution=p2res)

    tex_dir_abs = bpy.path.abspath(tex_dir_path)
    if not os.path.exists(tex_dir_abs):
      os.mkdir(tex_dir_abs)

    reduced_textures_filessize = 0
    for i in bpy.data.images:
      if i.name not in ['Render Result', 'Viewer Node']:
        print(f'scaling image {i.name} ({i.size[0]}x{i.size[1]})')
        if i.size[0] == 0 or i.size[1] == 0:
          print(f'image {i.name} is empty')
          continue

        fp = paths.get_texture_filepath(tex_dir_path, i, resolution=p2res)
        if p2res == orig_res:
          # first, let's link the image back to the original one.
          i['blenderkit_original_path'] = i.filepath
          # first round also makes reductions on the image, while keeping resolution
          image_utils.make_possible_reductions_on_image(i, fp, do_reductions=True, do_downscale=False)

        else:
          # lower resolutions only downscale
          image_utils.make_possible_reductions_on_image(i, fp, do_reductions=False, do_downscale=True)

        abspath = bpy.path.abspath(i.filepath)
        if os.path.exists(abspath):
          reduced_textures_filessize += os.path.getsize(abspath)

        i.pack()
    # save
    print(fpath)
    # if this isn't here, blender crashes.
    if bpy.app.version >= (3, 0, 0):
      bpy.context.preferences.filepaths.file_preview_type = 'NONE'

    # save the file
    bpy.ops.wm.save_as_mainfile(filepath=fpath, compress=True, copy=True)
    # compare file sizes
    
    if reduced_textures_filessize < original_textures_filesize:
      print(f'textures size was reduced from {original_textures_filesize} to {reduced_textures_filessize}')
      # this limits from uploaidng especially same-as-original resolution files in case when there is no advantage.
      # usually however the advantage can be big also for same as original resolution
      files.append({"type": p2res, "index": 0, "file_path": fpath})
    else:
      print(f'skipping resolution: textures size was not reduced, original={original_textures_filesize}, generated={reduced_textures_filessize}')

    print('prepared resolution file: ', p2res)
    if paths.rkeys.index(p2res) == 0:
      finished = True
    else:
      p2res = paths.rkeys[paths.rkeys.index(p2res) - 1]

  print(f'---> prepared resolution files: {files}')
  return files


if __name__ == "__main__":
  print('background resolution generator')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)

  result_files = generate_lower_resolutions(data)
  with open(data['result_filepath'], 'w') as f:
    json.dump(result_files, f, ensure_ascii=False, indent=4)
