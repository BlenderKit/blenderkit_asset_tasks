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

import os
import shutil
import tempfile
import threading
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

# Collected per-asset results, written to the GitHub Actions run summary.
_RESULTS: list[dict[str, str]] = []
_RESULTS_LOCK = threading.Lock()
# Total assets the search returned for this run (for the summary header).
_ASSET_COUNT: int = 0

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


def _resolve_asset_binary(fallback_binary_path: str) -> str:
    """Resolve the Blender binary to use for a single asset's background jobs.

    Returns an empty string when ``BLENDERS_PATH`` lists Blender versions, so
    ``send_to_bg`` auto-selects the build matching the asset's source version
    (never newer, to avoid re-saving the .blend with a newer Blender). When only
    a single ``BLENDER_PATH`` is configured, the provided fallback is used.

    Args:
        fallback_binary_path: Blender binary to use when no ``BLENDERS_PATH`` is set.

    Returns:
        The Blender binary path, or an empty string to trigger source-version
        auto-selection inside ``send_to_bg``.
    """
    return "" if config.BLENDERS_PATH else fallback_binary_path


def _resolve_gltf_binary(fallback_binary_path: str, asset_data: dict[str, Any]) -> str:
    """Resolve the Blender binary to use for an asset's GLTF export jobs.

    Unlike `_resolve_asset_binary`, this deliberately picks the *newest* installed
    Blender. GLTF export only reads the .blend and writes a separate .glb — it
    never re-saves or re-uploads the source .blend — so using a newer Blender than
    the asset's source version is safe here and yields better material export.

    Falls back to the source-version auto-selection (empty string) when picking
    the newest build fails, and to the single configured binary when no
    ``BLENDERS_PATH`` is set.

    Args:
        fallback_binary_path: Blender binary to use when no ``BLENDERS_PATH`` is set.
        asset_data: Asset metadata, used to locate installed Blender versions.

    Returns:
        Absolute path to the newest Blender binary, or a fallback as described.
    """
    if not config.BLENDERS_PATH:
        return fallback_binary_path
    try:
        return send_to_bg.get_blender_binary(asset_data, binary_type="NEWEST")
    except RuntimeError:
        logger.warning("Could not select newest Blender for GLTF export; using source-version auto-select.")
        return ""


def _resolve_processing_binary(fallback_binary_path: str, asset_data: dict[str, Any], blend_path: str) -> str:
    """Resolve the actual Blender binary used for the unpack and resolution jobs.

    Mirrors the source-version (CLOSEST) selection that ``send_to_bg`` performs
    when ``BLENDERS_PATH`` lists multiple builds, so the orchestrator can report
    the exact Blender version those jobs ran with. Unpack and resolutions share
    this same build. Falls back to the single configured binary when no
    ``BLENDERS_PATH`` is set, and to the fallback when selection fails.

    Args:
        fallback_binary_path: Blender binary to use when no ``BLENDERS_PATH`` is set.
        asset_data: Asset metadata, used to locate installed Blender versions.
        blend_path: Path to the downloaded .blend, used to refine version detection.

    Returns:
        Absolute path to the resolved Blender binary, or the fallback as described.
    """
    if not config.BLENDERS_PATH:
        return fallback_binary_path
    try:
        return send_to_bg.get_blender_binary(asset_data, file_path=blend_path, binary_type="CLOSEST")
    except RuntimeError:
        logger.warning("Could not select source-version Blender for unpack/resolution.")
        return fallback_binary_path


def _blender_version_label(binary_path: str) -> str:
    """Return a human-readable Blender version label for a resolved binary path.

    The binary lives at ``<BLENDERS_PATH>/<version_dir>/blender[.exe]``, so the
    parent directory name encodes the version. Falls back to the binary's own
    name, or ``"?"`` when no path is available.

    Args:
        binary_path: Absolute path to the resolved Blender binary.

    Returns:
        A short label identifying the Blender version/build.
    """
    if not binary_path:
        return "?"
    version_dir = os.path.basename(os.path.dirname(binary_path))
    return version_dir or os.path.basename(binary_path) or "?"


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
                _resolve_gltf_binary(binary_path, asset_data),
                fmt,
                asset_file_path=blend_path,
            )
    return ran_resolutions


def _record_result(record: dict[str, str]) -> None:
    """Append a per-asset result record, log live progress, and refresh the summary.

    The summary file is rewritten after every asset so partial progress is
    captured even if the run is cancelled or fails part-way through. A numbered
    completion line is also logged so progress streams live in the job log (the
    GitHub Summary tab only renders once the step finishes).

    Args:
        record: The result record to store for the run summary.
    """
    with _RESULTS_LOCK:
        _RESULTS.append(record)
        done = len(_RESULTS)
        _flush_step_summary()
    logger.info(
        "[%s/%s] DONE %s (%s) -- mark=%s jobs=%s status=%s",
        done,
        _ASSET_COUNT or "?",
        record["name"],
        record["type"],
        record["mark"],
        record["jobs"],
        record["status"],
    )


def _summarize(atype: str | None, *, mark_ok: bool, ran_resolutions: bool) -> tuple[str, str, str]:
    """Compute the mark, jobs, and status columns for an asset's summary row.

    Args:
        atype: The asset type.
        mark_ok: Whether marking succeeded (or the type is not markable).
        ran_resolutions: Whether a resolutions job ran.

    Returns:
        A tuple of (mark_label, jobs_label, status_label).
    """
    if atype not in MARKABLE_TYPES:
        mark = "n/a"
    elif mark_ok:
        mark = "ok"
    else:
        mark = "FAILED"

    jobs: list[str] = []
    if ran_resolutions:
        jobs.append("resolutions")
    if atype in GLTF_TYPES:
        jobs.append("gltf+godot")

    status = "ok" if mark_ok else "marking failed"
    return mark, ", ".join(jobs) or "-", status


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
        _record_result(
            {
                "name": str(asset_data.get("name", "?")) if asset_data else "?",
                "type": str(asset_data.get("assetType", "?")) if asset_data else "?",
                "base_id": str(asset_data.get("assetBaseId", "?")) if asset_data else "?",
                "mark": "-",
                "jobs": "-",
                "blender": "-",
                "status": "skipped (no files)",
            },
        )
        return

    atype = asset_data.get("assetType")
    name = str(asset_data.get("name", ""))
    base_id = str(asset_data.get("assetBaseId", ""))
    logger.info("START %s (%s) %s", name, atype, base_id)

    work_dir = tempfile.mkdtemp()
    try:
        blend_path = download.download_asset(asset_data, api_key=api_key, directory=work_dir)
        if not blend_path:
            logger.warning("Could not download blend for asset %s", asset_data.get("id"))
            _record_result(
                {
                    "name": name,
                    "type": str(atype),
                    "base_id": base_id,
                    "mark": "-",
                    "jobs": "-",
                    "blender": "-",
                    "status": "failed (download)",
                },
            )
            return

        # Mark and re-upload the canonical .blend first (textures packed) so the
        # online library is corrected even if the jobs below produce nothing.
        mark_ok = _mark_and_reupload(asset_data, api_key, _resolve_asset_binary(binary_path), blend_path)

        # Unpack + generate resolutions/GLTFs, reusing the same downloaded file.
        ran_resolutions = _run_generation_jobs(asset_data, api_key, binary_path, blend_path)

        # Record the Blender build the unpack/resolution jobs ran with (both
        # share the same source-version build).
        blender_label = _blender_version_label(
            _resolve_processing_binary(binary_path, asset_data, blend_path),
        )

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

        mark_label, jobs_label, status_label = _summarize(
            atype,
            mark_ok=mark_ok,
            ran_resolutions=ran_resolutions,
        )
        _record_result(
            {
                "name": name,
                "type": str(atype),
                "base_id": base_id,
                "mark": mark_label,
                "jobs": jobs_label,
                "blender": blender_label,
                "status": status_label,
            },
        )
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


def _build_search_params(order: str = "-created", base_id: str | None = None) -> dict[str, Any]:
    """Build the asset search parameters for a bulk or single-asset run.

    Args:
        order: Sort order for the bulk query (``"-created"`` for newest first,
            ``"created"`` for oldest first). Ignored for single-asset runs.
        base_id: When provided, build params for that single asset base ID
            instead of the bulk backlog query.

    Returns:
        The query parameters dictionary for the search API.
    """
    if base_id is not None:
        params: dict[str, Any] = {"asset_base_id": base_id}
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
    the backlog at once. A single-asset run (``ASSET_BASE_IDS``) bypasses the
    split and instead searches each requested base ID. Duplicates (possible when
    the two halves meet in the middle) are removed while preserving order.

    Returns:
        The merged, de-duplicated list of asset dictionaries.
    """
    if config.ASSET_BASE_IDS:
        collected: list[dict[str, Any]] = []
        for base_id in config.ASSET_BASE_IDS:
            collected.extend(
                _collect_assets(_build_search_params(base_id=base_id), config.MAX_ASSET_COUNT),
            )
        merged_ids: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for asset in collected:
            base_id = asset.get("assetBaseId")
            if base_id in seen_ids:
                continue
            if base_id is not None:
                seen_ids.add(base_id)
            merged_ids.append(asset)
        return merged_ids

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


def _flush_step_summary() -> None:
    """Rewrite the GitHub Actions step summary from the results collected so far.

    Callers must hold ``_RESULTS_LOCK``. The file is opened in overwrite mode so
    the summary always reflects the latest progress. The GitHub Summary tab only
    renders after the step finishes, but writing incrementally means a cancelled
    or failed run still shows everything processed up to that point.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    records = _RESULTS
    ok = sum(1 for r in records if r["status"] == "ok")
    failed = len(records) - ok
    scope = f"assets `{', '.join(config.ASSET_BASE_IDS)}`" if config.ASSET_BASE_IDS else "bulk backlog"

    lines = [
        "## Asset processing summary",
        "",
        f"- **Server:** `{config.SERVER}`",
        f"- **Scope:** {scope}",
        f"- **Found:** {_ASSET_COUNT} &nbsp;|&nbsp; **Processed:** {len(records)} "
        f"&nbsp;|&nbsp; **OK:** {ok} &nbsp;|&nbsp; **Issues:** {failed}",
        f"- **Max asset count:** {config.MAX_ASSET_COUNT}",
        f"- **SKIP_UPDATE:** {SKIP_UPDATE}",
        "",
        "| # | Name | Type | Mark | Jobs | Blender | Status | Asset Base ID |",
        "| - | ---- | ---- | ---- | ---- | ------- | ------ | ------------- |",
    ]
    for i, r in enumerate(records, start=1):
        lines.append(
            f"| {i} | {r['name']} | {r['type']} | {r['mark']} | {r['jobs']} | "
            f"{r.get('blender', '-')} | {r['status']} | `{r['base_id']}` |",
        )
    lines.append("")

    try:
        with open(summary_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:
        logger.exception("Failed to write GitHub step summary to %s", summary_path)


def main() -> None:
    """Search for assets and run all applicable processing jobs for each one."""
    global _ASSET_COUNT  # noqa: PLW0603
    assets = _collect_assets_from_both_ends()
    _ASSET_COUNT = len(assets)

    logger.info("Found %s assets to process", len(assets))
    for i, asset in enumerate(assets):
        logger.info("%s %s ||| %s ||| %s", i + 1, asset.get("name"), asset.get("assetType"), asset.get("assetBaseId"))

    with _RESULTS_LOCK:
        _flush_step_summary()

    iterate_assets(assets, api_key=config.BLENDERKIT_API_KEY, binary_path=config.BLENDER_PATH)

    with _RESULTS_LOCK:
        _flush_step_summary()


if __name__ == "__main__":
    main()
