import json
import os
import tempfile
import subprocess
import sys
import threading

from blenderkit_server_utils import download, search, paths, upload, send_to_bg

results = []
page_size = 100

BLENDERKIT_RESOLUTIONS_SEARCH_ID = os.environ.get('BLENDERKIT_RESOLUTIONS_SEARCH_ID', None)
BLENDERKIT_CHECK_NEEDS_RESOLUTION = os.environ.get('BLENDERKIT_CHECK_NEEDS_RESOLUTION', '1').lower() in (
  'true', '1', 't')
MAX_ASSETS = int(os.environ.get('BLENDERKIT_RESOLUTION_MAX_ASSET_COUNT', '100'))


def check_needs_resolutions(a):
  if a['assetType'] in ('material', 'model', 'scene', 'hdr'):  # a['verificationStatus'] == 'validated' and
    # the search itself now picks the right assets so there's no need to filter more than asset types.
    # TODO needs to check first if the upload date is older than resolution upload date, for that we need resolution upload date.
    for f in a['files']:
      if f['fileType'].find('resolution') > -1:
        print(f"{a['name']} already has resolutions")
        return False

    return True
  return False


def generate_resolution_thread(asset_data, api_key):
  '''
  A thread that:
   1.downloads file
   2.starts an instance of Blender that generates the resolutions
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

  # Unpack asset
  if file_path and asset_data['assetType'] != 'hdr':
    send_to_bg.send_to_bg(asset_data, file_path=file_path, script='unpack_asset_bg.py')

  if not file_path:
    # fail message?
    return;

  # Send to background to generate resolutions
  tempdir = tempfile.mkdtemp()
  result_path = os.path.join(tempdir, asset_data['assetBaseId'] + '_resdata.json')

  if asset_data['assetType'] == 'hdr':
    # file_path = 'empty.blend'
    send_to_bg.send_to_bg(asset_data, file_path=file_path, result_path=result_path,
                          script='resolutions_bg_blender_hdr.py')
  else:
    send_to_bg.send_to_bg(asset_data, file_path=file_path, result_path=result_path,
                          script='resolutions_bg_blender.py')

  # TODO add writing of the parameter, we'll skip it by now.
  upload.patch_asset_empty(asset_data['id'], api_key)
  return

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
  resgen_param = {'resolutionsGenerated': result_state}
  upload.patch_individual_parameter(asset_data, parameter=resgen_param, api_key=api_key)


def iterate_for_resolutions(filepath, process_count=12, api_key='', do_checks=True):
  ''' iterate through all assigned assets, check for those which need generation and send them to res gen'''
  assets = search.load_assets_list(filepath)
  print(len(assets))
  threads = []
  for asset_data in assets:
    if asset_data is not None:

      if not do_checks or check_needs_resolutions(asset_data):
        print('downloading and generating resolution for  %s' % asset_data['name'])
        # this is just a quick hack for not using original dirs in blendrkit...
        # generate_resolution_thread(asset_data, api_key)
        thread = threading.Thread(target=generate_resolution_thread, args=(asset_data, api_key))
        thread.start()
        threads.append(thread)
        while len(threads) > process_count - 1:
          for t in threads:
            if not t.is_alive():
              threads.remove(t)
            break;
      else:
        print('not generated resolutions:', asset_data['name'])


dpath = tempfile.gettempdir()
filepath = os.path.join(dpath, 'assets_for_resolutions.json')
filepath_filtered = os.path.join(dpath, 'assets_for_resolutions_filtered.json')
params = {
  # 'asset_type': 'hdr',
  'asset_type': 'material,model,hdr',
  'order': '-created',
  'verification_status': 'validated',
  # # 'textureResolutionMax_gte': '1024',
  'files_size_gte': '1024000',
  #
  # # 'last_resolution_upload_lte': '2021-01-01'
  'last_resolution_upload': '0001-01-01'
}

if BLENDERKIT_RESOLUTIONS_SEARCH_ID is not None:
  params = {'asset_base_id': BLENDERKIT_RESOLUTIONS_SEARCH_ID, }
# +last_resolution_upload:0001-01-01+files_size_gte:5000000
print(params)

# https://devel.blenderkit.com/api/v1/search/?query=last_resolution_upload_gt:2020-09-01
search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                         api_key=paths.API_KEY)

# optionally filter assets after search
# filters = [
#   'poolside',
# ]
# filter_assets(filepath, filepath_filtered, filters)

# skip all failed cases.
# assets_from_last_generated(filepath, filepath_filtered, filters)
#    

assets = search.load_assets_list(filepath)
print('ASSETS TO BE PROCESSED')
for i, a in enumerate(assets):
  print(a['name'], a['assetType'])

iterate_for_resolutions(filepath, process_count=1, api_key=paths.API_KEY, do_checks=BLENDERKIT_CHECK_NEEDS_RESOLUTION)
