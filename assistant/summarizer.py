"""Crawl a directory and produce a compact code-architecture summary."""
from __future__ import annotations

import os
import re

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", "dist", "build", "vendor"
})
_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".go", ".java", ".cs", ".cpp", ".c", ".h"
})

_CLASS_PATTERN = re.compile(
    r"^(?:export\s+)?class\s+(\w+)(?:\s*[\(:]?\s*([^{:\n]+))?",
    re.MULTILINE,
)
_DEF_PATTERN = re.compile(
    r"^(?:(?:export\s+)?(?:async\s+)?(?:def|function|const|let|var)\s+)(\w+)",
    re.MULTILINE,
)
_DOCSTRING_PATTERN = re.compile(r'^"""(.*?)"""', re.DOTALL)

_PATTERN_SIGNALS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b_instance\b|\binstance\(\)|def __new__"), "Singleton"),
    (re.compile(r"\bsubscribe\b|\bunsubscribe\b|\bnotify\b|\bemit\b|\bon_event\b"), "Observer"),
    (re.compile(r"\bcreate\b|\bbuild\b|\bfactory\b|\bmake_\w"), "Factory"),
    (re.compile(r"\bexecute\b.*\bundo\b|\bundo\b.*\bexecute\b", re.DOTALL), "Command"),
    (re.compile(r"\bABC\b|\babstractmethod\b|\bAbstractBase"), "Abstract/Strategy"),
    (re.compile(r"\bdecorat\w+\b|\b__wrapped__\b|\bwraps\b"), "Decorator"),
    (re.compile(r"\bRepository\b|\bDAO\b|\bMapper\b"), "Repository"),
]


def _detect_patterns(src: str) -> list[str]:
    return [name for pat, name in _PATTERN_SIGNALS if pat.search(src)]


def _extract_docstring(src: str) -> str:
    m = _DOCSTRING_PATTERN.search(src)
    if not m:
        return ""
    return m.group(1).strip().splitlines()[0].strip()


def _parse_bases(raw: str) -> str:
    raw = re.sub(r"\bextends\b|\bimplements\b", "", raw.strip().strip("()"))
    return ", ".join(b.strip() for b in raw.split(",") if b.strip())


def crawl(root: str) -> str:
    """Return a compact architecture summary of code files under root."""
    root = os.path.abspath(root)
    lines: list[str] = [f"# {os.path.basename(root)}/"]
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
                lines.append(f"{rel}: (unreadable)")
                continue

            doc = _extract_docstring(src)
            parts: list[str] = [rel + (":" if doc else "")]
            if doc:
                parts[0] += f" {doc}"

            classes = []
            for m in _CLASS_PATTERN.finditer(src):
                name = m.group(1)
                bases = _parse_bases(m.group(2) or "")
                classes.append(f"{name}({bases})" if bases else name)

            defs = _DEF_PATTERN.findall(src)
            patterns = _detect_patterns(src)

            detail: list[str] = []
            if classes:
                detail.append("cls:" + ",".join(classes))
            if defs:
                detail.append("fn:" + ",".join(defs))
            if patterns:
                detail.append("[" + ",".join(patterns) + "]")

            if detail:
                lines.append(parts[0] + "  " + "  ".join(detail))
            elif not doc:
                lines.append(rel)

    if not found_any:
        lines.append("(no code files found)")

    return "\n".join(lines)
