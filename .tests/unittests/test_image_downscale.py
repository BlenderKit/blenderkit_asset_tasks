"""Each lower resolution must halve every texture, thin ones included.

downscale() skipped an image when halving would take either side to 128 px or
below, so a strip or decal never shrank: 'Scale Ruler 30 cm' (3629x115),
'Building decor grunge decal1' (1024x112) and 'Apple Pencil' (800x192) recorded
"no-size-gain" in the October 2026 re-run, every level as large as the original.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import image_utils  # noqa: E402


def _downscaled(width: int, height: int) -> tuple[int, int] | None:
    image = types.SimpleNamespace(size=(width, height), scale=mock.Mock())
    image_utils.downscale(image)
    return image.scale.call_args.args if image.scale.called else None


class DownscaleTests(unittest.TestCase):
    def test_a_square_texture_is_halved(self) -> None:
        self.assertEqual(_downscaled(2048, 2048), (1024, 1024))

    def test_a_thin_strip_is_halved_too(self) -> None:
        self.assertEqual(_downscaled(3629, 115), (1814, 58))

    def test_a_tall_strip_is_halved_too(self) -> None:
        self.assertEqual(_downscaled(256, 1024), (128, 512))

    def test_a_one_pixel_side_stays_one_pixel(self) -> None:
        self.assertEqual(_downscaled(1024, 1), (512, 1))

    def test_a_texture_already_at_the_minimum_is_left_alone(self) -> None:
        self.assertIsNone(_downscaled(image_utils.MIN_DOWNSCALE_SIZE * 2, 64))


if __name__ == "__main__":
    unittest.main()
