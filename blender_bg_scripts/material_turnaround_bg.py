import bpy
import os
import sys
import json

# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)

from blenderkit_server_utils import paths, utils, render_nodes_graph

def getNode(mat, type):
  for n in mat.node_tree.nodes:
    if n.type == type:
      return n
  return None


def set_text(ob, text):
  print(bpy.data.objects.keys())
  textob = bpy.data.objects[ob]
  textob.data.body = str(text)




def render_material_turnaround(mat, asset_data, filepath):
  l = bpy.context.view_layer

  mat1 = None
  for ob in bpy.context.scene.objects:
    if ob.name[:15] == 'MaterialPreview':
      ob.material_slots[0].material = mat

  bpy.context.scene.render.filepath = filepath
  bpy.context.view_layer.update()
  bpy.context.scene.update_tag()
  bpy.context.view_layer.update()

  # enable GPU rendering
  preferences = bpy.context.preferences
  cycles_preferences = preferences.addons['cycles'].preferences
  cycles_preferences.compute_device_type = 'CUDA'
  cycles_preferences.devices[0].use = True

  utils.enable_cycles_CUDA()
  bpy.ops.render.render(animation=True)

  
def append_material(file_name, matname=None, link=False, fake_user=True):
    """append a material type asset

    first, we have to check if there is a material with same name
    in previous step there's check if the imported material
    is already in the scene, so we know same name != same material
    """

    mats_before = bpy.data.materials[:]
    try:
      with bpy.data.libraries.load(file_name, link=link, relative=True) as (
              data_from,
              data_to,
      ):
        found = False
        for m in data_from.materials:
          if m == matname or matname is None:
            data_to.materials = [m]
            matname = m
            found = True
            break

        # not found yet? probably some name inconsistency then.
        if not found and len(data_from.materials) > 0:
          data_to.materials = [data_from.materials[0]]
          matname = data_from.materials[0]
          print(
            f"the material wasn't found under the exact name, appended another one: {matname}"
          )

    except Exception as e:
      print(f"{e} - failed to open the asset file")
    # we have to find the new material , due to possible name changes
    mat = None
    for m in bpy.data.materials:
      if m not in mats_before:
        mat = m
        break
    # still not found?
    if mat is None:
      mat = bpy.data.materials.get(matname)

    if fake_user:
      mat.use_fake_user = True
    return mat

def purge():
  for mat in bpy.data.materials:
    mat.use_fake_user = False
  bpy.ops.outliner.orphans_purge()

def render_uploaded_material(data):
  asset_data = data['asset_data']
  result_filepath = data['result_filepath']
  #this can render more assets in one run, but let's skip that for simplicity for each render the file needs to be open now separately

  try:
    mat = append_material(file_name=data['file_path'])
  except:
    return
  mat.use_fake_user = False

  render_material_turnaround(mat, asset_data, result_filepath)

if __name__ == "__main__":
  print('background material turnaround generator')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)
  render_uploaded_material(data)