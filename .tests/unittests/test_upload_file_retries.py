"""upload_file must retry the API calls around the S3 transfer, not only the transfer.

On 7 Oct 2026 the 4K resolution of 'Stripe Gold Velvet' failed 46 ms into its upload:
the POST that creates the upload never reached the server, nothing retried it, and the
asset was left with only its 8K resolution (a resolutions_sequence_gap check row).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any
from unittest import mock

import requests
from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils import upload  # noqa: E402

UPLOAD_DATA = {"name": "Velvet", "displayName": "Stripe Gold Velvet", "token": "test-key", "id": "asset-id"}


def _response(status_code: int, body: dict[str, Any] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = b"" if body is None else json.dumps(body).encode()
    return response


CREATED = _response(201, {"id": "upload-id", "s3UploadUrl": "https://s3.example/upload"})
CONFIRMED = _response(200, {})


class UploadFileRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        handle, self.file_path = tempfile.mkstemp(suffix="_4k.blend")
        os.write(handle, b"blend")
        os.close(handle)
        self.addCleanup(os.remove, self.file_path)
        self.descriptor = {"type": "resolution_4K", "index": 0, "file_path": self.file_path}
        for patch in (
            mock.patch.object(upload.time, "sleep"),
            mock.patch.object(upload.utils, "get_headers", return_value={}),
            mock.patch.object(upload.paths, "get_api_url", return_value="https://api.example"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        session = mock.patch.object(upload.requests, "Session")
        self.put = session.start().return_value.put
        self.put.return_value = _response(200)
        self.addCleanup(session.stop)

    def _upload(self, *post_results: requests.Response | Exception) -> bool:
        with mock.patch.object(upload.requests, "post", side_effect=post_results) as self.post:
            return upload.upload_file(UPLOAD_DATA, self.descriptor)

    def test_a_create_call_that_does_not_connect_is_retried(self) -> None:
        self.assertTrue(self._upload(requests.exceptions.ConnectionError("reset"), CREATED, CONFIRMED))
        self.put.assert_called_once()

    def test_a_create_call_answered_with_an_error_page_is_retried(self) -> None:
        self.assertTrue(self._upload(_response(502), CREATED, CONFIRMED))

    def test_a_failed_confirmation_is_retried(self) -> None:
        self.assertTrue(self._upload(CREATED, _response(500), CONFIRMED))
        self.assertEqual(self.post.call_count, 3)

    def test_an_upload_that_never_confirms_fails(self) -> None:
        self.assertFalse(self._upload(CREATED, *[_response(500)] * upload.UPLOAD_ATTEMPTS))


if __name__ == "__main__":
    unittest.main()
