"""Background script to install/enable/disable a Blender extension (addon).

This runs in Blender background mode. It expects a JSON input file path as the
last CLI argument. It writes a results JSON file to the path provided in the
input under key "result_filepath".
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

import addon_utils  # type: ignore
import bpy

# Path injection so Blender can import our utils when running in background
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
if parent_path not in sys.path:
    sys.path.append(parent_path)

from blenderkit_server_utils import log  # noqa: E402

logger = log.create_logger(__name__)


def install_addon(zip_path: str) -> str:
    """Install an extension from a .zip path using the user_default repo.

    Args:
        zip_path: Absolute or Blender-resolved path to the zip file.

    Returns:
        Empty string on success, or an error message on failure.
    """
    logger.info("Installing %s", zip_path)
    try:
        bpy.ops.extensions.package_install_files(
            filepath=zip_path,
            repo="user_default",
            enable_on_install=False,
            overwrite=True,
        )
    except RuntimeError:
        logger.exception("Failed to install extension from %s", zip_path)
        return "Addon installation failed: RuntimeError"
    except Exception:  # Blender's API may raise various exceptions here
        logger.exception("Failed to install extension from %s", zip_path)
        trace = traceback.format_exc()
        return "Addon installation failed: traceback:\n" + trace
    return ""


def enable_addon(extension_id: str) -> str:
    """Enable an installed extension by its extension ID.

    Args:
        extension_id: The extension identifier (without the repo prefix).

    Returns:
        Empty string on success, or an error message on failure.
    """
    module_name = f"bl_ext.user_default.{extension_id}"
    logger.info("Enabling %s", module_name)
    try:
        module = addon_utils.enable(module_name, default_set=True, persistent=True, handle_error=None)
    except Exception:  # Blender's API may raise various exceptions here
        logger.exception("Failed to enable module %s", module_name)
        trace = traceback.format_exc()
        return "Addon enabling failed: traceback:\n" + trace
    if module is None:
        return "Addon enabling failed: None module returned"
    return ""


def disable_addon(extension_id: str) -> str:
    """Disable an enabled extension by its extension ID.

    Args:
        extension_id: The extension identifier (without the repo prefix).

    Returns:
        Empty string on success, or an error message on failure.
    """
    module_name = f"bl_ext.user_default.{extension_id}"
    logger.info("Disabling %s", module_name)
    try:
        addon_utils.disable(module_name, default_set=True, handle_error=None)
    except Exception:  # Blender's API may raise various exceptions here
        logger.exception("Failed to disable module %s", module_name)
        trace = traceback.format_exc()
        return "Addon disabling failed: traceback:\n" + trace
    return ""


def _load_input_json(path: str) -> dict[str, Any]:
    """Load and parse a JSON file with UTF-8 encoding."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data


def _write_output_json(path: str, payload: dict[str, Any]) -> None:
    """Write JSON results with UTF-8 encoding and pretty indent."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    logger.info(">>> Background addon test has started <<<")

    datafile = sys.argv[-1]
    try:
        data = _load_input_json(datafile)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read JSON input: %s", datafile)
        sys.exit(2)

    addon_name = data.get("asset_data", {}).get("name")
    zip_path = data.get("file_path")
    extension_id = data.get("asset_data", {}).get("dictParameters", {}).get("extensionId")

    if not zip_path or not extension_id:
        logger.error("Missing required input: zip_path or extension_id")
        sys.exit(2)

    logger.info("Testing addon %s (extid=%s), zip at: %s", addon_name, extension_id, zip_path)
    results: dict[str, str] = {}
    results["install"] = install_addon(zip_path)
    results["enabling"] = enable_addon(extension_id)
    results["disabling"] = disable_addon(extension_id)

    json_result_path = data.get("result_filepath")
    if not json_result_path:
        logger.error("Missing result_filepath in input JSON")
        sys.exit(2)

    try:
        _write_output_json(json_result_path, results)
    except OSError:
        logger.exception("Failed to write result JSON: %s", json_result_path)
        sys.exit(3)

    logger.info(">>> Background addon test has finished <<<")
    sys.exit(0)
