"""Background script for generating model thumbnails in Blender.

This script runs inside Blender's Python environment and is invoked by
``render_thumbnail.py``. It expects a JSON file path as the last CLI argument
containing:
- model file path (to link into a prepared scene)
- template blend file (e.g., model_thumbnailer.blend)
- result filepath for the rendered thumbnail
- render parameters inside ``asset_data``
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from typing import Any, Union

import bpy

# Add parent directory to Python path so we can import blenderkit_server_utils
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import append_link, log, utils  # noqa: E402

logger = log.create_logger(__name__)


def center_objs_for_thumbnail(obs: list[Any]) -> None:
    """Center and scale objects for optimal thumbnail framing.

    Steps:
    1. Center objects in world space (handles parent-child hierarchy)
    2. Adjust camera distance based on object bounds
    3. Scale helper objects to fit the model in frame

    Args:
        obs: List of Blender objects to center and frame.
    """
    scene = bpy.context.scene
    parent = obs[0]

    # Handle instanced collections (linked objects)
    if parent.type == "EMPTY" and parent.instance_collection is not None:
        obs = parent.instance_collection.objects[:]

    # Get top-level parent
    while parent.parent is not None:
        parent = parent.parent

    # Reset parent rotation for accurate snapping
    parent.rotation_euler = (0, 0, 0)
    parent.location = (0, 0, 0)
    bpy.context.view_layer.update()

    # Calculate bounding box in world space
    minx, miny, minz, maxx, maxy, maxz = utils.get_bounds_worldspace(obs)

    # Center object at world origin
    cx = (maxx - minx) / 2 + minx
    cy = (maxy - miny) / 2 + miny
    for ob in scene.collection.objects:
        ob.select_set(select=False)

    bpy.context.view_layer.objects.active = parent
    parent.location = (-cx, -cy, 0)

    # Adjust camera position and scale based on object size
    cam_z = scene.camera.parent.parent
    cam_z.location.z = maxz / 2

    # Calculate diagonal size of object for scaling
    dx = maxx - minx
    dy = maxy - miny
    dz = maxz - minz
    r = math.sqrt(dx * dx + dy * dy + dz * dz)

    # Scale scene elements to fit object
    scaler = bpy.context.view_layer.objects["scaler"]
    scaler.scale = (r, r, r)
    coef = 0.7  # Camera distance coefficient
    r *= coef
    cam_z.scale = (r, r, r)
    bpy.context.view_layer.update()


def render_thumbnails() -> None:
    """Render the current scene to a still image (no animation)."""
    bpy.ops.render.render(write_still=True, animation=False)


def replace_materials(obs: list[Any], material_name: str) -> Union[bpy.types.Material, None]:  # noqa: UP007
    """Replace all materials on the given objects with a wireframe material.

    Args:
        obs: List of Blender objects to modify.
        material_name: Name of the wireframe material to use.
    """
    # Create or get the wireframe material
    if material_name in bpy.data.materials:
        material = bpy.data.materials[material_name]
    else:
        return None

    # Assign the wireframe material to all objects
    for ob in obs:
        if ob.type == "MESH":
            # Clear all material slots and add the specified material
            ob.data.materials.clear()
            ob.data.materials.append(material)
    return material


def _str_to_color(s: str) -> tuple[float, float, float] | None:
    """Convert a color string to an RGB tuple.

    Args:
        s: Color string in the format "#RRGGBB" or "R,G,B".

    Returns:
        A tuple of (R, G, B) values as floats in the range [0.0, 1.0], or None.
    """
    hex_size = 7  # e.g. "#RRGGBB"
    rgb_size = 5  # e.g. "R,G,B"
    rgb_count = 3
    s = s.strip()
    if s.startswith("#") and len(s) == hex_size:
        r = int(s[1:3], 16) / 255.0
        g = int(s[3:5], 16) / 255.0
        b = int(s[5:7], 16) / 255.0
        return (r, g, b)
    if len(s) == rgb_size:
        parts = s.split(",")
        if len(parts) == rgb_count:
            try:
                r = float(parts[0].strip())
                g = float(parts[1].strip())
                b = float(parts[2].strip())

            except ValueError:
                pass
            else:
                return (r, g, b)
    # Default to None
    return None


def modify_wireframe_material(settings_data: dict[str, Any]) -> None:
    """Modify wireframe material properties for better visibility.

    Args:
        settings_data: Dictionary containing settings for wireframe material adjustments.
    """
    if "bkit wireframe" not in bpy.data.materials:
        logger.warning("Wireframe material 'bkit wireframe' not found.")
        return

    mat = bpy.data.materials["bkit wireframe"]
    nodes = mat.node_tree.nodes
    if "Wireframe" in nodes:
        nodes["Wireframe"].inputs["Size"].default_value = settings_data.get(
            "thumbnail_wireframe_thickness",
            1.0,
        )

    # by default wireframe is random based on object ID
    # check if we have a setting to make it non-random
    if "thumbnail_wireframe_color" in settings_data and settings_data.get("thumbnail_wireframe_color") is not None:
        clr = _str_to_color(settings_data["thumbnail_wireframe_color"])
        if clr is not None:
            if "custom_wire_color" in nodes:
                nodes["custom_wire_color"].outputs[0].default_value = 1

            if "wire_RGB" in nodes:
                nodes["wire_RGB"].outputs[0].default_value = clr
        else:
            logger.warning(
                "Invalid wireframe color: %s",
                settings_data["thumbnail_wireframe_color"],
            )


def modify_render_output(output_path: str) -> None:
    """Set render output format based on file extension.

    Args:
        output_path: The file path where the render will be saved.
    """
    ext = os.path.splitext(output_path)[1].lower()
    if ext in [".jpg", ".jpeg"]:
        bpy.context.scene.render.image_settings.file_format = "JPEG"
    elif ext == ".png":
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.context.scene.render.film_transparent = True
    scene.render.filepath = output_path


if __name__ == "__main__":
    # args order must match the order in blenderkit/autothumb.py:get_thumbnailer_args()!
    export_data_path = sys.argv[-1]
    logger.info("Preparing model thumbnail scene using export data: %s", export_data_path)

    # Read JSON export data with specific exceptions
    try:
        with open(export_data_path, encoding="utf-8") as s:
            data: dict[str, Any] = json.load(s)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse export data JSON: %s", export_data_path)
        sys.exit(1)

    try:
        thumbnail_use_gpu = data.get("thumbnail_use_gpu")
        asset = data["asset_data"]

        # if debug spit out asset data
        # >logger.debug("Asset data: %s", json.dumps(asset, indent=2))

        # Import the 3D model into the scene (linked to save memory)
        main_object, all_objects = append_link.link_collection(
            file_name=data["file_path"],
            location=(0, 0, 0),
            rotation=(0, 0, 0),
            link=True,
            name=asset["name"],
            parent=None,
        )

        # Position the model in the scene
        center_objs_for_thumbnail(all_objects)

        # Select appropriate camera based on object placement type
        camdict = {
            "GROUND": "camera ground",
            "WALL": "camera wall",
            "CEILING": "camera ceiling",
            "FLOAT": "camera float",
        }
        bpy.context.scene.camera = bpy.data.objects[camdict[asset["thumbnail_snap_to"]]]

        # Set the frame number to get different pre-configured angles
        fdict = {
            "DEFAULT": 1,
            "FRONT": 2,
            "SIDE": 3,
            "TOP": 4,
        }
        scene = bpy.context.scene
        scene.frame_set(fdict[asset["thumbnail_angle"]])

        # Enable the appropriate scene collection based on object placement
        snapdict = {
            "GROUND": "Ground",
            "WALL": "Wall",
            "CEILING": "Ceiling",
            "FLOAT": "Float",
        }
        collection = bpy.context.scene.collection.children[snapdict[asset["thumbnail_snap_to"]]]
        collection.hide_viewport = False
        collection.hide_render = False
        collection.hide_select = False

        # Reset object rotation to ensure consistent orientation
        main_object.rotation_euler = (0, 0, 0)

        # Configure render device (GPU/CPU) and settings
        if thumbnail_use_gpu is True:
            scene.cycles.device = "GPU"
            compute_device_type = data.get("cycles_compute_device_type")
            if compute_device_type is not None:
                prefs = bpy.context.preferences.addons["cycles"].preferences
                prefs.compute_device_type = compute_device_type
                prefs.refresh_devices()

        # Set render quality parameters
        scene.cycles.samples = asset["thumbnail_samples"]
        bpy.context.view_layer.cycles.use_denoising = asset["thumbnail_denoising"]

        # Configure background color brightness
        bpy.data.materials["bkit background"].node_tree.nodes["Value"].outputs["Value"].default_value = asset[
            "thumbnail_background_lightness"
        ]

        # Set output resolution
        scene.render.resolution_x = int(asset["thumbnail_resolution"])
        scene.render.resolution_y = int(asset["thumbnail_resolution"])

        # Configure output path and start render
        # make sure to use matching file format
        output_path = data["result_filepath"]
        modify_render_output(output_path)
        logger.info("Rendering model thumbnail to %s", output_path)

        render_thumbnails()

        # check other outputs
        additional_outputs = asset.get("additional_outputs", "")
        # now check if we also render a wireframe thumbnail
        if "wireframe" in additional_outputs:
            # modify scene for wireframe render
            bpy.data.materials["bkit background"].node_tree.nodes["Value"].outputs["Value"].default_value = 0.1
            # we do not need so much render samples for wireframe
            scene.cycles.samples = min(
                scene.cycles.samples,
                asset["thumbnail_wireframe_samples"],
            )

            # modify other attributes
            modify_wireframe_material(asset)

            # replace materials
            replace_materials(all_objects, material_name="bkit wireframe")

            # modify file paths and render again
            # check if we have a different path for wireframe
            wire_output_path = asset.get("wireframe_result_filepath")
            if wire_output_path is None:
                # replace last part of the path with _wireframe
                fn, ext = os.path.splitext(output_path)
                new_fn = "_".join(fn.split("_")[:-1]) + "_wireframe"
                wire_output_path = new_fn + ext

            modify_render_output(wire_output_path)

            logger.info("Rendering model wireframe thumbnail to %s", wire_output_path)

            render_thumbnails()
        ##  save this for debug next to images
        # > fn, ext = os.path.splitext(data["result_filepath"])
        # > bpy.ops.wm.save_as_mainfile(filepath=fn + ".blend")
        # > logger.info("Background autothumbnailer (model) finished successfully")

        sys.exit(0)

    except Exception:
        logger.exception("Background autothumbnailer (model) failed")
        # full traceback logged above
        logger.exception(traceback.format_exc())
        sys.exit(1)
