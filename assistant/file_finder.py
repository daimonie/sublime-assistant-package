"""Find file content by name across open tabs and project folders."""
from __future__ import annotations

import os
import sublime

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", "dist", "build", "vendor"
})


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def find(window: sublime.Window, filename: str) -> str | None:
    """Return file content for the closest match to the active file, or None."""
    filename = os.path.basename(filename).strip()

    # 1. Active file's directory first — walk upward through ancestors.
    #    This finds docker-compose.yml, Makefile, etc. in the project root
    #    rather than a copy in a sibling project folder.
    active = window.active_view_in_group(0)
    active_path = active.file_name() if active else None
    if active_path:
        current = os.path.dirname(active_path)
        while True:
            candidate = os.path.join(current, filename)
            if os.path.isfile(candidate):
                return _read(candidate)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    # 2. Open tabs — prefer the tab whose path shares the longest common prefix
    #    with the active file so we don't grab a tab from a different project.
    tab_matches: list[tuple[int, str]] = []
    for view in window.views():
        path = view.file_name()
        if path and os.path.basename(path) == filename:
            common = len(os.path.commonprefix([active_path or "", path]))
            tab_matches.append((common, path))
        elif view.name() == filename:
            tab_matches.append((0, ""))
            # Return inline buffer content immediately (no path to rank by)
            return view.substr(sublime.Region(0, view.size()))
    if tab_matches:
        tab_matches.sort(key=lambda t: t[0], reverse=True)
        best_path = tab_matches[0][1]
        if best_path:
            return _read(best_path)

    # 3. Full recursive walk across project folders — last resort.
    for folder in window.folders():
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
            if filename in files:
                return _read(os.path.join(root, filename))

    return None
