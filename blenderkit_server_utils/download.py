"""Download utilities for BlenderKit assets.

This module provides helpers to resolve asset file URLs from the server and
download them to a local directory. Functions include path resolution,
existing-file checks, and a simple non-threaded downloader.

All network operations use the requests library with basic error handling,
and logging is used instead of print statements.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from . import log, paths, utils

logger = log.create_logger(__name__)

SCENE_UUID = "5d22a2ce-7d4e-4500-9b1a-e5e79f8732c0"
HTTP_SUCCESS_MAX = 399
NAME_SLUG_MAX = 16
MIN_CONTENT_LENGTH = 1000
TWO_PATHS = 2


def server_2_local_filename(asset_data: dict[str, Any], filename: str) -> str:
    """Convert a server-side filename into a local filename.

    Removes known prefixes from server filenames and prefixes with a slugified
    asset name.

    Args:
        asset_data: Asset metadata containing at least a "name".
        filename: Original filename from the server (e.g., from a URL).

    Returns:
        A sanitized local filename suitable for saving to disk.
    """
    fn = filename.replace("blend_", "")
    fn = fn.replace("resolution_", "")
    n = paths.slugify(asset_data["name"]) + "_" + fn
    return n


def files_size_to_text(size: int) -> str:
    """Render a human-readable size string for a byte length.

    Args:
        size: File size in bytes.

    Returns:
        A short textual size such as "123KB" or "12.3MB".
    """
    fsmb = size / (1024 * 1024)
    fskb = size % 1024
    if fsmb == 0:
        text = f"{round(fskb)}KB"
        return text
    text = f"{round(fsmb, 1)}MB"
    return text


def get_file_type(asset_data: dict[str, Any], filetype: str = "blend") -> tuple[dict[str, Any] | None, str]:
    """Find the file entry for a specific type in the asset data.

    Iterates over asset_data["files"] and returns the one whose "fileType"
    equals the requested type.

    Args:
        asset_data: Asset metadata with a "files" list of dicts.
        filetype: Desired file type identifier (e.g., "blend", "gltf").

    Returns:
        A tuple of (file_dict_or_None, canonical_type_string).
    """
    for f in asset_data.get("files", []):
        if f.get("fileType") == filetype:
            orig: dict[str, Any] = f
            return orig, "blend"
    return None, filetype


def get_download_url(
    asset_data: dict[str, Any],
    scene_id: str,
    api_key: str,
    tcom: Any | None = None,
    resolution: str = "blend",
) -> bool | str:
    """Retrieve the download URL from the server for an asset.

    The server validates permissions and responds with a downloadable file path.
    On success, mutates the asset's selected file info with resolved URL and filename.

    Args:
        asset_data: Asset metadata.
        scene_id: Scene UUID used for server tracking.
        api_key: Authentication token for the API.
        tcom: Optional communication object with attributes 'error' and 'report'.
        resolution: File type key to resolve (legacy name "resolution").

    Returns:
        True on success; the string "Connection Error" on network failure; False on missing file type or HTTP error.
    """
    logger.debug("Requesting download URL for asset '%s'", asset_data.get("name"))

    headers = utils.get_headers(api_key)
    data = {"scene_uuid": scene_id}

    res_file_info, resolution = get_file_type(asset_data, resolution)
    if res_file_info is None:
        logger.warning("No file type '%s' found in asset data", resolution)
        if tcom is not None:
            tcom.error = True
            tcom.report = f"Missing file type: {resolution}"
        return False

    logger.debug("Resolved file info entry: %s", res_file_info)

    try:
        r = requests.get(res_file_info["downloadUrl"], params=data, headers=headers, timeout=30)
    except requests.RequestException:
        logger.exception("Network error while getting download URL")
        if tcom is not None:
            tcom.error = True
            tcom.report = "Connection Error"
        return "Connection Error"

    logger.debug("Server responded %s: %s", r.status_code, r.text[:200])

    if r.status_code <= HTTP_SUCCESS_MAX:
        payload = r.json()
        url = payload["filePath"]

        res_file_info["url"] = url
        res_file_info["file_name"] = paths.extract_filename_from_url(url)

        logger.info("Download URL resolved: %s", url)
        success: bool = True
        return success

    logger.warning("Failed to get download URL. Status: %s", r.status_code)
    if tcom is not None:
        tcom.error = True
        tcom.report = f"HTTP error: {r.status_code}"
    failure: bool = False
    return failure


def get_download_filepath(
    asset_data: dict[str, Any],
    resolution: str = "blend",
    directory: str | None = None,
) -> list[str]:
    """Compute local file paths where the asset may be stored.

    Will create the asset directory if it does not exist.

    Args:
        asset_data: Asset metadata.
        resolution: File type key (legacy "resolution").
        directory: Optional base directory; if None, uses global download dir by asset type.

    Returns:
        A list of candidate local file paths for the asset.
    """
    if directory is None:
        directory = paths.get_download_dir(asset_data["assetType"])  # type: ignore[index]

    res_file, resolution = get_file_type(asset_data, resolution)
    name_slug = paths.slugify(asset_data["name"])  # type: ignore[index]
    if len(name_slug) > NAME_SLUG_MAX:
        name_slug = name_slug[:NAME_SLUG_MAX]
    asset_folder_name = f"{name_slug}_{asset_data['id']}"  # type: ignore[index]

    file_names: list[str] = []

    if not res_file:
        return file_names
    if res_file.get("url") is not None:
        fn = paths.extract_filename_from_url(res_file["url"])  # type: ignore[index]
        n = server_2_local_filename(asset_data, fn)

        asset_folder_path = os.path.join(directory, asset_folder_name)

        if not os.path.exists(asset_folder_path):
            os.makedirs(asset_folder_path, exist_ok=True)

        file_name = os.path.join(asset_folder_path, n)
        file_names.append(file_name)

    logger.debug("Candidate file paths: %s", file_names)

    return file_names


def check_existing(asset_data: dict[str, Any], resolution: str = "blend", directory: str | None = None) -> bool:
    """Check if the asset file already exists on disk and sync between locations.

    If one of the two preferred locations exists, copy it to the other to keep them in sync.

    Args:
        asset_data: Asset metadata.
        resolution: File type key (legacy "resolution").
        directory: Optional base directory.

    Returns:
        True if the expected primary file exists.
    """
    fexists = False

    if asset_data.get("files") is None:
        # Compatibility: old asset data may not have a files structure.
        return False

    file_names = get_download_filepath(asset_data, resolution, directory=directory)
    logger.debug("Checking existing files: %s", file_names)
    if len(file_names) == TWO_PATHS:
        # If one exists and the other doesn't, copy to keep them in sync.
        if os.path.isfile(file_names[0]):  # and not os.path.isfile(file_names[1])
            utils.copy_asset(file_names[0], file_names[1])
        elif not os.path.isfile(file_names[0]) and os.path.isfile(file_names[1]):
            utils.copy_asset(file_names[1], file_names[0])

    if len(file_names) > 0 and os.path.isfile(file_names[0]):
        fexists = True
    return fexists


def delete_unfinished_file(file_name: str) -> None:
    """Delete an unfinished download and remove the directory if it becomes empty.

    Args:
        file_name: Path to the partially downloaded file.
    """
    try:
        os.remove(file_name)
    except FileNotFoundError:
        logger.debug("File already removed: %s", file_name)
    except OSError as exc:
        logger.warning("Failed to remove unfinished file '%s': %s", file_name, exc)

    asset_dir = os.path.dirname(file_name)
    try:
        if len(os.listdir(asset_dir)) == 0:
            os.rmdir(asset_dir)
    except FileNotFoundError:
        logger.debug("Asset directory already removed: %s", asset_dir)
    except OSError as exc:
        logger.debug("Could not remove asset directory '%s': %s", asset_dir, exc)


def download_asset_file(
    asset_data: dict[str, Any],
    resolution: str = "blend",
    api_key: str = "",
    directory: str | None = None,
) -> str | None:
    """Download a single asset file in a non-threaded manner.

    Args:
        asset_data: Asset metadata.
        resolution: File type key (legacy "resolution").
        api_key: Authentication token for the API.
        directory: Optional base directory to place the file.

    Returns:
        The path to the downloaded file, or None if download was canceled/failed.
    """
    file_names = get_download_filepath(asset_data, resolution, directory=directory)
    if len(file_names) == 0:
        return None

    file_name = file_names[0]

    if check_existing(asset_data, resolution=resolution, directory=directory):
        # File exists; return the path for further processing checks elsewhere.
        return file_name

    download_canceled = False

    headers = utils.get_headers(api_key)
    res_file_info, _resolution = get_file_type(asset_data, resolution)
    if res_file_info is None or "url" not in res_file_info:
        logger.warning("Missing URL for resolution '%s' in asset data", resolution)
        return None

    try:
        with requests.Session() as session, open(file_name, "wb") as f:
            logger.info("Downloading %s", file_name)
            response = session.get(res_file_info["url"], stream=True, headers=headers, timeout=(10, 300))
            response.raise_for_status()

            total_length_str = response.headers.get("Content-Length")
            total_length = int(total_length_str) if total_length_str is not None else None

            if total_length is None or total_length < MIN_CONTENT_LENGTH:  # no or too small content length
                download_canceled = True
                content_preview = response.content[:200]
                logger.warning("Canceling download; invalid Content-Length. Preview: %s", content_preview)
            else:
                dl = 0
                last_percent = 0
                for data in response.iter_content(chunk_size=4096 * 10):
                    dl += len(data)

                    if total_length:
                        fs_str = files_size_to_text(total_length)
                        percent = int(dl * 100 / total_length)
                        if percent > last_percent:
                            last_percent = percent
                            logger.info("Downloading %s %s %s%%", asset_data.get("name"), fs_str, percent)

                    f.write(data)
    except requests.RequestException:
        logger.exception("Network error during download")
        download_canceled = True

    if download_canceled:
        delete_unfinished_file(file_name)
        return None

    return file_name


def download_asset(
    asset_data: dict[str, Any],
    filetype: str = "blend",
    api_key: str = "",
    directory: str | None = None,
) -> str | None:
    """Download an asset (non-threaded) by first resolving its URL.

    Args:
        asset_data: Search result from Elastic or assets endpoints from API.
        filetype: Which of asset_data['files'] to download (e.g., 'blend', 'resolution_1K', 'gltf').
        api_key: Used for auth on the server API.
        directory: The path to which the file will be downloaded.

    Returns:
        Path to the resulting asset file, or None if asset isn't accessible/downloaded.
    """
    has_url = get_download_url(asset_data, SCENE_UUID, api_key, tcom=None, resolution=filetype)
    if not has_url:
        logger.warning("Could not get URL for the asset '%s'", asset_data.get("name"))
        return None

    fpath = download_asset_file(asset_data, resolution=filetype, api_key=api_key, directory=directory)
    return fpath
