# -----------------------------------------------------------------------------------
# generate material validation scene for all uploaded assets
# -------------------------------------------- ---------------------------------------

import json
import os
import tempfile
import threading
import time
import pathlib

from blenderkit_server_utils import download, search, paths, upload, send_to_bg, google_drive

results = []
page_size = 100

MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))
MATERIAL_VALIDATION_FOLDER_ID = "1L10ngR6vkTjmlzy9CQa2D08slhigBpwe" #changed it to be the same as models now
GOOGLE_SHARED_DRIVE_ID = "0ABpmYJ3IosxhUk9PVA"
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

  upload_id = asset_data['files'][0]['downloadUrl'].split('/')[-2]

  # Check if the asset has already been processed
  # stop using author folder
  # Check if the asset has already been processed
  result_file_name = f"{upload_id}.jpg"

  drive = google_drive.init_drive()

  # check if the directory exists on the drive
  # we check file by file, since the comparison with folder contents is not reliable and would potentially
  # compare with a very long list. main issue was what to set the page size for the search request...
  f_exists = google_drive.file_exists(drive, upload_id, folder_id=MATERIAL_VALIDATION_FOLDER_ID)
  if f_exists:
      print('file exists, skipping')
      return

  # Download asset
  file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

  # find template file
  current_dir = pathlib.Path(__file__).parent.resolve()
  template_file_path = os.path.join(current_dir, 'blend_files', 'material_validator_mix.blend')

  # Send to background to generate resolutions
  temp_folder = tempfile.mkdtemp()

  # result folder where the stuff for upload to drive goes
  result_folder = os.path.join(temp_folder, upload_id)
  os.makedirs(result_folder, exist_ok=True)

  # local file path of main rendered image
  result_path = os.path.join(temp_folder,
                             result_folder,
                             result_file_name)

  # send to background to render
  send_to_bg.send_to_bg(asset_data,
                        asset_file_path=file_path,
                        template_file_path=template_file_path,
                        result_path=result_path,
                        result_folder=result_folder,
                        temp_folder=temp_folder,
                        script='material_validation_bg.py',
                        binary_type = 'NEWEST',
                        verbosity_level=2)

  # send to background to render turnarounds
  template_file_path = os.path.join(current_dir, 'blend_files', 'material_turnaround.blend')

  result_path = os.path.join(temp_folder,
                             result_folder,
                             upload_id + '_turnaround.mkv')
  send_to_bg.send_to_bg(asset_data,
                        asset_file_path=file_path,
                        template_file_path=template_file_path,
                        result_path=result_path,
                        result_folder=result_folder,
                        temp_folder=temp_folder,
                        script='material_turnaround_bg.py',
                        binary_type = 'NEWEST',
                        verbosity_level=2)

  # Upload result
  drive = google_drive.init_drive()
  google_drive.upload_folder_to_drive(drive, result_folder, MATERIAL_VALIDATION_FOLDER_ID, GOOGLE_SHARED_DRIVE_ID)
  return



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
      'order': 'created',
      'asset_type': 'material',
      'verification_status': 'uploaded'
  }
  search.get_search_simple(params, filepath=filepath,  page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)


  assets = search.load_assets_list(filepath)
  print('ASSETS TO BE PROCESSED')
  for i, a in enumerate(assets):
    print(a['name'], a['assetType'])

  iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_material_validation_thread)

if __name__ == '__main__':
  main()