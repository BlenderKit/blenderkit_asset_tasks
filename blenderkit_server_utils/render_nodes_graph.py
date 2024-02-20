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

line_height = 0.5
text_scale = 0.25
line_scale = 2 * text_scale

text_x_offset = 0.1
class drawNode:
    def create_node_mesh(self):
        # Create a plane for each node and name it accordingly
        self.mesh = bpy.data.meshes.new(name=f"{self.node.name}_Plane")
        self.plane_obj = bpy.data.objects.new(name=f"{self.node.name}_Plane_Obj", object_data=self.mesh)
        self.scene.collection.objects.link(self.plane_obj)
        bpy.context.view_layer.objects.active = self.plane_obj
        self.plane_obj.location = self.position
        self.bm = bmesh.new()
        self.bm.verts.new((0, 0, 0))
        self.bm.verts.new((0, -self.node_height, 0))
        self.bm.verts.new((self.node_width, -self.node_height, 0))
        self.bm.verts.new((self.node_width, 0, 0))
        self.bm.faces.new(self.bm.verts)
        self.bm.to_mesh(self.mesh)
        self.bm.free()
        # add bevel modifier with vertex option to make the edges round
        bpy.ops.object.modifier_add(type='BEVEL')
        bpy.context.object.modifiers["Bevel"].width = 0.2
        bpy.context.object.modifiers["Bevel"].segments = 10
        bpy.context.object.modifiers["Bevel"].affect = 'VERTICES'
        # assign material
        self.plane_obj.data.materials.append(bpy.data.materials["DarkGrey"])

        # Apply bevel modifier, go to edit mode, slect all, inset by 0.1, invert selection, assign a new material slot and assign it to orange
        bpy.ops.object.modifier_apply(modifier="Bevel")
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        # set selection to faces
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.inset(thickness=0.02, depth=0)
        # invert selection
        bpy.ops.mesh.select_all(action='INVERT')
        # assign a new material slot
        bpy.ops.object.material_slot_add()
        # assign it to orange
        bpy.context.object.active_material_index = 1
        bpy.context.object.active_material = bpy.data.materials["Orange"]
        bpy.ops.object.material_slot_assign()
        bpy.ops.object.mode_set(mode='OBJECT')

    def count_used_inputs(self):
        i=0
        for input in self.node.inputs:
            if len(input.links) > 0:
                i+=1
        return i
    def __init__(self, node, scene, scale=0.01):
        self.node = node
        self.scene = scene
        self.scale = scale
        self.offs_x = 0
        self.offs_y = 0
        if node.parent is not None:
            self.offs_x = node.parent.location.x
            self.offs_y = node.parent.location.y


        node_pos_x = (self.offs_x + node.location.x) * scale
        node_pos_y = (self.offs_y + node.location.y) * scale
        self.position = Vector((node_pos_x, node_pos_y, 0))
        # Reroute is special, has no text, no size, no plane
        if node.type == 'REROUTE':
            self.node_width = 0
            self.node_height = 0
            self.input_pos = Vector((0, 0, 0))
            self.output_pos = Vector((0, 0, 0))
            return

        # calculate node width and height
        self.node_width = node.width * scale
        # calculate node height from number of inputs root - stupid approximation but the .dimensions parameter
        # is not available when blender runs from command line

        # count inputs with links
        self.node_height = (2 + self.count_used_inputs()) * line_height + 0.6

        # create the mesh
        self.create_node_mesh()
        #add texts
        self.text_obj = self.add_text(node.name, (0, -0.2), 'LEFT', 'TOP', text_scale)
        if node.type == 'TEX_IMAGE':
            self.text_obj1 = self.add_text(node.image.filepath.split(os.sep)[-1], (0, -0.5), 'LEFT', 'TOP', text_scale)
        # add start end position for node links:
        self.output_pos = Vector((self.node_width, - 0.2,0))
        self.input_pos = Vector((0, - 1,0))

    def add_text(self, text, position, alignment_x='LEFT', alignment_y='TOP', size=0.3):
        """
        Adds a text object to the node at the specified position.
        """
        # Adjust position to parent first
        bpy.ops.object.text_add(location=(position[0]+text_x_offset, position[1], 0.05))
        text_obj = bpy.context.object
        text_name = f"{self.node.name}_{text.replace(' ', '_')}"
        text_obj.name = text_name
        text_obj.data.name = f"{text_name}_Data"
        text_obj.data.body = text
        text_obj.data.align_x = alignment_x
        text_obj.data.align_y = alignment_y
        text_obj.data.size = size
        text_obj.parent = self.plane_obj
        # add material
        text_obj.data.materials.append(bpy.data.materials["White"])
        return text_obj

def draw_link(start_pos, end_pos, scene):
    # Create a new curve
    curve_data = bpy.data.curves.new('link_curve', type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.fill_mode = 'FULL'
    curve_data.bevel_depth = 0.02

    # Add a new spline to the curve
    spline = curve_data.splines.new(type='BEZIER')
    spline.bezier_points.add(1)  # Two points total (start and end)

    # Assign positions to the start and end points
    spline.bezier_points[0].co = start_pos
    spline.bezier_points[0].handle_right = start_pos+ Vector((.5, 0, 0))
    spline.bezier_points[0].handle_left = start_pos - Vector((.5, 0, 0))
    spline.bezier_points[1].co = end_pos
    spline.bezier_points[1].handle_left = end_pos - Vector((.5, 0, 0))
    spline.bezier_points[1].handle_right = end_pos + Vector((.5, 0, 0))

    # Create a new object with the curve
    curve_obj = bpy.data.objects.new('Link', curve_data)
    scene.collection.objects.link(curve_obj)
    #set resolution of the curve to 25
    curve_obj.data.resolution_u = 25
    #set material
    curve_obj.data.materials.append(bpy.data.materials["Orange"])
    #add a small sphere at start and end
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.08, location=start_pos + Vector((0,0,0.1)))
    bpy.context.object.data.materials.append(bpy.data.materials["Orange"])

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.08, location=end_pos + Vector((0,0,0.1)))
    bpy.context.object.data.materials.append(bpy.data.materials["Orange"])

    return curve_obj


def visualize_links(node_tree, viz_nodes, scene, scale=0.01):
    links = []
    scale = 2
    for link in node_tree.links:
        # Find the corresponding visual nodes
        from_viz_node = next((n for n in viz_nodes if n.node == link.from_node), None)
        to_viz_node = next((n for n in viz_nodes if n.node == link.to_node), None)

        if from_viz_node and to_viz_node:
            # Calculate the vertical offset for the output link start
            from_socket_index = list(from_viz_node.node.outputs).index(link.from_socket)
            from_socket_offset = (from_viz_node.node_height / max(len(from_viz_node.node.outputs),
                                                                  1)) * from_socket_index

            # Calculate the vertical offset for the input link end
            to_socket_index=0
            for to_socket in to_viz_node.node.inputs:
                if to_socket.identifier == link.to_socket.identifier:
                    break
                if len(to_socket.links) > 0:
                    to_socket_index+=1

            #to_socket_offset = (to_viz_node.node_height / max(len(to_viz_node.node.inputs), 1)) * to_socket_index
            # Calculate the vertical offset for the input link end
            end_pos = Vector((to_viz_node.input_pos.x,
                              to_viz_node.input_pos.y - to_socket_index * line_scale , -0.1)) #- to_viz_node.position
            start_pos = Vector((from_viz_node.output_pos.x, from_viz_node.output_pos.y, -0.1)) #- from_viz_node.position
            if to_viz_node.node.type == 'REROUTE':
                end_pos = to_viz_node.input_pos
            else:
                input_text = to_viz_node.add_text(link.to_socket.identifier, end_pos, 'LEFT', 'TOP', text_scale)
            if from_viz_node.node.type == 'REROUTE':
                start_pos = from_viz_node.output_pos

            offset = Vector((0, - text_scale * line_scale *.5,0))
            # Draw the link
            link_obj = draw_link(start_pos + from_viz_node.position, end_pos + to_viz_node.position  + offset, scene)
            links.append(link_obj)
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
    #emission node
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0,0)
    emission.inputs[0].default_value = color
    #transparent shader node with max transparency
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200,0)
    #mix shader node
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (0,-200)
    #mix ratio to defined alpha from color
    mix.inputs[0].default_value = color[3]
    #link nodes
    links.new(transparent.outputs[0], mix.inputs[1])

    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs[0])
    return mat

def visualize_nodes(tempfolder,name, node_tree, scene):
    #this should be able to render material or geometry nodes, shading nodes e.t..c just anything.

    black = create_emit_material("Black", (0, 0, 0, 1))
    grey = create_emit_material("Grey", (0.5, 0.5, 0.5, 1))
    white = create_emit_material("White", (1, 1, 1, 1))
    orange = create_emit_material("Orange", (.7, 0.35, 0, 1))
    dark_grey = create_emit_material("DarkGrey", (0.04, 0.04, 0.04, 0.7))

    new_scene, camera = setup_scene(name)

    min_x, max_x, min_y, max_y = (float('inf'), float('-inf'), float('inf'), float('-inf'))

    nodes = []
    for node in node_tree.nodes:
        if node.type == 'FRAME':
            continue

        viz_node = drawNode(node, new_scene)

        nodes.append(viz_node)
        # Update bounds for camera
        min_x = min(min_x, viz_node.position.x)
        max_x = max(max_x, viz_node.position.x + viz_node.node_width)
        min_y = min(min_y, viz_node.position.y)
        max_y = max(max_y, viz_node.position.y - viz_node.node_height)

    links = visualize_links(node_tree, nodes, new_scene)


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
    bpy.context.scene.cycles.use_denoising = False

    # set output to square 1024x1024
    bpy.context.scene.render.resolution_x = 2048
    bpy.context.scene.render.resolution_y = 2048
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

    if material_name not in bpy.data.materials:
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


def activate_object(aob):
    # this deselects everything, selects the object and makes it active
    for obj in bpy.context.visible_objects:
        obj.select_set(False)
    aob.select_set(True)
    bpy.context.view_layer.objects.active = aob

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
    for obj in unique_meshes_obs:
        obj.select_set(True)
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
        print('export uv layout for', obj.name, obj.data.name)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        #set back to object mode
        bpy.ops.object.mode_set(mode='OBJECT')
        #back to edit mode
        bpy.ops.object.mode_set(mode='EDIT')
        filepath = os.path.join(tempfolder, f"{obj.name}_uv")
        bpy.ops.uv.export_layout(filepath=filepath, export_all=True, modified=False, mode='SVG', size=(1024, 1024), opacity=0.25, check_existing=False)


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
