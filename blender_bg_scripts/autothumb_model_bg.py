"""
Background script for generating model thumbnails.
This script is called by render_thumbnail.py and runs within Blender's Python environment.
It handles the setup and rendering of 3D model thumbnails with the following workflow:
1. Imports the model into a pre-configured scene
2. Positions the model and camera for optimal framing
3. Configures render settings based on provided parameters
4. Renders the final thumbnail

Required inputs (passed via JSON):
- model file path: Path to the 3D model file to render
- template blend file: Pre-configured Blender scene (model_thumbnailer.blend)
- result filepath: Where to save the rendered thumbnail
- thumbnail parameters: Various rendering settings in asset_data
"""

import bpy
import os
import sys
import json
import math
import traceback
from pathlib import Path

# Add parent directory to Python path so we can import blenderkit_server_utils
# This is necessary because this script runs inside Blender's Python environment
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)

from blenderkit_server_utils import append_link, paths, utils


def center_obs_for_thumbnail(obs):
    """Center and scale objects for optimal thumbnail framing.
    
    This function:
    1. Centers the objects in world space
    2. Handles nested object hierarchies (parent-child relationships)
    3. Adjusts camera distance based on object bounds
    4. Scales the scene to ensure the object fits in frame
    
    Args:
        obs (list): List of Blender objects to center and frame
    """
    s = bpy.context.scene
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
    for ob in s.collection.objects:
        ob.select_set(False)

    bpy.context.view_layer.objects.active = parent
    parent.location = (-cx, -cy, 0)

    # Adjust camera position and scale based on object size
    camZ = s.camera.parent.parent
    camZ.location.z = (maxz) / 2
    
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
    camZ.scale = (r, r, r)
    bpy.context.view_layer.update()


def render_thumbnails():
    """Trigger Blender's render operation and save the result.
    The output path and render settings should be configured before calling this."""
    bpy.ops.render.render(write_still=True, animation=False)


if __name__ == "__main__":
    try:
        # Load thumbnail configuration from JSON
        # args order must match the order in blenderkit/autothumb.py:get_thumbnailer_args()!
        BLENDERKIT_EXPORT_DATA = sys.argv[-1]

        print("preparing thumbnail scene")
        print(BLENDERKIT_EXPORT_DATA)
        with open(BLENDERKIT_EXPORT_DATA, "r", encoding="utf-8") as s:
            data = json.load(s)

        thumbnail_use_gpu = data.get("thumbnail_use_gpu")

        # Import the 3D model into the scene
        # The model is linked rather than appended to save memory
        main_object, all_objects = append_link.link_collection(
            file_name=data["file_path"],
            location=(0, 0, 0),
            rotation=(0, 0, 0),
            link=True,
            name=data["asset_data"]["name"],
            parent=None,
        )

        # Position the model in the scene
        center_obs_for_thumbnail(all_objects)

        # Select appropriate camera based on object placement type
        # Each camera is pre-configured in the template file for different angles
        camdict = {
            "GROUND": "camera ground",    # Looking down at object on ground
            "WALL": "camera wall",        # Looking at object mounted on wall
            "CEILING": "camera ceiling",  # Looking up at ceiling-mounted object
            "FLOAT": "camera float",      # Looking at floating object
        }
        bpy.context.scene.camera = bpy.data.objects[camdict[data["asset_data"]["thumbnail_snap_to"]]]

    
        # Set the frame number to get different pre-configured angles
        # The template file uses keyframes to store different viewpoints
        fdict = {
            "DEFAULT": 1,  # Best angle for object type
            "FRONT": 2,    # Direct front view
            "SIDE": 3,     # Direct side view
            "TOP": 4,      # Top-down view
        }
        s = bpy.context.scene
        s.frame_set(fdict[data["asset_data"]["thumbnail_angle"]])

        # Enable the appropriate scene collection based on object placement
        # Each collection has specific lighting and environment setup
        snapdict = {
            "GROUND": "Ground",     # Floor-based lighting setup
            "WALL": "Wall",         # Wall-mounted lighting setup
            "CEILING": "Ceiling",   # Ceiling-mounted lighting setup
            "FLOAT": "Float",       # 360-degree lighting setup
        }
        collection = bpy.context.scene.collection.children[snapdict[data["asset_data"]["thumbnail_snap_to"]]]
        collection.hide_viewport = False
        collection.hide_render = False
        collection.hide_select = False

        # Reset object rotation to ensure consistent orientation
        main_object.rotation_euler = (0, 0, 0)

        # Configure render device (GPU/CPU) and settings
        if thumbnail_use_gpu is True:
            bpy.context.scene.cycles.device = "GPU"
            compute_device_type = data.get("cycles_compute_device_type")
            if compute_device_type is not None:
                bpy.context.preferences.addons["cycles"].preferences.compute_device_type = compute_device_type
                bpy.context.preferences.addons["cycles"].preferences.refresh_devices()

        # Set render quality parameters
        s.cycles.samples = data["asset_data"]["thumbnail_samples"]
        bpy.context.view_layer.cycles.use_denoising = data["asset_data"]["thumbnail_denoising"]

        # Configure background color brightness
        bpy.data.materials["bkit background"].node_tree.nodes["Value"].outputs["Value"].default_value = data["asset_data"]["thumbnail_background_lightness"]

        # Set output resolution
        bpy.context.scene.render.resolution_x = int(data["asset_data"]["thumbnail_resolution"])
        bpy.context.scene.render.resolution_y = int(data["asset_data"]["thumbnail_resolution"])

        # Configure output path and start render
        bpy.context.scene.render.filepath = data["result_filepath"]
        print("rendering thumbnail")
        render_thumbnails()

        print("background autothumbnailer finished successfully")
        sys.exit(0)

    except Exception as e:
        print(f"background autothumbnailer failed: {e}")
        print(traceback.format_exc())
        sys.exit(1)
