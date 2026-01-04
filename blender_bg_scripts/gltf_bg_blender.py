"""Background Blender script for generating GLTF/GLB files.

Supports two targets:
- "gltf": Optimized for web (Draco compression)
- "gltf_godot": Optimized for Godot (no Draco, broader compatibility)
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

import addon_utils  # type: ignore
import bmesh  # type: ignore
import bpy  # type: ignore
from mathutils import Vector  # type: ignore

# Add utils path for imports inside Blender
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import log  # noqa: E402, I001


logger = log.create_logger(__name__)


LOCAL_DEBUG = os.environ.get("LOCAL_DEBUG", "") == "1"

UV_NAME = "LightingUV"
MARGIN = 0.02  # UV margin for lightmap packing
UV_ISLAND_EPSILON = 1e-5

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


def disable_subsurf_modifiers(obj: bpy.types.Object) -> None:
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


def make_uv_active(obj: bpy.types.Object, name: str) -> None:
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


def move_uv_to_bottom(obj: bpy.types.Object, index: int) -> None:
    """Move a UV layer to the last position.

    Args:
        obj: Blender object with UV layers.
        index: Index of the UV layer to move to the bottom.

    Hint:
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


def make_active_uv_first(obj: bpy.types.Object) -> None:
    """Reorder UVs so the active one becomes first.

    Args:
        obj: Blender object with UV layers.

    Hint:
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


def ensure_world_shader() -> None:
    """Ensure the world has a shader with pure white ambient color."""
    world = bpy.context.scene.world
    if not world:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    if not world.use_nodes:
        # will be removed in ble6.0
        world.use_nodes = True

    nodes = world.node_tree.nodes
    links = world.node_tree.links

    # Clear existing nodes
    for node in nodes:
        nodes.remove(node)

    # Create Background node
    bg_node = nodes.new(type="ShaderNodeBackground")
    bg_node.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)  # Pure white
    bg_node.inputs["Strength"].default_value = 10.0
    bg_node.location = (0, 0)

    # Create World Output node
    output_node = nodes.new(type="ShaderNodeOutputWorld")
    output_node.location = (200, 0)

    # Link Background to World Output
    links.new(bg_node.outputs["Background"], output_node.inputs["Surface"])

    logger.info("Configured world shader with pure white ambient color")


def _fallback_pack_islands(
    islands: list[list[bmesh.types.BMFace]],
    uv_layer: bmesh.types.BMLoopLayer,
) -> None:
    """Pack UV islands into a deterministic grid within the 0..1 UV square."""
    rects: list[dict[str, Any]] = []
    for faces in islands:
        coords_u: list[float] = []
        coords_v: list[float] = []
        for face in faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                coords_u.append(uv.x)
                coords_v.append(uv.y)

        if not coords_u:
            continue
        min_u = min(coords_u)
        max_u = max(coords_u)
        min_v = min(coords_v)
        max_v = max(coords_v)
        rects.append(
            {
                "faces": faces,
                "min": Vector((min_u, min_v)),
                "size": Vector((max(max_u - min_u, 1e-6), max(max_v - min_v, 1e-6))),
            },
        )

    if not rects:
        logger.warning("No UV rectangles found for fallback packing")
        return

    cols = max(1, math.ceil(math.sqrt(len(rects))))
    rows = max(1, math.ceil(len(rects) / cols))
    cell_w = 1.0 / cols
    cell_h = 1.0 / rows

    pad_x = min(MARGIN * 0.5, cell_w * 0.25)
    pad_y = min(MARGIN * 0.5, cell_h * 0.25)
    usable_w = max(cell_w - 2 * pad_x, cell_w * 0.25)
    usable_h = max(cell_h - 2 * pad_y, cell_h * 0.25)

    for idx, rect in enumerate(rects):
        row = idx // cols
        col = idx % cols
        base = Vector((col * cell_w + pad_x, row * cell_h + pad_y))
        size = rect["size"]
        scale = min(usable_w / size.x, usable_h / size.y)
        scale = max(scale, 1e-6)

        for face in rect["faces"]:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                uv.x = (uv.x - rect["min"].x) * scale + base.x
                uv.y = (uv.y - rect["min"].y) * scale + base.y


def _faces_connected_in_uv(
    face_a: bmesh.types.BMFace,
    face_b: bmesh.types.BMFace,
    uv_layer: bmesh.types.BMLoopLayer,
) -> bool:
    """Return True if faces share an edge with matching UVs (within tolerance)."""
    shared = False
    for edge in face_a.edges:
        if face_b not in edge.link_faces:
            continue
        shared = True
        loops_a = [loop for loop in face_a.loops if loop.edge == edge]
        loops_b = [loop for loop in face_b.loops if loop.edge == edge]
        for loop_a in loops_a:
            match = next((loop_b for loop_b in loops_b if loop_b.vert == loop_a.vert), None)
            if match is None:
                continue
            uv_a = loop_a[uv_layer].uv
            uv_b = match[uv_layer].uv
            if (uv_a - uv_b).length > UV_ISLAND_EPSILON:
                return False
    return shared


def _collect_uv_islands(
    bm: bmesh.types.BMesh,
    uv_layer: bmesh.types.BMLoopLayer,
) -> list[list[bmesh.types.BMFace]]:
    """Build a list of UV islands by traversing connected faces."""
    islands: list[list[bmesh.types.BMFace]] = []
    visited: set[int] = set()

    for face in bm.faces:
        if face.index in visited:
            continue

        stack = [face]
        island: list[bmesh.types.BMFace] = []

        while stack:
            current = stack.pop()
            if current.index in visited:
                continue
            visited.add(current.index)
            island.append(current)

            for edge in current.edges:
                for neighbor in edge.link_faces:
                    if neighbor is current or neighbor.index in visited:
                        continue
                    if not _faces_connected_in_uv(current, neighbor, uv_layer):
                        continue
                    stack.append(neighbor)

        islands.append(island)

    return islands


def _pack_uv_islands(bm: bmesh.types.BMesh, uv_layer: bmesh.types.BMLoopLayer) -> None:
    """Pack UV islands deterministically inside 0..1 UV space."""
    bm.faces.ensure_lookup_table()
    islands = _collect_uv_islands(bm, uv_layer)
    if not islands:
        logger.warning("No UV islands found for packing")
        return

    logger.info("Found %d UV islands for packing", len(islands))
    _fallback_pack_islands(islands, uv_layer)


def _pack_uv_islands_with_operator(obj: bpy.types.Object) -> bool:
    """Use bpy.ops.uv.pack_islands with correct context; fall back by returning False."""
    mesh = obj.data
    if UV_NAME not in mesh.uv_layers:
        logger.warning("Cannot pack UVs on '%s': missing '%s' layer", obj.name, UV_NAME)
        return False

    view_layer = bpy.context.view_layer
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(state=True)
    view_layer.objects.active = obj

    try:
        bpy.ops.object.mode_set(mode="EDIT")
    except RuntimeError as exc:
        logger.warning("Failed to enter EDIT mode for '%s': %s", obj.name, exc)
        bpy.ops.object.mode_set(mode="OBJECT")
        return False

    try:
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        mesh.uv_layers.active = mesh.uv_layers[UV_NAME]
        bpy.ops.uv.pack_islands(rotate=True, margin=MARGIN)
        logger.debug("Packed UVs for '%s' using bpy.ops.uv.pack_islands", obj.name)
        return True  # noqa: TRY300
    except Exception as exc:  # noqa: BLE001
        logger.warning("bpy.ops.uv.pack_islands failed for '%s': %s", obj.name, exc)
        return False
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def ensure_lighting_uv(obj: bpy.types.Object) -> None:
    """Create a UV layer for lighting/baking if not present.

    Args:
        obj: Blender object to add lighting UV to.
    """
    mesh = obj.data

    # Store active UV
    prev_uv = mesh.uv_layers.active
    prev_idx = mesh.uv_layers.active_index if prev_uv else -1

    # Ensure mesh UV layer (names exist ONLY here)
    if UV_NAME not in mesh.uv_layers:
        mesh.uv_layers.new(name=UV_NAME)
    mesh.uv_layers.active = mesh.uv_layers[UV_NAME]

    # --- BMesh
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # BMesh UV layers are unnamed → verify
    uv_layer = bm.loops.layers.uv.verify()

    # --- FAST FACE-BASED UNWRAP (lighting-safe)
    for face in bm.faces:
        n = face.normal
        x = n.orthogonal().normalized()
        y = n.cross(x).normalized()

        for loop in face.loops:
            co = loop.vert.co
            loop[uv_layer].uv = Vector((co.dot(x), co.dot(y)))

    _pack_uv_islands(bm, uv_layer)

    # --- WRITE BACK
    bm.to_mesh(mesh)
    bm.free()

    packed = _pack_uv_islands_with_operator(obj)
    if not packed:
        logger.info("Using fallback UV layout for '%s'", obj.name)

    # Restore active UV
    if prev_uv:
        mesh.uv_layers.active_index = prev_idx
        mesh.uv_layers.active = prev_uv

    logger.info("Added 'LightingUV' UV layer to object '%s'", obj.name)


def is_procedural_material(mat: bpy.types.Material) -> bool:
    """Determine if a material is procedural.

    Args:
        mat: Blender material to check.

    Returns:
        True if the material is procedural, False otherwise.

    Hint:
        Procedural is defined as using nodes without any Image Texture nodes.
    """
    if not mat.use_nodes:  # noqa: SIM103
        return False
    # all shaders are procedural, if they heave more than texture
    return True
    # > for node in mat.node_tree.nodes:
    # >     if node.type == "TEX_IMAGE":
    # >         return False
    # > return True


def _enable_bake_color_pass(scene: bpy.types.Scene) -> None:
    """Ensures the bake color pass flag is enabled regardless of Blender version.

    Args:
        scene: Blender scene to adjust bake settings for.
    """
    cycles_settings = getattr(scene, "cycles", None)
    if cycles_settings and hasattr(cycles_settings, "use_pass_color"):
        logger.info("Enabling bake color pass in Cycles settings")
        cycles_settings.use_pass_color = True
        return

    bake_settings = getattr(scene.render, "bake", None)
    if bake_settings and hasattr(bake_settings, "use_pass_color"):
        logger.info("Enabling bake color pass in Render Bake settings")
        bake_settings.use_pass_color = True
        return

    logger.warning("Cannot enable bake color pass; API attribute missing")


def _set_bake_margin(scene: bpy.types.Scene, margin: int) -> None:
    """Set bake margin in a version-tolerant way.

    Args:
        scene: Blender scene to adjust bake settings for.
        margin: Margin value to set for baking.
    """
    cycles_settings = getattr(scene, "cycles", None)
    if cycles_settings and hasattr(cycles_settings, "bake_margin"):
        cycles_settings.bake_margin = margin
        return

    bake_settings = getattr(scene.render, "bake", None)
    if bake_settings and hasattr(bake_settings, "margin"):
        bake_settings.margin = margin
        return

    logger.warning("Cannot set bake margin; API attribute missing")


def connect_baked_textures(obj: bpy.types.Object) -> None:
    """Reconnect baked textures to the material nodes.

    And pack images into the Blender file.

    Args:
        obj: Blender object to reconnect baked textures for.
    """
    procedural_materials = [mat for mat in obj.data.materials if mat and is_procedural_material(mat)]
    if not procedural_materials:
        logger.info("No procedural materials found on '%s'. Skipping reconnect.", obj.name)
        return

    for mat in procedural_materials:
        nodes = mat.node_tree.nodes
        img_node = next(
            (
                node
                for node in nodes
                if node.type == "TEX_IMAGE" and node.image and node.image.name == f"{obj.name}_{mat.name}_Bake"
            ),
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
        else:
            # create diffuse shader and connect
            diffuse_node = nodes.new(type="ShaderNodeBsdfDiffuse")
            # connect image to diffuse
            mat.node_tree.links.new(img_node.outputs["Color"], diffuse_node.inputs["Color"])
            # connect diffuse to material output
            output_node = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
            if output_node:
                # Remove existing connections to Surface
                for link in list(output_node.inputs["Surface"].links):
                    mat.node_tree.links.remove(link)
                mat.node_tree.links.new(diffuse_node.outputs["BSDF"], output_node.inputs["Surface"])

        # Pack the image into the Blender file
        if not img_node.image.packed_file:
            img_node.image.pack()

    logger.info("Reconnected baked textures for object '%s'", obj.name)


def bake_all_procedural_textures(obj: bpy.types.Object) -> None:
    """Bake all procedural textures on a mesh object.

    Args:
        obj: Blender object to bake procedural textures for.

    Hint:
        Baked images are packed inside the Blender file for portability.
    """
    procedural_materials = [mat for mat in obj.data.materials if mat and is_procedural_material(mat)]
    if not procedural_materials:
        logger.info("No procedural materials found on '%s'. Skipping bake.", obj.name)
        return

    # Configure bake settings
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.cycles.bake_type = "DIFFUSE"
    _enable_bake_color_pass(scene)
    _set_bake_margin(scene, 16)

    # set some render limit , some light passes take ages
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False

    # Select the object and make it active
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(state=True)
    bpy.context.view_layer.objects.active = obj

    ensure_lighting_uv(obj)

    # Ensure the object is in Object Mode
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # Prepare materials for baking
    for mat in procedural_materials:
        try:
            if not mat.use_nodes:
                # will be removed in ble6.0
                mat.use_nodes = True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to enable nodes for material '%s': %s", mat.name, e)

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
        # log resulting image path
        logger.info("Prepared bake image '%s' for material '%s'", bake_image.name, mat.name)

    # Perform the bake
    try:
        bpy.ops.object.bake(
            type="DIFFUSE",  # "COMBINED"
            pass_filter={"COLOR"},  # looks better without
            use_selected_to_active=False,
            uv_layer="LightingUV",
            margin=16,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error during baking.: %s", e)  # noqa: TRY400
    logger.info("Baked procedural textures: %s", procedural_materials)

    # for debug export the baked textures as copies next to the blend file
    # otherwise while baking keep all material live
    for mat in procedural_materials:
        nodes = mat.node_tree.nodes
        img_node = next(
            (node for node in nodes if node.type == "TEX_IMAGE" and node.image.name == f"{obj.name}_{mat.name}_Bake"),
            None,
        )
        if not img_node:
            continue

        # for debug export the baked texture as a copy next to the blend file
        if LOCAL_DEBUG:
            img_node.image.filepath_raw = bpy.path.abspath(f"//baked_textures/{img_node.image.name}.png")
            img_node.image.file_format = "PNG"
            img_node.image.save()

        logger.info("Packed image '%s' into the Blender project.", img_node.image.name)
    logger.info("Baked textures assigned")


def generate_gltf(json_result_path: str, target_format: str) -> None:  # noqa: C901
    """Generate a GLB file for an asset and write results metadata.

    Args:
        json_result_path: Path to write the results JSON file.
        target_format: The target export format, e.g., 'gltf_godot'.

    Hint:
        On success, writes a JSON list with the GLTF file path to ``json_result_path``.
        On failure, no JSON is written and the caller should detect the missing file.
    """
    filepath = bpy.data.filepath.replace(".blend", ".glb")

    # make sure world has pure white ambient color
    ensure_world_shader()

    # PREPARE ASSET (do things which cannot be set in GLTF export options)
    logger.info("ASSET PRE-PROCESSING start")
    for i, obj in enumerate(bpy.data.objects):
        logger.info("Preprocess object %d: %s", i, obj.name)
        if obj.type != "MESH":
            continue
        disable_subsurf_modifiers(obj)
        bake_all_procedural_textures(obj)

    # after baking reconnect  baked images to shaders
    for i, obj in enumerate(bpy.data.objects):
        logger.info("Post-bake process object %d: %s", i, obj.name)
        if obj.type != "MESH":
            continue
        connect_baked_textures(obj)
        make_uv_active(obj, UV_NAME)
        make_active_uv_first(obj)

    # save duplicate of the scene for debugging , with all the baking setups
    if LOCAL_DEBUG:
        debug_save_path = bpy.data.filepath.replace(".blend", "_bake_debug.blend")
        bpy.ops.wm.save_as_mainfile(filepath=debug_save_path)

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
