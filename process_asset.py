"""Single orchestrator that runs all applicable processing jobs per asset.

Previously each task (resolutions, GLTF, Godot GLTF, reindex) was dispatched as
its own GitHub Actions job, re-checking out the repo and re-downloading the
asset every time. This script searches once and, for each asset, runs the jobs
applicable to its asset type.

Asset marking runs implicitly inside the shared unpack step used by the
resolution and GLTF jobs, so every processed asset ends up marked correctly for
both the online and local asset libraries.

Concurrency model: jobs for *different* assets run in parallel (they are
independent and download to separate files), while jobs for the *same* asset run
sequentially because they share the same downloaded file on disk.

The individual ``generate_*`` scripts remain runnable by hand for targeted reruns.
"""

from __future__ import annotations

import shutil
import tempfile
from typing import Any

import generate_gltf
import generate_resolutions
from blenderkit_server_utils import (
    concurrency,
    config,
    datetime_utils,
    download,
    log,
    search,
    send_to_bg,
    upload,
    utils,
)

logger = log.create_logger(__name__)

utils.raise_on_missing_env_vars(["BLENDERKIT_API_KEY"])

# Asset types eligible for each job.
RESOLUTION_TYPES: frozenset[str] = frozenset({"model", "material", "hdr", "scene", "printable"})
GLTF_TYPES: frozenset[str] = frozenset({"model"})

# Asset types whose canonical .blend can be re-marked and re-uploaded. HDRs are
# excluded because their asset is an image with no markable data block.
MARKABLE_TYPES: frozenset[str] = frozenset({"model", "material", "scene", "printable", "brush", "nodegroup"})

# Union of all asset types this orchestrator can process in a bulk run.
PROCESS_ASSET_TYPES: str = "model,material,hdr,scene,printable"

# GLTF export formats produced for eligible assets, in run order.
GLTF_FORMATS: tuple[str, ...] = ("gltf", "gltf_godot")

# Number of assets processed concurrently. Each asset spawns heavy Blender
# background jobs, so keep this low by default.
MAX_CONCURRENCY: int = 1

# Parameter stamped once an asset has been fully reprocessed and re-marked by the
# new pipeline. Drives the one-time re-processing sweep (search marker). This is
# separate from each job's own completion marker (last_resolution_upload,
# gltfGeneratedDate, gltfGodotGeneratedDate), which are still updated by the jobs.
PARAM_PROCESSING_DATE: str = "processingDate"

SKIP_UPDATE: bool = config.SKIP_UPDATE


def _run_job(job_name: str, asset_data: dict[str, Any], func: Any, *args: Any, **kwargs: Any) -> None:
    """Run a single processing job, isolating its failures from sibling jobs.

    Args:
        job_name: Human-readable job name used in logs.
        asset_data: Asset data dictionary (for logging context).
        func: The callable implementing the job.
        *args: Positional arguments forwarded to ``func``.
        **kwargs: Keyword arguments forwarded to ``func``.
    """
    logger.info("Starting job '%s' for asset %s", job_name, asset_data.get("id"))
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception("Job '%s' failed for asset %s", job_name, asset_data.get("id"))


def _trigger_reindex(asset_data: dict[str, Any], api_key: str) -> None:
    """Trigger a server-side reindex for the asset.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.
    """
    if SKIP_UPDATE:
        logger.warning("SKIP_UPDATE==True -> skipping reindex for %s", asset_data.get("id"))
        return
    try:
        upload.patch_asset_empty(asset_data["assetBaseId"], api_key=api_key)
    except Exception:
        logger.exception("Reindex trigger failed for asset %s", asset_data.get("id"))


def _patch_processing_date(asset_data: dict[str, Any], api_key: str) -> None:
    """Stamp the processingDate marker so the asset is not reprocessed again.

    This is set once all applicable jobs (and the shared marking step) have run
    for the asset. It is independent of each job's own completion marker.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.
    """
    if SKIP_UPDATE:
        logger.warning("SKIP_UPDATE==True -> skipping processingDate patch for %s", asset_data.get("id"))
        return
    # processingDate is a DateTime parameter, so send a full ISO 8601 timestamp
    # (with timezone) rather than a bare date.
    timestamp = datetime_utils.now_timestamp_iso()
    try:
        upload.patch_individual_parameter(
            asset_id=asset_data["id"],
            param_name=PARAM_PROCESSING_DATE,
            param_value=timestamp,
            api_key=api_key,
        )
    except Exception:
        logger.exception("Failed to patch %s for asset %s", PARAM_PROCESSING_DATE, asset_data.get("id"))
    else:
        logger.info("Patched %s=%s for asset %s", PARAM_PROCESSING_DATE, timestamp, asset_data.get("id"))


def _mark_and_reupload(asset_data: dict[str, Any], api_key: str, binary_path: str, blend_path: str) -> bool:
    """Re-mark the asset's canonical .blend and re-upload it to the server.

    Marks exactly the right data block while keeping textures packed, saves the
    file, and re-uploads it as the ``blend`` original. This runs before the
    resolution/GLTF jobs unpack the same file, so the uploaded original stays
    self-contained. This is the step that fixes incorrect marking for the online
    asset library.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.
        binary_path: Absolute path to the Blender binary for background jobs.
        blend_path: Path to the already-downloaded asset .blend (shared with the
            later resolution/GLTF jobs).

    Returns:
        True if marking succeeded (or the type is not markable), False if the
        marking or re-upload failed.
    """
    atype = asset_data.get("assetType")
    if atype not in MARKABLE_TYPES:
        logger.info("Asset type %s is not markable, skipping re-mark for %s", atype, asset_data.get("id"))
        return True

    try:
        # Flag a copy of the metadata so the bg script marks without unpacking.
        mark_payload = dict(asset_data)
        mark_payload["_mark_only"] = True
        send_to_bg.send_to_bg(
            mark_payload,
            asset_file_path=blend_path,
            script="unpack_asset_bg.py",
            binary_path=binary_path,
        )

        if SKIP_UPDATE:
            logger.warning("SKIP_UPDATE==True -> skipping marked blend re-upload for %s", asset_data.get("id"))
            return True

        return upload.reupload_main_blend(asset_data, blend_path, api_key=api_key)
    except Exception:
        logger.exception("Mark-and-reupload failed for asset %s", asset_data.get("id"))
        return False


def _run_generation_jobs(asset_data: dict[str, Any], api_key: str, binary_path: str, blend_path: str) -> bool:
    """Run the resolution and GLTF jobs for an asset, reusing a shared .blend.

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.
        binary_path: Absolute path to the Blender binary for background jobs.
        blend_path: Path to the shared, already-downloaded asset .blend.

    Returns:
        True if a resolutions job ran for this asset, False otherwise.
    """
    atype = asset_data.get("assetType")

    ran_resolutions = atype in RESOLUTION_TYPES
    if ran_resolutions:
        _run_job(
            "resolutions",
            asset_data,
            generate_resolutions.generate_resolution_thread,
            asset_data,
            api_key=api_key,
            asset_file_path=blend_path,
        )

    if atype in GLTF_TYPES:
        for fmt in GLTF_FORMATS:
            _run_job(
                f"gltf:{fmt}",
                asset_data,
                generate_gltf.generate_gltf,
                asset_data,
                api_key,
                binary_path,
                fmt,
                asset_file_path=blend_path,
            )
    return ran_resolutions


def process_asset(asset_data: dict[str, Any], api_key: str, binary_path: str) -> None:
    """Run every job applicable to a single asset, sequentially.

    The asset is downloaded once and the same .blend is reused by every job:

    1. download the original .blend
    2. mark the asset and save (textures kept packed)
    3. re-upload the marked original
    4. unpack + generate resolutions and GLTFs (reusing the same file)
    5. upload the generated variants

    Args:
        asset_data: Asset data dictionary.
        api_key: API key for authentication.
        binary_path: Absolute path to the Blender binary for background jobs.
    """
    if not asset_data or not asset_data.get("files"):
        logger.warning("Skipping empty or invalid asset entry")
        return

    atype = asset_data.get("assetType")
    logger.info("Processing asset %s (%s)", asset_data.get("assetBaseId"), atype)

    work_dir = tempfile.mkdtemp()
    try:
        blend_path = download.download_asset(asset_data, api_key=api_key, directory=work_dir)
        if not blend_path:
            logger.warning("Could not download blend for asset %s", asset_data.get("id"))
            return

        # Mark and re-upload the canonical .blend first (textures packed) so the
        # online library is corrected even if the jobs below produce nothing.
        mark_ok = _mark_and_reupload(asset_data, api_key, binary_path, blend_path)

        # Unpack + generate resolutions/GLTFs, reusing the same downloaded file.
        ran_resolutions = _run_generation_jobs(asset_data, api_key, binary_path, blend_path)

        # The resolutions job already triggers a reindex; only do it explicitly
        # when no resolutions job ran (e.g. brush/nodegroup single-asset runs).
        if not ran_resolutions:
            _trigger_reindex(asset_data, api_key)

        # Stamp the asset as fully reprocessed only when marking succeeded, so a
        # failed re-mark is retried on the next sweep instead of being skipped.
        if mark_ok:
            _patch_processing_date(asset_data, api_key)
        else:
            logger.warning("Marking failed for %s -> not stamping processingDate (will retry)", asset_data.get("id"))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def iterate_assets(assets: list[dict[str, Any]], api_key: str, binary_path: str) -> None:
    """Process assets, running each asset's jobs sequentially and assets in parallel.

    Args:
        assets: List of asset dictionaries to process.
        api_key: API key for authentication.
        binary_path: Absolute path to the Blender binary for background jobs.
    """
    concurrency.run_asset_threads(
        assets,
        worker=process_asset,
        worker_kwargs={
            "api_key": api_key,
            "binary_path": binary_path,
        },
        asset_arg_position=0,
        max_concurrency=MAX_CONCURRENCY,
        logger=logger,
    )


def _build_search_params(order: str = "-created") -> dict[str, Any]:
    """Build the asset search parameters for a bulk or single-asset run.

    Args:
        order: Sort order for the bulk query (``"-created"`` for newest first,
            ``"created"`` for oldest first). Ignored for single-asset runs.

    Returns:
        The query parameters dictionary for the search API.
    """
    if config.ASSET_BASE_ID is not None:
        params: dict[str, Any] = {"asset_base_id": config.ASSET_BASE_ID}
    else:
        params = {
            "asset_type": PROCESS_ASSET_TYPES,
            "order": order,
            "verification_status": "validated",
            # processingDate marks assets already handled by the new pipeline.
            f"{PARAM_PROCESSING_DATE}_isnull": True,
        }
    if config.CUSTOM_SEARCH_PARAMS:
        params.update(config.CUSTOM_SEARCH_PARAMS)
    return params


def _collect_assets(params: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    """Accumulate assets for a single search query up to ``max_results``.

    Args:
        params: Search query parameters.
        max_results: Maximum number of assets to accumulate.

    Returns:
        The list of accumulated asset dictionaries.
    """
    assets: list[dict[str, Any]] = []
    if max_results <= 0:
        return assets
    for page in search.iter_search_pages(
        params,
        custom_tokens=None,
        max_results=max_results,
        api_key=config.BLENDERKIT_API_KEY,
    ):
        if page:
            assets.extend(page)
    return assets


def _collect_assets_from_both_ends() -> list[dict[str, Any]]:
    """Collect assets, splitting the budget between oldest and newest assets.

    Half of ``MAX_ASSET_COUNT`` is taken from the newest assets and half from the
    oldest, so the one-time reprocessing sweep makes progress from both ends of
    the backlog at once. A single-asset run (``ASSET_BASE_ID``) bypasses the
    split. Duplicates (possible when the two halves meet in the middle) are
    removed while preserving order.

    Returns:
        The merged, de-duplicated list of asset dictionaries.
    """
    if config.ASSET_BASE_ID is not None:
        return _collect_assets(_build_search_params(), config.MAX_ASSET_COUNT)

    newest_budget = config.MAX_ASSET_COUNT // 2
    oldest_budget = config.MAX_ASSET_COUNT - newest_budget

    newest = _collect_assets(_build_search_params(order="-created"), newest_budget)
    oldest = _collect_assets(_build_search_params(order="created"), oldest_budget)
    logger.info("Collected %s newest and %s oldest assets", len(newest), len(oldest))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in (*newest, *oldest):
        base_id = asset.get("assetBaseId")
        if base_id in seen:
            continue
        if base_id is not None:
            seen.add(base_id)
        merged.append(asset)
    return merged


def main() -> None:
    """Search for assets and run all applicable processing jobs for each one."""
    assets = _collect_assets_from_both_ends()

    logger.info("Found %s assets to process", len(assets))
    for i, asset in enumerate(assets):
        logger.info("%s %s ||| %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"), asset.get("assetBaseId"))

    iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY, binary_path=config.BLENDER_PATH)


if __name__ == "__main__":
    main()
