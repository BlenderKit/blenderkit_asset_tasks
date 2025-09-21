"""Background Blender script to render material turnaround animations.

This script loads a material into a prepared scene and renders an animation that
turns the preview object to showcase the material. It expects the last CLI arg to
be a path to a JSON file with keys: file_path, result_filepath, asset_data.
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

from blenderkit_server_utils import log, utils  # noqa: E402


logger = log.create_logger(__name__)


MATERIAL_PREVIEW_SUFFIX = "MaterialPreview"


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


def render_material_turnaround(mat: Any, filepath: str) -> None:
    """Assign material to preview objects and render the turnaround animation.

    Args:
        mat: Material to assign to the preview mesh.
        filepath: Output path for the rendered frames/movie as configured in the scene.
    """
    # Assign material to all preview objects
    for ob in bpy.context.scene.objects:
        if ob.name.endswith(MATERIAL_PREVIEW_SUFFIX):
            ob.material_slots[0].material = mat

    # Configure render output
    bpy.context.scene.render.filepath = filepath
    bpy.context.view_layer.update()
    bpy.context.scene.update_tag()
    bpy.context.view_layer.update()

    # Enable GPU rendering if available
    try:
        preferences = bpy.context.preferences
        cycles_preferences = preferences.addons["cycles"].preferences
        # CUDA is a best-effort default; utils helper enables devices as needed
        cycles_preferences.compute_device_type = "CUDA"
        if cycles_preferences.devices:
            cycles_preferences.devices[0].use = True
    except Exception:
        logger.exception("Failed to configure GPU devices for Cycles")

    try:
        utils.enable_cycles_CUDA()
    except Exception:
        logger.exception("utils.enable_cycles_CUDA failed")

    # Render animation
    bpy.ops.render.render(animation=True)


def append_material(
    file_name: str,
    matname: str | None = None,
    *,
    link: bool = False,
    fake_user: bool = True,
) -> Any:
    """Append a material from a .blend file into the current file.

    Args:
      file_name: Path to the .blend library file.
      matname: Optional material name to load; if None, the first material is used.
      link: Whether to link the material instead of appending it.
      fake_user: Whether to set use_fake_user on the imported material.

    Returns:
      The appended Blender material or None if not found.
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

            # Not found yet? probably some name inconsistency then.
            if not found and len(data_from.materials) > 0:
                data_to.materials = [data_from.materials[0]]
                matname = data_from.materials[0]
                logger.info(
                    "Material not found under the exact name, appended first available: %s",
                    matname,
                )
    except (OSError, RuntimeError, ValueError):
        logger.exception("Failed to open the asset file: %s", file_name)

    # find the new material, due to possible name changes
    mat = None
    for m in bpy.data.materials:
        if m not in mats_before:
            mat = m
            break
    # still not found?
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
    """Load material from data and render its turnaround.

    Args:
      data: Input data dict with keys: file_path, result_filepath, asset_data.
    """
    result_filepath = data.get("result_filepath")
    if not result_filepath:
        logger.error("Missing result_filepath in input data")
        sys.exit(2)

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
    render_material_turnaround(mat, result_filepath)


if __name__ == "__main__":
    logger.info("Background material turnaround generator started")
    datafile = sys.argv[-1]
    try:
        with open(datafile, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse input JSON: %s", datafile)
        sys.exit(1)

    render_uploaded_material(data)
