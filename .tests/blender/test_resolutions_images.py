"""Lower resolutions must come out of the textures assets really carry.

In the October 2026 re-run, 147 assets recorded "no-size-gain" and 74 crashed:
- textures still packed (the unpack run had failed) were measured as 0 bytes, so no
  level could ever count as smaller;
- an opaque PNG pattern was rewritten as a JPEG several times its size;
- EXR textures were written uncompressed;
- BMP, DDS and PSD textures crashed the save ("enum not found"), Blender cannot write
  the last two at all;
- a generated color grid counted as a texture;
- images linked from another .blend crashed the rename or unpack ("Image is not editable").

Runs inside Blender, not unittest:
    blender --background --factory-startup --python-exit-code 1 --python .tests/blender/test_resolutions_images.py
"""

import glob
import os
import sys
import tempfile

import bpy
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "blender_bg_scripts")]

import resolutions_bg_blender  # noqa: E402

SIZE = 1024
SQUARE = 128
HALF_FLOAT_BYTES = 2
RNG = np.random.default_rng(7)


def _packed_image(folder: str, name: str, file_format: str, pixels: np.ndarray, *, float_buffer: bool = False) -> None:
    """Save pixels to a file, pack it and delete the file, as in a .blend that was never unpacked."""
    image = bpy.data.images.new(name, SIZE, SIZE, float_buffer=float_buffer)
    image.pixels.foreach_set(pixels.astype(np.float32).ravel())
    image.filepath_raw = os.path.join(folder, name)
    image.file_format = file_format
    image.save()
    image.source = "FILE"
    image.reload()
    image.pack()
    image.use_fake_user = True
    os.remove(os.path.join(folder, name))


def _rgba(rgb: np.ndarray) -> np.ndarray:
    return np.concatenate([rgb, np.ones((SIZE, SIZE, 1))], axis=2)


def _noise() -> np.ndarray:
    return _rgba(RNG.random((SIZE, SIZE, 3)))


def _checker() -> np.ndarray:
    cells = (np.indices((SIZE, SIZE)) // SQUARE).sum(axis=0) % 2
    return _rgba(np.repeat(cells[:, :, None], 3, axis=2).astype(float))


def _gradient() -> np.ndarray:
    ramp = np.linspace(0.0, 4.0, SIZE)
    across, down = np.meshgrid(ramp, ramp)
    return _rgba(np.stack([across, down, np.full((SIZE, SIZE), 0.5)], axis=2))


def _material_with(name: str, image: bpy.types.Image) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.use_fake_user = True
    return material


def _link_material(library_path: str, name: str) -> None:
    with bpy.data.libraries.load(library_path, link=True) as (_src, dst):
        dst.materials = [name]
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(dst.materials[0])


def _generate(blend_path: str) -> dict:
    return resolutions_bg_blender.generate_lower_resolutions({"file_path": blend_path, "asset_data": {}})


def _texture_files(folder: str, suffix: str) -> dict[str, int]:
    return {
        os.path.basename(path): os.path.getsize(path)
        for path in glob.glob(os.path.join(folder, f"textures{suffix}", "*"))
    }


def test_packed_textures_in_formats_blender_struggles_with(folder: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _packed_image(folder, "noise.png", "PNG", _noise())
    _packed_image(folder, "pattern.png", "PNG", _checker())
    _packed_image(folder, "legacy.bmp", "BMP", _noise())
    _packed_image(folder, "height.exr", "OPEN_EXR", _gradient(), float_buffer=True)
    grid = bpy.data.images.new("grid", SIZE, SIZE)
    grid.generated_type = "COLOR_GRID"
    grid.use_fake_user = True
    blend_path = os.path.join(folder, "asset.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    outcome = _generate(blend_path)

    assert [f["type"] for f in outcome.get("files", [])] == ["resolution_1K", "resolution_0_5K"], outcome
    half = _texture_files(folder, "_05k")
    assert sorted(half) == ["height.exr", "legacy.png", "noise.jpg", "pattern.png"], half
    uncompressed_exr = (SIZE // 2) ** 2 * 4 * HALF_FLOAT_BYTES
    assert half["height.exr"] < uncompressed_exr / 2, half


def test_textures_linked_from_another_file_are_left_alone(folder: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    library_image = bpy.data.images.new("library.png", SIZE, SIZE)
    library_image.pixels.foreach_set(_noise().astype(np.float32).ravel())
    library_image.pack()
    _material_with("Library material", library_image)
    library_path = os.path.join(folder, "library.blend")
    bpy.ops.wm.save_as_mainfile(filepath=library_path)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _link_material(library_path, "Library material")
    linked_only = os.path.join(folder, "linked_only.blend")
    bpy.ops.wm.save_as_mainfile(filepath=linked_only)
    _packed_image(folder, "own.png", "PNG", _noise())
    mixed = os.path.join(folder, "mixed.blend")
    bpy.ops.wm.save_as_mainfile(filepath=mixed)

    outcome = _generate(mixed)

    assert [f["type"] for f in outcome.get("files", [])] == ["resolution_1K", "resolution_0_5K"], outcome
    assert sorted(_texture_files(folder, "_05k")) == ["own.jpg"], _texture_files(folder, "_05k")
    try:
        _generate(linked_only)
    except RuntimeError as error:
        assert "linked from other .blend files" in str(error), error
    else:
        raise AssertionError("an asset whose textures are all linked must not pass as procedural")


def main() -> None:
    test_packed_textures_in_formats_blender_struggles_with(tempfile.mkdtemp())
    test_textures_linked_from_another_file_are_left_alone(tempfile.mkdtemp())


main()
