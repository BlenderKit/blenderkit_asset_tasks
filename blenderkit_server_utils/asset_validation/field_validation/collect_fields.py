"""Simple collector script that would go through all assets and collects manufacturer tags.

Tags will be written to csv file in the current directory.
"""

from __future__ import annotations

import argparse
import calendar
import os
import sys
import tempfile
from datetime import UTC, datetime
from typing import Any

from blenderkit_server_utils import config, exceptions, log, search, utils

logger = log.create_logger(__name__)

c_root = os.path.dirname(os.path.abspath(__file__))

PAGE_SIZE_LIMIT = 100  # limit the number of assets returned in a single query
ASSET_TYPES = "model,material,hdr,scene,brush,printable,addon,render,nodegroup"

ELASTIC_LIMIT = 10000  # hard limit of elasticsearch, we need to do multiple queries if we want more assets

SEARCH_TIMEOUT = 1  # limit search requests to avoid spamming the server

utils.raise_on_missing_env_vars(
    [
        "BLENDERKIT_API_KEY",
    ],
)

# modify limits to prevent hammering server
search.RETRY_ATTEMPTS = 2
search.RETRY_BASE_DELAY_S = 0.1  # delay grows quadratically by count**2


def ensure_tag_csv_file(filepath: str) -> None:
    """Ensure the CSV file exists and has the correct header.

    Args:
        filepath: Path to the CSV file.
    """
    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(
                "asset_id|name|upload_date|manufacturer|designer|collection|year|tags|description|status|author_name|author_id\n",
            )


def _sanitize_text(value: Any) -> str:
    """Sanitize a text field for pipe-delimited CSV.

    - Converts None to empty string
    - Replaces newlines with spaces
    - Replaces the pipe '|' separator with a safe placeholder '__'
    """
    s = "" if value is None else str(value)
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ")
    s = s.replace("|", "__")
    return s


def _sanitize_tags_list(tags: list[str] | None) -> str:
    """Sanitize tags list and join with commas.

    Ensures no pipe characters remain in the resulting string.
    """
    if not tags:
        return ""
    return ",".join(_sanitize_text(t) for t in tags if t is not None)


def _validate_no_pipes(fields: list[str]) -> bool:
    """Ensure none of the fields contain the pipe separator after sanitization."""
    return all("|" not in f for f in fields)


def _parse_created(created: str) -> tuple[int, int | None, int | None]:
    """Parse a created string 'YYYY'|'YYYY-MM'|'YYYY-MM-DD' to a tuple.

    Raises ValueError on invalid input.
    """
    parts = created.split("-")
    len_y, len_ym, len_ymd = 1, 2, 3
    if len(parts) == len_y:
        return (int(parts[0]), None, None)
    if len(parts) == len_ym:
        return (int(parts[0]), int(parts[1]), None)
    if len(parts) == len_ymd:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"Invalid created value: {created}")


def load_existing_tags(filepath: str) -> dict[str, dict[str, Any]]:
    """Load existing tags from the CSV file to avoid duplicates.

    Output is a dictionary mapping asset IDs to their manufacturer tags.

    Args:
        filepath: Path to the CSV file.

    Returns:
        Dictionary mapping asset IDs to their manufacturer tags.
    """
    existing_tags = {}
    if os.path.exists(filepath):
        with open(filepath, encoding="utf-8") as f:
            # decode header
            f.seek(0)
            header = f.readline().strip().split("|")
            if header != [
                "asset_id",
                "name",
                "upload_date",
                "manufacturer",
                "designer",
                "collection",
                "year",
                "tags",
                "description",
                "status",
                "author_name",
                "author_id",
            ]:
                logger.error("Invalid header in %s", filepath)
                sys.exit(1)

            # Skip any additional header processing if needed
            # No need to call f.readline() again since we already read the header

            for line in f.readlines():
                tmp_data = {}
                parts = line.strip().split("|")
                for i, c_nm in enumerate(header):
                    tmp_data[c_nm] = parts[i]
                    if c_nm == "tags":
                        tmp_data[c_nm] = parts[i].split(",")
                existing_tags[parts[0]] = tmp_data

    return existing_tags


def tag_extraction(asset_data: dict[str, Any], csv_filepath: str) -> None:
    """Extract manufacturer tags from asset data, and write to CSV.

    Args:
        asset_data: Dictionary containing asset information.
        csv_filepath: Path to the CSV file where tags are stored.
    """
    asset_id = _sanitize_text(asset_data.get("id"))
    name = _sanitize_text(asset_data.get("name", ""))
    upload_date = _sanitize_text(asset_data.get("created", ""))
    params = asset_data.get("dictParameters", {})
    manufacturer = _sanitize_text(params.get("manufacturer", ""))
    designer = _sanitize_text(params.get("designer", ""))
    design_collection = _sanitize_text(params.get("designCollection", ""))
    design_year = _sanitize_text(params.get("designYear", ""))
    tags_joined = _sanitize_tags_list(asset_data.get("tags", []))
    description = _sanitize_text(asset_data.get("description", ""))
    status = _sanitize_text(asset_data.get("verificationStatus", ""))

    author = asset_data.get("author", {})
    author_name = _sanitize_text(author.get("fullName", ""))
    author_id = _sanitize_text(author.get("id", ""))

    if asset_id:
        fields = [
            asset_id,
            name,
            upload_date,
            manufacturer,
            designer,
            design_collection,
            design_year,
            tags_joined,
            description,
            status,
            author_name,
            author_id,
        ]
        if not _validate_no_pipes(fields):
            logger.error(
                "Pipe '|' detected after sanitization for asset %s; skipping write to prevent corruption.",
                asset_id,
            )
            return
        line = "|".join(fields) + "\n"
        with open(csv_filepath, "a", encoding="utf-8") as f:
            f.write(line)


def iterate_assets(
    assets: list[dict[str, Any]],
    csv_filepath: str = "manufacturer_tags.csv",
) -> None:
    """Iterate assets and dispatch tag validation threads.

    Args:
        assets: List of asset dictionaries to process.
        csv_filepath: Path to the CSV file where tags are stored.

    Returns:
        None
    """
    if not assets:
        logger.info("No assets to process.")
        return

    # do this one by one for testing
    for asset in assets:
        tag_extraction(asset, csv_filepath=csv_filepath)

    # concurrency can be re-enabled here if needed


def _build_search_params(year: int, month: int | None = None, day: int | None = None) -> dict[str, Any]:
    """Build search parameters for fetching assets.

    Args:
        year: Year to search for assets.
        month: Month to search for assets (optional).
        day: Day to search for assets (optional).

    Returns:
        Dictionary of search parameters.

    Raises:
        ValueError: If day is provided without month.
    """
    if day is not None:
        if month is None:
            raise ValueError("Month must be provided if day is specified.")
        params = {
            "order": "-created",
            "created": f"{year}-{month:02d}-{day:02d}",  # to avoid too many old assets
        }
    elif month is not None:
        # Use the actual last day of the month to avoid invalid dates such as 02-31
        last_day = calendar.monthrange(year, month)[1]
        params = {
            "order": "-created",
            "created_gte": f"{year}-{month:02d}-01",  # to avoid too many old assets
            "created_lte": f"{year}-{month:02d}-{last_day:02d}",  # valid last day in month
        }
    else:
        params = {
            "order": "-created",
            "created_gte": f"{year}-01-01",  # to avoid too many old assets
            "created_lte": f"{year}-12-31",  # to avoid too many old assets
        }

    # @ we are hammering the api too much we need to slow down
    # later we ask for both "uploaded" and "validated" too
    params["verification_status"] = "validated,uploaded"
    return params


def process_assets(assets: list[dict[str, Any]], csv_filepath: str) -> None:
    """Process assets to extract manufacturer tags and store them in CSV.

    Args:
        assets: List of asset dictionaries to process.
        csv_filepath: Path to the CSV file where tags are stored.

    Returns:
        None
    """
    # pre-filter assets without manufacturer/designer/design collection
    # and with correct verification status
    filtered_assets = []
    for ass in assets:
        params = ass.get("dictParameters", {})
        manufacturer = params.get("manufacturer", "")
        designer = params.get("designer", "")
        design_collection = params.get("designCollection", "")
        design_year = params.get("designYear", "")
        if any([manufacturer, designer, design_collection, design_year]) and ass["verificationStatus"] in [
            "uploaded",
            "validated",
        ]:
            filtered_assets.append(ass)

    # process each year directly
    existing_tags = load_existing_tags(csv_filepath)

    # pre-filter already processed assets
    if existing_tags:
        pr = []
        for asset in filtered_assets:
            if existing_tags.get(asset.get("id")) is None:
                pr.append(asset)  # noqa: PERF401
        filtered_assets = pr

    logger.info("Assets to be processed : %s", len(filtered_assets))

    iterate_assets(
        filtered_assets,
        csv_filepath=csv_filepath,
    )


def search_assets(
    filepath: str,
    year: int,
    month: int | None = None,
    day: int | None = None,
    *,
    page_size: int | None = None,
):
    """Search for assets based on the provided date parameters.

    Args:
        filepath: Path to the JSON file where search results are stored.
        year: Year to search for assets.
        month: Month to search for assets (optional).
        day: Day to search for assets (optional).

    Args:
        filepath: Path to the JSON file where search results are stored.
        year: Year to search for assets.
        month: Month to search for assets (optional).
        day: Day to search for assets (optional).
        page_size: Optional override for API page size.

    Returns:
        List of assets found during the search.
    """
    params = _build_search_params(year=year, month=month, day=day)
    custom_tokens = [f"asset_type:{ASSET_TYPES}"]
    # Default page size unless an override is provided (e.g., in redo mode)
    effective_page_size = page_size if page_size is not None else min(config.MAX_ASSET_COUNT, 100)

    search.get_search_simple(
        parameters=params,
        custom_tokens=custom_tokens,
        filepath=filepath,
        page_size=effective_page_size,
        api_key=config.BLENDERKIT_API_KEY,
        early_exit=True,
    )
    assets = search.load_assets_list(filepath)
    logger.info("Collected %s assets from year %s month %s day %s", len(assets), year, month, day)
    return assets


def run_all_mode(csv_filepath: str) -> None:
    """Collect tags for all assets by crawling year -> month -> day windows.

    This avoids the Elasticsearch 10k cap and mirrors the previous behavior.
    """
    dpath = tempfile.gettempdir()
    filepath = os.path.join(dpath, "assets_for_tag_validation.json")
    current_year = datetime.now(tz=UTC).year
    for year in range(current_year, 2017, -1):
        logger.info("Collecting assets from year %s", year)
        assets: list[dict[str, Any]] = []
        try:
            assets = search_assets(filepath, year)
        except (exceptions.SearchResultLimitError, exceptions.SearchRequestRepeatError):
            logger.exception(
                "Year %s reached search limit. Will try month by month.",
                year,
            )
            # try month by month
            for month in range(12, 0, -1):
                try:
                    month_assets = search_assets(filepath, year, month)
                    assets.extend(month_assets)
                except (exceptions.SearchResultLimitError, exceptions.SearchRequestRepeatError):
                    logger.exception(
                        "Month limit reached for %s-%02d. Will try each day.",
                        year,
                        month,
                    )
                    # try each day - use valid days for the given month
                    last_day = calendar.monthrange(year, month)[1]
                    for day in range(last_day, 0, -1):
                        try:
                            day_assets = search_assets(filepath, year, month, day)
                            assets.extend(day_assets)
                        except Exception:
                            logger.exception(
                                "Failed to collect assets for %s-%02d-%02d. Skipping day.",
                                year,
                                month,
                                day,
                            )

        process_assets(assets, csv_filepath=csv_filepath)

    # cleanup temp file
    if os.path.exists(filepath):
        os.remove(filepath)


def run_single_asset_mode(asset_id: str, csv_filepath: str) -> None:
    """Collect tags for a single asset by base id."""
    params = {
        "asset_base_id": asset_id,
        # Do not restrict asset_type for single asset; server will resolve the id
    }
    assets = search.get_search_paginated(
        parameters=params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Collected %s assets for processing (single asset mode)", len(assets))
    process_assets(assets, csv_filepath=csv_filepath)


def run_created_mode(created: str, csv_filepath: str) -> None:
    """Collect tags filtered by created date (YYYY | YYYY-MM | YYYY-MM-DD)."""
    y, m, d = _parse_created(created)
    params = _build_search_params(year=y, month=m, day=d)
    # Ensure asset types are included similar to the ALL mode
    params["asset_type"] = ASSET_TYPES
    assets = search.get_search_paginated(
        parameters=params,
        page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
        max_results=config.MAX_ASSET_COUNT,
        api_key=config.BLENDERKIT_API_KEY,
    )
    logger.info("Collected %s assets for processing (created=%s)", len(assets), created)
    process_assets(assets, csv_filepath=csv_filepath)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for collection script.

    Modes:
      - Single asset: use --asset-id or env ASSET_BASE_ID
      - All assets: --all
      - By created date: --created YYYY[ -MM[ -DD]]
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="Collect tags for all assets.")
    p.add_argument("--asset-id", help="Collect tags for a single asset (asset_base_id).")
    p.add_argument("--created", help="Created filter: YYYY | YYYY-MM | YYYY-MM-DD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Fetch assets and run tag collection.

    Modes:
      - Single asset: --asset-id or env ASSET_BASE_ID
      - All assets: --all
      - By created date: --created YYYY[ -MM[ -DD]]
    """
    args = parse_args(argv)
    csv_filepath = os.path.join(c_root, "collected_fields.csv")
    ensure_tag_csv_file(csv_filepath)

    # Determine mode
    asset_id = args.asset_id or config.ASSET_BASE_ID

    if args.all:
        logger.info("Collecting tags for ALL assets via year/month/day windows")
        run_all_mode(csv_filepath)
    elif asset_id:
        run_single_asset_mode(asset_id, csv_filepath)

    elif args.created:
        run_created_mode(args.created, csv_filepath)
    else:
        logger.error("No mode selected. Use --asset-id, --all, or --created.")
        sys.exit(2)


if __name__ == "__main__":
    main()
