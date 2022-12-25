import json
import os
import subprocess
import sys
import tempfile

from . import paths



def version_to_float(version):
  vars = version.split('.')
  version = int(vars[0]) + .01 * int(vars[1])
  if len(vars) > 2:
    version += .0001 * int(vars[2])
  return version

def get_blender_binary(asset_data):
  # pick the right blender version for asset processing
  blenders_path = paths.BLENDERS_PATH
  blenders = []
  #Get available blender versions
  for fn in os.listdir(blenders_path):
    blenders.append((version_to_float(fn), fn))
  #get asset's blender upload version
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

def send_to_bg(asset_data, asset_file_path='', template_file_path = '', result_path='', api_key='', script='', addons = ''):
  '''
  Send varioust task to a new blender instance that runs and closes after finishing the task.
  This function waits until the process finishes.
  The function tries to set the same bpy.app.debug_value in the instance of Blender that is run.
  Parameters
  ----------
  asset_data
  asset_file_path - asset file that will be processed
  template_file_path - if provided, gets open first, and the background script handles what should be done with asset file
  command - command which should be run in background.

  Returns
  -------
  None
  '''
  binary_path = get_blender_binary(asset_data)

  data = {
    'file_path': asset_file_path,
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
  if template_file_path == '':
    template_file_path = asset_file_path

  command = [
    binary_path,
    "--background",
    "--factory-startup",
    "-noaudio",
    template_file_path,
    "--python", os.path.join(paths.BG_SCRIPTS_PATH, script),
    "--", datafile
  ]
  if addons != '':
    addons = f'--addons {addons}'
    command.insert(3, addons)

  proc = subprocess.run(command, bufsize=1, stdout=sys.stdout, stdin=subprocess.PIPE, creationflags=get_process_flags())
