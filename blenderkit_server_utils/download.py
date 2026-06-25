"""Download utilities for BlenderKit assets.

This module provides helpers to resolve asset file URLs from the server and
download them to a local directory. Functions include path resolution,
existing-file checks, and a simple non-threaded downloader.

All network operations use the requests library with basic error handling,
and logging is used instead of print statements.
"""

from __future__ import annotations

import os
import zipfile
from typing import Any

import requests

from . import log, paths, utils
from .exceptions import UnsafeArchiveError

logger = log.create_logger(__name__)

SCENE_UUID = "5d22a2ce-7d4e-4500-9b1a-e5e79f8732c0"
HTTP_SUCCESS_MAX = 399
NAME_SLUG_MAX = 16
MIN_CONTENT_LENGTH = 1000
TWO_PATHS = 2
BLEND_FILETYPE = "blend"
ZIP_FILETYPE = "zip_file"
ARCHIVE_INNER_EXTENSIONS = (".blend", ".exr")


def server_2_local_filename(asset_data: dict[str, Any], filename: str) -> str:
    """Convert a server-side filename into a local filename.

    Delegates to :func:`paths.server_2_local_filename` so both the asset-name
    prefix and the server-supplied stem are sanitized identically (no '+',
    spaces, accents, etc.). The file extension is preserved.

    Args:
        asset_data: Asset metadata containing at least a "name".
        filename: Original filename from the server (e.g., from a URL).

    Returns:
        A sanitized local filename suitable for saving to disk.
    """
    return paths.server_2_local_filename(asset_data, filename)


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
    asset_folder_name = paths.safe_asset_folder_name(asset_data, max_name_length=NAME_SLUG_MAX)

    file_names: list[str] = []

    if not res_file:
        return file_names
    if res_file.get("url") is not None:
        logger.debug("Resolved file info entry: %s", res_file["url"])
        fn = paths.extract_filename_from_url(res_file["url"])  # type: ignore[index]
        n = server_2_local_filename(asset_data, fn)

        asset_folder_path = os.path.join(directory, asset_folder_name)

        if not paths.verify_path_creatable(asset_folder_path):
            logger.error(
                "Asset folder name %r is not creatable on this filesystem; skipping asset %s",
                asset_folder_name,
                asset_data.get("id"),
            )
            return file_names

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
        logger.warning("Could not remove asset directory '%s': %s", asset_dir, exc)


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
        logger.error("Missing URL for resolution '%s' in asset data", resolution)
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
        # Some assets are stored as a .zip archive (fileType='zip_file') instead of
        # a direct .blend. When a 'blend' was requested but missing, fall back to the
        # archive, download it, and extract the inner .blend/.exr file.
        if filetype == BLEND_FILETYPE and get_file_type(asset_data, ZIP_FILETYPE)[0] is not None:
            logger.info("No '%s' file for asset '%s'; falling back to zip archive", filetype, asset_data.get("name"))
            zip_path = download_and_extract_zip(asset_data, api_key=api_key, directory=directory)
            return zip_path
        logger.warning("Could not get URL for the asset '%s'", asset_data.get("name"))
        return None

    fpath = download_asset_file(asset_data, resolution=filetype, api_key=api_key, directory=directory)
    return fpath


def _is_within_directory(directory: str, target: str) -> bool:
    """Check whether a target path resolves to a location inside a directory.

    Args:
        directory: The base directory that should contain the target.
        target: The path to verify.

    Returns:
        True if the target is inside the directory, False otherwise.
    """
    abs_directory = os.path.abspath(directory)
    abs_target = os.path.abspath(target)
    common = os.path.commonpath([abs_directory, abs_target])
    is_inside = common == abs_directory
    return is_inside


def _safe_extract_zip(archive: zipfile.ZipFile, extract_dir: str) -> None:
    """Extract a zip archive while guarding against path traversal (Zip Slip).

    Args:
        archive: The opened zip archive.
        extract_dir: The directory into which members are extracted.

    Raises:
        UnsafeArchiveError: If any member would be written outside ``extract_dir``.
    """
    for member in archive.namelist():
        member_path = os.path.join(extract_dir, member)
        if not _is_within_directory(extract_dir, member_path):
            raise UnsafeArchiveError(f"Unsafe path in archive member: {member!r}")
    archive.extractall(extract_dir)


def _find_inner_asset_file(extract_dir: str) -> str | None:
    """Locate the single usable asset file (.blend or .exr) in an extracted archive.

    Args:
        extract_dir: Directory containing the extracted archive contents.

    Returns:
        Path to the inner asset file, preferring .blend over .exr, or None if none found.
    """
    matches: list[str] = []
    for root, _dirs, files in os.walk(extract_dir):
        matches.extend(os.path.join(root, name) for name in files if name.lower().endswith(ARCHIVE_INNER_EXTENSIONS))

    if not matches:
        logger.warning("No %s file found in extracted archive at %s", ARCHIVE_INNER_EXTENSIONS, extract_dir)
        return None

    # Prefer a .blend file when present, otherwise fall back to the first match (e.g. .exr).
    for path in matches:
        if path.lower().endswith(".blend"):
            return path

    inner_path = matches[0]
    return inner_path


def download_and_extract_zip(
    asset_data: dict[str, Any],
    api_key: str = "",
    directory: str | None = None,
) -> str | None:
    """Download a zip-archived asset and extract its inner .blend/.exr file.

    Args:
        asset_data: Asset metadata containing a 'zip_file' entry in its files.
        api_key: Authentication token for the API.
        directory: Optional base directory to place the downloaded archive.

    Returns:
        Path to the extracted .blend (preferred) or .exr file, or None on failure.
    """
    has_url = get_download_url(asset_data, SCENE_UUID, api_key, tcom=None, resolution=ZIP_FILETYPE)
    if not has_url:
        logger.warning("Could not get zip URL for the asset '%s'", asset_data.get("name"))
        return None

    zip_path = download_asset_file(asset_data, resolution=ZIP_FILETYPE, api_key=api_key, directory=directory)
    if not zip_path:
        logger.warning("Failed to download zip archive for asset '%s'", asset_data.get("name"))
        return None

    extract_dir = os.path.join(os.path.dirname(zip_path), "unzipped")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract_zip(archive, extract_dir)
    except (zipfile.BadZipFile, OSError, UnsafeArchiveError):
        logger.exception("Failed to extract zip archive '%s'", zip_path)
        return None

    inner_path = _find_inner_asset_file(extract_dir)
    return inner_path
