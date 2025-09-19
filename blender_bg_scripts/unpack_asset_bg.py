"""Unpack packed image textures and mark data blocks as Blender assets.

This background script:
- Ensures a texture directory for a given resolution exists.
- Writes packed images to that directory and repaths image datablocks.
- Marks appropriate data blocks as assets and assigns tags.
- Saves the .blend and removes the .blend1 backup if present.
"""

from __future__ import annotations

import json
import os
import sys
import time
from functools import lru_cache
from typing import Any

import bpy

# Path injection so Blender can import our utils when running in background
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import paths, log  # isort: skip  # noqa: E402


logger = log.create_logger(__name__)


WAIT_TIME = 2.0  # seconds


@lru_cache(maxsize=1)
def _get_scene_directory() -> str:
    """Get the absolute filepath of the current Blender scene."""
    return os.path.dirname(bpy.path.abspath(bpy.data.filepath))


def _get_texture_abs_path(texture_path: str) -> str:
    """Get the absolute path to the texture.

    Args:
        texture_path: Relative or absolute path to the texture.

    Returns:
        str: Absolute path to the texture.
    """
    if not os.path.isabs(texture_path):
        texture_path = os.path.abspath(texture_path)
        if os.path.exists(texture_path):
            return texture_path

    scene_dir = _get_scene_directory()
    return bpy.path.abspath(os.path.join(scene_dir, texture_path))


def _wait_for_resource(filepath: str, timeout: float = WAIT_TIME) -> bool:
    """Wait for a file to become accessible within a timeout period.

    Args:
        filepath: Path to the file to check.
        timeout: Maximum time to wait in seconds.

    Returns:
        bool: True if the file becomes accessible within the timeout; False otherwise.
    """
    start_time = time.time()
    while True:
        if os.path.exists(filepath):
            return True
        if time.time() - start_time > timeout:
            logger.warning("Timeout waiting for file to be accessible: %s", filepath)
            return False
        logger.info("Waiting for file to be accessible: %s", filepath)
        time.sleep(0.1)
    return False


def _ensure_tex_dir(asset_data: dict[str, Any], resolution: str) -> str:
    """Ensure texture directory for resolution exists and return its Blender-relative path."""
    tex_dir_path = paths.get_texture_directory(asset_data, resolution=resolution)
    tex_dir_abs = bpy.path.abspath(tex_dir_path)
    if not os.path.exists(tex_dir_abs):
        os.makedirs(tex_dir_abs, exist_ok=True)
    return tex_dir_path


def _unpack_images_to(tex_dir_path: str, resolution: str) -> list[str]:
    """Write packed images to the target directory and repath image datablocks."""
    unpacked_files = []
    try:
        for image in bpy.data.images:
            if image.name == "Render Result":
                continue
            fp = paths.get_texture_filepath(tex_dir_path, image, resolution=resolution)
            logger.info("Unpacking image %s -> %s", image.name, fp)

            for pf in image.packed_files:
                pf.filepath = fp

            if image.packed_files:
                # WRITE_ORIGINAL writes to image.filepath; safer than REMOVE in our workflow
                try:
                    image.unpack(method="WRITE_ORIGINAL")
                except RuntimeError:
                    logger.exception("Failed to unpack image %s", image.name)

            # here is an issue where file is not immediately accessible after unpacking
            # we try to mitigate this by waiting a bit after unpacking all files
            absolute_path = _get_texture_abs_path(fp)
            _ = _wait_for_resource(absolute_path)

            image.filepath = fp
            image.filepath_raw = fp

            unpacked_files.append(fp)
    except Exception:
        logger.exception("Error unpacking images")
    return unpacked_files


def _set_asset_tags(data_block: object | None, asset_data: dict[str, Any]) -> None:
    """Assign description and tags to a data block's asset_data if available."""
    if bpy.app.version < (3, 0, 0) or data_block is None:
        return
    tags = data_block.asset_data.tags
    for t in list(tags):
        tags.remove(t)
    desc = asset_data.get("description", "")
    tg = asset_data.get("tags", [])
    tags.new(f"description: {desc}")
    tags.new("tags: " + ",".join(tg))


def _mark_model_assets() -> None:
    """Mark top-level visible objects as assets (no tags on a specific data block)."""
    if bpy.app.version < (3, 0, 0):
        return
    visibles = getattr(bpy.context, "visible_objects", [])
    for ob in bpy.data.objects:
        if ob.parent is None and ob in visibles:
            ob.asset_mark()


def _mark_material_assets() -> object | None:
    """Mark all materials as assets and return one representative data block."""
    data_block: object | None = None
    if bpy.app.version >= (3, 0, 0):
        for m in bpy.data.materials:
            m.asset_mark()
            data_block = m
    return data_block


def _mark_scene_asset() -> None:
    """Mark the current scene as an asset."""
    if bpy.app.version >= (3, 0, 0):
        bpy.context.scene.asset_mark()


def _mark_brush_assets() -> object | None:
    """Mark qualifying brushes as assets and return one representative data block."""
    data_block: object | None = None
    if bpy.app.version >= (3, 0, 0):
        for b in bpy.data.brushes:
            if b.get("asset_data") is not None:
                b.asset_mark()
                data_block = b
    return data_block


def _mark_assets(asset_data: dict[str, Any]) -> None:
    """Mark relevant data blocks as assets and set tags when available."""
    atype = asset_data.get("assetType")

    data_block: object | None = None
    if atype == "model":
        _mark_model_assets()
    elif atype == "material":
        data_block = _mark_material_assets()
    elif atype == "scene":
        _mark_scene_asset()
    elif atype == "brush":
        data_block = _mark_brush_assets()

    _set_asset_tags(data_block, asset_data)


def unpack_asset(data: dict[str, Any]) -> None:
    """Unpack images for the asset and save the .blend, logging progress and errors.

    Args:
        data: Input dict (from JSON) containing 'asset_data' with keys like
            'resolution', 'assetType', 'description', 'tags'.
    """
    asset_data = data["asset_data"]
    resolution = asset_data.get("resolution", "blend")

    tex_dir_path = _ensure_tex_dir(asset_data, resolution)
    bpy.data.use_autopack = False

    unpacked_images = _unpack_images_to(tex_dir_path, resolution)
    logger.info("Unpacked %d images to %s", len(unpacked_images), tex_dir_path)
    _mark_assets(asset_data)

    # If this isn't here, Blender may crash when saving.
    if bpy.app.version >= (3, 0, 0):
        bpy.context.preferences.filepaths.file_preview_type = "NONE"

    try:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, compress=False)
    except RuntimeError:
        logger.exception("Failed to save blend file")
    # now try to delete the .blend1 file
    backup = bpy.data.filepath + "1"
    try:
        if os.path.exists(backup):
            os.remove(backup)
    except OSError:
        logger.exception("Failed to remove backup file: %s", backup)


if __name__ == "__main__":
    logger.info("Background asset unpack")
    datafile = sys.argv[-1]

    try:
        with open(datafile, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input: %s", datafile)
        sys.exit(2)

    unpack_asset(data)
    bpy.ops.wm.quit_blender()
    sys.exit(0)
