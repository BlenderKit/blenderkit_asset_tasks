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
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"
DEFAULT_REPO = "blenderkit/blenderkit_asset_tasks"
DEFAULT_WORKFLOW = "webhook_process_asset.yml"
DEFAULT_REF = "main"


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def main() -> int:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var required", file=sys.stderr)
        return 2

    repo = os.getenv("GITHUB_REPO", DEFAULT_REPO)
    ref = os.getenv("GITHUB_REF", DEFAULT_REF)
    workflow = os.getenv("WORKFLOW_FILE", DEFAULT_WORKFLOW)

    asset_base_id = os.getenv("ASSET_BASE_ID")
    asset_type = os.getenv("ASSET_TYPE")

    if not asset_base_id or not asset_type:
        print("ASSET_BASE_ID and ASSET_TYPE are required", file=sys.stderr)
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
    payload["inputs"]["is_private"] = _bool_env("IS_PRIVATE", False)

    url = f"{API_BASE}/repos/{repo}/actions/workflows/{workflow}/dispatches"
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (controlled URL)
            if resp.status not in (200, 201, 202, 204):
                print(f"Unexpected status: {resp.status}", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as e:  # noqa: PERF203 (clarity)
        print(f"HTTPError: {e.code} {e.reason} - {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"URLError: {e.reason}", file=sys.stderr)
        return 1

    print("Workflow dispatch triggered successfully")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
