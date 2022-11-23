import json
import os
import tempfile
import subprocess
import sys
import threading

from blenderkit_server_utils import download, search, paths, upload

results = []
page_size = 5


BLENDERS_PATH = '..\\blender_processors'

dir_path = os.path.dirname(os.path.realpath(__file__))
bg_scripts_path = os.path.join(dir_path, 'blender_bg_scripts')




def version_to_float(version):
  vars = version.split('.')
  version = int(vars[0]) + .01 * int(vars[1])
  if len(vars) > 2:
    version += .0001 * int(vars[2])
  return version


def get_blender_binary(asset_data):
  # pick the right blender version for asset processing
  blenders_path = os.path.join(os.path.dirname(__file__), BLENDERS_PATH)
  blenders = []
  for fn in os.listdir(blenders_path):
    blenders.append((version_to_float(fn), fn))
  asset_blender_version = version_to_float(asset_data['sourceAppVersion'])
  print(blenders)
  print(asset_blender_version)
  blender_target = min(blenders, key=lambda x: abs(x[0] - asset_blender_version))
  # use latest blender version for hdrs
  if asset_data['assetType'] == 'hdr':
    blender_target = blenders[-1]

  print(blender_target)
  binary = os.path.join(blenders_path, blender_target[1], 'blender.exe')
  print(binary)
  return binary


def send_to_bg(asset_data, file_path='', result_path='', api_key='', script=''):
  '''
  Send varioust task to a new blender instance that runs and closes after finishing the task.
  This function waits until the process finishes.
  The function tries to set the same bpy.app.debug_value in the instance of Blender that is run.
  Parameters
  ----------
  asset_data
  fpath - file that will be processed
  command - command which should be run in background.

  Returns
  -------
  None
  '''
  binary_path = get_blender_binary(asset_data)

  data = {
    'file_path': file_path,
    'result_filepath': result_path,
    'asset_data': asset_data,
    'api_key': api_key,
  }
  # binary_path = global_vars.PREFS['binary_path']
  tempdir = tempfile.mkdtemp()
  datafile = os.path.join(tempdir + 'resdata.json').replace('\\', '\\\\')
  script_path = os.path.dirname(os.path.realpath(__file__))
  with open(datafile, 'w', encoding='utf-8') as s:
    json.dump(data, s, ensure_ascii=False, indent=4)

  print('opening Blender instance to do processing - ', script)

  # exclude hdrs from reading as .blend
  if asset_data['assetType'] == 'hdr':
    fpath = 'empty.blend'

  proc = subprocess.run([
    binary_path,
    "--background",
    "--factory-startup",
    "-noaudio",
    file_path,
    "--python", os.path.join(bg_scripts_path, script),
    "--", datafile
  ], bufsize=1, stdout=sys.stdout, stdin=subprocess.PIPE, creationflags=get_process_flags())


def get_process_flags():
  """Get proper priority flags so background processess can run with lower priority."""

  ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
  BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
  HIGH_PRIORITY_CLASS = 0x00000080
  IDLE_PRIORITY_CLASS = 0x00000040
  NORMAL_PRIORITY_CLASS = 0x00000020
  REALTIME_PRIORITY_CLASS = 0x00000100

  flags = BELOW_NORMAL_PRIORITY_CLASS
  if sys.platform != 'win32':  # TODO test this on windows
    flags = 0

  return flags


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
  A thread that downloads file and only then starts an instance of Blender that generates the resolution
  Parameters
  ----------
  asset_data

  Returns
  -------

  '''

  destination_directory = tempfile.gettempdir()
  print(destination_directory)
  file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

  if file_path and asset_data['assetType'] != 'hdr':
    send_to_bg(asset_data, file_path=file_path, script='unpack_asset_bg.py')

  if not file_path:
    # fail message?
    return;

  tempdir = tempfile.mkdtemp()
  result_path = os.path.join(tempdir, asset_data['assetBaseId'] + '_resdata.json')


  if asset_data['assetType'] == 'hdr':
    file_path = 'empty.blend'
    send_to_bg(asset_data, file_path=file_path, result_path=result_path,
               script='resolutions_bg_blender_hdr.py')
  else:
    send_to_bg(asset_data, file_path=file_path, result_path=result_path,
               script= 'resolutions_bg_blender.py')
  with open(result_path, 'r', encoding='utf-8') as f:
    files = json.load(f)

  upload.upload_resolutions(files, asset_data, api_key=api_key)
  upload.patch_asset_empty(asset_data['id'], api_key=api_key)


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
  'asset_type': 'hdr',
  # # 'asset_type': 'material,model,hdr',
  # # 'author_id': 2995,  # monika
  # # 'author_id': 2,  # vilem
  # 'order': '-created',
  # 'verification_status': 'validated',
  # # 'textureResolutionMax_gte': '1024',
  # 'files_size_gte': '1024000',
  #
  # # 'last_resolution_upload_lte': '2021-01-01'
  # 'last_resolution_upload': '0001-01-01'
}
# +last_resolution_upload:0001-01-01+files_size_gte:5000000
return_assets = 1

# https://devel.blenderkit.com/api/v1/search/?query=last_resolution_upload_gt:2020-09-01
search.get_search_simple(params, filepath, page_size=min(return_assets, 100), max_results=return_assets, api_key=paths.API_KEY)

filters = [
  ##            '75mm Power',
  ##            'Noodle Cup',
  #            'wooden chair',
  'poolside',
]
# filter_assets(filepath, filepath_filtered, filters)

# skip all failed cases.
# assets_from_last_generated(filepath, filepath_filtered, filters)
#    

assets = search.load_assets_list(filepath)
for i, a in enumerate(assets):
  print(a['name'], a['assetType'])

iterate_for_resolutions(filepath, process_count=1, api_key=paths.API_KEY, do_checks = False)
