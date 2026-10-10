"""Fail if backend code contains literals copied from a specific sketch.

Flags string constants that look like a dimension value (e.g. "50'" or '12"'),
outside docstrings. Fix by deriving the value from the data, not by typing it.
"""
import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
DIMENSION_LITERAL = re.compile(r"""^\s*\d+\s*['"]""")


def _docstring_nodes(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_no_sketch_specific_dimension_literals():
    offenders = []
    for path in BACKEND.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                if DIMENSION_LITERAL.match(node.value):
                    offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert not offenders, "hardcoded dimension literals:\n" + "\n".join(offenders)
