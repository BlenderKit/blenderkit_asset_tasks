"""Google Drive helpers used by BlenderKit asset tasks.

This module provides Google Drive API helpers for authentication,
listing/checking files, ensuring folders, uploading files/folders, and
cleaning up empty folders. It follows BlenderKit's typing and docstring
conventions and avoids bare exceptions.
"""

from __future__ import annotations

import json
import os
from typing import Any

from google.oauth2.service_account import Credentials  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore
from googleapiclient.http import MediaFileUpload  # type: ignore

from . import log

# Full read/write access to the authenticated account
SCOPES = ["https://www.googleapis.com/auth/drive"]

logger = log.create_logger(__name__)


def init_drive() -> Any:
    """Initialize the Google Drive service using a service account key.

    The service account key is expected in the environment variable
    "GDRIVE_SERVICE_ACCOUNT_KEY" as a JSON string.

    Returns:
        A Google Drive API service instance.

    Raises:
        RuntimeError: If the service account key is missing or invalid JSON.
    """
    key_env = os.getenv("GDRIVE_SERVICE_ACCOUNT_KEY")
    if not key_env:
        raise RuntimeError("Missing GDRIVE_SERVICE_ACCOUNT_KEY environment variable")

    try:
        key_info = json.loads(key_env)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid JSON in GDRIVE_SERVICE_ACCOUNT_KEY") from exc

    creds = Credentials.from_service_account_info(key_info, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds)
    return service


def list_files_in_folder(service: Any, folder_id: str, page_size: int = 10) -> list[dict[str, Any]]:
    """List files in a specific Google Drive folder.

    Args:
        service: Google Drive API service instance.
        folder_id: ID of the folder to list.
        page_size: Maximum number of files to return.

    Returns:
        A list of file dicts with keys "id" and "name". Empty list on error.
    """
    try:
        results = (
            service.files()
            .list(
                pageSize=page_size,
                q=f"'{folder_id}' in parents and trashed=false",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="nextPageToken, files(id, name)",
            )
            .execute()
        )
    except HttpError:
        logger.exception("Failed to list files in folder '%s'", folder_id)
        return []

    items: list[dict[str, Any]] = results.get("files", [])
    for item in items:
        logger.info("Found file: %s (%s)", item.get("name"), item.get("id"))
    return items


def file_exists(service: Any, filename: str, folder_id: str) -> bool:
    """Check if a file exists in a specific Google Drive folder.

    Args:
        service: Google Drive API service instance.
        filename: Exact name of the file to look for.
        folder_id: ID of the folder to search.

    Returns:
        True if the file exists, False otherwise.
    """
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    try:
        results = (
            service.files()
            .list(
                q=query,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name)",
            )
            .execute()
        )
    except HttpError:
        logger.exception("Failed to check existence of '%s' in '%s'", filename, folder_id)
        return False
    items = results.get("files", [])
    exists = len(items) > 0
    return exists


def file_exists_partial(service: Any, partial_filename: str, folder_id: str) -> bool:
    """Check if a file whose name contains a substring exists in a folder.

    Args:
        service: Google Drive API service instance.
        partial_filename: Substring to look for in file names.
        folder_id: ID of the folder to search.

    Returns:
        True if any file name contains the substring, False otherwise.
    """
    query = f"name contains '{partial_filename}' and '{folder_id}' in parents and trashed=false"
    try:
        results = (
            service.files()
            .list(
                q=query,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name)",
            )
            .execute()
        )
    except HttpError:
        logger.exception(
            "Failed to check partial existence '%s' in '%s'",
            partial_filename,
            folder_id,
        )
        return False
    items = results.get("files", [])
    exists = len(items) > 0
    return exists


def ensure_folder_exists(service: Any, folder_name: str, parent_id: str = "", drive_id: str = "root") -> str:
    """Ensure a specific folder exists on Google Drive under a parent.

    Args:
        service: Google Drive API service instance.
        folder_name: Name of the folder to find or create.
        parent_id: Parent folder ID; empty string means root of the selected drive.
        drive_id: Drive ID for corpora="drive" queries; often "root" for My Drive.

    Returns:
        The ID of the located or created folder.
    """
    query = (
        f"name='{folder_name}' and '{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    try:
        response = (
            service.files()
            .list(
                q=query,
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name)",
            )
            .execute()
        )
    except HttpError:
        logger.exception("Failed to list folder '%s' under parent '%s'", folder_name, parent_id)
        raise

    items = response.get("files", [])
    if items:
        folder_id = items[0]["id"]
        return folder_id

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    try:
        folder = service.files().create(body=metadata, supportsAllDrives=True, fields="id").execute()
    except HttpError:
        logger.exception("Failed to create folder '%s' under parent '%s'", folder_name, parent_id)
        raise
    folder_id = folder["id"]
    return folder_id


def upload_file_to_folder(service: Any, file_path: str, folder_id: str) -> str:
    """Upload a local file to a Google Drive folder.

    Args:
        service: Google Drive API service instance.
        file_path: Path to the local file to upload.
        folder_id: ID of the destination folder.

    Returns:
        The ID of the newly created file on Google Drive.
    """
    file_metadata = {"name": os.path.basename(file_path), "parents": [folder_id]}
    media = MediaFileUpload(file_path)
    try:
        created = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
    except HttpError:
        logger.exception("Failed to upload file '%s' to folder '%s'", file_path, folder_id)
        raise
    file_id = created.get("id", "")
    logger.info("Uploaded file '%s' as id %s", file_path, file_id)
    return file_id


def upload_folder_to_drive(service: Any, folder_path: str, drive_folder_id: str, drive_id: str) -> None:
    """Upload a local folder and its contents to Google Drive.

    Args:
        service: Google Drive API service instance.
        folder_path: Path to the local folder to upload.
        drive_folder_id: ID of the folder on Google Drive to upload into.
        drive_id: Drive ID used for folder creation queries.
    """
    drive_subfolder_id = ensure_folder_exists(
        service,
        os.path.basename(folder_path),
        parent_id=drive_folder_id,
        drive_id=drive_id,
    )

    try:
        entries = os.listdir(folder_path)
    except OSError:
        logger.exception("Failed to list local folder '%s'", folder_path)
        raise

    for item in entries:
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path):
            upload_file_to_folder(service, item_path, drive_subfolder_id)
        elif os.path.isdir(item_path):
            upload_folder_to_drive(service, item_path, drive_subfolder_id, drive_id)


def delete_empty_folders(service: Any, folder_id: str, *, recursive: bool = True) -> None:
    """Delete all empty folders within the specified Google Drive folder.

    Args:
        service: Google Drive API service instance.
        folder_id: Google Drive folder ID to check for empty subfolders.
        recursive: When True, also check and delete empty subfolders inside non-empty folders.
    """

    def get_subfolders(service_in: Any, parent_id: str) -> list[dict[str, Any]]:
        """Retrieve subfolders using pagination.

        Args:
            service_in: Google Drive API service instance.
            parent_id: Parent folder ID.

        Returns:
            A list of folder dicts with id and name keys.
        """
        subfolders: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            try:
                response = (
                    service_in.files()
                    .list(
                        q=(
                            "mimeType='application/vnd.google-apps.folder' "
                            f"and '{parent_id}' in parents and trashed=false"
                        ),
                        spaces="drive",
                        fields="nextPageToken, files(id, name)",
                        pageToken=page_token,
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            except HttpError:
                logger.exception("Failed to list subfolders for '%s'", parent_id)
                break
            subfolders.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return subfolders

    subfolders = get_subfolders(service, folder_id)

    for folder in subfolders:
        logger.info("Checking folder: %s (%s)", folder.get("name"), folder.get("id"))
        sub_query = f"'{folder['id']}' in parents and trashed=false"
        try:
            sub_response = (
                service.files()
                .list(
                    q=sub_query,
                    fields="files(id)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError:
            logger.exception("Failed to list folder contents for '%s'", folder.get("id"))
            continue

        if not sub_response.get("files"):
            try:
                service.files().delete(fileId=folder["id"], supportsAllDrives=True).execute()
                logger.info("Deleted empty folder: %s (%s)", folder.get("name"), folder.get("id"))
            except HttpError:
                logger.exception("Failed to delete empty folder '%s'", folder.get("id"))
        elif recursive:
            delete_empty_folders(service, folder["id"], recursive=True)
