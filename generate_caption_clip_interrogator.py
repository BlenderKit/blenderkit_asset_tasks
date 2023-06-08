# ----------------------------------------------------------------------------------------------------------------------
# installing pytorch gpu version
# https://pytorch.org/get-started/locally/
# & C:\Users\blend\AppData\Local\Programs\Python\Python310\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu117

# ----------------------------------------------------------------------------------------------------------------------
import subprocess
import sys
import os

# upgrade pip
subprocess.call([sys.executable, "-m", "ensurepip"])
subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

# python -m pip install --upgrade pip

# install required packages
subprocess.call([sys.executable, "-m", "pip", "install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu117"])
subprocess.call([sys.executable, "-m", "pip", "install", "gradio"])
subprocess.call([sys.executable, "-m", "pip", "install", "open_clip_torch"])
subprocess.call([sys.executable, "-m", "pip", "install", "clip-interrogator"])

import torch
torch.cuda.is_available()
print(torch.__version__)

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
    # 'asset_type': 'model',
    'verification_status': 'validated',
    param_name+'_isnull': True,
  }
dpath = tempfile.gettempdir()
filepath = os.path.join(dpath, 'assets_for_resolutions.json')
MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))

assets = search.get_search_simple(params, filepath, page_size=min(MAX_ASSETS, 100), max_results=MAX_ASSETS,
                           api_key=paths.API_KEY)

ci = Interrogator(Config(clip_model_name="ViT-L-14/openai"))
# ci = Interrogator(Config(clip_model_name="ViT-H-14/laion2b_s32b_b79k"))

for asset_data in assets:
    start_time = time.time()

    asset_id = asset_data['id']

    print(asset_data['thumbnailXlargeUrl'])
    print(f'Interrogating asset {asset_id} {asset_data["name"]}')
    img_data = requests.get(asset_data['thumbnailXlargeUrl']).content
    img_path = os.path.join(dpath, 'image_name.jpg')
    with open(img_path, 'wb') as handler:
        handler.write(img_data)

    # upload image
    # image = Image.open(data_json['results'][0]['thumbnailXlargeUrl']).convert('RGB')
    image = Image.open(img_path).convert('RGB')



    param_value = ci.interrogate(image)
    print(param_value)

    # --------------------------------------------------------------------------------------------------------------------
    #shorten param_value to 255 characters if it's longer
    if len(param_value) > 255:
        #split the string on last space before the max length is reached
        param_value = param_value[:255].rsplit(' ', 1)[0]
        print(f'param_value shortened to {param_value}')
    # patch parameters on server

    upload.patch_individual_parameter(asset_id = asset_id, param_name = param_name, param_value = param_value, api_key = paths.API_KEY)
    upload.get_individual_parameter(asset_id = asset_id, param_name = param_name, api_key = paths.API_KEY)

    # --------------------------------------------------------------------------------------------------------------------
    print("--- %s seconds ---" % (time.time() - start_time))
    # --------------------------------------------------------------------------------------------------------------------