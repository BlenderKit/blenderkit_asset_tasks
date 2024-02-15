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
MODEL_VALIDATION_FOLDER_ID = "1L10ngR6vkTjmlzy9CQa2D08slhigBpwe"
GOOGLE_SHARED_DRIVE_ID = "0ABpmYJ3IosxhUk9PVA"

existing_folders = []

def render_model_validation_thread(asset_data, api_key):
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
    global existing_folders

    destination_directory = tempfile.gettempdir()
    if len(asset_data['files']) == 0:
        print('no files for asset %s' % asset_data['name'])
        return
    upload_id = asset_data['files'][0]['downloadUrl'].split('/')[-2]

    # Check if the asset has already been processed
    # stop using author folder
    author_folder_name = f"{upload_id}"
    result_file_name = f"{upload_id}"
    predicted_filename = f'{result_file_name}.mkv'#let's try to super simplify now.

    # author_folder_id = google_drive.ensure_folder_exists(drive, author_folder_name,
    #                                                      parent_id=MODEL_VALIDATION_FOLDER_ID,
    #                                                      drive_id=GOOGLE_SHARED_DRIVE_ID)

    #print('all validation folders', all_validation_folders)

    # check if the file exists, only with partial name - because animations can end up with different framecount which is then in the name or similar
    f_exists=False
    for ef in existing_folders:
        if author_folder_name in ef['name']:
            f_exists=True
            break
    # f_exists = google_drive.file_exists_partial(drive, result_file_name, folder_id=MODEL_VALIDATION_FOLDER_ID)

    if f_exists:
        print('file exists, skipping')
        return

    # Download asset
    file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # find template file
    current_dir = pathlib.Path(__file__).parent.resolve()
    template_file_path = os.path.join(current_dir, 'blend_files', 'model_validation_mix.blend')

    # Send to background to generate resolutions
    #generated temp folder
    #.blend gets resaved there and also /tmp renders of images
    tempdir = tempfile.mkdtemp()

    # result folder where the stuff for upload to drive goes
    result_folder = os.path.join(tempdir, upload_id)
    os.makedirs(result_folder, exist_ok=True)

    # local file path of rendered image
    result_path = os.path.join(tempdir,
                               result_folder,
                               predicted_filename)

    # send to background to render
    send_to_bg.send_to_bg(asset_data,
                          asset_file_path=file_path,
                          template_file_path=template_file_path,
                          result_path=result_path,
                          result_folder=result_folder,
                          tempdir=tempdir,
                          script='model_validation_bg_render.py',
                          binary_type='NEWEST',
                          verbosity_level=2)

    # Upload result
    drive = google_drive.init_drive()
    google_drive.upload_folder_to_drive(drive, result_folder, MODEL_VALIDATION_FOLDER_ID, GOOGLE_SHARED_DRIVE_ID)

    return


def iterate_assets(filepath, thread_function=None, process_count=12, api_key=''):
    ''' iterate through all assigned assets, check for those which need generation and send them to res gen'''
    assets = search.load_assets_list(filepath)
    threads = []
    for asset_data in assets:
        if asset_data is not None:
            print('downloading and generating validation render for  %s' % asset_data['name'])
            thread = threading.Thread(target=thread_function, args=(asset_data, api_key))
            thread.start()
            threads.append(thread)
            while len(threads) > process_count - 1:
                for t in threads:
                    if not t.is_alive():
                        threads.remove(t)
                    break;
                time.sleep(0.1)  # wait for a bit to finish all threads


def main():

    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, 'assets_for_resolutions.json')
    params = {
        'order': 'created',
        'asset_type': 'model',
        'verification_status': 'uploaded'
    }
    search.get_search_simple(params, filepath=filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                             api_key=paths.API_KEY)

    global existing_folders
    drive = google_drive.init_drive()
    existing_folders = google_drive.list_files_in_folder(drive, MODEL_VALIDATION_FOLDER_ID)

    assets = search.load_assets_list(filepath)
    print('ASSETS TO BE PROCESSED')
    for i, a in enumerate(assets):
        print(a['name'], a['assetType'])

    iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_model_validation_thread)


if __name__ == '__main__':
    main()
