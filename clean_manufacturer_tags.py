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
import threading
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

PAGE_SIZE_LIMIT: int = 200

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

ValidationStat = dict[str, Any]


def _append_stat(
    stats_sink: list[ValidationStat] | None,
    stats_lock: threading.Lock | None,
    entry: ValidationStat,
) -> None:
    """Store a per-asset summary in-memory only."""
    if stats_sink is None:
        return
    if stats_lock is None:
        stats_sink.append(entry)
        return
    with stats_lock:
        stats_sink.append(entry)


def _base_params() -> dict[str, Any]:
    asset_base_id = config.ASSET_BASE_ID
    if asset_base_id is not None:
        return {"asset_base_id": asset_base_id}
    return {
        "order": "-created",
        "asset_type": "model,scene,material,printable",
        "verification_status": "validated,uploaded",
        "manufacturer_isnull": "false",
    }


def _fallback_params() -> dict[str, Any]:
    params = _base_params()
    params.update(
        {
            # retry any assets validated via fallback/unknown actors
            PARAM_ACTOR: "fallback,unknown",
        },
    )
    return params


def _new_params() -> dict[str, Any]:
    params = _base_params()
    params.update(
        {
            "manufacturer_isnull": "false",
            "validatedManufacturer_isnull": "true",
        },
    )
    return params


def _fetch_fallback_assets(limit: int, *, exclude_ids: set[str | None] | None = None) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if exclude_ids is None:
        exclude_ids = set()
    params = _fallback_params()
    raw_assets = _fetch_with_params(params, limit=limit * 2)  # light overfetch to offset exclusions
    results: list[dict[str, Any]] = []
    for asset in raw_assets:
        if asset.get("id") in exclude_ids:
            continue
        results.append(asset)
        if len(results) >= limit:
            break
    return results


def _fetch_new_assets(limit: int, *, exclude_ids: set[str | None] | None = None) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if exclude_ids is None:
        exclude_ids = set()
    params = _new_params()
    raw_assets = _fetch_with_params(params, limit=limit * 2)  # light overfetch to offset exclusions
    results: list[dict[str, Any]] = []
    for asset in raw_assets:
        if asset.get("id") in exclude_ids:
            continue
        results.append(asset)
        if len(results) >= limit:
            break
    return results


def _fetch_assets() -> list[dict[str, Any]]:
    """Fetch assets for retry (fallback) and new validation in two batches."""
    fallback_quota = max(config.MAX_ASSET_COUNT // 4, 1)
    new_quota = max(config.MAX_ASSET_COUNT - fallback_quota, 0)

    logger.info(
        "Fetching assets with fallback validation actors (limit=%d) and new validation (limit=%d)",
        fallback_quota,
        new_quota,
    )
    fallback_assets = _fetch_fallback_assets(limit=fallback_quota)

    fallback_ids = {a.get("id") for a in fallback_assets}

    logger.info("Fetched %d fallback assets for retry validation", len(fallback_assets))
    new_assets = _fetch_new_assets(limit=new_quota, exclude_ids=fallback_ids)
    logger.info("Fetched %d new assets for validation", len(new_assets))

    assets = fallback_assets + new_assets
    logger.info(
        "Assets to be processed: total=%d retry=%d new=%d",
        len(assets),
        len(fallback_assets),
        len(new_assets),
    )
    for asset in assets[:ASSET_LOG_PREVIEW]:
        logger.debug("%s ||| %s", asset.get("name"), asset.get("assetType"))
    return assets


def tag_validation_thread(
    asset_data: dict[str, Any],
    api_key: str,
    *,
    stats_sink: list[ValidationStat] | None = None,
    stats_lock: threading.Lock | None = None,
) -> None:
    """Worker for a single asset; mode selects model/material pipeline."""
    # basic guards
    if not asset_data:
        logger.warning("Skipping empty or invalid asset entry")
        return

    # capture data that may be removed
    captured_data = {}
    for man_param in ALL_MAN_PARAMS:
        captured_data[man_param] = asset_data.get("dictParameters", {}).get(man_param, "")

    validation_timestamp = datetime_utils.now_timestamp_iso()

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
                param_value=validation_timestamp,
                api_key=api_key,
            )
            upload.patch_individual_parameter(
                asset_id=asset_data["id"],
                param_name=PARAM_ACTOR,
                param_value="no_data",
                api_key=api_key,
            )
        _append_stat(
            stats_sink,
            stats_lock,
            {
                "asset_id": asset_data.get("id", ""),
                "name": asset_data.get("name", ""),
                "verdict": "no_data",
                "status": True,
                "actor": "no_data",
                "reason": "no manufacturer fields present",
                "updated": not SKIP_UPDATE,
            },
        )
        return

    result = field_validation.validate_fields.validate(asset_data, use_ai=True)
    if not result:
        logger.warning("Field validator failed successfully for '%s'", asset_data.get("name"))
        _append_stat(
            stats_sink,
            stats_lock,
            {
                "asset_id": asset_data.get("id", ""),
                "name": asset_data.get("name", ""),
                "verdict": "validation_error",
                "status": None,
                "actor": "unknown",
                "reason": "field validator returned no result",
                "updated": False,
            },
        )
        return
    status, actor, reason = result

    logger.info("Validation result: %s | %s | %s | %s", asset_data.get("id"), status, actor, reason)

    if SKIP_UPDATE:
        logger.info("SKIP_UPDATE is set, not patching the asset.")
        _append_stat(
            stats_sink,
            stats_lock,
            {
                "asset_id": asset_data.get("id", ""),
                "name": asset_data.get("name", ""),
                "verdict": "pass" if status else "fail",
                "status": status,
                "actor": actor,
                "reason": reason,
                "updated": False,
            },
        )
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
        param_value=validation_timestamp,
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

    _append_stat(
        stats_sink,
        stats_lock,
        {
            "asset_id": asset_data.get("id", ""),
            "name": asset_data.get("name", ""),
            "verdict": "pass" if status else "fail",
            "status": status,
            "actor": actor,
            "reason": reason,
            "updated": True,
        },
    )


def iterate_assets(
    assets: list[dict[str, Any]],
    api_key: str = "",
) -> list[ValidationStat]:
    """Iterate assets and dispatch tag validation threads.

    Args:
        assets: List of asset dictionaries to process.
        api_key: BlenderKit API key forwarded to the thread function.

    Returns:
        Collected per-asset validation statistics.
    """
    stats: list[ValidationStat] = []
    stats_lock = threading.Lock()
    concurrency.run_asset_threads(
        assets,
        worker=tag_validation_thread,
        worker_kwargs={
            "api_key": api_key,
            "stats_sink": stats,
            "stats_lock": stats_lock,
        },
        asset_arg_position=0,
        max_concurrency=config.MAX_VALIDATION_THREADS,
        logger=logger,
    )
    return stats


def _fetch_with_params(params: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Run a paginated search with temporary dir cleanup."""
    temp_dir = tempfile.mkdtemp(prefix="bk_tag_cleanup_")
    try:
        assets = search.get_search_paginated(
            params,
            page_size=min(limit, PAGE_SIZE_LIMIT),
            max_results=limit,
            api_key=config.BLENDERKIT_API_KEY,
        )
    finally:
        utils.cleanup_temp(temp_dir)
    return assets


def _print_stats(stats: list[ValidationStat]) -> None:
    """Print a compact, in-memory-only validation summary."""
    total = len(stats)
    passed = sum(1 for item in stats if item.get("status") is True)
    failed = sum(1 for item in stats if item.get("status") is False)
    skipped = sum(1 for item in stats if item.get("verdict") in {"no_data", "validation_error"})
    logger.info("Validation summary: total=%s, passed=%s, failed=%s, skipped=%s", total, passed, failed, skipped)

    if not stats:
        logger.info("No assets processed.")
        return

    logger.info("Processed assets (temporary, not stored):")
    for item in stats:
        asset_id = item.get("asset_id", "")
        name = item.get("name", "")
        verdict = item.get("verdict", "")
        actor = item.get("actor", "")
        status = item.get("status")
        updated = item.get("updated")
        reason = item.get("reason", "") or "n/a"
        reason_short = reason[:180]
        logger.info(
            "%s | %s | verdict=%s | status=%s | actor=%s | updated=%s | reason=%s",
            asset_id,
            name,
            verdict,
            status,
            actor,
            updated,
            reason_short,
        )


def main(_argv: list[str] | None = None) -> None:
    """Fetch assets, validate manufacturer metadata, and patch results."""
    assets: list[dict[str, Any]] = []
    assets = _fetch_assets()

    if assets:
        stats = iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY)
        _print_stats(stats)


if __name__ == "__main__":
    main()
