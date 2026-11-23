"""What the censuses over api/'s source walk its syntax trees with."""

from __future__ import annotations

import ast


def _qualnames(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}::{child.name}" if prefix else child.name
                if not isinstance(child, ast.ClassDef):
                    out.append((name, child))
                walk(child, name)
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
