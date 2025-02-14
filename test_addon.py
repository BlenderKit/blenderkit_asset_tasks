"""Script to smoke test single add-on extension.
TODO: add running command `blender --command extension validate`
TODO: figure out, how to pass the success/error message outside to aggregating Github workflow which will then comment on the asset.
"""

import json
import os
import tempfile
import sys
import json
from pathlib import Path

from blenderkit_server_utils import download, search, send_to_bg

page_size = 100


def test_addon(addon_data, api_key, binary_path: str) -> bool:
  error = ""
  destination_directory = tempfile.gettempdir()

  addon_file_path = download.download_asset(addon_data, api_key=api_key, directory=destination_directory, filetype='zip_file')
  if not addon_file_path:
    print(f"Asset file not found on path {addon_file_path}")
    return False # fail message?

  temp_folder = tempfile.mkdtemp()
  result_path = os.path.join(temp_folder, addon_data['assetBaseId'] + '_resdata.json')

  send_to_bg.send_to_bg(
    addon_data,
    asset_file_path=addon_file_path, # we do not open any project file
    template_file_path="empty.blend",
    result_path=result_path,
    script='test_addon_bg.py',
    binary_path=binary_path,
  )

  try:
    with open(result_path, 'r', encoding='utf-8') as f:
      bg_results = json.load(f)
  except Exception as e:
    print(f"---> Error reading result JSON {result_path}: {e}")
    error += f" {e}"

  test_ok = True
  for key in bg_results:
    if bg_results[key] != "": #empty error string
      test_ok = False

  return test_ok

def blender_validate_extension():
  pass


if __name__ == '__main__':
  BLENDER_PATH = os.environ.get('BLENDER_PATH','')
  API_KEY = os.environ.get('BLENDERKIT_API_KEY', '')
  ADDON_BASE_ID = os.environ.get('ADDON_BASE_ID', '')
  
  params = {'asset_base_id': ADDON_BASE_ID, 'asset_type': 'addon'}
  addons = search.get_search_without_bullshit(params, api_key=API_KEY)
  if len(addons) == 0:
    raise Exception("Addon not found in the database")

  for i, asset in enumerate(addons): # One result is expected, but for transparency..
    print(f"{i+1}. {asset['assetType']}: {asset['name']}")

  # We just take 1st result
  ok = test_addon(addons[0], API_KEY, binary_path=BLENDER_PATH)

  output_file = Path("temp/test_addon_results.json")
  output_file.parent.mkdir(exist_ok=True)
  if ok:
    results = {"passed": True, "messages": ""}
    output_file.write_text(json.dumps(results))
    sys.exit(0)
  else: # Signal job failure
    results = {"passed": False, "messages": "Something went wrong"}
    output_file.write_text(json.dumps(results))
    sys.exit(1)
