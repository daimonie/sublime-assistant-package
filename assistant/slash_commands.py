"""Slash command parsing and template expansion."""
from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "/explain":  "Explain the following code: what it does, how it works, and any non-obvious design choices:\n\n",
    "/fix":      "Identify and fix bugs or issues in the following code. Explain what was wrong:\n\n",
    "/tests":    "Write thorough unit tests for the following code:\n\n",
    "/review":   "Review the following code for correctness, style, performance, and maintainability:\n\n",
    "/debug":    "Debug the following issue. Identify the root cause and propose a fix:\n\n",
    "/docs":     "Write clear documentation (docstrings and comments) for the following code:\n\n",
    "/diff": "Explain and review the following git diff:\n\n",
}

# These commands trigger plugin behaviour; no template expansion.
# /goal and /loop drive the iterative goal-pursuit loop (assistant/loop_runner.py);
# /research is a preset over that same loop rather than a single-shot template.
SPECIAL: frozenset[str] = frozenset({"/init", "/compact", "/clear", "/goal", "/loop", "/research"})


def parse(query: str) -> tuple[str, str, str]:
    """Return (display_query, api_query, command_name).

    display_query — original user text, shown in chat panel.
    api_query     — expanded text sent to the model.
    command_name  — matched command (e.g. '/explain') or "" if none.
    """
    stripped = query.strip()
    for cmd in SPECIAL:
        if stripped == cmd or stripped.startswith(cmd + " "):
            return query, query, cmd
    for cmd, template in TEMPLATES.items():
        if stripped == cmd or stripped.startswith(cmd + " "):
            remainder = stripped[len(cmd):].lstrip()
            return query, template + remainder, cmd
    return query, query, ""
