# GPL License
# (c) BlenderKit 2021
# This script is a modified version of the original script from BlenderKit
#
# This script is used to visualize the node graph of a material in Blender.
# It creates a new scene with a camera and a plane for each node in the material's node tree.
# The planes are positioned and scaled according to the nodes' positions and dimensions.
# The script also creates text objects for the nodes' names and links between the nodes.
# The camera is adjusted to cover all nodes and the scene is rendered.
# The rendered image is saved to the user's desktop.
# The script is intended to be used in Blender's scripting environment.

import bpy
import bmesh
from mathutils import *
import os
import tempfile
# from . import utils
def setup_scene(material_name):
    # Create a new scene with a clear name indicating it's for visualizing material nodes
    new_scene = bpy.data.scenes.new(name=f"{material_name}_Node_Visualization")
    bpy.context.window.scene = new_scene
    # Add background
    # bpy.ops.mesh.primitive_plane_add(size=100, location=(0, 0, -1))
    # bpy.context.object.name = f"{material_name}_Background"
    # bpy.context.object.data.materials.append(bpy.data.materials["Grey"])

    # Add an orthographic camera and name it properly
    bpy.ops.object.camera_add(location=(0, 0, 10))
    camera = bpy.context.object
    camera.name = f"{material_name}_Visualization_Camera"
    camera.data.name = f"{material_name}_Visualization_Camera_Data"
    camera.rotation_euler = (0, 0, 0)
    camera.data.type = 'ORTHO'
    new_scene.camera = camera

    return new_scene, camera

class drawNode:
    def __init__(self, node, scene, scale=0.01):
        self.node = node
        self.scene = scene
        self.scale = scale
        self.offs_x = 0
        self.offs_y = 0
        if node.parent is not None:
            self.offs_x = node.parent.location.x
            self.offs_y = node.parent.location.y
        self.node_width = node.width * scale
        # calculate node height from number of inputs root - stupid approximation but the .dimensions parameter
        # is not available when blender runs from command line
        self.node_height = len(node.inputs) * 0.2 + 0.6
        self.node_pos_x = (self.offs_x + node.location.x) * scale
        self.node_pos_y = (self.offs_y + node.location.y) * scale

        # Create a plane for each node and name it accordingly
        self.mesh = bpy.data.meshes.new(name=f"{node.name}_Plane")
        self.plane_obj = bpy.data.objects.new(name=f"{node.name}_Plane_Obj", object_data=self.mesh)
        self.scene.collection.objects.link(self.plane_obj)
        bpy.context.view_layer.objects.active = self.plane_obj
        self.plane_obj.location = (self.node_pos_x, self.node_pos_y, 0)
        self.bm = bmesh.new()
        self.bm.verts.new((0, 0, 0))
        self.bm.verts.new((0, -self.node_height, 0))
        self.bm.verts.new((self.node_width, -self.node_height, 0))
        self.bm.verts.new((self.node_width, 0, 0))
        self.bm.faces.new(self.bm.verts)
        self.bm.to_mesh(self.mesh)
        self.bm.free()
        #add bevel modifier with vertex option to make the edges round
        bpy.ops.object.modifier_add(type='BEVEL')
        bpy.context.object.modifiers["Bevel"].width = 0.2
        bpy.context.object.modifiers["Bevel"].segments = 10
        bpy.context.object.modifiers["Bevel"].affect = 'VERTICES'


        # Create a text object for the node's name and name it accordingly
        bpy.ops.object.text_add(location=(0.05, -0.2, 0.05))
        self.text_obj = bpy.context.object
        self.text_obj.name = f"{node.name}_Text"
        self.text_obj.data.name = f"{node.name}_Text_Data"
        self.text_obj.data.body = node.name
        self.text_obj.data.align_x = 'LEFT'
        self.text_obj.data.align_y = 'TOP'
        self.text_obj.data.size = .3
        self.text_obj.parent = self.plane_obj

        # For image nodes, create a text object with the image's name
        if node.type == 'TEX_IMAGE':
            bpy.ops.object.text_add(location=(0.05, -0.5, 0.05))
            self.text_obj1 = bpy.context.object
            self.text_obj1.name = f"{node.name}_Imagename"
            self.text_obj1.data.name = f"{node.name}_Imagename_Data"
            self.text_obj1.data.body = node.image.filepath.split(os.sep)[-1]
            self.text_obj1.data.align_x = 'LEFT'
            self.text_obj1.data.align_y = 'TOP'
            self.text_obj1.data.size = .3
            self.text_obj1.parent = self.plane_obj

def draw_link(start_pos, end_pos, scene):
    # Create a new curve
    curve_data = bpy.data.curves.new('link_curve', type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.fill_mode = 'FULL'
    curve_data.bevel_depth = 0.05

    # Add a new spline to the curve
    spline = curve_data.splines.new(type='BEZIER')
    spline.bezier_points.add(1)  # Two points total (start and end)

    # Assign positions to the start and end points
    spline.bezier_points[0].co = start_pos
    spline.bezier_points[0].handle_right = start_pos+ Vector((1, 0, 0))
    spline.bezier_points[0].handle_left = start_pos - Vector((1, 0, 0))
    spline.bezier_points[1].co = end_pos
    spline.bezier_points[1].handle_left = end_pos - Vector((1, 0, 0))
    spline.bezier_points[1].handle_right = end_pos + Vector((1, 0, 0))

    # Create a new object with the curve
    curve_obj = bpy.data.objects.new('Link', curve_data)
    scene.collection.objects.link(curve_obj)

    return curve_obj


def visualize_links(node_tree, viz_nodes, scene):
    links = []
    for link in node_tree.links:
        for n in viz_nodes:
            if n.node.name == link.from_node.name:
                from_node = n
            if n.node.name == link.to_node.name:
                to_node = n
        start_pos = Vector((from_node.node_pos_x + from_node.node_width, from_node.node_pos_y - 0.2,0))
        end_pos = Vector((to_node.node_pos_x, to_node.node_pos_y - to_node.node_height / 2 ,0))
        link = draw_link(start_pos, end_pos, scene)
        links.append(link)
    return links


def create_emit_material(name, color = (1,1,1,1)):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (200,0)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0,0)
    emission.inputs[0].default_value = color
    links.new(emission.outputs[0], output.inputs[0])
    return mat

def visualize_nodes(tempfolder,name, node_tree, scene):
    #this should be able to render material or geometry nodes, shading nodes e.t..c just anything.

    black = create_emit_material("Black", (0, 0, 0, 1))
    grey = create_emit_material("Grey", (0.5, 0.5, 0.5, 1))
    white = create_emit_material("White", (1, 1, 1, 1))
    orange = create_emit_material("Orange", (1, 0.5, 0, 1))
    dark_grey = create_emit_material("DarkGrey", (0.04, 0.04, 0.04, 1))

    new_scene, camera = setup_scene(name)

    min_x, max_x, min_y, max_y = (float('inf'), float('-inf'), float('inf'), float('-inf'))

    nodes = []
    for node in node_tree.nodes:
        if node.type == 'FRAME':
            continue

        viz_node = drawNode(node, new_scene)

        nodes.append(viz_node)
        # Update bounds for camera
        min_x = min(min_x, viz_node.node_pos_x)
        max_x = max(max_x, viz_node.node_pos_x + viz_node.node_width)
        min_y = min(min_y, viz_node.node_pos_y)
        max_y = max(max_y, viz_node.node_pos_y - viz_node.node_height)

    links = visualize_links(node_tree, nodes, new_scene)

    # set materials
    for n in nodes:
        n.plane_obj.data.materials.append(dark_grey)
        n.text_obj.data.materials.append(white)
        if n.node.type == 'TEX_IMAGE':
            n.text_obj1.data.materials.append(white)
    for l in links:
        l.data.materials.append(orange)

    # Adjust the camera to cover all nodes and name it
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    width = max_x - min_x
    height = max_y - min_y

    camera.location.x = center_x
    camera.location.y = center_y
    camera.data.ortho_scale = max(width, height) * 1.1  # Adding some padding for better framing

    # Add text object with material name in the upper left corner
    max_corner = max(width, height)
    bpy.ops.object.text_add(location=(center_x-max_corner/2, center_y+max_corner/2, 0))
    text_obj = bpy.context.object
    text_obj.name = f"{name}_Name"
    text_obj.data.name = f"{name}_Name_Data"
    text_obj.data.body = 'Material: ' + name
    text_obj.data.align_x = 'LEFT'
    text_obj.data.align_y = 'TOP'
    text_obj.data.size = .6
    text_obj.data.materials.append(white)

    # set fast render settings for quick render with cycles
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.device = 'GPU'
    bpy.context.scene.cycles.samples = 5

    # set output to square 1024x1024
    bpy.context.scene.render.resolution_x = 1024
    bpy.context.scene.render.resolution_y = 1024
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = 'WEBP'

    # set output path
    bpy.context.scene.render.filepath = os.path.join(tempfolder,
                                                     f"{name}_m")

    # Render the scene
    bpy.ops.render.render(write_still=True)

    # delete the scene
    bpy.data.scenes.remove(new_scene)


def visualize_material_nodes(material_name, tempfolder = None):
    # visualize all materials
    # make a copy of the materials, because we add some extra so that it does not mess up the original
    materials = bpy.data.materials[:]

    if material_name not in materials:
        print(f"Material '{material_name}' not found.")
        return
    material = bpy.data.materials[material_name]
    visualize_nodes(tempfolder, material_name, material.node_tree, bpy.context.scene)

def visualize_all_nodes(tempfolder = None, objects = None):
    # visualize all materials
    # make a copy of the materials, because we add some extra so that it does not mess up the original
    mts = []
    for ob in objects:
        if ob.type in ['MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'VOLUME']:
            for slot in ob.material_slots:
                if slot.material is not None and slot.material not in mts:
                    mts.append(slot.material)

    for material in mts:
        if material.use_nodes and material.node_tree is not None:
            visualize_nodes(tempfolder, material.name, material.node_tree, bpy.context.scene)

    # visualize all geometry nodes
    gngroups=[]
    for ob in objects:
        for modifier in ob.modifiers:
            if modifier.type == 'NODES':
                if modifier.node_group not in gngroups:
                    gngroups.append(modifier.node_group)

    for geometry_nodes in gngroups:
        if geometry_nodes.bl_idname == 'GeometryNodeTree':
            visualize_nodes(tempfolder, geometry_nodes.name, geometry_nodes, bpy.context.scene)
def activate_object(ob):
    # this deselects everything, selects the object and makes it active
    #bpy.ops.object.select_all(action='DESELECT')
    for ob in bpy.context.visible_objects:
        ob.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob

def save_uv_layouts(tempfolder, objects):
    # save uv layouts for all objects
    # select all objects
    for ob in objects:
        ob.select_set(True)

    # find an object that is mesh and make it active
    for obj in objects:
        if obj.type == 'MESH':
            bpy.context.view_layer.objects.active = obj
            break
    # go to edit mode
    bpy.ops.object.mode_set(mode='EDIT')
    # select all
    for ob in objects:
        ob.select_set(True)
    # save uv layout
    unique_meshes_obs = []
    for obj in objects:
        #check if object is mesh and has uv layers
        if obj.type == 'MESH' and obj.data.uv_layers.active is not None:
            if obj.data not in [o.data for o in unique_meshes_obs]:
                unique_meshes_obs.append(obj)
    # No UV = no svg
    if len(unique_meshes_obs) == 0:
        return

    filepath = os.path.join(tempfolder, f"Complete_asset_uv")
    #select all mesh elements to render complete uv layout
    activate_object(unique_meshes_obs[0])
    bpy.context.scene.update_tag()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.export_layout(filepath=filepath, export_all=False, modified=False, mode='SVG', size=(1024, 1024), opacity=0.25, check_existing=True)
    #now let's save all uv layouts separately for all mesh type objects in the asset:
    # we only need the 'common' UV layout when this happens
    if len(unique_meshes_obs) == 1:
        return

    for obj in unique_meshes_obs:
        activate_object(obj)
        bpy.context.scene.update_tag()
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        filepath = os.path.join(tempfolder, f"{obj.name}_uv")
        bpy.ops.uv.export_layout(filepath=filepath, export_all=True, modified=False, mode='SVG', size=(1024, 1024), opacity=0.25, check_existing=True)


def export_all_textures(tempfolder, objects):
    # export all textures
    unique_textures = []
    for ob in objects:
        if ob.type in ['MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'VOLUME']:
            for slot in ob.material_slots:
                if slot.material is not None:
                    for node in slot.material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE':
                            img = node.image
                            if img is not None:
                                if img not in unique_textures:
                                    unique_textures.append(img)
    for img in unique_textures:
        img.filepath = os.path.join(tempfolder, f"{img.name}")
        img.save()

def export_all_material_textures(tempfolder, material):
    # export all textures
    unique_textures = []
    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE':
            img = node.image
            if img is not None:
                if img not in unique_textures:
                    unique_textures.append(img)
    for img in unique_textures:
        img.filepath = os.path.join(tempfolder, f"{img.name}")
        img.save()
def visualize_and_save_all(tempfolder, objects):
    # first let's save all textures
    export_all_textures(tempfolder, objects)
    # save uv layouts
    save_uv_layouts(tempfolder, objects)
    # visualize all nodes
    visualize_all_nodes(tempfolder, objects)
