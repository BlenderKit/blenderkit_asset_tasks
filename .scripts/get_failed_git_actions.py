"""Rerun all failed GitHub Actions workflow runs for a specific repository.

Requires a GITHUB_TOKEN environment variable with appropriate permissions.
"""

import os

import requests

TOKEN = os.environ["GITHUB_TOKEN"]
OWNER = "BlenderKit"
REPO = "blenderkit_asset_tasks"
WORKFLOW = "webhook_process_asset.yml"  # filename or numeric ID

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})


def failed_runs():
    """Generator yielding (run_id, url, title) for each failed workflow run."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/runs"
    params = {"status": "failure", "per_page": 100}
    while url:
        resp = session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        for run in data["workflow_runs"]:
            yield run["id"], run["html_url"], run["display_title"]
        url = resp.links.get("next", {}).get("url")


def rerun(run_id:str, *, failed_only:bool=True):
    """Rerun a workflow run by its ID.

    Args:
        run_id: The ID of the workflow run to rerun.
        failed_only: If True, only rerun failed jobs within the workflow run.
    """
    suffix = "rerun-failed-jobs" if failed_only else "rerun"
    resp = session.post(f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs/{run_id}/{suffix}")
    resp.raise_for_status()


for _run_id, url, title in failed_runs():
    print("Rerunning", title, url)  # noqa: T201
    # > rerun(_run_id)
