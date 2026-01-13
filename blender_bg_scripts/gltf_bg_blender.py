"""Background Blender script for generating GLTF/GLB files.

Supports two targets:
- "gltf": Optimized for web (Draco compression)
- "gltf_godot": Optimized for Godot (no Draco, broader compatibility)

Pipeline orientation:
1. `generate_gltf()` sets up the scene, cycling through every mesh to disable heavy modifiers
    and call `bake_all_procedural_textures()`.
2. Baking guarantees a `LightingUV` layer, creates shared bake images for each entry in
    `BAKE_PASSES`, and runs Blender's bake operator before packing images and caching them
    per object.
3. `connect_baked_textures()` swaps the original shader trees for versions driven by the
    baked images (falling back to transparent placeholders for materials flagged in
    `FAILED_MATERIAL_FIXES`).
4. Once materials are image-backed, the script attempts a "maximal" then "minimal" glTF
    export preset and writes a JSON descriptor that downstream tooling consumes.

These notes are only meant for high-level orientation; see function docstrings for details.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from collections.abc import Iterable
from typing import Any

import addon_utils  # type: ignore
import bmesh  # type: ignore
import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Vector  # type: ignore

# Add utils path for imports inside Blender
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import log  # noqa: E402, I001


logger = log.create_logger(__name__)


LOCAL_DEBUG = 0  # os.environ.get("LOCAL_DEBUG", "") == "1"

MAX_EXPANDED_NODES = 2000
MAX_GROUP_REUSE = 8
MAX_DEPTH = 32

UV_NAME = "LightingUV"
MARGIN = 0.0003  # UV margin for light-map packing
MAX_ISLAND_OVERLAPS = 0
BLEED = 4  # pixels
UV_ISLAND_EPSILON = 1e-5
UV_SPACE_EPSILON = 1e-5
TEX_SIZE = int(float(os.environ.get("GLTF_TEXTURE_SIZE", "1024")))  # size for baked textures
BAKE_PASSES: list[dict[str, Any]] = [
    {
        "key": "diffuse",
        "type": "DIFFUSE",
        "suffix": "clr",
        "pass_filter": {"COLOR"},
        "colorspace": "sRGB",
        "usage": "color",
    },
    {
        "key": "normal",
        "type": "NORMAL",
        "suffix": "n",
        "pass_filter": None,
        "colorspace": "Non-Color",
        "usage": "normal",
    },
    {
        "key": "occlusion",
        "type": "AO",
        "suffix": "occl",
        "pass_filter": None,
        "colorspace": "Non-Color",
        "usage": "occlusion",
    },
    {
        "key": "roughness",
        "type": "ROUGHNESS",
        "suffix": "rough",
        "pass_filter": None,
        "colorspace": "Non-Color",
        "usage": "roughness",
    },
    {
        "key": "metallic",
        "type": "GLOSSY",
        "suffix": "metal",
        "pass_filter": None,
        "colorspace": "Non-Color",
        "usage": "metallic",
    },
]

DRACO_MESH_COMPRESSION: dict[str, Any] = {
    "export_draco_mesh_compression_enable": True,
}
MINIMAL_GLTF: dict[str, Any] = {
    "export_format": "GLB",  # single GLTF binary file
    "export_apply": True,  # apply modifiers
    "export_texcoords": True,
    "export_normals": True,
}
MAXIMAL_GLTF: dict[str, Any] = MINIMAL_GLTF | {
    "export_image_format": "WEBP",
    "export_image_add_webp": True,
    "export_jpeg_quality": 50,
    "export_image_quality": 50,
}

PROCEDURAL_MATERIALS: list[bpy.types.Material] = []
FAILED_MATERIAL_FIXES: dict[int, dict[str, Any]] = {}
BAKED_MATERIAL_DATA: dict[int, dict[str, Any]] = {}
OBJECT_BAKE_DATA: dict[int, dict[str, Any]] = {}


_DIRECT_TEXTURE_INPUTS_BY_SHADER = {
    "BSDF_PRINCIPLED": ("Base Color", "Normal", "Metallic", "Roughness", "Specular", "Alpha"),
    "BSDF_DIFFUSE": ("Color",),
    "EMISSION": ("Color",),
    "BSDF_GLOSSY": ("Color",),
}

_SHADER_TEXTURE_SLOTS = {
    "BSDF_PRINCIPLED": ("Base Color", "Metallic", "Roughness", "Specular", "Alpha"),
}

_NORMAL_CHAIN_NODES = {
    "NORMAL_MAP": ("Color",),
    "BUMP": ("Height",),
}

_ORM_CHANNEL_KEYS = ("occlusion", "roughness", "metallic")


scene_path = bpy.data.filepath
scene_dir = os.path.dirname(bpy.path.abspath(scene_path))


def disable_subsurf_modifiers(obj: bpy.types.Object) -> None:
    """Disable Subdivision Surface modifiers on an object.

    Args:
        obj: Blender object whose modifiers should be adjusted.

    Returns:
        None.
    """
    for mod in obj.modifiers:
        if mod.type != "SUBSURF":
            continue
        mod.show_viewport = False
        mod.show_render = False
    logger.info("Disabled Subdivision Surface modifier for '%s'", obj.name)


# region UVMAP


def move_uv_to_bottom(obj: bpy.types.Object, index: int) -> None:
    """Move a UV layer to the last position.

    Args:
        obj: Object whose UV layer order should change.
        index: Current index of the UV layer that needs to move.

    Returns:
        None.
    """
    uvs = obj.data.uv_layers
    uvs.active_index = index
    new_name = uvs.active.name

    bpy.context.view_layer.objects.active = obj
    obj.select_set(state=True)
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.uv_texture_add()
    make_uv_active(obj, new_name)
    bpy.ops.mesh.uv_texture_remove()

    uvs.active_index = len(uvs) - 1
    uvs.active.name = new_name


def make_active_uv_first(obj: bpy.types.Object) -> None:
    """Reorder UV layers so the active layer becomes first.

    Args:
        obj: Object whose UV layer order should be normalized.

    Returns:
        None.
    """
    uvs = obj.data.uv_layers
    logger.info("Number of UV layers found on object '%s': %d", obj.name, len(uvs))
    active_layer = uvs.active
    if not active_layer:
        logger.warning("No UV layers found on object '%s'", obj.name)
        return
    orig_name = active_layer.name
    orig_index = uvs.active_index
    if orig_index == 0:
        return

    logger.info("UVs before order: %s (%s is active)", list(uvs), orig_name)
    for idx in range(len(uvs)):
        if idx == orig_index:
            continue
        if idx < orig_index:
            move_uv_to_bottom(obj, 0)
        else:
            move_uv_to_bottom(obj, 1)

    make_uv_active(obj, orig_name)
    logger.info("UVs after order: %s (%s is active)", list(uvs), uvs.active.name)


def make_uv_active(obj: bpy.types.Object, name: str) -> None:
    """Set a UV layer active by name.

    Args:
        obj: Blender object with UV layers.
        name: Name of the UV layer to activate.

    Returns:
        None.
    """
    uvs = obj.data.uv_layers
    for uv in uvs:
        if uv.name == name:
            uvs.active = uv
            return
    logger.warning("Active UV '%s' not found (unexpected)", name)


def _calc_uv_bounds(coords: list[Vector]) -> tuple[Vector, Vector]:
    """Compute min/max UV bounds for supplied coordinates.

    Args:
        coords: UV coordinates forming a polygon.

    Returns:
        A tuple containing the minimum and maximum UV vectors.
    """
    min_u = min(v.x for v in coords)
    max_u = max(v.x for v in coords)
    min_v = min(v.y for v in coords)
    max_v = max(v.y for v in coords)
    return Vector((min_u, min_v)), Vector((max_u, max_v))


def _uv_coords_outside_unit_square(coords: list[Vector]) -> bool:
    """Check if any UV lies outside the [0, 1] interval.

    Args:
        coords: UV coordinates to test.

    Returns:
        True when at least one coordinate exceeds the tolerated range, otherwise False.
    """
    for uv in coords:
        if (
            uv.x < -UV_SPACE_EPSILON
            or uv.x > 1.0 + UV_SPACE_EPSILON
            or uv.y < -UV_SPACE_EPSILON
            or uv.y > 1.0 + UV_SPACE_EPSILON
        ):
            return True
    return False


def _orientation(a: Vector, b: Vector, c: Vector) -> float:
    """Compute the signed area of triangle ABC.

    Args:
        a: First vertex of the triangle.
        b: Second vertex of the triangle.
        c: Third vertex of the triangle.

    Returns:
        A signed float whose sign indicates winding (positive == counter-clockwise).
    """
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(a: Vector, b: Vector, c: Vector) -> bool:
    """Check if point b lies on segment ac.

    Args:
        a: Segment start point.
        b: Test point.
        c: Segment end point.

    Returns:
        True when b lies on the segment within tolerance, otherwise False.
    """
    return (
        min(a.x, c.x) - UV_SPACE_EPSILON <= b.x <= max(a.x, c.x) + UV_SPACE_EPSILON
        and min(a.y, c.y) - UV_SPACE_EPSILON <= b.y <= max(a.y, c.y) + UV_SPACE_EPSILON
    )


def _segments_intersect(p1: Vector, p2: Vector, q1: Vector, q2: Vector) -> bool:
    """Determine whether two 2D segments intersect.

    Args:
        p1: First endpoint of the first segment.
        p2: Second endpoint of the first segment.
        q1: First endpoint of the second segment.
        q2: Second endpoint of the second segment.

    Returns:
        True when the segments intersect or touch, otherwise False.
    """
    o1 = _orientation(p1, p2, q1)
    o2 = _orientation(p1, p2, q2)
    o3 = _orientation(q1, q2, p1)
    o4 = _orientation(q1, q2, p2)

    def _sign(val: float) -> int:
        if abs(val) <= UV_SPACE_EPSILON:
            return 0
        return 1 if val > 0 else -1

    s1 = _sign(o1)
    s2 = _sign(o2)
    s3 = _sign(o3)
    s4 = _sign(o4)

    if s1 != s2 and s3 != s4:
        return True

    if s1 == 0 and _on_segment(p1, q1, p2):
        return True
    if s2 == 0 and _on_segment(p1, q2, p2):
        return True
    if s3 == 0 and _on_segment(q1, p1, q2):
        return True
    if s4 == 0 and _on_segment(q1, p2, q2):  # noqa: SIM103
        return True
    return False


def _polygons_overlap(coords_a: list[Vector], coords_b: list[Vector]) -> bool:
    """Determine whether two UV polygons overlap via edge and containment tests.

    Args:
        coords_a: UV coordinates defining the first polygon.
        coords_b: UV coordinates defining the second polygon.

    Returns:
        True when the polygons intersect or one is contained in the other, otherwise False.
    """
    if len(coords_a) < 3 or len(coords_b) < 3:  # noqa: PLR2004
        return False

    edges_a = [(coords_a[i], coords_a[(i + 1) % len(coords_a)]) for i in range(len(coords_a))]
    edges_b = [(coords_b[i], coords_b[(i + 1) % len(coords_b)]) for i in range(len(coords_b))]
    for p1, p2 in edges_a:
        for q1, q2 in edges_b:
            if _segments_intersect(p1, p2, q1, q2):
                return True

    if _point_in_polygon(coords_a[0], coords_b):
        return True
    if _point_in_polygon(coords_b[0], coords_a):  # noqa: SIM103
        return True
    return False


def _point_in_polygon(point: Vector, polygon: list[Vector]) -> bool:
    """Return True if a point lies inside (or on) a polygon using ray casting.

    Args:
        point: Point to test.
        polygon: Polygon described by UV coordinates.

    Returns:
        True when the point lies inside or on the edges of the polygon, otherwise False.
    """
    inside = False
    n = len(polygon)
    if n < 3:  # noqa: PLR2004
        return False

    for idx in range(n):
        curr = polygon[idx]
        nxt = polygon[(idx + 1) % n]
        if abs(_orientation(curr, nxt, point)) <= UV_SPACE_EPSILON and _on_segment(curr, point, nxt):
            return True
        cond = ((curr.y > point.y) != (nxt.y > point.y)) and (
            point.x < (nxt.x - curr.x) * (point.y - curr.y) / (nxt.y - curr.y + 1e-12) + curr.x
        )
        if cond:
            inside = not inside
    return inside


def _faces_connected_in_uv(
    face_a: bmesh.types.BMFace,
    face_b: bmesh.types.BMFace,
    uv_layer: bpy.types.MeshUVLoopLayer,
) -> bool:
    """Check whether two faces share an edge with matching UVs within tolerance.

    Args:
        face_a: First face in the test.
        face_b: Second face in the test.
        uv_layer: UV layer containing coordinates to compare.

    Returns:
        True when the faces share an aligned UV edge, otherwise False.
    """
    shared = False
    for edge in face_a.edges:
        if face_b not in edge.link_faces:
            continue
        shared = True
        loops_a = [loop for loop in face_a.loops if loop.edge == edge]
        loops_b = [loop for loop in face_b.loops if loop.edge == edge]
        for loop_a in loops_a:
            match = next((loop_b for loop_b in loops_b if loop_b.vert == loop_a.vert), None)
            if match is None:
                continue
            uv_a = loop_a[uv_layer].uv
            uv_b = match[uv_layer].uv
            if (uv_a - uv_b).length > UV_ISLAND_EPSILON:
                return False
    return shared


def _collect_uv_islands(
    bm: bmesh.types.BMesh,
    uv_layer: bpy.types.MeshUVLoopLayer,
) -> list[list[bmesh.types.BMFace]]:
    """Build a list of UV islands by traversing connected faces.

    Args:
        bm: BMesh containing the target faces.
        uv_layer: UV layer that defines connectivity.

    Returns:
        A list of face lists, where each nested list is a UV island.
    """
    islands: list[list[bmesh.types.BMFace]] = []
    visited: set[int] = set()

    for face in bm.faces:
        if face.index in visited:
            continue

        stack = [face]
        island: list[bmesh.types.BMFace] = []

        while stack:
            current = stack.pop()
            if current.index in visited:
                continue
            visited.add(current.index)
            island.append(current)

            for edge in current.edges:
                for neighbor in edge.link_faces:
                    if neighbor is current or neighbor.index in visited:
                        continue
                    if not _faces_connected_in_uv(current, neighbor, uv_layer):
                        continue
                    stack.append(neighbor)

        islands.append(island)

    return islands


def _bounds_overlap_numpy(bounds_a: np.ndarray, bounds_b: np.ndarray) -> np.ndarray:
    """Return a boolean matrix describing which axis-aligned bounds overlap.

    Args:
        bounds_a: Array of [min_u, min_v, max_u, max_v] rows for set A.
        bounds_b: Array of [min_u, min_v, max_u, max_v] rows for set B.

    Returns:
        A boolean matrix where element (i, j) is True when bounds A[i] overlaps bounds B[j].
    """
    if bounds_a.size == 0 or bounds_b.size == 0:
        return np.zeros((bounds_a.shape[0], bounds_b.shape[0]), dtype=bool)
    a_min = bounds_a[:, :2]
    a_max = bounds_a[:, 2:]
    b_min = bounds_b[:, :2]
    b_max = bounds_b[:, 2:]
    cond_x = (a_min[:, None, 0] < b_max[None, :, 0] - UV_ISLAND_EPSILON) & (
        b_min[None, :, 0] < a_max[:, None, 0] - UV_ISLAND_EPSILON
    )
    cond_y = (a_min[:, None, 1] < b_max[None, :, 1] - UV_ISLAND_EPSILON) & (
        b_min[None, :, 1] < a_max[:, None, 1] - UV_ISLAND_EPSILON
    )
    return cond_x & cond_y


def _fallback_pack_islands(
    islands: list[list[bmesh.types.BMFace]],
    uv_layer: bpy.types.MeshUVLoopLayer,
) -> None:
    """Pack UV islands into a deterministic grid inside 0..1 space.

    Args:
        islands: UV islands to repack.
        uv_layer: UV layer whose coordinates are updated.

    Returns:
        None.
    """
    rects: list[dict[str, Any]] = []
    for faces in islands:
        coords_u: list[float] = []
        coords_v: list[float] = []
        for face in faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                coords_u.append(uv.x)
                coords_v.append(uv.y)

        if not coords_u:
            continue
        min_u = min(coords_u)
        max_u = max(coords_u)
        min_v = min(coords_v)
        max_v = max(coords_v)
        rects.append(
            {
                "faces": faces,
                "min": Vector((min_u, min_v)),
                "size": Vector((max(max_u - min_u, 1e-6), max(max_v - min_v, 1e-6))),
            },
        )

    if not rects:
        logger.warning("No UV rectangles found for fallback packing")
        return

    cols = max(1, math.ceil(math.sqrt(len(rects))))
    rows = max(1, math.ceil(len(rects) / cols))
    cell_w = 1.0 / cols
    cell_h = 1.0 / rows

    pad_x = min(MARGIN * 0.5, cell_w * 0.25)
    pad_y = min(MARGIN * 0.5, cell_h * 0.25)
    usable_w = max(cell_w - 2 * pad_x, cell_w * 0.25)
    usable_h = max(cell_h - 2 * pad_y, cell_h * 0.25)

    for idx, rect in enumerate(rects):
        row = idx // cols
        col = idx % cols
        base = Vector((col * cell_w + pad_x, row * cell_h + pad_y))
        size = rect["size"]
        scale = min(usable_w / size.x, usable_h / size.y)
        scale = max(scale, 1e-6)

        for face in rect["faces"]:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                uv.x = (uv.x - rect["min"].x) * scale + base.x
                uv.y = (uv.y - rect["min"].y) * scale + base.y


def _pack_uv_islands(bm: bmesh.types.BMesh, uv_layer: bpy.types.MeshUVLoopLayer) -> None:
    """Pack UV islands deterministically inside 0..1 UV space.

    Args:
        bm: BMesh whose faces should be repacked.
        uv_layer: UV layer to mutate during packing.

    Returns:
        None.
    """
    bm.faces.ensure_lookup_table()
    islands = _collect_uv_islands(bm, uv_layer)
    if not islands:
        logger.warning("No UV islands found for packing")
        return

    logger.info("Found %d UV islands for packing", len(islands))
    _fallback_pack_islands(islands, uv_layer)


def _pack_uv_islands_with_operator(obj: bpy.types.Object) -> bool:
    """Use `bpy.ops.uv.pack_islands` with the correct context, falling back on failure.

    Args:
        obj: Object whose UV islands should be packed via the operator.

    Returns:
        True when the operator succeeds, False when packing should fall back.
    """
    mesh = obj.data
    if UV_NAME not in mesh.uv_layers:
        logger.warning("Cannot pack UVs on '%s': missing '%s' layer", obj.name, UV_NAME)
        return False

    view_layer = bpy.context.view_layer
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(state=True)
    view_layer.objects.active = obj

    try:
        bpy.ops.object.mode_set(mode="EDIT")
    except RuntimeError as exc:
        logger.warning("Failed to enter EDIT mode for '%s': %s", obj.name, exc)
        bpy.ops.object.mode_set(mode="OBJECT")
        return False

    try:
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        mesh.uv_layers.active = mesh.uv_layers[UV_NAME]
        bpy.ops.uv.pack_islands(rotate=True, margin=MARGIN)
        logger.debug("Packed UVs for '%s' using bpy.ops.uv.pack_islands", obj.name)
        return True  # noqa: TRY300
    except Exception as exc:  # noqa: BLE001
        logger.warning("bpy.ops.uv.pack_islands failed for '%s': %s", obj.name, exc)
        return False
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def check_uv_face_overlap(bm: bmesh.types.BMesh, uv_layer: bpy.types.MeshUVLoopLayer) -> bool:  # noqa: C901, PLR0911
    """Check for overlapping UV faces in the given BMesh.

    Args:
        bm: BMesh to check for UV overlaps.
        uv_layer: UV layer to use for overlap detection.

    Returns:
        True if overlapping UV faces are detected, otherwise False.
    """
    bm.faces.ensure_lookup_table()
    face_data: dict[int, dict[str, Any]] = {}
    for face in bm.faces:
        uv_coords = [loop[uv_layer].uv.copy() for loop in face.loops]
        if not uv_coords:
            continue
        if _uv_coords_outside_unit_square(uv_coords):
            msg = f"Face {face.index} has UV outside 0-1"
            logger.info(msg)
            return True
        bounds = _calc_uv_bounds(uv_coords)
        bounds_np = np.array(
            (bounds[0].x, bounds[0].y, bounds[1].x, bounds[1].y),
            dtype=np.float64,
        )
        face_data[face.index] = {"coords": uv_coords, "bounds": bounds, "bounds_np": bounds_np}

    if not face_data:
        return False

    islands = _collect_uv_islands(bm, uv_layer)
    if not islands:
        return False

    island_infos: list[dict[str, Any]] = []
    for island in islands:
        indices = [face.index for face in island if face.index in face_data]
        if not indices:
            continue
        min_u = min(face_data[idx]["bounds"][0].x for idx in indices)
        min_v = min(face_data[idx]["bounds"][0].y for idx in indices)
        max_u = max(face_data[idx]["bounds"][1].x for idx in indices)
        max_v = max(face_data[idx]["bounds"][1].y for idx in indices)
        face_bounds = np.stack([face_data[idx]["bounds_np"] for idx in indices], axis=0)
        island_infos.append(
            {
                "faces": indices,
                "bounds": (Vector((min_u, min_v)), Vector((max_u, max_v))),
                "bounds_np": np.array((min_u, min_v, max_u, max_v), dtype=np.float64),
                "face_bounds": face_bounds,
            },
        )

    if len(island_infos) < 2:  # noqa: PLR2004
        return False

    island_bounds_np = np.stack([info["bounds_np"] for info in island_infos], axis=0)
    island_overlap_matrix = _bounds_overlap_numpy(island_bounds_np, island_bounds_np)
    island_overlap_matrix = np.triu(island_overlap_matrix, k=1)

    if not island_overlap_matrix.any():
        return False

    def _check_island_pair(island_a: dict[str, Any], island_b: dict[str, Any]) -> bool:
        candidate_mask = _bounds_overlap_numpy(island_a["face_bounds"], island_b["face_bounds"])
        if not candidate_mask.any():
            return False
        faces_a = island_a["faces"]
        faces_b = island_b["faces"]
        overlap_hits = 0
        for idx_a, idx_b in np.argwhere(candidate_mask):
            face_idx_a = faces_a[int(idx_a)]
            face_idx_b = faces_b[int(idx_b)]
            data_a = face_data[face_idx_a]
            data_b = face_data[face_idx_b]
            if _polygons_overlap(data_a["coords"], data_b["coords"]):
                overlap_hits += 1
                if overlap_hits > MAX_ISLAND_OVERLAPS:
                    return True
        return False

    for idx_a, idx_b in np.argwhere(island_overlap_matrix):
        if _check_island_pair(island_infos[int(idx_a)], island_infos[int(idx_b)]):
            return True

    return False


def ensure_lighting_uv(obj: bpy.types.Object) -> None:  # noqa: C901
    """Create a UV layer for lighting/baking if not present.

    Args:
        obj: Blender object to add lighting UV to.

    Returns:
        None.
    """
    mesh = obj.data

    # Store active UV
    prev_uv = mesh.uv_layers.active
    prev_idx = mesh.uv_layers.active_index if prev_uv else -1

    # Ensure mesh UV layer (names exist ONLY here)
    if UV_NAME not in mesh.uv_layers:
        mesh.uv_layers.new(name=UV_NAME)
        # make sure this uv layer is last
        mesh.uv_layers.active_index = len(mesh.uv_layers) - 1
    lighting_layer = mesh.uv_layers[UV_NAME]
    mesh.uv_layers.active = lighting_layer

    # Duplicate currently active UV data into the lighting layer up front
    if prev_uv and prev_uv != lighting_layer:
        try:
            for src, dst in zip(prev_uv.data, lighting_layer.data, strict=True):
                dst.uv = src.uv.copy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to duplicate UV layer '%s' onto '%s': %s", prev_uv.name, UV_NAME, exc)

    # --- BMesh
    bm = bmesh.new()
    bm.from_mesh(mesh)

    uv_layer = bm.loops.layers.uv.get(UV_NAME)
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.new(UV_NAME)

    unwrapped = False
    if check_uv_face_overlap(bm, uv_layer):
        logger.info("Using procedural UV layout for '%s' due to overlapping UVs", obj.name)
        # --- FAST FACE-BASED UNWRAP (lighting-safe)
        for face in bm.faces:
            n = face.normal
            x = n.orthogonal().normalized()
            y = n.cross(x).normalized()

            for loop in face.loops:
                co = loop.vert.co
                loop[uv_layer].uv = Vector((co.dot(x), co.dot(y)))

        _pack_uv_islands(bm, uv_layer)
        unwrapped = True

    # --- WRITE BACK
    bm.to_mesh(mesh)
    bm.free()

    if unwrapped:
        packed = _pack_uv_islands_with_operator(obj)
        if not packed:
            logger.info("Using fallback UV layout for '%s'", obj.name)

    # Restore active UV
    if prev_uv:
        mesh.uv_layers.active_index = prev_idx
        mesh.uv_layers.active = prev_uv

    logger.info("Added 'LightingUV' UV layer to object '%s'", obj.name)


# endregion UVMAP

# region MATERIAL


def ensure_world_shader() -> None:
    """Ensure the world has a pure white ambient shader."""
    world = bpy.context.scene.world
    if not world:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    if not world.use_nodes:
        world.use_nodes = True

    nodes = world.node_tree.nodes
    links = world.node_tree.links
    for node in nodes:
        nodes.remove(node)

    bg_node = nodes.new(type="ShaderNodeBackground")
    bg_node.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bg_node.inputs["Strength"].default_value = 10.0
    bg_node.location = (0, 0)

    output_node = nodes.new(type="ShaderNodeOutputWorld")
    output_node.location = (200, 0)
    links.new(bg_node.outputs["Background"], output_node.inputs["Surface"])

    logger.info("Configured world shader with pure white ambient color")


def _activate_material_on_object(obj: bpy.types.Object, mat: bpy.types.Material) -> bool:
    """Select the material slot that owns a material so baking affects it.

    Args:
        obj: Object whose material slots are inspected.
        mat: Material that should become active for baking.

    Returns:
        True when the material slot was activated, otherwise False.
    """
    materials = obj.data.materials
    for idx, slot_mat in enumerate(materials):
        if slot_mat and slot_mat == mat:
            obj.active_material_index = idx
            return True
    logger.debug("Material '%s' not found on object '%s' while baking", mat.name, obj.name)
    return False


def _ensure_single_user_materials(obj: bpy.types.Object) -> None:
    """Duplicate shared materials so edits stay local to the object.

    Args:
        obj: Object whose material slots might need unique copies.

    Returns:
        None.
    """
    materials = obj.data.materials
    for idx, mat in enumerate(materials):
        if not mat or mat.users <= 1:
            continue
        unique_mat = mat.copy()
        unique_mat.name = f"{mat.name}_unique_{obj.name}"
        materials[idx] = unique_mat

        ptr = mat.as_pointer()
        fix_info = FAILED_MATERIAL_FIXES.pop(ptr, None)
        if fix_info:
            fix_copy = fix_info.copy()
            fix_copy["material"] = unique_mat
            FAILED_MATERIAL_FIXES[unique_mat.as_pointer()] = fix_copy


def _rebuild_failed_material(
    mat: bpy.types.Material,
    baked_image: bpy.types.Image,
    *,
    transparent: bool,
) -> None:
    """Replace a problematic shader tree with a baked-texture placeholder.

    Args:
        mat: Material whose node tree should be replaced.
        baked_image: Image that drives the placeholder shader.
        transparent: Whether a Transparent BSDF should be used instead of Principled.

    Returns:
        None.
    """
    if not mat.node_tree:
        return

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    uv_node = nodes.new(type="ShaderNodeUVMap")
    uv_node.uv_map = UV_NAME
    uv_node.location = (-600, 0)

    tex_node = nodes.new(type="ShaderNodeTexImage")
    tex_node.image = baked_image
    tex_node.label = "Baked Placeholder"
    tex_node.location = (-350, 0)

    links.new(uv_node.outputs["UV"], tex_node.inputs["Vector"])

    shader_node = None
    if transparent:
        shader_node = nodes.new(type="ShaderNodeBsdfTransparent")
        shader_node.location = (-50, 0)
        if "Color" in shader_node.inputs:
            links.new(tex_node.outputs["Color"], shader_node.inputs["Color"])

    output_node = nodes.new(type="ShaderNodeOutputMaterial")
    output_node.location = (200, 0)
    if shader_node is not None:
        links.new(shader_node.outputs[0], output_node.inputs["Surface"])

    placeholder_type = "Transparent" if transparent else "Principled"
    logger.info("Replaced shader for material '%s' with %s placeholder", mat.name, placeholder_type)


def _node_tree_has_transparent_shader(node_tree: bpy.types.NodeTree | None, visited: set[int] | None = None) -> bool:
    """Determine whether a node tree uses a Transparent BSDF.

    Args:
        node_tree: Node tree to inspect.
        visited: Optional set that tracks node trees already visited to avoid recursion loops.

    Returns:
        True when any Transparent BSDF node is detected, otherwise False.
    """
    if node_tree is None:
        return False
    if visited is None:
        visited = set()
    tree_id = id(node_tree)
    if tree_id in visited:
        return False
    visited.add(tree_id)

    for node in node_tree.nodes:
        if node.type == "BSDF_TRANSPARENT":
            return True
        if node.type == "GROUP" and _node_tree_has_transparent_shader(node.node_tree, visited):
            return True
    return False


def _check_if_node_connected_to_output(node: bpy.types.Node) -> bool:
    """Check if a node is connected to the material output node.

    Args:
        node: Node to check for connection to output.

    Returns:
        True if the node is connected to output, otherwise False.
    """
    if not node.outputs:
        return False

    for output in node.outputs:
        for link in output.links:
            to_node = link.to_node
            if to_node.type == "OUTPUT_MATERIAL":
                return True
            if _check_if_node_connected_to_output(to_node):
                return True

    return False


def _node_tree_has_direct_textures(node_tree: bpy.types.NodeTree | None) -> bool:
    """Return True if shader inputs use image textures directly.

    Args:
        node_tree: Node tree whose shader inputs are inspected.

    Returns:
        True when every monitored socket sources straight from texture nodes, otherwise False.
    """
    if node_tree is None:
        return False

    for node in node_tree.nodes:
        input_names = _DIRECT_TEXTURE_INPUTS_BY_SHADER.get(node.type)
        if not input_names:
            continue
        if not _check_if_node_connected_to_output(node):
            continue
        for input_name in input_names:
            socket = node.inputs.get(input_name)
            if not socket:
                continue
            for link in socket.links:
                if link.from_node.type == "TEX_IMAGE":
                    return True
    return False


def _find_surface_shader(node_tree: bpy.types.NodeTree | None) -> bpy.types.Node | None:
    """Return the first shader feeding into the material output surface socket.

    Args:
        node_tree: Node tree to search.

    Returns:
        The shader node connected to the material output surface, or None if not found.
    """
    if node_tree is None:
        return None

    for output in node_tree.nodes:
        if output.type != "OUTPUT_MATERIAL":
            continue
        surface = output.inputs.get("Surface")
        if not surface or not surface.is_linked:
            continue
        for link in surface.links:
            return link.from_node
    return None


def _node_chain_is_direct_texture(  # noqa: C901, PLR0911, PLR0912
    node: bpy.types.Node,
    *,
    allow_normal_chain: bool,
    visited: set[int] | None = None,
) -> bool:
    """Return True when a node ultimately sources from an Image Texture with no extra nodes.

    Args:
        node: Node to analyze.
        allow_normal_chain: Whether to allow certain nodes in normal map chains.
        visited: Optional set of visited node IDs to avoid cycles.

    Returns:
        True if the node chain leads directly to an image texture, otherwise False.
    """
    if visited is None:
        visited = set()
    node_id = id(node)
    if node_id in visited:
        return False
    visited.add(node_id)

    if node.type == "TEX_IMAGE":
        return True

    if node.type == "REROUTE":
        for input_socket in node.inputs:
            if not input_socket.is_linked:
                continue
            for link in input_socket.links:
                if _node_chain_is_direct_texture(
                    link.from_node,
                    allow_normal_chain=allow_normal_chain,
                    visited=visited,
                ):
                    return True
        return False

    if allow_normal_chain and node.type in _NORMAL_CHAIN_NODES:
        for socket_name in _NORMAL_CHAIN_NODES[node.type]:
            input_socket = node.inputs.get(socket_name)
            if not input_socket or not input_socket.is_linked:
                continue
            for link in input_socket.links:
                if _node_chain_is_direct_texture(
                    link.from_node,
                    allow_normal_chain=True,
                    visited=visited,
                ):
                    return True
        return False

    return False


def _shader_inputs_are_direct_textures(node_tree: bpy.types.NodeTree | None) -> bool:
    """Return True when the primary shader uses only direct image textures on key inputs.

    Args:
        node_tree: Node tree whose main shader is inspected.

    Returns:
        True if every monitored shader input pulls from a direct texture chain, otherwise False.
    """
    shader = _find_surface_shader(node_tree)
    if shader is None:
        return False

    texture_slots = _SHADER_TEXTURE_SLOTS.get(shader.type)
    if not texture_slots:
        return False

    for slot in texture_slots:
        socket = shader.inputs.get(slot)
        if not socket or not socket.is_linked:
            continue
        for link in socket.links:
            if not _node_chain_is_direct_texture(link.from_node, allow_normal_chain=False):
                return False

    normal_socket = shader.inputs.get("Normal")
    if normal_socket and normal_socket.is_linked:
        for link in normal_socket.links:
            if not _node_chain_is_direct_texture(link.from_node, allow_normal_chain=True):
                return False

    return True


def estimate_shader_cost(
    node_tree: bpy.types.NodeTree | None,
    depth: int = 0,
    group_usage: dict[int, int] | None = None,
):
    """Estimate the cost of a shader node tree.

    Args:
        node_tree: Shader node tree to analyze.
        depth: Current recursion depth.
        group_usage: Dictionary tracking usage count of node groups.

    Returns:
        Estimated cost of the shader node tree; infinity when limits are exceeded.
    """
    if group_usage is None:
        group_usage = {}

    if depth > MAX_DEPTH:
        return float("inf")

    cost = 0

    for node in node_tree.nodes:
        cost += 1

        # Fan-out penalty
        for out in node.outputs:
            cost += max(0, len(out.links) - 1)

        # Node group expansion
        if node.type == "GROUP" and node.node_tree:
            gid = id(node.node_tree)
            group_usage[gid] = group_usage.get(gid, 0) + 1

            if group_usage[gid] > MAX_GROUP_REUSE:
                return float("inf")

            cost += estimate_shader_cost(
                node.node_tree,
                depth + 1,
                group_usage,
            )

        if cost > MAX_EXPANDED_NODES:
            return float("inf")

    return cost


def is_procedural_material(mat: bpy.types.Material | bpy.types.NodeTree) -> bool:  # noqa: C901, PLR0911, PLR0912
    """Determine if a material is procedural.

    Args:
        mat: Blender material or node tree to check.

    Returns:
        True if the material is procedural, otherwise False.



    Hint:
        Procedural is defined as using nodes without any Image Texture nodes.
    """
    node_tree: bpy.types.NodeTree | None

    if mat:
        if not hasattr(mat, "node_tree"):
            return False
        if not mat.use_nodes:
            return False
        node_tree = mat.node_tree
    else:
        node_tree = mat if isinstance(mat, bpy.types.NodeTree) else None

    if node_tree is None:
        return False

    has_blocking_group = False
    for node in node_tree.nodes:
        if node.type == "GROUP" and estimate_shader_cost(node.node_tree) >= 10000:  # noqa: PLR2004
            logger.info("Detected complex node group '%s' in material '%s'", node.name, mat.name)
            has_blocking_group = True

    if has_blocking_group:
        key = mat.as_pointer()
        FAILED_MATERIAL_FIXES[key] = {
            "material": mat,
            "transparent": _node_tree_has_transparent_shader(node_tree),
        }
        return True

    if not _node_tree_has_direct_textures(node_tree):
        return True

    if not _shader_inputs_are_direct_textures(node_tree):
        return True

    for node in node_tree.nodes:
        if node.type == "GROUP" and node.node_tree:  # noqa: SIM102
            if is_procedural_material(node.node_tree):
                return True

    return False


def _create_single_baked_material(  # noqa: PLR0915
    obj: bpy.types.Object,
    shared_images: dict[str, bpy.types.Image],
    combined_image: bpy.types.Image | None,
) -> bpy.types.Material:
    """Build a simplified Principled material that references shared baked textures.

    Args:
        obj: Blender object to assign the baked material to.
        shared_images: Dictionary of shared baked images by type.
        combined_image: Combined ORM image, if available.

    Returns:
        The created baked material.
    """
    baked_material = bpy.data.materials.new(name=f"{obj.name}_Baked")
    baked_material.use_nodes = True
    nodes = baked_material.node_tree.nodes
    links = baked_material.node_tree.links
    nodes.clear()

    uv_node = nodes.new(type="ShaderNodeUVMap")
    uv_node.uv_map = UV_NAME
    uv_node.location = (-900, 0)

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)

    output_node = nodes.new(type="ShaderNodeOutputMaterial")
    output_node.location = (250, 0)
    links.new(bsdf.outputs["BSDF"], output_node.inputs["Surface"])

    color_image = shared_images.get("diffuse")
    if color_image:
        color_node = nodes.new(type="ShaderNodeTexImage")
        color_node.label = "Baked Color"
        color_node.location = (-600, 200)
        color_node.image = color_image
        links.new(uv_node.outputs["UV"], color_node.inputs["Vector"])
        links.new(color_node.outputs["Color"], bsdf.inputs["Base Color"])

    normal_image = shared_images.get("normal")
    if normal_image and "Normal" in bsdf.inputs:
        normal_tex = nodes.new(type="ShaderNodeTexImage")
        normal_tex.label = "Baked Normal"
        normal_tex.location = (-650, -200)
        normal_tex.image = normal_image
        links.new(uv_node.outputs["UV"], normal_tex.inputs["Vector"])

        normal_map = nodes.new(type="ShaderNodeNormalMap")
        normal_map.location = (-350, -200)
        links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    if combined_image:
        orm_node = nodes.new(type="ShaderNodeTexImage")
        orm_node.label = "Baked ORM"
        orm_node.location = (-700, 0)
        orm_node.image = combined_image
        links.new(uv_node.outputs["UV"], orm_node.inputs["Vector"])
        split_node = nodes.new(type="ShaderNodeSeparateColor")
        split_node.location = (-450, 0)
        split_node.label = "Baked RT Split"
        links.new(orm_node.outputs["Color"], split_node.inputs["Color"])
        rough_socket = split_node.outputs.get("Green")
        if rough_socket and "Roughness" in bsdf.inputs:
            links.new(rough_socket, bsdf.inputs["Roughness"])

        metallic_socket = split_node.outputs.get("Blue")
        if metallic_socket and "Metallic" in bsdf.inputs:
            links.new(metallic_socket, bsdf.inputs["Metallic"])

    mesh_mats = obj.data.materials
    if hasattr(mesh_mats, "clear"):
        mesh_mats.clear()
    else:
        while len(mesh_mats):
            mesh_mats.pop(index=len(mesh_mats) - 1)
    mesh_mats.append(baked_material)
    obj.active_material = baked_material

    return baked_material


# endregion MATERIAL


# region MAPS


def connect_baked_textures(obj: bpy.types.Object) -> None:  # noqa: C901, PLR0912, PLR0915
    """Reconnect baked texture passes to material shaders and pack images.

    Args:
        obj: Mesh object whose materials should be rewired to baked textures.

    Returns:
        None.
    """
    obj_ptr = obj.as_pointer()
    object_bake = OBJECT_BAKE_DATA.pop(obj_ptr, None)
    if object_bake:
        shared_images = object_bake.get("images", {})
        combined_image = object_bake.get("orm_image")
        baked_material = _create_single_baked_material(obj, shared_images, combined_image)
        images_to_pack = list(shared_images.values())
        if combined_image:
            images_to_pack.append(combined_image)
        _pack_and_debug_images(images_to_pack)
        logger.info(
            "Applied baked material '%s' with shared textures to object '%s'",
            baked_material.name,
            obj.name,
        )
        return

    target_ptrs = {mat.as_pointer() for mat in PROCEDURAL_MATERIALS if mat}
    processed: set[int] = set()

    for mat in obj.data.materials:
        if not mat or not mat.node_tree:
            continue

        ptr = mat.as_pointer()
        if ptr in processed or ptr not in target_ptrs:
            continue
        processed.add(ptr)

        bake_record = BAKED_MATERIAL_DATA.get(ptr)
        if not bake_record:
            continue
        pass_slots: dict[str, Any] = bake_record.get("passes", {})

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        uv_node = _ensure_uv_map_node(mat)

        baked_images: dict[str, bpy.types.Image] = {}
        image_nodes: dict[str, bpy.types.Node] = {}
        for index, bake_pass in enumerate(BAKE_PASSES):
            slot = pass_slots.get(bake_pass["key"])
            if not slot:
                continue

            # only color, other passes are in ORM
            if bake_pass["key"] != "diffuse":
                continue

            image = slot.get("image")
            if image is None:
                image_name = slot.get("image_name")
                if image_name:
                    image = bpy.data.images.get(image_name)
            node = slot.get("node")
            if node and node.name not in nodes:
                node = None
            if node is None:
                node = nodes.new(type="ShaderNodeTexImage")
                node.label = f"Baked {bake_pass['suffix']}"
                node.location = (-500, -200 * index)
                slot["node"] = node
            if image is None and node.image:
                image = node.image
            if image is None:
                continue

            slot["image"] = image
            try:
                image.colorspace_settings.name = bake_pass["colorspace"]
            except Exception:  # noqa: BLE001
                logger.debug("Failed to enforce colorspace '%s' on '%s'", bake_pass["colorspace"], image.name)
            node.image = image
            if not node.inputs["Vector"].is_linked:
                links.new(uv_node.outputs["UV"], node.inputs["Vector"])

            baked_images[bake_pass["key"]] = image
            image_nodes[bake_pass["key"]] = node

        orm_slot = pass_slots.get("orm")
        if orm_slot:
            image = orm_slot.get("image")
            if image is None and orm_slot.get("image_name"):
                image = bpy.data.images.get(orm_slot["image_name"])
            node = orm_slot.get("node")
            if node and node.name not in nodes:
                node = None
            if node is None:
                node = nodes.new(type="ShaderNodeTexImage")
                node.label = "Baked ORM"
                node.location = (-650, -200)
                orm_slot["node"] = node
            if image is not None:
                node.image = image
                if not node.inputs["Vector"].is_linked:
                    links.new(uv_node.outputs["UV"], node.inputs["Vector"])
                baked_images["orm"] = image
                image_nodes["orm"] = node

        if not baked_images:
            continue

        fix_info = FAILED_MATERIAL_FIXES.pop(ptr, None)
        if fix_info:
            fallback = baked_images.get("diffuse") or next(iter(baked_images.values()), None)
            if fallback is None:
                slot = pass_slots.get("diffuse")
                if slot and slot.get("image_name"):
                    image_name = slot["image_name"]
                else:
                    image_name = f"{obj.name}_{mat.name}_bake_clr"
                fallback = _new_bake_image(image_name, "sRGB")
            _pack_and_debug_images([fallback])
            _rebuild_failed_material(mat, fallback, transparent=fix_info["transparent"])
            continue

        bsdf = _ensure_principled_node(mat)

        color_node = image_nodes.get("diffuse")
        if color_node and "Base Color" in bsdf.inputs:
            for link in list(bsdf.inputs["Base Color"].links):
                links.remove(link)
            links.new(color_node.outputs["Color"], bsdf.inputs["Base Color"])

        combined_node = image_nodes.get("orm")
        if combined_node:
            split_node = next(
                (node for node in nodes if node.type == "SEPRGB" and node.label == "Baked ORM Split"),
                None,
            )
            if split_node is None:
                split_node = nodes.new(type="ShaderNodeSeparateColor")
                split_node.label = "Baked ORM Split"
                split_node.location = (-350, -200)
            for link in list(split_node.inputs["Color"].links):
                links.remove(link)
            links.new(combined_node.outputs["Color"], split_node.inputs["Color"])

            occlusion_output = split_node.outputs.get("Red")
            if occlusion_output and "Occlusion" in bsdf.inputs:
                for link in list(bsdf.inputs["Occlusion"].links):
                    links.remove(link)
                links.new(occlusion_output, bsdf.inputs["Occlusion"])

            rough_output = split_node.outputs.get("Green")
            if rough_output and "Roughness" in bsdf.inputs:
                for link in list(bsdf.inputs["Roughness"].links):
                    links.remove(link)
                links.new(rough_output, bsdf.inputs["Roughness"])

            metallic_output = split_node.outputs.get("Blue")
            if metallic_output and "Metallic" in bsdf.inputs:
                for link in list(bsdf.inputs["Metallic"].links):
                    links.remove(link)
                links.new(metallic_output, bsdf.inputs["Metallic"])

        normal_node = image_nodes.get("normal")
        if normal_node and "Normal" in bsdf.inputs:
            normal_map = _ensure_normal_map_node(mat)
            for link in list(normal_map.inputs["Color"].links):
                links.remove(link)
            links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
            for link in list(bsdf.inputs["Normal"].links):
                links.remove(link)
            links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

        _pack_and_debug_images(baked_images.values())

    logger.info("Reconnected baked textures for object '%s'", obj.name)


def _enable_bake_color_pass(scene: bpy.types.Scene) -> None:
    """Ensure the bake color pass flag is enabled regardless of Blender version.

    Args:
        scene: Blender scene to adjust bake settings for.

    Returns:
        None.
    """
    cycles_settings = getattr(scene, "cycles", None)
    if cycles_settings and hasattr(cycles_settings, "use_pass_color"):
        logger.info("Enabling bake color pass in Cycles settings")
        cycles_settings.use_pass_color = True
        return

    bake_settings = getattr(scene.render, "bake", None)
    if bake_settings and hasattr(bake_settings, "use_pass_color"):
        logger.info("Enabling bake color pass in Render Bake settings")
        bake_settings.use_pass_color = True
        return

    logger.warning("Cannot enable bake color pass; API attribute missing")


def _set_bake_margin(scene: bpy.types.Scene, margin: int) -> None:
    """Set bake margin in a version-tolerant way.

    Args:
        scene: Blender scene to adjust bake settings for.
        margin: Margin value to set for baking.

    Returns:
        None.
    """
    cycles_settings = getattr(scene, "cycles", None)
    if cycles_settings and hasattr(cycles_settings, "bake_margin"):
        cycles_settings.bake_margin = margin
        return

    bake_settings = getattr(scene.render, "bake", None)
    if bake_settings and hasattr(bake_settings, "margin"):
        bake_settings.margin = margin
        return

    logger.warning("Cannot set bake margin; API attribute missing")


def _new_bake_image(name: str, colorspace: str) -> bpy.types.Image:
    """Create a new image for baking with consistent defaults.

    Args:
        name: Name of the image datablock.
        colorspace: Colorspace to assign to the image.

    Returns:
        The created Blender image object.
    """
    bake_image = bpy.data.images.new(
        name=name,
        width=TEX_SIZE,
        height=TEX_SIZE,
        # > alpha=True,
    )
    bake_image.generated_color = (0, 0, 0, 1)
    try:
        bake_image.colorspace_settings.name = colorspace
    except Exception:  # noqa: BLE001
        logger.debug("Failed to set colorspace '%s' on image '%s'", colorspace, name)
    return bake_image


def _ensure_uv_map_node(mat: bpy.types.Material) -> bpy.types.Node:
    """Return an existing Lighting UV node or create one.

    Args:
        mat: Material whose node tree is inspected or modified.

    Returns:
        The UV Map node referencing `LightingUV`.
    """
    nodes = mat.node_tree.nodes
    uv_node = next((node for node in nodes if node.type == "UVMAP" and getattr(node, "uv_map", None) == UV_NAME), None)
    if uv_node is None:
        uv_node = nodes.new(type="ShaderNodeUVMap")
        uv_node.uv_map = UV_NAME
        uv_node.location = (-900, 0)
    return uv_node


def _ensure_output_node(mat: bpy.types.Material) -> bpy.types.Node:
    """Ensure the material has an output node.

    Args:
        mat: Material whose node tree should contain an output.

    Returns:
        The material output node.
    """
    nodes = mat.node_tree.nodes
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.location = (400, 0)
    return output


def _ensure_principled_node(mat: bpy.types.Material) -> bpy.types.Node:
    """Ensure a Principled BSDF exists and is connected to output.

    Args:
        mat: Material whose shader tree should expose a Principled node.

    Returns:
        The Principled BSDF node.
    """
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf.location = (-50, 0)

    output = _ensure_output_node(mat)
    for link in list(output.inputs["Surface"].links):
        links.remove(link)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return bsdf


def _ensure_normal_map_node(mat: bpy.types.Material) -> bpy.types.Node:
    """Return a reusable normal map node for baked normals.

    Args:
        mat: Material whose node tree should expose a normal map node.

    Returns:
        The reusable normal map node configured for baked data.
    """
    nodes = mat.node_tree.nodes
    normal_node = next((node for node in nodes if node.type == "NORMAL_MAP" and node.label == "Baked Normal Map"), None)
    if normal_node is None:
        normal_node = nodes.new(type="ShaderNodeNormalMap")
        normal_node.label = "Baked Normal Map"
        normal_node.location = (150, -200)
    normal_node.space = "TANGENT"
    return normal_node


def _pack_and_debug_images(images: Iterable[bpy.types.Image]) -> None:
    """Pack baked images and optionally dump copies for debugging.

    Args:
        images: Iterable of images to process.

    Returns:
        None.
    """
    seen: set[str] = set()
    for image in images:
        if not image or image.name in seen:
            continue
        seen.add(image.name)

        if not image.packed_file:
            image.pack()

        if LOCAL_DEBUG:
            try:
                image.filepath_raw = bpy.path.abspath(f"//baked_textures/{image.name}.png")
                image.file_format = "PNG"
                image.save()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to save debug image for '%s': %s", image.name, exc)


# endregion MAPS


def _combine_roughness_transmission(
    images_by_key: dict[str, bpy.types.Image],
    name_prefix: str,
) -> bpy.types.Image | None:
    """Create a combined ORM texture (R=occlusion, G=roughness, B=metallic).

    Args:
        images_by_key: Mapping of bake keys to images.
        name_prefix: Prefix for the combined image name.

    Returns:
        The combined image when inputs match in size, otherwise None.
    """
    images: list[bpy.types.Image] = []
    for key in _ORM_CHANNEL_KEYS:
        image = images_by_key.get(key)
        if image is None:
            return None
        images.append(image)

    base_width, base_height = images[0].size[0], images[0].size[1]
    for image in images[1:]:
        width, height = image.size[0], image.size[1]
        if width != base_width or height != base_height:  # type: ignore[index]
            logger.warning(
                "Cannot combine ORM for '%s': mismatched image sizes",
                name_prefix,
            )
            return None

    combined_name = f"{name_prefix}_bake_orm"
    combined_image = _new_bake_image(combined_name, "Non-Color")
    if combined_image.size[0] != base_width or combined_image.size[1] != base_height:  # type: ignore[index]
        combined_image.scale(base_width, base_height)

    data_occl = list(images[0].pixels[:])
    data_rough = list(images[1].pixels[:])
    data_metallic = list(images[2].pixels[:])
    total = len(data_rough)
    combined_pixels = [0.0] * total

    for idx in range(0, total, 4):
        combined_pixels[idx] = data_occl[idx]
        combined_pixels[idx + 1] = data_rough[idx]
        combined_pixels[idx + 2] = data_metallic[idx]
        combined_pixels[idx + 3] = 1.0

    combined_image.pixels[:] = combined_pixels
    logger.info("Combined none/roughness/transmission into '%s'", combined_image.name)
    return combined_image


def bake_all_procedural_textures(obj: bpy.types.Object) -> None:  # noqa: C901, PLR0912, PLR0915
    """Bake four texture passes (color, normal, roughness, transmission) for procedural materials.

    Args:
        obj: Blender object to bake procedural textures for.

    Returns:
        None.
    """
    _ensure_single_user_materials(obj)
    procedural_materials = [mat for mat in obj.data.materials if mat and is_procedural_material(mat)]
    if not procedural_materials:
        logger.info("No procedural materials found on '%s'. Skipping bake.", obj.name)
        return

    PROCEDURAL_MATERIALS.extend(procedural_materials)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(state=True)
    bpy.context.view_layer.objects.active = obj

    ensure_lighting_uv(obj)

    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    scene = bpy.context.scene
    _enable_bake_color_pass(scene)
    _set_bake_margin(scene, BLEED)

    if LOCAL_DEBUG:
        os.makedirs(bpy.path.abspath("//baked_textures"), exist_ok=True)

    shared_images: dict[str, bpy.types.Image] = {}
    for bake_pass in BAKE_PASSES:
        shared_image = _new_bake_image(
            name=f"{obj.name}_bake_{bake_pass['suffix']}",
            colorspace=bake_pass["colorspace"],
        )
        shared_images[bake_pass["key"]] = shared_image

    obj_ptr = obj.as_pointer()
    OBJECT_BAKE_DATA[obj_ptr] = {
        "images": shared_images,
        "orm_image": None,
    }

    for mat in procedural_materials:
        try:
            if not mat.use_nodes:
                mat.use_nodes = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to enable nodes for material '%s': %s", mat.name, exc)

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for node in nodes:
            node.select = False

        uv_node = _ensure_uv_map_node(mat)
        mat_key = mat.as_pointer()
        BAKED_MATERIAL_DATA[mat_key] = {"material": mat, "passes": {}}
        pass_store = BAKED_MATERIAL_DATA[mat_key]["passes"]

        for idx, bake_pass in enumerate(BAKE_PASSES):
            img_node = nodes.new(type="ShaderNodeTexImage")
            img_node.label = f"Baked {bake_pass['suffix']}"
            img_node.location = (-500, -200 * idx)
            bake_image = shared_images[bake_pass["key"]]
            img_node.image = bake_image
            links.new(uv_node.outputs["UV"], img_node.inputs["Vector"])
            img_node.select = False

            pass_store[bake_pass["key"]] = {
                "node": img_node,
                "image_name": bake_image.name,
                "image": bake_image,
            }

            logger.info(
                "Prepared bake image '%s' for material '%s' (%s pass)",
                bake_image.name,
                mat.name,
                bake_pass["key"],
            )

    for bake_pass in BAKE_PASSES:
        logger.info("Baking %s pass for '%s'", bake_pass["key"], obj.name)
        for mat in procedural_materials:
            bake_record = BAKED_MATERIAL_DATA.get(mat.as_pointer())
            if not bake_record:
                continue
            slot = bake_record.get("passes", {}).get(bake_pass["key"])
            if not slot:
                continue

            _activate_material_on_object(obj, mat)
            nodes = mat.node_tree.nodes
            for node in nodes:
                node.select = False
            img_node = slot["node"]
            if img_node is None:
                continue
            img_node.select = True
            nodes.active = img_node

        bake_kwargs: dict[str, Any] = {
            "type": bake_pass["type"],
            "use_selected_to_active": False,
            "uv_layer": UV_NAME,
            "margin": BLEED,
        }
        if bake_pass["pass_filter"]:
            bake_kwargs["pass_filter"] = bake_pass["pass_filter"]

        try:
            bpy.ops.object.bake(**bake_kwargs)
        except Exception:
            logger.exception("Error while baking %s pass", bake_pass["key"])
            logger.error("Trace \n %s", traceback.format_exc())  # noqa: TRY400

    combined_image = _combine_roughness_transmission(shared_images, obj.name)
    OBJECT_BAKE_DATA[obj_ptr]["orm_image"] = combined_image
    if combined_image:
        for mat in procedural_materials:
            record = BAKED_MATERIAL_DATA.get(mat.as_pointer())
            if not record:
                continue
            record.setdefault("passes", {})["orm"] = {
                "image": combined_image,
                "image_name": combined_image.name,
                "node": None,
            }

    # save and pack images in debug mode
    if LOCAL_DEBUG:
        debug_images: list[bpy.types.Image] = list(shared_images.values())
        if combined_image:
            debug_images.append(combined_image)
        _pack_and_debug_images(debug_images)

    make_active_uv_first(obj)
    logger.info("Baked procedural textures: %s", procedural_materials)


def fix_texture_paths() -> None:
    """Fix texture paths that may not be loading correctly."""
    for img in bpy.data.images:
        if not img.filepath:
            continue
        logger.debug("Checking image path: '%s'", img.filepath)
        try:
            img.reload()
        except Exception:
            logger.exception("Failed to reload image at path: %s", img.filepath)

        if img.filepath.startswith((
            "\\\\textures",
            "\\textures",
            "/textures",
        )):
            fixed_path = (
                img.filepath.replace("/textures", "//textures")
                .replace("\\\\textures", "//textures")
                .replace("\\textures", "//textures")
            )
            img.filepath = fixed_path
            logger.info("Fixed image path from '%s' to '%s'", img.filepath, fixed_path)
            img.filepath_raw = fixed_path
            try:
                img.reload()
            except Exception:
                logger.exception("Failed to reload image at fixed path: %s", fixed_path)


# endregion MAPS

# region EXPORT


def gltf_addon_setup() -> None:
    """Ensure the glTF addon is enabled."""
    if "io_scene_gltf2" not in bpy.context.preferences.addons:
        addon_utils.enable("io_scene_gltf2")
    try:
        prefs = bpy.context.preferences.addons["io_scene_gltf2"].preferences
        prefs.export_extras = True
        prefs.export_tangents = True
        prefs.settings_node_ui = True
        prefs.material_variations_ui = True
        prefs.allow_embedded_format = True
    except Exception:  # noqa: BLE001
        logger.warning("Failed to configure glTF addon preferences")


def generate_gltf(json_result_path: str, target_format: str) -> None:  # noqa: C901, PLR0912, PLR0915
    """Generate a GLB file for an asset and write results metadata.

    Args:
        json_result_path: Path to write the results JSON file.
        target_format: The target export format, e.g., 'gltf_godot'.

    Hint:
        On success, writes a JSON list with the GLTF file path to ``json_result_path``.
        On failure, no JSON is written and the caller should detect the missing file.

    Returns:
        None.
    """
    scene_path = bpy.data.filepath
    filepath = scene_path.replace(".blend", ".glb")

    # make sure world has pure white ambient color
    ensure_world_shader()

    # enable gltf extra options if not enabled
    gltf_addon_setup()

    # PREPARE ASSET (do things which cannot be set in GLTF export options)
    logger.info("ASSET PRE-PROCESSING start")
    # first try to fix all textures they may not loading  and start with \\textures
    fix_texture_paths()

    # Configure bake settings
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.cycles.bake_type = "DIFFUSE"
    _enable_bake_color_pass(scene)
    _set_bake_margin(scene, BLEED)

    # set some render limit , some light passes take ages
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False

    count = len(bpy.data.objects)
    logger.info("Number of objects in the scene: %d", count)
    for i, obj in enumerate(bpy.data.objects):
        logger.info("Preprocess object %d/%d: %s", i + 1, count, obj.name)
        if obj.type != "MESH":
            continue
        disable_subsurf_modifiers(obj)
        bake_all_procedural_textures(obj)

    # save duplicate of the scene for debugging , with all the baking setups
    if LOCAL_DEBUG:
        pre_save_path = scene_path.replace(".blend", "_before_reconnect_debug.blend")
        bpy.ops.wm.save_as_mainfile(filepath=pre_save_path)
    logger.info("Connecting baked textures to materials")
    for i, obj in enumerate(bpy.data.objects):
        logger.info("Connecting textures %d/%d: %s", i + 1, count, obj.name)
        if obj.type != "MESH":
            continue
        connect_baked_textures(obj)

    # save duplicate of the scene for debugging , with all the baking setups
    if LOCAL_DEBUG:
        debug_save_path = scene_path.replace(".blend", "_bake_debug.blend")
        try:
            bpy.ops.wm.save_as_mainfile(filepath=debug_save_path)
        except Exception:
            logger.exception("Failed to save debug blend file at: %s", debug_save_path)
            logger.error("Trace \n %s", traceback.format_exc())  # noqa: TRY400

    logger.info("ASSET PRE-PROCESSING finished")

    # CHOOSE EXPORT OPTIONS - based on target_format (gltf/gltf_godot)
    logger.info("Generating GLTF for target format: %s", target_format)
    if target_format == "gltf":  # Optimize for web presentation - adding draco compression
        options = [
            ["maximal", MAXIMAL_GLTF | DRACO_MESH_COMPRESSION],
            ["minimal", MINIMAL_GLTF | DRACO_MESH_COMPRESSION],
        ]
    elif target_format == "gltf_godot":  # Optimize for use in Godot
        options = [
            ["maximal", MAXIMAL_GLTF],
            ["minimal", MINIMAL_GLTF],
        ]
    else:
        logger.error("target_format needs to be gltf or gltf_godot")
        sys.exit(10)

    # TRY EXPORT - go from ideal to minimal export settings
    success = False
    for option in options:
        options_name = option[0]
        gltf_options = option[1]
        try:
            bpy.ops.export_scene.gltf(filepath=filepath, **gltf_options)
            success = True
            break  # No need to continue
        except Exception:
            logger.exception("Error during '%s' GLTF export: \n%s", options_name, traceback.format_exc())

    # FAILURE - Exit now, calling script will detect missing JSON and react properly
    if not success:
        sys.exit(101)

    # SUCCESS - Write results data to a JSON file
    files = [{"type": target_format, "index": 0, "file_path": filepath}]
    try:
        with open(json_result_path, "w", encoding="utf-8") as f:
            json.dump(files, f, ensure_ascii=False, indent=4)
    except (OSError, PermissionError):
        logger.exception("Failed to write results JSON")
        sys.exit(102)


if __name__ == "__main__":
    addon_utils.enable("io_scene_gltf2")
    datafile = sys.argv[-1]
    logger.info("Background GLTF generator has started")

    try:
        with open(datafile, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Failed to read/parse input JSON: %s", datafile)
        sys.exit(10)

    json_result_path = data.get("result_filepath")  # Output data JSON
    if not json_result_path:
        logger.error("Missing result_filepath for GLTF generation")
        sys.exit(10)

    target_format = data.get("target_format")
    if not target_format:
        logger.error("Missing target_format (gltf/gltf_godot) for GLTF generation")
        sys.exit(10)

    generate_gltf(json_result_path, target_format)

# endregion EXPORT
