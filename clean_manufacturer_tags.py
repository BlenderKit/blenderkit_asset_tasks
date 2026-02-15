"""Validate and optionally clean manufacturer metadata for BlenderKit assets.

The script fetches candidate assets, runs the field-validation heuristics (with
optional AI backing), patches validation status parameters, and can remove
invalid manufacturer/designer/collection/year entries when configured.

Environment variables:
    Required:
        BLENDERKIT_API_KEY: Auth token for asset API calls.
    Optional:
        ASSET_BASE_ID: Restrict processing to a single asset.
        MAX_VALIDATION_THREADS: Override concurrency level (defaults to config).
        SKIP_UPDATE: Set to "1" to perform dry runs without patching assets.
"""

from __future__ import annotations

import tempfile
from typing import Any

from blenderkit_server_utils import concurrency, config, datetime_utils, log, search, upload, utils
from blenderkit_server_utils.asset_validation import field_validation

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(
    ["BLENDERKIT_API_KEY", "OPENAI_API_KEY"],
)
# based on api model check if we have keys or fall back
model_order = ["grok", "openai"]
if not any(getattr(config, f"{provider.upper()}_API_KEY") for provider in model_order):
    raise OSError(
        f"Missing API key for all providers: {', '.join(model_order)}. "
        f"Set one of the following environment variables: {', '.join(f'{provider.upper()}_API_KEY' for provider in model_order)}",  # noqa: E501
    )

# modify the chosen model in the config for use in the field validation module,
# which is where the model choice is made for AI validation
if config.GROK_API_KEY:
    config.AI_PROVIDER = "grok"
    logger.info("Using Grok for AI validation.")
elif config.OPENAI_API_KEY:
    config.AI_PROVIDER = "openai"
    logger.info("Using OpenAI for AI validation.")

SKIP_UPDATE: bool = config.SKIP_UPDATE

PAGE_SIZE_LIMIT: int = 500

PARAM_BOOL: str = "validatedManufacturer"
PARAM_DATE: str = "validatedManufacturerDate"
PARAM_RESULT: str = "validatedManufacturerOutput"
PARAM_ACTOR: str = "validatedManufacturerActor"

# manufacturer parameters, inside "dictParameters"
MAN_PARAM_MANUFACTURER: str = "manufacturer"
MAN_PARAM_DESIGNER: str = "designer"
MAN_PARAM_COLLECTION: str = "designCollection"
MAN_PARAM_VARIANT: str = "designVariant"  # should we permit this ?
MAN_PARAM_YEAR: str = "designYear"

ALL_MAN_PARAMS: list[str] = [
    MAN_PARAM_MANUFACTURER,
    MAN_PARAM_DESIGNER,
    MAN_PARAM_COLLECTION,
    MAN_PARAM_VARIANT,
    MAN_PARAM_YEAR,
]

ASSET_LOG_PREVIEW: int = 20


def _fetch_assets() -> list[dict[str, Any]]:
    """Fetch assets targeted for manufacturer tag cleanup."""
    temp_dir = tempfile.mkdtemp(prefix="bk_tag_cleanup_")
    params = _build_search_params()
    assets: list[dict[str, Any]] = []
    try:
        assets = search.get_search_paginated(
            params,
            page_size=min(config.MAX_ASSET_COUNT, PAGE_SIZE_LIMIT),
            max_results=config.MAX_ASSET_COUNT,
            api_key=config.BLENDERKIT_API_KEY,
        )

        logger.info("Assets to be processed: %d", len(assets))
        for asset in assets[:ASSET_LOG_PREVIEW]:
            logger.debug("%s ||| %s", asset.get("name"), asset.get("assetType"))
    finally:
        utils.cleanup_temp(temp_dir)
    return assets


def tag_validation_thread(asset_data: dict[str, Any], api_key: str) -> None:
    """Worker for a single asset; mode selects model/material pipeline."""
    # basic guards
    if not asset_data:
        logger.warning("Skipping empty or invalid asset entry")
        return

    # capture data that may be removed
    captured_data = {}
    for man_param in ALL_MAN_PARAMS:
        captured_data[man_param] = asset_data.get("dictParameters", {}).get(man_param, "")

    today = datetime_utils.today_date_iso()

    # skip as we have nothing to validate
    if not any(captured_data.values()):
        logger.info("No manufacturer data to validate for '%s'", asset_data.get("name"))
        if not SKIP_UPDATE:
            upload.patch_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_BOOL,
                param_value="True",
                api_key=api_key,
            )
            upload.patch_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_DATE,
                param_value=today,
                api_key=api_key,
            )
            upload.patch_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_ACTOR,
                param_value="no_data",
                api_key=api_key,
            )
        return

    result = field_validation.validate_fields.validate(asset_data, use_ai=True)
    if not result:
        logger.warning("Field validator failed successfully for '%s'", asset_data.get("name"))
        return
    status, actor, reason = result

    logger.info("Validation result: %s | %s | %s | %s", asset_data.get("id"), status, actor, reason)

    if SKIP_UPDATE:
        logger.info("SKIP_UPDATE is set, not patching the asset.")
        return

    # start with cleaning if we have invalid data,
    # to avoid confusion with the validation result parameters
    # and to avoid rate limit issues with multiple patch calls if we do it after storing the validation result
    if not status:
        # remove invalid manufacturer data
        # we clear all of them to be safe
        for man_param in ALL_MAN_PARAMS:
            upload.delete_individual_parameter(
                asset_id=asset_data["id"],
                param_name=man_param,
                api_key=api_key,
            )

    # store our validation result
    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=PARAM_BOOL,
        param_value=str(status),
        api_key=api_key,
    )
    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=PARAM_DATE,
        param_value=today,
        api_key=api_key,
    )

    # enhance reason with previous data
    if not status:
        reason += "\n"
        removed_parts = "|".join(f"{k}: {v}" for k, v in captured_data.items() if v)
        reason += removed_parts

    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=PARAM_RESULT,
        param_value=reason,
        api_key=api_key,
    )

    upload.patch_individual_parameter(
        asset_id=asset_data["id"],
        param_name=PARAM_ACTOR,
        param_value=actor,
        api_key=api_key,
    )


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
) -> None:
    """Iterate assets and dispatch tag validation threads.

    Args:
        assets: List of asset dictionaries to process.
        api_key: BlenderKit API key forwarded to the thread function.

    Returns:
        None
    """
    concurrency.run_asset_threads(
        assets,
        worker=tag_validation_thread,
        worker_kwargs={
            "api_key": api_key,
        },
        asset_arg_position=0,
        max_concurrency=config.MAX_VALIDATION_THREADS,
        logger=logger,
    )


def _build_search_params() -> dict[str, Any]:
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {"asset_base_id": asset_base_id}

    # only not validated manufacturers
    out = {
        "order": "-created",
        "asset_type": "model,scene,material,printable",
        "verification_status": "validated,uploaded",
        "manufacturer_isnull": "false",
        # > "validatedManufacturer_isnull": "true",
        # > "validatedManufacturerDate_isnull": "true",
        # > "validatedManufacturerDate_lte": "2026-02-13",  # to exclude assets validated with a future date by mistake
    }
    return out


def main(_argv: list[str] | None = None) -> None:
    """Fetch assets, validate manufacturer metadata, and patch results."""
    assets: list[dict[str, Any]] = []
    assets = _fetch_assets()

    if assets:
        iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY)


if __name__ == "__main__":
    main()
