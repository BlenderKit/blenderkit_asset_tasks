"""generate_gltf must bake and export assets with hidden objects and unusual node graphs.

All failed whole GLTF exports in the October 2026 re-run:
- an object hidden in the viewport and from rendering: entering Edit mode for the UV
  pack, then leaving the failed attempt, raised "Cannot edit hidden object" ('Carpet');
- an object hidden by a driver (a character rig's outfit switch): every update hid it
  again, so its bake failed with "bake.poll() failed" ('Foxy');
- a group node without a node tree: "'NoneType' object has no attribute 'nodes'"
  ('Eket ikea');
- node links that form a loop: RecursionError ('Aquila Live Edge table').

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 \
        --python .tests/blender/test_gltf_node_graph_edge_cases.py
"""

import json
import os
import sys
import tempfile

import bpy
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402

# A failed bake leaves the image black (0); baked noise is well above this.
MIN_BAKED_COLOR = 0.05


def _procedural_cube(name: str, location: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=(location, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(f"{name} material")
    material.use_nodes = True
    tree = material.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    tree.links.new(noise.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)
    return obj


def _baked_color_mean(name: str) -> float:
    image = bpy.data.images[f"{name}_bake_clr"]
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return float(pixels.reshape(-1, 4)[:, :3].mean())


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    hidden = _procedural_cube("Hidden", 0.0)
    hidden.hide_viewport = True
    hidden.hide_render = True

    switched = _procedural_cube("Switched off", 3.0)
    for path in ("hide_viewport", "hide_render"):
        driver = switched.driver_add(path).driver
        driver.type = "SCRIPTED"
        driver.expression = "1"

    empty_group = _procedural_cube("Empty group", 6.0)
    empty_group.data.materials[0].node_tree.nodes.new("ShaderNodeGroup")

    looped = _procedural_cube("Looped", 9.0)
    tree = looped.data.materials[0].node_tree
    first, second = tree.nodes.new("ShaderNodeMath"), tree.nodes.new("ShaderNodeMath")
    tree.links.new(first.outputs[0], second.inputs[0])
    tree.links.new(second.outputs[0], first.inputs[0])

    bpy.context.view_layer.update()
    assert not switched.visible_get(), "the driver must hide the object before the export"

    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf"])

    with open(result_path, encoding="utf-8") as f:
        outcomes = json.load(f)
    assert "file_path" in outcomes[0], outcomes
    for name in ("Hidden", "Switched off"):
        assert _baked_color_mean(name) > MIN_BAKED_COLOR, (name, "baked black", _baked_color_mean(name))


main()
