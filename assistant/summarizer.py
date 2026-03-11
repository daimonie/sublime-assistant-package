"""Crawl a directory and produce a structured code-structure summary."""
from __future__ import annotations

import os
import re

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", "dist", "build", "vendor"
})
_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".go", ".java", ".cs", ".cpp", ".c", ".h"
})
# Matches top-level `def`, `async def`, and `class` in Python/JS/TS/etc.
_DEF_PATTERN = re.compile(
    r"^(?:(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let|var)\s+)(\w+)",
    re.MULTILINE,
)


def crawl(root: str) -> str:
    """Return a structured summary of code files under root."""
    root = os.path.abspath(root)
    lines: list[str] = [f"Directory: {root}", ""]
    found_any = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _CODE_EXTS:
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            found_any = True
            try:
                with open(fpath, encoding="utf-8") as f:
                    src = f.read()
            except Exception:
                lines.append(f"{rel}")
                lines.append("  (could not read)")
                continue
            defs = _DEF_PATTERN.findall(src)
            lines.append(rel)
            for d in defs:
                lines.append(f"  - {d}")
            if not defs:
                lines.append("  (no top-level definitions found)")

    if not found_any:
        lines.append("(no code files found)")

    return "\n".join(lines)
