"""Unit tests for blender_bg_scripts.test_addon_bg helpers.

We mock bpy and addon_utils to test pure functions without Blender.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from helpers.testutils import ensure_src_on_path, install_bpy_mock


class TestAddonBgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_src_on_path()
        cls.bpy = install_bpy_mock()
        from blender_bg_scripts import test_addon_bg as mod  # type: ignore

        cls.mod = mod

    def test_load_input_json(self):
        payload = {"x": 1}
        with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(payload))):
            out = self.mod._load_input_json("in.json")
        self.assertEqual(out, payload)

    def test_write_output_json(self):
        payload = {"result": "ok"}
        m = mock.mock_open()
        with mock.patch("builtins.open", m):
            self.mod._write_output_json("out.json", payload)
        handle = m()
        handle.write.assert_called()  # wrote something

    def test_install_enable_disable_happy_path(self):
        # Ensure addon_utils functions are called
        with (
            mock.patch.object(self.mod.bpy.ops.extensions, "package_install_files", return_value=None),
            mock.patch.object(self.mod.addon_utils, "enable", return_value=object()) as m_en,
            mock.patch.object(self.mod.addon_utils, "disable", return_value=None) as m_dis,
        ):
            self.assertEqual(self.mod.install_addon("x.zip"), "")
            self.assertEqual(self.mod.enable_addon("extid"), "")
            self.assertEqual(self.mod.disable_addon("extid"), "")
        self.assertTrue(m_en.called)
        self.assertTrue(m_dis.called)

    def test_install_failure_returns_message(self):
        # Patch logger.exception to avoid noisy traceback output while still testing failure path
        with (
            mock.patch.object(self.mod.logger, "exception") as m_exc,
            mock.patch.object(self.mod.bpy.ops.extensions, "package_install_files", side_effect=RuntimeError),
        ):
            msg = self.mod.install_addon("x.zip")
        self.assertIn("failed", msg.lower())
        self.assertTrue(m_exc.called)

    def test_enable_disable_handles_exceptions(self):
        # Patch logger.exception to avoid noisy traceback output while still verifying it's called
        with (
            mock.patch.object(self.mod.logger, "exception") as m_exc,
            mock.patch.object(self.mod.addon_utils, "enable", side_effect=Exception),
        ):
            msg = self.mod.enable_addon("extid")
            self.assertIn("failed", msg.lower())
            self.assertTrue(m_exc.called)

        with (
            mock.patch.object(self.mod.logger, "exception") as m_exc,
            mock.patch.object(self.mod.addon_utils, "disable", side_effect=Exception),
        ):
            msg = self.mod.disable_addon("extid")
            self.assertIn("failed", msg.lower())
            self.assertTrue(m_exc.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
