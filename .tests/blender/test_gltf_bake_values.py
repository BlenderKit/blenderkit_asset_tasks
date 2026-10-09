"""Baked GLTF textures must carry the material's own base color and metallic value.

The color pass baked Cycles' diffuse color, which is base color x (1 - metallic), less
transmission and subsurface, and nothing at all for a Glossy BSDF: procedural metals,
glass and skin came out black or empty (112 of 176 baked base-color images in an October
2026 sample of re-processed models). The metallic pass baked glossy lighting instead of
the Metallic input. A mesh whose UVs all sit on one point baked nothing.
A label printed by an image mask that mixes two Principled shaders lost the label:
the material passed as image-only and exported the first shader's flat color
('Beauty Cream Bottle Tube', October 2026).
A diffuse color under a Glossy coat (the classic pre-Principled plastic) baked
washed out toward the coat's white and partly metallic.
An object hidden for rendering baked nothing: its GLB came out black ('Pink Eraser').

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_bake_values.py
"""

import json
import os
import struct
import sys
import tempfile

import bpy
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402

TOLERANCE = 0.08
DOMINANT = 0.6
OTHERS = 0.35
COVERED = 0.02
MASK_SIZE = 64


def _constant_ramp(tree: bpy.types.NodeTree, color: tuple[float, float, float]) -> bpy.types.NodeSocket:
    """A noise texture through a single-color ramp: procedural for the baker, constant in value."""
    noise = tree.nodes.new("ShaderNodeTexNoise")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    for element in ramp.color_ramp.elements:
        element.color = (*color, 1.0)
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    return ramp.outputs["Color"]


def _cube(name: str, location: float, color: tuple[float, float, float], **inputs: float) -> None:
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    tree.links.new(_constant_ramp(tree, color), bsdf.inputs["Base Color"])
    for socket_name, value in inputs.items():
        if socket_name == "linked_metallic":
            tree.links.new(_constant_ramp(tree, (value, value, value)), bsdf.inputs["Metallic"])
        else:
            bsdf.inputs[socket_name].default_value = value
    obj.data.materials.append(material)


def _glossy_cube(name: str, location: float, color: tuple[float, float, float]) -> None:
    """A metal built the pre-Principled way: a Glossy BSDF with a procedural color."""
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.remove(tree.nodes["Principled BSDF"])
    glossy = tree.nodes.new("ShaderNodeBsdfGlossy")
    tree.links.new(_constant_ramp(tree, color), glossy.inputs["Color"])
    tree.links.new(glossy.outputs["BSDF"], tree.nodes["Material Output"].inputs["Surface"])
    obj.data.materials.append(material)


def _coated_cube(name: str, location: float) -> None:
    """Red Diffuse and white Glossy mixed half and half: a plastic, not a metal."""
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.remove(tree.nodes["Principled BSDF"])
    diffuse = tree.nodes.new("ShaderNodeBsdfDiffuse")
    tree.links.new(_constant_ramp(tree, (0.8, 0.05, 0.05)), diffuse.inputs["Color"])
    glossy = tree.nodes.new("ShaderNodeBsdfGlossy")
    glossy.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.5
    tree.links.new(diffuse.outputs["BSDF"], mix.inputs[1])
    tree.links.new(glossy.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], tree.nodes["Material Output"].inputs["Surface"])
    obj.data.materials.append(material)


def _label_cube(name: str, location: float) -> None:
    """Red and blue Principled shaders mixed by an image's alpha, left half opaque."""
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    red = tree.nodes["Principled BSDF"]
    red.inputs["Base Color"].default_value = (0.8, 0.05, 0.05, 1.0)
    blue = tree.nodes.new("ShaderNodeBsdfPrincipled")
    blue.inputs["Base Color"].default_value = (0.05, 0.05, 0.8, 1.0)
    mask = bpy.data.images.new("label mask", MASK_SIZE, MASK_SIZE, alpha=True)
    alpha = np.zeros((MASK_SIZE, MASK_SIZE, 4), dtype=np.float32)
    alpha[:, : MASK_SIZE // 2] = 1.0
    mask.pixels.foreach_set(alpha.ravel())
    mask.pack()
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = mask
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(texture.outputs["Alpha"], mix.inputs["Fac"])
    tree.links.new(red.outputs["BSDF"], mix.inputs[1])
    tree.links.new(blue.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], tree.nodes["Material Output"].inputs["Surface"])
    obj.data.materials.append(material)


def _textures(glb_path: str) -> dict[str, dict[str, np.ndarray]]:
    """Mean RGB over the baked texels of the base-color and metallic-roughness textures per material."""
    with open(glb_path, "rb") as f:
        data = f.read()
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + json_length])
    offset = 20 + json_length
    binary = data[offset + 8 : offset + 8 + struct.unpack_from("<I", data, offset)[0]]

    def mean_rgb(texture_index: int) -> np.ndarray:
        texture = gltf["textures"][texture_index]
        source = texture.get("source", texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
        image_info = gltf["images"][source]
        view = gltf["bufferViews"][image_info["bufferView"]]
        start = view.get("byteOffset", 0)
        path = os.path.join(tempfile.mkdtemp(), "texture." + image_info["mimeType"].split("/")[1])
        with open(path, "wb") as f:
            f.write(binary[start : start + view["byteLength"]])
        image = bpy.data.images.load(path)
        image.colorspace_settings.name = "Non-Color"
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rgb = pixels.reshape(-1, 4)[:, :3]
        # Texels outside the UV islands stay at the bake image's black fill.
        covered = rgb[rgb.max(axis=1) > COVERED]
        return covered.mean(axis=0) if len(covered) else np.zeros(3)

    result = {}
    for material in gltf["materials"]:
        pbr = material.get("pbrMetallicRoughness", {})
        result[material["name"]] = {
            key: mean_rgb(pbr[slot]["index"])
            for key, slot in (("color", "baseColorTexture"), ("orm", "metallicRoughnessTexture"))
            if slot in pbr
        }
    return result


def _named(textures: dict[str, dict[str, np.ndarray]], name: str) -> dict[str, np.ndarray]:
    return next(value for key, value in textures.items() if key.startswith(name))


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _cube("Red metal", 0.0, (0.8, 0.05, 0.05), Metallic=1.0)
    _cube("Green glass", 3.0, (0.05, 0.8, 0.05), **{"Transmission Weight": 1.0})
    _cube("Blue plastic", 6.0, (0.05, 0.05, 0.8), linked_metallic=0.75)
    _glossy_cube("Yellow glossy", 9.0, (0.8, 0.8, 0.05))
    _cube("Flat UV", 12.0, (0.05, 0.8, 0.8))
    _label_cube("Label", 15.0)
    _coated_cube("Coated plastic", 18.0)
    _cube("Render hidden", 21.0, (0.8, 0.05, 0.8))
    bpy.data.objects["Render hidden"].hide_render = True
    for loop_uv in bpy.data.objects["Flat UV"].data.uv_layers[0].data:
        loop_uv.uv = (0.0, 0.0)
    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot"])

    with open(result_path, encoding="utf-8") as f:
        glb_path = json.load(f)[0]["file_path"]
    textures = _textures(glb_path)
    red, green, blue, yellow, flat, label, coated, hidden = (
        _named(textures, name)
        for name in (
            "Red metal",
            "Green glass",
            "Blue plastic",
            "Yellow glossy",
            "Flat UV",
            "Label",
            "Coated plastic",
            "Render hidden",
        )
    )
    assert coated["color"][0] > DOMINANT, ("coated plastic color", coated["color"])
    assert coated["color"][1:].max() < OTHERS, ("coated plastic color", coated["color"])
    assert abs(coated["orm"][2]) < TOLERANCE, ("coated plastic metallic", coated["orm"])
    assert hidden["color"][[0, 2]].min() > DOMINANT, ("render-hidden object color", hidden["color"])
    assert "color" in label, ("label exported without a base color texture", label)
    assert min(label["color"][0], label["color"][2]) > OTHERS, ("label color mixes red and blue", label["color"])
    assert flat["color"][1:].min() > DOMINANT and flat["color"][0] < OTHERS, ("flat UV color", flat["color"])
    # Base colors come back in sRGB, so compare the dominant channel instead of exact values.
    assert red["color"][0] > DOMINANT and red["color"][1:].max() < OTHERS, ("red metal base color", red["color"])
    assert green["color"][1] > DOMINANT and green["color"][[0, 2]].max() < OTHERS, ("green glass color", green["color"])
    assert yellow["color"][2] < OTHERS, ("yellow glossy color", yellow["color"])
    assert yellow["color"][:2].min() > DOMINANT, ("yellow glossy color", yellow["color"])
    assert abs(yellow["orm"][2] - 1.0) < TOLERANCE, ("yellow glossy metallic", yellow["orm"])
    assert abs(red["orm"][2] - 1.0) < TOLERANCE, ("red metal metallic", red["orm"])
    assert abs(blue["orm"][2] - 0.75) < TOLERANCE, ("blue plastic metallic", blue["orm"])
    assert abs(green["orm"][2]) < TOLERANCE, ("green glass metallic", green["orm"])


main()
