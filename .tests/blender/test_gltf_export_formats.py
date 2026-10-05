"""generate_gltf must bake once and report one outcome per requested format.

Both GLTF formats come from a single bake, so a failed export of one format
(some Blender builds ship without the Draco library) must not cost the other
its file, and each format must land in its own file.

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_export_formats.py
"""

import json
import os
import sys
import tempfile
from typing import Any

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402


def _procedural_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    material = bpy.data.materials.new("Noise")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    noise = nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)
    return obj


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _procedural_cube()
    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))

    baked: list[str] = []
    bake = gltf_bg_blender.bake_all_procedural_textures

    def counting_bake(obj: bpy.types.Object) -> Any:
        baked.append(obj.name)
        return bake(obj)

    gltf_bg_blender.bake_all_procedural_textures = counting_bake
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot", "gltf"])

    with open(result_path, encoding="utf-8") as f:
        outcomes = json.load(f)
    assert baked == ["Cube"], baked
    assert [outcome["type"] for outcome in outcomes] == ["gltf_godot", "gltf"], outcomes
    assert "file_path" in outcomes[0], outcomes
    for outcome in outcomes:
        assert ("file_path" in outcome) != ("error" in outcome), outcome
    files = [outcome["file_path"] for outcome in outcomes if "file_path" in outcome]
    assert len(set(files)) == len(files), files
    for file_path in files:
        assert os.path.basename(file_path) == "asset.glb", file_path
        assert os.path.getsize(file_path) > 0, file_path


main()
