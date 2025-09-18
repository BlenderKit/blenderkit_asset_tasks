"""Cloudflare R2 storage helper utilities.

Typed helpers for basic S3-compatible operations against Cloudflare R2.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from . import log

logger = log.create_logger(__name__)

# S3 limit for batch delete operations
S3_DELETE_BATCH_LIMIT = 1000


class CloudflareStorage:
    """Client wrapper for Cloudflare R2 (S3-compatible) operations.

    Args:
        access_key: Cloudflare R2 access key.
        secret_key: Cloudflare R2 secret key.
        endpoint_url: URL endpoint for Cloudflare's S3-compatible storage.
        region_name: R2 region name. Defaults to "auto".
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint_url: str,
        region_name: str = "auto",
    ) -> None:
        self.session: boto3.session.Session = boto3.session.Session()
        # "client" is the boto3 S3 client; type hinted as Any for broad compatibility.
        self.client: Any = self.session.client(
            "s3",
            region_name=region_name,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def upload_file(self, file_name: str, bucket_name: str, object_name: str | None = None) -> bool:
        """Upload a file to an R2 bucket.

        Args:
            file_name: Local path of file to upload.
            bucket_name: Name of the destination bucket.
            object_name: Destination object key (path in bucket). Defaults to ``file_name``.

        Returns:
            True if the upload succeeded, False otherwise.
        """
        if object_name is None:
            object_name = file_name

        try:
            self.client.upload_file(file_name, bucket_name, object_name)
        except NoCredentialsError:
            logger.exception("Credentials not available for Cloudflare R2")
            return False
        except (ClientError, BotoCoreError, OSError):
            logger.exception(
                "Failed to upload '%s' to '%s/%s'",
                file_name,
                bucket_name,
                object_name,
            )
            return False
        else:
            logger.info("Uploaded '%s' to '%s/%s'", file_name, bucket_name, object_name)
            return True

    def list_all_folders(self, bucket_name: str) -> set[str]:
        """List all unique folder prefixes in the bucket.

        Args:
            bucket_name: Name of the R2 bucket.

        Returns:
            A set of all folder prefixes; empty on error or if none found.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        folders: set[str] = set()

        try:
            for page in paginator.paginate(Bucket=bucket_name, Delimiter="/"):
                for prefix in page.get("CommonPrefixes", []) or []:
                    folders.add(prefix["Prefix"])
        except (ClientError, BotoCoreError):
            logger.exception("Failed to list folders for bucket '%s'", bucket_name)

        return folders

    def list_folder_contents(self, bucket_name: str, folder_name: str) -> list[dict[str, Any]]:
        """List all objects within a folder prefix.

        Args:
            bucket_name: The name of the R2 bucket.
            folder_name: The prefix of the folder to list contents from. A trailing slash is added if missing.

        Returns:
            A list of object dictionaries under the given prefix; empty if none.
        """
        if not folder_name.endswith("/"):
            folder_name += "/"

        try:
            response = self.client.list_objects_v2(Bucket=bucket_name, Prefix=folder_name)
        except (ClientError, BotoCoreError):
            logger.exception(
                "Failed to list folder contents for bucket '%s' and prefix '%s'",
                bucket_name,
                folder_name,
            )
            return []
        return response.get("Contents", []) or []

    def folder_exists(self, bucket_name: str, folder_name: str) -> bool:
        """Check if a folder exists in a specified bucket.

        Args:
            bucket_name: Name of the bucket.
            folder_name: The folder name (prefix) to check for.

        Returns:
            True if the folder exists, False otherwise.
        """
        if not folder_name.endswith("/"):
            folder_name += "/"

        try:
            response = self.client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=folder_name,
                MaxKeys=1,
            )
        except (ClientError, BotoCoreError):
            logger.exception(
                "Failed to check folder existence for bucket '%s' and prefix '%s'",
                bucket_name,
                folder_name,
            )
            return False
        return bool(response.get("Contents"))

    def upload_folder(
        self,
        local_folder_path: str,
        bucket_name: str,
        cloudflare_folder_prefix: str = "",
    ) -> None:
        """Recursively upload a folder to R2 and write an index.json.

        Args:
            local_folder_path: The local path to the folder to upload.
            bucket_name: The Cloudflare R2 bucket to upload to.
            cloudflare_folder_prefix: The prefix (including folder structure) to store the files under.
        """
        uploaded_files: list[str] = []

        for root, _dirs, files in os.walk(local_folder_path):
            for filename in files:
                local_path = os.path.join(root, filename)
                relative_path = os.path.relpath(local_path, start=local_folder_path)
                cloudflare_object_name = os.path.join(cloudflare_folder_prefix, relative_path)
                cloudflare_object_name = cloudflare_object_name.replace("\\", "/")

                # Upload the file
                if self.upload_file(local_path, bucket_name, cloudflare_object_name):
                    uploaded_files.append(cloudflare_object_name)

        if not uploaded_files:
            logger.info("No files found to upload from '%s'", local_folder_path)
            return

        # Create a temporary index.json file with uploaded keys
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as index_file:
            json.dump(uploaded_files, index_file)
            index_file_path = index_file.name

        # Upload the index file
        cloudflare_object_name = os.path.join(cloudflare_folder_prefix, "index.json").replace("\\", "/")
        try:
            self.upload_file(index_file_path, bucket_name, cloudflare_object_name)
            logger.info("Uploaded index file to R2 at '%s'", cloudflare_object_name)
        finally:
            try:
                os.unlink(index_file_path)
            except OSError:
                logger.exception("Failed to remove temporary index file '%s'", index_file_path)

    def delete_folder_contents(self, bucket_name: str, folder_prefix: str) -> None:
        """Delete all contents under a folder prefix.

        Args:
            bucket_name: The name of the Cloudflare R2 bucket.
            folder_prefix: The prefix of the folder to delete contents from. A trailing slash is added if missing.
        """
        if not folder_prefix.endswith("/"):
            folder_prefix += "/"

        try:
            response = self.client.list_objects_v2(Bucket=bucket_name, Prefix=folder_prefix)
        except (ClientError, BotoCoreError):
            logger.exception("Failed to list objects for deletion under '%s'", folder_prefix)
            return
        objects = response.get("Contents", []) or []

        if not objects:
            logger.info("No objects found to delete in '%s'", folder_prefix)
            return

        delete_keys = {"Objects": [{"Key": obj["Key"]} for obj in objects]}
        try:
            delete_response = self.client.delete_objects(Bucket=bucket_name, Delete=delete_keys)
            logger.info("Deleted objects response: %s", delete_response)
        except (ClientError, BotoCoreError):
            logger.exception("Failed to delete objects under '%s'", folder_prefix)

    def delete_old_files(self, bucket_name: str, x_days: int) -> None:
        """Delete files older than ``x_days`` in the bucket.

        Args:
            bucket_name: The name of the Cloudflare R2 bucket.
            x_days: The age threshold in days for deleting files.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        delete_before_date = datetime.now(timezone.utc) - timedelta(days=x_days)

        delete_batch: dict[str, list[dict[str, str]]] = {"Objects": []}
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []) or []:
                if obj["LastModified"] < delete_before_date:
                    delete_batch["Objects"].append({"Key": obj["Key"]})

                    if len(delete_batch["Objects"]) >= S3_DELETE_BATCH_LIMIT:
                        try:
                            self.client.delete_objects(Bucket=bucket_name, Delete=delete_batch)
                        except (ClientError, BotoCoreError):
                            logger.exception("Batch delete failed (older-than) for '%s'", bucket_name)
                        finally:
                            delete_batch = {"Objects": []}

        if delete_batch["Objects"]:
            try:
                self.client.delete_objects(Bucket=bucket_name, Delete=delete_batch)
            except (ClientError, BotoCoreError):
                logger.exception("Final batch delete failed (older-than) for '%s'", bucket_name)
        logger.info("Old files deletion pass completed for '%s'", bucket_name)

    def delete_new_files(self, bucket_name: str, x_days: int) -> None:
        """Delete files newer than ``x_days`` in the bucket.

        Args:
            bucket_name: The name of the Cloudflare R2 bucket.
            x_days: The age threshold in days for deleting files.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        delete_after_date = datetime.now(timezone.utc) - timedelta(days=x_days)

        delete_batch: dict[str, list[dict[str, str]]] = {"Objects": []}
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []) or []:
                if obj["LastModified"] > delete_after_date:
                    delete_batch["Objects"].append({"Key": obj["Key"]})

                    if len(delete_batch["Objects"]) >= S3_DELETE_BATCH_LIMIT:
                        try:
                            self.client.delete_objects(Bucket=bucket_name, Delete=delete_batch)
                        except (ClientError, BotoCoreError):
                            logger.exception("Batch delete failed (newer-than) for '%s'", bucket_name)
                        finally:
                            delete_batch = {"Objects": []}

        if delete_batch["Objects"]:
            try:
                self.client.delete_objects(Bucket=bucket_name, Delete=delete_batch)
            except (ClientError, BotoCoreError):
                logger.exception("Final batch delete failed (newer-than) for '%s'", bucket_name)
        logger.info("New files deletion pass completed for '%s'", bucket_name)
