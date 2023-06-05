# --------------------------------------------------------------------------------------------------------------------
import os
import subprocess

def setup():
    install_cmds = [
        ['pip', 'install', 'Pillow'],
        ['pip', 'install', 'gradio'],
        ['pip', 'install', 'open_clip_torch'],
        ['pip', 'install', 'clip-interrogator'],
    ]
    for cmd in install_cmds:
        print(subprocess.run(cmd, stdout=subprocess.PIPE).stdout.decode('utf-8'))

setup()

from PIL import Image
from clip_interrogator import Config, Interrogator
from urllib.request import urlopen
from blenderkit_server_utils import download, search, paths, upload, send_to_bg

import json
import requests
import tempfile
import time


# --------------------------------------------------------------------------------------------------------------------
def read_json(json_url):
  """Reads json from search link.
  """
  print("Reading json from link {}".format(json_url))

  # store the response of URL
  response = urlopen(json_url)

  # storing the JSON response from url
  data_json = json.loads(response.read())

  # return the json response
  return data_json




param_name  = "imageCaptionInterrogator"
params = {
    'order': '-created',
    'verification_status': 'validated',
    param_name+'_isnull': True,
  }
dpath = tempfile.gettempdir()
filepath = os.path.join(dpath, 'assets_for_resolutions.json')
max_assets = 2
assets = search.get_search_simple(params, filepath, page_size=min(max_assets, 100), max_results=max_assets,
                           api_key=paths.API_KEY)
print(assets)

ci = Interrogator(Config(clip_model_name="ViT-L-14/openai"))
# ci = Interrogator(Config(clip_model_name="ViT-H-14/laion2b_s32b_b79k"))

for asset_data in assets:
    start_time = time.time()

    asset_id = asset_data['id']
    json_url = "https://www.blenderkit.com/api/v1/search/?format=json&query=asset_id:" + asset_id
    data_json = read_json(json_url)

    # print(data_json['results'][0]['id'])
    # print(data_json['results'][0]['description'])
    print(data_json['results'][0]['thumbnailXlargeUrl'])

    img_data = requests.get(data_json['results'][0]['thumbnailXlargeUrl']).content
    img_path = os.path.join(dpath, 'image_name.jpg')
    with open(img_path, 'wb') as handler:
        handler.write(img_data)

    # upload image
    # image = Image.open(data_json['results'][0]['thumbnailXlargeUrl']).convert('RGB')
    image = Image.open(img_path).convert('RGB')


    print(ci.interrogate(image))
    param_value = ci.interrogate(image)
    # --------------------------------------------------------------------------------------------------------------------

    # patch parameters on server
    upload.patch_individual_parameter(asset_id = asset_id, param_name = param_name, param_value = param_value, api_key = paths.API_KEY)
    upload.get_individual_parameter(asset_id = asset_id, param_name = param_name, api_key = paths.API_KEY)

    # --------------------------------------------------------------------------------------------------------------------
    print("--- %s seconds ---" % (time.time() - start_time))
    # --------------------------------------------------------------------------------------------------------------------