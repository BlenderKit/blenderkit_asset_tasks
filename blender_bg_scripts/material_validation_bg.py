"""Render a material validation still and export node graph and textures.

This background Blender script assigns a material to preview objects, generates a
validation render with derived metrics (displacement, SSS), and writes auxiliary
artifacts (node graph visualization and exported textures).
"""

# isort: skip_file
from __future__ import annotations

import json
import os
import sys
from typing import Any

import bpy

# Add utils path for imports inside Blender
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import render_nodes_graph, log, utils  # noqa: E402


logger = log.create_logger(__name__)


MATERIAL_PREVIEW_PREFIX = "MaterialPreview"


def get_node(mat: Any, type_name: str) -> Any | None:
    """Return the first node matching a given node type in a material.

    Args:
        mat: Blender material to inspect.
        type_name: Node type identifier, e.g., 'BSDF_PRINCIPLED'.

    Returns:
        The first matching node or None if not found.
    """
    for n in mat.node_tree.nodes:
        if n.type == type_name:
            return n
    return None


def set_text(object_name: str, text: Any) -> None:
    """Set text on a Blender text object by name.

    Args:
        object_name: Name of the text object in bpy.data.objects.
        text: Value to set as the text body.
    """
    if object_name not in bpy.data.objects:
        logger.warning("Text object '%s' not found", object_name)
        return
    textob = bpy.data.objects[object_name]
    textob.data.body = str(text)


def configure_cycles_gpu() -> None:
    """Enable GPU rendering for Cycles when available."""
    try:
        preferences = bpy.context.preferences
        cycles_preferences = preferences.addons["cycles"].preferences
        cycles_preferences.compute_device_type = "CUDA"
        if cycles_preferences.devices:
            cycles_preferences.devices[0].use = True
    except Exception:
        logger.exception("Failed to configure GPU devices for Cycles")

    try:
        utils.enable_cycles_CUDA()
    except Exception:
        logger.exception("utils.enable_cycles_CUDA failed")


def assign_preview_materials(mat: Any) -> tuple[Any | None, Any | None]:
    """Assign material to preview objects and build a second-slot variation.

    Args:
        mat: Material to assign to the preview mesh.

    Returns:
        A tuple of (mat1, principled_node) where mat1 is the duplicated
        material used in the second slot and principled_node is its Principled BSDF.
    """
    mat1: Any | None = None
    principled: Any | None = None
    for ob in bpy.context.scene.objects:
        if ob.name.startswith(MATERIAL_PREVIEW_PREFIX):
            ob.material_slots[0].material = mat
            if len(ob.material_slots) > 1 and mat1 is None:
                bpy.context.view_layer.objects.active = ob
                mat1 = mat.copy()
                principled = get_node(mat1, "BSDF_PRINCIPLED")
                output = get_node(mat1, "OUTPUT_MATERIAL")
                if principled and output:
                    inlinks = principled.inputs["Normal"].links
                    if inlinks:
                        inl = inlinks[0]
                        n1 = mat1.node_tree.nodes.new("ShaderNodeVectorMath")
                        n1.inputs[1].default_value = (0.5, 0.5, 0.5)
                        n1.operation = "MULTIPLY"
                        n2 = mat1.node_tree.nodes.new("ShaderNodeVectorMath")
                        n2.inputs[1].default_value = (0.5, 0.5, 0.5)
                        n2.operation = "ADD"
                        mat1.node_tree.links.new(inl.from_socket, n1.inputs[0])
                        mat1.node_tree.links.new(n1.outputs[0], n2.inputs[0])
                        mat1.node_tree.links.new(n2.outputs[0], output.inputs["Surface"])

            if len(ob.material_slots) > 1:
                ob.material_slots[1].material = mat1

    return mat1, principled


def update_overlay_texts(asset_data: dict[str, Any], mat1: Any | None, principled: Any | None) -> None:
    """Update overlay text fields like author, displacement, and SSS.

    Args:
        asset_data: Dictionary with asset metadata.
        mat1: The duplicated material assigned to the second slot.
        principled: The Principled BSDF node in mat1, if available.
    """
    # Author and name
    author = asset_data.get("author", {})
    first = author.get("firstName", "")
    last = author.get("lastName", "")
    name = asset_data.get("name", "")
    set_text("name_info", f"{first}_{last} / {name}_{first}_{last}")

    # Displacement
    displacement_node = get_node(mat1, "DISPLACEMENT") if mat1 is not None else None
    if displacement_node:
        disp = displacement_node.inputs["Scale"].default_value
        set_text("displacement_info", f"{disp:.3f} m")
    else:
        set_text("displacement_info", "none")

    # SSS
    if principled:
        sss = principled.inputs["Subsurface Weight"].default_value
        set_text("sss_intensity_info", sss)
        if sss > 0:
            sssr = principled.inputs["Subsurface Radius"].default_value
            sssrs = principled.inputs["Subsurface Scale"].default_value
            text_val = f"{sssr[0] * sssrs:.2f} {sssr[1] * sssrs:.2f} {sssr[2] * sssrs:.2f} m\n"
            set_text("sss_radius_info", text_val)
        else:
            set_text("sss_radius_info", "none")
    else:
        set_text("sss_radius_info", "none")

    # Material parameters list
    textob = bpy.data.objects.get("material_info")
    if textob is not None:
        textob.data.body = ""
        for p, v in asset_data.get("dictParameters", {}).items():
            textob.data.body += f"{p}\n{v}\n"
    else:
        logger.warning("Object 'material_info' not found; skipping parameter overlay")


def render_material_validation(mat: Any, asset_data: dict[str, Any], filepath: str) -> None:
    """Assign material, compute derived info, update overlays, and render.

    Args:
        mat: Material to assign to the preview mesh.
        asset_data: Asset metadata; currently unused but reserved for future tweaks.
        filepath: Output path for the rendered frames/movie as configured in the scene.
    """
    mat1, principled = assign_preview_materials(mat)
    update_overlay_texts(asset_data, mat1, principled)

    # Render setup
    bpy.context.scene.render.filepath = filepath
    # force redraw
    bpy.context.view_layer.update()
    bpy.context.scene.update_tag()
    bpy.context.view_layer.update()

    configure_cycles_gpu()
    bpy.ops.render.render(write_still=True)


def append_material(
    file_name: str,
    matname: str | None = None,
    *,
    link: bool = False,
    fake_user: bool = True,
) -> Any | None:
    """Append a material type asset from a .blend file.

    Args:
        file_name: Path to the .blend file to load from.
        matname: Optional material name to target. If None, the first material is used.
        link: Whether to link the material instead of appending.
        fake_user: Whether to set use_fake_user on the imported material.

    Returns:
        The appended Blender material, or None if not found.
    """
    mats_before = bpy.data.materials[:]
    try:
        with bpy.data.libraries.load(file_name, link=link, relative=True) as (data_from, data_to):
            found = False
            for m in data_from.materials:
                if m == matname or matname is None:
                    data_to.materials = [m]
                    matname = m
                    found = True
                    break

            if not found and len(data_from.materials) > 0:
                data_to.materials = [data_from.materials[0]]
                matname = data_from.materials[0]
                logger.info(
                    "Material not found under the exact name, appended first available: %s",
                    matname,
                )
    except (OSError, RuntimeError, ValueError):
        logger.exception("Failed to open the asset file: %s", file_name)

    # Find the new material due to possible name changes
    mat: Any | None = None
    for m in bpy.data.materials:
        if m not in mats_before:
            mat = m
            break
    if mat is None:
        mat = bpy.data.materials.get(matname)  # type: ignore[arg-type]

    if mat is not None and fake_user:
        mat.use_fake_user = True
    return mat


def purge() -> None:
    """Remove fake users and purge orphans to clean the file."""
    for material in bpy.data.materials:
        material.use_fake_user = False
    bpy.ops.outliner.orphans_purge()


def render_uploaded_material(data: dict[str, Any]) -> None:
    """Render the validation still for a material described in data.

    Args:
        data: A dict including keys 'asset_data', 'result_filepath', 'file_path', and optional 'result_folder'.
    """
    asset_data = data.get("asset_data")
    result_filepath = data.get("result_filepath")
    if not asset_data or not result_filepath:
        logger.error("Missing asset_data or result_filepath in input data")
        sys.exit(2)

    # This can render more assets in one run; for now, render single asset
    jpg_candidate = f"{result_filepath}.jpg"
    if os.path.exists(jpg_candidate):
        logger.info("Image already exists for %s; skipping", asset_data.get("name", "<unknown>"))
        return

    try:
        mat = append_material(file_name=data["file_path"])
    except KeyError:
        logger.exception("Missing file_path in input data")
        sys.exit(2)
    except Exception:
        logger.exception("Failed to append material from file")
        sys.exit(3)

    if mat is None:
        logger.error("No material imported; aborting render")
        sys.exit(4)

    mat.use_fake_user = False
    render_material_validation(mat, asset_data, result_filepath)

    # Export helpers
    try:
        result_folder = data.get("result_folder", os.path.dirname(result_filepath))
        render_nodes_graph.visualize_nodes(
            result_folder,
            mat.name,
            mat.node_tree,
            bpy.context.scene,
        )
        render_nodes_graph.export_all_material_textures(result_folder, mat)
    except Exception:
        logger.exception("Failed to export node graph or textures")


if __name__ == "__main__":
    logger.info("Background material validation generator started")
    datafile = sys.argv[-1]
    try:
        with open(datafile, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse input JSON: %s", datafile)
        sys.exit(1)

    render_uploaded_material(data)

# End of script
