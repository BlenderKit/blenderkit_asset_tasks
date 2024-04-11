# -----------------------------------------------------------------------------------
# generate material validation scene for all uploaded assets
# -------------------------------------------- ---------------------------------------

import json
import os
import shutil
import tempfile
import threading
import time
import pathlib

from blenderkit_server_utils import download, search, paths, upload, send_to_bg, utils
# Assuming necessary imports are done at the top of the script
from blenderkit_server_utils.cloudflare_storage import CloudflareStorage


results = []
page_size = 100

MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))


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

    destination_directory = tempfile.gettempdir()
    if len(asset_data['files']) == 0:
        print('no files for asset %s' % asset_data['name'])
        return
    upload_id = asset_data['files'][0]['downloadUrl'].split('/')[-2]

    # Check if the asset has already been processed
    # stop using author folder
    result_file_name = f"{upload_id}"
    predicted_filename = f'{result_file_name}.mkv'#let's try to super simplify now.

    #print('all validation folders', all_validation_folders)

    # check if the directory exists on the drive
    # we check file by file, since the comparison with folder contents is not reliable and would potentially
    # compare with a very long list. main issue was what to set the page size for the search request...
    # Initialize Cloudflare Storage with your credentials
    cloudflare_storage = CloudflareStorage(
        access_key=os.getenv('CF_ACCESS_KEY'),
        secret_key=os.getenv('CF_ACCESS_SECRET'),
        endpoint_url=os.getenv('CF_ENDPOINT_URL')
    )
    f_exists = cloudflare_storage.folder_exists(bucket_name='validation-renders', folder_name=result_file_name)

    #let's not skip now.
    if f_exists:
        # purge the folder
        # cloudflare_storage.delete_folder_contents('validation-renders', upload_id)
        print(f'directory {upload_id} exists, skipping')
        return

    # Download asset
    file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)

    # find template file
    current_dir = pathlib.Path(__file__).parent.resolve()
    template_file_path = os.path.join(current_dir, 'blend_files', 'model_validation_static_renders.blend')

    # Send to background to generate resolutions
    #generated temp folder
    #.blend gets resaved there and also /tmp renders of images
    temp_folder = tempfile.mkdtemp()

    # result folder where the stuff for upload to drive goes
    result_folder = os.path.join(temp_folder, upload_id)
    os.makedirs(result_folder, exist_ok=True)

    # local file path of rendered image
    result_path = os.path.join(temp_folder,
                               result_folder,
                               predicted_filename)

    # send to background to render
    send_to_bg.send_to_bg(asset_data,
                          asset_file_path=file_path,
                          template_file_path=template_file_path,
                          result_path=result_path,
                          result_folder=result_folder,
                          temp_folder=temp_folder,
                          script='model_validation_bg_render.py',
                          binary_type='NEWEST',
                          verbosity_level=2)

    # part of the results is in temfolder/tmp/Render, so let's move all of it's files to the result folder,
    # so that there are no subdirectories and everything is in one folder.
    # and then upload the result folder to drive
    render_folder = os.path.join(temp_folder, 'tmp', 'Render')
    file_names = os.listdir(render_folder)
    for file_name in file_names:
        shutil.move(os.path.join(render_folder, file_name), result_folder)

    # Upload result
    # # Instead of using Google Drive for upload, use Cloudflare Storage
    # Initialize the CloudFlare service
    cloudflare_storage = CloudflareStorage(
        access_key=os.getenv('CF_ACCESS_KEY'),
        secret_key=os.getenv('CF_ACCESS_SECRET'),
        endpoint_url=os.getenv('CF_ENDPOINT_URL')
    )
    cloudflare_storage.upload_folder(result_folder, bucket_name='validation-renders', cloudflare_folder_prefix=result_file_name)
    shutil.rmtree(temp_folder)
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
    # cleanup the drive folder

    # Get os temp directory
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, 'assets_for_resolutions.json')
    params = {
        'order': 'last_blend_upload',
        'asset_type': 'model',
        'verification_status': 'uploaded'
    }
    search.get_search_simple(params, filepath=filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                             api_key=paths.API_KEY)

    assets = search.load_assets_list(filepath)
    print('ASSETS TO BE PROCESSED')
    for i, a in enumerate(assets):
        print(a['name'], a['assetType'])

    iterate_assets(filepath, process_count=1, api_key=paths.API_KEY, thread_function=render_model_validation_thread)


if __name__ == '__main__':
    main()
