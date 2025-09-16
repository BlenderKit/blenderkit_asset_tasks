"""Background script for generating material thumbnails in Blender.

This script is invoked by ``render_thumbnail.py`` and expects the path to a JSON
file (as the last CLI argument) that contains:
- material file path
- template blend file (material_thumbnailer_cycles.blend)
- result filepath for the image output
- thumbnail parameters inside ``asset_data``
"""

from __future__ import annotations
# isort: skip_file

from pathlib import Path
import json
import logging
import os
import sys
import typing as t

import bpy

# Add parent directory to Python path so we can import blenderkit_server_utils
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import append_link, utils  # noqa: E402

logger = logging.getLogger(__name__)

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def render_thumbnails() -> None:
    """Render the current scene to a still image (no animation)."""
    bpy.ops.render.render(write_still=True, animation=False)


def unhide_collection(cname: str) -> None:
    """Unhide a collection in the scene by name.

    Args:
        cname: Collection name as shown in the scene children.
    """
    collection = bpy.context.scene.collection.children[cname]
    collection.hide_viewport = False
    collection.hide_render = False
    collection.hide_select = False


if __name__ == "__main__":
    # args order must match blenderkit/autothumb.py:get_thumbnailer_args()!
    export_data_path = sys.argv[-1]
    logger.info("Preparing thumbnail scene using export data: %s", export_data_path)

    # Read JSON export data with specific exceptions
    try:
        with open(export_data_path, encoding="utf-8") as s:
            data: dict[str, t.Any] = json.load(s)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse export data JSON: %s", export_data_path)
        sys.exit(1)

    try:
        thumbnail_use_gpu = data.get("thumbnail_use_gpu")
        asset = data["asset_data"]

        mat = append_link.append_material(
            file_name=data["file_path"],
            matname=asset["name"],
            link=True,
            fake_user=False,
        )

        scene = bpy.context.scene

        colmapdict = {
            "BALL": "Ball",
            "BALL_COMPLEX": "Ball complex",
            "FLUID": "Fluid",
            "CLOTH": "Cloth",
            "HAIR": "Hair",
        }
        unhide_collection(colmapdict[asset["thumbnail_type"]])
        if asset["thumbnail_background"]:
            unhide_collection("Background")
            bpy.data.materials["bg checker colorable"].node_tree.nodes["input_level"].outputs[
                "Value"
            ].default_value = asset["thumbnail_background_lightness"]
        tscale = asset["thumbnail_scale"]
        scaler = bpy.context.view_layer.objects["scaler"]
        scaler.scale = (tscale, tscale, tscale)
        utils.activate_object(scaler)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # find any object with solidify and scale the thickness accordingly
        for ob in bpy.context.visible_objects:
            if ob.name[:15] == "MaterialPreview":
                for m in ob.modifiers:
                    if m.type == "SOLIDIFY":
                        m.thickness *= tscale

        bpy.context.view_layer.update()

        for ob in bpy.context.visible_objects:
            if ob.name[:15] == "MaterialPreview":
                utils.activate_object(ob)
                if bpy.app.version >= (3, 3, 0):
                    bpy.ops.object.transform_apply(
                        location=False,
                        rotation=False,
                        scale=True,
                        isolate_users=True,
                    )
                else:
                    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

                ob.material_slots[0].material = mat
                ob.data.use_auto_texspace = False
                ob.data.texspace_size.x = 1
                ob.data.texspace_size.y = 1
                ob.data.texspace_size.z = 1
                ob.cycles.use_adaptive_subdivision = bool(asset.get("thumbnail_adaptive_subdivision"))

                # Get texture size from dictParameters
                ts = asset["dictParameters"].get("textureSizeMeters", 1.0)
                if asset["thumbnail_type"] in ["BALL", "BALL_COMPLEX", "CLOTH"]:
                    utils.automap(
                        ob.name,
                        tex_size=ts / tscale,
                        just_scale=True,
                        bg_exception=True,
                    )
        bpy.context.view_layer.update()

        scene.cycles.volume_step_size = tscale * 0.1

        if thumbnail_use_gpu is True:
            scene.cycles.device = "GPU"
            compute_device_type = data.get("cycles_compute_device_type")
            if compute_device_type is not None:
                # DOCS: https://github.com/dfelinto/blender/blob/master/intern/cycles/blender/addon/properties.py
                prefs = bpy.context.preferences.addons["cycles"].preferences
                prefs.compute_device_type = compute_device_type
                prefs.refresh_devices()

        scene.cycles.samples = asset["thumbnail_samples"]
        bpy.context.view_layer.cycles.use_denoising = asset["thumbnail_denoising"]

        # import Blender's HDR here
        hdr_path = Path("datafiles/studiolights/world/interior.exr")
        bpath = Path(bpy.utils.resource_path("LOCAL"))
        ipath = bpath / hdr_path
        ipath_str = str(ipath)
        if ipath_str.startswith("//"):
            ipath_str = ipath_str[1:]

        img = bpy.data.images.get("interior.exr")
        if img is not None:
            img.filepath = ipath_str
            img.reload()
        else:
            logger.warning("HDR image 'interior.exr' not found in Blender data")

        scene.render.resolution_x = int(asset["thumbnail_resolution"])
        scene.render.resolution_y = int(asset["thumbnail_resolution"])

        scene.render.filepath = data["result_filepath"]
        logger.info("Rendering thumbnail to %s", scene.render.filepath)
        render_thumbnails()
        logger.info("Background autothumbnailer finished successfully (no upload)")
        sys.exit(0)

    except Exception:  # Blender ops can raise varied exceptions
        logger.exception("Background autothumbnailer failed")
        sys.exit(1)
