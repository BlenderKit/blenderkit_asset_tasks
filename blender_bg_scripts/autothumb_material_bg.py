"""
Background script for generating material thumbnails.
This script is called by render_thumbnail.py and expects:
- material file path
- template blend file (material_thumbnailer_cycles.blend)
- result filepath for the JSON output
- thumbnail parameters in the asset_data
"""

import bpy
import os
import sys
import json
import traceback
from pathlib import Path
# Add parent directory to Python path so we can import blenderkit_server_utils
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)

from blenderkit_server_utils import append_link, paths, utils


def render_thumbnails():
    bpy.ops.render.render(write_still=True, animation=False)


def unhide_collection(cname):
    collection = bpy.context.scene.collection.children[cname]
    collection.hide_viewport = False
    collection.hide_render = False
    collection.hide_select = False


if __name__ == "__main__":
    try:
        # args order must match the order in blenderkit/autothumb.py:get_thumbnailer_args()!
        BLENDERKIT_EXPORT_DATA = sys.argv[-1]

        print("preparing thumbnail scene")
        print(BLENDERKIT_EXPORT_DATA)
        with open(BLENDERKIT_EXPORT_DATA, "r", encoding="utf-8") as s:
            data = json.load(s)
            # append_material(file_name, matname = None, link = False, fake_user = True)

        thumbnail_use_gpu = data.get("thumbnail_use_gpu")
        

        mat = append_link.append_material(
            file_name=data["file_path"],
            matname=data["asset_data"]["name"],
            link=True,
            fake_user=False,
        )

        s = bpy.context.scene

        colmapdict = {
            "BALL": "Ball",
            "BALL_COMPLEX": "Ball complex",
            "FLUID": "Fluid",
            "CLOTH": "Cloth",
            "HAIR": "Hair",
        }
        unhide_collection(colmapdict[data["asset_data"]["thumbnail_type"]])
        if data["asset_data"]["thumbnail_background"]:
            unhide_collection("Background")
            bpy.data.materials["bg checker colorable"].node_tree.nodes[
                "input_level"
            ].outputs["Value"].default_value = data["asset_data"]["thumbnail_background_lightness"]
        tscale = data["asset_data"]["thumbnail_scale"]
        scaler = bpy.context.view_layer.objects["scaler"]
        scaler.scale = (tscale, tscale, tscale)
        utils.activate_object(scaler)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # find any object with solidify and scale the thickness accordingly 
        # this currently involves only cloth preview, but might also others or other scale depended modifiers
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
                        location=False, rotation=False, scale=True, isolate_users=True
                    )
                else:
                    bpy.ops.object.transform_apply(
                        location=False, rotation=False, scale=True
                    )
                bpy.ops.object.transform_apply(
                    location=False, rotation=False, scale=True
                )

                ob.material_slots[0].material = mat
                ob.data.use_auto_texspace = False
                ob.data.texspace_size.x = 1  # / tscale
                ob.data.texspace_size.y = 1  # / tscale
                ob.data.texspace_size.z = 1  # / tscale
                if data["asset_data"]["thumbnail_adaptive_subdivision"] == True:
                    ob.cycles.use_adaptive_subdivision = True

                else:
                    ob.cycles.use_adaptive_subdivision = False
                # Get texture size from dictParameters
                ts = data["asset_data"]["dictParameters"].get("textureSizeMeters", 1.0)
                if data["asset_data"]["thumbnail_type"] in ["BALL", "BALL_COMPLEX", "CLOTH"]:
                    utils.automap(
                        ob.name,
                        tex_size=ts / tscale,
                        just_scale=True,
                        bg_exception=True,
                    )
        bpy.context.view_layer.update()

        s.cycles.volume_step_size = tscale * 0.1

        if thumbnail_use_gpu is True:
            bpy.context.scene.cycles.device = "GPU"
            compute_device_type = data.get("cycles_compute_device_type")
            if compute_device_type is not None:
                # DOCS:https://github.com/dfelinto/blender/blob/master/intern/cycles/blender/addon/properties.py
                bpy.context.preferences.addons[
                    "cycles"
                ].preferences.compute_device_type = compute_device_type
                bpy.context.preferences.addons["cycles"].preferences.refresh_devices()

        s.cycles.samples = data["asset_data"]["thumbnail_samples"]
        bpy.context.view_layer.cycles.use_denoising = data["asset_data"]["thumbnail_denoising"]

        # import blender's HDR here
        hdr_path = Path("datafiles/studiolights/world/interior.exr")
        bpath = Path(bpy.utils.resource_path("LOCAL"))
        ipath = bpath / hdr_path
        ipath = str(ipath)

        # this  stuff is for mac and possibly linux. For blender // means relative path.
        # for Mac, // means start of absolute path
        if ipath.startswith("//"):
            ipath = ipath[1:]

        img = bpy.data.images["interior.exr"]
        img.filepath = ipath
        img.reload()

        bpy.context.scene.render.resolution_x = int(data["asset_data"]["thumbnail_resolution"])
        bpy.context.scene.render.resolution_y = int(data["asset_data"]["thumbnail_resolution"])

        bpy.context.scene.render.filepath = data["result_filepath"]
        print("rendering thumbnail")
        # bpy.ops.wm.save_as_mainfile(filepath='C:/tmp/test.blend')
        render_thumbnails()
        print(
            "background autothumbnailer finished successfully (no upload)"
        )
        sys.exit(0)


    except Exception as e:
        print(f"background autothumbnailer failed: {e}")
        print(traceback.format_exc())
        sys.exit(1)
