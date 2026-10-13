"""Open .blend files without evaluating their subdivision; saved files keep the scene's own settings.

Blender evaluates every scene as soon as it opens a .blend. Unpacking, marking and resolutions
only touch images and asset metadata, yet evaluating the subdivision of 25 MB truck models
(eight Bevel + Subdivision wheels each) took more than the runner's 16 GB, and the job was
killed before its script started. Run before the file is opened (``--python`` ahead of the
.blend on Blender's command line): every load caps subdivision through the scene's Simplify
setting, and every save writes the scene's own Simplify values back.
"""

import bpy
from bpy.app.handlers import persistent

# Scene name -> (use_simplify, simplify_subdivision) as the file had them.
_OWN_SIMPLIFY: dict[str, tuple[bool, int]] = {}


def _cap(scene: bpy.types.Scene) -> None:
    scene.render.use_simplify = True
    scene.render.simplify_subdivision = 0


@persistent
def _cap_after_load(*_args: object) -> None:
    _OWN_SIMPLIFY.clear()
    for scene in bpy.data.scenes:
        _OWN_SIMPLIFY[scene.name] = (scene.render.use_simplify, scene.render.simplify_subdivision)
        _cap(scene)


@persistent
def _restore_before_save(*_args: object) -> None:
    for scene in bpy.data.scenes:
        if scene.name in _OWN_SIMPLIFY:
            scene.render.use_simplify, scene.render.simplify_subdivision = _OWN_SIMPLIFY[scene.name]


@persistent
def _cap_after_save(*_args: object) -> None:
    for scene in bpy.data.scenes:
        if scene.name in _OWN_SIMPLIFY:
            _cap(scene)


bpy.app.handlers.load_post.append(_cap_after_load)
bpy.app.handlers.save_pre.append(_restore_before_save)
bpy.app.handlers.save_post.append(_cap_after_save)
