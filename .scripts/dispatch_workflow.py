"""Helper script to trigger GitHub workflow dispatch events.

Reads required parameters from environment variables and performs a POST to
GitHub's workflow dispatch API. Keeps dependencies minimal (stdlib only).

Environment variables:
    GITHUB_TOKEN           - (required) PAT or workflow token with repo:actions scope
    GITHUB_REPO            - (optional) owner/repo, defaults to blenderkit/blenderkit_asset_tasks
    GITHUB_REF             - (optional) ref to dispatch, defaults to main
    WORKFLOW_FILE          - (optional) workflow filename, defaults to webhook_process_asset.yml
    ASSET_BASE_ID          - (required) asset_base_id input
    ASSET_TYPE             - (required) asset_type input (model/material/...)
    VERIFICATION_STATUS    - (optional) verification_status input
    IS_PRIVATE             - (optional) true/false (boolean) defaults to false
    SOURCE_APP_VERSION_XY  - (optional) source_app_version_xy (e.g. 4.3)

Usage inside VSCode: Use provided launch configurations which populate env vars.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"
DEFAULT_REPO = "blenderkit/blenderkit_asset_tasks"
DEFAULT_WORKFLOW = "webhook_process_asset.yml"
DEFAULT_REF = "main"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _bool_env(name: str, *, default: bool = False) -> bool:
    """Read a boolean environment variable.

    Args:
        name: Name of the environment variable.
        default: Default value if the variable is not set.

    Returns:
        Boolean value of the environment variable.
    """
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def main() -> int:
    """Main function to dispatch a GitHub workflow."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN env var required")
        return 2

    repo = os.getenv("GITHUB_REPO", DEFAULT_REPO)
    ref = os.getenv("GITHUB_REF", DEFAULT_REF)
    workflow = os.getenv("WORKFLOW_FILE", DEFAULT_WORKFLOW)

    asset_base_id = os.getenv("ASSET_BASE_ID")
    asset_type = os.getenv("ASSET_TYPE")

    if not asset_base_id or not asset_type:
        logger.error("ASSET_BASE_ID and ASSET_TYPE are required")
        return 2

    payload: dict[str, Any] = {
        "ref": ref,
        "inputs": {
            "asset_base_id": asset_base_id,
            "asset_type": asset_type,
        },
    }

    # Optional inputs
    if v := os.getenv("VERIFICATION_STATUS"):
        payload["inputs"]["verification_status"] = v
    if s := os.getenv("SOURCE_APP_VERSION_XY"):
        payload["inputs"]["source_app_version_xy"] = s
    payload["inputs"]["is_private"] = _bool_env("IS_PRIVATE", default=False)

    data = json.dumps(payload).encode("utf-8")

    url = f"{API_BASE}/repos/{repo}/actions/workflows/{workflow}/dispatches"

    if not url.startswith("https://"):
        raise ValueError("URL must start with https://")
    req = urllib.request.Request(url, data=data, method="POST")  # noqa: S310
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:  # nosec B310  # noqa: S310
            if resp.status not in (200, 201, 202, 204):
                logger.error("Unexpected status: %s", resp.status)
                return 1
    except urllib.error.HTTPError as e:
        logger.exception("HTTPError: %s %s - %s", e.code, e.reason, e.read().decode("utf-8", "ignore"))
        return 1
    except urllib.error.URLError as e:
        logger.exception("URLError: %s", e.reason)
        return 1

    logger.info("Workflow dispatch triggered successfully")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
