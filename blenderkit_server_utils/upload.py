"""Upload helpers for BlenderKit API and S3-compatible storage.

Typed utilities to stream files in chunks and push uploads via the BlenderKit API,
including convenience wrappers for parameters and metadata updates.
"""

import json
import os
import sys
import time
from collections.abc import Iterable, Iterator
from typing import Any

import requests

from . import log, paths, utils

logger = log.create_logger(__name__)

# HTTP success range and request timeout
HTTP_STATUS_SUCCESS_MIN = 199
HTTP_STATUS_SUCCESS_MAX = 250
REQUEST_TIMEOUT_SECONDS = 30
SUCCESS_STATUS_CODES = {200, 201}
SUCCESS_STATUS_CODES_WITH_NO_CONTENT = {200, 201, 204}


class UploadInChunks:
    """Iterator that yields a file in chunks for streaming uploads.

    Args:
        filename: Path to the file being uploaded.
        chunksize: Size of each read chunk in bytes (default: 8 KiB).
        report_name: Label used in progress messages.

    Attributes:
        filename: File path.
        chunksize: Chunk size in bytes.
        totalsize: Total file size in bytes.
        readsofar: Bytes read so far.
        report_name: Display name for progress.
    """

    def __init__(self, filename: str, chunksize: int = 1 << 13, report_name: str = "file") -> None:
        self.filename: str = filename
        self.chunksize: int = chunksize
        self.totalsize: int = os.path.getsize(filename)
        self.readsofar: int = 0
        self.report_name: str = report_name

    def __iter__(self) -> Iterator[bytes]:
        with open(self.filename, "rb") as file:
            while True:
                data = file.read(self.chunksize)
                if not data:
                    sys.stderr.write("\n")
                    break
                self.readsofar += len(data)
                percent = self.readsofar * 1e2 / self.totalsize
                # Use debug level to avoid spamming info logs during large uploads.
                logger.debug("Uploading %s %.2f%%", self.report_name, percent)
                yield data

    def __len__(self) -> int:
        return self.totalsize


def upload_file(upload_data: dict[str, Any], f: dict[str, Any]) -> bool:
    """Upload a single file to S3 and confirm it with the BlenderKit API.

    Expects ``upload_data`` to contain ``token`` and ``id``; ``f`` should have
    ``type``, ``index``, and ``file_path`` keys.

    Returns:
        True on success, False otherwise.
    """
    headers = utils.get_headers(upload_data["token"])
    version_id = upload_data["id"]
    logger.info("----> Uploading %s %s", f["type"], os.path.basename(f["file_path"]))

    upload_info = {
        "assetId": version_id,
        "fileType": f["type"],
        "fileIndex": f["index"],
        "originalFilename": os.path.basename(f["file_path"]),
    }
    logger.debug("Upload init payload: %s", upload_info)

    upload_create_url = paths.get_api_url() + "/uploads/"
    response = requests.post(
        upload_create_url,
        json=upload_info,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    upload = response.json()

    chunk_size = 1024 * 1024 * 2
    # s3 upload is now the only option
    for _ in range(5):
        try:
            session = requests.Session()
            session.trust_env = True
            upload_response = session.put(
                upload["s3UploadUrl"],
                data=UploadInChunks(
                    f["file_path"],
                    chunk_size,
                    f["type"],
                ),
                stream=True,
                verify=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            status_code = upload_response.status_code
            if HTTP_STATUS_SUCCESS_MIN < status_code < HTTP_STATUS_SUCCESS_MAX:
                upload_done_url = paths.get_api_url() + "/uploads_s3/" + upload["id"] + "/upload-file/"
                upload_response = requests.post(
                    upload_done_url,
                    headers=headers,
                    verify=True,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                logger.info(
                    "Finished file upload: %s",
                    os.path.basename(f["file_path"]),
                )
                return True
            message = f"Upload failed, retry. File : {f['type']} {os.path.basename(f['file_path'])}"
            logger.warning(message)
        except requests.exceptions.RequestException:  # noqa: PERF203
            logger.exception(
                "Upload attempt raised exception for file %s (%s)",
                f["file_path"],
                f["type"],
            )
            message = f"Upload failed, retry. File : {f['type']} {os.path.basename(f['file_path'])}"
            logger.warning(message)
            time.sleep(1)

            # confirm single file upload to bkit server
    return False


def upload_files(upload_data: dict[str, Any], files: Iterable[dict[str, Any]]) -> bool:
    """Upload several files in one run.

    Returns:
        True if all files uploaded successfully, False otherwise.
    """
    uploaded_all = True
    for f in files:
        uploaded = upload_file(upload_data, f)
        if not uploaded:
            uploaded_all = False
        logger.info(
            "Uploaded file type %s (index %s) for asset %s -> %s",
            f["type"],
            f["index"],
            upload_data["displayName"],
            "SUCCESS" if uploaded else "FAIL",
        )
    return uploaded_all


def upload_resolutions(files: Iterable[dict[str, Any]], asset_data: dict[str, Any], api_key: str = "") -> None:
    """Upload a collection of resolution files for an asset.

    Args:
        files: Iterable of file descriptors with keys: type, index, file_path.
        asset_data: Asset info dict with name, displayName, id.
        api_key: BlenderKit API key.
    """
    upload_data = {
        "name": asset_data["name"],
        "displayName": asset_data["displayName"],
        "token": api_key,
        "id": asset_data["id"],
    }

    uploaded = upload_files(upload_data, files)
    if uploaded:
        logger.info("Upload of resolutions finished successfully")
    else:
        logger.error("Upload of resolutions failed")


def get_individual_parameter(asset_id: str = "", param_name: str = "", api_key: str = "") -> dict[str, Any]:
    """Fetch a single parameter value for an asset.

    Args:
        asset_id: Asset identifier.
        param_name: Name of the parameter.
        api_key: BlenderKit API key.

    Returns:
        Parsed JSON response from the API for the parameter.
    """
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)  # files = files,
    parameter = r.json()
    logger.debug("GET parameter url: %s", url)
    return parameter


def patch_individual_parameter(
    asset_id: str = "",
    param_name: str = "",
    param_value: str = "",
    api_key: str = "",
) -> bool:
    """Patch a single parameter value for an asset.

    Args:
        asset_id: Asset identifier.
        param_name: Name of the parameter to update.
        param_value: New value to set (as stringified value).
        api_key: BlenderKit API key.

    Returns:
        True if the API responds with a success code, False otherwise.
    """
    # changes individual parameter in the parameters dictionary of the assets
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    metadata_dict = {"value": param_value}
    logger.debug("PATCH parameter url: %s", url)
    r = requests.put(
        url,
        json=metadata_dict,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )  # files = files,
    logger.debug("PATCH response text: %s", r.text)
    logger.info("PATCH status code: %s", r.status_code)
    ok = r.status_code in SUCCESS_STATUS_CODES
    return ok


def delete_individual_parameter(
    asset_id: str = "",
    param_name: str = "",
    param_value: str = "",
    api_key: str = "",
) -> bool:
    """Delete a single parameter from an asset.

    Args:
        asset_id: Asset identifier.
        param_name: Name of the parameter to delete.
        param_value: Optional current value (for auditing on server side).
        api_key: BlenderKit API key.

    Returns:
        True if the API responds with a success code, False otherwise.
    """
    # delete the parameter from the asset
    url = f"{paths.get_api_url()}/assets/{asset_id}/parameter/{param_name}/"
    headers = utils.get_headers(api_key)
    metadata_dict = {"value": param_value}
    logger.debug("DELETE parameter url: %s", url)
    r = requests.delete(
        url,
        json=metadata_dict,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )  # files = files,
    logger.debug("DELETE response text: %s", r.text)
    logger.info("DELETE status code: %s", r.status_code)
    ok = r.status_code in SUCCESS_STATUS_CODES_WITH_NO_CONTENT
    return ok


def patch_asset_empty(asset_id: str, api_key: str):
    """Patch the asset with an empty payload to trigger reindex.

    Should be removed once the server reindexes automatically after resolution uploads.
    """
    upload_data = {}
    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    headers = utils.get_headers(api_key)
    logger.info("Patching asset %s with empty data (reindex trigger)", asset_id)
    try:
        r = requests.patch(
            url,
            json=upload_data,
            headers=headers,
            verify=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )  # files = files,
    except requests.exceptions.RequestException:
        logger.exception("Patch asset (empty) request failed for %s", asset_id)
        return {"CANCELLED"}
    logger.info("Patch asset empty status code: %s", r.status_code)
    logger.info("Patched asset %s with empty data", asset_id)
    return {"FINISHED"}


def upload_asset_metadata(upload_data: dict[str, Any], api_key: str):
    """Upload metadata to create a new asset.

    Args:
        upload_data: JSON-serializable asset metadata.
        api_key: BlenderKit API key.

    Returns:
        Parsed JSON response from the API on success, or {"CANCELLED"} on error.
    """
    url = f"{paths.get_api_url()}/assets/"
    headers = utils.get_headers(api_key)
    logger.info("Uploading new asset metadata: %s", upload_data.get("displayName") or upload_data.get("name"))
    try:
        r = requests.post(
            url,
            json=upload_data,
            headers=headers,
            verify=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )  # files = files,
    except requests.exceptions.RequestException:
        logger.exception("Upload asset metadata request failed")
        return {"CANCELLED"}
    else:
        logger.debug("Asset metadata creation response: %s", r.text)
        result = r.json()
        logger.info("Created asset metadata id=%s", result.get("id"))
        return result


def patch_asset_metadata(asset_id: str, api_key: str, data: dict[str, Any] | None = None):
    """Patch an existing asset's metadata.

    Args:
        asset_id: Asset identifier.
        api_key: BlenderKit API key.
        data: Partial metadata to patch.
    """
    if data is None:
        data = {}
    logger.info("Patching asset metadata %s", asset_id)

    headers = utils.get_headers(api_key)

    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    logger.debug("PATCH asset url: %s", url)
    r = requests.patch(
        url,
        json=data,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )  # files = files,
    logger.debug("PATCH asset metadata response: %s", r.text)


def mark_for_thumbnail(  # noqa: C901, PLR0912, PLR0913
    asset_id: str,
    api_key: str,
    *,
    # Common parameters
    use_gpu: bool | None = None,
    samples: int | None = None,
    resolution: int | None = None,
    denoising: bool | None = None,
    background_lightness: float | None = None,
    # Model-specific parameters
    angle: str | None = None,  # DEFAULT, FRONT, SIDE, TOP
    snap_to: str | None = None,  # GROUND, WALL, CEILING, FLOAT
    # Material-specific parameters
    thumbnail_type: str | None = None,  # BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR
    scale: float | None = None,
    background: bool | None = None,
    adaptive_subdivision: bool | None = None,
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
        params["thumbnail_use_gpu"] = use_gpu
    if samples is not None:
        params["thumbnail_samples"] = samples
    if resolution is not None:
        params["thumbnail_resolution"] = resolution
    if denoising is not None:
        params["thumbnail_denoising"] = denoising
    if background_lightness is not None:
        params["thumbnail_background_lightness"] = background_lightness

    # Model-specific parameters
    if angle is not None:
        params["thumbnail_angle"] = angle
    if snap_to is not None:
        params["thumbnail_snap_to"] = snap_to

    # Material-specific parameters
    if thumbnail_type is not None:
        params["thumbnail_type"] = thumbnail_type
    if scale is not None:
        params["thumbnail_scale"] = scale
    if background is not None:
        params["thumbnail_background"] = background
    if adaptive_subdivision is not None:
        params["thumbnail_adaptive_subdivision"] = adaptive_subdivision

    try:
        # Convert parameters to JSON string
        params_json = json.dumps(params)

        # Update the asset's markThumbnailRender parameter
        result = patch_individual_parameter(
            asset_id=asset_id,
            param_name="markThumbnailRender",
            param_value=params_json,
            api_key=api_key,
        )
    except (TypeError, ValueError):
        logger.exception("Error marking asset %s for thumbnail", asset_id)
        return False
    else:
        return result
