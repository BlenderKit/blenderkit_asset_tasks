"""Validate Blender addon assets for basic safety and installability.

Checks include:
- Syntax errors in Python sources.
- Usage of os module.
- Usage of subprocess or process-spawning calls.
- Usage of eval/exec.
- Bandit scan before installation.
- Installation and enabling in Blender background mode.
- Sandbox execution to limit IO and block network calls.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

from blenderkit_server_utils import config, download, log, paths, send_to_bg, utils

logger = log.create_logger(__name__)

ADDON_FILETYPE_CANDIDATES = ["zip_file", "addon", "zip", "blend"]
SANDBOX_SUBDIRS = {
    "BLENDER_USER_CONFIG": "config",
    "BLENDER_USER_DATAFILES": "datafiles",
    "BLENDER_USER_SCRIPTS": "scripts",
    "BLENDER_USER_RESOURCES": "resources",
}
OS_SUBPROCESS_CALLS = {
    "os.system",
    "os.popen",
    "os.spawnl",
    "os.spawnlp",
    "os.spawnv",
    "os.spawnvp",
    "os.startfile",
}
NETWORK_MODULE_PREFIXES = (
    "aiohttp",
    "ftplib",
    "http",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "smtplib",
    "urllib",
    "urllib3",
)
NETWORK_CALL_PREFIXES = (
    "aiohttp.",
    "http.client.",
    "httpx.",
    "requests.",
    "socket.",
    "urllib.",
    "urllib3.",
)


def validate(asset_data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]] | None:
    """Validate addon asset by inspecting source and installing in Blender.

    Args:
        asset_data: Asset metadata from the server.

    Returns:
        Tuple of (status, reason, captured_data) or None on fatal error.
    """
    addon_zip_path = _download_addon_zip(asset_data)
    if not addon_zip_path:
        return None

    extract_dir = tempfile.mkdtemp(prefix="bk_addon_extract_")
    try:
        python_files = _read_addon_python_sources(addon_zip_path)
        analysis = _analyze_python_sources(python_files)

        bandit_report = _run_bandit_scan(addon_zip_path, extract_dir)
        install_result = _install_and_enable_addon(asset_data, addon_zip_path)

        captured = {
            "syntax_errors": analysis["syntax_errors"],
            "network_imports": analysis["network_imports"],
            "network_calls": analysis["network_calls"],
            "subprocess_imports": analysis["subprocess_imports"],
            "subprocess_calls": analysis["subprocess_calls"],
            "eval_calls": analysis["eval_calls"],
            "bandit": bandit_report,
            "install": install_result,
        }

        reason_parts: list[str] = []
        status = True
        if analysis["syntax_errors"]:
            status = False
            reason_parts.append("Syntax errors detected")
        if _install_failed(install_result):
            status = False
            reason_parts.append("Blender install/enable failed")
        if install_result.get("network_attempts"):
            status = False
            reason_parts.append("Network access attempted")
        if analysis["subprocess_calls"]:
            reason_parts.append("Uses process-spawning calls")
        if analysis["eval_calls"]:
            reason_parts.append("Uses eval/exec")
        if _bandit_failed(bandit_report):
            status = False
            reason_parts.append("Bandit scan failed")
        if _bandit_has_high_severity(bandit_report):
            status = False
            reason_parts.append("Bandit high severity findings")

        reason = "; ".join(reason_parts) if reason_parts else "Validation completed"
        result = (status, reason, captured)
        return result
    finally:
        utils.cleanup_temp(extract_dir)
        _safe_remove_file(addon_zip_path)
        _cleanup_empty_parent(addon_zip_path)


def _download_addon_zip(asset_data: dict[str, Any]) -> str | None:
    """Download the addon archive file for validation.

    Args:
        asset_data: Asset metadata.

    Returns:
        Path to the downloaded archive or None on failure.
    """
    destination_directory = tempfile.gettempdir()
    api_key = config.BLENDERKIT_API_KEY
    filetype = _pick_addon_filetype(asset_data)

    addon_path = download.download_asset(
        asset_data,
        filetype=filetype,
        api_key=api_key,
        directory=destination_directory,
    )
    if not addon_path:
        logger.warning("Failed to download addon asset %s", asset_data.get("id"))
        return None
    return addon_path


def _pick_addon_filetype(asset_data: dict[str, Any]) -> str:
    """Pick a likely fileType for addon downloads.

    Args:
        asset_data: Asset metadata with files list.

    Returns:
        File type string to request from the download API.
    """
    files = asset_data.get("files") or []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = _pick_filename(entry)
        if filename.endswith(".zip"):
            return str(entry.get("fileType", "addon")).lower()

    file_types = [str(f.get("fileType", "")).lower() for f in files if isinstance(f, dict)]
    for candidate in ADDON_FILETYPE_CANDIDATES:
        if candidate in file_types:
            return candidate
    return file_types[0] if file_types else "addon"


def _pick_filename(entry: dict[str, Any]) -> str:
    """Return the most reliable filename from a file entry.

    Args:
        entry: File entry mapping from asset metadata.

    Returns:
        Lowercased filename or empty string.
    """
    for key in ("fileName", "filename", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    url = entry.get("url") or entry.get("downloadUrl") or ""
    if isinstance(url, str) and url:
        return os.path.basename(url).lower()
    return ""


def _read_addon_python_sources(addon_zip_path: str) -> list[tuple[str, str]]:
    """Extract Python sources from the addon archive.

    Args:
        addon_zip_path: Path to the addon .zip archive.

    Returns:
        List of (path, source_text) pairs.
    """
    sources: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(addon_zip_path) as archive:
            for name in archive.namelist():
                if not name.endswith(".py"):
                    continue
                if name.startswith("__MACOSX/"):
                    continue
                try:
                    with archive.open(name) as handle:
                        content = handle.read().decode("utf-8", errors="replace")
                        sources.append((name, content))
                except OSError:
                    logger.exception("Failed reading %s from %s", name, addon_zip_path)
    except (OSError, zipfile.BadZipFile):
        logger.exception("Addon archive is invalid: %s", addon_zip_path)
    return sources


def _analyze_python_sources(sources: list[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:  # noqa: C901, PLR0912
    """Analyze Python sources for syntax errors and risky usage.

    Args:
        sources: List of (path, source_text) pairs.

    Returns:
        Dict with findings lists.
    """
    syntax_errors: list[dict[str, Any]] = []
    os_imports: list[dict[str, Any]] = []
    network_imports: list[dict[str, Any]] = []
    network_calls: list[dict[str, Any]] = []
    subprocess_imports: list[dict[str, Any]] = []
    subprocess_calls: list[dict[str, Any]] = []
    eval_calls: list[dict[str, Any]] = []

    for path, source in sources:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            syntax_errors.append({
                "file": path,
                "line": exc.lineno,
                "message": exc.msg,
            })
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "os":
                        os_imports.append({"file": path, "line": node.lineno, "name": alias.name})
                    if _is_network_module(alias.name):
                        network_imports.append({"file": path, "line": node.lineno, "name": alias.name})
                    if alias.name == "subprocess":
                        subprocess_imports.append({"file": path, "line": node.lineno, "name": alias.name})
            elif isinstance(node, ast.ImportFrom):
                if node.module == "os":
                    os_imports.append({"file": path, "line": node.lineno, "name": node.module})
                if _is_network_module(node.module):
                    network_imports.append({"file": path, "line": node.lineno, "name": node.module})
                if node.module == "subprocess":
                    subprocess_imports.append({"file": path, "line": node.lineno, "name": node.module})
            elif isinstance(node, ast.Call):
                call_name = _call_name(node)
                if _is_network_call(call_name):
                    network_calls.append({"file": path, "line": node.lineno, "call": call_name})
                if call_name in {"eval", "exec"}:
                    eval_calls.append({"file": path, "line": node.lineno, "call": call_name})
                if call_name in OS_SUBPROCESS_CALLS or call_name.startswith("subprocess."):
                    subprocess_calls.append({"file": path, "line": node.lineno, "call": call_name})

    return {
        "syntax_errors": syntax_errors,
        "os_imports": os_imports,
        "network_imports": network_imports,
        "network_calls": network_calls,
        "subprocess_imports": subprocess_imports,
        "subprocess_calls": subprocess_calls,
        "eval_calls": eval_calls,
    }


def _call_name(node: ast.Call) -> str:
    """Return a best-effort call name for an AST Call node.

    Args:
        node: AST Call node.

    Returns:
        The call name string.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _attribute_chain(node.func)
    return ""


def _attribute_chain(node: ast.Attribute) -> str:
    """Return a dotted attribute chain from an Attribute node.

    Args:
        node: AST Attribute node.

    Returns:
        Dotted attribute chain string.
    """
    parts: list[str] = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _is_network_module(module_name: str | None) -> bool:
    """Return True when the import module is network related.

    Args:
        module_name: Imported module name.

    Returns:
        True when the module indicates network usage.
    """
    if not module_name:
        return False
    return module_name.startswith(NETWORK_MODULE_PREFIXES)


def _is_network_call(call_name: str) -> bool:
    """Return True when the call is a network-related API.

    Args:
        call_name: Resolved call name.

    Returns:
        True when the call suggests network usage.
    """
    if not call_name:
        return False
    return call_name.startswith(NETWORK_CALL_PREFIXES)


def _install_and_enable_addon(asset_data: dict[str, Any], addon_zip_path: str) -> dict[str, Any]:
    """Install and enable the addon using Blender background mode.

    Args:
        asset_data: Asset metadata.
        addon_zip_path: Path to the addon archive.

    Returns:
        Dict with install/enabling/disabling results.
    """
    result: dict[str, Any] = {
        "install": None,
        "enabling": None,
        "disabling": None,
        "return_code": None,
        "result_json": None,
    }
    if not config.BLENDER_PATH:
        result["install"] = "Missing BLENDER_PATH"
        return result

    temp_folder = tempfile.mkdtemp(prefix="bk_addon_validate_")
    result_path = os.path.join(temp_folder, "addon_result.json")
    sandbox_dir = tempfile.mkdtemp(prefix="bk_addon_sandbox_")
    env_overrides = _build_sandbox_env(sandbox_dir)

    return_code = send_to_bg.send_to_bg(
        asset_data,
        asset_file_path=addon_zip_path,
        template_file_path=paths.get_clean_filepath(),
        result_path=result_path,
        script="test_addon_bg.py",
        binary_path=config.BLENDER_PATH,
        verbosity_level=2,
        env_overrides=env_overrides,
    )
    result["return_code"] = return_code

    try:
        with open(result_path, encoding="utf-8") as handle:
            result_json = json.load(handle)
            result.update(result_json)
            result["result_json"] = result_json
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read addon install results: %s", result_path)
        result["install"] = result.get("install") or "Missing result JSON"
    finally:
        utils.cleanup_temp(temp_folder)
        utils.cleanup_temp(sandbox_dir)

    return result


def _install_failed(result: dict[str, Any]) -> bool:
    """Return True when addon install/enable steps failed.

    Args:
        result: Install result mapping.

    Returns:
        True when any install step reports an error.
    """
    if not result:
        return True
    return_code = result.get("return_code")
    if isinstance(return_code, int) and return_code != 0:
        return True
    for key in ("install", "enabling", "disabling"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return True
    return False


def _run_bandit_scan(addon_zip_path: str, extract_dir: str) -> dict[str, Any]:
    """Run bandit against the addon package before installation.

    Args:
        addon_zip_path: Path to the addon archive.
        extract_dir: Directory to extract the archive into.

    Returns:
        Bandit report mapping with output and issues.
    """
    report: dict[str, Any] = {
        "status": "ok",
        "return_code": None,
        "issues": [],
        "error": "",
    }
    try:
        with zipfile.ZipFile(addon_zip_path) as archive:
            archive.extractall(extract_dir)
    except (OSError, zipfile.BadZipFile):
        logger.exception("Failed to extract addon for bandit: %s", addon_zip_path)
        report["status"] = "error"
        report["error"] = "Failed to extract addon"
        return report

    command = [sys.executable, "-m", "bandit", "-r", extract_dir, "-f", "json"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        logger.exception("Bandit execution failed for %s", extract_dir)
        report["status"] = "error"
        report["error"] = "Bandit execution failed"
        return report

    report["return_code"] = result.returncode
    if result.stdout:
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            report["status"] = "error"
            report["error"] = "Bandit output not JSON"
            return report
        report["issues"] = parsed.get("results", [])
    if result.returncode not in {0, 1}:
        report["status"] = "error"
        report["error"] = "Bandit returned error"
    return report


def _bandit_failed(report: dict[str, Any]) -> bool:
    """Return True when the bandit scan failed.

    Args:
        report: Bandit report mapping.

    Returns:
        True when bandit did not execute cleanly.
    """
    return report.get("status") != "ok"


def _bandit_has_high_severity(report: dict[str, Any]) -> bool:
    """Return True if bandit results include high severity issues.

    Args:
        report: Bandit report mapping.

    Returns:
        True when a high severity issue is present.
    """
    for issue in report.get("issues", []):
        severity = str(issue.get("issue_severity", "")).upper()
        if severity == "HIGH":
            return True
    return False


def _build_sandbox_env(sandbox_dir: str) -> dict[str, str]:
    """Build environment overrides to keep Blender in a sandbox.

    Args:
        sandbox_dir: Base sandbox directory.

    Returns:
        Environment overrides for Blender process.
    """
    env_overrides: dict[str, str] = {
        "TMP": sandbox_dir,
        "TEMP": sandbox_dir,
        "TMPDIR": sandbox_dir,
        "HOME": sandbox_dir,
        "USERPROFILE": sandbox_dir,
        "APPDATA": sandbox_dir,
        "LOCALAPPDATA": sandbox_dir,
    }
    for key, subdir in SANDBOX_SUBDIRS.items():
        target = os.path.join(sandbox_dir, subdir)
        os.makedirs(target, exist_ok=True)
        env_overrides[key] = target
    return env_overrides


def _safe_remove_file(path: str) -> None:
    """Remove a file if it exists.

    Args:
        path: File path to remove.
    """
    try:
        os.remove(path)
    except (FileNotFoundError, PermissionError, OSError):
        logger.debug("Failed to remove file: %s", path, exc_info=True)


def _cleanup_empty_parent(path: str) -> None:
    """Remove the parent folder if it becomes empty.

    Args:
        path: File path whose parent may be removed.
    """
    if not path:
        return
    parent = os.path.dirname(path)
    if not parent or not os.path.isdir(parent):
        return
    if os.path.abspath(parent) == os.path.abspath(tempfile.gettempdir()):
        return
    try:
        if not os.listdir(parent):
            os.rmdir(parent)
    except (FileNotFoundError, PermissionError, OSError):
        logger.debug("Failed to remove empty folder: %s", parent, exc_info=True)
