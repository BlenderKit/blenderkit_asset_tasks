import bpy
import sys
import os
import json
import tempfile


# import utils- add path
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_path = os.path.join(dir_path, os.path.pardir)
sys.path.append(parent_path)
from blenderkit_server_utils import paths, image_utils

def generate_lower_resolutions(data):
    '''generates lower resolutions for HDR images.
    1. since an empty .blend was opened, we need to load the HDR image
    2. then we need to downscale it and save it in the same folder with the suffixes like _2k, _1k, _512
    3. dumps a json file with the paths to the generated files, so they can be uploaded by the main thread.
    '''
    asset_data = data['asset_data']
    fpath = data['file_path']
    hdr = bpy.data.images.load(fpath)
    actres = max(hdr.size[0], hdr.size[1])
    p2res = paths.round_to_closest_resolution(actres)
    original_filesize = os.path.getsize(fpath) # for comparison on the original level
    i = 0
    finished = False
    files = []
    while not finished:
        dirn = os.path.dirname(fpath)
        fn_strip, ext = os.path.splitext(fpath)
        ext = '.exr'
        if i>0:
            image_utils.downscale(hdr)


        hdr_resolution_filepath = fn_strip + paths.resolution_suffix[p2res] + ext
        image_utils.img_save_as(hdr, filepath=hdr_resolution_filepath, file_format='OPEN_EXR', quality=20, color_mode='RGB', compression=15,
                    view_transform='Raw', exr_codec = 'DWAA')

        if os.path.exists(hdr_resolution_filepath):
            reduced_filesize = os.path.getsize(hdr_resolution_filepath)

        # compare file sizes
        print(f'HDR size was reduced from {original_filesize} to {reduced_filesize}')
        if reduced_filesize < original_filesize:
            # this limits from uploaidng especially same-as-original resolution files in case when there is no advantage.
            # usually however the advantage can be big also for same as original resolution
            files.append({
                "type": p2res,
                "index": 0,
                "file_path": hdr_resolution_filepath
            })

            print('prepared resolution file: ', p2res)

        if paths.rkeys.index(p2res) == 0:
            finished = True
        else:
            p2res = paths.rkeys[paths.rkeys.index(p2res) - 1]
        i+=1

    print('uploading resolution files')
    print(files)
    data_out = files
    # binary_path = global_vars.PREFS['binary_path']
    # temp_folder = tempfile.mkdtemp()
    # datafile = os.path.join(temp_folder + 'resdata.json').replace('\\', '\\\\')
    # script_path = os.path.dirname(os.path.realpath(__file__))
    with open(data['result_filepath'], 'w', encoding='utf-8') as s:
        json.dump(files, s, ensure_ascii=False, indent=4)


if __name__ == "__main__":
  print('background resolution generator')
  datafile = sys.argv[-1]
  with open(datafile, 'r', encoding='utf-8') as f:
    data = json.load(f)
  generate_lower_resolutions(data)