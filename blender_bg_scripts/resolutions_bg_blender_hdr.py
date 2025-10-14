"""Generate lower resolutions for HDR images in Blender background mode.

This script loads an HDR image, generates lower-resolution EXR variants with
standard suffixes (e.g., _2k, _1k, _512), and outputs a JSON listing of the
generated files for the main process to upload.
"""

import json
import os
import sys
from typing import Any

import bpy

# isort: off  # path injection is required for Blender background execution
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import image_utils, paths, log  # isort: skip  # noqa: E402


logger = log.create_logger(__name__)


def generate_lower_resolutions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate lower-resolution EXR files from the provided HDR image path.

    Args:
        data: Input data with keys 'asset_data', 'file_path', and 'result_filepath'.

    Returns:
        A list of dictionaries describing the generated files.
    """
    # Input may contain asset_data metadata; it's not required here.
    fpath = data["file_path"]

    try:
        hdr = bpy.data.images.load(fpath)
    except RuntimeError:
        logger.exception("Failed to load HDR image: %s", fpath)
        return []

    actres = max(hdr.size[0], hdr.size[1])
    p2res = paths.round_to_closest_resolution(actres)
    try:
        original_filesize = os.path.getsize(fpath)
    except OSError:
        logger.exception("Failed to stat original HDR file: %s", fpath)
        return []

    i = 0
    finished = False
    files: list[dict[str, Any]] = []

    while not finished:
        fn_strip, _ext = os.path.splitext(fpath)
        ext = ".exr"
        if i > 0:
            image_utils.downscale(hdr)

        hdr_resolution_filepath = fn_strip + paths.resolution_suffix[p2res] + ext
        image_utils.img_save_as(
            hdr,
            filepath=hdr_resolution_filepath,
            file_format="OPEN_EXR",
            quality=20,
            color_mode="RGB",
            compression=15,
            view_transform="Raw",
            exr_codec="DWAA",
        )

        if os.path.exists(hdr_resolution_filepath):
            try:
                reduced_filesize = os.path.getsize(hdr_resolution_filepath)
            except OSError:
                logger.exception("Failed to stat generated file: %s", hdr_resolution_filepath)
                reduced_filesize = original_filesize
        else:
            logger.warning("Generated file not found: %s", hdr_resolution_filepath)
            reduced_filesize = original_filesize

        logger.info(
            "HDR size reduced from %d to %d for type %s",
            original_filesize,
            reduced_filesize,
            p2res,
        )

        # Only include file if it is smaller than the original
        if reduced_filesize < original_filesize:
            files.append(
                {
                    "type": p2res,
                    "index": 0,
                    "file_path": hdr_resolution_filepath,
                },
            )
            logger.info("Prepared resolution file: %s", p2res)

        if paths.rkeys.index(p2res) == 0:
            finished = True
        else:
            p2res = paths.rkeys[paths.rkeys.index(p2res) - 1]
        i += 1

    logger.info("Uploading resolution files: %s", files)
    with open(data["result_filepath"], "w", encoding="utf-8") as s:
        json.dump(files, s, ensure_ascii=False, indent=4)

    return files


if __name__ == "__main__":
    logger.info("Background resolution generator (HDR)")
    datafile = sys.argv[-1]
    try:
        with open(datafile, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input: %s", datafile)
        sys.exit(2)

    _ = generate_lower_resolutions(data)
    sys.exit(0)
