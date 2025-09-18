"""Utility helpers for BlenderKit asset tasks.

This module includes HTTP header creation, Blender selection utilities, simple
parameter helpers, and UV mapping helpers when Blender's bpy is available.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable
from typing import Any

from . import log

logger = log.create_logger(__name__)

bpy = None
try:  # pragma: no cover - only available inside Blender
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:
    logger.warning("bpy not present")


def get_headers(api_key: str) -> dict[str, str]:
    """Build HTTP headers for BlenderKit API.

    Args:
        api_key: API token; if empty, Authorization is omitted.

    Returns:
        A dict of HTTP headers.
    """
    headers: dict[str, str] = {
        "accept": "application/json",
        "Platform-Version": platform.platform(),
    }
    if api_key != "":
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def activate_object(aob: Any) -> None:  # bpy.types.Object when bpy is present
    """Make the given object the only selected and active object.

    Args:
        aob: Blender object to activate.
    """
    for obj in bpy.context.visible_objects:
        obj.select_set(False)  # noqa: FBT003
    aob.select_set(True)  # noqa: FBT003
    bpy.context.view_layer.objects.active = aob


def selection_get() -> tuple[Any, list[Any]]:
    """Return active object and a list of selected objects.

    Returns:
        A tuple of (active_object, selected_objects_list).
    """
    aob = bpy.context.view_layer.objects.active
    selobs = bpy.context.view_layer.objects.selected[:]
    return aob, selobs


def selection_set(sel: tuple[Any, Iterable[Any]]) -> None:
    """Restore selection and active object from a tuple.

    Args:
        sel: Tuple of (active_object, iterable_of_selected_objects).
    """
    bpy.ops.object.select_all(action="DESELECT")
    try:
        bpy.context.view_layer.objects.active = sel[0]
        for ob in sel[1]:
            ob.select_set(True)  # noqa: FBT003
    except (AttributeError, IndexError, TypeError) as e:
        logger.warning("Selectable objects not found: %s", e)


def get_param(asset_data: dict[str, Any], parameter_name: str, default: Any | None = None) -> Any | None:
    """Get parameter value from asset data, with default.

    Args:
        asset_data: Asset dictionary possibly containing "dictParameters".
        parameter_name: Name of the parameter to retrieve.
        default: Value to return if not found.

    Returns:
        Parameter value or default if missing.
    """
    if not asset_data.get("dictParameters"):
        # this can appear in older version files.
        return default

    return asset_data["dictParameters"].get(parameter_name, default)


def dict_to_params(inputs: dict[str, Any], parameters: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Convert a dict of parameters into BlenderKit parameter list format.

    Args:
        inputs: Source key/value mapping.
        parameters: Optional existing list to extend.

    Returns:
        A list of dicts with keys parameterType and value.
    """
    if parameters is None:
        parameters = []
    for k, v in inputs.items():
        if isinstance(v, list):
            value = ",".join(str(s) for s in v)
        elif isinstance(v, bool):
            value = str(v)
        else:
            value = v
        parameters.append({"parameterType": k, "value": value})
    return parameters


def enable_cycles_cuda() -> None:
    """Enable CUDA/OPTIX rendering if available in Blender preferences."""
    preferences = bpy.context.preferences
    cycles_preferences = preferences.addons["cycles"].preferences

    cycles_preferences.compute_device_type = "CUDA"
    if cycles_preferences.compute_device_type == "CUDA":
        logger.info("CUDA is enabled for rendering")
    elif cycles_preferences.compute_device_type == "OPTIX":
        logger.info("OPTIX is enabled for rendering")
    else:
        logger.info("GPU rendering is not enabled")


# Backward compatibility wrapper for older callers
def enable_cycles_CUDA() -> None:  # noqa: N802 - preserve legacy API name
    """Legacy name that forwards to enable_cycles_cuda."""
    return enable_cycles_cuda()


# Moved helpers from blenderkit/utils.py to here (only if bpy is present)
if bpy:

    def scale_2d(v: tuple[float, float], s: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
        """Scale a 2D vector with a pivot."""
        return p[0] + s[0] * (v[0] - p[0]), p[1] + s[1] * (v[1] - p[1])

    def scale_uvs(
        ob: Any,
        scale: Vector | tuple[float, float] | None = None,
        pivot: Vector | None = None,
    ) -> None:
        """Scale UVs of the given object around a pivot.

        Args:
            ob: Blender object with UV layers.
            scale: 2D scale factor as Vector or (x, y). Defaults to (1.0, 1.0).
            pivot: Pivot point in UV space. Defaults to (0.5, 0.5).
        """
        if pivot is None:
            pivot = Vector((0.5, 0.5))
        scale_val = Vector((1.0, 1.0)) if scale is None else scale
        mesh = ob.data
        if len(mesh.uv_layers) > 0:
            uv = mesh.uv_layers[mesh.uv_layers.active_index]

            # Scale a UV map iterating over its coordinates to a given scale and with a pivot point
            for uvindex in range(len(uv.data)):
                uv.data[uvindex].uv = scale_2d(uv.data[uvindex].uv, scale_val, pivot)

    # map uv cubic and switch of auto tex space and set it to 1,1,1

    def automap(
        target_object: str | None = None,
        target_slot: int | None = None,
        tex_size: int = 1,
        *,
        bg_exception: bool = False,
        just_scale: bool = False,
    ) -> None:
        """Automatically map an object's UVs using cube projection.

        Args:
            target_object: Name of the Blender object to map.
            target_slot: Material slot index to make active.
            tex_size: Texture size used to determine cube projection scale.
            bg_exception: Select all faces when true (background workaround).
            just_scale: Skip cube projection and only scale existing UVs.
        """
        tob = bpy.data.objects[target_object]
        # Only automap mesh models; exit early if not valid
        if tob.type != "MESH" or len(tob.data.polygons) == 0:
            return

        # Check polycount for a rare case where no polys are in editmesh
        actob = bpy.context.active_object
        bpy.context.view_layer.objects.active = tob

        # Auto tex space
        if tob.data.use_auto_texspace:
            tob.data.use_auto_texspace = False

        do_cube = not just_scale
        if do_cube:
            tob.data.texspace_size = (1, 1, 1)

        if "automap" not in tob.data.uv_layers:
            bpy.ops.mesh.uv_texture_add()
            uvl = tob.data.uv_layers[-1]
            uvl.name = "automap"

        tob.data.uv_layers.active = tob.data.uv_layers["automap"]
        tob.data.uv_layers["automap"].active_render = True

        # NOTE: limited to active material
        scale = tob.scale.copy()

        if target_slot is not None:
            tob.active_material_index = target_slot
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")

        # Background thumbnailer crash workaround; can be removed when material slot select works.
        if bg_exception or len(tob.material_slots) == 0:
            bpy.ops.mesh.select_all(action="SELECT")
        else:
            bpy.ops.object.material_slot_select()

        scale = (scale.x + scale.y + scale.z) / 3.0

        # Prevent division by zero from invalid texture sizes
        tex_size = 1 if tex_size == 0 else tex_size

        if do_cube:
            # Compensate for the undocumented operator change in Blender 3.2
            cube_size = tex_size / scale if bpy.app.version >= (3, 2, 0) else (scale * 2.0 / tex_size)
            bpy.ops.uv.cube_project(cube_size=cube_size, correct_aspect=False)

        bpy.ops.object.editmode_toggle()
        # Currently scales the whole UV for thumbnail preview; doesn't respect multiple materials per object
        if just_scale:
            scale_uvs(tob, scale=Vector((1 / tex_size, 1 / tex_size)))
        bpy.context.view_layer.objects.active = actob


def get_bounds_worldspace(objects: Iterable[Any]) -> tuple[float, float, float, float, float, float]:
    """Get the bounding box of objects in world space.

    Args:
        objects: Iterable of Blender-like objects with matrix_world, type, and data.

    Returns:
        Tuple (minx, miny, minz, maxx, maxy, maxz). Returns all zeros if no valid objects.
    """
    minx = miny = minz = float("inf")
    maxx = maxy = maxz = -float("inf")

    for obj in objects:
        # Skip objects that shouldn't be included in bounds
        if obj.type == "EMPTY" and not getattr(obj, "instance_collection", None):
            continue

        matrix_world = obj.matrix_world

        if obj.type == "MESH":
            for v in obj.data.vertices:
                world_coord = matrix_world @ v.co
                minx = min(minx, world_coord.x)
                miny = min(miny, world_coord.y)
                minz = min(minz, world_coord.z)
                maxx = max(maxx, world_coord.x)
                maxy = max(maxy, world_coord.y)
                maxz = max(maxz, world_coord.z)
        else:
            world_coord = matrix_world.translation
            minx = min(minx, world_coord.x)
            miny = min(miny, world_coord.y)
            minz = min(minz, world_coord.z)
            maxx = max(maxx, world_coord.x)
            maxy = max(maxy, world_coord.y)
            maxz = max(maxz, world_coord.z)

    if minx == float("inf"):
        # No valid objects found, return zero bounds
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    return minx, miny, minz, maxx, maxy, maxz
