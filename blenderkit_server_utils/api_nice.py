"""Convenience helpers to interact with the BlenderKit API.

This module contains small, well-typed utilities for common API operations.
"""

from __future__ import annotations

import json

import requests

from . import log

logger = log.create_logger(__name__)


def create_comment(
    comment: str,
    asset_base_id: str,
    api_key: str,
    reply_to_id: int = 0,
    server_url: str = "https://blenderkit.com",
) -> None:
    """Create a comment on an asset.

    Args:
        comment: The comment text to post.
        asset_base_id: The base ID of the asset to comment on.
        api_key: API key used for authentication.
        reply_to_id: Optional ID of an existing comment to reply to. Default is 0 (no reply).
        server_url: Base URL of the BlenderKit server. Defaults to the production server.

    Raises:
        RuntimeError: If the request for form data or the comment creation fails, or if
            required fields cannot be extracted from the form response.
    """
    get_url = f"{server_url}/api/v1/comments/asset-comment/{asset_base_id}/"
    post_url = f"{server_url}/api/v1/comments/comment/"
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # 1) GET request to fetch form data (Timestamp and SecurityHash)
    try:
        resp_get = requests.get(get_url, headers=headers, timeout=30)
        resp_get.raise_for_status()
        comments_data = resp_get.json()
    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(f"Failed GET request for comment form data: {e}") from e

    try:
        timestamp = comments_data["form"]["timestamp"]
        security_hash = comments_data["form"]["securityHash"]
    except (KeyError, TypeError) as e:
        logger.debug("comments_data: %s", comments_data)
        raise RuntimeError(f"Could not get required data from asset-comment: {e}") from e

    # 2) Build the POST payload using form data from the GET response
    post_data: dict[str, object] = {
        "name": "",
        "email": "",
        "url": "",
        "followup": reply_to_id > 0,
        "reply_to": reply_to_id,
        "honeypot": "",
        "content_type": "assets.uuidasset",
        "object_pk": asset_base_id,
        "timestamp": timestamp,
        "security_hash": security_hash,
        "comment": comment,
    }

    # 3) POST request to create the new comment
    try:
        resp_post = requests.post(
            post_url,
            headers=headers,
            data=json.dumps(post_data),
            timeout=30,
        )
        resp_post.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed POST request to create comment: {e}") from e
