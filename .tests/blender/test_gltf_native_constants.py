"""Materials exported without baking must keep their constant values.

simplify_material_to_principled() rebuilt a glTF-native material as a fresh
Principled BSDF and wired only its image textures, so every unlinked value fell
back to Blender's defaults: a black plastic or a chrome came out as 0.8 grey,
roughness 0.5, metallic 0 ('Stella garden Spot Light', October 2026).

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_native_constants.py
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

TOLERANCE = 0.02
SIZE = 8


def _cube(name: str, location: float, **inputs: object) -> bpy.types.Material:
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    for socket_name, value in inputs.items():
        bsdf.inputs[socket_name].default_value = value
    obj.data.materials.append(material)
    return material


def _gltf_materials(glb_path: str) -> dict[str, dict]:
    with open(glb_path, "rb") as f:
        data = f.read()
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + json_length])
    return {material["name"]: material for material in gltf["materials"]}


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) < TOLERANCE


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _cube("Chrome", 0.0, **{"Base Color": (0.8, 0.05, 0.05, 1.0), "Metallic": 1.0, "Roughness": 0.25})
    textured = _cube("Textured", 3.0, Metallic=1.0, Roughness=0.1)
    image = bpy.data.images.new("base", SIZE, SIZE)
    image.pack()
    tree = textured.node_tree
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    tree.links.new(texture.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    # A second user makes the bake step give each object its own copy, as asset files often do.
    bpy.ops.mesh.primitive_cube_add(location=(6.0, 0.0, 0.0))
    bpy.context.active_object.data.materials.append(bpy.data.materials["Chrome"])
    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot"])

    with open(result_path, encoding="utf-8") as f:
        materials = _gltf_materials(json.load(f)[0]["file_path"])
    for name, material in materials.items():
        pbr = material["pbrMetallicRoughness"]
        if name.startswith("Chrome"):
            color = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
            assert _close(color[0], 0.8) and _close(color[1], 0.05), (name, "base color", color)
            assert _close(pbr.get("metallicFactor", 1.0), 1.0), (name, "metallic", pbr)
            assert _close(pbr.get("roughnessFactor", 1.0), 0.25), (name, "roughness", pbr)
        if name.startswith("Textured"):
            assert "baseColorTexture" in pbr, (name, "lost its texture", pbr)
            assert _close(pbr.get("metallicFactor", 1.0), 1.0), (name, "metallic", pbr)
            assert _close(pbr.get("roughnessFactor", 1.0), 0.1), (name, "roughness", pbr)
    assert any(name.startswith("Chrome") for name in materials), materials


main()
