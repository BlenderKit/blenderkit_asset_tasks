import os
import requests
from requests.adapters import HTTPAdapter, Retry


def get_asset_id(server: str, asset_base_id: str) -> str:
    """Get asset_id (in admin also presented as 'version ID', in API as 'id') for the asset
    identified by asset_base_id (in admin presented as 'asset ID', in API as 'assetBaseId').
    """
    url = f"{server}/api/v1/search?query=asset_base_id:{asset_base_id}"
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[ 500, 502, 503, 504 ]
        )
    s.mount('https://', HTTPAdapter(max_retries=retries))
    headers = {"Accept": "application/json"}
    resp = s.get(url=url, headers=headers)
    resp_json = resp.json()
    
    count = resp_json.get("count")
    if count != 1:
        print(f"unexpected count of results: {count}")

    results = resp_json.get("results")
    if results is None:
        print(f"results not present in response: {resp_json} on {url}")
        exit(10)

    if len(results) == 0:
        print(f"results length is 0 in response: {resp_json} on {url}")
        exit(10)

    asset_id = results[0].get("id")
    if not asset_id:
        print(f"unexpected asset id: {asset_id} in response: {resp_json} on {url}")
        exit(10)

    return asset_id


def trigger_reindex(server: str, api_key: str, asset_id: str):
    """Trigger reindex of the asset by making an empty PATCH request to /api/v1/assets/{asset_id}.
    The asset is identified by asset_id (which in admin is presented as 'version ID', on API as 'id').
    We cannot use asset_base_id, so call the get_asset_id() to get the asset_id based on asset_base_id."""
    url = f"{server}/api/v1/assets/{asset_id}"
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[ 500, 502, 503, 504 ]
        )
    s.mount('https://', HTTPAdapter(max_retries=retries))
    headers = {
        "Accept": "application/json",
        "Authorization" : f"Bearer {api_key}",
    }
    resp = s.patch(url=url, headers=headers)
    if resp.status_code != 200:
        print(f"http response OK was expected, but we got: {resp.status_code}")
    else:
        print("Asset reindex successfully scheduled!")


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
