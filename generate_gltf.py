import json
import os
import tempfile
import time
import threading
from datetime import datetime

from blenderkit_server_utils import download, search, paths, upload, send_to_bg

results = []
page_size = 100


def generate_gltf_thread(asset_data, api_key):
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

  destination_directory = tempfile.gettempdir()

  # Download asset
  asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory, resolution='2k')

  # Unpack asset
  send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path, script='unpack_asset_bg.py')

  if not asset_file_path:
    # fail message?
    return;

  # Send to background to generate GLTF
  tempdir = tempfile.mkdtemp()
  result_path = os.path.join(tempdir, asset_data['assetBaseId'] + '_resdata.json')

  send_to_bg.send_to_bg(asset_data, asset_file_path=asset_file_path,
                          result_path=result_path,
                          script='gltf_bg_blender.py')

  files = None
  try:
    with open(result_path, 'r', encoding='utf-8') as f:
      files = json.load(f)
  except Exception as e:
    print(e)

  if files == None:
    # this means error
    result_state = 'error'
  elif len(files) > 0:
    # there are no actual resolutions
    upload.upload_resolutions(files, asset_data, api_key=api_key)
    result_state = 'success'
  else:
    # zero files, consider skipped
    result_state = 'skipped'
  print('changing asset variable')

  today = datetime.today().strftime('%Y-%m-%d')

  # patch the parameter to know when this was generated

  upload.patch_individual_parameter(asset_data['id'], param_name='gltfGeneratedDate', param_value=today, api_key=api_key)
  upload.get_individual_parameter(asset_data['id'], param_name='gltfGeneratedDate', api_key=api_key)
  # upload.patch_asset_empty(asset_data['id'], api_key)
  return



def iterate_assets(filepath, thread_function = None, process_count=12, api_key=''):
  ''' iterate through all assigned assets, check for those which need generation and send them to res gen'''
  assets = search.load_assets_list(filepath)
  threads = []
  for asset_data in assets:
    if asset_data is not None:
      print('downloading and generating GLTF files for  %s' % asset_data['name'])
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
  # search for assets if assets were not provided with these parameters

  params = {
    'asset_type': 'model',
    'order': '-created',
    'verification_status': 'validated',
    'gltfGeneratedDate_isnull': True
  }

  MAX_ASSETS = int(os.environ.get('BLENDERKIT_RESOLUTION_MAX_ASSET_COUNT', '100'))

  assets = search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)

  print('ASSETS TO BE PROCESSED')
  for i, a in enumerate(assets):
    print(a['name'], a['assetType'])
  # return
  iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=generate_gltf_thread)

if __name__ == '__main__':
  main()