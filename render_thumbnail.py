"""
Script to rerender of thumbnail for materials and models.
This script handles the automated process of generating new thumbnails for BlenderKit assets.
It supports both materials and models, with configurable rendering parameters.

Required environment variables:
BLENDERKIT_API_KEY - API key to be used
BLENDERS_PATH - path to the folder with blender versions

Optional environment variables for thumbnail parameters:
THUMBNAIL_USE_GPU - (bool) Use GPU for rendering
THUMBNAIL_SAMPLES - (int) Number of render samples
THUMBNAIL_RESOLUTION - (int) Resolution of render
THUMBNAIL_DENOISING - (bool) Use denoising
THUMBNAIL_BACKGROUND_LIGHTNESS - (float) Background lightness (0-1)

For materials:
THUMBNAIL_TYPE - Type of material preview (BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR)
THUMBNAIL_SCALE - (float) Scale of preview object
THUMBNAIL_BACKGROUND - (bool) Use background for transparent materials
THUMBNAIL_ADAPTIVE_SUBDIVISION - (bool) Use adaptive subdivision

For models:
THUMBNAIL_ANGLE - Camera angle (DEFAULT, FRONT, SIDE, TOP)
THUMBNAIL_SNAP_TO - Object placement (GROUND, WALL, CEILING, FLOAT)

The script workflow:
1. Fetches assets that need thumbnail regeneration
2. For each asset:
   - Downloads the asset file
   - Renders a new thumbnail using Blender
   - Uploads the new thumbnail
   - Updates the asset metadata
3. Handles multiple assets concurrently using threading
"""

import json
import os
import tempfile
import time
import threading
from datetime import datetime
from pathlib import Path

from blenderkit_server_utils import download, search, paths, upload, send_to_bg

# Required environment variables
ASSET_BASE_ID = os.environ.get('ASSET_BASE_ID', None)
MAX_ASSETS = int(os.environ.get('MAX_ASSET_COUNT', '100'))
SKIP_UPLOAD = os.environ.get('SKIP_UPLOAD', False) == "True"

# Thumbnail default parameters
DEFAULT_THUMBNAIL_PARAMS = {
    'thumbnail_use_gpu': True,
    'thumbnail_samples': 100,
    'thumbnail_resolution': 2048,
    'thumbnail_denoising': True,
    'thumbnail_background_lightness': 0.9,
}

# Material-specific defaults
DEFAULT_MATERIAL_PARAMS = {
    'thumbnail_type': 'BALL',
    'thumbnail_scale': 1.0,
    'thumbnail_background': False,
    'thumbnail_adaptive_subdivision': False,
}

# Model-specific defaults
DEFAULT_MODEL_PARAMS = {
    'thumbnail_angle': 'DEFAULT',
    'thumbnail_snap_to': 'GROUND',
}

def parse_json_params(json_str):
    """Parse the markThumbnailRender JSON parameter.
    
    Args:
        json_str: JSON string containing thumbnail parameters
        
    Returns:
        dict: Parsed parameters or empty dict if invalid JSON
    """
    if not json_str:
        return {}
        
    try:
        params = json.loads(json_str)
        # Convert string boolean values to actual booleans
        bool_params = [
            'thumbnail_use_gpu', 
            'thumbnail_denoising',
            'thumbnail_background',
            'thumbnail_adaptive_subdivision'
        ]
        for param in bool_params:
            if param in params and isinstance(params[param], str):
                params[param] = params[param].lower() == 'true'
                
        # Convert numeric values
        numeric_params = [
            'thumbnail_samples',
            'thumbnail_resolution',
            'thumbnail_background_lightness',
            'thumbnail_scale'
        ]
        for param in numeric_params:
            if param in params:
                try:
                    if '.' in str(params[param]):  # Convert to float if decimal point present
                        params[param] = float(params[param])
                    else:
                        params[param] = int(params[param])
                except (ValueError, TypeError):
                    del params[param]  # Remove invalid numeric values
                    
        return params
    except json.JSONDecodeError:
        print(f"Warning: Invalid JSON in markThumbnailRender parameter")
        return {}

def get_thumbnail_params(asset_type, mark_thumbnail_render=None):
    """Get thumbnail parameters from environment variables or defaults.
    
    This function consolidates all thumbnail rendering parameters, combining values
    from different sources in order of priority:
    1. Environment variables (highest priority)
    2. markThumbnailRender JSON parameter
    3. Default values (lowest priority)
    
    Args:
        asset_type (str): Type of asset ('material' or 'model')
        mark_thumbnail_render (str, optional): JSON string from markThumbnailRender parameter
        
    Returns:
        dict: Combined dictionary of all thumbnail parameters
    """
    # Start with default parameters
    params = DEFAULT_THUMBNAIL_PARAMS.copy()
    
    # Add type-specific defaults
    if asset_type == 'material':
        params.update(DEFAULT_MATERIAL_PARAMS)
    elif asset_type == 'model':
        params.update(DEFAULT_MODEL_PARAMS)
    
    # Update with markThumbnailRender parameters if available
    json_params = parse_json_params(mark_thumbnail_render)
    if json_params:
        params.update(json_params)
    
    # Update with environment variables (highest priority)
    env_updates = {
        'thumbnail_use_gpu': os.environ.get('THUMBNAIL_USE_GPU', params['thumbnail_use_gpu']) == "True",
        'thumbnail_samples': int(os.environ.get('THUMBNAIL_SAMPLES', params['thumbnail_samples'])),
        'thumbnail_resolution': int(os.environ.get('THUMBNAIL_RESOLUTION', params['thumbnail_resolution'])),
        'thumbnail_denoising': os.environ.get('THUMBNAIL_DENOISING', params['thumbnail_denoising']) == "True",
        'thumbnail_background_lightness': float(os.environ.get('THUMBNAIL_BACKGROUND_LIGHTNESS', params['thumbnail_background_lightness'])),
    }
    
    # Add type-specific environment variables
    if asset_type == 'material':
        env_updates.update({
            'thumbnail_type': os.environ.get('THUMBNAIL_TYPE', params['thumbnail_type']),
            'thumbnail_scale': float(os.environ.get('THUMBNAIL_SCALE', params['thumbnail_scale'])),
            'thumbnail_background': os.environ.get('THUMBNAIL_BACKGROUND', params['thumbnail_background']) == "True",
            'thumbnail_adaptive_subdivision': os.environ.get('THUMBNAIL_ADAPTIVE_SUBDIVISION', params['thumbnail_adaptive_subdivision']) == "True",
        })
    elif asset_type == 'model':
        env_updates.update({
            'thumbnail_angle': os.environ.get('THUMBNAIL_ANGLE', params['thumbnail_angle']),
            'thumbnail_snap_to': os.environ.get('THUMBNAIL_SNAP_TO', params['thumbnail_snap_to']),
        })
    
    # Only update with environment variables that are actually set
    params.update({k: v for k, v in env_updates.items() if k in params})
    
    return params

def render_thumbnail_thread(asset_data, api_key):
    """Process a single asset's thumbnail in a separate thread.
    
    This function handles the complete thumbnail generation workflow for a single asset:
    1. Downloads the asset file to a temporary directory
    2. Sets up the thumbnail parameters based on asset type
    3. Launches Blender in background mode to render the thumbnail
    4. Uploads the resulting thumbnail
    5. Updates the asset metadata with new thumbnail information
    6. Cleans up temporary files
    
    Args:
        asset_data (dict): Asset metadata including ID, type, and other properties
        api_key (str): BlenderKit API key for authentication
    """
    destination_directory = tempfile.gettempdir()
    
    # Download asset
    asset_file_path = download.download_asset(asset_data, api_key=api_key, directory=destination_directory)
    
    if not asset_file_path:
        print(f"Failed to download asset {asset_data['name']}")
        return

    # Create temp folder for results
    temp_folder = tempfile.mkdtemp()
    result_filepath = os.path.join(temp_folder, f"{asset_data['assetBaseId']}_thumb.{'jpg' if asset_data['assetType'] == 'model' else 'png'}")
    
    # Get thumbnail parameters based on asset type and markThumbnailRender
    thumbnail_params = get_thumbnail_params(
        asset_data['assetType'].lower(),
        mark_thumbnail_render=asset_data.get('markThumbnailRender')
    )
    
    # Update asset_data with thumbnail parameters
    asset_data.update(thumbnail_params)

    # Select appropriate script and template based on asset type
    if asset_data['assetType'] == 'material':
        script_name = 'autothumb_material_bg.py'
        template_path = Path(__file__).parent / 'blend_files' / 'material_thumbnailer_cycles.blend'
    elif asset_data['assetType'] == 'model':
        script_name = 'autothumb_model_bg.py'
        template_path = Path(__file__).parent / 'blend_files' / 'model_thumbnailer.blend'
    else:
        print(f"Unsupported asset type: {asset_data['assetType']}")
        return

    # Send to background Blender for thumbnail generation
    send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=asset_file_path,
        template_file_path=str(template_path),
        result_path=result_filepath,
        script=script_name,
    )

    # Check results and upload
    try:
            
        if SKIP_UPLOAD:
            print('----- SKIP_UPLOAD==True -> skipping upload -----')
            return

        files = [
            {
                "type": "thumbnail",
                "index": 0,
                "file_path": result_filepath,
            }
        ]
        upload_data = {
            "name": asset_data["name"],
            "displayName": asset_data["displayName"],
            "token": api_key,
            "id":asset_data["id"],
            }
        # Upload the new thumbnail
        print(f"Uploading thumbnail for {asset_data['name']}")
        ok = upload.upload_files(upload_data, files)

        
        if ok:
            print(f"Successfully uploaded new thumbnail for {asset_data['name']}")
            # Clear the markThumbnailRender parameter
            clear_ok = upload.delete_individual_parameter(
                asset_data['id'],
                param_name='markThumbnailRender',
                param_value='',  # Empty string to clear the parameter
                api_key=api_key
            )
            if clear_ok:
                print(f"Successfully cleared markThumbnailRender for {asset_data['name']}")
            else:
                print(f"Failed to clear markThumbnailRender for {asset_data['name']}")
        else:
            print(f"Failed to upload thumbnail for {asset_data['name']}")
    except Exception as e:
        print(f"Error processing thumbnail results: {e}")
    finally:
        # Cleanup
        try:
            os.remove(asset_file_path)
            os.remove(result_filepath)
            os.rmdir(temp_folder)
        except:
            pass

def iterate_assets(filepath, api_key, process_count=1):
    """Process multiple assets concurrently using threading.
    
    Manages a pool of worker threads to process multiple assets simultaneously.
    Limits the number of concurrent processes to avoid system overload.
    
    Args:
        filepath (str): Path to the JSON file containing asset data
        api_key (str): BlenderKit API key for authentication
        process_count (int): Maximum number of concurrent thumbnail generations
    """
    assets = search.load_assets_list(filepath)
    threads = []
    
    for asset_data in assets:
        if asset_data is not None:
            print(f"Processing thumbnail for {asset_data['name']}")
            thread = threading.Thread(target=render_thumbnail_thread, args=(asset_data, api_key))
            thread.start()
            threads.append(thread)
            
            while len(threads) > process_count - 1:
                for t in threads[:]:
                    if not t.is_alive():
                        threads.remove(t)
                    break
                time.sleep(0.1)

    # Wait for remaining threads
    for thread in threads:
        thread.join()

def main():
    """Main entry point for the thumbnail generation script.
    
    Sets up the initial conditions for thumbnail generation:
    1. Creates a temporary directory for asset processing
    2. Configures search parameters to find assets needing thumbnails
    3. Fetches the list of assets to process
    4. Initiates the thumbnail generation process
    
    The script can either process a specific asset (if ASSET_BASE_ID is set)
    or process multiple assets based on search criteria.
    """
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, 'assets_for_thumbnails.json')

    # Set up search parameters
    if ASSET_BASE_ID:
        params = {'asset_base_id': ASSET_BASE_ID}
    else:
        params = {
            'asset_type': 'model,material',
            'order': '-created',
            'markThumbnailRender_isnull': False,
        }
    
    # Get assets to process
    assets = search.get_search_simple(
        params,
        filepath,
        page_size=min(MAX_ASSETS, 100),
        max_results=MAX_ASSETS,
        api_key=paths.API_KEY
    )

    print(f'Found {len(assets)} assets to process:')
    for asset in assets:
        print(f"{asset['name']} ({asset['assetType']})")
    
    iterate_assets(filepath, api_key=paths.API_KEY)

if __name__ == '__main__':
    main()
