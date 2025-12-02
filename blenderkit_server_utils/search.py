"""Search utilities for BlenderKit server API.

Provides helper functions to perform paginated searches, persist/restore
asset result lists, and apply basic filtering/reduction utilities.

Refactor goals implemented:
 - Added typing for public functions
 - Replaced bare prints with structured logging
 - Added robust retry logic with exponential-ish backoff (kept original behavior)
 - Narrowed exception handling; no broad bare `except` clauses
 - Docstrings updated to clearly state arguments and return types
 - No getattr() on Blender objects (not used in this module)
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from typing import Any

import requests

from . import exceptions, log, paths, utils

logger = log.create_logger(__name__)

# Constants
DEFAULT_PAGE_SIZE: int = 100
DEFAULT_MAX_RESULTS: int = 100_000_000
RETRY_ATTEMPTS: int = 5
RETRY_BASE_DELAY_S: float = 1.0  # delay grows quadratically by count**2

MAX_ELASTICSEARCH_RESULTS = 10_000  # Elasticsearch hard limit


def get_search_simple(  # noqa: PLR0913
    parameters: dict[str, Any],
    filepath: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_results: int = DEFAULT_MAX_RESULTS,
    api_key: str = "",
    custom_tokens: list[str] | None = None,
    *,
    early_exit: bool = True,
) -> list[dict[str, Any]]:
    """Execute a search and optionally persist results to a JSON file.

    Args:
        parameters: Mapping of elastic parameter keys to values.
        filepath: Optional path to write the resulting list as JSON.
        page_size: Page size for paginated API retrieval.
        max_results: Maximum number of results to accumulate.
        api_key: Optional BlenderKit API key for authenticated queries.
        custom_tokens: Optional list of custom query tokens (e.g. ["mytoken:something",]).
        early_exit: If True, raise an exception if the search hits the max result limit.

    Returns:
        List of asset dictionaries returned by the search.
    """
    results = get_search_paginated(
        parameters=parameters,
        custom_tokens=custom_tokens,
        page_size=page_size,
        max_results=max_results,
        api_key=api_key,
        early_exit=early_exit,
    )
    if filepath:
        try:
            with open(filepath, "w", encoding="utf-8") as stream:
                json.dump(results, stream, ensure_ascii=False, indent=4)
            logger.info("Saved %d search results to %s", len(results), filepath)
        except OSError:
            logger.exception("Failed to write search results to %s", filepath)
    else:
        logger.debug("Returning %d search results (no filepath provided)", len(results))
    return results


def get_search_paginated(  # noqa: C901, PLR0912, PLR0913, PLR0915
    parameters: dict[str, Any],
    custom_tokens: list[str] | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_results: int = DEFAULT_MAX_RESULTS,
    api_key: str = "",
    *,
    early_exit: bool = False,
) -> list[dict[str, Any]]:
    """Low-level search helper performing paginated API requests.

    Args:
        parameters: Mapping of elastic query parameters.
        custom_tokens: Optional list of custom query tokens (e.g. ["mytoken:something",]).
        page_size: Number of results per page.
        max_results: Hard ceiling for accumulated results.
        api_key: Optional API key.
        early_exit: If True, raise an exception if the search hits the max result limit.

    Returns:
        List of result dictionaries.

    Raises:
        exceptions.SearchRequestRepeatError: If all retry attempts fail to get a valid response.
        exceptions.SearchResultLimitError: If early_exit is True and the search hits the max result limit.
    """
    headers = utils.get_headers(api_key)
    base_url = paths.get_api_url() + "/search/"
    # Construct query string
    request_url = base_url + "?query=" + "".join(f"+{k}:{v}" for k, v in parameters.items())

    # Append custom tokens if any
    if custom_tokens:
        request_url += "".join(f"+{t}" for t in custom_tokens)
    request_url += f"&page_size={page_size}&dict_parameters=1"

    logger.debug("Search request URL: %s", request_url)
    search_results: dict[str, Any] = {}
    response: requests.Response = None  # type: ignore[assignment]
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response: requests.Response = requests.get(request_url, headers=headers, timeout=30)
            response.raise_for_status()
            search_results = response.json()
            logger.info(
                "Search initial page retrieved (count=%s, page_size=%s)",
                search_results.get("count"),
                page_size,
            )
            if early_exit and search_results.get("count") == MAX_ELASTICSEARCH_RESULTS:
                raise exceptions.SearchResultLimitError(
                    f"Search hit maximum result limit of {MAX_ELASTICSEARCH_RESULTS}.",
                )
            break
        except requests.exceptions.HTTPError:
            status = getattr(response, "status_code", "?")
            hdr_keys = ("Content-Type", "Content-Length", "CF-Ray", "X-Request-Id", "Date")
            hdrs = {k: response.headers.get(k) for k in hdr_keys if hasattr(response, "headers")}
            logger.warning(
                "HTTP error on search attempt %d/%d (status=%s) \nURL=%s \nHeaders=%s",
                attempt,
                RETRY_ATTEMPTS,
                status,
                request_url,
                hdrs,
                exc_info=True,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            logger.warning(
                "Connection/timeout error on search attempt %d/%d",
                attempt,
                RETRY_ATTEMPTS,
                exc_info=True,
            )
        except (ValueError, requests.exceptions.JSONDecodeError):
            logger.warning("JSON decode error on search attempt %d/%d", attempt, RETRY_ATTEMPTS, exc_info=True)
        except requests.exceptions.RequestException:
            logger.warning("Generic request exception on attempt %d/%d", attempt, RETRY_ATTEMPTS, exc_info=True)

        if attempt == RETRY_ATTEMPTS:
            raise exceptions.SearchRequestRepeatError(
                f"Could not get search results after {RETRY_ATTEMPTS} attempts; connection or server issue.",
            )
        delay = attempt**2 * RETRY_BASE_DELAY_S
        logger.info("Retrying search attempt %d in %.1f seconds", attempt + 1, delay)
        time.sleep(delay)

    if not search_results:
        # Defensive: should not happen due to raise above
        return []

    results: list[dict[str, Any]] = list(search_results.get("results", []))
    page_index = 2
    total_count = int(search_results.get("count", len(results)))
    page_count = (total_count + page_size - 1) // page_size if page_size else 1

    while search_results.get("next") and len(results) < max_results:
        next_url = search_results["next"]
        logger.debug("Fetching page %d/%d: %s", page_index, page_count, next_url)
        # Add retries for pagination similar to the initial page request
        pagination_ok = False
        response = None  # type: ignore[assignment]
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                response = requests.get(next_url, headers=headers, timeout=30)
                response.raise_for_status()
                search_results = response.json()
                pagination_ok = True
                break
            except requests.exceptions.HTTPError:
                status = getattr(response, "status_code", "?")
                logger.warning(
                    "Pagination HTTP error on page %d attempt %d/%d (status=%s) \nURL=%s",
                    page_index,
                    attempt,
                    RETRY_ATTEMPTS,
                    status,
                    next_url,
                    exc_info=True,
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                logger.warning(
                    "Pagination connection/timeout error on page %d attempt %d/%d \nURL=%s",
                    page_index,
                    attempt,
                    RETRY_ATTEMPTS,
                    next_url,
                    exc_info=True,
                )
            except (ValueError, requests.exceptions.JSONDecodeError):
                # Response received but JSON was invalid or unexpected
                logger.warning(
                    "Pagination JSON decode error on page %d attempt %d/%d \nURL=%s",
                    page_index,
                    attempt,
                    RETRY_ATTEMPTS,
                    next_url,
                    exc_info=True,
                )
            except requests.exceptions.RequestException:
                logger.warning(
                    "Pagination generic request exception on page %d attempt %d/%d \nURL=%s",
                    page_index,
                    attempt,
                    RETRY_ATTEMPTS,
                    next_url,
                    exc_info=True,
                )

            if attempt == RETRY_ATTEMPTS:
                logger.error("Pagination request failed at page %d after %d attempts", page_index, RETRY_ATTEMPTS)
                break
            delay = attempt**2 * RETRY_BASE_DELAY_S
            logger.info("Retrying pagination page %d attempt %d in %.1f seconds", page_index, attempt + 1, delay)
            time.sleep(delay)

        if not pagination_ok:
            # Diagnostic fallback: if a single-item page fails with dict_parameters=1,
            # try again without dict_parameters to identify the problematic asset id.
            if page_size == 1 and isinstance(next_url, str) and "dict_parameters=1" in next_url:
                alt_url = next_url.replace("dict_parameters=1", "dict_parameters=0")
                try:
                    diag_resp = requests.get(alt_url, headers=headers, timeout=30)
                    diag_resp.raise_for_status()
                    alt_json = diag_resp.json()
                    alt_results = alt_json.get("results", []) if isinstance(alt_json, dict) else []
                    if alt_results:
                        suspect = alt_results[0]
                        sid = suspect.get("id")
                        name = suspect.get("name")
                        logger.error(
                            "Suspect asset causing 500 with dict_parameters=1 at page %d: id=%s name=%s URL=%s",
                            page_index,
                            sid,
                            name,
                            next_url,
                        )
                    else:
                        logger.error(
                            "Fallback without dict_parameters returned no results at page %d (URL=%s)",
                            page_index,
                            alt_url,
                        )
                except requests.RequestException:
                    logger.exception(
                        "Fallback without dict_parameters also failed for page %d (URL=%s)",
                        page_index,
                        alt_url,
                    )
                except (ValueError, requests.exceptions.JSONDecodeError):
                    logger.exception(
                        "Fallback without dict_parameters returned invalid JSON for page %d (URL=%s)",
                        page_index,
                        alt_url,
                    )
            break

        results.extend(search_results.get("results", []))
        page_index += 1

    if len(results) > max_results:
        results = results[:max_results]
    logger.info("Accumulated %d/%d results (max=%d)", len(results), total_count, max_results)
    return results


def load_assets_list(filepath: str) -> list[dict[str, Any]]:
    """Load a JSON list of asset dicts from a file.

    Args:
        filepath: Path to the JSON file created by a prior search.

    Returns:
        List of asset dictionaries (empty list if file missing or invalid).
    """
    if not os.path.exists(filepath):
        logger.warning("Assets file not found: %s", filepath)
        return []
    try:
        with open(filepath, encoding="utf-8") as stream:
            data = json.load(stream)
            if isinstance(data, list):
                return data  # type: ignore[return-value]
            logger.error("Expected list in assets file %s, got %s", filepath, type(data).__name__)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load assets list from %s", filepath)
    return []


def filter_assets(
    filepath_source: str,
    filepath_target: str,
    name_strings: Sequence[str],
) -> list[dict[str, Any]]:
    """Filter assets whose name contains any of the provided substrings.

    Args:
        filepath_source: Source JSON list file.
        filepath_target: Destination file to write filtered subset.
        name_strings: Iterable of substrings to match (case-sensitive).

    Returns:
        The filtered list that was written.
    """
    assets = load_assets_list(filepath_source)
    filtered: list[dict[str, Any]] = []
    for asset in assets:
        name = str(asset.get("name", ""))
        if any(sub in name for sub in name_strings):
            logger.debug("Matched asset name: %s", name)
            filtered.append(asset)
    try:
        with open(filepath_target, "w", encoding="utf-8") as stream:
            json.dump(filtered, stream, ensure_ascii=False, indent=2)
        logger.info("Wrote %d filtered assets to %s", len(filtered), filepath_target)
    except OSError:
        logger.exception("Failed writing filtered assets to %s", filepath_target)
    return filtered


def reduce_assets(filepath_source: str, filepath_target: str, count: int = 20) -> list[dict[str, Any]]:
    """Take only the first N assets and persist them.

    Args:
        filepath_source: Source JSON asset list path.
        filepath_target: Destination path for reduced list.
        count: Number of leading assets to keep.

    Returns:
        The reduced list.
    """
    assets = load_assets_list(filepath_source)
    reduced = assets[:count]
    try:
        with open(filepath_target, "w", encoding="utf-8") as stream:
            json.dump(reduced, stream, ensure_ascii=False, indent=2)
        logger.info("Wrote %d reduced assets to %s", len(reduced), filepath_target)
    except OSError:
        logger.exception("Failed writing reduced assets to %s", filepath_target)
    return reduced


def assets_from_last_generated(
    filepath_source: str,
    filepath_target: str,
) -> list[dict[str, Any]]:
    """Return assets from the last one that already has a resolution file onward.

    Finds the highest index whose files contain a 'resolution' fileType substring and
    returns the slice from that index onward, writing it to ``filepath_target``.

    Args:
        filepath_source: Source JSON asset list path.
        filepath_target: Destination path for the resulting slice.

    Returns:
        Sliced list of assets from last generated onward.
    """
    assets = load_assets_list(filepath_source)
    max_index = 0
    for i, asset in enumerate(assets):
        name = asset.get("name")
        logger.debug("Scanning asset %s (idx=%d) for existing resolutions", name, i)
        for f in asset.get("files", []):
            if isinstance(f, dict) and "fileType" in f and "resolution" in str(f["fileType"]):
                max_index = i
    sliced = assets[max_index:]
    try:
        with open(filepath_target, "w", encoding="utf-8") as stream:
            json.dump(sliced, stream, ensure_ascii=False, indent=2)
        logger.info(
            "Wrote %d assets (from index %d) with/after last resolution to %s",
            len(sliced),
            max_index,
            filepath_target,
        )
    except OSError:
        logger.exception("Failed writing assets slice to %s", filepath_target)
    return sliced
