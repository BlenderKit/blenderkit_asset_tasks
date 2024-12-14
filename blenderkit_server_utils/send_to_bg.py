import json
import os
import subprocess
import sys
import tempfile
import threading
from . import paths


def version_to_float(version):
    vars = version.split('.')
    version = int(vars[0]) + .01 * int(vars[1])
    if len(vars) > 2:
        version += .0001 * int(vars[2])
    return version


def get_blender_version_from_blend(blend_file_path):
    # get blender version from blend file, works only for 2.8+
    with open(blend_file_path, 'rb') as blend_file:
        # Read the first 12 bytes
        header = blend_file.read(24)
        # Check for compression
        if header[0:7] == b'BLENDER':
            # If the file is uncompressed, the version is in bytes 9-11
            version_bytes = header[9:12]
            version = (chr(version_bytes[0]), chr(version_bytes[2]))
        elif header[12:19] == b'BLENDER':
            # If the file is compressed, the version is in bytes 8-10
            version_bytes = header[21:24]
            version = (chr(version_bytes[0]), chr(version_bytes[2]))
        else:
            version_bytes = None
            version = ('2', '93')  # last supported version by now
        print(version)
        return '.'.join(version)


def get_blender_binary(asset_data, file_path='', binary_type='CLOSEST'):
    # pick the right blender version for asset processing
    blenders_path = paths.BLENDERS_PATH
    blenders = []
    # Get available blender versions
    for fn in os.listdir(blenders_path):
        blenders.append((version_to_float(fn), fn))
    if binary_type == 'CLOSEST':
        # get asset's blender upload version
        asset_blender_version = version_to_float(asset_data['sourceAppVersion'])
        print('asset blender version', asset_blender_version)

        asset_blender_version_from_blend = get_blender_version_from_blend(file_path)
        print('asset blender version from blend', asset_blender_version_from_blend)

        asset_blender_version_from_blend = version_to_float(asset_blender_version_from_blend)
        asset_blender_version = max(asset_blender_version, asset_blender_version_from_blend)
        print('asset blender version picked', asset_blender_version)

        blender_target = min(blenders, key=lambda x: abs(x[0] - asset_blender_version))
    if binary_type == 'NEWEST':
        blender_target = max(blenders, key=lambda x: x[0])
    # use latest blender version for hdrs
    if asset_data['assetType'] == 'hdr':
        blender_target = blenders[-1]

    print(blender_target)
    ext = '.exe' if sys.platform == 'win32' else ''
    binary = os.path.join(blenders_path, blender_target[1], f'blender{ext}')
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

def send_to_bg(
        asset_data: dict,
        asset_file_path: str = '',
        template_file_path: str = '',
        temp_folder: str = '',
        result_path: str = '',
        result_folder: str = '',
        api_key: str = '',
        script: str = '',
        addons: str = '',
        binary_type: str = 'CLOSEST',
        verbosity_level: int = 2,
        binary_path: str = "",
        target_format: str = ""
        ):
    '''
    Send varioust task to a new blender instance that runs and closes after finishing the task.
    This function waits until the process finishes.
    The function tries to set the same bpy.app.debug_value in the instance of Blender that is run.
    Parameters
    ----------
    asset_data
    asset_file_path - asset file that will be processed
    template_file_path - if provided, gets open first, and the background script handles what should be done with asset file
    temp_folder - temporary directory where the results will be stored
    result_path - path where the result of the processing will be stored
    result_folder - path where only things for possible upload can get stored if there is more than one outpit file
    api_key - api key for the server
    script - script that should be run in background
    addons - addons that should be enabled in the background instance
    target_format - which file format we want to export, e.g.: gltf, gltf-godot

    command - command which should be run in background.
    verbosity_level - level of verbosity: 0 for silent mode, 1 to only print errors, 2 to print everything
    Returns
    -------
    None
    '''

    def reader_thread(pipe, func):
        for line in iter(pipe.readline, b''):
            func(line.decode().strip())
        pipe.close()

    if binary_path != "":
        print(f"Blender binary path: {binary_path}")
    else:
        binary_path = get_blender_binary(asset_data, file_path=asset_file_path, binary_type=binary_type) 

    own_temp_folder = False
    if temp_folder == '':
        temp_folder = tempfile.mkdtemp()
        own_temp_folder = True
    data = {
        'file_path': asset_file_path,
        'result_filepath': result_path,
        'result_folder': result_folder,
        'asset_data': asset_data,
        'api_key': api_key,
        'temp_folder': temp_folder,
        'target_format': target_format,
    }
    datafile = os.path.join(temp_folder, 'resdata.json').replace('\\', '\\\\')
    with open(datafile, 'w', encoding='utf-8') as s:
        json.dump(data, s, ensure_ascii=False, indent=4)

    print('opening Blender instance to do processing - ', script)

    # exclude hdrs from reading as .blend
    if template_file_path == '':
        template_file_path = asset_file_path

    command = [
        binary_path,
        "--background",
        # "--factory-startup",
        "-noaudio",
        template_file_path,
        "--python", os.path.join(paths.BG_SCRIPTS_PATH, script),
        "--", datafile
    ]
    if addons != '':
        addons = f'--addons {addons}'
        command.insert(3, addons)

    # Other code remains the same ...
    stdout_val, stderr_val = subprocess.PIPE, subprocess.PIPE

    with subprocess.Popen(command, stdout=stdout_val, stderr=stderr_val, creationflags=get_process_flags()) as proc:
        if verbosity_level == 2:
            stdout_thread = threading.Thread(target=reader_thread,
                                             args=(proc.stdout, lambda line: print('STDOUT:', line)))
            stderr_thread = threading.Thread(target=reader_thread,
                                             args=(proc.stderr, lambda line: print('STDERR:', line)))
        elif verbosity_level == 1:
            stdout_thread = threading.Thread(target=reader_thread,
                                             args=(proc.stdout, lambda _: None))
            stderr_thread = threading.Thread(target=reader_thread,
                                             args=(proc.stderr, lambda line: print('STDERR:', line)))
        else:
            stdout_thread = threading.Thread(target=reader_thread, args=(proc.stdout, lambda _: None))
            stderr_thread = threading.Thread(target=reader_thread, args=(proc.stderr, lambda _: None))

        stdout_thread.start()
        stderr_thread.start()
        stdout_thread.join()
        stderr_thread.join()
        returncode = proc.wait()

    if returncode != 0:
        print("Error while running command: ", command)
        print("Return code: ", returncode)

    # cleanup
    os.remove(datafile)
    if own_temp_folder:
        os.rmdir(temp_folder)
