# Tests for blenderkit_asset_tasks

## Structure
- `_test_blenderkit_asset_tasks/unittests` holds unit tests.
- Helpers in `_test_blenderkit_asset_tasks/unittests/helpers` provide a lightweight `bpy` mock and path setup.

## Running
You can run tests from the repo root:

```powershell
# Windows PowerShell
python -m unittest discover -s _test_blenderkit_asset_tasks/unittests -p "test_*.py" -v
```

These tests do not require Blender. The `bpy` module is mocked where needed.
