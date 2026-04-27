"""Utilities for sanitizing names and paths for BlenderKit server use."""

import random
import re
import string
import unicodedata
from os import PathLike

try:
    import unidecode as _unidecode  # type: ignore
except ImportError:  # pragma: no cover - environment-dependent (e.g. Blender's bundled Python)
    _unidecode = None

# Manual ligature/special-character map used as a fallback when `unidecode` is
# not available (notably inside Blender's bundled Python). Covers the common
# Western-European ligatures that `unicodedata.normalize("NFKD", ...)` cannot
# decompose on its own.
_FALLBACK_LIGATURES = str.maketrans(
    {
        "ß": "ss",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "Ø": "O",
        "ø": "o",
        "Þ": "Th",
        "þ": "th",
        "Ð": "D",
        "ð": "d",
        "Ł": "L",
        "ł": "l",
    },
)

_BLACKLISTED_CHARACTERS = r' \`\'".:,#\|;?!<>*$%^@~=+{}[]()/\\'
"""Invalid characters in object names, and path tokens."""

_INVALID_NO_HYPHEN = re.compile(f"[{re.escape(_BLACKLISTED_CHARACTERS)}]+")
_INVALID_WITH_HYPHEN = re.compile(f"[{re.escape(_BLACKLISTED_CHARACTERS + '-')}]+")

_INVALID_PATH_REGEX = re.compile(r"\s|[^a-zA-Z0-9_.%/\\]")
_INVALID_NAME_REGEX = re.compile(r"\s|[^a-z0-9_.]")
_INVALID_NAME_REGEX_WITH_HYPHEN = re.compile(r"\s|[^a-z0-9_.\-]")
_DRIVE_REMOVE_REGEX = re.compile(r"^[a-zA-Z]:")


def random_string(length: int = 6) -> str:
    """Generate a random string of lowercase letters."""
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for _ in range(length))  # noqa: S311


def _replace_invalid_chars(text: str, *, allow_hyphen: bool) -> str:
    """Replace blacklisted characters with underscores."""
    pattern = _INVALID_WITH_HYPHEN if allow_hyphen else _INVALID_NO_HYPHEN
    return pattern.sub("_", text.strip())


def strip_accents(text: str) -> str:
    """Strip accents from a string."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _normalize_to_ascii(text: str) -> str:
    """Strip accents and transliterate to ASCII.

    Uses `unidecode` when available (best quality, handles Cyrillic/Greek/CJK).
    Falls back to a stdlib-only path (NFKD decomposition + ligature map) when
    `unidecode` is not installed — for example, inside Blender's bundled
    Python interpreter. The fallback covers Latin scripts well but will drop
    characters from non-Latin scripts.
    """
    if _unidecode is not None:
        text = strip_accents(text)
        return _unidecode.unidecode(text)
    # Stdlib-only fallback.
    text = text.translate(_FALLBACK_LIGATURES)
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _remove_prefix_suffix(text: str, prefix: str, suffix: str) -> str:
    """Remove prefix and suffix from text (case-insensitive)."""
    folded = text.casefold()
    if prefix and folded.startswith(prefix.casefold()):
        text = text[len(prefix) :]

    if suffix and folded.endswith(suffix.casefold()):
        text = text[: -len(suffix)]

    return text


# noinspection SpellCheckingInspection
def to_snake_case(text: str) -> str:
    """Convert any input string to snake_case.

    Handles spaces, hyphens, underscores, camelCase, PascalCase, ACRONYMcase,
    and consecutive uppercase letters like 'MYlongName' -> 'my_long_name'.
    """
    # Step 1: Insert underscore between consecutive uppercase letters and lowercase
    # but keep the last uppercase with the lowercase that follows
    # "MYveryLITTLEveryLONG" -> "MY_very_LITTLE_very_LONG"
    text = re.sub(r"([A-Z]{2,})([a-z])", r"\1_\2", text)
    # "MyLongName" -> "my_long_name"
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)

    # Step 2: Replace all non-word characters with underscores
    text = re.sub(r"[\W]+", "_", text)

    # Step 3: Convert to lowercase and strip trailing underscores
    return text.lower().strip("_")


def sanitize_name(  # noqa: PLR0913
    name: str | PathLike[str],
    prefix_rem: str = "",
    suffix_rem: str = "",
    *,
    snake_case: bool = True,
    leave_empty: bool = False,
    allow_prefix_number: bool = False,
    allow_hyphen: bool = True,
) -> str:
    """Return a pipeline-safe ASCII identifier from arbitrary input.

    Process: strip prefix/suffix → remove accents → replace invalid chars →
    optional snake_case → collapse underscores → guard digit prefix → random fallback.

    Args:
        name: Input string/PathLike.
        prefix_rem: Case-insensitive prefix to remove.
        suffix_rem: Case-insensitive suffix to remove.
        snake_case: Apply snake_case conversion (vs simple lowercase).
        leave_empty: Allow empty result (vs random 8-char token).
        allow_prefix_number: Allow leading digit (vs 'q_' prefix).
        allow_hyphen: Keep '-' (vs replace with '_').

    Returns:
        Sanitized identifier: [a-z0-9_] + optional '-'.

    Examples:
        >>> sanitize_name("MyAssetNameV2")
        'my_asset_name_v2'
        >>> sanitize_name("MYassetNAMEv2")
        'my_asset_name_v2'
        >>> sanitize_name("MyAssetNameV2",snake_case=False)
        'myassetnamev2'
        >>> sanitize_name("Hero-Cam.xcam",suffix_rem=".xcam")
        'hero_cam'
        >>> sanitize_name("001car",allow_prefix_number=False)
        'q_001car'
    """
    # Convert to string
    result = str(name) if isinstance(name, PathLike) else name

    result = _remove_prefix_suffix(result, prefix_rem, suffix_rem)
    result = _normalize_to_ascii(result)
    result = _replace_invalid_chars(result, allow_hyphen=allow_hyphen)
    result = to_snake_case(result) if snake_case else result.lower()
    if result and result[0].isdigit() and not allow_prefix_number:
        result = "q_" + result

    # Fallback for empty result
    if not result and not leave_empty:
        result = random_string(length=8)

    # Final cleanup. When hyphens were preserved (allow_hyphen=False), keep
    # them through this pass too; otherwise the previously-preserved '-'
    # characters would be replaced with '_' here.
    final_regex = _INVALID_NAME_REGEX if allow_hyphen else _INVALID_NAME_REGEX_WITH_HYPHEN
    if final_regex.search(result):
        result = final_regex.sub("_", result)

    return result
