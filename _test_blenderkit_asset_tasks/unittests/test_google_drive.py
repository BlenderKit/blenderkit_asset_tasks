"""Unit tests for blenderkit_server_utils.google_drive.

Tests mock the Google API client and avoid any network calls.
"""

import os
import sys
import types
import unittest
from unittest import mock


class GoogleDriveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Ensure the src folder is importable as top-level (blenderkit_server_utils)
        here = os.path.dirname(__file__)
        src_root = os.path.abspath(os.path.join(here, "..", ".."))
        if src_root not in sys.path:
            sys.path.insert(0, src_root)

        # Build fake google modules to avoid external dependency
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_discovery = types.ModuleType("googleapiclient.discovery")
        fake_errors = types.ModuleType("googleapiclient.errors")
        fake_http = types.ModuleType("googleapiclient.http")

        class FakeHttpError(Exception):
            pass

        class FakeMediaFileUpload:
            def __init__(self, filename: str, *_, **__):
                self.filename = filename

        def fake_build(*_args, **_kwargs):
            return mock.sentinel.service

        fake_errors.HttpError = FakeHttpError
        fake_http.MediaFileUpload = FakeMediaFileUpload
        fake_discovery.build = fake_build

        fake_google = types.ModuleType("google")
        fake_oauth2 = types.ModuleType("google.oauth2")
        fake_service_account = types.ModuleType("google.oauth2.service_account")

        class FakeCredentials:
            @classmethod
            def from_service_account_info(cls, info, scopes=None):
                return {"creds": True, "info": info, "scopes": scopes}

        fake_service_account.Credentials = FakeCredentials

        # Register all fakes into sys.modules
        cls._orig_modules = {}
        for name, mod in [
            ("googleapiclient", fake_googleapiclient),
            ("googleapiclient.discovery", fake_discovery),
            ("googleapiclient.errors", fake_errors),
            ("googleapiclient.http", fake_http),
            ("google", fake_google),
            ("google.oauth2", fake_oauth2),
            ("google.oauth2.service_account", fake_service_account),
        ]:
            cls._orig_modules[name] = sys.modules.get(name)
            sys.modules[name] = mod

        # Import target module now that fakes are in place
        from blenderkit_server_utils import google_drive as gd  # type: ignore

        cls.gd = gd
        cls.HttpError = FakeHttpError

    @classmethod
    def tearDownClass(cls) -> None:
        # Restore any original modules to avoid side effects
        for name, original in getattr(cls, "_orig_modules", {}).items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def test_init_drive_missing_env_raises(self):
        with (
            mock.patch.dict(os.environ, {"GDRIVE_SERVICE_ACCOUNT_KEY": ""}, clear=False),
            self.assertRaises(RuntimeError),
        ):
            self.gd.init_drive()

    def test_init_drive_invalid_json_raises(self):
        with (
            mock.patch.dict(os.environ, {"GDRIVE_SERVICE_ACCOUNT_KEY": "not json"}, clear=False),
            self.assertRaises(RuntimeError),
        ):
            self.gd.init_drive()

    def test_init_drive_success(self):
        key = {"type": "service_account", "project_id": "x"}
        with (
            mock.patch.dict(os.environ, {"GDRIVE_SERVICE_ACCOUNT_KEY": self._to_json(key)}, clear=False),
            mock.patch.object(self.gd, "build", return_value=mock.sentinel.service),
        ):
            service = self.gd.init_drive()
        self.assertIs(service, mock.sentinel.service)

    def test_list_files_in_folder_success(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "1", "name": "foo"}, {"id": "2", "name": "bar"}],
        }
        out = self.gd.list_files_in_folder(service, "folder123", page_size=5)
        self.assertEqual(len(out), 2)
        service.files.return_value.list.assert_called_with(
            pageSize=5,
            q="'folder123' in parents and trashed=false",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name)",
        )

    def test_list_files_in_folder_http_error_returns_empty(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.side_effect = self.HttpError("boom")
        out = self.gd.list_files_in_folder(service, "folder123")
        self.assertEqual(out, [])

    def test_file_exists_true_false(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {"files": [{"id": "x"}]}
        self.assertTrue(self.gd.file_exists(service, "name.txt", "fid"))
        service.files.return_value.list.return_value.execute.return_value = {"files": []}
        self.assertFalse(self.gd.file_exists(service, "name.txt", "fid"))

    def test_ensure_folder_exists_existing(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "exists"}],
        }
        fid = self.gd.ensure_folder_exists(service, "MyFolder", parent_id="P", drive_id="D")
        self.assertEqual(fid, "exists")
        self.assertFalse(service.files.return_value.create.called)

    def test_ensure_folder_exists_create(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {"files": []}
        service.files.return_value.create.return_value.execute.return_value = {"id": "NEW"}
        fid = self.gd.ensure_folder_exists(service, "MyFolder", parent_id="P", drive_id="D")
        self.assertEqual(fid, "NEW")
        service.files.return_value.create.assert_called()

    def test_upload_file_to_folder_success(self):
        service = mock.MagicMock()
        service.files.return_value.create.return_value.execute.return_value = {"id": "F123"}
        base = os.path.join("local", "files")
        os_path = os.path.join(base, "foo.txt")
        out_id = self.gd.upload_file_to_folder(service, os_path, "FOLDER")
        self.assertEqual(out_id, "F123")
        kwargs = service.files.return_value.create.call_args.kwargs
        self.assertEqual(kwargs["body"]["name"], "foo.txt")
        self.assertEqual(kwargs["body"]["parents"], ["FOLDER"])
        self.assertTrue(kwargs.get("supportsAllDrives"))

    def test_upload_folder_to_drive_invokes_children(self):
        base = os.path.join("local")
        with (
            mock.patch.object(self.gd, "ensure_folder_exists", return_value="DST") as m_ensure,
            mock.patch.object(self.gd, "upload_file_to_folder") as m_upload_file,
            mock.patch.object(self.gd.os, "listdir", side_effect=lambda p: ["a.txt", "sub"] if p == base else []),
            mock.patch.object(self.gd.os.path, "isfile", side_effect=lambda p: p.endswith(".txt")),
            mock.patch.object(
                self.gd.os.path,
                "isdir",
                side_effect=lambda p: p == os.path.join(base, "sub"),
            ),
        ):
            service = mock.MagicMock()
            self.gd.upload_folder_to_drive(service, base, "ROOT", "DRIVE")

        # ensure_folder_exists is called for base and then for nested folder
        m_ensure.assert_any_call(service, os.path.basename(base), parent_id="ROOT", drive_id="DRIVE")
        m_ensure.assert_any_call(service, "sub", parent_id="DST", drive_id="DRIVE")
        self.assertEqual(m_ensure.call_count, 2)
        m_upload_file.assert_called_once()

    def test_delete_empty_folders_deletes(self):
        service = mock.MagicMock()
        service.files.return_value.list.return_value.execute.side_effect = [
            {"nextPageToken": None, "files": [{"id": "E", "name": "Empty"}, {"id": "N", "name": "NonEmpty"}]},
            {"files": []},
            {"files": [{"id": "x"}]},
        ]
        service.files.return_value.delete.return_value.execute.return_value = None

        self.gd.delete_empty_folders(service, "ROOT", recursive=False)

        service.files.return_value.delete.assert_called_once_with(fileId="E", supportsAllDrives=True)

    @staticmethod
    def _to_json(obj: dict) -> str:
        import json

        return json.dumps(obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
