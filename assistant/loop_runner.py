"""Iterative goal-pursuit loop: prompt building and status-marker parsing.

Each loop iteration is a normal assistant turn (built and sent the same way
as any other query) that is required to end with a hidden HTML-comment
marker declaring whether the goal is done or more work remains. These
functions are pure so the marker protocol can be unit-tested without a
running Sublime instance.
"""
from __future__ import annotations

import re

DEFAULT_MAX_ITERATIONS = 8

_MARKER_COMPLETE_RE = re.compile(r"<!--\s*LOOP_STATUS:\s*complete\s*-->", re.IGNORECASE)
_MARKER_CONTINUE_RE = re.compile(r"<!--\s*LOOP_STATUS:\s*continue:\s*(.*?)\s*-->", re.IGNORECASE)


def build_iteration_prompt(goal: str, iteration: int, max_iterations: int) -> str:
    """Build the user-turn text for one loop iteration pursuing `goal`."""
    return (
        f"You are pursuing this goal, working autonomously across multiple turns:\n\n"
        f"GOAL: {goal}\n\n"
        f"This is iteration {iteration} of at most {max_iterations}. Use the available "
        "tools (read_file, fetch_url, list_project_files, get_file_summary) as needed to "
        "make progress, then report what you did this iteration.\n\n"
        "End your reply with exactly one of the following as the very last line, and "
        "nothing after it:\n"
        "- `<!-- LOOP_STATUS: complete -->` — if the goal is now fully achieved.\n"
        "- `<!-- LOOP_STATUS: continue: <one-sentence next step> -->` — if more work remains."
    )


def is_goal_complete(reply: str) -> bool:
    """True if `reply` contains the 'complete' status marker."""
    return bool(_MARKER_COMPLETE_RE.search(reply))


def has_status_marker(reply: str) -> bool:
    """True if `reply` contains either status marker form."""
    return bool(_MARKER_COMPLETE_RE.search(reply) or _MARKER_CONTINUE_RE.search(reply))


def extract_next_step(reply: str) -> str:
    """Return the next-step text from a 'continue' marker, or '' if absent."""
    m = _MARKER_CONTINUE_RE.search(reply)
    return m.group(1).strip() if m else ""


def build_research_goal(topic: str) -> str:
    """Build the goal text used by /research: a topic wrapped in research-specific instructions."""
    return (
        "Research the following topic thoroughly across multiple iterations. Use the "
        "fetch_url tool to consult several authoritative sources rather than stopping at "
        "the first one, cross-check claims where sources disagree, and cite a source for "
        f"every claim in your final summary: {topic}"
    )
