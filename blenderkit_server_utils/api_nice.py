"""Code to interact with the BlenderKit API. Only nice code. No copied bullshit chaos."""

import json
import requests


def create_comment(
    comment: str,
    asset_base_id: str,
    api_key: str,
    reply_to_id: int = 0,
    server_url: str = "https://blenderkit.com",
) -> None:
    """
    Put a Comment on the asset identified by Asset_base_ID.
    API_key is needed to authenticate the account who comments.
    If no reply is needed, leave reply_to_id on default value.
    """

    get_url = f"{server_url}/api/v1/comments/asset-comment/{asset_base_id}/"
    post_url = f"{server_url}/api/v1/comments/comment/"
    headers = {
      "Accept": "application/json",
      "Content-Type": "application/json",
      "Authorization": f"Bearer {api_key}",
    }

    # 1) GET request to fetch form data (Timestamp and SecurityHash)
    try:
        resp_get = requests.get(get_url, headers=headers)
        resp_get.raise_for_status()
        comments_data = resp_get.json()
    except (requests.RequestException, ValueError) as e:
        raise Exception(f"Failed GET request for comment form data: {e}")

    try:
      timestamp = comments_data["form"]["timestamp"]
      security_hash = comments_data["form"]["securityHash"]
    except Exception as e:
      raise Exception(f"Could not get required data from asset-comment: {e}")

    # 2) Build the POST payload using form data from the GET response
    post_data = {
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
      "comment": comment
    }

    # 3) POST request to create the new comment
    try:
        resp_post = requests.post(post_url, headers=headers, data=json.dumps(post_data))
        resp_post.raise_for_status()
    except (requests.RequestException, ValueError) as e:
        raise Exception(f"Failed POST request to create comment: {e}")
