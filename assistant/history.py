"""Per-window conversation history store."""
from __future__ import annotations

_store: dict[int, list[dict]] = {}


def get_messages(window_id: int, system_prompt: str) -> list[dict]:
    """Return a snapshot of history, initialising with system prompt if new."""
    if window_id not in _store:
        _store[window_id] = [{"role": "system", "content": system_prompt}]
    return list(_store[window_id])


def append(window_id: int, role: str, content: str) -> None:
    """Append a message to the stored history."""
    if window_id in _store:
        _store[window_id].append({"role": role, "content": content})
