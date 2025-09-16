"""Script to trigger reindex of an asset on the BlenderKit server.

It uses ``asset_base_id`` to find the ``asset_id``, which is then used to trigger the reindex.
``asset_base_id`` is in the admin presented as "asset ID", ``asset_id`` is in admin presented
as "version ID".
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Configure basic logging only if root has no handlers (script usage)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

RESPONSE_OK = 200
REQUEST_TIMEOUT_SECONDS = 30
RETRIES_TOTAL = 5
RETRIES_BACKOFF_FACTOR = 1
RETRIES_STATUS_FORCELIST = (500, 502, 503, 504)


def _build_session() -> requests.Session:
    """Create a requests session with retry logic.

    Returns:
        Configured requests.Session instance.
    """
    session = requests.Session()
    retries = Retry(
        total=RETRIES_TOTAL,
        backoff_factor=RETRIES_BACKOFF_FACTOR,
        status_forcelist=RETRIES_STATUS_FORCELIST,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_asset_id(server: str, asset_base_id: str) -> str:
    """Get asset_id.

    (in admin also presented as 'version ID', in API as 'id') for the asset
    identified by asset_base_id (in admin presented as 'asset ID', in API as 'assetBaseId').

    Args:
        server (str): BlenderKit server URL.
        asset_base_id (str): Asset base ID.

    Returns:
        str: Asset ID (version ID).
    """
    url = f"{server}/api/v1/search?query=asset_base_id:{asset_base_id}"
    session = _build_session()
    headers: dict[str, str] = {"Accept": "application/json"}
    resp = session.get(url=url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("HTTP error while fetching asset ID for %s", asset_base_id)
        raise

    try:
        resp_json: dict[str, Any] = resp.json()
    except ValueError:
        logger.exception("Invalid JSON response when fetching asset ID for %s from %s", asset_base_id, url)
        raise

    count = resp_json.get("count")
    if count != 1:
        logger.error("Unexpected result count for %s: %s", asset_base_id, count)
        raise ValueError(f"Unexpected count of results: {count}")

    results = resp_json.get("results")
    if not isinstance(results, list) or len(results) == 0:
        logger.error("Results missing or empty in response: %s on %s", resp_json, url)
        raise ValueError("Results missing or empty in response")

    asset_id = results[0].get("id")
    if not asset_id:
        logger.error("Asset ID missing in response: %s on %s", resp_json, url)
        raise ValueError("Asset ID missing in response")

    asset_id_str: str = str(asset_id)
    return asset_id_str


def trigger_reindex(server: str, api_key: str, asset_id: str) -> None:
    """Trigger reindex of the asset by making an empty PATCH request to /api/v1/assets/{asset_id}.

    The asset is identified by asset_id (which in admin is presented as 'version ID', on API as 'id').
    We cannot use asset_base_id, so call the get_asset_id() to get the asset_id based on asset_base_id.

    Args:
        server (str): BlenderKit server URL.
        api_key (str): API key with permission to edit the asset.
        asset_id (str): Asset ID (version ID).

    Returns:
        None
    """
    url = f"{server}/api/v1/assets/{asset_id}"
    session = _build_session()
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = session.patch(url=url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    if resp.status_code != RESPONSE_OK:
        logger.error("HTTP response OK was expected, but got: %s", resp.status_code)
        try:
            resp.raise_for_status()
        except requests.RequestException:
            logger.exception("HTTP error while triggering reindex for %s", asset_id)
            raise
    else:
        logger.info("Asset reindex successfully scheduled for %s", asset_id)


if __name__ == "__main__":
    server = os.getenv("BLENDERKIT_SERVER")
    if server is None:
        raise RuntimeError("env variable BLENDERKIT_SERVER must be defined")

    api_key = os.getenv("BLENDERKIT_API_KEY")
    if api_key is None:
        raise RuntimeError("env variable BLENDERKIT_API_KEY must be defined")

    asset_base_id = os.getenv("ASSET_BASE_ID")
    if asset_base_id is None:
        raise RuntimeError("env variable ASSET_BASE_ID must be defined")

    asset_id = get_asset_id(server, asset_base_id)
    trigger_reindex(server, api_key, asset_id)
