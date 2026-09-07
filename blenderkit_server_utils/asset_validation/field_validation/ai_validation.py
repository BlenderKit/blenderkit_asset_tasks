"""AI validation helpers for manufacturer field checks.

This module wraps DeepSeek, OpenAI, and Grok calls with a common interface
used by validate_fields.py.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from collections.abc import Mapping
from pprint import pformat
from typing import Any, Protocol

import requests

from blenderkit_server_utils import config, log  # type: ignore

logger = log.create_logger(__name__)

AI_LIMIT_PER_ITEM = 100
AI_LOG_PREVIEW = 800
HTTP_PAYMENT_REQUIRED = 402
HTTP_TOO_MANY_REQUESTS = 429
AI_REQUEST_PREVIEW = 600
AI_ERROR_DETAIL_PREVIEW = 400
CODE_FENCE_SPLIT_MAX = 2
AI_MAX_RETRIES = 4
AI_RETRY_BASE_SECONDS = 5
AI_RETRY_MAX_SECONDS = 20
AI_RETRY_JITTER_SECONDS = 2

AI_RESPONSE_SCHEMA = {
    "name": "validation_decision",
    "schema": {
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1},
            "corrections": {
                "type": ["object", "null"],
                "properties": {
                    "manufacturer": {"type": ["string", "null"]},
                    "designer": {"type": ["string", "null"]},
                    "collection": {"type": ["string", "null"]},
                    "variant": {"type": ["string", "null"]},
                    "year": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "required": ["valid", "reason"],
        "additionalProperties": False,
    },
}

CORRECTION_FIELDS = {"manufacturer", "designer", "collection", "variant", "year"}

AI_RESPONSE_SCHEMA_TEXT = json.dumps(AI_RESPONSE_SCHEMA["schema"], separators=(",", ":"))

AI_PROVIDER_ENV = config.AI_PROVIDER

OPENAI_DEFAULT_MODEL = "gpt-5"
GROK_DEFAULT_MODEL = "grok-4-1-fast-reasoning"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"

GROK_ENDPOINT = "https://api.x.ai/v1/responses"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MAX_TOKENS = 2048

SUPPORTED_AI_PROVIDERS = ("deepseek", "grok", "openai")

# Tried in order when the active provider runs out of credits.
AI_PROVIDER_FALLBACK_ORDER = ("deepseek", "grok", "openai")

# Providers exposing a server-side web_search tool.
PROVIDERS_WITH_WEB_SEARCH = frozenset({"grok", "openai"})


class HeuristicSummary(Protocol):
    """Protocol for heuristic summaries used by the AI client."""

    suspicion_score: int
    reasons: list[str]


class _GrokHttpError(RuntimeError):
    """HTTP error wrapper for Grok responses."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _DeepSeekHttpError(RuntimeError):
    """HTTP error wrapper for DeepSeek responses."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class AICreditsExhaustedError(RuntimeError):
    """Fatal AI provider error indicating credits/quota are exhausted.

    Raised when the provider rejects requests because the account is out of
    credits or has reached its spending limit. This must not be retried and
    must abort the validation run so CI surfaces the failure.
    """

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"{provider} AI credits/quota exhausted: {detail}")
        self.provider = provider
        self.detail = detail


# Fragments that indicate a non-recoverable credits/quota condition.
_AI_CREDITS_EXHAUSTED_MARKERS = (
    "credits",
    "monthly spending limit",
    "spending limit",
    "insufficient_quota",
    "insufficient quota",
    "insufficient balance",
    "resource has been exhausted",
    "billing",
)


def _is_credits_exhausted_message(message: str) -> bool:
    """Return True when an error message indicates exhausted AI credits.

    Args:
        message: Raw error message or response body.

    Returns:
        True when the message matches a known credits/quota exhaustion pattern.
    """
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _AI_CREDITS_EXHAUSTED_MARKERS)


def _sanitize_prompt_value(value: str | None) -> str:
    """Clean values placed into AI prompts.

    Args:
        value: Raw value to sanitize.

    Returns:
        Sanitized prompt value.
    """
    if not value:
        return ""
    cleaned = value.replace("|", " ").replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    sanitized = cleaned.strip()
    if len(sanitized) > AI_LIMIT_PER_ITEM:
        sanitized = sanitized[:AI_LIMIT_PER_ITEM] + "..."
    return sanitized


def _build_search_query(row: Mapping[str, str]) -> str:
    """Create a deterministic search query for the AI tool call.

    Args:
        row: Asset metadata mapping.

    Returns:
        Search query string.
    """
    parts: list[str] = []
    manufacturer = _sanitize_prompt_value(row.get("manufacturer"))
    designer = _sanitize_prompt_value(row.get("designer"))
    collection = _sanitize_prompt_value(row.get("collection"))
    variant = _sanitize_prompt_value(row.get("variant"))
    year = _sanitize_prompt_value(row.get("year"))
    name = _sanitize_prompt_value(row.get("name"))
    if manufacturer:
        parts.append(manufacturer)
    if variant:
        parts.append(variant)
    elif collection:
        parts.append(collection)
    if name and name.lower() not in (variant or "").lower():
        parts.append(name)
    if designer:
        parts.append(designer)
    if year:
        parts.append(year)
    if not parts and name:
        parts.append(name)
    query = " ".join(parts)
    return query


def _build_ai_context(row: Mapping[str, str], heuristics: HeuristicSummary) -> dict[str, object]:
    """Bundle asset data and heuristics for the AI request.

    Args:
        row: Asset metadata mapping.
        heuristics: Heuristic summary for the asset.

    Returns:
        AI request context dictionary.
    """
    context = {
        "search_query": _build_search_query(row),
        "asset": {
            "asset_id": row.get("asset_id", ""),
            "name": _sanitize_prompt_value(row.get("name")),
            "manufacturer": _sanitize_prompt_value(row.get("manufacturer")),
            "designer": _sanitize_prompt_value(row.get("designer")),
            "collection": _sanitize_prompt_value(row.get("collection")),
            "variant": _sanitize_prompt_value(row.get("variant")),
            "year": _sanitize_prompt_value(row.get("year")),
            "author_name": _sanitize_prompt_value(row.get("author_name")),
            "description": _sanitize_prompt_value(row.get("description")),
        },
        "heuristics": {
            "score": heuristics.suspicion_score,
            "reasons": heuristics.reasons,
        },
    }
    return context


def _strip_code_fence(value: str) -> str:
    """Remove optional ```json fences from AI responses.

    Args:
        value: Raw response string.

    Returns:
        Cleaned response string.
    """
    cleaned = value.strip()
    cleaned = _strip_grok_inline_citations(cleaned)
    if cleaned.startswith("```"):
        parts = cleaned.split("```", CODE_FENCE_SPLIT_MAX)
        if len(parts) >= CODE_FENCE_SPLIT_MAX:
            candidate = parts[1]
            if candidate.startswith("json"):
                candidate = candidate[len("json") :]
            return candidate.strip()
    return cleaned


def _strip_grok_inline_citations(text: str) -> str:
    """Remove Grok inline citation tags from text.

    Args:
        text: Raw response text.

    Returns:
        Text without Grok inline citation tags.
    """
    return re.sub(r"<grok:render[^>]*>.*?</grok:render>", "", text, flags=re.DOTALL)


def _extract_json_object(raw: str) -> str:
    """Extract the first JSON object from a string.

    Args:
        raw: Raw model output.

    Returns:
        JSON object substring.
    """
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return raw
    return match.group(0)


def _is_retryable_ai_exception(error: Exception) -> bool:
    """Return True when the AI exception should be retried.

    Args:
        error: Exception raised by the AI client.

    Returns:
        True when the failure is likely transient.
    """
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in {408, 429, 500, 502, 503, 504}:
        return True
    error_name = error.__class__.__name__.lower()
    if "timeout" in error_name or "ratelimit" in error_name:
        return True
    message = str(error).lower()
    return "timeout" in message or "rate limit" in message


def _get_ai_provider() -> str:
    """Return the configured AI provider name.

    Returns:
        Normalized provider name.
    """
    raw = (config.AI_PROVIDER or os.getenv("AI_PROVIDER", "deepseek")).lower()
    if raw not in SUPPORTED_AI_PROVIDERS:
        logger.warning("Unknown AI_PROVIDER=%r; defaulting to deepseek", raw)
        return "deepseek"
    return raw


def _get_ai_model(provider: str) -> str:
    """Return the model name for the configured provider.

    Args:
        provider: Provider name.

    Returns:
        Model name to use for requests.
    """
    if provider == "deepseek":
        model_name = config.DEEPSEEK_MODEL or DEEPSEEK_DEFAULT_MODEL
        return model_name
    if provider == "grok":
        model_name = config.GROK_MODEL or GROK_DEFAULT_MODEL
        return model_name
    model_name = config.OPENAI_MODEL or OPENAI_DEFAULT_MODEL
    return model_name


def _build_ai_prompts(
    row: Mapping[str, str],
    heuristics: HeuristicSummary,
    *,
    web_search: bool = True,
) -> tuple[str, str, str, str]:
    """Build shared prompt content for AI requests.

    Args:
        row: Asset field mapping.
        heuristics: Heuristic summary for the asset.
        web_search: Whether the provider exposes a server-side web_search tool.

    Returns:
        Tuple of (system_prompt, instructions, search_query, user_payload).
    """
    payload = _build_ai_context(row, heuristics)
    search_query = payload.get("search_query") or ""
    user_payload = json.dumps(payload, ensure_ascii=False)
    intro = (
        "You verify manufacturer/designer claims for BlenderKit assets. "
        "Reject self-promotional, placeholder, or unverifiable entries. "
        "If manufacturer in metadata matches a known brand, it's likely valid. "
        "Also accept historic or defunct manufacturers when the product name "
        "matches known historic items, including evidence from collector or "
        "marketplace listings (e.g., museum catalogs, auction archives, eBay). "
    )
    search_block = (
        "\n\nSEARCH STRATEGY:\n"
        "You MUST call web_search at least once with the provided search_query. "
        "If the first search yields no useful results, you SHOULD call web_search "
        "again with alternative queries. Try these strategies:\n"
        "1. Search for the manufacturer name + product variant/name together\n"
        "2. Search for just the manufacturer name to verify it exists\n"
        "3. Search for the product variant or collection name alone\n"
        "4. Try alternative spellings or common misspellings of the manufacturer\n"
        "5. Search for the manufacturer's official website or product catalog\n"
        "Do NOT give up after a single failed search. A manufacturer may exist "
        "even if one specific query fails.\n"
    )
    offline_block = (
        "\n\nEVIDENCE:\n"
        "You have no web search tool available. Judge using your own knowledge of "
        "manufacturers, designers, product lines, and design history. Consider "
        "alternative spellings and common misspellings before rejecting a brand. "
        "Never invent sources or cite pages you cannot recall. If you are not "
        "reasonably confident the manufacturer exists, set valid=false and say so.\n"
    )
    correction_block = (
        "\n\nIMPORTANT - Correction Mode:\n"
        "When the submitted data is almost correct but contains small errors "
        "(misspelled manufacturer, wrong collection name, incorrect variant, etc.), "
        "you MUST set valid=true and provide a 'corrections' object with the "
        "corrected values. Only include fields that need correction; set unchanged "
        "fields to null.\n"
        "Examples of correctable issues:\n"
        "- Misspelled manufacturer: 'Hermna Miller' -> 'Herman Miller'\n"
        "- Wrong collection name: 'Alperto' when the actual collection is 'Alperton'\n"
        "- Typo in designer name: 'Charles Eamse' -> 'Charles Eames'\n"
        "- Minor year error when the correct year can be confirmed\n"
        "- Incorrect capitalization or spacing: 'urban+' -> 'Urban +'\n"
        "If you cannot verify ANY of the data or the data is clearly fabricated, "
        "set valid=false with no corrections. If data is partially verifiable and "
        "you can confidently correct the remaining fields, set valid=true with corrections.\n"
        "Set corrections to null when all submitted values are already correct."
    )
    system_prompt = intro + (search_block if web_search else offline_block) + correction_block
    if web_search:
        lead_in = (
            "Call web_search with the provided search_query. If results are insufficient, "
            "call web_search again with alternative queries to verify the manufacturer and product. "
        )
    else:
        lead_in = "Verify the manufacturer and product from your own knowledge. "
    instructions = (
        f"{lead_in}"
        f"Respond with strict minified json matching schema: {AI_RESPONSE_SCHEMA_TEXT}. "
        "Do not emit explanations or reasoning outside the JSON body."
    )
    prompts = (system_prompt, instructions, search_query, user_payload)
    return prompts


def _build_openai_message_input(
    system_prompt: str,
    instructions: str,
    search_query: str,
    user_payload: str,
) -> list[dict[str, Any]]:
    """Build OpenAI Responses input payload.

    Args:
        system_prompt: System prompt string.
        instructions: Instruction string.
        search_query: Search query string.
        user_payload: JSON payload string.

    Returns:
        Input list for OpenAI Responses API.
    """
    message_input: list[dict[str, Any]] = [
        {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": instructions},
                {"type": "input_text", "text": f"search_query: {search_query or 'n/a'}"},
                {"type": "input_text", "text": user_payload},
            ],
        },
    ]
    return message_input


def _build_grok_message_input(
    system_prompt: str,
    instructions: str,
    search_query: str,
    user_payload: str,
) -> list[dict[str, str]]:
    """Build Grok Responses input payload.

    Args:
        system_prompt: System prompt string.
        instructions: Instruction string.
        search_query: Search query string.
        user_payload: JSON payload string.

    Returns:
        Input list for Grok Responses API.
    """
    user_parts = [
        instructions,
        f"search_query: {search_query or 'n/a'}",
        user_payload,
    ]
    user_message = "\n".join(user_parts)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    return messages


def _extract_grok_text(response_json: Mapping[str, Any]) -> str:
    """Extract output text from a Grok Responses API payload.

    Args:
        response_json: Parsed response JSON payload.

    Returns:
        The output text content.
    """
    output_text = response_json.get("output_text")
    if output_text:
        return str(output_text)
    outputs = response_json.get("output", [])
    for output in outputs or []:
        content = output.get("content", []) if isinstance(output, Mapping) else []
        for chunk in content or []:
            if chunk.get("type") == "output_text" and chunk.get("text"):
                return str(chunk.get("text"))
    return ""


def _build_deepseek_message_input(
    system_prompt: str,
    instructions: str,
    search_query: str,
    user_payload: str,
) -> list[dict[str, str]]:
    """Build DeepSeek chat completion messages.

    Args:
        system_prompt: System prompt string.
        instructions: Instruction string.
        search_query: Search query string.
        user_payload: JSON payload string.

    Returns:
        Message list for the DeepSeek chat completions API.
    """
    user_parts = [
        instructions,
        f"search_query: {search_query or 'n/a'}",
        user_payload,
    ]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
    return messages


def _extract_deepseek_text(response_json: Mapping[str, Any]) -> str:
    """Extract assistant text from a DeepSeek chat completion payload.

    Args:
        response_json: Parsed response JSON payload.

    Returns:
        The assistant message content.
    """
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, Mapping) else None
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content") or ""
    return str(content)


def _describe_ai_exception(error: Exception) -> str:
    """Return short diagnostic string extracted from AI errors.

    Args:
        error: Raised exception.

    Returns:
        Short diagnostic string.
    """
    parts: list[str] = [error.__class__.__name__]
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status is not None:
        parts.append(f"status={status}")
    request_id = getattr(error, "request_id", None)
    if not request_id:
        response = getattr(error, "response", None)
        request_id = getattr(response, "request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")
    message = str(getattr(error, "message", "")) or str(error)
    if message:
        snippet = message[:AI_ERROR_DETAIL_PREVIEW]
        suffix = "..." if len(message) > AI_ERROR_DETAIL_PREVIEW else ""
        parts.append(f"message={snippet}{suffix}")
    detail = " ".join(parts)
    return detail


def _describe_incomplete_response(response: Any) -> str:
    """Summarize why the AI response did not complete.

    Args:
        response: Provider response payload.

    Returns:
        Summary string.
    """

    def _pick(source: Any, attribute: str) -> Any:
        if source is None:
            return None
        if isinstance(source, Mapping):
            return source.get(attribute)
        return getattr(source, attribute, None)

    parts: list[str] = []
    status = getattr(response, "status", None)
    if status:
        parts.append(f"status={status}")
    incomplete = getattr(response, "incomplete_details", None)
    reason = _pick(incomplete, "reason")
    if reason:
        parts.append(f"reason={reason}")
    limit = getattr(response, "max_output_tokens", None)
    if limit:
        parts.append(f"max_tokens={limit}")
    usage = getattr(response, "usage", None)
    output_tokens = _pick(usage, "output_tokens")
    if output_tokens:
        parts.append(f"output_tokens={output_tokens}")
    if not parts:
        return "response incomplete"
    summary = " ".join(parts)
    return summary


def _extract_response_text(response: Any) -> str:
    """Return unified string content from a Responses API call.

    Args:
        response: Provider response payload.

    Returns:
        Output text content.
    """
    text_value = getattr(response, "output_text", "")
    return text_value


def _parse_ai_decision(raw: str) -> tuple[bool, str, dict[str, str] | None] | None:
    """Parse boolean verdict, reason, and optional corrections from AI JSON response.

    Args:
        raw: Raw response string.

    Returns:
        Decision tuple with corrections dict or None when parsing fails.
    """
    try:
        json_text = _extract_json_object(_strip_code_fence(raw))
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.exception("AI response was not valid JSON")
        return None
    valid_value = bool(data.get("valid", False))
    reason_text = str(data.get("reason", "AI decision")).strip() or "AI decision"
    corrections = _extract_corrections(data.get("corrections"))
    decision = (valid_value, reason_text, corrections)
    return decision


def _extract_corrections(raw_corrections: Any) -> dict[str, str] | None:
    """Extract and validate corrections from AI response data.

    Only keeps non-null string values for known correction fields.

    Args:
        raw_corrections: Raw corrections object from AI response.

    Returns:
        Dict of field corrections or None when no corrections present.
    """
    if not raw_corrections or not isinstance(raw_corrections, dict):
        return None
    cleaned: dict[str, str] = {}
    for field in CORRECTION_FIELDS:
        value = raw_corrections.get(field)
        if value is not None and isinstance(value, str) and value.strip():
            cleaned[field] = value.strip()
    if not cleaned:
        return None
    return cleaned


class AIClient:
    """Minimal AI wrapper used for fallback validation."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.provider = _get_ai_provider()
        self.client = None
        self.grok_api_key = ""
        self.deepseek_api_key = ""
        self.timeout_s = float(os.getenv("VALIDATOR_AI_TIMEOUT", "45"))
        self.model_name = _get_ai_model(self.provider)
        self.log_raw = os.getenv("VALIDATOR_LOG_AI") == "1"
        self.attempted_providers: set[str] = set()

        if not self.enabled:
            return
        self.attempted_providers.add(self.provider)
        if not self._configure_provider(self.provider):
            self.enabled = False

    @property
    def supports_web_search(self) -> bool:
        """Whether the active provider exposes a server-side web_search tool."""
        return self.provider in PROVIDERS_WITH_WEB_SEARCH

    def _configure_provider(self, provider: str) -> bool:
        """Configure credentials for the given provider.

        Args:
            provider: Provider name to configure.

        Returns:
            True when the provider is usable.
        """
        if provider == "deepseek":
            return self._configure_deepseek()
        if provider == "grok":
            return self._configure_grok()
        return self._configure_openai()

    def _activate(self, provider: str) -> None:
        """Mark a provider as active and refresh its model name.

        Args:
            provider: Provider name that has been configured.
        """
        self.provider = provider
        self.model_name = _get_ai_model(provider)

    def _configure_deepseek(self) -> bool:
        """Configure DeepSeek access and return whether it is available."""
        api_key = config.DEEPSEEK_API_KEY
        if not api_key:
            logger.warning("AI validation requested but DEEPSEEK_API_KEY is missing")
            return False
        self.deepseek_api_key = api_key
        self._activate("deepseek")
        return True

    def _configure_grok(self) -> bool:
        """Configure Grok access and return whether it is available."""
        api_key = config.GROK_API_KEY
        if not api_key:
            logger.warning("AI validation requested but XAI_API_KEY is missing")
            return False
        self.grok_api_key = api_key
        self._activate("grok")
        return True

    def _configure_openai(self) -> bool:
        """Configure the OpenAI client and return whether it is available."""
        api_key = config.OPENAI_API_KEY
        if not api_key:
            logger.warning("AI validation requested but OPENAI_API_KEY is missing")
            return False
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            logger.warning(
                "OpenAI SDK is not installed; run `pip install openai` to enable AI validation",
            )
            return False
        self.client = OpenAI(api_key=api_key)  # type: ignore[call-arg]
        self._activate("openai")
        return True

    def _fallback_to_next_provider(self) -> bool:
        """Switch to the next configured provider after credits are exhausted.

        Returns:
            True when another provider was activated.
        """
        exhausted = self.provider
        for candidate in AI_PROVIDER_FALLBACK_ORDER:
            if candidate in self.attempted_providers:
                continue
            self.attempted_providers.add(candidate)
            if self._configure_provider(candidate):
                logger.warning(
                    "%s credits/quota exhausted; falling back to %s",
                    exhausted,
                    candidate,
                )
                return True
        return False

    def judge(  # noqa: C901
        self,
        row: Mapping[str, str],
        heuristics: HeuristicSummary,
    ) -> tuple[bool, str, dict[str, str] | None] | None:
        """Return AI verdict with optional corrections when available.

        Args:
            row: Asset metadata mapping.
            heuristics: Heuristic summary for the asset.

        Returns:
            Tuple of (valid, reason, corrections) or None.

        Raises:
            AICreditsExhaustedError: If the AI provider rejects the request
                because account credits or the spending limit are exhausted.
                Re-raised so the caller can abort the validation run.
        """
        if not self.enabled:
            return None
        if not self._has_credentials():
            return None
        search_query = _build_search_query(row)
        response = None
        attempt = 0
        while attempt < AI_MAX_RETRIES:
            attempt += 1
            try:
                response = self._request_ai_response(row, heuristics)
                break
            except AICreditsExhaustedError:
                if self._fallback_to_next_provider():
                    attempt = 0
                    continue
                # Fatal: credits/quota exhausted on every configured provider.
                # Abort so CI fails loudly and notifies maintainers.
                logger.critical(
                    "AI provider %s credits exhausted; aborting validation run",
                    self.provider,
                )
                raise
            except Exception as exc:
                detail = _describe_ai_exception(exc)
                should_retry = _is_retryable_ai_exception(exc)
                if _is_credits_exhausted_message(detail) or _is_credits_exhausted_message(str(exc)):
                    logger.critical(
                        "AI provider %s credits exhausted; aborting validation run",
                        self.provider,
                    )
                    raise AICreditsExhaustedError(self.provider, detail) from exc
                logger.exception(
                    "AI validation request failed (model=%s, query=%r) detail=%s",
                    self.model_name,
                    search_query or "n/a",
                    detail,
                )
                if not should_retry or attempt >= AI_MAX_RETRIES:
                    return None
                delay_seconds = min(
                    AI_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    AI_RETRY_MAX_SECONDS,
                )
                delay_seconds += random.uniform(0, AI_RETRY_JITTER_SECONDS)  # noqa: S311
                logger.warning(
                    "Retrying AI validation in %.2f seconds (attempt %s/%s)",
                    delay_seconds,
                    attempt,
                    AI_MAX_RETRIES,
                )
                time.sleep(delay_seconds)
        if response is None:
            return None
        content = self._extract_ai_text(response)
        if not content:
            logger.warning("AI response was empty; skipping decision")
            return None
        decision = _parse_ai_decision(content)
        return decision

    def _has_credentials(self) -> bool:
        """Return True when the active provider has usable credentials."""
        if self.provider == "deepseek":
            return bool(self.deepseek_api_key)
        if self.provider == "grok":
            return bool(self.grok_api_key)
        return self.client is not None

    def _request_ai_response(
        self,
        row: Mapping[str, str],
        heuristics: HeuristicSummary,
    ) -> Any:
        """Issue a request to the active provider.

        Args:
            row: Asset metadata mapping.
            heuristics: Heuristic summary for the asset.

        Returns:
            Provider response payload.
        """
        system_prompt, instructions, search_query, user_payload = _build_ai_prompts(
            row,
            heuristics,
            web_search=self.supports_web_search,
        )
        payload_preview = user_payload[:AI_REQUEST_PREVIEW]
        payload_suffix = "..." if len(user_payload) > AI_REQUEST_PREVIEW else ""
        logger.debug(
            "AI request (%s/%s) query=%r heuristics=%s payload=%s%s",
            self.provider,
            self.model_name,
            search_query or "n/a",
            heuristics.suspicion_score,
            payload_preview,
            payload_suffix,
        )
        tools: Any = [{"type": "web_search"}] if search_query and self.supports_web_search else None
        if self.provider == "deepseek":
            return self._request_deepseek(system_prompt, instructions, search_query, user_payload)
        if self.provider == "grok":
            return self._request_grok(system_prompt, instructions, search_query, user_payload, tools)
        message_input = _build_openai_message_input(
            system_prompt,
            instructions,
            search_query,
            user_payload,
        )
        response = self.client.responses.create(  # type: ignore[call-arg]
            model=self.model_name,
            input=message_input,
            tools=tools,
            timeout=self.timeout_s,
            reasoning={"effort": "low"},
            include=["web_search_call.action.sources"],
        )
        return response

    def _request_grok(
        self,
        system_prompt: str,
        instructions: str,
        search_query: str,
        user_payload: str,
        tools: Any,
    ) -> Any:
        """Call the Grok Responses API.

        Args:
            system_prompt: System prompt string.
            instructions: Instruction string.
            search_query: Search query string.
            user_payload: JSON payload string.
            tools: Optional tool list.

        Returns:
            Parsed response JSON payload.

        Raises:
            AICreditsExhaustedError: If Grok rejects the request because
                credits or the spending limit are exhausted.
            _GrokHttpError: If the Grok API request fails.
        """
        grok_input = _build_grok_message_input(
            system_prompt,
            instructions,
            search_query,
            user_payload,
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": grok_input,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(
            GROK_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=self.timeout_s,
        )
        if not response.ok:
            if response.status_code == HTTP_TOO_MANY_REQUESTS and _is_credits_exhausted_message(response.text):
                raise AICreditsExhaustedError("grok", response.text)
            raise _GrokHttpError(response.status_code, response.text)
        response_json = response.json()
        return response_json

    def _request_deepseek(
        self,
        system_prompt: str,
        instructions: str,
        search_query: str,
        user_payload: str,
    ) -> Any:
        """Call the DeepSeek chat completions API.

        Args:
            system_prompt: System prompt string.
            instructions: Instruction string.
            search_query: Search query string.
            user_payload: JSON payload string.

        Returns:
            Parsed response JSON payload.

        Raises:
            AICreditsExhaustedError: If DeepSeek rejects the request because
                the account balance is exhausted.
            _DeepSeekHttpError: If the DeepSeek API request fails.
        """
        messages = _build_deepseek_message_input(
            system_prompt,
            instructions,
            search_query,
            user_payload,
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": DEEPSEEK_MAX_TOKENS,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(
            DEEPSEEK_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=self.timeout_s,
        )
        if not response.ok:
            credits_gone = response.status_code == HTTP_PAYMENT_REQUIRED or _is_credits_exhausted_message(
                response.text,
            )
            if credits_gone:
                raise AICreditsExhaustedError("deepseek", response.text)
            raise _DeepSeekHttpError(response.status_code, response.text)
        response_json = response.json()
        return response_json

    def _extract_ai_text(self, response: Any) -> str:
        """Extract output text from the provider response.

        Args:
            response: Provider response payload.

        Returns:
            Output text content.
        """
        if self.log_raw:
            logger.info(pformat(response))
        if self.provider == "deepseek":
            content = _extract_deepseek_text(response)
            return content
        if self.provider == "grok":
            content = _extract_grok_text(response)
            return content
        response_status = getattr(response, "status", "completed")
        if response_status != "completed":
            detail = _describe_incomplete_response(response)
            logger.warning(
                "AI response incomplete (model=%s) %s",
                self.model_name,
                detail,
            )
            return ""
        usage = getattr(response, "usage", None)
        output_tokens = None
        if usage is not None:
            output_tokens = getattr(usage, "output_tokens", None)
            if output_tokens is None and isinstance(usage, Mapping):
                output_tokens = usage.get("output_tokens")
        logger.debug(
            "AI response meta (model=%s, output_tokens=%s)",
            self.model_name,
            output_tokens or "n/a",
        )
        content = _extract_response_text(response)
        return content
