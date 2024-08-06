import os
import requests

from . import utils
from . import paths

SCENE_UUID = '5d22a2ce-7d4e-4500-9b1a-e5e79f8732c0'



def server_2_local_filename(asset_data, filename):
  """Convert file name on server to file name local. This should get replaced."""

  fn = filename.replace('blend_', '')
  fn = fn.replace('resolution_', '')
  n = paths.slugify(asset_data['name']) + '_' + fn
  return n


def files_size_to_text(size):
  fsmb = size / (1024 * 1024)
  fskb = size % 1024
  if fsmb == 0:
    return f'{round(fskb)}KB'
  else:
    return f'{round(fsmb, 1)}MB'


def get_core_file(asset_data, resolution, find_closest_with_url=False):
  '''
  Returns core blend file.
  '''
  for f in asset_data['files']:
    if f['fileType'] == 'blend':
      orig = f
      return orig, 'blend'


def get_download_url(asset_data, scene_id, api_key, tcom=None, resolution='blend'):
  ''''retrieves the download url. The server checks if user can download the item.'''
  print('getting download url')

  headers = utils.get_headers(api_key)

  data = {
    'scene_uuid': scene_id
  }
  r = None

  res_file_info, resolution = get_core_file(asset_data, resolution)
  print(res_file_info)
  try:
    r = requests.get(res_file_info['downloadUrl'], params=data, headers=headers)
  except Exception as e:
    print(e)
    if tcom is not None:
      tcom.error = True
  if r == None:
    if tcom is not None:
      tcom.report = 'Connection Error'
      tcom.error = True
    return 'Connection Error'
  print(r.status_code, r.text)

  if r.status_code < 400:
    data = r.json()
    url = data['filePath']

    res_file_info['url'] = url
    res_file_info['file_name'] = paths.extract_filename_from_url(url)

    # print(res_file_info, url)
    print("URL:", url)
    return True




def get_download_filepath(asset_data, resolution='blend', can_return_others=False, directory=None):
  '''Get all possible paths of the asset and resolution. Usually global and local directory.'''
  windows_path_limit = 250
  if directory is None:
    directory = paths.get_download_dir(asset_data['assetType'])

  res_file, resolution = get_core_file(asset_data, resolution, find_closest_with_url=can_return_others)
  name_slug = paths.slugify(asset_data['name'])
  if len(name_slug) > 16:
    name_slug = name_slug[:16]
  asset_folder_name = f"{name_slug}_{asset_data['id']}"

  file_names = []

  if not res_file:
    return file_names
  if res_file.get('url') is not None:
    # Tweak the names a bit:
    # remove resolution and blend words in names
    #
    fn = paths.extract_filename_from_url(res_file['url'])
    n = server_2_local_filename(asset_data, fn)

    asset_folder_path = os.path.join(directory, asset_folder_name)

    if not os.path.exists(asset_folder_path):
      os.makedirs(asset_folder_path)

    file_name = os.path.join(asset_folder_path, n)
    file_names.append(file_name)

  print('file paths', file_names)

  return file_names


def check_existing(asset_data, resolution='blend', can_return_others=False, directory=None):
  ''' check if the object exists on the hard drive'''
  fexists = False

  if asset_data.get('files') == None:
    # this is because of some very odl files where asset data had no files structure.
    return False

  file_names = get_download_filepath(asset_data, resolution, can_return_others=can_return_others, directory=directory)

  print('check if file already exists' + str(file_names))
  if len(file_names) == 2:
    # TODO this should check also for failed or running downloads.
    # If download is running, assign just the running thread. if download isn't running but the file is wrong size,
    #  delete file and restart download (or continue downoad? if possible.)
    if os.path.isfile(file_names[0]):  # and not os.path.isfile(file_names[1])
      utils.copy_asset(file_names[0], file_names[1])
    elif not os.path.isfile(file_names[0]) and os.path.isfile(
            file_names[1]):  # only in case of changed settings or deleted/moved global dict.
      utils.copy_asset(file_names[1], file_names[0])

  if len(file_names) > 0 and os.path.isfile(file_names[0]):
    fexists = True
  return fexists


def delete_unfinished_file(file_name):
  '''
  Deletes download if it wasn't finished. If the folder it's containing is empty, it also removes the directory
  Parameters
  ----------
  file_name

  Returns
  -------
  None
  '''
  try:
    os.remove(file_name)
  except Exception as e:
    print(f'{e}')
  asset_dir = os.path.dirname(file_name)
  if len(os.listdir(asset_dir)) == 0:
    os.rmdir(asset_dir)
  return


def download_asset_file(asset_data, resolution='blend', api_key='', directory=None):
  # this is a simple non-threaded way to download files for background resolution genenration tool
  file_names = get_download_filepath(asset_data, resolution, directory=directory)  # prefer global dir if possible.
  if len(file_names) == 0:
    return None

  file_name = file_names[0]

  if check_existing(asset_data, resolution=resolution, directory=directory):
    # this sends the thread for processing, where another check should occur, since the file might be corrupted.
    # print('not downloading, already in db')
    return file_name

  download_canceled = False

  with open(file_name, "wb") as f:
    print("Downloading %s" % file_name)
    headers = utils.get_headers(api_key)
    res_file_info, resolution = get_core_file(asset_data, resolution)
    session = requests.Session()

    response = session.get(res_file_info['url'], stream=True)
    total_length = response.headers.get('Content-Length')

    if total_length is None or int(total_length) < 1000:  # no content length header
      download_canceled = True
      print(response.content)
    else:
      total_length = int(total_length)
      dl = 0
      last_percent = 0
      percent = 0
      for data in response.iter_content(chunk_size=4096 * 10):
        dl += len(data)

        # the exact output you're looking for:
        fs_str = files_size_to_text(total_length)

        percent = int(dl * 100 / total_length)
        if percent > last_percent:
          last_percent = percent
          # sys.stdout.write('\r')
          # sys.stdout.write(f'Downloading {asset_data['name']} {fs_str} {percent}% ')  # + int(dl * 50 / total_length) * 'x')
          print(
            f'Downloading {asset_data["name"]} {fs_str} {percent}% ')  # + int(dl * 50 / total_length) * 'x')
          # sys.stdout.flush()

        # print(int(dl*50/total_length)*'x'+'\r')
        f.write(data)
  if download_canceled:
    delete_unfinished_file(file_name)
    return None

  return file_name


def download_asset(asset_data, resolution='blend', api_key='', directory=None):
  '''
  Download an asset non-threaded way.
  Parameters
  ----------
  asset_data - search result from elastic or assets endpoints from API

  Returns
  -------
  path to the resulting asset file or None if asset isn't accessible
  '''

  has_url = get_download_url(asset_data, SCENE_UUID, api_key, tcom=None, resolution='blend') # Resolution does not have any effect
  if not has_url:
    print("Could not get URL for the asset")
    return None

  fpath = download_asset_file(asset_data, api_key=api_key, directory=directory)
  return fpath
