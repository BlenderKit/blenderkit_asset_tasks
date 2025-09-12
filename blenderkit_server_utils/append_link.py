"""BlenderKit server utilities for appending and linking materials and collections."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Imported for type checking only; Blender not required at runtime.
    from bpy.types import Material as BpyMaterial
    from bpy.types import Object as BpyObject

logger = logging.getLogger(__name__)


def append_material(
    file_name: str,
    *,
    matname: str | None = None,
    link: bool = False,
    fake_user: bool = True,
) -> BpyMaterial | None:
    """Append or link a material from a .blend file.

    Args:
        file_name: Path to the .blend file containing the material.
        matname: Name of the material to append/link. If None, uses the first available.
        link: If True, link the material instead of appending. Defaults to False.
        fake_user: Set fake user on the appended material. Defaults to True.

    Returns:
        The appended/linked material, or None if not found or on failure.

    Raises:
        RuntimeError: If loading the library fails due to IO/runtime issues.
    """
    # Local import to avoid hard dependency when not running inside Blender.
    import bpy  # type: ignore

    mats_before = bpy.data.materials[:]
    try:
        with bpy.data.libraries.load(file_name, link=link, relative=True) as (
            data_from,
            data_to,
        ):
            selected = next(
                (m for m in data_from.materials if matname is None or m == matname),
                None,
            )
            if selected is None:
                logger.warning("Material '%s' not found in '%s'", matname, file_name)
                return None
            data_to.materials = [selected]
    except (OSError, RuntimeError, ValueError):
        logger.exception("Failed to append/link material from '%s'", file_name)
        return None
    else:
        # Get the newly added material
        mats_after = bpy.data.materials[:]
        new_mats = [m for m in mats_after if m not in mats_before]
        if not new_mats:
            logger.warning("No new materials after append/link from '%s'", file_name)
            return None

        mat = new_mats[0]
        if fake_user:
            mat.use_fake_user = True
        return mat


def link_collection(
    file_name: str,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    link: bool = False,
    name: str | None = None,
    parent: BpyObject | None = None,
) -> tuple[BpyObject | None, list[BpyObject]]:
    """Link or append a collection from a .blend file.

    Args:
        file_name: Path to the .blend file.
        location: Location to place the collection's root object(s).
        rotation: Rotation to apply to the collection's root object(s).
        link: True to link, False to append.
        name: Name of the collection to find. If None, uses the first available.
        parent: Optional parent object to parent the collection root object(s) to.

    Returns:
        A tuple of (main_object, all_objects):
            - main_object: The first object without a parent, suitable as a root. None if none found.
            - all_objects: All objects loaded from the collection.
    """
    # Local import to avoid hard dependency when not running inside Blender.
    import bpy  # type: ignore

    # Store existing collections to find new ones
    collections_before = bpy.data.collections[:]

    # Link/append the collection
    try:
        with bpy.data.libraries.load(file_name, link=link) as (data_from, data_to):
            selected = next(
                (cname for cname in data_from.collections if name is None or cname == name),
                None,
            )
            if selected is None:
                logger.warning("Collection '%s' not found in '%s'", name, file_name)
                return None, []
            data_to.collections = [selected]
    except (OSError, RuntimeError, ValueError):
        logger.exception("Failed to link/append collection from '%s'", file_name)
        return None, []

    # Find the newly added collection
    collections_after = bpy.data.collections[:]
    new_collections = [c for c in collections_after if c not in collections_before]
    if not new_collections:
        logger.warning("No new collections after link/append from '%s'", file_name)
        return None, []

    new_collection = new_collections[0]

    # Link the collection to the scene (by name to avoid object-identity issues)
    scene_children_names = {c.name for c in bpy.context.scene.collection.children}
    if new_collection.name not in scene_children_names:
        bpy.context.scene.collection.children.link(new_collection)

    # Get all objects from the collection
    all_objects: list[BpyObject] = list(new_collection.all_objects)
    for obj in all_objects:
        if obj.parent is None:
            obj.location = location
            obj.rotation_euler = rotation
            if parent is not None:
                obj.parent = parent

    # Find main/parent object (first object without parent)
    main_object: BpyObject | None = next(
        (o for o in all_objects if o.parent is None),
        None,
    )

    return main_object, all_objects
