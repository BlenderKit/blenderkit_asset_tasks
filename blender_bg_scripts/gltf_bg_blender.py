"""Background Blender script for generating GLTF/GLB files.

Supports two targets:
- "gltf": Optimized for web (Draco compression)
- "gltf_godot": Optimized for Godot (no Draco, broader compatibility)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import addon_utils  # type: ignore
import bpy  # type: ignore

# Add utils path for imports inside Blender
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import log  # noqa: E402, I001


logger = log.create_logger(__name__)


DRACO_MESH_COMPRESSION: dict[str, Any] = {
    "export_draco_mesh_compression_enable": True,
}
MINIMAL_GLTF: dict[str, Any] = {
    "export_format": "GLB",  # single GLTF binary file
    "export_apply": True,  # apply modifiers
}
MAXIMAL_GLTF: dict[str, Any] = MINIMAL_GLTF | {
    "export_image_format": "WEBP",
    "export_image_add_webp": True,
    "export_jpeg_quality": 50,
    "export_image_quality": 50,
}


def disable_subsurf_modifiers(obj) -> None:
    """Disable Subdivision Surface modifiers on an object.

    Args:
        obj: Blender object whose modifiers should be adjusted.
    """
    for mod in obj.modifiers:
        if mod.type != "SUBSURF":
            continue
        mod.show_viewport = False
        mod.show_render = False
    logger.info("Disabled Subdivision Surface modifier for '%s'", obj.name)


def make_uv_active(obj, name: str) -> None:
    """Set a UV layer active by name.

    Args:
        obj: Blender object with UV layers.
        name: Name of the UV layer to activate.
    """
    uvs = obj.data.uv_layers
    for uv in uvs:
        if uv.name == name:
            uvs.active = uv
            return
    logger.warning("Active UV '%s' not found (unexpected)", name)


def move_uv_to_bottom(obj, index: int) -> None:
    """Move a UV layer to the last position.

    This is a hack, because we cannot directly modify ``obj.data.uv_layers`` ordering.
    Instead we duplicate and remove to effectively move a layer to the bottom.
    """
    uvs = obj.data.uv_layers
    uvs.active_index = index
    new_name = uvs.active.name

    # set the context, so later we can add uv_texture
    bpy.context.view_layer.objects.active = obj
    obj.select_set(state=True)
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.uv_texture_add()
    make_uv_active(obj, new_name)
    bpy.ops.mesh.uv_texture_remove()

    uvs.active_index = len(uvs) - 1
    uvs.active.name = new_name


def make_active_uv_first(obj) -> None:
    """Reorder UVs so the active one becomes first.

    Effectively copies UVs one by one (to last position). Skips the active UV,
    so in the end the active is first and all other UVs follow in original order.
    """
    uvs = obj.data.uv_layers
    # log number of uvs
    logger.info("Number of UV layers found on object '%s': %d", obj.name, len(uvs))
    active_layer = uvs.active
    # check if we have active or at least some UV
    if not active_layer:
        logger.warning("No UV layers found on object '%s'", obj.name)
        return
    orig_name = active_layer.name
    orig_index = uvs.active_index
    if orig_index == 0:
        return

    logger.info("UVs before order: %s (%s is active)", list(uvs), orig_name)
    for i in range(len(uvs)):
        if i == orig_index:
            continue  # keep active on top
        if i < orig_index:
            move_uv_to_bottom(obj, 0)
        else:
            # active is first, move the second element to keep original order
            move_uv_to_bottom(obj, 1)

    make_uv_active(obj, orig_name)  # restore originally active UV
    logger.info("UVs after order: %s (%s is active)", list(uvs), uvs.active.name)


def is_procedural_material(mat) -> bool:
    """Determine if a material is procedural.

    Procedural is defined as using nodes without any Image Texture nodes.
    """
    if not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:  # noqa: SIM110
        if node.type == "TEX_IMAGE":
            return False
    return True


def bake_all_procedural_textures(obj) -> None:
    """Bake all procedural textures on a mesh object.

    Baked images are packed inside the Blender file for portability.
    """
    procedural_materials = [mat for mat in obj.data.materials if mat and is_procedural_material(mat)]
    if not procedural_materials:
        logger.info("No procedural materials found on '%s'. Skipping bake.", obj.name)
        return

    # Configure bake settings
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.bake_type = "DIFFUSE"
    bpy.context.scene.cycles.use_pass_color = True
    bpy.context.scene.cycles.bake_margin = 16

    # Select the object and make it active
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(state=True)
    bpy.context.view_layer.objects.active = obj

    # Ensure the object is in Object Mode
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # Prepare materials for baking
    for mat in procedural_materials:
        if not mat.use_nodes:
            mat.use_nodes = True
        nodes = mat.node_tree.nodes

        # Create a new Image Texture node
        img_node = nodes.new(type="ShaderNodeTexImage")
        img_node.location = (-300, 0)

        # Create a new image for baking
        bake_image = bpy.data.images.new(
            name=f"{obj.name}_{mat.name}_Bake",
            width=1024,
            height=1024,
        )
        img_node.image = bake_image
        bake_image.generated_color = (0, 0, 0, 1)  # Initialize with black

        # Set the image as active for baking
        img_node.select = True
        mat.node_tree.nodes.active = img_node

    # Perform the bake
    try:
        bpy.ops.object.bake(
            type="DIFFUSE",
            pass_filter={"COLOR"},
            use_selected_to_active=False,
            margin=16,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error during baking.: %s", e)  # noqa: TRY400
    logger.info("Baked procedural textures: %s", procedural_materials)

    # Assign baked textures to materials
    for mat in procedural_materials:
        nodes = mat.node_tree.nodes
        img_node = next(
            (node for node in nodes if node.type == "TEX_IMAGE" and node.image.name == f"{obj.name}_{mat.name}_Bake"),
            None,
        )
        if not img_node:
            continue
        # Find the Principled BSDF node
        bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            # Remove existing connections to Base Color
            for link in list(bsdf.inputs["Base Color"].links):
                mat.node_tree.links.remove(link)
            # Connect the Image Texture node to Base Color
            mat.node_tree.links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])
        # Pack the image into the Blender file
        if not img_node.image.packed_file:
            img_node.image.pack()
        logger.info("Packed image '%s' into the Blender project.", img_node.image.name)
    logger.info("Baked textures assigned")


def generate_gltf(json_result_path: str, target_format: str) -> None:
    """Generate a GLB file for an asset and write results metadata.

    On success, writes a JSON list with the GLTF file path to ``json_result_path``.
    On failure, no JSON is written and the caller should detect the missing file.
    """
    filepath = bpy.data.filepath.replace(".blend", ".glb")

    # PREPARE ASSET (do things which cannot be set in GLTF export options)
    logger.info("ASSET PRE-PROCESSING start")
    for i, obj in enumerate(bpy.data.objects):
        logger.info("Preprocess object %d: %s", i, obj.name)
        if obj.type != "MESH":
            continue
        disable_subsurf_modifiers(obj)
        bake_all_procedural_textures(obj)
        make_active_uv_first(obj)

    logger.info("ASSET PRE-PROCESSING finished")

    # CHOOSE EXPORT OPTIONS - based on target_format (gltf/gltf_godot)
    logger.info("Generating GLTF for target format: %s", target_format)
    if target_format == "gltf":  # Optimize for web presentation - adding draco compression
        options = [
            ["maximal", MAXIMAL_GLTF | DRACO_MESH_COMPRESSION],
            ["minimal", MINIMAL_GLTF | DRACO_MESH_COMPRESSION],
        ]
    elif target_format == "gltf_godot":  # Optimize for use in Godot
        options = [
            ["maximal", MAXIMAL_GLTF],
            ["minimal", MINIMAL_GLTF],
        ]
    else:
        logger.error("target_format needs to be gltf or gltf_godot")
        sys.exit(10)

    # TRY EXPORT - go from ideal to minimal export settings
    success = False
    for option in options:
        options_name = option[0]
        gltf_options = option[1]
        try:
            bpy.ops.export_scene.gltf(filepath=filepath, **gltf_options)
            success = True
            break  # No need to continue
        except Exception:
            logger.exception("Error during '%s' GLTF export", options_name)

    # FAILURE - Exit now, calling script will detect missing JSON and react properly
    if not success:
        sys.exit(101)

    # SUCCESS - Write results data to a JSON file
    files = [{"type": target_format, "index": 0, "file_path": filepath}]
    try:
        with open(json_result_path, "w", encoding="utf-8") as f:
            json.dump(files, f, ensure_ascii=False, indent=4)
    except (OSError, PermissionError):
        logger.exception("Failed to write results JSON")
        sys.exit(102)


if __name__ == "__main__":
    addon_utils.enable("io_scene_gltf2")
    datafile = sys.argv[-1]
    logger.info("Background GLTF generator has started")

    try:
        with open(datafile, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse input JSON: %s", datafile)
        sys.exit(10)

    json_result_path = data.get("result_filepath")  # Output data JSON
    if not json_result_path:
        logger.error("Missing result_filepath for GLTF generation")
        sys.exit(10)

    target_format = data.get("target_format")
    if not target_format:
        logger.error("Missing target_format (gltf/gltf_godot) for GLTF generation")
        sys.exit(10)

    generate_gltf(json_result_path, target_format)
