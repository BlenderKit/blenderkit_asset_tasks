import os
import sys
import math

import bpy
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


def link_collection(
        file_name, obnames=[], location=(0, 0, 0), link=False, parent=None, **kwargs
):
    """link an instanced group - model type asset"""
    sel = utils.selection_get()

    with bpy.data.libraries.load(file_name, link=link, relative=True) as (
            data_from,
            data_to,
    ):
        scols = []
        for col in data_from.collections:
            if col == kwargs["name"]:
                data_to.collections = [col]

    rotation = (0, 0, 0)
    if kwargs.get("rotation") is not None:
        rotation = kwargs["rotation"]

    bpy.ops.object.empty_add(type="PLAIN_AXES", location=location, rotation=rotation)
    main_object = bpy.context.view_layer.objects.active
    main_object.instance_type = "COLLECTION"

    if parent is not None:
        main_object.parent = bpy.data.objects.get(parent)

    main_object.matrix_world.translation = location

    for col in bpy.data.collections:
        if col.library is not None:
            fp = bpy.path.abspath(col.library.filepath)
            fp1 = bpy.path.abspath(file_name)
            if fp == fp1:
                main_object.instance_collection = col
                break

    # sometimes, the lib might already  be without the actual link.
    if not main_object.instance_collection and kwargs["name"]:
        col = bpy.data.collections.get(kwargs["name"])
        if col:
            main_object.instance_collection = col

    main_object.name = main_object.instance_collection.name

    # bpy.ops.wm.link(directory=file_name + "/Collection/", filename=kwargs['name'], link=link, instance_collections=True,
    #                 autoselect=True)
    # main_object = bpy.context.view_layer.objects.active
    # if kwargs.get('rotation') is not None:
    #     main_object.rotation_euler = kwargs['rotation']
    # main_object.location = location

    utils.selection_set(sel)
    return main_object, []


def add_text_line(strip, text):
    bpy.data.scenes["Composite"].sequence_editor.sequences_all[strip].text += text + 10 * '    '


def writeout_param(asset_data, param_name):
    pl = utils.get_param(asset_data, param_name)
    if pl is not None:
        add_text_line('asset', f'{param_name}:{pl}')


def set_text(strip, text):
    bpy.data.scenes["Composite"].sequence_editor.sequences_all[strip].text = text


def scale_cameras(asset_data):
    params = asset_data['dictParameters']
    minx = params['boundBoxMinX']
    miny = params['boundBoxMinY']
    minz = params['boundBoxMinZ']
    maxx = params['boundBoxMaxX']
    maxy = params['boundBoxMaxY']
    maxz = params['boundBoxMaxZ']

    dx = (maxx - minx)
    dy = (maxy - miny)
    dz = (maxz - minz)

    print(dx, dy, dz)

    r = math.sqrt(dx * dx + dy * dy + dz * dz)
    r *= 1.2
    scaler = bpy.data.objects['scaler']
    scaler.scale = (r, r, r)
    scaler.location.z = (maxz + minz) / 2

    # get scene camera
    cam = bpy.data.objects['Camera']
    # Set ortho scale to max of dimensions
    cam.data.ortho_scale = max(dx, dy, dz) * 1.1

    # let's keep floor where it should be! so this is commented out:
    # floor = bpy.data.objects['floor']
    # floor.location.z = minz

    # camZ = s.camera.parent.parent
    # camZ.location.z = (maxz - minz) / 2
    # dx = (maxx - minx)
    # dy = (maxy - miny)
    # dz = (maxz - minz)
    # r = math.sqrt(dx * dx + dy * dy + dz * dz)
    #
    # scaler = bpy.context.view_layer.objects['scaler']
    # scaler.scale = (r, r, r)
    # coef = .7
    # r *= coef
    # camZ.scale = (r, r, r)
    bpy.context.view_layer.update()


def check_for_flat_faces():
    for ob in bpy.context.scene.objects:
        if ob.type == 'MESH':
            for f in ob.data.polygons:
                if not f.use_smooth:
                    return True
    return False


def mark_freestyle_edges():
    for m in bpy.data.meshes:
        for e in m.edges:
            e.use_freestyle_mark = True


def set_asset_data_texts(asset_data):
    set_text('asset', '')
    add_text_line('asset', asset_data['name'])
    dx = utils.get_param(asset_data, 'dimensionX')
    dy = utils.get_param(asset_data, 'dimensionY')
    dz = utils.get_param(asset_data, 'dimensionZ')
    dim_text = f"Dimensions:{dx}x{dy}x{dz}m"
    add_text_line('asset', dim_text)
    fc = utils.get_param(asset_data, 'faceCount', 1)
    fcr = utils.get_param(asset_data, 'faceCountRender', 1)

    add_text_line('asset', f"fcount {fc} render {fcr}")

    if check_for_flat_faces():
        add_text_line('asset', 'Flat faces detected')

    writeout_param(asset_data, 'productionLevel')
    writeout_param(asset_data, 'shaders')
    writeout_param(asset_data, 'modifiers')
    writeout_param(asset_data, 'meshPolyType')
    writeout_param(asset_data, 'manifold')
    writeout_param(asset_data, 'objectCount')
    writeout_param(asset_data, 'nodeCount')
    writeout_param(asset_data, 'textureCount')
    writeout_param(asset_data, 'textureResolutionMax')


def set_scene(name=''):
    print(f'setting scene {name}')
    bpy.context.window.scene = bpy.data.scenes[name]
    c = bpy.context.scene.objects.get('Camera')
    if c is not None:
        bpy.context.scene.camera = c
    bpy.context.view_layer.update()
    # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def set_view_shading(shading_type='RENDERED', face_orientation=False, wireframe=False):
    # bpy.data.workspaces['Layout'].screens['Layout'].areas[4].spaces[0].shading
    for w in bpy.data.workspaces:
        for a in w.screens[0].areas:
            if a.type == 'VIEW_3D':
                for s in a.spaces:
                    if s.type == 'VIEW_3D':
                        s.shading.type = shading_type
                        s.overlay.show_wireframes = wireframe
                        s.overlay.show_face_orientation = face_orientation
    bpy.context.scene.display.shading.type = shading_type
    # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def set_workspace(name='Layout'):
    for a in range(0, 2):
        bpy.context.window.workspace = bpy.data.workspaces[name]
        bpy.context.workspace.update_tag()
        bpy.context.view_layer.update()
        # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

def switch_off_all_modifiers():
    #switches off all modifiers for render in the scene and stores and returns them in a list with original state.
    original_states = []
    for ob in bpy.context.scene.objects:
        if ob.type == 'MESH':
            for m in ob.modifiers:
                original_states.append((ob, m, m.show_render))
                m.show_render = False
    return original_states

def switch_on_all_modifiers(original_states):
    #switches on all modifiers for render in the scene and restores them to the original state.
    for ob, m, state in original_states:
        m.show_render = state

def add_geometry_nodes_to_all_objects(group = 'wireNodes', dimensions = 1):
    #takes all visible objects in the scene and adds geometry nodes modifier with the group to them.
    #avoids objects with more than 300k face.
    for ob in bpy.context.scene.objects:
        if ob.type == 'MESH' and ob.visible_get() and len(ob.data.polygons) < 300000:
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.modifier_add(type='NODES')
            m = bpy.context.object.modifiers[-1]
            m.node_group = bpy.data.node_groups[group]
            #asset dimensions needed
            m["Socket_0"] = float(dimensions)

def remove_geometry_nodes_from_all_objects(group = 'wireNodes'):
    #takes all visible objects in the scene and removes geometry nodes modifier with the group to them.
    for ob in bpy.context.scene.objects:
        if ob.type == 'MESH' and ob.visible_get() and len(ob.data.polygons) < 300000:
            bpy.context.view_layer.objects.active = ob
            # check if the modifier is there
            for m in ob.modifiers:
                if m.type == 'NODES' and m.node_group.name == group:
                    bpy.context.object.modifiers.remove(m)
def render_model_validation( asset_data, filepath):
    # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    # render basic render
    set_scene('Render')
    # set_view_shading(shading_type='RENDERED')
    # set_workspace('Render')
    bpy.ops.render.render(animation=True)
    # bpy.ops.render.opengl(animation=True, view_context=True)

    # render the Mesh checker
    # now in render
    set_scene('Mesh_checker')
    # freestyle is crazy slow. Need better edge render :(
    # mark_freestyle_edges()

    # set_view_shading(shading_type='MATERIAL', wireframe = True, face_orientation=True)
    # set_workspace('Mesh_checker')
    bpy.ops.render.render(animation=True)
    # bpy.ops.render.opengl(animation=True, view_context=False)

    # set_scene('Bevel_checker')
    # bpy.ops.render.render(animation=True)

    # render the UV Checker
    # now in render
    # set_scene('UV_checker')
    # bpy.ops.render.render(animation=True, write_still=True)

    #switch off modifiers for this one
    set_scene('Mesh_checker_no_modif')
    original_states = switch_off_all_modifiers()
    dimensionX = utils.get_param(asset_data, 'dimensionX')
    dimensionY = utils.get_param(asset_data, 'dimensionY')
    dimensionZ = utils.get_param(asset_data, 'dimensionZ')
    # Max length is taken as the dimension of the asset
    dimensions = max(dimensionX, dimensionY, dimensionZ)
    add_geometry_nodes_to_all_objects(group='wireNodes', dimensions=dimensions)
    bpy.ops.render.render(animation=True)
    remove_geometry_nodes_from_all_objects(group='wireNodes')
    switch_on_all_modifiers(original_states)
    # switch to composite and render video
    #No video, in this one we render only large stills
    # set_scene('Composite')
    #
    # bpy.context.scene.render.filepath = filepath
    # print(filepath)
    # # bpy.context.view_layer.update()
    # # bpy.context.scene.update_tag()
    # # bpy.context.view_layer.update()
    # print(f'rendering validation preview for {asset_data["name"]}')
    # bpy.ops.render.render(animation=True, write_still=True)

def export_gltf(filepath=''):
    bpy.ops.export_scene.gltf(filepath=filepath, check_existing=True, export_import_convert_lighting_mode='SPEC',
                              gltf_export_id="", export_format='GLB', ui_tab='GENERAL', export_copyright="",
                              export_image_format='AUTO', export_image_add_webp=True, export_image_webp_fallback=False,
                              export_texture_dir="", export_jpeg_quality=50, export_image_quality=50,
                              export_keep_originals=False, export_texcoords=True, export_normals=True,
                              export_draco_mesh_compression_enable=False, export_draco_mesh_compression_level=6,
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
def render_asset_bg(data):
    asset_data = data['asset_data']
    set_scene('Empty_start')

    # first lets build the filepath and find out if its already rendered?
    s = bpy.context.scene

    utils.enable_cycles_CUDA()

    # first clean up all scenes.
    for s in bpy.data.scenes:
        c = s.collection
        for ob in c.objects:
            if ob.instance_collection:
                c.objects.unlink(ob)

    for c in bpy.data.collections:
        for ob in c.objects:
            if ob.instance_collection:
                c.objects.unlink(ob)
    bpy.ops.outliner.orphans_purge()
    # if i ==1:
    #     fal

    fpath = data["file_path"]
    if fpath:
        try:
            parent, new_obs = link_collection(fpath,
                                                          location=(0, 0, 0),
                                                          rotation=(0, 0, 0),
                                                          link=True,
                                                          name=asset_data['name'],
                                                          parent=None)

            # we need to realize for UV , texture, and nodegraph exports here..
            utils.activate_object(parent)

            bpy.ops.object.duplicates_make_real(use_base_parent=True, use_hierarchy=True)
            all_obs = bpy.context.selected_objects[:]
            bpy.ops.object.make_local(type='ALL')

        except Exception as e:
            print(e)
            print('failed to append asset')
            return
        for s in bpy.data.scenes:
            if s != bpy.context.scene:
                # s.collection.objects.link(parent)
                #try link all already realized.
                for ob in all_obs:
                    s.collection.objects.link(ob)

        gltf_path = os.path.join(data['result_folder'], f"GLTF_{asset_data['name']}.glb")
        export_gltf(filepath=gltf_path)


        set_asset_data_texts(asset_data)

        scale_cameras(asset_data)

        #save the file to temp folder, so all files go there.
        blend_file_path = os.path.join((data['temp_folder']), f"{asset_data['name']}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_file_path, compress=False, copy=False, relative_remap=False)

        #first render the video
        render_model_validation( asset_data, data['result_filepath'])
        #then render the rest, since that makes total mess in the file...
        render_nodes_graph.visualize_and_save_all(tempfolder=data['result_folder'], objects=all_obs)


if __name__ == "__main__":
    print('background resolution generator')
    datafile = sys.argv[-1]
    with open(datafile, 'r', encoding='utf-8') as f:
        data = json.load(f)
    render_asset_bg(data)
