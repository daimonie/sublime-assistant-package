"""Build the LLM context block from active file, selection, and @file references."""
from __future__ import annotations

import re
from typing import NamedTuple

import sublime

from .file_finder import find as find_file

_REF_PATTERN = re.compile(r"@([\w.\-]+\.[\w\-]+)")


class ContextResult(NamedTuple):
    content: str
    hints: list[str]


def build(
    window: sublime.Window,
    query: str,
    active_file: str,
    active_filename: str,
    selection: str,
) -> ContextResult:
    """Assemble the full user message content and collect UI hints."""
    parts: list[str] = []
    hints: list[str] = []

    if active_file:
        parts.append(f"--- ACTIVE FILE ({active_filename}) ---\n{active_file}")

    for fname in _REF_PATTERN.findall(query):
        content = find_file(window, fname)
        if content is not None:
            parts.append(f"--- REFERENCED FILE: {fname} ---\n{content}")
            hints.append(f"@{fname}")
        else:
            parts.append(f"--- REFERENCED FILE: {fname} (NOT FOUND) ---")
            hints.append(f"@{fname} (not found)")

    if selection:
        parts.append(f"--- SELECTED CODE ---\n{selection}")
        hints.append("selection")

    parts.append(f"--- QUERY ---\n{query}")

    return ContextResult(content="\n\n".join(parts), hints=hints)
