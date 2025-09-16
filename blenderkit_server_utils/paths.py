# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Common path and filename helpers for BlenderKit background tasks.

This module centralizes server/API constants, file naming utilities, resolution
helpers, and common local directory paths used by background scripts.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None  # type: ignore
    logging.getLogger(__name__).debug("bpy not present; running outside Blender")

# Local imports used by some helpers.
from . import utils

logger = logging.getLogger(__name__)
# Provide a minimal logging configuration when no handlers are configured (CLI/background usage)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
dir_path: str = os.path.dirname(os.path.realpath(__file__))
parent_path: str = os.path.join(dir_path, os.path.pardir)

SERVER: str = os.environ.get("BLENDERKIT_SERVER", "https://www.blenderkit.com")
API_KEY: str = os.environ.get("BLENDERKIT_API_KEY", "")
BLENDERKIT_API: str = "/api/v1"
BLENDERS_PATH: str = os.environ.get("BLENDERS_PATH", "")


BG_SCRIPTS_PATH: str = os.path.join(parent_path, "blender_bg_scripts")

SLUG_MAX_LENGTH: int = 50

resolutions: dict[str, int] = {
    "resolution_0_5K": 512,
    "resolution_1K": 1024,
    "resolution_2K": 2048,
    "resolution_4K": 4096,
    "resolution_8K": 8192,
}
rkeys: list[str] = list(resolutions.keys())


resolution_suffix: dict[str, str] = {
    "blend": "",
    "resolution_0_5K": "_05k",
    "resolution_1K": "_1k",
    "resolution_2K": "_2k",
    "resolution_4K": "_4k",
    "resolution_8K": "_8k",
}


def ensure_bpy(func):
    """Decorator to ensure bpy is available for functions that need it.

    If bpy is not available, the decorated function will log a warning and return None.

    Args:
        func: The function to decorate.
    """

    def wrapper(*args, **kwargs):
        if bpy is None:
            logger.warning("bpy not available; cannot execute %s", func.__name__)
            return None
        return func(*args, **kwargs)

    return wrapper


def get_api_url() -> str:
    """Return BlenderKit API base URL."""
    url = SERVER + BLENDERKIT_API
    return url


def default_global_dict() -> str:
    """Return the base data directory for BlenderKit cache.

    Respects XDG_DATA_HOME if set, otherwise defaults to the user's home folder.

    Returns:
        Absolute path to the base BlenderKit data directory.
    """
    home = os.path.expanduser("~")
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home is not None:
        home = data_home
    base_dir = os.path.join(home, "blenderkit_data")
    return base_dir


def get_download_dir(asset_type: str) -> str:
    """Get/download directory for assets by type. Creates it if necessary.

    Args:
        asset_type: One of brush, texture, model, scene, material, hdr.

    Returns:
        Absolute path to the target download directory.

    Raises:
        KeyError: If asset_type is not recognized.
        OSError: If directory creation fails.
    """
    subd_mapping = {
        "brush": "brushes",
        "texture": "textures",
        "model": "models",
        "scene": "scenes",
        "material": "materials",
        "hdr": "hdrs",
    }

    ddir = default_global_dict()
    if not os.path.exists(ddir):
        os.makedirs(ddir)

    subdir_name = subd_mapping[asset_type]
    subdir = os.path.join(ddir, subdir_name)
    if not os.path.exists(subdir):
        os.makedirs(subdir)
    return subdir


def slugify(slug: str) -> str:
    """Normalize a string for safe filenames.

    Lowercases, replaces disallowed characters, and trims length.

    Args:
        slug: Source string.

    Returns:
        A sanitized, lowercased, and shortened slug.
    """
    import re

    slug = slug.lower()

    characters = '<>:"/\\|?\\*., ()#'
    for ch in characters:
        slug = slug.replace(ch, "_")
    # Keep original regular expressions for compatibility
    slug = re.sub(r"[^a-z0-9]+.- ", "-", slug).strip("-")
    slug = re.sub(r"[-]+", "-", slug)
    slug = re.sub(r"/", "_", slug)
    slug = re.sub(r"\\\'\"", "_", slug)
    if len(slug) > SLUG_MAX_LENGTH:
        slug = slug[:SLUG_MAX_LENGTH]
    return slug


def extract_filename_from_url(url: str | None) -> str:
    """Extract filename from a URL.

    Args:
        url: URL string or None.

    Returns:
        The filename portion of the URL, or an empty string if not available.
    """
    if url is not None:
        imgname = url.split("/")[-1]
        imgname = imgname.split("?")[0]
        return imgname
    return ""


def round_to_closest_resolution(res: int) -> str:
    """Return the closest available resolution key to the given pixel size.

    Args:
        res: Desired resolution size in pixels (e.g., 2048).

    Returns:
        The key name from the resolutions mapping representing the closest size.
    """
    rdist = 1_000_000
    # Initialize to a deterministic default to avoid unbound variable warnings.
    best_key = next(iter(resolutions))
    for rkey, rval in resolutions.items():
        d = abs(res - rval)
        if d < rdist:
            rdist = d
            best_key = rkey
    return best_key


def get_res_file(
    asset_data: dict[str, Any],
    resolution: str,
    *,
    find_closest_with_url: bool = False,  # kept for compatibility; not used
) -> tuple[dict[str, Any] | None, str]:
    """Pick a file entry matching the desired resolution, or a close fallback.

    If there are no resolution files, returns the original .blend file.
    If resolution is 'blend', returns the original .blend file.

    Args:
        asset_data: Asset data containing a 'files' list of dicts.
        resolution: Desired resolution key (e.g., 'resolution_2K' or 'blend').
        find_closest_with_url: Kept for compatibility; not used.

    Returns:
        A tuple of (file_entry, resolved_resolution_key).
    """
    del find_closest_with_url  # unused, kept for compatibility

    orig: dict[str, Any] | None = None
    closest: dict[str, Any] | None = None
    target_resolution = resolutions.get(resolution)
    mindist = 100_000_000

    for f in asset_data["files"]:
        if f["fileType"] == "blend":
            orig = f
            if resolution == "blend":
                return orig, "blend"

        if f["fileType"] == resolution:
            return f, resolution

        rval = resolutions.get(f["fileType"])
        if rval and target_resolution:
            rdiff = abs(target_resolution - rval)
            if rdiff < mindist:
                closest = f
                mindist = rdiff

    if closest is None:
        return orig, "blend"

    return closest, closest["fileType"]


def server_2_local_filename(asset_data: dict[str, Any], filename: str) -> str:
    """Convert a server-side file name to a local, slugified file name.

    Args:
        asset_data: Asset data dict; uses 'name' for slug prefix.
        filename: Original filename from server.

    Returns:
        A normalized local filename suitable for filesystem use.
    """
    fn = filename.replace("blend_", "")
    fn = fn.replace("resolution_", "")
    local_name = f"{slugify(asset_data['name'])}_{fn}"
    return local_name


def get_texture_directory(asset_data: dict[str, Any], resolution: str = "blend") -> str:
    """Get a relative texture directory for the given resolution.

    Args:
        asset_data: Asset metadata (unused; kept for signature compatibility).
        resolution: Resolution key, such as 'blend' or 'resolution_2K'.

    Returns:
        A relative Blender path like '//textures_2k/' depending on resolution.
    """
    del asset_data  # unused
    tex_dir_path = f"//textures{resolution_suffix[resolution]}{os.sep}"
    return tex_dir_path


@ensure_bpy
def get_texture_filepath(tex_dir_path: str, image: Any, resolution: str = "blend") -> str:
    """Return a unique texture path under the given directory for an image.

    If an image of the same name already exists with the path, append an index
    to keep the path unique.

    Args:
        tex_dir_path: Base directory for textures (can be a Blender path like //textures/).
        image: Blender image object (bpy.types.Image) or a compatible object.
        resolution: Resolution key (unused; kept for compatibility).

    Returns:
        A filepath string that should be unique among bpy.data.images.
    """
    del resolution  # unused

    if len(image.packed_files) > 0:
        image_file_name = bpy.path.basename(image.packed_files[0].filepath)
    else:
        image_file_name = bpy.path.basename(image.filepath)

    if image_file_name == "":
        image_file_name = image.name.split(".")[0]

    fp = os.path.join(tex_dir_path, image_file_name)
    fpn = fp

    done = False
    i = 0
    while not done:
        is_solo = True
        for image1 in bpy.data.images:
            if image != image1 and image1.filepath == fpn:
                is_solo = False
                fpleft, fpext = os.path.splitext(fp)
                fpn = fpleft + str(i).zfill(3) + fpext
                i += 1
        if is_solo:
            done = True

    return fpn


def delete_asset_debug(asset_data: dict[str, Any]) -> None:
    """Delete local asset files for debugging.

    Note:
        This is a debug helper. It logs and continues on per-asset deletion errors.

    Args:
        asset_data: Asset data dict.
    """
    from . import download  # local import to avoid cycles

    # utils.get_scene_id and api_key are context-dependent; left as in original code.
    try:
        _ = download.get_download_url(asset_data, utils.get_scene_id(), API_KEY)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError, OSError):
        logger.exception("Failed to obtain download URL for asset id=%s", asset_data.get("id"))

    file_names = get_download_filepaths(asset_data)
    for f in file_names:
        asset_dir = os.path.dirname(f)
        if os.path.isdir(asset_dir):
            try:
                logger.info("Removing debug asset directory: %s", asset_dir)
                shutil.rmtree(asset_dir)
            except (FileNotFoundError, PermissionError, OSError):
                logger.exception("Failed to remove directory: %s", asset_dir)


def get_clean_filepath() -> str:
    """Return the path to the clean template .blend file."""
    script_path = os.path.dirname(os.path.realpath(__file__))
    subpath = f"blendfiles{os.sep}cleaned.blend"
    cp = os.path.join(script_path, subpath)
    return cp


def get_addon_file(subpath: str = "") -> str:
    """Return an absolute path under this package directory.

    Args:
        subpath: A relative subpath under the module folder.

    Returns:
        Absolute path to the requested resource.
    """
    script_path = os.path.dirname(os.path.realpath(__file__))
    outpath = os.path.join(script_path, subpath)
    return outpath


def get_download_filepaths(asset_data: dict[str, Any]) -> list[str]:
    """Derive expected local filepaths for downloaded files of an asset.

    Builds paths under the per-asset directory using the server-provided
    filenames when available. Falls back to the asset directory if file
    metadata is incomplete.

    Args:
        asset_data: Asset metadata containing keys like 'id', 'assetType' or 'type',
            and an optional 'files' list with entries holding 'fileName' and/or 'url'.

    Returns:
        A list of absolute filepaths under the asset's local download directory.
        If no file entries are present, returns the asset directory path as a single item.
    """
    # Resolve asset type/name gracefully.
    asset_type = str(asset_data.get("assetType") or asset_data.get("type") or "model")
    asset_id = str(asset_data.get("id") or "")
    base_dir = os.path.join(get_download_dir(asset_type), asset_id) if asset_id else get_download_dir(asset_type)

    files_meta = asset_data.get("files") or []
    filepaths: list[str] = []

    for f in files_meta:
        try:
            url = f.get("url") or f.get("downloadUrl") or ""
            server_name = f.get("fileName") or extract_filename_from_url(url)
            if server_name:
                local_name = server_2_local_filename(asset_data, server_name)
                target_path = os.path.join(base_dir, local_name)
            else:
                target_path = base_dir
            filepaths.append(target_path)
        except (TypeError, AttributeError, KeyError):  # noqa: PERF203
            logger.exception("Invalid file entry while deriving paths for asset id=%s", asset_id)

    if not filepaths:
        filepaths.append(base_dir)

    return filepaths
