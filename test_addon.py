"""Smoke test a single add-on extension.

Note:
- Potential future improvement: run `blender --command extension validate`.
- Figure out how to propagate success/failure to an aggregating workflow that posts a comment.

Required environment variables:
- BLENDERKIT_API_KEY: API key used for downloading the asset.
- ASSET_BASE_ID: Base ID of the add-on asset to test.
- BLENDER_PATH: Path to the Blender binary to run in background.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from blenderkit_server_utils import config, download, log, search, send_to_bg, utils

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY", "ASSET_BASE_ID", "BLENDER_PATH"])


def test_addon(addon_data: dict[str, Any], api_key: str, binary_path: str) -> tuple[bool, dict[str, str]]:
    """Test the add-on using background Blender steps defined in ``test_addon_bg.py``.

    Args:
        addon_data: Asset data of the add-on to be tested.
        api_key: API key used for downloading the asset.
        binary_path: Path to Blender binary to run in background.

    Returns:
        A tuple: (all_tests_ok, results_dict). The results dict maps test names to error messages ("" for OK).
    """
    addon_file_path = download.download_asset(
        addon_data,
        api_key=api_key,
        directory=tempfile.gettempdir(),
        filetype="zip_file",
    )
    if not addon_file_path:
        logger.error("Asset file not found or download failed for %s", addon_data.get("name"))
        # no fail message - we do not want to spam this to users in comment
        return False, {}

    temp_folder = tempfile.mkdtemp()
    result_path = os.path.join(temp_folder, f"{addon_data['assetBaseId']}_resdata.json")

    template_path = Path(__file__).parent / "blend_files" / "empty.blend"
    send_to_bg.send_to_bg(
        addon_data,
        asset_file_path=addon_file_path,  # we do not open any project file
        template_file_path=str(template_path),
        result_path=result_path,
        script="test_addon_bg.py",
        binary_path=binary_path,
    )

    try:
        with open(result_path, encoding="utf-8") as f:
            bg_results: dict[str, str] = json.load(f)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        logger.exception("Error reading result JSON %s", result_path)
        # just fail it, when JSON is not present, it means something went wrong
        return False, {}

    tests_ok = True
    for value in bg_results.values():
        if value != "":  # empty error string indicates success
            tests_ok = False

    return tests_ok, bg_results


def blender_validate_extension() -> None:
    """Validate the extension using Blender's built-in command.

    empty for now, not implemented yet.
    """
    pass  # noqa: PIE790


def main() -> None:
    """Run the add-on smoke test and emit a JSON result file for CI consumption."""
    params = {"asset_base_id": config.ASSET_BASE_ID, "asset_type": "addon"}
    addons = search.get_search_paginated(params, api_key=config.BLENDERKIT_API_KEY)
    if len(addons) == 0:
        raise RuntimeError("Addon not found in the database")

    # One result is expected, but we log all found for transparency
    for i, asset in enumerate(addons, start=1):
        logger.info("%s. %s: %s (%s)", i, asset.get("assetType"), asset.get("name"), asset.get("url"))

    # We just take 1st result
    test_ok, test_results = test_addon(addons[0], config.BLENDERKIT_API_KEY, binary_path=config.BLENDER_PATH)

    output_file = Path("temp/test_addon_results.json")
    output_file.parent.mkdir(exist_ok=True)
    output_file.write_text(json.dumps(test_results), encoding="utf-8")
    sys.exit(0 if test_ok else 1)


if __name__ == "__main__":
    main()
