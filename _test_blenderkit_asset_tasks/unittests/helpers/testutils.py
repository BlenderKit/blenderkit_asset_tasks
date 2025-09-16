"""Common test utilities.

- ensure_src_on_path: add repo root and package root to sys.path for imports
- install_bpy_mock: inject a lightweight bpy mock into sys.modules
"""

from __future__ import annotations

import os
import sys


def ensure_src_on_path() -> str:
    """Ensure repository and package roots are importable.

    Returns:
        The detected repository root path.
    """
    here = os.path.dirname(__file__)
    # tests/helpers -> tests -> _test_blenderkit_asset_tasks -> repo root
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    tasks_root = os.path.join(repo_root, "blenderkit_asset_tasks")
    tests_root = os.path.abspath(os.path.join(here, "..", ".."))

    for path in (repo_root, tasks_root, tests_root):
        if path not in sys.path:
            sys.path.insert(0, path)

    return repo_root


def install_bpy_mock():
    from .bpy_mock import install_into_sys_modules

    return install_into_sys_modules()
