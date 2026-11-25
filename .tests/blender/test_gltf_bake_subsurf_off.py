"""generate_gltf must disable every Subdivision modifier before the first bake.

A bake renders the whole scene, so objects not yet preprocessed were baked into it
subdivided at their render level: 'Pink Hydrangea Bouquet' (11 objects, 531k faces)
needed over 16 GB while baking its first object and was killed by the runner
(bg_returncode=-9, 271 models in the November 2026 re-run); with subdivision off
everywhere up front it peaked at 2.5 GB. The export drops subdivision anyway.

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_bake_subsurf_off.py
"""

import json
import os
import sys
import tempfile

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402


def _procedural_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    material = bpy.data.materials.new(f"{name} material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    noise = nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)
    subsurf = obj.modifiers.new("Subdivision", "SUBSURF")
    subsurf.levels = 1
    subsurf.render_levels = 3
    return obj


def _subdivided_at_bake() -> list[str]:
    return [
        obj.name
        for obj in bpy.context.scene.objects
        if any(mod.type == "SUBSURF" and mod.show_render for mod in obj.modifiers)
    ]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _procedural_cube("First", (0.0, 0.0, 0.0))
    _procedural_cube("Second", (3.0, 0.0, 0.0))

    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    seen: list[tuple[str, list[str]]] = []
    bake = gltf_bg_blender.bake_all_procedural_textures

    def recording_bake(obj: bpy.types.Object) -> None:
        seen.append((obj.name, _subdivided_at_bake()))
        bake(obj)

    gltf_bg_blender.bake_all_procedural_textures = recording_bake
    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot"])

    with open(result_path, encoding="utf-8") as f:
        outcomes = json.load(f)
    assert "file_path" in outcomes[0], outcomes
    assert [name for name, _ in seen] == ["First", "Second"], seen
    assert all(not subdivided for _, subdivided in seen), seen


main()
