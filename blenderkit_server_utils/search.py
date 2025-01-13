import requests
import math
import json
import os
import time

from . import utils, paths


def get_search_simple(
    parameters, filepath=None, page_size=100, max_results=100000000, api_key=""
):
    """
    Searches and returns the


    Parameters
    ----------
    parameters - dict of blenderkit elastic parameters
    filepath - a file to save the results. If None, results are returned
    page_size - page size for retrieved results
    max_results - max results of the search
    api_key - BlenderKit api key

    Returns
    -------
    Returns search results as a list, and optionally saves to filepath

    """
    results = get_search_without_bullshit(
        parameters, page_size=page_size, max_results=max_results, api_key=api_key
    )
    if not filepath:
        return results

    with open(filepath, "w", encoding="utf-8") as s:
        json.dump(results, s, ensure_ascii=False, indent=4)
    print(f"retrieved {len(results)} assets from elastic search")
    return results


def get_search_without_bullshit(
    parameters, page_size=100, max_results=100000000, api_key=""
) -> list:
    headers = utils.get_headers(api_key)
    url = paths.get_api_url() + "/search/"
    requeststring = url + "?query="
    for p in parameters.keys():
        requeststring += f"+{p}:{parameters[p]}"

    requeststring += '&page_size=' + str(page_size)
    requeststring += '&dict_parameters=1'

    print(requeststring)
    for count in range(1,6): # retry 5 times
        try:
            response = requests.get(requeststring, headers=headers)  # , params = rparameters)
            response.raise_for_status()
            search_results = response.json()
            break # success, lets continue after the for loop
        except requests.exceptions.HTTPError as e:
            print(f"HTTP error occurred: {e} \nStatus Code: {response.status_code}, Response Content: {response.text}")
        except requests.exceptions.ConnectionError:
            print("Connection error occurred. Check network connection.")
        except requests.exceptions.Timeout:
            print("Request timed out. The server might be busy or unresponsive.")
        except requests.exceptions.JSONDecodeError as e:
            print(f"Failed to decode JSON. Response content is not valid JSON.\nResponse Content: {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"Unexpected request exception: {e}")
        if count == 5:
            raise RuntimeError("Could not get search results 5 times, retry depleted, probably broken connection.")

        delay = count**2 # retry fast, then slowdown
        print(f"retrying no. {count} in {delay} seconds")
        time.sleep(delay) 
        
    results = []
    results.extend(search_results['results'])
    page_index = 2
    page_count = math.ceil(search_results['count'] / page_size)
    while search_results.get('next') and len(results) < max_results:
        print(f'getting page {page_index} , total pages {page_count}')
        response = requests.get(search_results['next'], headers=headers)  # , params = rparameters)
        search_results = response.json()
        results.extend(search_results["results"])
        page_index += 1
    return results


def load_assets_list(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as s:
            assets = json.load(s)
    return assets


def filter_assets(filepath_source, filepath_target, name_strings):
    # Filter assets by will:
    assets = load_assets_list(filepath_source)
    nassets = []
    last_asset_with_resolution_index = -1  # should help toskip failed assets
    for a in assets:
        #        print(a['name'])
        for filter in name_strings:
            if a["name"].find(filter) > -1:
                print(a["name"])
                nassets.append(a)
    with open(filepath_target, "w") as s:
        json.dump(nassets, s)


def reduce_assets(filepath_source, filepath_target, count=20):
    # Filter assets by will:
    assets = load_assets_list(filepath_source)
    nassets = assets[:count]
    with open(filepath_target, "w") as s:
        json.dump(nassets, s)


def assets_from_last_generated(filepath_source, filepath_target, count=20):
    # Enables to skip all fails.
    assets = load_assets_list(filepath_source)
    nassets = []
    max_index = 0
    for i, a in enumerate(assets):
        print(a["name"])
        for f in a["files"]:
            if f["fileType"].find("resolution") > -1:
                max_index = i
    nassets = assets[max_index:]

    with open(filepath_target, "w") as s:
        json.dump(nassets, s)
