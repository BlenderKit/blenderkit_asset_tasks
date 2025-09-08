"""Script to generate comment from the test results and post the comment under the add-on on BlenderKit.com.

Results are expected to be at: temp/blender-{x.y}/test_addon_results.json
"""

import json
from collections import OrderedDict
from os import environ
from pathlib import Path

from blenderkit_server_utils import api_nice


def read_result_files() -> OrderedDict[str, dict]:
    """Read all result JSON files from temp folder.

    Returns:
        OrderedDict[str, dict]: Dictionary with test results.
    """
    temp = Path("temp")
    results = OrderedDict()
    for entry in temp.iterdir():
        if entry.is_file():
            continue

        for file in entry.iterdir():
            json_data = json.loads(file.read_text())
            results[entry.name] = json_data

    return results


def generate_comment(results: OrderedDict[str, dict]) -> str:
    """Generate the comment text from the results dictionary.

    Args:
        results (OrderedDict[str, dict]): Dictionary with test results.

    Returns:
        str: Generated comment text.
    """
    if len(results) == 0:
        raise Exception("Results are expected to be not empty")  # noqa: TRY002
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


results = read_result_files()
comment = generate_comment(results)
print(f"comment generated:\n{comment}")

api_nice.create_comment(
    comment=comment,
    asset_base_id=environ.get('ASSET_BASE_ID', ''),
    # prefer KEY for account of specialized commenting bot
    api_key=environ.get('TEXTYBOT_API_KEY',
                        environ.get('BLENDERKIT_API_KEY', '')),
    server_url=environ.get('BLENDERKIT_SERVER', ''),
)
print("comment uploaded")
