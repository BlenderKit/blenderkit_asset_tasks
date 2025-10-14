# Tests for blenderkit_asset_tasks

## Structure
- `.tests/unittests` holds unit tests.
- Helpers in `.tests/unittests/helpers` provide a lightweight `bpy` mock and path setup.

## Running
You can run tests from the repo root:

```powershell
# Windows PowerShell
python -m unittest discover -s .tests/unittests -p "test_*.py" -v
```

These tests do not require Blender. The `bpy` module is mocked where needed.
