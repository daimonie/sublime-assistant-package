"""Load project-level prompt rules from AGENTS.md and SKILLS.md."""
from __future__ import annotations

import os

# win_id -> combined rules text (empty string means "checked and found nothing")
_cache: dict[int, str] = {}


def load(git_root: str, win_id: int) -> str:
    """Return combined contents of AGENTS.md and SKILLS.md from git_root.

    Results are cached per window so the files are only read once per session.
    """
    if win_id in _cache:
        return _cache[win_id]
    parts: list[str] = []
    for name in ("AGENTS.md", "SKILLS.md"):
        path = os.path.join(git_root, name)
        if os.path.isfile(path):
            try:
                text = open(path, encoding="utf-8").read().strip()
                if text:
                    parts.append(text)
            except Exception:
                pass
    combined = "\n\n".join(parts)
    _cache[win_id] = combined
    return combined
