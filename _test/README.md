# Tests for blenderkit_asset_tasks

## Structure
- `_test/unittests` holds unit tests.
- Helpers in `_test/unittests/helpers` provide a lightweight `bpy` mock and path setup.

## Running
You can run tests from the repo root:

```powershell
# Windows PowerShell
python -m unittest discover -s _test/unittests -p "test_*.py" -v
```

These tests do not require Blender. The `bpy` module is mocked where needed.
