"""Jobs that never need geometry must open a .blend without evaluating its subdivision.

Opening 25 MB truck models evaluated every wheel's Bevel + Subdivision and took more than
the runner's 16 GB; unpack and resolutions were killed before their scripts started
(October 2026). light_load_bg.py caps subdivision on load and must leave saved files with
the scene's own Simplify settings.

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_light_load.py
"""

import os
import runpy
import tempfile

import bpy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEVELS = 4
CUBE_VERTS = 8


def _evaluated_vertex_count() -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return len(bpy.data.objects["Subdivided"].evaluated_get(depsgraph).data.vertices)


def _saved_simplify(path: str) -> tuple[bool, int]:
    with bpy.data.libraries.load(path) as (src, dst):
        dst.scenes = list(src.scenes)
    scene = dst.scenes[0]
    return scene.render.use_simplify, scene.render.simplify_subdivision


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "Subdivided"
    modifier = cube.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = LEVELS
    bpy.context.scene.render.simplify_subdivision = 3
    folder = tempfile.mkdtemp()
    asset = os.path.join(folder, "asset.blend")
    bpy.ops.wm.save_as_mainfile(filepath=asset)
    assert _evaluated_vertex_count() > CUBE_VERTS, "the fixture must subdivide without the preload"

    runpy.run_path(os.path.join(REPO_ROOT, "blender_bg_scripts", "light_load_bg.py"))
    bpy.ops.wm.open_mainfile(filepath=asset)

    assert _evaluated_vertex_count() == CUBE_VERTS, "subdivision must not be evaluated after a light load"
    copy = os.path.join(folder, "copy.blend")
    bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)
    assert _saved_simplify(copy) == (False, 3), "a saved file keeps the scene's own Simplify settings"
    assert _evaluated_vertex_count() == CUBE_VERTS, "the session stays capped after saving"
    marked = os.path.join(folder, "marked.blend")
    bpy.ops.wm.save_as_mainfile(filepath=marked)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert _saved_simplify(marked) == (False, 3), "unpack's own save keeps them too"


main()
