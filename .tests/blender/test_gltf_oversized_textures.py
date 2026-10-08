"""Textures wider than WEBP allows must still reach the GLB.

The maximal export writes WEBP, which cannot encode more than 16383 px a side: a 16K
texture left its GLB with a texture entry and no image (664 validated models carry a
16K texture; 'Rugged Rock Mountain Photoscan' exported untextured, October 2026).

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_oversized_textures.py
"""

import json
import os
import struct
import sys
import tempfile

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402

WEBP_LIMIT = 16383


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    image = bpy.data.images.new("Wide", width=WEBP_LIMIT + 17, height=64)
    image.generated_color = (0.2, 0.6, 0.3, 1.0)
    image.pack()
    material = bpy.data.materials.new("Wide texture")
    material.use_nodes = True
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    bsdf = material.node_tree.nodes["Principled BSDF"]
    material.node_tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    obj.data.materials.append(material)
    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot"])

    with open(result_path, encoding="utf-8") as f:
        glb_path = json.load(f)[0]["file_path"]
    with open(glb_path, "rb") as f:
        data = f.read()
    gltf = json.loads(data[20 : 20 + struct.unpack_from("<I", data, 12)[0]])
    sources = [
        texture.get("source", texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
        for texture in gltf.get("textures", [])
    ]
    assert sources and None not in sources, ("textures without an image", gltf.get("textures"), gltf.get("images"))


main()
