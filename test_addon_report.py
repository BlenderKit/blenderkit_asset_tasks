"""Generate and post a comment with add-on test results to BlenderKit.com.

Results are expected under directories like: ``temp/blender-{x.y}/test_addon_results.json``.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from os import environ
from pathlib import Path
from typing import Any

from blenderkit_server_utils import api_nice

logger = logging.getLogger(__name__)

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def read_result_files() -> OrderedDict[str, dict[str, Any]]:
    """Read all result JSON files from the temp folder.

    Returns:
        OrderedDict mapping Blender release name to its result dictionary.
    """
    temp = Path("temp")
    if not temp.exists() or not temp.is_dir():
        raise FileNotFoundError("temp directory with test results not found")

    results: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for entry in temp.iterdir():
        if entry.is_file():
            continue

        # Each subdir should contain a single results json; pick the first *.json
        for file in entry.iterdir():
            if file.suffix.lower() != ".json":
                continue
            try:
                json_data: dict[str, Any] = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.exception("Failed to read/parse results file: %s", file)
                continue
            results[entry.name] = json_data
            break

    return results


def generate_comment(results: OrderedDict[str, dict[str, Any]]) -> str:
    """Generate the comment text from the results dictionary.

    Args:
        results (OrderedDict[str, dict]): Dictionary with test results.

    Returns:
        str: Generated comment text.
    """
    if len(results) == 0:
        raise ValueError("Results are expected to be not empty")
    comment = "We have automatically tested your add-on. Below are the results:"
    all_ok = True
    for rkey, release in results.items():
        release_ok = True
        message = ""
        for tkey, test in release.items():
            if test == "":  # empty error -> test OK
                continue
            release_ok = False
            all_ok = False
            message += f"\n- test '{tkey}' failed: {test}"
        if release_ok:  # noqa: SIM108
            message = "OK"
        else:
            message = f"FAIL{message}"
        comment += f"\n***\n**{rkey}**: {message}"

    if not all_ok:
        comment += "\n***\nSome tests has failed. Please check your add-on in the failed versions of Blender. It is possible there is a problem."  # noqa: E501

    return comment


def main() -> None:
    """Read results, generate the comment, and post it to BlenderKit.com."""
    results = read_result_files()
    comment = generate_comment(results)
    logger.info("Comment generated:\n%s", comment)

    api_nice.create_comment(
        comment=comment,
        asset_base_id=environ.get("ASSET_BASE_ID", ""),
        # prefer KEY for account of specialized commenting bot
        api_key=environ.get("TEXTYBOT_API_KEY", environ.get("BLENDERKIT_API_KEY", "")),
        server_url=environ.get("BLENDERKIT_SERVER", ""),
    )
    logger.info("Comment uploaded")


if __name__ == "__main__":
    main()
