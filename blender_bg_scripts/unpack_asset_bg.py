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
import urllib.error
import urllib.request
import uuid
from functools import lru_cache
from typing import Any

import bpy

# Path injection so Blender can import our utils when running in background
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import paths, log, utils  # isort: skip  # noqa: E402


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

    # get just basename of texture_path
    texture_path = os.path.basename(texture_path)

    scene_dir = _get_scene_directory()
    # does scene dir and /textures exist?
    textures_dir = os.path.join(scene_dir, "textures")
    if os.path.exists(textures_dir):
        return bpy.path.abspath(os.path.join(textures_dir, texture_path))

    # fallback to scene dir
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
            logger.debug("Timeout waiting for file to be accessible: %s", filepath)
            return False
        logger.debug("Waiting for file to be accessible: %s", filepath)
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
    # we need to monitor disk space on git specially for large textures
    # current scene path
    scene_dir = os.path.dirname(bpy.path.abspath(bpy.data.filepath))
    logger.info("Disk space before unpacking: %sGB", utils.get_disk_free_space_gb(scene_dir))
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

            logger.info("Disk space after unpacking %s: %sGB", image.name, utils.get_disk_free_space_gb(scene_dir))
    except Exception:
        logger.exception("Error unpacking images")
    return unpacked_files


# Minimum Blender version that supports the asset browser (asset_mark/asset_clear).
_MIN_ASSET_VERSION = (3, 0, 0)

# Integer bounds for Blender IDProperty storage; larger ints overflow int32.
_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647

# Asset-type folder names used to derive the library root from a blend path.
_ASSET_TYPE_DIRS = {
    "models",
    "materials",
    "hdrs",
    "scenes",
    "brushes",
    "textures",
    "nodegroups",
    "printables",
    "addons",
}

# Datablock collections (on bpy.data) that can hold an asset_mark and that a
# BlenderKit asset file might contain. Used to enforce "exactly one asset".
_MARKABLE_DATA_COLLECTIONS = (
    "objects",
    "collections",
    "materials",
    "node_groups",
    "brushes",
    "worlds",
    "images",
)

# Maps an asset type to its top-level catalog name in the asset browser.
_CATALOG_MAP = {
    "model": "Models",
    "material": "Materials",
    "hdr": "HDRIs",
    "hdri": "HDRIs",
    "printable": "Printables",
    "scene": "Scenes",
    "brush": "Brushes",
    "texture": "Textures",
    "nodegroup": "Node Groups",
    "addon": "Add-ons",
}

# Maximum length (including the metadata key) of a single asset tag.
_MAX_TAG_LENGTH = 59


def _can_mark() -> bool:
    """Return whether the current Blender supports asset marking."""
    return bpy.app.version >= _MIN_ASSET_VERSION


def _sanitize_for_idprops(value: Any) -> Any:
    """Recursively sanitize a value so it can be stored as a Blender IDProperty.

    Large integers that would overflow int32 are converted to strings.

    Args:
        value: Value to sanitize.

    Returns:
        The sanitized value, safe for Blender IDProperty storage.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value < _INT32_MIN or value > _INT32_MAX:
            return str(value)
        return value
    if isinstance(value, dict):
        return {k: _sanitize_for_idprops(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_idprops(v) for v in value]
    return value


def _resolve_author_name(asset_data: dict[str, Any]) -> str:
    """Return the author's display name from asset data.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        The author full name, or a first/last name combination.
    """
    author = asset_data.get("author") or {}
    full_name = author.get("fullName") or ""
    if full_name:
        return full_name
    first = author.get("firstName") or ""
    last = author.get("lastName") or ""
    return f"{first} {last}".strip()


def _resolve_thumbnail_url(asset_data: dict[str, Any]) -> str:
    """Return the best available thumbnail URL from asset data.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        A thumbnail URL, or an empty string when none is present.
    """
    for key in ("thumbnailMiddleUrl", "thumbnailSmallUrl", "thumbnailLargeUrl"):
        url = asset_data.get(key)
        if url:
            return str(url)

    for file in asset_data.get("files", []):
        if file.get("fileType") not in ("thumbnail", "photo_thumbnail", "wire_thumbnail"):
            continue
        for key in ("thumbnailMiddleUrl", "thumbnailSmallUrl", "fileThumbnailLarge", "fileThumbnail"):
            url = file.get(key)
            if url:
                return str(url)

    return ""


def _library_dir_from_fpath(blend_path: str) -> str:
    """Derive the library root from a blend file path by stripping the asset_type folder.

    Expected structure: ``<library>/<asset_type>/<asset_id>/<file>.blend``. Falls
    back to two levels above the blend file directory.

    Args:
        blend_path: Absolute or relative path to the asset .blend file.

    Returns:
        The resolved library root path, or an empty string when input is empty.
    """
    if not blend_path:
        return ""

    norm_path = os.path.abspath(blend_path)
    parts = norm_path.split(os.sep)
    for idx, part in enumerate(parts):
        if part.lower() in _ASSET_TYPE_DIRS and idx > 0:
            return os.sep.join(parts[:idx])

    dir_path = os.path.dirname(norm_path)
    return os.path.abspath(os.path.join(dir_path, os.pardir, os.pardir))


def _download_thumbnail(url: str) -> str:
    """Download the thumbnail image next to the current .blend file.

    Args:
        url: HTTP(S) URL of the thumbnail to download.

    Returns:
        The path of the downloaded thumbnail, or an empty string on failure.
    """
    if not url or not url.startswith(("http://", "https://")):
        return ""
    target_dir = os.path.dirname(bpy.data.filepath)
    if not target_dir:
        return ""
    target_path = os.path.join(target_dir, "preview.png")
    if os.path.exists(target_path):
        return target_path
    try:
        req = urllib.request.Request(url)  # noqa: S310  # nosec B310 - scheme restricted to http(s) above
        req.add_header("User-Agent", "BlenderKit")
        req.add_header("Accept", "image/*")
        with urllib.request.urlopen(req, timeout=15) as response, open(target_path, "wb") as handle:  # noqa: S310  # nosec B310
            handle.write(response.read())
    except (urllib.error.URLError, OSError):
        logger.exception("Failed to download thumbnail from %s", url)
        return ""
    else:
        return target_path


def _sanitize_preview_image(preview_path: str) -> str:
    """Re-save a preview image as PNG so Blender can load it reliably.

    Args:
        preview_path: Path to the source preview image.

    Returns:
        Path to the sanitized PNG, or an empty string on failure.
    """
    if not preview_path or not os.path.exists(preview_path):
        return ""
    base_dir = os.path.dirname(preview_path)
    base_name = os.path.splitext(os.path.basename(preview_path))[0]
    sanitized_path = os.path.join(base_dir, f"{base_name}_clean.png")
    if os.path.exists(sanitized_path):
        return sanitized_path
    img = None
    try:
        img = bpy.data.images.load(preview_path, check_existing=False)
        img.filepath_raw = sanitized_path
        img.file_format = "PNG"
        img.save()
    except (RuntimeError, OSError):
        logger.exception("Failed to sanitize preview image %s", preview_path)
        return ""
    else:
        return sanitized_path
    finally:
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except (RuntimeError, ReferenceError):
                logger.debug("Could not remove temporary preview image")


def _op_poll(op_callable: Any, data_block: Any) -> bool:
    """Check whether an operator can run in the context of a data block.

    Args:
        op_callable: The bpy operator callable to poll.
        data_block: The ID data block to use as the operator's context.

    Returns:
        True when the operator reports it can run, False otherwise.
    """
    try:
        if hasattr(bpy.context, "temp_override"):
            with bpy.context.temp_override(id=data_block):
                return op_callable.poll()
        override = bpy.context.copy()
        override["id"] = data_block
        return op_callable.poll(override)
    except (RuntimeError, TypeError):
        return False


def _op_call(op_callable: Any, data_block: Any, **kwargs: Any) -> Any:
    """Call an operator in the context of a data block.

    Args:
        op_callable: The bpy operator callable to invoke.
        data_block: The ID data block to use as the operator's context.
        **kwargs: Keyword arguments forwarded to the operator.

    Returns:
        The operator's return value.
    """
    if hasattr(bpy.context, "temp_override"):
        with bpy.context.temp_override(id=data_block):
            return op_callable(**kwargs)
    override = bpy.context.copy()
    override["id"] = data_block
    return op_callable(override, **kwargs)


def _load_custom_preview(data_block: Any, preview_path: str) -> bool:
    """Try to apply a downloaded thumbnail as the asset's custom preview.

    Args:
        data_block: The asset-marked data block.
        preview_path: Path to the preview image.

    Returns:
        True when the custom preview was applied, False otherwise.
    """
    try:
        if _op_poll(bpy.ops.ed.lib_id_load_custom_preview, data_block):
            result = _op_call(bpy.ops.ed.lib_id_load_custom_preview, data_block, filepath=preview_path)
            return "FINISHED" in result
    except RuntimeError:
        logger.exception("Failed to load custom thumbnail preview")
    return False


def _apply_asset_preview(data_block: Any, asset_data: dict[str, Any]) -> None:
    """Apply an asset preview, downloading the thumbnail or generating one.

    Args:
        data_block: The asset-marked data block.
        asset_data: Asset metadata dictionary.
    """
    if data_block is None:
        return
    logger.info("Applying asset preview")
    url = _resolve_thumbnail_url(asset_data)
    preview_path = _download_thumbnail(url) if url else ""
    if preview_path:
        clean_path = _sanitize_preview_image(preview_path)
        if clean_path:
            preview_path = clean_path
        if _load_custom_preview(data_block, preview_path):
            logger.info("Thumbnail preview applied successfully")
            return

    try:
        if _op_poll(bpy.ops.ed.lib_id_generate_preview, data_block):
            _op_call(bpy.ops.ed.lib_id_generate_preview, data_block)
            logger.info("Generated preview applied successfully")
    except RuntimeError:
        logger.exception("Failed to generate preview; asset will have no preview")


def _reset_tags(data_block: Any) -> Any:
    """Remove all existing asset tags from a data block and return the tag list.

    Args:
        data_block: The asset-marked data block.

    Returns:
        The data block's asset tag collection.
    """
    tags = data_block.asset_data.tags
    for t in list(tags):
        tags.remove(t)
    return data_block.asset_data.tags


def _collect_other_meta(asset_data: dict[str, Any]) -> dict[str, str]:
    """Collect searchable metadata key/value pairs from asset data.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        A mapping of metadata keys to string values.
    """
    other_meta: dict[str, str] = {}
    if asset_data.get("assetBaseId"):
        other_meta["id"] = asset_data["assetBaseId"]
    if asset_data.get("assetType"):
        other_meta["asset_type"] = asset_data.get("assetType", "")
    if asset_data.get("sourceAppVersion"):
        other_meta["source_app_version"] = asset_data.get("sourceAppVersion", "")

    dict_parameters = asset_data.get("dictParameters", {})
    simple_keys = {
        "category": "category",
        "condition": "condition",
        "pbrType": "pbr_type",
        "materialStyle": "material_style",
        "engine": "engine",
    }
    for src, dst in simple_keys.items():
        if src in dict_parameters:
            other_meta[dst] = dict_parameters[src]
    if dict_parameters.get("animated"):
        other_meta["animated"] = "yes"
    if dict_parameters.get("simulation"):
        other_meta["simulation"] = "yes"
    return other_meta


def _description_to_meta(description: str) -> dict[str, str]:
    """Split a description into chunked metadata tags within Blender's length limit.

    Args:
        description: The asset description text.

    Returns:
        A mapping of ``dNN`` keys to description chunks.
    """
    chunks: dict[str, str] = {}
    if not description:
        return chunks
    words = description.split()
    current_tag = ""
    dp_index = 0
    for word in words:
        if len(current_tag) + len(word) + 1 <= _MAX_TAG_LENGTH:
            current_tag += (" " if current_tag else "") + word
        else:
            if current_tag:
                chunks[f"d{dp_index:02}"] = current_tag
            dp_index += 1
            current_tag = word
    if current_tag:
        chunks[f"d{dp_index:02}"] = current_tag
    return chunks


def _write_metadata(data_block: Any, asset_data: dict[str, Any]) -> None:
    """Write asset metadata (tags, author, description, license) to a data block.

    Args:
        data_block: The asset-marked data block.
        asset_data: Asset metadata dictionary.
    """
    if data_block is None:
        return
    logger.info("Writing asset metadata")
    tags = _reset_tags(data_block)
    for t in asset_data.get("tags", []):
        tags.new(str(t))

    other_meta = _collect_other_meta(asset_data)
    description = asset_data.get("description", "")
    author_name = _resolve_author_name(asset_data)

    data_block.asset_data.author = author_name
    other_meta["author_name"] = author_name
    data_block.asset_data.description = description
    if hasattr(data_block.asset_data, "copyright"):
        cop = asset_data.get("copyright", "")
        data_block.asset_data.copyright = cop
        other_meta["copyright"] = cop
    if hasattr(data_block.asset_data, "license"):
        lic = asset_data.get("license", "")
        data_block.asset_data.license = lic
        other_meta["license"] = lic

    other_meta.update(_description_to_meta(description))
    for key, value in other_meta.items():
        tags.new(f"{key}:{value}")


def _sanitize_catalog_segment(segment: str) -> str:
    """Normalize a single catalog path segment.

    Args:
        segment: Raw catalog segment text.

    Returns:
        A cleaned segment, or ``Uncategorized`` when empty.
    """
    cleaned = (segment or "").strip()
    cleaned = cleaned.replace(":", "-").replace("/", "-").replace("\\", "-")
    return cleaned or "Uncategorized"


def _resolve_category_segments(asset_data: dict[str, Any]) -> list[str]:
    """Resolve the category portion of the catalog path from asset data.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        A list of sanitized category segments.
    """
    category_slug = asset_data.get("category") or asset_data.get("dictParameters", {}).get("category")
    if not category_slug:
        return []
    parts = [p for p in str(category_slug).split("-") if p]
    return [_sanitize_catalog_segment(part) for part in parts]


def _resolve_catalog_path_parts(asset_data: dict[str, Any]) -> list[str]:
    """Resolve the full catalog path parts for an asset.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        A list of catalog path segments (top level + category hierarchy).
    """
    asset_type = (asset_data.get("assetType") or "").lower()
    parts = []
    top_level = _CATALOG_MAP.get(asset_type)
    if top_level:
        parts.append(top_level)
    parts.extend(_resolve_category_segments(asset_data))
    return parts


def _read_existing_catalogs(cat_path: str) -> dict[str, str]:
    """Read existing catalog path -> UUID mappings from a catalog file.

    Args:
        cat_path: Path to the ``blender_assets.cats.txt`` file.

    Returns:
        A mapping of catalog path to catalog UUID.
    """
    cats: dict[str, str] = {}
    with open(cat_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 3:  # noqa: PLR2004
                continue
            cat_uuid, cat_path_entry, _ = parts
            cats[cat_path_entry] = cat_uuid
    return cats


def _ensure_catalog_exists(library_path: str, catalog_path: str, catalog_simple_name: str) -> str:
    """Ensure an asset catalog entry exists, creating the catalog file if needed.

    Args:
        library_path: Library root directory.
        catalog_path: Catalog path (``Models/MyCategory``).
        catalog_simple_name: Human-readable catalog name.

    Returns:
        The catalog UUID, or an empty string on failure.
    """
    head = (
        "# This is an Asset Catalog Definition file for Blender.\n"
        "#\n"
        "# Empty lines and lines starting with `#` will be ignored.\n"
        "# The first non-ignored line should be the version indicator.\n"
        '# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"\n'
        "\n"
        "VERSION 1\n"
    )
    cat_path = os.path.join(library_path, "blender_assets.cats.txt")
    if not os.path.exists(cat_path):
        try:
            with open(cat_path, "w", encoding="utf-8") as f:
                f.write(head)
        except OSError:
            logger.exception("Failed to create catalog file %s", cat_path)
            return ""

    cats = _read_existing_catalogs(cat_path)
    if catalog_path in cats:
        return cats[catalog_path]

    new_uuid = str(uuid.uuid4())
    try:
        with open(cat_path, "a", encoding="utf-8") as f:
            f.write(f"{new_uuid}:{catalog_path}:{catalog_simple_name}\n")
    except OSError:
        logger.exception("Failed to append catalog entry to %s", cat_path)
        return ""
    else:
        return new_uuid


def _assign_asset_catalog(data_block: Any, asset_data: dict[str, Any], blend_path: str | None = None) -> None:
    """Assign the asset to a catalog based on its type and category hierarchy.

    Args:
        data_block: The asset-marked data block.
        asset_data: Asset metadata dictionary.
        blend_path: Path to the asset .blend file used to locate the library root.
    """
    if data_block is None or data_block.asset_data is None:
        return
    if not blend_path:
        logger.warning("Asset catalog assignment skipped: blend path missing")
        return

    library_dir = os.path.abspath(bpy.path.abspath(_library_dir_from_fpath(blend_path)))
    if not os.path.exists(library_dir):
        try:
            os.makedirs(library_dir, exist_ok=True)
        except OSError:
            logger.warning("Asset catalog assignment skipped: cannot create '%s'", library_dir)
            return

    path_parts = _resolve_catalog_path_parts(asset_data)
    if not path_parts:
        logger.warning("Asset catalog assignment skipped: could not resolve catalog path")
        return

    catalog_path = "/".join(path_parts)
    catalog_id = _ensure_catalog_exists(library_dir, catalog_path, path_parts[-1])
    if not catalog_id:
        logger.warning("Asset catalog assignment skipped: failed to create catalog entry")
        return

    asset_meta = data_block.asset_data
    if hasattr(asset_meta, "catalog_id"):
        try:
            asset_meta.catalog_id = catalog_id
        except AttributeError:
            logger.warning("Asset catalog assignment skipped: catalog_id is read-only")
    else:
        logger.warning("Asset catalog assignment skipped: asset_data has no catalog_id attribute")


def _match_asset_by_name(datablocks: Any, asset_data: dict[str, Any]) -> Any:
    """Return the data block whose name matches the asset name, or None.

    Args:
        datablocks: Iterable of candidate data blocks.
        asset_data: Asset metadata dictionary.

    Returns:
        The matching data block, or None when no match is found.
    """
    name = (asset_data.get("name") or "").strip()
    if not name:
        return None
    for db in datablocks:
        if db.name == name:
            return db
    lname = name.lower()
    for db in datablocks:
        if db.name.lower() == lname:
            return db
    return None


def _is_official_nodegroup(ng: Any) -> bool:
    """Return whether a node group is an official Blender (non-asset) node group.

    Args:
        ng: The node group data block.

    Returns:
        True when the node group must not be treated as a downloadable asset.
    """
    ad = getattr(ng, "asset_data", None)
    if ad is None:
        return False
    if hasattr(ad, "copyright") and ad.copyright == "Blender Foundation":
        return True
    try:
        return bool(ad.is_property_readonly("author"))
    except (AttributeError, TypeError):
        return False


def _mark_single_asset(
    datablocks: Any,
    asset_data: dict[str, Any],
    skip: Any = None,
    fallback_to_first: bool = False,  # noqa: FBT001, FBT002
) -> Any:
    """Mark exactly one data block from ``datablocks`` as the scene's asset.

    Selection order: an already asset-marked data block -> a data block whose
    name matches the asset name -> optionally the first available data block.
    Any other asset-marked data block in ``datablocks`` is cleared.

    Args:
        datablocks: Iterable of candidate data blocks.
        asset_data: Asset metadata dictionary.
        skip: Optional predicate; data blocks for which it returns True are ignored.
        fallback_to_first: When True, mark the first candidate if no match is found.

    Returns:
        The chosen (marked) data block, or None when nothing was marked.
    """
    items = [db for db in datablocks if skip is None or not skip(db)]
    if not items:
        return None

    marked = [db for db in items if getattr(db, "asset_data", None) is not None]
    if marked:
        chosen = _match_asset_by_name(marked, asset_data) or marked[0]
    else:
        chosen = _match_asset_by_name(items, asset_data)
        if chosen is None and fallback_to_first:
            chosen = items[0]

    if chosen is None:
        return None

    if _can_mark():
        if getattr(chosen, "asset_data", None) is None:
            chosen.asset_mark()
        for db in items:
            if db is not chosen and getattr(db, "asset_data", None) is not None:
                db.asset_clear()
        chosen["asset_data"] = _sanitize_for_idprops(asset_data)

    return chosen


def _clear_stray_mark(db: Any, label: str) -> None:
    """Clear an asset mark from a data block, logging failures.

    Args:
        db: The data block to clear.
        label: Human-readable label used in log messages.
    """
    try:
        db.asset_clear()
    except RuntimeError:
        logger.exception("Could not clear stray asset mark on %s", label)


def _clear_marks_in_data(keep: Any) -> None:
    """Clear asset marks from all markable data collections except ``keep``.

    Args:
        keep: The data block that must remain the only marked asset.
    """
    for coll_name in _MARKABLE_DATA_COLLECTIONS:
        data_coll = getattr(bpy.data, coll_name, None)
        if not data_coll:
            continue
        for db in data_coll:
            if db is not keep and getattr(db, "asset_data", None) is not None:
                _clear_stray_mark(db, repr(db))


def enforce_single_asset_mark(keep: Any) -> None:
    """Guarantee that ``keep`` is the only asset-marked data block in the file.

    Clears asset marks from every other markable data block and from any scene
    other than ``keep``, healing files that arrive with stray/duplicate marks.

    Args:
        keep: The data block that must remain the only marked asset.
    """
    if not _can_mark() or keep is None:
        return
    _clear_marks_in_data(keep)
    for scene in bpy.data.scenes:
        if scene is not keep and getattr(scene, "asset_data", None) is not None:
            _clear_stray_mark(scene, f"scene {scene!r}")


def _select_main_collection(candidate_collections: list[Any], asset_data: dict[str, Any]) -> Any:
    """Choose the collection that should represent a model/printable asset.

    Order of reliability: an upload-marked collection -> a name match -> a
    collection containing root objects -> the first child collection.

    Args:
        candidate_collections: Top-level child collections of the scene.
        asset_data: Asset metadata dictionary.

    Returns:
        The chosen collection, or None when there are no candidates.
    """
    main = next((c for c in candidate_collections if getattr(c, "asset_data", None) is not None), None)
    if main is None:
        main = _match_asset_by_name(candidate_collections, asset_data)
    if main is None:
        main = next((c for c in candidate_collections if any(ob.parent is None for ob in c.objects)), None)
    if main is None and candidate_collections:
        main = candidate_collections[0]
    return main


def _mark_main_collection(main_collection: Any, candidate_collections: list[Any], asset_data: dict[str, Any]) -> Any:
    """Mark a chosen collection as the asset and clear competing marks.

    Args:
        main_collection: The collection to mark as the asset.
        candidate_collections: All top-level child collections of the scene.
        asset_data: Asset metadata dictionary.

    Returns:
        The marked collection.
    """
    for ob in bpy.data.objects:
        if getattr(ob, "asset_data", None) is not None:
            ob.asset_clear()
    for col in candidate_collections:
        if col is not main_collection and getattr(col, "asset_data", None) is not None:
            col.asset_clear()
    if _can_mark() and getattr(main_collection, "asset_data", None) is None:
        main_collection.asset_mark()
    main_collection["asset_data"] = _sanitize_for_idprops(asset_data)
    return main_collection


def _mark_fallback_root_object(asset_data: dict[str, Any]) -> Any:
    """Mark a single root object as the asset when no collection is available.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        The marked object, or None when there are no root objects.
    """
    root_objects = [ob for ob in bpy.data.objects if ob.parent is None]
    chosen = _match_asset_by_name(root_objects, asset_data) or (root_objects[0] if root_objects else None)
    if chosen is None:
        return None
    for ob in bpy.data.objects:
        if ob is not chosen and getattr(ob, "asset_data", None) is not None:
            ob.asset_clear()
    if _can_mark() and getattr(chosen, "asset_data", None) is None:
        chosen.asset_mark()
    chosen["asset_data"] = _sanitize_for_idprops(asset_data)
    return chosen


def _mark_model_collection(asset_data: dict[str, Any]) -> Any:
    """Mark the main collection (or a fallback root object) for a model/printable.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        The marked collection or object, or None when nothing could be marked.
    """
    scene_collection = bpy.context.scene.collection
    candidate_collections = list(scene_collection.children)
    main_collection = _select_main_collection(candidate_collections, asset_data)
    if main_collection is not None:
        return _mark_main_collection(main_collection, candidate_collections, asset_data)
    return _mark_fallback_root_object(asset_data)


def mark_asset(asset_data: dict[str, Any]) -> Any:
    """Mark exactly one data block as the file's asset based on its type.

    Args:
        asset_data: Asset metadata dictionary.

    Returns:
        The marked data block, or None when nothing was marked.
    """
    atype = asset_data.get("assetType")
    data_block: Any = None

    if atype in ("model", "printable"):
        data_block = _mark_model_collection(asset_data)
    elif atype == "material":
        data_block = _mark_single_asset(bpy.data.materials, asset_data, fallback_to_first=True)
    elif atype == "scene":
        if _can_mark():
            bpy.context.scene.asset_mark()
            data_block = bpy.context.scene
    elif atype == "brush":
        data_block = _mark_single_asset(bpy.data.brushes, asset_data, fallback_to_first=True)
    elif atype == "nodegroup":
        data_block = _mark_single_asset(bpy.data.node_groups, asset_data, skip=_is_official_nodegroup)
    else:
        logger.warning("Unrecognized asset type for marking: %s %s", atype, asset_data.get("id"))

    return data_block


def _mark_assets(asset_data: dict[str, Any], blend_path: str | None = None) -> None:
    """Mark the asset, enforce a single mark, and write metadata/preview/catalog.

    Args:
        asset_data: Asset metadata dictionary.
        blend_path: Path to the asset .blend file used for catalog assignment.
    """
    data_block = mark_asset(asset_data)
    if not _can_mark() or data_block is None:
        return
    enforce_single_asset_mark(data_block)
    _write_metadata(data_block, asset_data)
    _apply_asset_preview(data_block, asset_data)
    _assign_asset_catalog(data_block, asset_data, blend_path=blend_path)


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
    _mark_assets(asset_data, blend_path=bpy.data.filepath)

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


def mark_asset_only(data: dict[str, Any]) -> None:
    """Mark the asset and save the .blend without unpacking its textures.

    Unlike :func:`unpack_asset`, this keeps packed textures intact so the saved
    file can be re-uploaded as the canonical ``blend`` original with correct
    asset marking, without leaving external (unpacked) texture references.

    Args:
        data: Input dict (from JSON) containing 'asset_data'.
    """
    asset_data = data["asset_data"]

    # Keep textures packed so the re-uploaded original stays self-contained.
    bpy.data.use_autopack = True
    _mark_assets(asset_data, blend_path=bpy.data.filepath)

    # If this isn't here, Blender may crash when saving.
    if bpy.app.version >= (3, 0, 0):
        bpy.context.preferences.filepaths.file_preview_type = "NONE"

    try:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, compress=True)
    except RuntimeError:
        logger.exception("Failed to save marked blend file")

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

    if data.get("asset_data", {}).get("_mark_only"):
        mark_asset_only(data)
    else:
        unpack_asset(data)
    bpy.ops.wm.quit_blender()
    sys.exit(0)
