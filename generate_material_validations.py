# -----------------------------------------------------------------------------------
# generate material validation scene for all uploaded assets
# -------------------------------------------- ---------------------------------------

import json
import os
import tempfile
import threading
import time
import pathlib

from blenderkit_server_utils import download, search, paths, upload, send_to_bg

results = []
page_size = 100

MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))
MATERIAL_VALIDATION_FOLDER = 'G:\\Shared drives\\Validations\\material_validation\\'

def render_material_validation_thread(asset_data, api_key):
  '''
  A thread that:
   1.downloads file
   2.starts an instance of Blender that renders the validation
   3.uploads files that were prepared
   4.patches asset data with a new parameter.

  Parameters
  ----------
  asset_data

  Returns
  -------

  '''

  destination_directory = tempfile.gettempdir()

  # Download asset
  file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

  # find template file
  current_dir = pathlib.Path(__file__).parent.resolve()
  template_file_path = os.path.join(current_dir, 'blend_files', 'material_validator_mix.blend')

  # Send to background to generate resolutions
  tempdir = tempfile.mkdtemp()
  # result_path =  os.path.join(tempdir, asset_data['assetBaseId'] + '_resdata.json')
  upload_id = asset_data['files'][0]['downloadUrl'].split('/')[-2]

  result_path = f"{MATERIAL_VALIDATION_FOLDER}{asset_data['author']['firstName']}_{asset_data['author']['lastName']}/{upload_id}_{asset_data['name']}_{asset_data['author']['firstName']}_{asset_data['author']['lastName']}"

  send_to_bg.send_to_bg(asset_data,
                        asset_file_path=file_path,
                        template_file_path=template_file_path,
                        result_path=result_path,
                        script='material_validation_bg.py',
                        binary_type = 'NEWEST')

  # TODO add writing of the parameter, we'll skip it by now.
  # upload.patch_asset_empty(asset_data['id'], api_key)
  return

  files = None
  try:
    with open(result_path, 'r', encoding='utf-8') as f:
      files = json.load(f)
  except Exception as e:
    print(e)

  
  print('changing asset variable')
  resgen_param = {'resolutionsGenerated': result_state}
  upload.patch_individual_parameter(asset_data, parameter=resgen_param, api_key=api_key)
  os.remove(file_path)


def iterate_assets(filepath, thread_function = None, process_count=12, api_key=''):
  ''' iterate through all assigned assets, check for those which need generation and send them to res gen'''
  assets = search.load_assets_list(filepath)
  threads = []
  for asset_data in assets:
    if asset_data is not None:
      print('downloading and generating resolution for  %s' % asset_data['name'])
      thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
      thread.start()
      threads.append(thread)
      while len(threads) > process_count - 1:
        for t in threads:
          if not t.is_alive():
            threads.remove(t)
          break;
        time.sleep(0.1) # wait for a bit to finish all threads


def main():
  dpath = tempfile.gettempdir()
  filepath = os.path.join(dpath, 'assets_for_resolutions.json')
  params = {
      'order': '-created',
      'asset_type': 'material',
      'verification_status': 'uploaded'
  }
  search.get_search_simple(params, filepath=filepath,  page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)

  search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)

  assets = search.load_assets_list(filepath)
  print('ASSETS TO BE PROCESSED')
  for i, a in enumerate(assets):
    print(a['name'], a['assetType'])

  iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_material_validation_thread)

if __name__ == '__main__':
  main()