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
# isort: skip_file

import json
import math
import os
import sys
from typing import Any

import bpy

# Add parent directory to Python path so we can import blenderkit_server_utils
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import append_link, utils, log  # noqa: E402


logger = log.create_logger(__name__)


def center_obs_for_thumbnail(obs: list[Any]) -> None:
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
        center_obs_for_thumbnail(all_objects)

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
        scene.render.filepath = data["result_filepath"]
        logger.info("Rendering model thumbnail to %s", scene.render.filepath)
        render_thumbnails()

        logger.info("Background autothumbnailer (model) finished successfully")
        sys.exit(0)

    except Exception:
        logger.exception("Background autothumbnailer (model) failed")
        sys.exit(1)
