# blenderkit_asset_tasks
Scripts to do automatic processing of Blender assets in the database.

## Quick Start

### Prerequisites
- Python 3.11 or higher
- But all code must be compatible with Python 3.9 at least all code that runs in Blender bin
- [UV package manager](https://docs.astral.sh/uv/) (automatically installed by setup scripts if not present)
- Blender installation(s) - see [Blender Requirements](#requirements-for-blender) section

### Project Setup

**Windows (PowerShell/CMD):**
```cmd
.scripts\setup_project.bat
```

**Linux/macOS:**
```bash
.scripts/setup_project.sh
```

The setup script will:
1. Install UV package manager if not present
2. Create a virtual environment (`.venv`)
3. Install all dependencies from `pyproject.toml`
4. Set up the development environment

### Running Scripts

After setup, activate the virtual environment and run any script:

**Windows:**
```cmd
.venv\Scripts\activate
python generate_gltf.py --target_format gltf_godot
```

**Linux/macOS:**
```bash
source .venv/bin/activate
python generate_gltf.py --target_format gltf_godot
```

## Project Structure

- `.github/` - GitHub Actions workflow definitions
- `.scripts/` - Development utilities for project setup, testing, and automation
- `.tests/` - Unit tests for synthetic testing
- `blend_files/` - Template .blend projects used by processing tasks
- `blender_bg_scripts/` - Scripts executed inside Blender instances
- `blenderkit_server_utils/` - Shared Python module for common functionality
- `./` - Standalone scripts for specific asset processing tasks
- `temp/` - Output directory for test results in JSON format

## Architecture Principles

Scripts in the root are standalone scripts which perform, preferably one, specific task.
They can import from `blenderkit_server_utils`, but should not import from one another.
If code needs to be shared, it should be placed in `blenderkit_server_utils`!

Standalone scripts often need to execute code inside Blender instances.
For this, they start Blender with scripts from `blender_bg_scripts/`.
All code that runs inside Blender should be placed in `blender_bg_scripts/`.

## Dependency Management

This project uses [UV](https://docs.astral.sh/uv/) for fast, reliable Python package management:

- **Dependencies**: Defined in `pyproject.toml` under `[project.dependencies]`
- **Development Dependencies**: Defined in `pyproject.toml` under `[dependency-groups.dev]`
- **Lock File**: `uv.lock` ensures reproducible installations
- **Legacy**: `requirements.txt` is maintained for compatibility but `pyproject.toml` is the source of truth

### Managing Dependencies

**Add a new dependency:**
```bash
uv add package-name
```

**Add a development dependency:**
```bash
uv add --group dev package-name
```

**Update dependencies:**
```bash
uv sync --upgrade
```

**Install specific groups:**
```bash
uv sync --group dev  # Install dev dependencies
```

## Available Scripts

### Main Processing Scripts
- `generate_gltf.py` - Convert assets to GLTF format for various target platforms
- `generate_resolutions.py` - Generate different resolution variants of assets
- `generate_validations.py` - Validate asset integrity and compatibility
- `render_thumbnail.py` - Generate thumbnail images for assets
- `generate_caption_alt_text_gpt.py` - Generate captions using GPT
- `generate_caption_clip_interrogator.py` - Generate captions using CLIP Interrogator
- `reindex.py` - Reindex assets in the database
- `sync_TwinBru_library.py` - Synchronize with TwinBru library

### Testing Scripts
- `test_addon.py` - Smoke test for Blender add-ons
- `test_addon_report.py` - Generate test reports from addon test results

### Development Utilities (`.scripts/`)
- `setup_project.bat` / `setup_project.sh` - Automated project setup
- `dispatch_workflow.py` - Trigger GitHub Actions workflows
- `just_download_asset.py` - Download assets for testing
- `run_unittests_in_blender.py` - Run unit tests inside Blender
- `start_blender_test.bat` - Start Blender with test configuration

## Logging & Debugging

All scripts now use a unified logger (`blenderkit_server_utils.log.create_logger`).
Set the environment variable `DEBUG_LOGGING=1` (any non-empty value) to switch global log level to DEBUG for more detailed diagnostics (per‑thread events, API calls, subprocess command lines, etc.).

Examples:

Windows (PowerShell):
```powershell
$env:DEBUG_LOGGING=1
python generate_gltf.py --target_format gltf_godot
```

Windows (cmd):
```cmd
set DEBUG_LOGGING=1
python generate_gltf.py --target_format gltf_godot
```

Linux / macOS:
```bash
DEBUG_LOGGING=1 python generate_gltf.py  --target_format gltf_godot
```

In a `.env` file (VS Code auto-loads if configured):
```
DEBUG_LOGGING=1
```

GitHub Actions (add to a step’s env):
```yaml
env:
  DEBUG_LOGGING: "1"
```

Unset or leave empty to fall back to INFO level.

## Environment Configuration

The project supports configuration through environment variables and `.env` files:
(not shared "secrets")

### Required Environment Variables
- `BLENDER_PATH` - Path to single Blender executable (for single-version scripts)
- `BLENDERS_PATH` - Path to directory containing multiple Blender versions (for multi-version scripts)

### Optional Environment Variables
- `DEBUG_LOGGING` - Set to `1` to enable debug logging
- Additional variables may be required depending on the specific script being used

### Using .env Files
Create a `.env` file in the project root:
```env
DEBUG_LOGGING=1
BLENDER_PATH=C:\Program Files\Blender Foundation\Blender 4.2\blender.exe
BLENDERS_PATH=C:\BlenderVersions
OTHER_SECRETS=....
```
Place your API keys in `<this_repo>/.env` for ease of use, this file is not gitted.

**Note**: Restart VS Code after updating `.env` files to ensure changes are loaded.

### VS Code Configuration
Project-specific settings can be configured in `.vscode/settings.json` for:
- Python interpreter paths
- Environment variable loading
- Extension-specific configurations
- Workspace-specific preferences

### Requirements for Blender

Assets tasks have to start a Blender to make the job and the questions is which version?
Basically there are 2 types of scripts in this repo:
- requiring multiple versions of Blender (we want to target closest to original)
- requiring one target version of Blender (we know the version in advance, or we are ok with just one version, preferebly latest)

#### Multiple versions
There are tasks where we need the asset to be processed in the same Blender version as original to keep compatibility with as old Blender as possible.
Like in case of generating resolutions which will be later imported by users into their Blenders.
Script automatically detects closest Blender to use.

We define the path `BLENDERS_PATH` to directory containing multiple installations of Blender.
Each version of the Blender should be placed in directory named `X.Y` so the scripts can detect it automatically:
![image](https://user-images.githubusercontent.com/6907354/203579508-952ba12e-6a83-49dd-bca2-b3d33dd1ad36.png)

For example:

```
BLENDERS_PATH="/Users/ag/blenders"
ls /Users/ag/blenders
2.93
3.0
3.1
4.0
4.1
```

NOTE: On MacOS you will need to create a symbolic links.

For multiple versions scripts you can use docker image `blenderkit/headless-blender:multi-version`.
It has the blenders directory at: `/home/headless/blenders`.

#### Single versions
There are tasks in which we do not care about compatibility with older Blenders.
Latest Blender brings better stability and performance in these cases.
Like in case of generating GLTFs or renders which does not get imported back to users' Blenders.
Scripts will just use the specified path directly to Blender executable defined by `BLENDER_PATH`.

For example: `BLENDER_PATH=/Applications/Blender420/Contents/MacOS/Blender`

For single versions scripts you can use docker image `blenderkit/headless-blender:blender-x.y`.
It has the blender executable placed at: `/home/headless/blender/blender`.

## CI

### Developing

### Trigger job via webhook

Webhook can be tested with this curl command:

```
curl -X POST -H "Accept: application/vnd.github.v3+json" \
     -H "Authorization: token <TOKEN>" \
     https://api.github.com/repos/blenderkit/blenderkit_asset_tasks/actions/workflows/webhook_process_asset.yml/dispatches \
     -d '{
       "ref": "main",
       "inputs": {
         "asset_base_id": "eda7948f-a0a5-457a-b0a0-c4031bed093d",
         "asset_type": "model",
         "verification_status": "validated",
         "is_private": true,
         "source_app_version_xy": "4.3",
       }
     }'
```

## Development

### Code Quality
The project uses several tools for code quality and consistency:

- **Ruff**: Linting and code formatting (configured in `pyproject.toml`)
- **Bandit**: Security analysis (configured in `_bandit.yaml`)
- **Pre-commit**: Git hooks for automated checks
- **Pydoclint**: Docstring validation

**Run code quality checks:**
```bash
# Activate virtual environment first
uv run ruff check .
uv run ruff format .
uv run bandit -r . -f json
```
or run actions defined in `.vscode/launch.json`


**Set up pre-commit hooks:**
```bash
uv run pre-commit install
```

### Testing
Run tests using the provided utilities:

```bash
# Run unit tests in Blender environment
python .scripts/run_unittests_in_blender.py

# Test addon functionality
python test_addon.py
```
or run actions defined in `.vscode/launch.json`


### Common Development Tasks

**Set up a fresh development environment:**
```bash
# Windows
.scripts\setup_project.bat

# Linux/macOS
.scripts/setup_project.sh
```
or run actions defined in `.vscode/launch.json`


**Add new dependencies:**
```bash
# Runtime dependency
uv add requests

# Development dependency
uv add --group dev pytest
```
Better way is to edit `pyproject.toml` an re-run `project_setup.bat`


### JOBS

#### webhook_process_asset.yml
This job handles generation of resolutions and gltf files.

#### webhook_test_addon.yml - test_addon.py & test_addon_report.py
Workflow to do smoke tests of add-ons.
Script test_addon.py prints informations about progress to console, and also at the end generates a .JSON file containing the test results.
Test results are saved into `./temp/test_addon_results.json`.

In the Github workflow the results file is uploaded and saved as an artifact into `./temp/blender-{blender_version_X_Y}/test_addon_results.json`.
Once the jobs for every minor Blender release finish, then final reporting job is started.
This job downloads all artifacts and starts `test_addon_report.py` script which parses all the JSON files into an informative comment and uploads it to blenderkit.com.

## Troubleshooting

### Common Issues

**UV not found:**
- The setup scripts will automatically install UV if not present
- Manual installation: https://docs.astral.sh/uv/getting-started/installation/
- Ensure UV is in your system PATH (is handled during automated setup)

**Blender path issues:**
- Set `BLENDER_PATH` for single-version scripts
- Set `BLENDERS_PATH` for multi-version scripts
- Use absolute paths to avoid issues
- On macOS, you may need to create symbolic links for multiple versions

**Dependencies not installing:**
- Ensure you're using Python 3.11 or higher
- Try removing `.venv` and running setup script again
- Check that `pyproject.toml` is not corrupted

**Import errors:**
- Ensure virtual environment is activated
- Run `uv sync` to ensure all dependencies are installed
- Check that the script is being run from the project root
- if launchers in `.vscode/launch.json` are correctly setup, it is better to use those

### Getting Help
- Check the logs with `DEBUG_LOGGING=1` for detailed diagnostics
- Review the GitHub Actions workflows in `.github/` for CI/CD examples
- Examine existing scripts for usage patterns

