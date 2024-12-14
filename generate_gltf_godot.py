"""Generate GLTF file for Godot. This is an optimized version for Godot engine (no Draco compression enabled)."""


import json
import os
import tempfile
from datetime import datetime

from blenderkit_server_utils import download, search, upload, send_to_bg

results = []
page_size = 100


def generate_gltf(asset_data, api_key, binary_path: str) -> bool:
  '''
  A thread that:
   1.downloads file
   2.starts an instance of Blender that generates the GLTF file
   3.uploads GLTF file
   4.patches asset data with a new parameter.

  Parameters
  ----------
  asset_data

  Returns
  -------
  '''
  error = ""
  destination_directory = tempfile.gettempdir()

  # Download asset
  asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory, resolution='2k')

  # Unpack asset
  send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path, script='unpack_asset_bg.py', binary_path=binary_path)

  if not asset_file_path:
    print(f"Asset file not found on path {asset_file_path}")
    # fail message?
    return False

  # Send to background to generate GLTF
  temp_folder = tempfile.mkdtemp()
  result_path = os.path.join(temp_folder, asset_data['assetBaseId'] + '_resdata.json')

  send_to_bg.send_to_bg(
    asset_data,
    asset_file_path=asset_file_path,
    result_path=result_path,
    script='gltf_bg_blender.py',
    binary_path=binary_path,
    target_format="gltf_godot"
    )

  files = None
  try:
    with open(result_path, 'r', encoding='utf-8') as f:
      files = json.load(f)
  except Exception as e:
    print(f"---> Error reading result JSON {result_path}: {e}")
    error += f" {e}"

  if files == None:
    error += " Files are None"
  elif len(files) == 0:
    error += f" len(files)={len(files)}"
  else:
    # there are no actual resolutions
    print("Files are:", files)
    upload.upload_resolutions(files, asset_data, api_key=api_key)
    today = datetime.today().strftime('%Y-%m-%d')
    param = 'gltfGodotGeneratedDate'
    upload.patch_individual_parameter(asset_data['id'], param_name=param, param_value=today, api_key=api_key)
    upload.get_individual_parameter(asset_data['id'], param_name=param, api_key=api_key)
    print(f"---> Asset parameter {param} successfully patched with value {today}")
    # TODO: Remove gltfGodotGeneratedError if it was filled by previous runs
    return True
  
  print('---> GLTF generation failed')
  param = "gltfGodotGeneratedError"
  value = error.strip()
  upload.patch_individual_parameter(asset_data['id'], param_name=param, param_value=value, api_key=api_key)
  upload.get_individual_parameter(asset_data['id'], param_name=param, api_key=api_key)
  print(f'--> Asset parameter {param} patched with value {value} to signal GLTF generation FAILURE')

  return False



def iterate_assets(assets: list, api_key: str='', binary_path:str=''):
  for i, asset_data in enumerate(assets):
    print(f"\n\n=== {i+1} downloading and generating GLTF files for {asset_data['name']}")
    if asset_data is None:
      print("---> skipping, asset_data are None")
      continue
    ok = generate_gltf(asset_data, api_key, binary_path=binary_path)
    if ok:
      print("===> GLTF GODOT SUCCESS")
    else:
      print("===> GLTF GODOT FAILED")


def main():
  BLENDER_PATH = os.environ.get('BLENDER_PATH','')
  API_KEY = os.environ.get('BLENDERKIT_API_KEY', '')
  ASSET_BASE_ID = os.environ.get('ASSET_BASE_ID')
  MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))
  
  if ASSET_BASE_ID is not None: # Single asset handling - for asset validation hook
    params = {
      'asset_base_id': ASSET_BASE_ID,
      'asset_type': 'model',
      }
  else: # None asset specified - will run on 100 unprocessed assets - for nightly jobs
    params = {
      'asset_type': 'model',
      'order': '-created',
      'verification_status': 'validated',
      'gltfGeneratedDate_isnull': True, # Assets which does not have generated GLTF
      'gltfGeneratedError_isnull': True, # Assets which does not have error from previously failed GLTF generation
      }

  assets = search.get_search_without_bullshit(params, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS, api_key=API_KEY)
  print(f"--- Found {len(assets)} for GLTF conversion: ---")
  for i, asset in enumerate(assets):
    print(f"{i+1} {asset['name']} ||| {asset['assetType']}")

  iterate_assets(assets, api_key=API_KEY, binary_path=BLENDER_PATH)

if __name__ == '__main__':
  main()
