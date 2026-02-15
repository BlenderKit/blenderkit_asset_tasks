"""Single-asset validator for manufacturer and designer metadata.

This module exposes a lightweight 'validate' helper that can be invoked
from 'clean_manufacturer_tags.py' for one asset at a time. It keeps the
previous heuristic scoring logic, but removes CSV/CLI orchestration. When
heuristics cannot confidently decide, an optional AI agent can provide a
final verdict.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from blenderkit_server_utils import log  # type: ignore
from blenderkit_server_utils.asset_validation.field_validation.ai_validation import AIClient

logger = log.create_logger(__name__)

__all__ = ["ValidationResult", "score_asset", "validate"]

VALIDATE_ACTORS = ["heuristic", "ai", "fallback"]


# region Heuristic configuration
NAME_MIN_LEN = 4
NAME_MAX_LEN = 120
NAME_SYMBOL_RATIO_MAX = 0.5
NAME_REPEAT_MIN = 5
DESC_MIN_LEN = 15
DESC_MAX_LEN = 5000
DESC_REPEAT_MIN = 6
HEURISTIC_FAIL_SCORE = 65
HEURISTIC_FIELD_FAIL = 55
HEURISTIC_PASS_SCORE = 15
REASON_LOG_LIMIT = 4
SIM_AUTHOR_STRONG = 0.98
SIM_AUTHOR_MODERATE = 0.8
SIM_MENTION_THRESHOLD = 0.8
SIM_SAME_BRAND_THRESHOLD = 0.7
MENTION_REDUCTION = 10
KNOWN_BRAND_REDUCTION = 30
# endregion Heuristic configuration

GENERIC_BAD_VALUES = {
    "me",
    "myself",
    "own",
    "self",
    "unknown",
    "n/a",
    "na",
    "none",
    "test",
    "blender",
    "blenderkit",
    "generic",
    "brand",
    "company",
    "factory",
    "manufacturer",
    "producer",
    "render",
    "template",
    "sample",
    "demo",
    "placeholder",
    "default",
    "asset",
    "model",
    "cloud",
    "nature",
    "sky",
    "fantasy",
    "environment",
    "effect",
    "scene",
    "-",
    "+",
    "@",
    "/",
    "home decor",
}


# will be later updated with file load
DEFAULT_KNOWN_BRANDS: set[str] = set()

KNOWN_BRANDS_FILE = Path(__file__).resolve().parent / "known_manufacturers.txt"


@dataclass
class ValidationResult:
    """Holds heuristic suspicion data for a single asset."""

    suspicion_score: int
    reasons: list[str]
    suspicion_manufacturer: int
    suspicion_designer: int
    suspicion_collection: int
    suspicion_year: int


# region Normalization helpers


def _normalize(value: str) -> str:
    """Convert user-provided text to a comparable lowercase representation."""
    cleaned = value.strip().replace("|", " ")
    cleaned = unicodedata.normalize("NFKD", cleaned)
    ascii_only = cleaned.encode("ascii", "ignore").decode("ascii")
    ascii_only = re.sub(r"\s+", " ", ascii_only)
    normalized = ascii_only.lower()
    return normalized


def _load_known_brands_from_file(path: Path) -> set[str]:
    """Return normalized brand names loaded from the provided file."""
    results: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                value = raw_line.strip()
                if not value or value.startswith("#"):
                    continue
                results.add(_normalize(value))
    except FileNotFoundError:
        logger.debug("Known brands file not found: %s", path)
    except OSError:
        logger.exception("Failed to read known brands file: %s", path)
    return results


DEFAULT_KNOWN_BRANDS.update(_load_known_brands_from_file(KNOWN_BRANDS_FILE))
# endregion Normalization helpers


def _similar(a: str, b: str) -> float:
    """Return similarity ratio between two free-form strings."""
    a_norm = _normalize(a)
    b_norm = _normalize(b)
    if not a_norm or not b_norm:
        return 0.0
    matcher = SequenceMatcher(None, a_norm, b_norm)
    ratio = matcher.ratio()
    return ratio


def _contains_url_or_handle(value: str) -> bool:
    """Detect obvious URLs or @handles that should not appear in brand fields."""
    lower = value.lower()
    has_url = ("http://" in lower) or ("https://" in lower)
    has_handle = "@" in lower
    return has_url or has_handle


def _contains_email(value: str) -> bool:
    """Detect email-like substrings."""
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
    return match is not None


def _char_ratios(value: str) -> tuple[float, float, float]:
    """Return ratios of letters, digits, and symbols in the string."""
    if not value:
        return 0.0, 0.0, 0.0
    total = len(value)
    letters = sum(ch.isalpha() for ch in value)
    digits = sum(ch.isdigit() for ch in value)
    symbols = total - letters - digits
    ratios = (letters / total, digits / total, symbols / total)
    return ratios


def _has_repeated_chars(value: str, threshold: int = 4) -> bool:
    """Detect repeated characters like "!!!!!" that signal spam."""
    if threshold <= 1 or not value:
        return False
    pattern = rf"(.)\1{{{threshold - 1},}}"
    match = re.search(pattern, value)
    return match is not None


def _is_generic_value(value: str) -> bool:
    """Return True when the normalized value is obviously invalid."""
    normalized = _normalize(value)
    result = normalized in GENERIC_BAD_VALUES
    return result


def _score_name(name: str) -> tuple[int, list[str]]:
    """Score asset name for heuristic issues."""
    score = 0
    reasons: list[str] = []
    trimmed_len = len(name.strip())
    if 0 < trimmed_len < NAME_MIN_LEN:
        score += 25
        reasons.append("name too short")
    if trimmed_len > NAME_MAX_LEN:
        score += 10
        reasons.append("name very long")
    if _contains_url_or_handle(name) or _contains_email(name):
        score += 35
        reasons.append("name contains url/@/email")
    _, _, symbol_ratio = _char_ratios(name)
    if symbol_ratio > NAME_SYMBOL_RATIO_MAX:
        score += 20
        reasons.append("name too many symbols")
    if _has_repeated_chars(name, NAME_REPEAT_MIN):
        score += 15
        reasons.append("name repeated chars")
    result = (score, reasons)
    return result


def _score_description(description: str) -> tuple[int, list[str]]:
    """Score description quality for spam clues."""
    score = 0
    reasons: list[str] = []
    trimmed_len = len(description.strip())
    if 0 < trimmed_len < DESC_MIN_LEN:
        score += 20
        reasons.append("description too short")
    if trimmed_len > DESC_MAX_LEN:
        score += 10
        reasons.append("description very long")
    if _contains_url_or_handle(description) or _contains_email(description):
        score += 15
        reasons.append("description contains url/@/email")
    if _has_repeated_chars(description, DESC_REPEAT_MIN):
        score += 10
        reasons.append("description repeated chars")
    lower = description.lower()
    if any(keyword in lower for keyword in ("subscribe", "follow me", "instagram", "youtube", "tiktok")):
        score += 10
        reasons.append("description social CTA")
    result = (score, reasons)
    return result


def _plausible_year(year_str: str) -> bool:
    """Return True when the provided year looks realistic."""
    if not year_str:
        return True
    digits = re.sub(r"[^0-9]", "", year_str)
    if not digits:
        return False
    try:
        year_val = int(digits)
    except ValueError:
        return False
    now_year = datetime.now(tz=UTC).year
    minimum_year = 1850
    return minimum_year <= year_val <= (now_year + 1)


# region Heuristic scoring helpers


def _score_self_claims(
    fields: Mapping[str, str],
    author_name: str,
    scores: dict[str, int],
    reasons: list[str],
) -> None:
    """Penalize manufacturer or designer claims that match the author name."""
    if not author_name:
        return
    checks = [
        ("manufacturer", 40, "manufacturer ~= author_name"),
        ("designer", 20, "designer ~= author_name"),
    ]
    for field, penalty, message in checks:
        value = fields.get(field, "")
        if value and _similar(value, author_name) >= SIM_AUTHOR_STRONG:
            scores[field] += penalty
            reasons.append(message)
        elif value and _similar(value, author_name) >= SIM_AUTHOR_MODERATE:
            scores[field] += penalty // 2
            reasons.append(f"{message} (moderate)")


def _score_generic_fields(fields: Mapping[str, str], scores: dict[str, int], reasons: list[str]) -> None:
    """Raise suspicion for placeholder or generic values."""
    penalties = {
        "manufacturer": (35, "manufacturer is generic value"),
        "designer": (20, "designer is generic value"),
        "collection": (15, "collection is generic value"),
    }
    for field, (penalty, message) in penalties.items():
        value = fields.get(field, "")
        if value and _is_generic_value(value):
            scores[field] += penalty
            reasons.append(message)


def _score_contact_tokens(fields: Mapping[str, str], scores: dict[str, int], reasons: list[str]) -> None:
    """Penalize brand fields containing URLs, handles, or emails."""
    penalties = {
        "manufacturer": (35, "manufacturer contains url/@"),
        "designer": (25, "designer contains url/@"),
        "collection": (20, "collection contains url/@"),
    }
    for field, (penalty, message) in penalties.items():
        value = fields.get(field, "")
        if value and (_contains_url_or_handle(value) or _contains_email(value)):
            scores[field] += penalty
            reasons.append(message)


def _score_year_field(year: str, scores: dict[str, int], reasons: list[str]) -> None:
    """Add penalty when the provided year looks implausible."""
    if not _plausible_year(year):
        scores["year"] += 40
        reasons.append("implausible year")


def _apply_brand_adjustments(
    manufacturer: str,
    brand_set: set[str],
    scores: dict[str, int],
    reasons: list[str],
) -> None:
    """Reduce suspicion when the manufacturer is a known brand."""
    if manufacturer and _normalize(manufacturer) in brand_set:
        scores["manufacturer"] = max(0, scores["manufacturer"] - KNOWN_BRAND_REDUCTION)
        reasons.append("known-brand manufacturer")


def _score_mentions(
    name: str,
    description: str,
    fields: Mapping[str, str],
    scores: dict[str, int],
    reasons: list[str],
) -> None:
    """Lower suspicion when the asset text naturally references metadata."""
    text_samples = (name, description)
    adjustments = {
        "manufacturer": "asset mentions manufacturer",
        "collection": "asset mentions collection",
    }
    for field, message in adjustments.items():
        value = fields.get(field, "")
        if not value:
            continue
        if any(_similar(sample, value) >= SIM_MENTION_THRESHOLD for sample in text_samples):
            scores[field] = max(0, scores[field] - MENTION_REDUCTION)
            reasons.append(message)


def _score_text_quality(
    name: str,
    description: str,
    scores: dict[str, int],
    reasons: list[str],
) -> None:
    """Apply name and description heuristics."""
    name_delta, reasons_name = _score_name(name)
    scores["name"] += name_delta
    reasons.extend(reasons_name)
    desc_delta, reasons_desc = _score_description(description)
    scores["description"] += desc_delta
    reasons.extend(reasons_desc)


# endregion Heuristic scoring helpers


# region Heuristic scoring entry point


def score_asset(row: Mapping[str, str], known_brands: Iterable[str] | None = None) -> ValidationResult:
    """Score asset metadata using rule-based heuristics only."""
    manufacturer = row.get("manufacturer", "")
    designer = row.get("designer", "")
    collection = row.get("collection", "")
    author_name = row.get("author_name", "")
    year = row.get("year", "")
    name = row.get("name", "")
    description = row.get("description", "")
    fields = {
        "manufacturer": manufacturer,
        "designer": designer,
        "collection": collection,
    }
    scores = {
        "manufacturer": 0,
        "designer": 0,
        "collection": 0,
        "year": 0,
        "name": 0,
        "description": 0,
    }
    reasons: list[str] = []
    _score_self_claims(fields, author_name, scores, reasons)
    _score_generic_fields(fields, scores, reasons)
    _score_contact_tokens(fields, scores, reasons)
    _score_year_field(year, scores, reasons)
    brand_set = {_normalize(b) for b in (known_brands or DEFAULT_KNOWN_BRANDS)}
    _apply_brand_adjustments(manufacturer, brand_set, scores, reasons)
    _score_mentions(name, description, fields, scores, reasons)
    _score_text_quality(name, description, scores, reasons)
    clamped_keys = ("manufacturer", "designer", "collection", "year")
    for key in clamped_keys:
        scores[key] = max(0, min(100, scores[key]))
    total_score = sum(scores.values())
    unique_reasons = list(dict.fromkeys(reasons))
    result = ValidationResult(
        suspicion_score=total_score,
        reasons=unique_reasons,
        suspicion_manufacturer=scores["manufacturer"],
        suspicion_designer=scores["designer"],
        suspicion_collection=scores["collection"],
        suspicion_year=scores["year"],
    )
    return result


def _should_escalate_same_brand(
    manufacturer: str,
    designer: str,
    brand_set: set[str],
) -> bool:
    """Return True when same-brand entries should be sent to AI.

    Args:
        manufacturer: Manufacturer text from the asset.
        designer: Designer text from the asset.
        brand_set: Approved brand names (normalized).

    Returns:
        True when the designer and manufacturer are similar and not approved.
    """
    if not manufacturer or not designer:
        return False
    similarity = _similar(manufacturer, designer)
    if similarity < SIM_SAME_BRAND_THRESHOLD:
        return False
    normalized_manufacturer = _normalize(manufacturer)
    needs_escalation = normalized_manufacturer not in brand_set
    return needs_escalation


def _should_escalate_unknown_manufacturer(manufacturer: str, brand_set: set[str]) -> bool:
    """Return True when manufacturer is not approved and should go to AI.

    Args:
        manufacturer: Manufacturer text from the asset.
        brand_set: Approved brand names (normalized).

    Returns:
        True when the manufacturer is present but not in the approved list.
    """
    if not manufacturer:
        return False
    normalized_manufacturer = _normalize(manufacturer)
    return normalized_manufacturer not in brand_set


# endregion Heuristic scoring entry point


# region Public helpers


def _summarize_reasons(reasons: list[str]) -> str:
    """Build short log-friendly reason list."""
    if not reasons:
        return ""
    snippet = "; ".join(reasons[:REASON_LOG_LIMIT])
    if len(reasons) > REASON_LOG_LIMIT:
        snippet += "; ..."
    return snippet


def _should_reject_missing_manufacturer(row: Mapping[str, str]) -> bool:
    """Return True when metadata requires a manufacturer but it is missing.

    Args:
        row: Normalized asset metadata row.

    Returns:
        True when year, collection, or variant is provided without manufacturer.
    """
    manufacturer = (row.get("manufacturer") or "").strip()
    if manufacturer:
        return False
    requires_manufacturer = any([
        (row.get("year") or "").strip(),
        (row.get("collection") or "").strip(),
        (row.get("variant") or "").strip(),
    ])
    return requires_manufacturer


def _heuristic_decision(
    row: Mapping[str, str],
    known_brands: Iterable[str] | None = None,
) -> tuple[bool | None, str | None, ValidationResult]:
    """Return heuristic verdict when confidence is high."""
    heuristics = score_asset(row, known_brands)
    if _should_reject_missing_manufacturer(row):
        message = "Heuristics rejected metadata: manufacturer missing with year/collection/variant"
        result = (False, message, heuristics)
        return result
    brand_set = {_normalize(value) for value in (known_brands or DEFAULT_KNOWN_BRANDS)}
    manufacturer = row.get("manufacturer", "")
    designer = row.get("designer", "")
    if _should_escalate_same_brand(manufacturer, designer, brand_set):
        message = "Heuristics inconclusive: manufacturer and designer are the same"
        result = (None, message, heuristics)
        return result
    if _should_escalate_unknown_manufacturer(manufacturer, brand_set):
        message = "Heuristics inconclusive: manufacturer not in approved brands"
        result = (None, message, heuristics)
        return result
    highest_field = max(
        heuristics.suspicion_manufacturer,
        heuristics.suspicion_designer,
        heuristics.suspicion_collection,
        heuristics.suspicion_year,
    )
    reason_snippet = _summarize_reasons(heuristics.reasons)
    if heuristics.suspicion_score >= HEURISTIC_FAIL_SCORE or highest_field >= HEURISTIC_FIELD_FAIL:
        message = (
            f"Heuristics rejected metadata: {reason_snippet}" if reason_snippet else "Heuristics rejected metadata"
        )
        result = (False, message, heuristics)
        return result
    if heuristics.suspicion_score <= HEURISTIC_PASS_SCORE:
        message = "Heuristics approved metadata"
        result = (True, message, heuristics)
        return result
    result = (None, None, heuristics)
    return result


def _sanitize_text(value: Any) -> str:
    """Normalize arbitrary values for downstream string comparisons.

    Args:
        value: Any value pulled from the asset payload.

    Returns:
        str: Lower-risk string with newlines and pipes replaced.
    """
    s = "" if value is None else str(value)
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ")
    s = s.replace("|", "__")
    return s


def _sanitize_tags_list(tags: list[str] | None) -> str:
    """Return a comma-joined list of sanitized tags.

    Args:
        tags: Optional list of tag strings from the asset payload.

    Returns:
        str: Comma-separated, sanitized representation.
    """
    if not tags:
        return ""
    return ",".join(_sanitize_text(t) for t in tags if t is not None)


def _prepare_row(asset_data: Mapping[str, Any | None]) -> dict[str, str]:
    """Flatten asset metadata into comparable string fields.

    Args:
        asset_data: Raw asset mapping returned by the BlenderKit search API.

    Returns:
        dict[str, str]: Normalized string representation consumed by heuristics.
    """
    asset_id = _sanitize_text(asset_data.get("id"))
    name = _sanitize_text(asset_data.get("name", ""))
    upload_date = _sanitize_text(asset_data.get("created", ""))
    params = asset_data.get("dictParameters", {})
    manufacturer = _sanitize_text(params.get("manufacturer", ""))  # type: ignore
    designer = _sanitize_text(params.get("designer", ""))  # type: ignore
    design_collection = _sanitize_text(params.get("designCollection", ""))  # type: ignore
    design_variant = _sanitize_text(params.get("designVariant", ""))  # type: ignore
    design_year = _sanitize_text(params.get("designYear", ""))  # type: ignore
    tags_joined = _sanitize_tags_list(asset_data.get("tags", []))
    description = _sanitize_text(asset_data.get("description", ""))
    status = _sanitize_text(asset_data.get("verificationStatus", ""))

    author: dict = asset_data.get("author", {})  # type: ignore
    author_name = _sanitize_text(author.get("fullName", ""))
    author_id = _sanitize_text(author.get("id", ""))

    prepared: dict[str, str] = {
        "asset_id": asset_id,
        "name": name,
        "upload_date": upload_date,
        "manufacturer": manufacturer,
        "designer": designer,
        "collection": design_collection,
        "variant": design_variant,
        "year": design_year,
        "tags": tags_joined,
        "description": description,
        "verification_status": status,
        "author_name": author_name,
        "author_id": author_id,
    }

    return prepared


def validate(
    asset_data: Mapping[str, str | None],
    *,
    use_ai: bool | None = None,
    extra_known_brands: Iterable[str] | None = None,
) -> tuple[bool, str, str]:
    """Validate metadata for a single asset.

    Args:
        asset_data: Asset dictionary containing manufacturer-related fields.
        use_ai: Overrides the VALIDATOR_USE_AI environment flag when set.
        extra_known_brands: Optional iterable of additional whitelisted brands.

    Returns:
        Tuple of ``(is_valid, actor, reason)`` describing the decision source and justification.
    """
    row = _prepare_row(asset_data)
    logger.debug(row)
    brand_set = {_normalize(value) for value in DEFAULT_KNOWN_BRANDS}
    if extra_known_brands:
        brand_set.update(_normalize(b) for b in extra_known_brands if b)
    verdict, reason, heuristics = _heuristic_decision(row, brand_set)
    logger.info(
        "Heuristic decision for asset %s: verdict=%s reasons=%s score=%s",
        row.get("asset_id", "n/a"),
        verdict,
        _summarize_reasons(heuristics.reasons),
        heuristics.suspicion_score,
    )
    if verdict is not None and reason:
        result = (verdict, "heuristic", reason)
        return result
    env_flag = os.getenv("VALIDATOR_USE_AI") == "1"
    ai_requested = use_ai if use_ai is not None else env_flag
    if not ai_requested:
        fallback_reason = "Heuristics inconclusive; AI disabled"
        result = (True, "fallback", fallback_reason)
        return result
    ai_client = AIClient(enabled=ai_requested)
    decision = ai_client.judge(row, heuristics)
    if decision:
        # modify reason to include AI actor
        valid, reason_text = decision
        decision = (valid, "ai", reason_text)
        result = decision
        return result
    fallback_reason = "AI unavailable; heuristics inconclusive"
    result = (True, "fallback", fallback_reason)
    return result


# endregion Public helpers
