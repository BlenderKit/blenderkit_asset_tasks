"""Central logging helper.

Provides a single convenience function `create_logger` that returns a
`logging.Logger` configured with a uniform formatter used across the
asset task scripts.

Format:
    date time.millisec | level name | function_name | line_number | message

Example:
    from blenderkit_server_utils.log import create_logger

    logger = create_logger()  # derives name from caller's module
    logger.info("Starting export")

    custom = create_logger("blenderkit.custom")
    custom.warning("Something interesting happened")

Notes:
Using caller function name and line number incurs a small overhead due
to stack inspection. If performance in a tight loop is critical, you
can pass include_caller=False to skip that (you'll still get the log
message and level, but function/line fields become '-').
"""

from __future__ import annotations

import datetime as _dt
import inspect
import logging
import re
import sys
from types import FrameType
import os

CALLER_STACK_LEVEL = 2  # create_logger -> caller wrapper (usually module)


_EMBEDDED_LINE_RE = re.compile(
    r"^(?P<ts>20\d{2}-\d{2}-\d{2} "  # date
    r"\d{2}:\d{2}:\d{2}\.\d{3}) "  # time with ms
    r"\|\s+(?P<level>[A-Z]{4,5}) \| "  # level
    r"(?P<func>[^|]+) \| "  # function name (no pipe)
    r"(?P<line>\d+) \| "  # line number
    r"(?P<msg>.*)$",
)


class _TaskLogFormatter(logging.Formatter):
    """Custom formatter that also de-duplicates nested pre-formatted lines.

    Sometimes Blender starts a subprocess or captures stdout and we end up
    logging an already formatted line again as the *message* prefixed with
    "STDOUT:". Example of a duplicated log:

    2025-09-18 22:36:58.669 |  INFO | <lambda> | 237 | STDOUT: \
    2025-09-18 22:36:58.657 |  INFO | generate_gltf | 229 | Preprocess...

    This formatter detects that pattern and collapses it to the inner line
    so it only appears once.
    """

    def _collapse_embedded(self, msg: str) -> tuple[str, str, None, str | None, int | None]:
        """Return (new_message, level_name, func, line) collapsing embedded formatted line.

        If message contains 'STDOUT: <already formatted line>' or 'STDERR: <already formatted line>'
        or starts with formatted datetime followed by levelname,
        extract inner
        function name and line number so outer record can reflect original
        caller. Returns original message if no embedded pattern is detected.
        """
        default_response = (msg, None, None, None)

        if "STDOUT:" not in msg and "STDERR:" not in msg and re.match(_EMBEDDED_LINE_RE, msg) is None:
            return default_response
        if "STDOUT:" in msg:
            prefix, _, remainder = msg.partition("STDOUT:")
        elif "STDERR:" in msg:
            prefix, _, remainder = msg.partition("STDERR:")
        else:  # math output
            remainder = msg
        remainder = remainder.strip()
        match = _EMBEDDED_LINE_RE.match(remainder)
        if not match:
            return default_response
        # Use parsed func/line and the original inner message only.
        inner_level = match.group("level").strip()
        if inner_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "TRACE"):
            return default_response
        inner_func = match.group("func").strip()
        try:
            inner_line = int(match.group("line"))
        except ValueError:
            inner_line = None
        inner_msg = match.group("msg")
        return inner_msg, inner_level, inner_func, inner_line

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        millis = int(record.msecs)
        message = record.getMessage()
        collapsed_msg, inner_level, inner_func, inner_line = self._collapse_embedded(message)

        recorded_level = inner_level if inner_level is not None else record.levelname
        func_name = inner_func or record.funcName
        line_no = inner_line if inner_line is not None else record.lineno

        return f"{ts}.{millis:03d} | {recorded_level:>5} | {func_name} | {line_no} | {collapsed_msg}"


def _derive_caller_info(stack_level: int = CALLER_STACK_LEVEL) -> tuple[str, str, int]:
    """Return (logger_name, func_name, line_no) from the call stack.

    Falls back to root logger if unavailable.
    """
    frame: FrameType | None
    try:
        frame = inspect.stack()[stack_level].frame  # type: ignore[index]
    except (IndexError, AttributeError):  # pragma: no cover - defensive
        return ("blenderkit", "-", 0)
    module = inspect.getmodule(frame)
    logger_name = getattr(module, "__name__", "blenderkit")
    return logger_name


def create_logger(name: str | None = None) -> logging.Logger:
    """Create (or retrieve) a logger with repository standard formatting.

    Args:
        name (str | None): Explicit logger name. If omitted, derives from caller module.

    Returns:
        logging.Logger: A configured logger (or adapter) instance.
    """
    if name is None:
        derived_name = _derive_caller_info()
        name = derived_name

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    # check env for debuglogging level
    if "DEBUG_LOGGING" in os.environ:
        logger.setLevel(logging.DEBUG)

    # Attach a handler if none present.
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(_TaskLogFormatter())
        logger.addHandler(handler)

    return logger


__all__ = ["create_logger"]
