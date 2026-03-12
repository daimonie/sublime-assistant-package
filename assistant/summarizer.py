"""Crawl a directory and produce a compact code-architecture summary."""
from __future__ import annotations

import os
import re

_IGNORE_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "dist", "build", "vendor"
})
_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".md", ".sql", ".yml", ".yaml"
})
_MAX_FILE_BYTES = 1_000_000  # skip files larger than 1 MB

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


def crawl(target_dir: str) -> tuple[str, dict[str, str]]:
    """Crawl the directory and generate a summary, stopping at .git boundaries."""
    file_contents: dict[str, str] = {}
    summary_lines: list[str] = []

    def _crawl_directory(directory: str) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir():
                    if entry.name == ".git":
                        continue  # Skip .git directory
                    _crawl_directory(entry.path)  # Recurse into subdirectory
                elif entry.is_file() and os.path.splitext(entry.name)[1] in _CODE_EXTS:
                    rel_path = os.path.relpath(entry.path, target_dir)
                    try:
                        with open(entry.path, "r", encoding="utf-8") as f:
                            content = f.read()
                            file_contents[rel_path] = content
                            summary_lines.append(rel_path)
                    except Exception:
                        continue

    _crawl_directory(target_dir)
    raw_summary = "\n".join(summary_lines)
    return raw_summary, file_contents
