"""Generate lower resolutions for texture-based assets in Blender background.

This script iterates over all images in the current .blend, computes the
closest standard resolution, creates downscaled copies into a resolution-
specific textures directory, saves a new .blend copy per resolution level,
and returns a JSON list of generated files.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import bpy

# Path injection so Blender can import our utils when running in background
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)
from blenderkit_server_utils import image_utils, paths, log  # isort: skip  # noqa: E402

# Constants
MIN_NO_PREVIEW_VERSION = (3, 0, 0)


logger = log.create_logger(__name__)


def get_current_resolution() -> int:
    """Find the maximum image resolution in the .blend file.

    Returns:
        The maximum of width/height across all images excluding render/viewer.
    """
    actres = 0
    for img in bpy.data.images:
        if img.name not in {"Render Result", "Viewer Node"}:
            actres = max(actres, img.size[0], img.size[1])
    return actres


def _compute_original_textures_size() -> int:
    """Compute the total size of all existing image files in the scene."""
    total = 0
    for img in bpy.data.images:
        abspath = bpy.path.abspath(img.filepath)
        if os.path.exists(abspath):
            try:
                total += os.path.getsize(abspath)
            except OSError:
                logger.exception("Failed to stat image file: %s", abspath)
    return total


def _prepare_texture_dir(asset_data: dict[str, Any], resolution: str) -> str:
    """Ensure and return the Blender-relative texture directory for resolution."""
    tex_dir_path = paths.get_texture_directory(asset_data, resolution=resolution)
    tex_dir_abs = bpy.path.abspath(tex_dir_path)
    if not os.path.exists(tex_dir_abs):
        os.makedirs(tex_dir_abs, exist_ok=True)
    return tex_dir_path


def _process_images_for_resolution(tex_dir_path: str, *, p2res: str, orig_res: str) -> int:
    """Process and write all images for the given resolution; return total size."""
    reduced_total = 0
    for img in bpy.data.images:
        if img.name in ["Render Result", "Viewer Node"]:
            continue

        logger.info("Scaling image %s (%dx%d)", img.name, img.size[0], img.size[1])
        if img.size[0] == 0 or img.size[1] == 0:
            logger.warning("Image %s is empty", img.name)
            continue

        fp = paths.get_texture_filepath(tex_dir_path, img, resolution=p2res)
        if p2res == orig_res:
            img["blenderkit_original_path"] = img.filepath
            image_utils.make_possible_reductions_on_image(
                img,
                fp,
                do_reductions=True,
                do_downscale=False,
            )
        else:
            image_utils.make_possible_reductions_on_image(
                img,
                fp,
                do_reductions=False,
                do_downscale=True,
            )

        abspath = bpy.path.abspath(img.filepath)
        if os.path.exists(abspath):
            try:
                reduced_total += os.path.getsize(abspath)
            except OSError:
                logger.exception("Failed to stat generated image: %s", abspath)

        img.pack()

    return reduced_total


def _save_resolution_blend(fpath: str) -> bool:
    """Save a copy of the current .blend to the given path, safely."""
    if bpy.app.version >= MIN_NO_PREVIEW_VERSION:
        bpy.context.preferences.filepaths.file_preview_type = "NONE"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=fpath, compress=True, copy=True)
    except RuntimeError:
        logger.exception("Failed to save blend file: %s", fpath)
        return False
    else:
        return True


def generate_lower_resolutions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate lower-resolution .blend copies with downscaled textures.

    Steps:
        1. Compute the current asset resolution.
        2. Round to the nearest standard resolution (4k, 2k, 1k, 512).
        3. For each step down, write textures into a resolution-specific directory and save a copy of the .blend.
        4. Collect created files in a list for the caller to upload.

    Args:
        data: Input dict with 'asset_data'.

    Returns:
        List of dicts: [{"type", "index", "file_path"}].
    """
    files: list[dict[str, Any]] = []
    asset_data = data["asset_data"]
    base_fpath = bpy.data.filepath

    actual_resolution = get_current_resolution()
    logger.info("Current asset resolution: %d", actual_resolution)
    if actual_resolution <= 0:
        logger.info("resolution<=0, probably procedural asset -> skipping")
        return []

    p2res = paths.round_to_closest_resolution(actual_resolution)
    orig_res = p2res
    logger.info("Starting resolution key: %s", p2res)

    if p2res == paths.rkeys[0]:
        logger.info("Asset is at the lowest possible resolution -> skipping")
        return []

    original_textures_filesize = _compute_original_textures_size()

    finished = False
    while not finished:
        blend_file_name = os.path.basename(base_fpath)
        dirn = os.path.dirname(base_fpath)
        fn_strip, ext = os.path.splitext(blend_file_name)

        fn = fn_strip + paths.resolution_suffix[p2res] + ext
        fpath = os.path.join(dirn, fn)

        try:
            tex_dir_path = _prepare_texture_dir(asset_data, p2res)
        except OSError:
            logger.exception("Failed to create texture directory for %s", p2res)
            return []

        reduced_textures_filessize = _process_images_for_resolution(
            tex_dir_path,
            p2res=p2res,
            orig_res=orig_res,
        )

        logger.info("Saving resolution blend: %s", fpath)
        if not _save_resolution_blend(fpath):
            return []

        if reduced_textures_filessize < original_textures_filesize:
            logger.info(
                "Textures size reduced from %d to %d",
                original_textures_filesize,
                reduced_textures_filessize,
            )
            files.append({"type": p2res, "index": 0, "file_path": fpath})
        else:
            logger.info(
                "Skipping resolution %s: not reduced (orig=%d, gen=%d)",
                p2res,
                original_textures_filesize,
                reduced_textures_filessize,
            )

        logger.info("Prepared resolution file: %s", p2res)
        if paths.rkeys.index(p2res) == 0:
            finished = True
        else:
            p2res = paths.rkeys[paths.rkeys.index(p2res) - 1]

    logger.info("Prepared resolution files: %s", files)
    return files


if __name__ == "__main__":
    logger.info("Background resolution generator (non-HDR)")
    datafile = sys.argv[-1]
    try:
        with open(datafile, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input: %s", datafile)
        sys.exit(2)

    result_files = generate_lower_resolutions(data)
    try:
        with open(data["result_filepath"], "w", encoding="utf-8") as f:
            json.dump(result_files, f, ensure_ascii=False, indent=4)
    except OSError:
        logger.exception(
            "Failed to write result JSON: %s",
            data.get("result_filepath"),
        )
        sys.exit(3)

    sys.exit(0)
