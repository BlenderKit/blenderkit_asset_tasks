#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"

echo "Script directory: ${script_dir}"
echo "Repository directory: ${repo_dir}"

echo "--- Checking for 'uv' ---"
if ! command -v uv >/dev/null 2>&1; then
    echo "'uv' not found. Attempting installation..."
    if command -v curl >/dev/null 2>&1; then
        installer='curl -LsSf https://astral.sh/uv/install.sh | sh'
    elif command -v wget >/dev/null 2>&1; then
        installer='wget -qO- https://astral.sh/uv/install.sh | sh'
    else
        echo "Neither curl nor wget is available; cannot install 'uv'." >&2
        exit 1
    fi
    echo "+ ${installer}"
    # shellcheck disable=SC2086
    eval ${installer}
    export PATH="${HOME}/.local/bin:${PATH}"
    if ! command -v uv >/dev/null 2>&1; then
        echo "'uv' still not found after installation. Add ${HOME}/.local/bin to PATH or install manually." >&2
        exit 1
    fi
else
    echo "'uv' found: $(command -v uv)"
fi

echo "--- Updating uv (best effort) ---"
if ! uv self update; then
    echo "Warning: 'uv self update' failed (continuing)." >&2
fi

cd "${repo_dir}"

echo "----------------------------------------"
echo "Setting up project at: ${repo_dir}"
echo "----------------------------------------"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (.venv)..."
    uv venv .venv
else
    echo "Reusing existing virtual environment (.venv)."
fi

if [ -f "pyproject.toml" ]; then
    echo "Syncing dependencies from pyproject.toml..."
    uv sync
else
    echo "No pyproject.toml found at ${repo_dir}; skipping dependency sync."
fi

echo "----------------------------------------"
echo "Virtual environment setup completed successfully."
