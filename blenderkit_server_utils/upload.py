import os
import sys
import requests
from . import utils, paths
import json


class upload_in_chunks(object):
    def __init__(self, filename, chunksize=1 << 13, report_name="file"):
        self.filename = filename
        self.chunksize = chunksize
        self.totalsize = os.path.getsize(filename)
        self.readsofar = 0
        self.report_name = report_name

    def __iter__(self):
        with open(self.filename, "rb") as file:
            while True:
                data = file.read(self.chunksize)
                if not data:
                    sys.stderr.write("\n")
                    break
                self.readsofar += len(data)
                percent = self.readsofar * 1e2 / self.totalsize
                print(
                    f"Uploading {self.report_name} {percent}%",
                )

                # bg_blender.progress('uploading %s' % self.report_name, percent)
                # sys.stderr.write("\r{percent:3.0f}%".format(percent=percent))
                yield data

    def __len__(self):
        return self.totalsize


def upload_file(upload_data, f):
    headers = utils.get_headers(upload_data['token'])
    version_id = upload_data['id']
    print(f"\n----> UPLOADING {f['type']} {os.path.basename(f['file_path'])}")

    upload_info = {
        "assetId": version_id,
        "fileType": f["type"],
        "fileIndex": f["index"],
        "originalFilename": os.path.basename(f["file_path"]),
    }
    print(f" -  data:{upload_info}")
    
    upload_create_url = paths.get_api_url() + '/uploads/'
    upload = requests.post(upload_create_url, json=upload_info, headers=headers, verify=True)

    upload = upload.json()

    chunk_size = 1024 * 1024 * 2
    # utils.pprint(upload)
    # file gets uploaded here:
    # s3 upload is now the only option
    for a in range(0, 5):
        try:
            session = requests.Session()
            session.trust_env = True
            upload_response = session.put(
                upload['s3UploadUrl'],
                data=upload_in_chunks(f['file_path'],
                chunk_size, f['type']),
                stream=True,
                verify=True
                )

            if 250 > upload_response.status_code > 199:
                upload_done_url = (
                    paths.get_api_url()
                    + "/uploads_s3/"
                    + upload["id"]
                    + "/upload-file/"
                )
                upload_response = requests.post(
                    upload_done_url, headers=headers, verify=True
                )
                # print(upload_response)
                # print(upload_response.text)
                print(
                    f"Finished file upload: {os.path.basename(f['file_path'])}",
                )
                return True
            else:
                message = f"Upload failed, retry. File : {f['type']} {os.path.basename(f['file_path'])}"
                print(message)

        except Exception as e:
            print(e)
            message = f"Upload failed, retry. File : {f['type']} {os.path.basename(f['file_path'])}"
            print(message)
            import time

            time.sleep(1)

            # confirm single file upload to bkit server
    return False


def upload_files(upload_data, files):
    """uploads several files in one run"""
    uploaded_all = True
    for f in files:
        uploaded = upload_file(upload_data, f)
        if not uploaded:
            uploaded_all = False
        print(f"Uploaded all files for asset {upload_data['displayName']}")
    return uploaded_all


def upload_resolutions(files, asset_data, api_key=""):
    upload_data = {
        "name": asset_data["name"],
        "displayName": asset_data["displayName"],
        "token": api_key,
        "id": asset_data["id"],
    }

    uploaded = upload_files(upload_data, files)
    if uploaded:
        print("upload finished successfully")
    else:
        print("upload failed.")


def get_individual_parameter(asset_id="", param_name="", api_key=""):
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    r = requests.get(url, headers=headers)  # files = files,
    parameter = r.json()
    print(url)
    return parameter


def patch_individual_parameter(asset_id="", param_name="", param_value="", api_key=""):
    # changes individual parameter in the parameters dictionary of the assets
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    metadata_dict = {"value": param_value}
    print(url)
    r = requests.put(
        url, json=metadata_dict, headers=headers, verify=True
    )  # files = files,
    print(r.text)
    print(r.status_code)
    if r.status_code == 200 or r.status_code == 201:
        return True
    else:
        return False

def delete_individual_parameter(asset_id="", param_name="", param_value="", api_key=""):
    # delete the parameter from the asset
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    metadata_dict = {"value": param_value}
    print(url)
    r = requests.delete(
        url, json=metadata_dict, headers=headers, verify=True
    )  # files = files,
    print(r.text)
    print(r.status_code)
    if r.status_code == 200 or r.status_code == 201 or r.status_code == 204:
        return True
    else:
        return False


def patch_asset_empty(asset_id, api_key):
    """
    This function patches the asset for the purpose of it getting a reindex.
    Should be removed once this is fixed on the server and
    the server is able to reindex after uploads of resolutions
    Returns
    -------
    """
    upload_data = {}
    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    headers = utils.get_headers(api_key)
    print("patching asset with empty data")
    try:
        r = requests.patch(
            url, json=upload_data, headers=headers, verify=True
        )  # files = files,
    except requests.exceptions.RequestException as e:
        print(e)
        return {"CANCELLED"}
    print("patched asset with empty data")
    return {"FINISHED"}


def upload_asset_metadata(upload_data, api_key):
    url = f"{paths.get_api_url()}/assets/"
    headers = utils.get_headers(api_key)
    print("uploading new asset metadata")
    try:
        r = requests.post(
            url, json=upload_data, headers=headers, verify=True
        )  # files = files,
        print(r.text)
        # result should be json
        result = r.json()
        print(result)
        return result
    except requests.exceptions.RequestException as e:
        print(e)
        return {"CANCELLED"}


def patch_asset_metadata(asset_id, api_key, data={}):
    print("patching asset metadata")

    headers = utils.get_headers(api_key)

    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    print(url)
    r = requests.patch(url, json=data, headers=headers, verify=True)  # files = files,
    print(r.text)


def mark_for_thumbnail(
    asset_id: str,
    api_key: str,
    # Common parameters
    use_gpu: bool = None,
    samples: int = None,
    resolution: int = None,
    denoising: bool = None,
    background_lightness: float = None,
    # Model-specific parameters
    angle: str = None,  # DEFAULT, FRONT, SIDE, TOP
    snap_to: str = None,  # GROUND, WALL, CEILING, FLOAT
    # Material-specific parameters
    thumbnail_type: str = None,  # BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR
    scale: float = None,
    background: bool = None,
    adaptive_subdivision: bool = None,
) -> bool:
    """Mark an asset for thumbnail regeneration.
    
    This function creates a JSON with thumbnail parameters and stores it in the
    markThumbnailRender parameter of the asset. Only non-None parameters will be included.
    
    Args:
        asset_id (str): The ID of the asset to update
        api_key (str): BlenderKit API key
        use_gpu (bool, optional): Use GPU for rendering
        samples (int, optional): Number of render samples
        resolution (int, optional): Resolution of render
        denoising (bool, optional): Use denoising
        background_lightness (float, optional): Background lightness (0-1)
        angle (str, optional): Camera angle for models (DEFAULT, FRONT, SIDE, TOP)
        snap_to (str, optional): Object placement for models (GROUND, WALL, CEILING, FLOAT)
        thumbnail_type (str, optional): Type of material preview (BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR)
        scale (float, optional): Scale of preview object for materials
        background (bool, optional): Use background for transparent materials
        adaptive_subdivision (bool, optional): Use adaptive subdivision for materials
        
    Returns:
        bool: True if successful, False otherwise
    """
    # Build parameters dict with only non-None values
    params = {}
    
    # Common parameters
    if use_gpu is not None:
        params['thumbnail_use_gpu'] = use_gpu
    if samples is not None:
        params['thumbnail_samples'] = samples
    if resolution is not None:
        params['thumbnail_resolution'] = resolution
    if denoising is not None:
        params['thumbnail_denoising'] = denoising
    if background_lightness is not None:
        params['thumbnail_background_lightness'] = background_lightness
        
    # Model-specific parameters
    if angle is not None:
        params['thumbnail_angle'] = angle
    if snap_to is not None:
        params['thumbnail_snap_to'] = snap_to
        
    # Material-specific parameters
    if thumbnail_type is not None:
        params['thumbnail_type'] = thumbnail_type
    if scale is not None:
        params['thumbnail_scale'] = scale
    if background is not None:
        params['thumbnail_background'] = background
    if adaptive_subdivision is not None:
        params['thumbnail_adaptive_subdivision'] = adaptive_subdivision
    
    try:
        # Convert parameters to JSON string
        params_json = json.dumps(params)
        
        # Update the asset's markThumbnailRender parameter
        return patch_individual_parameter(
            asset_id=asset_id,
            param_name='markThumbnailRender',
            param_value=params_json,
            api_key=api_key
        )
    except Exception as e:
        print(f"Error marking asset for thumbnail: {e}")
        return False
