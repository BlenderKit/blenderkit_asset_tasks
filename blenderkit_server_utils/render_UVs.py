import bpy
import numpy as np


# Sets up the camera within the given scene for rendering the UV layout.
def setup_scene_camera(scene):
    # Create a new camera object and add it to the scene.
    camera_data = bpy.data.cameras.new("UVLayoutCam")
    camera_object = bpy.data.objects.new("UVLayoutCam", camera_data)
    scene.collection.objects.link(camera_object)
    scene.camera = camera_object

    # Configure the camera to use orthographic projection,
    # making it suitable for 2D UV layout rendering.
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = 1  # Adjust based on the size of your UV meshes.
    camera_object.location = (0.5, 0.5, 1)  # Position the camera to capture all UVs.


# Configures rendering settings for the scene, including output format and file path.
def set_render_settings(scene, filepath):
    # Enable transparency in the final render to accommodate for transparent materials.
    scene.render.film_transparent = True
    # Use the Cycles render engine for high-quality rendering.
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 5  # Reduce samples for faster rendering of simple scenes.

    # Set output format to WEBP, resolution, and file path for saving the render.
    scene.render.image_settings.file_format = 'WEBP'
    scene.render.image_settings.color_mode = 'RGB'

    scene.render.image_settings.quality = 60
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.filepath = filepath


# Renders the scene and saves the output to the specified file path.
def render_and_save(scene):
    bpy.context.window.scene = scene
    bpy.ops.render.render(write_still=True)


# Cleans up by removing the temporary scene and its objects after rendering.
def cleanup_scene(scene):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in scene.objects:
        obj.select_set(True)
    bpy.ops.object.delete()  # Delete all objects in the scene.
    bpy.data.scenes.remove(scene)  # Remove the temporary scene.


# Utility function to set the active scene and camera, ensuring correct rendering settings.
def set_scene(name=''):
    print(f'setting scene {name}')
    bpy.context.window.scene = bpy.data.scenes[name]
    c = bpy.context.scene.objects.get('Camera')
    if c is not None:
        bpy.context.scene.camera = c
    bpy.context.view_layer.update()


# The main function to export UV layouts of selected objects as WEBP images.
def export_uvs_as_webps(obs, filepath):
    original_scene = bpy.context.scene
    uv_scene = bpy.data.scenes.new("UVScene")  # Create a new scene for UV rendering.
    set_scene(name='UVScene')
    setup_scene_camera(uv_scene)
    build_uv_meshes(obs, uv_scene)  # Generate mesh representations of UVs.
    set_render_settings(uv_scene, filepath)
    render_and_save(uv_scene)
    cleanup_scene(uv_scene)
    bpy.context.window.scene = original_scene  # Revert to the original scene.


# Retrieves or creates a material designed for rendering UV layouts.
def get_UV_material():
    m = bpy.data.materials.get('UV_RENDER_MATERIAL')
    if m is None:
        m = bpy.data.materials.new('UV_RENDER_MATERIAL')
        m.use_nodes = True
        nodes = m.node_tree.nodes
        links = m.node_tree.links
        nodes.clear()  # Start with a fresh node setup.

        # Set up nodes for a material that's partially transparent and emissive.
        emission_node = nodes.new(type='ShaderNodeEmission')
        emission_node.inputs['Color'].default_value = (1, 1, 1, 1)  # White color for emission.
        emission_node.inputs['Strength'].default_value = 1.0  # Emission strength.

        transparent_node = nodes.new(type='ShaderNodeBsdfTransparent')

        mix_shader_node = nodes.new(type='ShaderNodeMixShader')
        mix_shader_node.inputs['Fac'].default_value = 0.05  # Control the mix between transparent and emission.

        material_output_node = nodes.new('ShaderNodeOutputMaterial')

        # Connect the nodes to set up the material.
        links.new(emission_node.outputs['Emission'], mix_shader_node.inputs[2])
        links.new(transparent_node.outputs['BSDF'], mix_shader_node.inputs[1])
        links.new(mix_shader_node.outputs['Shader'], material_output_node.inputs['Surface'])

    return m


def build_uv_meshes(obs, scene):
    m = get_UV_material()  # Retrieve or create the UV layout rendering material.
    i = 0  # Counter to slightly offset each UV mesh object for visibility.

    for ob in obs:
        me = ob.data  # The mesh data of the object.

        # Skip objects without UV layers.
        if len(ob.data.uv_layers) == 0 or ob.data.uv_layers.active is None or len(ob.data.uv_layers.active.data) == 0:
            continue

        uv_layer = me.uv_layers.active  # The active UV layer of the mesh.

        # Retrieve UV coordinates.
        uvs = np.empty((2 * len(me.loops), 1))
        uv_layer.data.foreach_get("uv", uvs)
        x, y = uvs.reshape((-1, 2)).T
        z = np.zeros(len(x))  # Create a Z-axis array filled with zeros for 2D UV layout.

        # Create a new mesh for the UV layout.
        uvme = bpy.data.meshes.new("UVMesh_" + ob.name)
        verts = np.array((x, y, z)).T  # Combine x, y, z coordinates into vertices.
        faces = [p.loop_indices for p in me.polygons]  # Create faces from the polygons of the original mesh.

        # Convert UV data to mesh data.
        uvme.from_pydata(verts, [], faces)

        # Create a new object for the UV mesh and link it to the scene.
        uv_object = bpy.data.objects.new("UVMesh_" + ob.name, uvme)
        scene.collection.objects.link(uv_object)

        # Assign the previously created UV material to the new object.
        uv_object.data.materials.append(m)

        # Select and activate the UV object for further operations.
        bpy.context.view_layer.objects.active = uv_object
        uv_object.select_set(True)

        # Offset each UV object slightly on the Z-axis to prevent z-fighting in the render.
        uv_object.location.z -= i * 0.01
        i += 1

        if len(uv_object.data.vertices) < 50000:
            # Duplicate the object to apply a wireframe modifier for visual distinction of edges.
            # only do this for smaller objects.
            bpy.ops.object.duplicate()
            bpy.ops.object.modifier_add(type='WIREFRAME')

            # Adjust the wireframe modifier to make the lines very thin.
            bpy.context.object.modifiers["Wireframe"].thickness = 0.001

