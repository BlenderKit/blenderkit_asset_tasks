"""ensure_lighting_uv must leave Blender running and the original UV map active.

It kept a reference to the active UV map across uv_layers.new(), bm.to_mesh()
and an EDIT-mode round trip, which reallocate the mesh's layers. Assigning
the stale reference back segfaulted Blender 5.1/5.2: every one of the 50
GLTF export segfaults in the 795 webhook runs of 28 Sep - 2 Oct 2026.

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_lighting_uv.py
"""

import os
import sys

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402


def _cube(index: int, extra_uv_maps: int, *, uvs_outside_unit_square: bool) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=(index * 3.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = f"Cube {index}"
    mesh = obj.data
    for extra in range(extra_uv_maps):
        mesh.uv_layers.new(name=f"Extra {extra}")
    mesh.uv_layers.active = mesh.uv_layers[0]
    if uvs_outside_unit_square:
        for loop_uv in mesh.uv_layers[0].data:
            loop_uv.uv = (loop_uv.uv[0] * 3.0, loop_uv.uv[1] * 3.0)
    return obj


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for index in range(40):
        obj = _cube(index, extra_uv_maps=index % 4, uvs_outside_unit_square=index % 2 == 0)
        original = obj.data.uv_layers.active.name

        gltf_bg_blender.ensure_lighting_uv(obj)

        assert gltf_bg_blender.UV_NAME in obj.data.uv_layers, obj.name
        assert obj.data.uv_layers.active.name == original, (obj.name, obj.data.uv_layers.active.name)


main()
