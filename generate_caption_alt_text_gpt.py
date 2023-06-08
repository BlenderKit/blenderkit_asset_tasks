# ----------------------------------------------------------------------------------------------------------------------
# installing pytorch gpu version
# https://pytorch.org/get-started/locally/
# & C:\Users\blend\AppData\Local\Programs\Python\Python310\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu117

# ----------------------------------------------------------------------------------------------------------------------
import os


import openai

from blenderkit_server_utils import search, paths, upload

import tempfile
import time

param_name_source  = "imageCaptionInterrogator"
param_name_target  = "imageAltTextGen3"
params = {
    'order': '-created',
    'asset_type': 'model',
    'verification_status': 'validated',
    param_name_source+'_isnull': False, #just get those which already have interrogator data, but don't have the rest
    param_name_target+'_isnull': True, #jand those that don't have gpt alt caption yet
  }

dpath = tempfile.gettempdir()
filepath = os.path.join(dpath, 'assets_for_resolutions.json')
MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))
openai.api_key = os.environ.get('OPENAI_API_KEY', '')

assets = search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)

def get_GPT_request_text(asset_data):
    """Returns text for GPT request.
    """
    text = f'''We got this information from a BlenderKit 3D {asset_data['assetType']}.
name of 3d model: "{asset_data['name']}"
category slug: "{asset_data['category']}"
description AI generated(based on thumbnail, don't trust it too much):
"{asset_data['dictParameters']['imageCaptionInterrogator']}"
description user written:
"{asset_data['description']}"
software used:
Blender 3D
We need a good alt text that optimizes our SEO for google image search, when people search for 3D models.
Please write an alt text in max 3 sentences, use the keywords in the description and use the better one of the 2 descriptions provided.'''

    return text

for asset_data in assets:
    start_time = time.time()


    print(f'Processing asset {asset_data["id"]}: {asset_data["name"]}')
    request_message = get_GPT_request_text(asset_data)
    # putting everything into try statement since openai api is not very stable, can be overloaded etc.
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a chatbot"},
                {"role": "user", "content": request_message},
            ]
        )

        result = ''
        for choice in response.choices:
            result += choice.message.content
        param_value = result

        # --------------------------------------------------------------------------------------------------------------------

        upload.patch_individual_parameter(asset_id = asset_data['id'], param_name = param_name_target, param_value = param_value, api_key = paths.API_KEY)
        upload.get_individual_parameter(asset_id = asset_data['id'], param_name = param_name_target, api_key = paths.API_KEY)

        # --------------------------------------------------------------------------------------------------------------------
        print("--- %s seconds ---" % (time.time() - start_time))
        # --------------------------------------------------------------------------------------------------------------------
        time.sleep(2) #to avoid openai throttling
    except Exception as e:
        print(e)