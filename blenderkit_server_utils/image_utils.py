"""Image utilities for BlenderKit asset tasks.

This module provides helpers for saving images via Blender's render pipeline,
colorspace handling, conversions between Blender images and NumPy arrays,
thumbnail generation for HDR images, and simple image analysis utilities.

All Blender-specific calls are guarded; if Blender's `bpy` module is not
available, functions that require it will raise a RuntimeError.
"""

from __future__ import annotations

import os
import time
from typing import Any

from . import log

logger = log.create_logger(__name__)

# Constants to avoid magic numbers and improve readability
TRUE_HDR_THRESHOLD = 1.05
CHANNELS_RGBA = 4
CHANNELS_RGB = 3
PNG_MAX_COMPRESSION = 100
JPEG_QUALITY_DEFAULT = 90
MAX_THUMBNAIL_SIZE = 2048
NORMAL_MEAN_LOW = 0.45
NORMAL_MEAN_HIGH = 0.55
MIN_DOWNSCALE_SIZE = 128


try:  # Blender is not available in unit tests or CI
    import bpy  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    bpy = None  # type: ignore[assignment]
    logger.debug("bpy not present; Blender-specific functions will be unavailable")


def _require_bpy() -> None:
    """Ensure Blender `bpy` module is available.

    Raises:
        RuntimeError: If the bpy module is not available.
    """
    if bpy is None:  # type: ignore[name-defined]
        raise RuntimeError("Blender 'bpy' module is not available in this environment")


def get_orig_render_settings() -> dict[str, Any]:
    """Capture current render/image settings from the active scene.

    Returns:
        A dictionary snapshot of selected render and color settings.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    rs = bpy.context.scene.render  # type: ignore[attr-defined]
    ims = rs.image_settings
    vs = bpy.context.scene.view_settings  # type: ignore[attr-defined]

    orig_settings: dict[str, Any] = {
        "file_format": ims.file_format,
        "quality": ims.quality,
        "color_mode": ims.color_mode,
        "compression": ims.compression,
        "exr_codec": ims.exr_codec,
        "view_transform": vs.view_transform,
    }
    return orig_settings


def set_orig_render_settings(orig_settings: dict[str, Any]) -> None:
    """Restore previously saved render/image settings.

    Args:
        orig_settings: The dictionary captured by `get_orig_render_settings`.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    rs = bpy.context.scene.render  # type: ignore[attr-defined]
    ims = rs.image_settings
    vs = bpy.context.scene.view_settings  # type: ignore[attr-defined]

    ims.file_format = orig_settings["file_format"]
    ims.quality = orig_settings["quality"]
    ims.color_mode = orig_settings["color_mode"]
    ims.compression = orig_settings["compression"]
    ims.exr_codec = orig_settings["exr_codec"]

    vs.view_transform = orig_settings["view_transform"]


def img_save_as(  # noqa: PLR0913 - API kept for backward compatibility
    img: Any,
    filepath: str = "//",
    file_format: str = "JPEG",
    quality: int = JPEG_QUALITY_DEFAULT,
    color_mode: str = "RGB",
    compression: int = 15,
    view_transform: str = "Raw",
    exr_codec: str = "DWAA",
) -> None:
    """Save a Blender image using render pipeline settings.

    Blender saves images most reliably via the render settings. This temporarily
    overrides render/image settings, saves the image, and restores originals.

    Args:
        img: Blender image object (`bpy.types.Image`).
        filepath: Destination path (supports Blender's `//` project-relative).
        file_format: Blender image file format.
        quality: JPEG quality (when applicable).
        color_mode: Color mode, e.g. 'RGB', 'RGBA', 'BW'.
        compression: PNG compression (0-100).
        view_transform: View transform to use during saving.
        exr_codec: EXR codec when saving EXR files.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    ors = get_orig_render_settings()

    rs = bpy.context.scene.render  # type: ignore[attr-defined]
    vs = bpy.context.scene.view_settings  # type: ignore[attr-defined]

    ims = rs.image_settings
    ims.file_format = file_format
    ims.quality = quality
    ims.color_mode = color_mode
    ims.compression = compression
    ims.exr_codec = exr_codec
    vs.view_transform = view_transform

    img.save_render(filepath=bpy.path.abspath(filepath), scene=bpy.context.scene)  # type: ignore[attr-defined]

    set_orig_render_settings(ors)


def set_colorspace(img: Any, colorspace: str) -> None:
    """Set the image colorspace with a safe fallback.

    Some users may customize or replace color management. If the requested
    colorspace does not exist, a warning is logged.

    Args:
        img: Blender image object (`bpy.types.Image`).
        colorspace: Colorspace name or 'Non-Color'.
    """
    try:
        if colorspace == "Non-Color":
            img.colorspace_settings.is_data = True
        else:
            img.colorspace_settings.name = colorspace
    except (AttributeError, RuntimeError, ValueError):  # pragma: no cover - depends on user config
        logger.warning("Colorspace '%s' not found; keeping current setting", colorspace)


def analyze_image_is_true_hdr(image: Any) -> None:
    """Analyze if the image contains HDR values (> TRUE_HDR_THRESHOLD) and tag it.

    Sets `image.blenderkit.true_hdr` to a boolean.

    Args:
        image: Blender image object (`bpy.types.Image`).

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    import numpy as np  # local import to avoid hard dependency during tests

    size = image.size
    image_width = size[0]
    image_height = size[1]
    temp_buffer = np.empty(image_width * image_height * 4, dtype=np.float32)
    image.pixels.foreach_get(temp_buffer)
    image.blenderkit.true_hdr = np.amax(temp_buffer) > TRUE_HDR_THRESHOLD


def generate_hdr_thumbnail() -> None:
    """Generate and save a thumbnail for the HDR image selected in UI.

    Reads UI properties from Blender to locate the HDR image, creates a JPG
    thumbnail (max dimension MAX_THUMBNAIL_SIZE), sets Linear colorspace, and saves it.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    import numpy as np  # local import to avoid hard dependency during tests

    ui_props = bpy.context.window_manager.blenderkitUI  # type: ignore[attr-defined]
    hdr_image = ui_props.hdr_upload_image

    base, _ext = os.path.splitext(hdr_image.filepath)
    thumb_path = f"{base}.jpg"
    thumb_name = os.path.basename(thumb_path)

    size = hdr_image.size
    ratio = size[0] / size[1]

    image_width = size[0]
    image_height = size[1]
    thumbnail_width = min(size[0], MAX_THUMBNAIL_SIZE)
    thumbnail_height = min(size[1], int(MAX_THUMBNAIL_SIZE / ratio))

    temp_buffer = np.empty(image_width * image_height * 4, dtype=np.float32)
    inew = bpy.data.images.new(  # type: ignore[attr-defined]
        thumb_name,
        image_width,
        image_height,
        alpha=False,
        float_buffer=False,
    )

    hdr_image.pixels.foreach_get(temp_buffer)
    hdr_image.blenderkit.true_hdr = np.amax(temp_buffer) > TRUE_HDR_THRESHOLD

    inew.filepath = thumb_path
    set_colorspace(inew, "Linear")
    inew.pixels.foreach_set(temp_buffer)

    bpy.context.view_layer.update()  # type: ignore[attr-defined]
    if thumbnail_width < image_width:
        inew.scale(thumbnail_width, thumbnail_height)

    img_save_as(inew, filepath=inew.filepath)


def find_color_mode(image: Any) -> str:
    """Infer the color mode for a Blender image based on bit depth.

    Args:
        image: Blender image object (`bpy.types.Image`).

    Returns:
        'BW', 'RGB', or 'RGBA' depending on depth.

    Raises:
        TypeError: If `image` is not a Blender image.
    """
    _require_bpy()
    if not isinstance(image, bpy.types.Image):  # type: ignore[attr-defined]
        raise TypeError("image must be a bpy.types.Image")

    depth_mapping = {
        8: "BW",
        24: "RGB",
        32: "RGBA",  # can also be BW, but image.channels may not reflect that
        96: "RGB",
        128: "RGBA",
    }
    color_mode = depth_mapping.get(image.depth, "RGB")
    return color_mode


def find_image_depth(image: Any) -> str:
    """Infer the color depth to write ('8' or '16') from Blender image depth.

    Args:
        image: Blender image object (`bpy.types.Image`).

    Returns:
        '8' or '16' as a string representing target color depth.

    Raises:
        TypeError: If `image` is not a Blender image.
    """
    _require_bpy()
    if not isinstance(image, bpy.types.Image):  # type: ignore[attr-defined]
        raise TypeError("image must be a bpy.types.Image")

    depth_mapping = {
        8: "8",
        24: "8",
        32: "8",  # can also be BW, but image.channels may not reflect that
        96: "16",
        128: "16",
    }
    image_depth = depth_mapping.get(image.depth, "8")
    return image_depth


def can_erase_alpha(na: Any) -> bool:
    """Check whether alpha is fully opaque (all 1.0) across image data.

    Args:
        na: A flat NumPy array of RGBA floats.

    Returns:
        True if alpha channel is fully 1.0 and can be removed.
    """
    alpha = na[3::4]
    alpha_sum = alpha.sum()
    if alpha_sum == alpha.size:
        logger.debug("Image can have alpha erased")
    return alpha_sum == alpha.size


def is_image_black(na: Any) -> bool:
    """Determine if the image is entirely black (RGB sums to zero).

    Args:
        na: A flat NumPy array of RGBA floats.

    Returns:
        True if all RGB values are zero.
    """
    r = na[::4]
    g = na[1::4]
    b = na[2::4]

    rgb_sum = r.sum() + g.sum() + b.sum()
    if rgb_sum == 0:
        logger.debug("Image RGB sum is zero; alpha channel could be dropped")
    return rgb_sum == 0


def is_image_bw(na: Any) -> bool:
    """Check if the image is grayscale (R == G == B for all pixels).

    Args:
        na: A flat NumPy array of RGBA floats.

    Returns:
        True if the image is black-and-white.
    """
    r = na[::4]
    g = na[1::4]
    b = na[2::4]

    rg_equal = r == g
    gb_equal = g == b
    rgb_equal = rg_equal.all() and gb_equal.all()
    if rgb_equal:
        logger.debug("Image is black-and-white; channels can be reduced")
    return rgb_equal


def numpy_to_image(a: Any, iname: str, width: int = 0, height: int = 0, channels: int = CHANNELS_RGB) -> Any:
    """Create or reuse a Blender image and write data from a flat NumPy array.

    Args:
        a: Flat float array of pixel data (length = width * height * channels).
        iname: Name prefix to match or create.
        width: Image width.
        height: Image height.
        channels: Number of channels (3 or 4 supported by this helper).

    Returns:
        The Blender image object.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    t_start = time.time()
    foundimage = False

    for image in bpy.data.images:  # type: ignore[attr-defined]
        if image.name[: len(iname)] == iname and image.size[0] == a.shape[0] and image.size[1] == a.shape[1]:
            i = image
            foundimage = True

    if not foundimage:
        if channels == CHANNELS_RGBA:
            bpy.ops.image.new(  # type: ignore[attr-defined]
                name=iname,
                width=width,
                height=height,
                color=(0, 0, 0, 1),
                alpha=True,
                generated_type="BLANK",
                float=True,
            )
        if channels == CHANNELS_RGB:
            bpy.ops.image.new(  # type: ignore[attr-defined]
                name=iname,
                width=width,
                height=height,
                color=(0, 0, 0),
                alpha=False,
                generated_type="BLANK",
                float=True,
            )

    i = None

    for image in bpy.data.images:  # type: ignore[attr-defined]
        if image.name[: len(iname)] == iname and image.size[0] == width and image.size[1] == height:
            i = image
    if i is None:
        i = bpy.data.images.new(  # type: ignore[attr-defined]
            iname,
            width,
            height,
            alpha=False,
            float_buffer=False,
            stereo3d=False,
            is_data=False,
            tiled=False,
        )

    i.pixels.foreach_set(a)  # type: ignore[attr-defined]
    logger.debug("numpy_to_image time %.4fs", time.time() - t_start)
    return i


def image_to_numpy_flat(i: Any) -> Any:
    """Convert a Blender image into a flat NumPy float array.

    Args:
        i: Blender image object (`bpy.types.Image`).

    Returns:
        A flat NumPy float array of size width*height*channels.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    import numpy as np

    width = i.size[0]
    height = i.size[1]
    size = width * height * i.channels
    na = np.empty(size, np.float32)
    i.pixels.foreach_get(na)
    return na


def image_to_numpy(i: Any) -> Any:
    """Convert a Blender image into a (width, height, channels) NumPy array.

    Args:
        i: Blender image object (`bpy.types.Image`).

    Returns:
        A NumPy float array shaped (height, width, channels) swapped to (width, height, channels).

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()
    import numpy as np

    width = i.size[0]
    height = i.size[1]
    size = width * height * i.channels
    na = np.empty(size, np.float32)
    i.pixels.foreach_get(na)

    na = na.reshape(height, width, i.channels)
    na = na.swapaxes(0, 1)
    return na


def downscale(i: Any) -> None:
    """Downscale an image by half while keeping minimum dimension >= MIN_DOWNSCALE_SIZE.

    Args:
        i: Blender image object (`bpy.types.Image`).
    """
    sx, sy = i.size[:]
    sx = round(sx / 2)
    sy = round(sy / 2)
    if sx > MIN_DOWNSCALE_SIZE and sy > MIN_DOWNSCALE_SIZE:
        i.scale(sx, sy)


def get_rgb_mean(i: Any) -> tuple[float, float, float]:
    """Compute the mean of R, G, and B channels of a Blender image.

    Args:
        i: Blender image object (`bpy.types.Image`)

    Returns:
        Tuple of (r_mean, g_mean, b_mean).
    """
    na = image_to_numpy_flat(i)

    r = na[::4]
    g = na[1::4]
    b = na[2::4]

    rmean = float(r.mean())
    gmean = float(g.mean())
    bmean = float(b.mean())

    means: tuple[float, float, float] = (rmean, gmean, bmean)
    return means


def check_nmap_mean_ok(i: Any) -> bool:
    """Check whether a normal map has expected mean values around 0.5.

    Args:
        i: Blender image object (`bpy.types.Image`)

    Returns:
        True when R and G channel means are between NORMAL_MEAN_LOW and NORMAL_MEAN_HIGH.
    """
    rmean, gmean, _bmean = get_rgb_mean(i)
    nmap_ok = NORMAL_MEAN_LOW < rmean < NORMAL_MEAN_HIGH and NORMAL_MEAN_LOW < gmean < NORMAL_MEAN_HIGH
    return nmap_ok


def check_nmap_ogl_vs_dx(
    i: Any,
    mask: Any | None = None,
    *,
    generated_test_images: bool = False,
) -> str:
    """Classify a normal map as 'DirectX' or 'OpenGL'.

    This computes an approximate height map by integrating from normal vectors
    to compare variance of two reconstructions and pick the more plausible one.

    Args:
        i: Blender image object (`bpy.types.Image`).
        mask: Optional mask image; alpha channel decides valid pixels.
        generated_test_images: If True, creates diagnostic images 'OpenGL' and 'DirectX'.

    Returns:
        'DirectX' or 'OpenGL'.
    """
    import numpy as np

    width = i.size[0]
    height = i.size[1]

    rmean, gmean, _bmean = get_rgb_mean(i)
    na = image_to_numpy(i)

    if mask is not None:
        mask = image_to_numpy(mask)

    ogl = np.zeros((width, height), np.float32)
    dx = np.zeros((width, height), np.float32)

    if generated_test_images:
        ogl_img = np.empty((width, height, 4), np.float32)
        dx_img = np.empty((width, height, 4), np.float32)

    for y in range(height):
        for x in range(width):
            if mask is None or mask[x, y, 3] > 0:
                last_height_x = ogl[max(x - 1, 0), min(y, height - 1)]
                last_height_y = ogl[max(x, 0), min(y - 1, height - 1)]

                diff_x = (na[x, y, 0] - rmean) / (na[x, y, 2] - 0.5)
                diff_y = (na[x, y, 1] - gmean) / (na[x, y, 2] - 0.5)
                calc_height = (last_height_x + last_height_y) - diff_x - diff_y
                calc_height = calc_height / 2
                ogl[x, y] = calc_height
                if generated_test_images:
                    rgb = calc_height * 0.1 + 0.5
                    ogl_img[x, y] = [rgb, rgb, rgb, 1]

                last_height_x = dx[max(x - 1, 0), min(y, height - 1)]
                last_height_y = dx[max(x, 0), min(y - 1, height - 1)]

                diff_x = (na[x, y, 0] - rmean) / (na[x, y, 2] - 0.5)
                diff_y = (na[x, y, 1] - gmean) / (na[x, y, 2] - 0.5)
                calc_height = (last_height_x + last_height_y) - diff_x + diff_y
                calc_height = calc_height / 2
                dx[x, y] = calc_height
                if generated_test_images:
                    rgb = calc_height * 0.1 + 0.5
                    dx_img[x, y] = [rgb, rgb, rgb, 1]

    ogl_std = float(ogl.std())
    dx_std = float(dx.std())

    logger.debug(
        "Normal map classification metrics: ogl_std=%.6f, dx_std=%.6f for %s",
        ogl_std,
        dx_std,
        getattr(i, "name", "<image>"),
    )

    if generated_test_images:
        ogl_img = ogl_img.swapaxes(0, 1)
        ogl_img = ogl_img.flatten()

        dx_img = dx_img.swapaxes(0, 1)
        dx_img = dx_img.flatten()

        numpy_to_image(ogl_img, "OpenGL", width=width, height=height, channels=1)
        numpy_to_image(dx_img, "DirectX", width=width, height=height, channels=1)

    if abs(ogl_std) > abs(dx_std):
        return "DirectX"
    return "OpenGL"


def _restore_image_settings(
    ims: Any,
    settings: tuple[Any, Any, Any, Any, Any],
) -> None:
    """Restore image settings helper to reduce function statement count."""
    (
        file_format,
        quality,
        color_mode,
        compression,
        color_depth,
    ) = settings
    ims.file_format = file_format
    ims.quality = quality
    ims.color_mode = color_mode
    ims.compression = compression
    ims.color_depth = color_depth


def _finalize_image_paths(teximage: Any, filepath: str) -> None:
    """Update image filepaths and reload after saving."""
    teximage.filepath = filepath
    teximage.filepath_raw = filepath
    teximage.reload()


def make_possible_reductions_on_image(
    teximage: Any,
    input_filepath: str,
    *,
    do_reductions: bool = False,
    do_downscale: bool = False,
) -> None:
    """Reduce channels/bit depth or convert formats based on simple heuristics.

    This can convert PNG to JPEG for opaque images, reduce channels to BW when
    applicable, set PNG compression or JPEG quality, optionally downscale, and
    finally save the image to the provided filepath via `save_render`.

    Args:
        teximage: Blender image object (`bpy.types.Image`).
        input_filepath: Target file path for saving.
        do_reductions: When True, apply conversions like alpha drop, BW, JPEG.
        do_downscale: When True, downscale image by half with a minimum size.

    Raises:
        RuntimeError: If bpy is not available.
    """
    _require_bpy()

    colorspace = teximage.colorspace_settings.name
    teximage.colorspace_settings.name = "Non-Color"

    rs = bpy.context.scene.render  # type: ignore[attr-defined]
    ims = rs.image_settings

    orig_settings = (
        ims.file_format,
        ims.quality,
        ims.color_mode,
        ims.compression,
        ims.color_depth,
    )

    logger.debug(
        "Image name=%s, depth=%s, channels=%s",
        getattr(teximage, "name", "<image>"),
        getattr(teximage, "depth", "?"),
        getattr(teximage, "channels", "?"),
    )

    image_depth = find_image_depth(teximage)
    logger.debug("Found image depth: %s", image_depth)
    ims.color_mode = find_color_mode(teximage)
    logger.debug("Found color mode: %s", ims.color_mode)

    fp = input_filepath
    if do_reductions:
        na = image_to_numpy_flat(teximage)

        if can_erase_alpha(na) and teximage.file_format == "PNG":
            logger.info("Converting PNG to JPEG due to opaque alpha")
            _base, ext = os.path.splitext(fp)
            teximage["original_extension"] = ext

            fp = fp.replace(".png", ".jpg").replace(".PNG", ".jpg")

            teximage.name = teximage.name.replace(".png", ".jpg").replace(".PNG", ".jpg")

            teximage.file_format = "JPEG"
            ims.quality = JPEG_QUALITY_DEFAULT
            ims.color_mode = "RGB"
            image_depth = "8"

        if is_image_bw(na):
            ims.color_mode = "BW"

    ims.file_format = teximage.file_format
    ims.color_depth = image_depth

    if ims.file_format == "PNG":
        ims.compression = PNG_MAX_COMPRESSION
    if ims.file_format == "JPG":
        ims.quality = JPEG_QUALITY_DEFAULT

    if do_downscale:
        downscale(teximage)

    teximage.save_render(filepath=bpy.path.abspath(fp), scene=bpy.context.scene)  # type: ignore[attr-defined]
    if len(teximage.packed_files) > 0:
        teximage.unpack(method="REMOVE")

    _finalize_image_paths(teximage, fp)

    teximage.colorspace_settings.name = colorspace

    _restore_image_settings(ims, orig_settings)
