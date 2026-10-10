"""The UV overlap check must not compare every face of two huge islands at once.

_check_island_pair built a faces_a x faces_b matrix: for two islands of 556,320 faces
it asked numpy for 288 GiB ("Unable to allocate 288. GiB for an array with shape
(556320, 556320)") and the GLTF export died. Above MAX_FACE_PAIRS_FOR_OVERLAP_CHECK
the pair is treated as overlapping, as too many islands already are.

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_uv_overlap_limits.py
"""

import os
import sys

import bmesh

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import gltf_bg_blender  # noqa: E402

# Two triangles whose UV bounds overlap while the triangles themselves do not.
LOWER_LEFT = ((0.0, 0.0), (0.6, 0.0), (0.0, 0.6))
UPPER_RIGHT = ((0.62, 0.3), (0.62, 0.62), (0.3, 0.62))


def _two_island_mesh() -> tuple[bmesh.types.BMesh, bmesh.types.BMLayerItem]:
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for offset, triangle in ((0.0, LOWER_LEFT), (5.0, UPPER_RIGHT)):
        verts = [bm.verts.new((offset + u, v, 0.0)) for u, v in triangle]
        face = bm.faces.new(verts)
        for loop, uv in zip(face.loops, triangle, strict=True):
            loop[uv_layer].uv = uv
    # Fresh faces all have index -1; a mesh loaded from an object has real ones.
    bm.faces.index_update()
    return bm, uv_layer


def main() -> None:
    bm, uv_layer = _two_island_mesh()
    assert gltf_bg_blender.check_uv_face_overlap(bm, uv_layer) is False, "triangles do not overlap"

    gltf_bg_blender.MAX_FACE_PAIRS_FOR_OVERLAP_CHECK = 0
    assert gltf_bg_blender.check_uv_face_overlap(bm, uv_layer) is True, "a pair over the limit is not compared"
    bm.free()


main()
