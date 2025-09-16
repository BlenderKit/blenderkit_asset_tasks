@REM Run Blender-based unittests from repo root

@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Move to repository root (two levels up from this script)
set current_dir=%~dp0
set "script_dir=%~dp0"
cd /d "%script_dir%\..\.."

:: clear log file
if exist !current_dir!blender_test_results.txt del !current_dir!blender_test_results.txt

rem List of candidate Blender executables to try
set "used_blender_versions="

:search_blender
set "blender_path="
for %%B in (
    "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
    "C:\Program Files\Blender Foundation\Blender 4.1\blender.exe"
    "C:\Program Files\Blender Foundation\Blender 4.0\blender.exe"
    "D:\blender-4.5.3-windows-x64\blender.exe"
    "D:\blender-3.0.0-windows-x64\blender.exe"
) do (
    echo Checking %%~B
    if exist "%%~B" (
        set "candidate=%%~B"
        rem Skip if already used
        set "skip="
        for %%U in (!used_blender_versions!) do (
            if /I "%%~U"=="%%~B" set "skip=1"
        )
        if not defined skip (
            set "blender_path=%%~B"
            goto found_blender
        )
    )
)

if not defined blender_path (
    echo Blender not found
    exit /b 1
)


:found_blender
echo Using blender at "%blender_path%"



rem Run tests; write output to file (suppress unittest duplicate prints)
"%blender_path%" -y -b -noaudio -P blenderkit_asset_tasks\_scripts\run_unittests_in_blender.py -- -s blenderkit_asset_tasks\_test_blenderkit_asset_tasks\unittests -p "test_*.py" --runner-stream none >> !current_dir!blender_test_results.txt 2>&1
if errorlevel 1 (
    echo Tests failed. See blender_test_results.txt for details.
    exit /b 1
) else (
    echo Tests completed successfully. See blender_test_results.txt for details.
)

rem Mark this Blender as used and look for another
set "used_blender_versions=!used_blender_versions! %blender_path%"
goto search_blender
