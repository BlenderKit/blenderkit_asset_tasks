"""Constants for the BlenderKit server utils."""

from __future__ import annotations

import os

SERVER: str = os.getenv("BLENDERKIT_SERVER", "https://www.blenderkit.com")

BLENDERKIT_API_KEY: str = os.getenv("BLENDERKIT_API_KEY", "")
"""API key for BlenderKit server authentication."""

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
"""API key for OpenAI authentication."""

GROK_API_KEY: str = os.getenv("XAI_API_KEY", "")
"""API key for Grok (XAI) authentication."""

OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5")
"""Default OpenAI model to use for AI interactions."""

GROK_MODEL: str = os.getenv("VALIDATOR_GROK_MODEL", "grok-4-1-fast-reasoning")
"""Default Grok model to use for AI interactions."""

AI_PROVIDER: str = os.getenv("AI_PROVIDER", "grok")
"""AI provider to use for validation (e.g., 'grok', 'openai')."""

BLENDERKIT_API_VERSION: str = "/api/v1"

BLENDER_PATH: str = os.getenv("BLENDER_PATH", "")
"""Path to the Blender executable."""

BLENDERS_PATH: str = os.getenv("BLENDERS_PATH", "")
"""Path to the folder with Blender versions."""


ASSET_BASE_ID: str | None = os.getenv("ASSET_BASE_ID", None)
"""Asset base ID to be processed."""

CUSTOM_SEARCH_PARAMS: dict[str, str] | None = None
"""Custom search parameters to filter assets for processing. Should be a dictionary of query parameters."""
# check env var for custom search params and parse it as JSON if it exists
if os.getenv("CUSTOM_SEARCH_PARAMS"):
    import json

    try:
        CUSTOM_SEARCH_PARAMS = json.loads(os.getenv("CUSTOM_SEARCH_PARAMS", "{}"))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Invalid JSON for CUSTOM_SEARCH_PARAMS: {e}")  # noqa: T201
        CUSTOM_SEARCH_PARAMS = {}
        # try convert direct simple string of key=value;key2=value2 to dict (values may contain commas)
        if "=" in os.getenv("CUSTOM_SEARCH_PARAMS", ""):
            CUSTOM_SEARCH_PARAMS = dict(
                item.split("=", 1) for item in os.getenv("CUSTOM_SEARCH_PARAMS", "").split(";") if "=" in item
            )
            # validate that all keys and values are non-empty
            if not all(k and v for k, v in CUSTOM_SEARCH_PARAMS.items()):
                print("ERROR: Invalid key=value pairs in CUSTOM_SEARCH_PARAMS.")  # noqa: T201
                CUSTOM_SEARCH_PARAMS = {}


MAX_ASSET_COUNT: int = int(os.getenv("MAX_ASSET_COUNT", "200"))
"""Maximum number of assets to process in one run."""

MAX_VALIDATION_THREADS: int = int(os.getenv("MAX_VALIDATION_THREADS", "8"))
"""Maximum number of concurrent validation threads."""

# DEBUGGING OPTIONS AND SPECIAL MODES
DEBUG: bool = bool(os.getenv("DEBUG", "False") in ["1", "true", "True"])
"""Modifies behavior for debugging purposes, e.g., shows images when generating captions."""

DEBUG_LOGGING: bool = bool(os.getenv("DEBUG_LOGGING", "False") in ["1", "true", "True"])
"""Enables debug-level logging output."""

SKIP_UPDATE: bool = os.getenv("SKIP_UPDATE", "False") in ["1", "true", "True"]
"""If True, skips updating assets on the server (for testing)."""
