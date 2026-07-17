"""Load project-level prompt rules from AGENTS.md.

Per-skill instructions used to live here too (a flat SKILLS.md), but that's now
handled by assistant.skills, which loads skills/<name>/SKILL.md files on demand
instead of always injecting their full text.
"""
from __future__ import annotations

import os

# win_id -> rules text (empty string means "checked and found nothing")
_cache: dict[int, str] = {}


def load(git_root: str, win_id: int) -> str:
    """Return the contents of AGENTS.md from git_root, or "" if it doesn't exist.

    Results are cached per window so the file is only read once per session.
    """
    if win_id in _cache:
        return _cache[win_id]
    path = os.path.join(git_root, "AGENTS.md")
    text = ""
    if os.path.isfile(path):
        try:
            text = open(path, encoding="utf-8").read().strip()
        except Exception:
            pass
    _cache[win_id] = text
    return text
