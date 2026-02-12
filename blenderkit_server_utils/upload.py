"""Upload helpers for BlenderKit API and S3-compatible storage.

Typed utilities to stream files in chunks and push uploads via the BlenderKit API,
including convenience wrappers for parameters and metadata updates.
"""

import json
import os
import random
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
RATE_LIMIT_STATUS_CODE = 429
RATE_LIMIT_MAX_RETRIES = 8
RATE_LIMIT_BASE_BACKOFF_SECONDS = 5
RATE_LIMIT_MIN_BACKOFF_SECONDS = 2
RATE_LIMIT_MAX_BACKOFF_SECONDS = 60
RATE_LIMIT_JITTER_MAX_SECONDS = 1


def _get_retry_after_seconds(response: requests.Response) -> int | None:
    """Parse a Retry-After header into seconds.

    Args:
        response: HTTP response object to inspect for retry headers.

    Returns:
        Number of seconds to wait before retrying, or None if not available.
    """
    retry_after_value = response.headers.get("Retry-After")
    if not retry_after_value:
        return None
    try:
        retry_after_seconds = int(retry_after_value)
    except ValueError:
        return None
    return retry_after_seconds


def _request_with_rate_limit_retry(
    method: str,
    url: str,
    *,
    max_retries: int = RATE_LIMIT_MAX_RETRIES,
    base_backoff_seconds: int = RATE_LIMIT_BASE_BACKOFF_SECONDS,
    max_backoff_seconds: int = RATE_LIMIT_MAX_BACKOFF_SECONDS,
    **kwargs: Any,
) -> requests.Response | None:
    """Send a request and retry if rate limited.

    Args:
        method: HTTP method name (GET, POST, PATCH, PUT, DELETE).
        url: Target URL for the request.
        max_retries: Maximum number of retry attempts when rate limited.
        base_backoff_seconds: Base delay for exponential backoff.
        max_backoff_seconds: Maximum delay between retries.
        **kwargs: Additional request arguments passed to requests.request.

    Returns:
        Response object if the request was sent successfully, otherwise None.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            response = requests.request(method, url, **kwargs)  # noqa: S113
        except requests.exceptions.RequestException:
            logger.exception("Request failed for %s %s", method, url)
            return None

        if response.status_code != RATE_LIMIT_STATUS_CODE:
            return response

        if attempt > max_retries:
            logger.warning("Rate limit retry budget exhausted for %s %s", method, url)
            return response

        retry_after_seconds = _get_retry_after_seconds(response)
        if retry_after_seconds is None:
            retry_after_seconds = min(
                base_backoff_seconds * (2 ** (attempt - 1)),
                max_backoff_seconds,
            )
        else:
            retry_after_seconds = min(retry_after_seconds, max_backoff_seconds)

        retry_after_seconds = max(retry_after_seconds, RATE_LIMIT_MIN_BACKOFF_SECONDS)
        retry_after_seconds += random.uniform(0, RATE_LIMIT_JITTER_MAX_SECONDS)  # noqa: S311

        logger.warning(
            "Rate limited (429) for %s %s, retrying in %s seconds (attempt %s/%s)",
            method,
            url,
            retry_after_seconds,
            attempt,
            max_retries,
        )
        time.sleep(retry_after_seconds)


class UploadInChunks:
    """Iterator that yields a file in chunks for streaming uploads."""

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

    Args:
        upload_data: Dictionary with upload context, including API token and asset ID.
        f: Dictionary describing the file to upload.

    Returns:
        True on success, False otherwise.

    Hint:
        Expects ``upload_data`` to contain ``token`` and ``id``; ``f`` should have
        ``type``, ``index``, and ``file_path`` keys.

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
        except requests.exceptions.RequestException:
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

    Args:
        upload_data: Dictionary with upload context, including API token and asset ID.
        files: Iterable of file descriptors with keys: type, index, file_path.

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
    response = _request_with_rate_limit_retry(
        "GET",
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        return {}
    parameter = response.json()
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
    response = _request_with_rate_limit_retry(
        "PUT",
        url,
        json=metadata_dict,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        return False
    logger.debug("PATCH response text: %s", response.text)
    logger.info("PATCH status code: %s", response.status_code)
    ok = response.status_code in SUCCESS_STATUS_CODES
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
    response = _request_with_rate_limit_retry(
        "DELETE",
        url,
        json=metadata_dict,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        return False
    logger.debug("DELETE response text: %s", response.text)
    logger.info("DELETE status code: %s", response.status_code)
    ok = response.status_code in SUCCESS_STATUS_CODES_WITH_NO_CONTENT
    return ok


def patch_asset_empty(asset_id: str, api_key: str):
    """Patch the asset with an empty payload to trigger reindex.

    Should be removed once the server reindexes automatically after resolution uploads.
    """
    upload_data = {}
    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    headers = utils.get_headers(api_key)
    logger.info("Patching asset %s with empty data (reindex trigger)", asset_id)
    response = _request_with_rate_limit_retry(
        "PATCH",
        url,
        json=upload_data,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        logger.exception("Patch asset (empty) request failed for %s", asset_id)
        return {"CANCELLED"}
    logger.info("Patch asset empty status code: %s", response.status_code)
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
    response = _request_with_rate_limit_retry(
        "POST",
        url,
        json=upload_data,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        logger.exception("Upload asset metadata request failed")
        return {"CANCELLED"}
    logger.debug("Asset metadata creation response: %s", response.text)
    result = response.json()
    logger.info("Created asset metadata id=%s", result.get("id"))
    return result


def patch_asset_metadata(asset_id: str, api_key: str, data: dict[str, Any] | None = None):
    """Patch an existing asset's metadata.

    Args:
        asset_id: Asset identifier.
        api_key: BlenderKit API key.
        data: Partial metadata to patch.

    Returns:
        None
    """
    if data is None:
        data = {}
    logger.info("Patching asset metadata %s", asset_id)

    headers = utils.get_headers(api_key)

    url = f"{paths.get_api_url()}/assets/{asset_id}/"
    logger.debug("PATCH asset url: %s", url)
    response = _request_with_rate_limit_retry(
        "PATCH",
        url,
        json=data,
        headers=headers,
        verify=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response is None:
        logger.exception("Patch asset metadata request failed for %s", asset_id)
        return
    logger.debug("PATCH asset metadata response: %s", response.text)


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
        asset_id: The ID of the asset to update
        api_key: BlenderKit API key
        use_gpu: Use GPU for rendering (optional)
        samples: Number of render samples (optional)
        resolution: Resolution of render (optional)
        denoising: Use denoising (optional)
        background_lightness: Background lightness (0-1) (optional)
        angle: Camera angle for models (DEFAULT, FRONT, SIDE, TOP) (optional)
        snap_to: Object placement for models (GROUND, WALL, CEILING, FLOAT) (optional)
        thumbnail_type: Type of material preview (BALL, BALL_COMPLEX, FLUID, CLOTH, HAIR) (optional)
        scale: Scale of preview object for materials (optional)
        background: Use background for transparent materials (optional)
        adaptive_subdivision: Use adaptive subdivision for materials (optional)

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
