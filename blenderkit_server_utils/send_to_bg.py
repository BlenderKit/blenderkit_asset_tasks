"""Launch Blender background tasks with controlled environment and logging.

This module selects an appropriate Blender binary, prepares a temporary
datafile for the background script, spawns Blender with flags, streams output,
and cleans up afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import config, log, paths, read_header, utils

logger = log.create_logger(__name__)

# Verbosity constants
VERBOSITY_STDERR: int = 1
VERBOSITY_ALL: int = 2
STREAM_TAIL_LINE_LIMIT: int = 200


def get_blender_version_from_blend(blend_file_path: str) -> str:
    """Extract Blender version from a .blend file header (2.8+ heuristic).

    Returns 'major.minor' as a string. Falls back to '2.93' if not detected.
    """
    # we have slightly more advanced method in read_header, which supports compressed blends
    version_data = None
    try:
        version_data = read_header.detect_blender_version(blend_file_path)
    except Exception:
        logger.exception("Failed to detect Blender version from blend header.")

    if version_data and version_data["version"]:
        return version_data

    with open(blend_file_path, "rb") as blend_file:
        header = blend_file.read(24)
        if header[0:7] == b"BLENDER":
            version_bytes = header[9:12]
            version = (chr(version_bytes[0]), chr(version_bytes[2]))
        elif header[12:19] == b"BLENDER":
            version_bytes = header[21:24]
            version = (chr(version_bytes[0]), chr(version_bytes[2]))
        else:
            version = ("2", "93")
        ver_str = ".".join(version)
        logger.debug("Blend header reported version %s", ver_str)
        return ver_str


def get_blender_binary(asset_data: dict[str, Any], file_path: str = "", binary_type: str = "CLOSEST") -> str:
    """Pick the appropriate Blender binary based on asset metadata and policy.

    Args:
        asset_data: Asset metadata; expects 'sourceAppVersion' and 'assetType'.
        file_path: Path to a .blend file to derive version from if present.
        binary_type: 'CLOSEST' or 'NEWEST'. CLOSEST uses nearest to asset version.

    Returns:
        Absolute path to the chosen Blender executable.

    Raises:
        RuntimeError: If no binaries are found or the selected binary doesn't exist.
    """
    blenders_path = config.BLENDERS_PATH

    # Get available blender versions
    blenders = utils.get_all_blender_versions(blenders_path)

    if len(blenders) == 0:
        raise RuntimeError(f"No valid Blender versions found in {blenders_path}")

    if binary_type == "CLOSEST":
        # get asset's blender upload version
        source_ver = str(asset_data.get("sourceAppVersion", "0.0"))
        asset_blender_version = utils.version_to_float(source_ver)

        logger.debug("Asset Blender version (metadata): %s -> %s", source_ver, asset_blender_version)

        asset_blender_version_from_blend = get_blender_version_from_blend(file_path) if file_path else "0.0"
        logger.debug("Asset Blender version (from blend): %s", asset_blender_version_from_blend)

        asset_blender_version_from_blend_f = utils.version_to_float(asset_blender_version_from_blend)
        asset_blender_version = max(asset_blender_version, asset_blender_version_from_blend_f)
        logger.debug("Asset Blender version (picked): %s", asset_blender_version)

        blender_target = min(blenders, key=lambda x: abs(x[0] - asset_blender_version))
    if binary_type == "NEWEST":
        blender_target = max(blenders, key=lambda x: x[0])

    # use latest blender version for hdrs
    if str(asset_data.get("assetType", "")).lower() == "hdr":
        blender_target = blenders[-1]

    logger.debug("Selected Blender target: %s", blender_target)

    # Handle different OS paths
    if sys.platform == "darwin":  # macOS
        binary = os.path.join(
            blenders_path,
            blender_target[1],
            "Contents",
            "MacOS",
            "Blender",
        )
    else:  # Windows and Linux
        ext = ".exe" if sys.platform == "win32" else ""
        binary = os.path.join(blenders_path, blender_target[1], f"blender{ext}")

    logger.info("Using Blender binary: %s", binary)
    if not os.path.exists(binary):
        raise RuntimeError(f"Blender binary not found at {binary}")

    return binary


def get_process_flags() -> int:
    """Get OS-specific priority flags so subprocess runs at lower priority."""
    below_normal_priority_class = 0x00004000
    return below_normal_priority_class if sys.platform == "win32" else 0


def _reader_thread(pipe: Any, func: Callable[[str], None]) -> None:
    """Read a pipe line-by-line and pass decoded text to a callback."""
    for line in iter(pipe.readline, b""):
        func(line.decode(errors="replace").strip())
    pipe.close()


def _collecting_callback(
    callback: Callable[[str], None],
    collector: deque[str],
) -> Callable[[str], None]:
    """Wrap a pipe callback so streamed lines are stored for later inspection."""

    def wrapper(line: str) -> None:
        collector.append(line)
        callback(line)

    return wrapper


def _log_stream_tail(stream_name: str, lines: deque[str]) -> None:
    """Log the captured trailing lines of a Blender stream for easier debugging."""
    if not lines:
        logger.error("%s stream produced no output before failure.", stream_name)
        return
    joined = "\n".join(lines)
    logger.error("Captured %s tail (%d lines):\n%s", stream_name, len(lines), joined)


def _select_binary_path(
    binary_path: str,
    asset_data: dict[str, Any],
    *,
    asset_file_path: str,
    binary_type: str,
) -> str:
    """Choose Blender binary path either from input or by detection."""
    if binary_path:
        logger.info("Send_to_BG: using predefined Blender binary path: %s", binary_path)
        return binary_path
    detected = get_blender_binary(asset_data, file_path=asset_file_path, binary_type=binary_type)
    logger.info("Send_to_BG: using detected Blender binary path: %s", detected)
    return detected


def _ensure_temp_folder(temp_folder: str) -> tuple[str, bool]:
    """Ensure a temporary folder exists; return the path and ownership flag."""
    if temp_folder:
        return temp_folder, False
    created = tempfile.mkdtemp()
    return created, True


def _write_datafile(
    temp_folder: str,
    payload: DataPayload,
) -> str:
    """Write the JSON datafile used by the background script and return its path."""
    data = {
        "file_path": payload.file_path,
        "result_filepath": payload.result_filepath,
        "result_folder": payload.result_folder,
        "asset_data": payload.asset_data,
        "api_key": payload.api_key,
        "temp_folder": temp_folder,
        "target_format": payload.target_format,
    }
    datafile = os.path.join(temp_folder, "resdata.json").replace("\\", "\\\\")
    with open(datafile, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=4)
    return datafile


def _resolve_template(template_file_path: str, asset_file_path: str) -> str:
    """Return the template path, defaulting to the asset file if not provided."""
    if template_file_path:
        return template_file_path
    return asset_file_path


def _build_command(
    binary_path: str,
    template_file_path: str,
    script: str,
    datafile: str,
    addons: str,
) -> list[str]:
    """Construct the Blender CLI command list with optional addons."""
    command: list[str] = [
        binary_path,
        "--background",
        "--factory-startup",
        "-noaudio",
        template_file_path,
        "--python",
        os.path.join(paths.BG_SCRIPTS_PATH, script),
        "--",
        datafile,
    ]
    if addons:
        command.insert(3, "--addons")
        command.insert(4, addons)
    return command


def _run_blender(command: list[str], verbosity_level: int, env: dict[str, str] | None = None) -> int:
    """Run Blender with the given command and stream output per verbosity."""
    stdout_val, stderr_val = subprocess.PIPE, subprocess.PIPE
    logger.info("Running Blender command: %s", command)
    logger.debug("Raw command: %s", " ".join(command))
    stdout_lines: deque[str] = deque(maxlen=STREAM_TAIL_LINE_LIMIT)
    stderr_lines: deque[str] = deque(maxlen=STREAM_TAIL_LINE_LIMIT)
    with subprocess.Popen(
        command,
        stdout=stdout_val,
        stderr=stderr_val,
        creationflags=get_process_flags(),
        env=env,
    ) as proc:
        if verbosity_level == VERBOSITY_ALL:
            stdout_callback = lambda line: logger.info("STDOUT: %s", line)  # noqa: E731
            stderr_callback = lambda line: logger.error("STDERR: %s", line)  # noqa: E731
        elif verbosity_level == VERBOSITY_STDERR:
            stdout_callback = lambda line: logger.debug("STDOUT: %s", line)  # noqa: E731
            stderr_callback = lambda line: logger.error("STDERR: %s", line)  # noqa: E731
        else:
            stdout_callback = lambda line: logger.debug("STDOUT: %s", line)  # noqa: E731
            stderr_callback = lambda line: logger.debug("STDERR: %s", line)  # noqa: E731

        stdout_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stdout, _collecting_callback(stdout_callback, stdout_lines)),
        )
        stderr_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stderr, _collecting_callback(stderr_callback, stderr_lines)),
        )
        stdout_thread.start()
        stderr_thread.start()
        stdout_thread.join()
        stderr_thread.join()
        returncode = proc.wait()
    if returncode != 0:
        _log_stream_tail("STDOUT", stdout_lines)
        _log_stream_tail("STDERR", stderr_lines)
    return returncode


def _onerror_delete(func: Callable[[str], None], path: str, exc_info: tuple) -> None:
    """Error handler for shutil.rmtree to handle read-only files.

    Args:
        func: The function that raised the exception (e.g., os.remove, os.rmdir).
        path: The file or directory path on which the function failed.
        exc_info: The exception information tuple.
    """
    logger.warning("Failed %s on %s: %s", func.__name__, path, exc_info[1])
    # Example: try to chmod and retry if read-only
    if not os.access(path, os.W_OK):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            logger.exception("Retry failed for %s", path)


def _cleanup_paths(datafile: str, temp_folder: str, *, remove_temp_folder: bool) -> None:
    """Remove temporary files/folders created by this module.

    Args:
        datafile: The path to the datafile to remove.
        temp_folder: The path to the temporary folder to remove.
        remove_temp_folder: Flag indicating whether to remove the temp folder.

    Returns:
        None
    """
    try:
        logger.debug("Cleaning up datafile: %s", datafile)
        os.remove(datafile)
    except OSError:
        logger.debug("Failed to remove temp datafile: %s", datafile, exc_info=True)
    if remove_temp_folder:
        try:
            logger.debug("Cleaning up temp folder: %s", temp_folder)
            shutil.rmtree(temp_folder, onerror=_onerror_delete)
        except OSError:
            logger.debug("Failed to remove temp folder: %s", temp_folder, exc_info=True)


@dataclass
class DataPayload:
    """Container for background script input payload."""

    file_path: str
    result_filepath: str
    result_folder: str
    asset_data: dict[str, Any]
    api_key: str
    target_format: str


def send_to_bg(  # noqa: PLR0913
    asset_data: dict[str, Any],
    asset_file_path: str = "",
    template_file_path: str = "",
    temp_folder: str = "",
    result_path: str = "",
    result_folder: str = "",
    api_key: str = "",
    script: str = "",
    addons: str = "",
    binary_type: str = "CLOSEST",
    verbosity_level: int = 2,
    binary_path: str = "",
    target_format: str = "",
    env_overrides: dict[str, str] | None = None,
) -> int:
    """Run a Blender background script and wait for it to finish.

    Args:
        asset_data: Asset metadata used to select Blender and passed to the script.
        asset_file_path: Asset file to process.
        template_file_path: Optional .blend template to open first.
        temp_folder: Temporary directory used to store the JSON datafile and outputs.
        result_path: Output path used by the background script.
        result_folder: Output directory for multi-file results.
        api_key: API key string forwarded to the background script.
        script: Python file name in paths.BG_SCRIPTS_PATH to run with Blender.
        addons: Comma-separated addon names to enable in Blender.
        binary_type: 'CLOSEST' or 'NEWEST' to select Blender.
        verbosity_level: 0=quiet, 1=stderr only, 2=stdout+stderr streaming.
        binary_path: Explicit Blender binary path to use; if empty, autodetect.
        target_format: Optional target format forwarded to script.
        env_overrides: Optional environment variables for Blender process.

    Returns:
        Process return code from Blender.
    """
    binary_path = _select_binary_path(binary_path, asset_data, asset_file_path=asset_file_path, binary_type=binary_type)

    temp_folder, own_temp_folder = _ensure_temp_folder(temp_folder)
    payload = DataPayload(
        file_path=asset_file_path,
        result_filepath=result_path,
        result_folder=result_folder,
        asset_data=asset_data,
        api_key=api_key,
        target_format=target_format,
    )
    datafile = _write_datafile(temp_folder, payload)
    logger.info("Opening Blender instance to process script: %s", script)
    template_file_path = _resolve_template(template_file_path, asset_file_path)
    command = _build_command(binary_path, template_file_path, script, datafile, addons)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    returncode = _run_blender(command, verbosity_level, env=env)

    if returncode != 0:
        logger.error("Error while running command: %s", command)
        logger.error("Return code: %s", returncode)

    # cleanup
    _cleanup_paths(datafile, temp_folder, remove_temp_folder=own_temp_folder)

    return returncode
