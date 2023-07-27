import os
import math
import sys  # read argument from sys.argv
import tempfile

from blenderkit_server_utils import search, paths

MODEL_VALIDATION_FOLDER = 'G:\\Shared drives\\Validations\\model_validation\\'
MODEL_VALIDATION_FOLDER_URL = 'https://drive.google.com/drive/u/0/folders/1ur9NG0SFEpPVj7rnJfhvKNz_rhnKJXPg'


def get_models_for_validation(page_size=100, max_results=100000000):
  dpath = os.path.dirname(tempfile.gettempdir())
  filepath = os.path.join(dpath, 'models_for_validation.json')
  params = {
    'asset_type': 'model',
    'verification_status': 'uploaded',
    'order': 'created',
    'is_private': False,

  }
  search.get_search_simple(params, filepath, page_size=page_size, max_results=max_results, api_key=paths.API_KEY)
  return filepath


def render_all_uploaded_models():
  # for mat in bpy.data.materials:
  #   mat.use_fake_user = False
  # preferences = bpy.context.preferences.addons['blenderkit'].preferences

  filepath = get_models_for_validation(max_results=200)
  assets = search.load_assets_list(filepath)
  print(len(assets))
  for a in assets:
    print(a['name'])

  for i, asset_data in enumerate(assets):

if __name__ == "__main__":
  render_all_uploaded_models()


