import sys
import os
import bpy
import json

#import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)
from blenderkit_server_utils import paths

resolution_suffix = {
  'blend': '',
  'resolution_0_5K': '_05k',
  'resolution_1K': '_1k',
  'resolution_2K': '_2k',
  'resolution_4K': '_4k',
  'resolution_8K': '_8k',
}


def unpack_asset(data):
  asset_data = data['asset_data']
  # printprint(asset_data)

  blend_file_name = os.path.basename(bpy.data.filepath)
  ext = os.path.splitext(blend_file_name)[1]

  resolution = asset_data.get('resolution', 'blend')
  # TODO - passing resolution inside asset data might not be the best solution
  tex_dir_path = paths.get_texture_directory(asset_data, resolution=resolution)
  tex_dir_abs = bpy.path.abspath(tex_dir_path)
  if not os.path.exists(tex_dir_abs):
    try:
      os.mkdir(tex_dir_abs)
    except Exception as e:
      print(e)
  bpy.data.use_autopack = False
  for image in bpy.data.images:
    if image.name != 'Render Result':
      # suffix = paths.resolution_suffix(data['suffix'])
      fp = paths.get_texture_filepath(tex_dir_path, image, resolution=resolution)
      print('unpacking file', image.name)
      print(image.filepath, fp)

      for pf in image.packed_files:
        pf.filepath = fp  # bpy.path.abspath(fp)
      image.filepath = fp  # bpy.path.abspath(fp)
      image.filepath_raw = fp  # bpy.path.abspath(fp)
      # image.save()
      if len(image.packed_files) > 0:
        # image.unpack(method='REMOVE')
        image.unpack(method='WRITE_ORIGINAL')

  # mark asset browser asset
  data_block = None
  if asset_data['assetType'] == 'model':
    for ob in bpy.data.objects:
      if ob.parent is None and ob in bpy.context.visible_objects:
        if bpy.app.version >= (3, 0, 0):
          ob.asset_mark()
    # for c in bpy.data.collections:
    #     if c.get('asset_data') is not None:
    #         if bpy.app.version >= (3, 0, 0):

    #         c.asset_mark()
    #         data_block = c
  elif asset_data['assetType'] == 'material':
    for m in bpy.data.materials:
      if bpy.app.version >= (3, 0, 0):
        m.asset_mark()
      data_block = m
  elif asset_data['assetType'] == 'scene':
    if bpy.app.version >= (3, 0, 0):
      bpy.context.scene.asset_mark()
  elif asset_data['assetType'] == 'brush':
    for b in bpy.data.brushes:
      if b.get('asset_data') is not None:
        if bpy.app.version >= (3, 0, 0):
          b.asset_mark()
        data_block = b
  if bpy.app.version >= (3, 0, 0) and data_block is not None:
    tags = data_block.asset_data.tags
    for t in tags:
      tags.remove(t)
    tags.new('description: ' + asset_data['description'])
    tags.new('tags: ' + ','.join(asset_data['tags']))
  #
  # if this isn't here, blender crashes when saving file.
  if bpy.app.version >= (3, 0, 0):
    bpy.context.preferences.filepaths.file_preview_type = 'NONE'

  bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, compress=False)
  # now try to delete the .blend1 file
  try:

    os.remove(bpy.data.filepath + '1')
  except Exception as e:
    print(e)
  bpy.ops.wm.quit_blender()
  sys.exit()


if __name__ == "__main__":
  print('background asset unpack')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)
  unpack_asset(data)
