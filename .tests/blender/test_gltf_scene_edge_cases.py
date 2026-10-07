"""generate_gltf must export scenes holding objects it cannot bake as they come.

Both failed whole GLTF exports in the October 2026 re-run:
- rig widget shapes ('cs_wire_eyebrow') that are in the file but not in the view
  layer: "Object ... cannot be selected because it is not in View Layer";
- meshes that already use all 8 UV maps, so no LightingUV map could be added:
  "KeyError: 'bpy_prop_collection[key]: key "LightingUV" not found'";
- the same KeyError on a mesh that also has a non-UV attribute named LightingUV:
  Blender named the new UV map "LightingUV.001" ('Hair.001', 6 Oct 2026).

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_gltf_scene_edge_cases.py
"""

import json
import os
import sys
import tempfile

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402

UV_MAP_LIMIT = 8


def _procedural_material(name: str) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    noise = nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    return material


def _procedural_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(_procedural_material(f"{name} material"))
    return obj


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _procedural_cube("Visible", (0.0, 0.0, 0.0))

    full = _procedural_cube("Full UV maps", (3.0, 0.0, 0.0))
    while len(full.data.uv_layers) < UV_MAP_LIMIT:
        full.data.uv_layers.new(name=f"Spare {len(full.data.uv_layers)}")
    full.data.uv_layers.active = full.data.uv_layers[0]
    full.data.attributes.new(name=gltf_bg_blender.UV_NAME, type="FLOAT", domain="POINT")
    tree = full.data.materials[0].node_tree
    uv_map_node = tree.nodes.new("ShaderNodeUVMap")
    uv_map_node.uv_map = "Spare 7"
    tree.links.new(uv_map_node.outputs["UV"], tree.nodes["Noise Texture"].inputs["Vector"])

    unlinked = bpy.data.objects.new("cs_wire_unlinked", bpy.data.meshes.new_from_object(full))
    unlinked.data.materials.append(_procedural_material("Unlinked material"))
    widgets = bpy.data.collections.new("Widgets")
    bpy.context.scene.collection.children.link(widgets)
    excluded = bpy.data.objects.new("cs_wire_excluded", unlinked.data.copy())
    widgets.objects.link(excluded)
    bpy.context.view_layer.layer_collection.children["Widgets"].exclude = True

    folder = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "asset.blend"))
    result_path = os.path.join(folder, "result.json")

    gltf_bg_blender.generate_gltf(result_path, ["gltf_godot"])

    with open(result_path, encoding="utf-8") as f:
        outcomes = json.load(f)
    assert "file_path" in outcomes[0], outcomes
    uv_names = [layer.name for layer in full.data.uv_layers]
    assert gltf_bg_blender.UV_NAME in uv_names, uv_names
    assert len(uv_names) == UV_MAP_LIMIT, uv_names
    assert "UVMap" in uv_names, uv_names
    assert "Spare 7" in uv_names, uv_names


main()
