@echo off
setlocal EnableDelayedExpansion

for %%I in ("%~dp0..") do set "REPO_FOLDER=%%~fI"
set "THIS_FOLDER=%~dp0"

:: print for debug
echo Script directory: !THIS_FOLDER!
echo Repository directory: !REPO_FOLDER!

:: Single-project mode: operate only on parent project directory

:: check if UV is accessible in paths
:: if not ERROR and exit
where uv >nul 2>nul
if errorlevel 1 (
    :: try to install UV and set paths automatically
    echo "'uv' command not found in PATH. Attempting to install 'uv'..."
    :: must run powershell
    rem Invoke PowerShell to install uv (use full cmdlet names for reliability in cmd context)
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression"

    if errorlevel 1 (
        echo "Failed to install 'uv'. Please install it manually (https://docs.astral.sh/uv/getting-started/installation/)."
        exit /b 1
    )
    :: set paths
    set Path=C:\Users\%USERNAME%\.local\bin;!Path!

    :: check again
    where uv >nul 2>nul
    if errorlevel 1 (
        echo "'uv' command still not found in PATH after installation. Please ensure it is accessible in your system PATH."
        exit /b 1
    )
)

:: update uv itself
uv self update

pushd "!REPO_FOLDER!"

echo ----------------------------------------
echo Setting up project at: !REPO_FOLDER!
echo ----------------------------------------

if not exist ".venv" (
    echo Creating virtual environment...
    uv venv .venv
) else (
    echo Reusing existing virtual environment.
)

if exist "pyproject.toml" (
    echo Syncing dependencies from pyproject.toml...
    uv sync
) else (
    echo No pyproject.toml found at !REPO_FOLDER!, skipping dependency sync.
)

echo Virtual environment setup completed successfully.
echo ----------------------------------------
popd >nul
endlocal
