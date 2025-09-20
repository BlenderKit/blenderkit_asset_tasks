"""Constants for the BlenderKit server utils."""

import os

SERVER: str = os.getenv("BLENDERKIT_SERVER", "https://www.blenderkit.com")

BLENDERKIT_API_KEY: str = os.getenv("BLENDERKIT_API_KEY", "")
"""API key for BlenderKit server authentication."""

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
"""API key for OpenAI authentication."""

BLENDERKIT_API_VERSION: str = "/api/v1"

BLENDER_PATH: str = os.getenv("BLENDER_PATH", "")
"""Path to the Blender executable."""

BLENDERS_PATH: str = os.getenv("BLENDERS_PATH", "")
"""Path to the folder with Blender versions."""


ASSET_BASE_ID: str | None = os.getenv("ASSET_BASE_ID", None)
"""Asset base ID to be processed."""

MAX_ASSET_COUNT = int(os.getenv("MAX_ASSET_COUNT", "100"))
"""Maximum number of assets to process in one run."""

MAX_VALIDATION_THREADS = int(os.getenv("MAX_VALIDATION_THREADS", "8"))
"""Maximum number of concurrent validation threads."""
