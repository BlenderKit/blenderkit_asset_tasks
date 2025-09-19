"""Minimal bpy mock for unit testing without Blender.

Provides enough attributes used by our scripts to avoid ImportErrors and allow
asserting calls.
"""

from __future__ import annotations

import os
import tempfile
import types
from unittest import mock


class BpyMock(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("bpy")
        # app and version
        self.app = types.SimpleNamespace(version=(3, 6, 0))

        # data containers
        self.data = types.SimpleNamespace(
            images=[],
            materials=[],
            brushes=[],
            objects=[],
            filepath=os.path.join(tempfile.gettempdir(), "mock.blend"),
            use_autopack=True,
        )

        # context and preferences
        self.context = types.SimpleNamespace(
            scene=types.SimpleNamespace(asset_mark=mock.Mock()),
            visible_objects=[],
            preferences=types.SimpleNamespace(
                filepaths=types.SimpleNamespace(file_preview_type="NONE"),
            ),
        )

        # ops
        self.ops = types.SimpleNamespace(
            wm=types.SimpleNamespace(
                save_as_mainfile=mock.Mock(return_value={}),
                quit_blender=mock.Mock(),
            ),
            extensions=types.SimpleNamespace(
                package_install_files=mock.Mock(return_value=None),
            ),
        )

        # path utilities
        self.path = types.SimpleNamespace(abspath=lambda p: p)


def install_into_sys_modules():
    import sys as _sys

    bpy = BpyMock()
    _sys.modules["bpy"] = bpy
    return bpy
