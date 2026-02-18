"""Find file content by name across open tabs and project folders."""
from __future__ import annotations

import os
import sublime

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", "dist", "build", "vendor"
})


def find(window: sublime.Window, filename: str) -> str | None:
    """Return file content for the first match, or None if not found."""
    filename = os.path.basename(filename).strip()

    for view in window.views():
        path = view.file_name()
        if (path and os.path.basename(path) == filename) or view.name() == filename:
            return view.substr(sublime.Region(0, view.size()))

    for folder in window.folders():
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
            if filename in files:
                try:
                    with open(os.path.join(root, filename), encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    return None

    return None
