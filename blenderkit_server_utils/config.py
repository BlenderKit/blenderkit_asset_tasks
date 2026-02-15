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

MAX_ASSET_COUNT: int = int(os.getenv("MAX_ASSET_COUNT", "100"))
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
