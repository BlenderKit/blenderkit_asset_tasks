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




def render_material_validation(mat, asset_data, filepath):
  l = bpy.context.view_layer

  mat1 = None
  for ob in bpy.context.scene.objects:
    if ob.name[:15] == 'MaterialPreview':
      ob.material_slots[0].material = mat

      if len(ob.material_slots) > 1:

        if not mat1:
          l.objects.active = ob
          mat1 = mat.copy()
          principled = getNode(mat1, 'BSDF_PRINCIPLED')
          output = getNode(mat1, 'OUTPUT_MATERIAL')
          if principled:

            inlinks = principled.inputs['Normal'].links
            if len(inlinks) > 0:
              inl = inlinks[0]
              n1 = mat1.node_tree.nodes.new('ShaderNodeVectorMath')
              n1.inputs[1].default_value = (.5, .5, .5)
              n1.operation = 'MULTIPLY'
              n2 = mat1.node_tree.nodes.new('ShaderNodeVectorMath')
              n2.inputs[1].default_value = (.5, .5, .5)
              n2.operation = 'ADD'
              new_link = mat1.node_tree.links.new(inl.from_socket, n1.inputs[0])
              new_link = mat1.node_tree.links.new(n1.outputs[0], n2.inputs[0])
              new_link = mat1.node_tree.links.new(n2.outputs[0], output.inputs['Surface'])

        ob.material_slots[1].material = mat1

  # write name
  set_text('name_info',
           f"{asset_data['author']['firstName']}_{asset_data['author']['lastName']} / {asset_data['name']}_{asset_data['author']['firstName']}_{asset_data['author']['lastName']}")

  displacement_node = getNode(mat1, 'DISPLACEMENT')
  if displacement_node:
    disp = displacement_node.inputs['Scale'].default_value
    set_text('displacement_info', f'{disp:.3f} m')
  else:
    set_text('displacement_info', 'none')

  if principled:
    sss = principled.inputs['Subsurface Weight'].default_value
    set_text('sss_intensity_info', sss)
    if sss > 0:
      sssr = principled.inputs['Subsurface Radius'].default_value
      sssrs = principled.inputs['Subsurface Scale'].default_value
      # radius gets multiplied by scale
      t = f'{sssr[0] * sssrs:.2f} {sssr[1]*sssrs:.2f} {sssr[2]*sssrs:.2f} m\n'

      set_text('sss_radius_info', t)
    else:
      set_text('sss_radius_info', 'none')
  else:
    set_text('sss_radius_info', 'none')
    set_text('sss_radius_info', 'none')

  textob = bpy.data.objects['material_info']
  textob.data.body = ''
  for p in asset_data['dictParameters'].keys():
    pt = p
    pv = str(asset_data['dictParameters'][p])
    textob.data.body += pt + '\n' + pv + '\n'
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
  bpy.ops.render.render(write_still=True)

  
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
  # for i, asset_data in enumerate(assets):

  if os.path.exists(result_filepath + '.jpg'):
    print(f'image already existsfor {asset_data["name"]}')
    return

  try:
    mat = append_material(file_name=data['file_path'])
  except:
    return
  mat.use_fake_user = False

  render_material_validation(mat, asset_data, result_filepath)
  
  render_nodes_graph.visualize_nodes(data['result_folder'], mat.name, mat.node_tree, bpy.context.scene)
  render_nodes_graph.export_all_material_textures(data['result_folder'], mat)


if __name__ == "__main__":
  print('background resolution generator')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)
  render_uploaded_material(data)