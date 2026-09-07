"""Tests for AI provider selection and quota fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils.asset_validation.field_validation import ai_validation  # noqa: E402


class AIClientTests(unittest.TestCase):
    """Verify AI provider fallback behavior without network requests."""

    def test_grok_credits_exhausted_falls_back_to_openai(self) -> None:
        """Retry the request through OpenAI when Grok quota is exhausted."""
        with (
            patch.object(ai_validation.config, "GROK_API_KEY", "grok-key"),
            patch.object(
                ai_validation.config,
                "OPENAI_API_KEY",
                "openai-key",
            ),
        ):
            client = ai_validation.AIClient(enabled=True)

        def configure_openai() -> bool:
            client.provider = "openai"
            client.client = object()
            client.model_name = "gpt-test"
            return True

        request_results = [
            ai_validation.AICreditsExhaustedError("grok", "quota exhausted"),
            object(),
        ]
        with (
            patch.object(client, "_configure_openai", side_effect=configure_openai),
            patch.object(
                client,
                "_request_ai_response",
                side_effect=request_results,
            ) as request_mock,
            patch.object(
                client,
                "_extract_ai_text",
                return_value='{"valid": true}',
            ),
            patch.object(
                ai_validation,
                "_parse_ai_decision",
                return_value=(True, "fallback worked", None),
            ),
        ):
            decision = client.judge(
                {"manufacturer": "Example Brand"},
                SimpleNamespace(suspicion_score=0, reasons=[]),
            )

        self.assertEqual(decision, (True, "fallback worked", None))
        self.assertEqual(client.provider, "openai")
        self.assertEqual(request_mock.call_count, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
