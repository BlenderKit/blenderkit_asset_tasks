# This script is able to delete a single parameter from all assets, good for db cleanup.
# needs finishing to become a full-fledged task that can be run from actions, but you can by now use it manually
# just fill in name and also provide api key as environment variable.

from blenderkit_server_utils import search, paths, upload

import tempfile
import time
import os

param_name = ""
params = {
    param_name + '_isnull': False,
}
dpath = tempfile.gettempdir()
filepath = os.path.join(dpath, 'assets_for_resolutions.json')
MAX_ASSETS = 10000

assets = search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                                  api_key=paths.API_KEY)

for asset_data in assets:
    start_time = time.time()

    asset_id = asset_data['id']
    param_value = ''

    upload.delete_individual_parameter(asset_id=asset_id, param_name=param_name, param_value=param_value,
                                       api_key=paths.API_KEY)
    upload.patch_asset_empty(asset_id=asset_id, api_key=paths.API_KEY)
    # --------------------------------------------------------------------------------------------------------------------
    print("--- %s seconds ---" % (time.time() - start_time))
    # --------------------------------------------------------------------------------------------------------------------
