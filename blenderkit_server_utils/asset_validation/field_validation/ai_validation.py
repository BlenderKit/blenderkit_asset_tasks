"""AI validation helpers for manufacturer field checks.

This module wraps OpenAI and Grok calls with a common interface used by
validate_fields.py.
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
AI_REQUEST_PREVIEW = 600
AI_ERROR_DETAIL_PREVIEW = 400
CODE_FENCE_SPLIT_MAX = 2
AI_MAX_RETRIES = 2
AI_RETRY_BASE_SECONDS = 4
AI_RETRY_MAX_SECONDS = 10
AI_RETRY_JITTER_SECONDS = 1

AI_RESPONSE_SCHEMA = {
    "name": "validation_decision",
    "schema": {
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": ["valid", "reason"],
        "additionalProperties": False,
    },
}

AI_RESPONSE_SCHEMA_TEXT = json.dumps(AI_RESPONSE_SCHEMA["schema"], separators=(",", ":"))

AI_PROVIDER_ENV = config.AI_PROVIDER

OPENAI_DEFAULT_MODEL = "gpt-5"
GROK_DEFAULT_MODEL = "grok-4-1-fast-reasoning"

GROK_ENDPOINT = "https://api.x.ai/v1/responses"


class HeuristicSummary(Protocol):
    """Protocol for heuristic summaries used by the AI client."""

    suspicion_score: int
    reasons: list[str]


class _GrokHttpError(RuntimeError):
    """HTTP error wrapper for Grok responses."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    year = _sanitize_prompt_value(row.get("year"))
    name = _sanitize_prompt_value(row.get("name"))
    if manufacturer:
        parts.append(f"{manufacturer} manufacturer")
    if designer:
        parts.append(f"{designer} designer")
    if collection:
        parts.append(f"{collection} collection")
    if year:
        parts.append(f"{year} design year")
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
    raw = config.AI_PROVIDER or os.getenv("AI_PROVIDER", "grok").lower()
    if raw not in {"grok", "openai"}:
        logger.warning("Unknown AI_PROVIDER=%r; defaulting to grok", raw)
        return "grok"
    return raw


def _get_ai_model(provider: str) -> str:
    """Return the model name for the configured provider.

    Args:
        provider: Provider name.

    Returns:
        Model name to use for requests.
    """
    if provider == "grok":
        model_name = config.GROK_MODEL or GROK_DEFAULT_MODEL
        return model_name
    model_name = config.OPENAI_MODEL or OPENAI_DEFAULT_MODEL
    return model_name


def _build_ai_prompts(
    row: Mapping[str, str],
    heuristics: HeuristicSummary,
) -> tuple[str, str, str, str]:
    """Build shared prompt content for AI requests.

    Args:
        row: Asset field mapping.
        heuristics: Heuristic summary for the asset.

    Returns:
        Tuple of (system_prompt, instructions, search_query, user_payload).
    """
    payload = _build_ai_context(row, heuristics)
    search_query = payload.get("search_query") or ""
    user_payload = json.dumps(payload, ensure_ascii=False)
    system_prompt = (
        "You verify manufacturer/designer claims for BlenderKit assets. "
        "Reject self-promotional, placeholder, or unverifiable entries. "
        "If manufacturer in metadata matches a known brand, it's likely valid. "
        "Also accept historic or defunct manufacturers when the product name "
        "matches known historic items, including evidence from collector or "
        "marketplace listings (e.g., museum catalogs, auction archives, eBay). "
    )
    instructions = (
        "If search_query is non-empty, call the web_search tool once with that query. "
        f"Respond with strict minified JSON matching schema: {AI_RESPONSE_SCHEMA_TEXT}. "
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


def _parse_ai_decision(raw: str) -> tuple[bool, str] | None:
    """Parse boolean verdict and reason from AI JSON response.

    Args:
        raw: Raw response string.

    Returns:
        Decision tuple or None when parsing fails.
    """
    try:
        json_text = _extract_json_object(_strip_code_fence(raw))
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.exception("AI response was not valid JSON")
        return None
    valid_value = bool(data.get("valid", False))
    reason_text = str(data.get("reason", "AI decision")).strip() or "AI decision"
    decision = (valid_value, reason_text)
    return decision


class AIClient:
    """Minimal AI wrapper used for fallback validation."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.provider = _get_ai_provider()
        self.client = None
        self.grok_api_key = ""
        self.timeout_s = float(os.getenv("VALIDATOR_AI_TIMEOUT", "15"))
        self.model_name = _get_ai_model(self.provider)
        self.log_raw = os.getenv("VALIDATOR_LOG_AI") == "1"

        if not self.enabled:
            return
        if self.provider == "grok":
            api_key = config.GROK_API_KEY
            if not api_key:
                logger.warning("AI validation requested but XAI_API_KEY is missing")
                self.enabled = False
                return
            self.grok_api_key = api_key
            return
        api_key = config.OPENAI_API_KEY
        if not api_key:
            logger.warning("AI validation requested but OPENAI_API_KEY is missing")
            self.enabled = False
            return
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            logger.warning(
                "OpenAI SDK is not installed; run `pip install openai` to enable AI validation",
            )
            self.enabled = False
            return
        self.client = OpenAI(api_key=api_key)  # type: ignore[call-arg]

    def judge(self, row: Mapping[str, str], heuristics: HeuristicSummary) -> tuple[bool, str] | None:  # noqa: PLR0911
        """Return AI verdict when available.

        Args:
            row: Asset metadata mapping.
            heuristics: Heuristic summary for the asset.

        Returns:
            Decision tuple or None.
        """
        if not self.enabled:
            return None
        if self.provider == "openai" and not self.client:
            return None
        if self.provider == "grok" and not self.grok_api_key:
            return None
        system_prompt, instructions, search_query, user_payload = _build_ai_prompts(row, heuristics)
        payload_preview = user_payload[:AI_REQUEST_PREVIEW]
        payload_suffix = "..." if len(user_payload) > AI_REQUEST_PREVIEW else ""
        logger.debug(
            "AI request (%s) query=%r heuristics=%s payload=%s%s",
            self.model_name,
            search_query or "n/a",
            heuristics.suspicion_score,
            payload_preview,
            payload_suffix,
        )
        tools: Any = [{"type": "web_search"}] if search_query else None
        message_input = _build_openai_message_input(
            system_prompt,
            instructions,
            search_query,
            user_payload,
        )
        grok_input = _build_grok_message_input(
            system_prompt,
            instructions,
            search_query,
            user_payload,
        )
        response = None
        for attempt in range(1, AI_MAX_RETRIES + 1):
            try:
                response = self._request_ai_response(
                    message_input=message_input,
                    grok_input=grok_input,
                    tools=tools,
                )
                break
            except Exception as exc:
                detail = _describe_ai_exception(exc)
                should_retry = _is_retryable_ai_exception(exc)
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

    def _request_ai_response(
        self,
        *,
        message_input: list[dict[str, Any]],
        grok_input: list[dict[str, str]],
        tools: list[dict[str, str]] | None,
    ) -> Any:
        """Issue a provider-specific AI request.

        Args:
            message_input: OpenAI formatted input.
            grok_input: Grok formatted input.
            tools: Optional tool list.

        Returns:
            Provider response payload.

        Raises:
            _GrokHttpError: If the Grok API request fails.
        """
        if self.provider == "grok":
            payload = {
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
                raise _GrokHttpError(response.status_code, response.text)
            response_json = response.json()
            return response_json
        response = self.client.responses.create(  # type: ignore[call-arg]
            model=self.model_name,
            input=message_input,
            tools=tools,
            timeout=self.timeout_s,
            reasoning={"effort": "low"},
            include=["web_search_call.action.sources"],
        )
        return response

    def _extract_ai_text(self, response: Any) -> str:
        """Extract output text from the provider response.

        Args:
            response: Provider response payload.

        Returns:
            Output text content.
        """
        if self.log_raw:
            logger.info(pformat(response))
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
