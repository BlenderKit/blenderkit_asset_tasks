"""Code that Blender loads must import on Blender 2.93, whose Python is 3.9.

sanitize_names.py annotated a parameter as ``str | PathLike[str]`` without
``from __future__ import annotations``. Python 3.9 evaluates that annotation
at import and raises TypeError, so from 2026-04-28 every unpack and
resolution run on an asset that selects Blender 2.93 died at import.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from collections.abc import Iterator

from helpers.testutils import ensure_src_on_path

REPO_ROOT = pathlib.Path(ensure_src_on_path())
# Imported inside Blender; the oldest Blender the builder runs ships Python 3.9.
BLENDER_LOADED_DIRS = ("blenderkit_server_utils", "blender_bg_scripts")


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _annotations(tree: ast.Module) -> Iterator[ast.expr]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            for arg in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                arguments.vararg,
                arguments.kwarg,
            ):
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _uses_union_operator(annotation: ast.expr) -> bool:
    return any(isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) for node in ast.walk(annotation))


class BlenderPythonCompatibilityTests(unittest.TestCase):
    def test_union_annotations_are_not_evaluated_at_import(self) -> None:
        offenders = []
        for directory in BLENDER_LOADED_DIRS:
            for path in sorted((REPO_ROOT / directory).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                if _has_future_annotations(tree):
                    continue
                lines = [annotation.lineno for annotation in _annotations(tree) if _uses_union_operator(annotation)]
                if lines:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lines[0]}")

        self.assertEqual(offenders, [], "these modules need `from __future__ import annotations`")


if __name__ == "__main__":
    unittest.main()
