"""Tests for AI provider selection and quota fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers.testutils import ensure_src_on_path

ensure_src_on_path()

from blenderkit_server_utils.asset_validation.field_validation import ai_validation  # noqa: E402

HEURISTICS = SimpleNamespace(suspicion_score=0, reasons=[])
ROW = {"manufacturer": "Example Brand", "name": "Example Chair"}


def _make_client(**keys: str) -> ai_validation.AIClient:
    with (
        patch.object(ai_validation.config, "DEEPSEEK_API_KEY", keys.get("deepseek", "")),
        patch.object(ai_validation.config, "GROK_API_KEY", keys.get("grok", "")),
        patch.object(ai_validation.config, "OPENAI_API_KEY", keys.get("openai", "")),
        patch.object(ai_validation.config, "AI_PROVIDER", keys.get("provider", "deepseek")),
    ):
        return ai_validation.AIClient(enabled=True)


def _activate_openai(client: ai_validation.AIClient) -> bool:
    client.client = object()
    client._activate("openai")
    return True


class ProviderSelectionTests(unittest.TestCase):
    """Verify provider resolution and prompt shaping."""

    def test_deepseek_is_default_provider(self) -> None:
        client = _make_client(deepseek="ds-key", grok="grok-key", openai="openai-key")
        self.assertEqual(client.provider, "deepseek")
        self.assertEqual(client.model_name, ai_validation.config.DEEPSEEK_MODEL)
        self.assertTrue(client.enabled)

    def test_deepseek_has_no_web_search(self) -> None:
        client = _make_client(deepseek="ds-key")
        self.assertFalse(client.supports_web_search)

    def test_missing_keys_disable_client(self) -> None:
        client = _make_client()
        self.assertFalse(client.enabled)

    def test_prompt_omits_web_search_when_unsupported(self) -> None:
        system_prompt, instructions, _, _ = ai_validation._build_ai_prompts(
            ROW,
            HEURISTICS,
            web_search=False,
        )
        self.assertNotIn("web_search", instructions)
        self.assertNotIn("SEARCH STRATEGY", system_prompt)
        self.assertIn("json", instructions)


class FallbackTests(unittest.TestCase):
    """Verify provider fallback behavior without network requests."""

    def _run_fallback(self, client: ai_validation.AIClient, keys: dict[str, str]) -> tuple:
        request_results = [
            ai_validation.AICreditsExhaustedError(client.provider, "quota exhausted"),
            object(),
        ]
        with (
            patch.object(ai_validation.config, "DEEPSEEK_API_KEY", keys.get("deepseek", "")),
            patch.object(ai_validation.config, "GROK_API_KEY", keys.get("grok", "")),
            patch.object(ai_validation.config, "OPENAI_API_KEY", keys.get("openai", "")),
            patch.object(
                client,
                "_request_ai_response",
                side_effect=request_results,
            ) as request_mock,
            patch.object(client, "_extract_ai_text", return_value='{"valid": true}'),
            patch.object(
                ai_validation,
                "_parse_ai_decision",
                return_value=(True, "fallback worked", None),
            ),
        ):
            decision = client.judge(ROW, HEURISTICS)
        return decision, request_mock

    def test_deepseek_exhausted_falls_back_to_grok(self) -> None:
        keys = {"deepseek": "ds-key", "grok": "grok-key", "openai": "openai-key"}
        client = _make_client(**keys)
        decision, request_mock = self._run_fallback(client, keys)
        self.assertEqual(decision, (True, "fallback worked", None))
        self.assertEqual(client.provider, "grok")
        self.assertEqual(request_mock.call_count, 2)

    def test_deepseek_skips_to_openai_without_grok_key(self) -> None:
        keys = {"deepseek": "ds-key", "openai": "openai-key"}
        client = _make_client(**keys)
        with patch.object(client, "_configure_openai", side_effect=lambda: _activate_openai(client)):
            decision, request_mock = self._run_fallback(client, keys)
        self.assertEqual(decision, (True, "fallback worked", None))
        self.assertEqual(client.provider, "openai")
        self.assertEqual(request_mock.call_count, 2)

    def test_all_providers_exhausted_raises(self) -> None:
        client = _make_client(deepseek="ds-key")
        with (
            patch.object(ai_validation.config, "GROK_API_KEY", ""),
            patch.object(ai_validation.config, "OPENAI_API_KEY", ""),
            patch.object(
                client,
                "_request_ai_response",
                side_effect=ai_validation.AICreditsExhaustedError("deepseek", "quota exhausted"),
            ),
            self.assertRaises(ai_validation.AICreditsExhaustedError),
        ):
            client.judge(ROW, HEURISTICS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
