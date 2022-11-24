# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import os
import shutil
import sys
try:
  import bpy
except:
  print('bpy not present')


SERVER = os.environ.get('BLENDERKIT_SERVER', 'https://www.blenderkit.com')
API_KEY = os.environ.get('BLENDERKIT_API_KEY', '')
BLENDERKIT_API = "/api/v1"
BLENDERS_PATH = os.environ.get('BLENDERS_PATH','..\\blender_processors')

dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
BG_SCRIPTS_PATH = os.path.join(parent_path, 'blender_bg_scripts')

resolutions = {
  'resolution_0_5K': 512,
  'resolution_1K': 1024,
  'resolution_2K': 2048,
  'resolution_4K': 4096,
  'resolution_8K': 8192,
}
rkeys = list(resolutions.keys())

def get_api_url():
  return SERVER + BLENDERKIT_API

def default_global_dict():
  home = os.path.expanduser("~")
  data_home = os.environ.get('XDG_DATA_HOME')
  if data_home != None:
    home = data_home
  return home + os.sep + 'blenderkit_data'


def get_download_dir(asset_type):
  ''' get directories where assets will be downloaded'''
  subdmapping = {'brush': 'brushes', 'texture': 'textures', 'model': 'models', 'scene': 'scenes',
                 'material': 'materials', 'hdr': 'hdrs'}

  ddir = default_global_dict()
  if not os.path.exists(ddir):
    os.makedirs(ddir)

  subd = subdmapping[asset_type]
  subdir = os.path.join(ddir, subd)
  if not os.path.exists(subdir):
    os.makedirs(subdir)
  return subdir


def slugify(slug):
  """
  Normalizes string, converts to lowercase, removes non-alpha characters,
  and converts spaces to hyphens.
  """
  import re
  slug = slug.lower()

  characters = '<>:"/\\|?\*., ()#'
  for ch in characters:
    slug = slug.replace(ch, '_')
  # import re
  # slug = unicodedata.normalize('NFKD', slug)
  # slug = slug.encode('ascii', 'ignore').lower()
  slug = re.sub(r'[^a-z0-9]+.- ', '-', slug).strip('-')
  slug = re.sub(r'[-]+', '-', slug)
  slug = re.sub(r'/', '_', slug)
  slug = re.sub(r'\\\'\"', '_', slug)
  if len(slug) > 50:
    slug = slug[:50]
  return slug


def extract_filename_from_url(url: str) -> str:
  """Extract filename from URL."""

  if url is not None:
    imgname = url.split('/')[-1]
    imgname = imgname.split('?')[0]
    return imgname
  return ''


resolution_suffix = {
  'blend': '',
  'resolution_0_5K': '_05k',
  'resolution_1K': '_1k',
  'resolution_2K': '_2k',
  'resolution_4K': '_4k',
  'resolution_8K': '_8k',
}
resolutions = {
  'resolution_0_5K': 512,
  'resolution_1K': 1024,
  'resolution_2K': 2048,
  'resolution_4K': 4096,
  'resolution_8K': 8192,
}


def round_to_closest_resolution(res):
  rdist = 1000000
  #    while res/2>1:
  #        p2res*=2
  #        res = res/2
  for rkey in resolutions:
    d = abs(res - resolutions[rkey])
    if d < rdist:
      rdist = d
      p2res = rkey

  return p2res


def get_res_file(asset_data, resolution, find_closest_with_url=False):
  '''
  Returns closest resolution that current asset can offer.
  If there are no resolutions, return orig file.
  If orig file is requested, return it.
  params
  asset_data
  resolution - ideal resolution
  find_closest_with_url:
      returns only resolutions that already containt url in the asset data, used in scenes where asset is/was already present.
  Returns:
      resolution file
      resolution, so that other processess can pass correctly which resolution is downloaded.
  '''
  orig = None
  res = None
  closest = None
  target_resolution = resolutions.get(resolution)
  mindist = 100000000

  for f in asset_data['files']:
    if f['fileType'] == 'blend':
      orig = f
      if resolution == 'blend':
        # orig file found, return.
        return orig, 'blend'

    if f['fileType'] == resolution:
      # exact match found, return.
      return f, resolution
    # find closest resolution if the exact match won't be found.
    rval = resolutions.get(f['fileType'])
    if rval and target_resolution:
      rdiff = abs(target_resolution - rval)
      if rdiff < mindist:
        closest = f
        mindist = rdiff
  if not res and not closest:
    return orig, 'blend'
  return closest, closest['fileType']


def server_2_local_filename(asset_data, filename):
  '''
  Convert file name on server to file name local.
  This should get replaced
  '''

  fn = filename.replace('blend_', '')
  fn = fn.replace('resolution_', '')
  n = slugify(asset_data['name']) + '_' + fn
  return n


def get_texture_directory(asset_data, resolution='blend'):
  tex_dir_path = f"//textures{resolution_suffix[resolution]}{os.sep}"
  return tex_dir_path


def get_texture_filepath(tex_dir_path, image, resolution='blend'):
  if len(image.packed_files) > 0:
    image_file_name = bpy.path.basename(image.packed_files[0].filepath)
  else:
    image_file_name = bpy.path.basename(image.filepath)
  if image_file_name == '':
    image_file_name = image.name.split('.')[0]

  fp = os.path.join(tex_dir_path, image_file_name)
  # check if there is allready an image with same name and thus also assigned path
  # (can happen easily with genearted tex sets and more materials)
  done = False
  fpn = fp
  i = 0
  while not done:
    is_solo = True
    for image1 in bpy.data.images:
      if image != image1 and image1.filepath == fpn:
        is_solo = False
        fpleft, fpext = os.path.splitext(fp)
        fpn = fpleft + str(i).zfill(3) + fpext
        i += 1
    if is_solo:
      done = True

  return fpn

def delete_asset_debug(asset_data):
  '''TODO fix this for resolutions - should get ALL files from ALL resolutions.'''
  from . import download

  download.get_download_url(asset_data, utils.get_scene_id(), api_key)

  file_names = get_download_filepaths(asset_data)
  for f in file_names:
    asset_dir = os.path.dirname(f)

    if os.path.isdir(asset_dir):
      try:
        print(f'{asset_dir}')
        shutil.rmtree(asset_dir)
      except:
        e = sys.exc_info()[0]
        print(f'{e}')


def get_clean_filepath():
  script_path = os.path.dirname(os.path.realpath(__file__))
  subpath = "blendfiles" + os.sep + "cleaned.blend"
  cp = os.path.join(script_path, subpath)
  return cp


def get_addon_file(subpath=''):
  script_path = os.path.dirname(os.path.realpath(__file__))
  # fpath = os.path.join(p, subpath)
  return os.path.join(script_path, subpath)
