"""A texture too large for Blender's Python pixel access must not stop its resolutions.

'Acoustic Guitar Taylor' carries a 25,600 px square mask: 2.6 billion RGBA values,
more than pixels.foreach_get() can count, so the reductions check raised
"expected sequence size -1673527296" and the asset got no resolutions (October 2026).
"""

from __future__ import annotations

import types
import unittest

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import image_utils  # noqa: E402


def _image(width: int, height: int, channels: int = 4) -> types.SimpleNamespace:
    return types.SimpleNamespace(size=(width, height), channels=channels)


class PythonCanReadPixelsTests(unittest.TestCase):
    def test_a_16k_rgba_texture_fits(self) -> None:
        self.assertTrue(image_utils.python_can_read_pixels(_image(16384, 16384)))

    def test_a_25600_px_rgba_texture_does_not(self) -> None:
        self.assertFalse(image_utils.python_can_read_pixels(_image(25600, 25600)))

    def test_the_channel_count_decides_at_the_limit(self) -> None:
        self.assertTrue(image_utils.python_can_read_pixels(_image(25600, 25600, channels=3)))
        self.assertFalse(image_utils.python_can_read_pixels(_image(25600, 25600, channels=4)))


if __name__ == "__main__":
    unittest.main()
