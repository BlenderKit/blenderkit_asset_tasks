import json
import os
import tempfile
import sys

from blenderkit_server_utils import download, search, upload, send_to_bg

results = []
page_size = 100


def test_addon(addon_data, api_key, binary_path: str) -> bool:
  error = ""
  destination_directory = tempfile.gettempdir()

  # Download addon
  addon_file_path = download.download_asset(addon_data, api_key=api_key, directory=destination_directory, filetype='zip_file')

  if not addon_file_path:
    print(f"Asset file not found on path {addon_file_path}")
    return False # fail message?

  # Send to background to generate GLTF
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

def iterate_addons(addons: list, api_key: str='', binary_path:str='') -> bool:
  all_ok = True
  for i, addon_data in enumerate(addons):
    print(f"\n\n=== {i+1} downloading and generating GLTF files for {addon_data['name']} ===")
    if addon_data is None:
      print("---> skipping, asset_data are None")
      continue
    ok = test_addon(addon_data, api_key, binary_path=binary_path)
    if ok:
      print("===> TEST SUCCESS")
    else:
      print("===> TEST FAILED")
      all_ok = False
  return all_ok


if __name__ == '__main__':
  BLENDER_PATH = os.environ.get('BLENDER_PATH','')
  API_KEY = os.environ.get('BLENDERKIT_API_KEY', '')
  ADDON_BASE_ID = os.environ.get('ADDON_BASE_ID')
  
  params = {
    'asset_base_id': ADDON_BASE_ID,
    'asset_type': 'addon',
  }

  addons = search.get_search_without_bullshit(params, api_key=API_KEY)
  print(f"--- Found {len(addons)} addon for testing: ---")
  for i, asset in enumerate(addons):
    print(f"{i+1}. {asset['assetType']}: {asset['name']}")

  all_ok = iterate_addons(addons, api_key=API_KEY, binary_path=BLENDER_PATH)
  if not all_ok:
    sys.exit(1)
