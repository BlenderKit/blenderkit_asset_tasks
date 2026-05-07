"""Background Blender script that generates a .prxc proxor file for an asset.

Pipeline:
1. ``send_to_bg.send_to_bg(..., script="prxc_bg_blender.py")`` opens the
   asset's .blend file (already unpacked by ``unpack_asset_bg.py``) and
   passes a JSON datafile via ``sys.argv[-1]``.
2. This script imports the vendored :mod:`bl_proxor` package living in
   the same ``blender_bg_scripts`` directory (no BlenderKit addon
   required) and runs ``generate_proxor_multi`` on every MESH object in
   the scene.
3. The resulting payload is written as a quantised .prxc file named
   ``<assetBaseId>.prxc`` into ``temp_folder`` (the directory that holds
   the JSON datafile, which is the same temp folder ``send_to_bg`` cleans
   up after upload).
4. A ``[{type, index, file_path}]`` descriptor is written to the
   ``result_filepath`` provided in the datafile so the calling Python
   process (``generate_proxors.py``) can hand it to
   ``upload.upload_resolutions``. The ``type`` is ``"prxc"`` to match
   what the BlenderKit Go client uploads (see ``client/main.go``).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import bpy  # type: ignore

# Add this directory to sys.path so the vendored ``bl_proxor`` package is
# importable, and the parent directory so ``blenderkit_server_utils``
# (used for shared logger config) is importable.
dir_path = os.path.dirname(os.path.realpath(__file__))
if dir_path not in sys.path:
    sys.path.insert(0, dir_path)
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import log  # noqa: E402, I001
from bl_proxor import generate as proxor_generate  # noqa: E402
from bl_proxor import prx_format as proxor_prx_format  # noqa: E402


logger = log.create_logger(__name__)


def _collect_mesh_objects() -> list[bpy.types.Object]:
    """Return all MESH-type objects from all scenes in the current .blend.

    We deliberately do not filter by collection or selection: an unpacked
    BlenderKit asset typically has every model object linked into the
    main scene already.
    """
    mesh_objects: list[bpy.types.Object] = []
    seen: set[int] = set()
    for scene in bpy.data.scenes:
        for obj in scene.objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            ptr = obj.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            mesh_objects.append(obj)
    return mesh_objects


def generate_prxc(data: dict[str, Any]) -> None:
    """Generate a .prxc proxor file for the asset and emit a result manifest.

    Args:
        data: Parsed JSON payload from ``send_to_bg.send_to_bg`` (the
            datafile passed via ``sys.argv[-1]``). Expected keys:
            ``asset_data``, ``temp_folder``, ``result_filepath``.

    Side effects:
        - Writes ``<temp_folder>/<assetBaseId>.prxc`` on success.
        - Writes ``result_filepath`` as a JSON list of upload descriptors.
        - Calls ``sys.exit`` with a non-zero code on hard failures so the
          parent process can detect them via the Blender return code.
    """
    asset_data = data.get("asset_data") or {}
    asset_base_id = asset_data.get("assetBaseId") or asset_data.get("asset_base_id")
    if not asset_base_id:
        logger.error("Missing assetBaseId in asset_data, cannot derive .prxc filename")
        sys.exit(11)

    temp_folder = data.get("temp_folder") or ""
    if not temp_folder or not os.path.isdir(temp_folder):
        logger.error("Missing or invalid temp_folder in datafile: %r", temp_folder)
        sys.exit(12)

    result_filepath = data.get("result_filepath") or ""
    if not result_filepath:
        logger.error("Missing result_filepath in datafile")
        sys.exit(13)

    mesh_objects = _collect_mesh_objects()
    if not mesh_objects:
        logger.error("No MESH objects found in the .blend; nothing to proxorise")
        sys.exit(20)

    logger.info("Generating proxor payload from %d mesh objects", len(mesh_objects))
    try:
        payload = proxor_generate.generate_proxor_multi(
            mesh_objects,
            include_normals=True,
        )
    except Exception:
        logger.exception("generate_proxor_multi raised")
        sys.exit(21)

    if payload is None:
        logger.error("generate_proxor_multi returned no data")
        sys.exit(22)

    prxc_path = os.path.join(temp_folder, f"{asset_base_id}.prxc")
    try:
        proxor_prx_format.write_prx(
            prxc_path,
            payload,
            name=mesh_objects[0].name,
            compress=True,
            include_mesh=proxor_generate.EXPORT_INCLUDE_MESH,
            include_lines=proxor_generate.EXPORT_INCLUDE_LINES,
            include_points=proxor_generate.EXPORT_INCLUDE_POINTS,
            include_colors=proxor_generate.EXPORT_INCLUDE_COLORS,
        )
    except Exception:
        logger.exception("write_prx failed")
        sys.exit(23)

    if not os.path.exists(prxc_path):
        logger.error("write_prx did not produce a file at %s", prxc_path)
        sys.exit(24)

    logger.info("Proxor written to %s (%d bytes)", prxc_path, os.path.getsize(prxc_path))

    # File descriptor list consumed by upload.upload_resolutions in the
    # parent process. The "prxc" type matches what the Go client uploads
    # for native asset uploads (see client/main.go: type "prxc").
    files = [{"type": "prxc", "index": 0, "file_path": prxc_path}]
    try:
        with open(result_filepath, "w", encoding="utf-8") as f:
            json.dump(files, f, ensure_ascii=False, indent=4)
    except OSError:
        logger.exception("Failed to write results JSON: %s", result_filepath)
        sys.exit(25)


if __name__ == "__main__":
    logger.info(">>> Background PRXC generator started <<<")
    datafile_path = sys.argv[-1]
    try:
        with open(datafile_path, encoding="utf-8") as f:
            input_data: dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input: %s", datafile_path)
        sys.exit(10)

    generate_prxc(input_data)
    logger.info(">>> Background PRXC generator finished <<<")
    bpy.ops.wm.quit_blender()
    sys.exit(0)
