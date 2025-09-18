"""Utility helpers for rendering UV layouts as flat WEBP images.

This module builds a temporary scene, projects UV coordinates into 3D space
as a set of mesh objects (one per source mesh) and performs a quick Cycles
render with an emissive material.

Refactor hygiene applied:
 - Added type hints.
 - Introduced structured logging (replace prints).
 - Added/expanded docstrings.
 - Prepared for safer ops (future exception handling) without changing behavior.
"""  # noqa: N999

from __future__ import annotations

from collections.abc import Iterable

import bpy

from . import log

logger = log.create_logger(__name__)


WIRE_FRAME_VERTEX_THRESHOLD = 50_000


def setup_scene_camera(scene: bpy.types.Scene) -> None:
    """Create and configure an orthographic camera in the provided scene.

    The camera is centered over the unit square where UV meshes are placed.
    """
    camera_data = bpy.data.cameras.new("UVLayoutCam")
    camera_object = bpy.data.objects.new("UVLayoutCam", camera_data)
    scene.collection.objects.link(camera_object)
    scene.camera = camera_object
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 1
    camera_object.location = (0.5, 0.5, 1)


def set_render_settings(scene: bpy.types.Scene, filepath: str) -> None:
    """Configure render settings for transparent WEBP output.

    Args:
        scene: Scene to configure.
        filepath: Absolute (or Blender-relative) output filepath.
    """
    scene.render.film_transparent = True
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 5
    scene.render.image_settings.file_format = "WEBP"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.quality = 60
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.filepath = filepath


def render_and_save(scene: bpy.types.Scene) -> None:
    """Render the supplied scene writing the still image to its configured filepath."""
    bpy.context.window.scene = scene
    bpy.ops.render.render(write_still=True)


def cleanup_scene(scene: bpy.types.Scene) -> None:
    """Remove all objects from a temporary scene and delete the scene.

    Swallows Blender operator runtime errors while logging them; callers
    should still restore the original window scene afterwards.
    """
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in scene.objects:
            obj.select_set(state=True)
        bpy.ops.object.delete()
    except RuntimeError:  # Common Blender operator failure mode
        logger.exception("Failed to delete objects while cleaning up scene %s", scene.name)
    try:
        bpy.data.scenes.remove(scene)
    except RuntimeError:
        logger.exception("Failed to remove temporary scene %s", scene.name)


def set_scene(name: str = "") -> None:
    """Activate a scene by name and adopt any existing camera.

    Args:
        name: Name of an existing scene to make active.
    """
    logger.debug("Setting scene '%s'", name)
    bpy.context.window.scene = bpy.data.scenes[name]
    cam = bpy.context.scene.objects.get("Camera")
    if cam is not None:
        bpy.context.scene.camera = cam
    bpy.context.view_layer.update()


def export_uvs_as_webps(obs: Iterable[bpy.types.Object], filepath: str) -> None:
    """Export UV layouts for provided objects to a WEBP image.

    Creates a throwaway scene, builds UV meshes, renders, then restores context.
    """
    original_scene = bpy.context.scene
    uv_scene = bpy.data.scenes.new("UVScene")
    set_scene(name="UVScene")
    setup_scene_camera(uv_scene)
    build_uv_meshes(obs, uv_scene)
    set_render_settings(uv_scene, filepath)
    render_and_save(uv_scene)
    cleanup_scene(uv_scene)
    bpy.context.window.scene = original_scene


def get_UV_material() -> bpy.types.Material:  # noqa: N802 (legacy public name retained)
    """Return (creating if needed) the material used for UV rendering.

    A mix of transparent + emission for visibility over transparency.
    """
    m = bpy.data.materials.get("UV_RENDER_MATERIAL")
    if m is None:
        m = bpy.data.materials.new("UV_RENDER_MATERIAL")
        m.use_nodes = True
        nodes = m.node_tree.nodes
        links = m.node_tree.links
        nodes.clear()
        emission_node = nodes.new(type="ShaderNodeEmission")
        emission_node.inputs["Color"].default_value = (1, 1, 1, 1)
        emission_node.inputs["Strength"].default_value = 1.0
        transparent_node = nodes.new(type="ShaderNodeBsdfTransparent")
        mix_shader_node = nodes.new(type="ShaderNodeMixShader")
        mix_shader_node.inputs["Fac"].default_value = 0.05
        material_output_node = nodes.new("ShaderNodeOutputMaterial")
        links.new(emission_node.outputs["Emission"], mix_shader_node.inputs[2])
        links.new(transparent_node.outputs["BSDF"], mix_shader_node.inputs[1])
        links.new(mix_shader_node.outputs["Shader"], material_output_node.inputs["Surface"])
    return m


def build_uv_meshes(obs: Iterable[bpy.types.Object], scene: bpy.types.Scene) -> None:
    """Create mesh objects in scene representing the UV layout of each source object."""
    material = get_UV_material()
    offset_index = 0
    for ob in obs:
        me = ob.data  # type: ignore[attr-defined]
        has_no_uv_layers = len(ob.data.uv_layers) == 0  # type: ignore[attr-defined]
        active_layer = ob.data.uv_layers.active  # type: ignore[attr-defined]
        active_layer_empty = bool(active_layer and len(active_layer.data) == 0)  # type: ignore[attr-defined]
        if has_no_uv_layers or active_layer is None or active_layer_empty:
            continue
        uv_layer = me.uv_layers.active  # type: ignore[attr-defined]
        uvs = [0.0] * (2 * len(me.loops))  # type: ignore[attr-defined]
        uv_layer.data.foreach_get("uv", uvs)  # type: ignore[attr-defined]
        xs = uvs[0::2]
        ys = uvs[1::2]
        zs = [0.0] * len(xs)
        uv_mesh = bpy.data.meshes.new(f"UVMesh_{ob.name}")
        verts = [(xs[i], ys[i], zs[i]) for i in range(len(xs))]
        faces = [p.loop_indices for p in me.polygons]  # type: ignore[attr-defined]
        uv_mesh.from_pydata(verts, [], faces)
        uv_object = bpy.data.objects.new(f"UVMesh_{ob.name}", uv_mesh)
        scene.collection.objects.link(uv_object)
        uv_object.data.materials.append(material)
        bpy.context.view_layer.objects.active = uv_object
        uv_object.select_set(state=True)
        uv_object.location.z -= offset_index * 0.01
        offset_index += 1
        if len(uv_object.data.vertices) < WIRE_FRAME_VERTEX_THRESHOLD:
            bpy.ops.object.duplicate()
            bpy.ops.object.modifier_add(type="WIREFRAME")
            bpy.context.object.modifiers["Wireframe"].thickness = 0.001
