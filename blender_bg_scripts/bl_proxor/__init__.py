"""Vendored copy of the BlenderKit addon's ``bl_proxor`` package.

Only the modules required for headless .prxc generation are vendored:
``generate`` (mesh sampling + payload) and ``prx_format`` (file writer).
The ``draw`` submodule from the addon is intentionally omitted because it
depends on Blender's GPU module and is only used for in-viewport preview.

Keep this in sync with ``bl_proxor`` in the blenderkit_addon repository.
"""
