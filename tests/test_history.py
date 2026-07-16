"""Unit tests for assistant.history — per-window conversation history store."""
from __future__ import annotations

import itertools

from assistant import history

_ids = itertools.count(100000)


def _window_id() -> int:
    """A fresh window id per test, since history's store is a module-level
    global and tests must not bleed state into one another."""
    return next(_ids)


def test_get_messages_initializes_with_system_prompt():
    wid = _window_id()
    messages = history.get_messages(wid, "you are a helpful assistant")
    assert messages == [{"role": "system", "content": "you are a helpful assistant"}]


def test_get_messages_returns_a_snapshot_not_a_live_reference():
    wid = _window_id()
    messages = history.get_messages(wid, "system")
    messages.append({"role": "user", "content": "mutating the snapshot"})
    assert history.get_messages(wid, "system") == [{"role": "system", "content": "system"}]


def test_append_adds_to_existing_history():
    wid = _window_id()
    history.get_messages(wid, "system")
    history.append(wid, "user", "hello")
    history.append(wid, "assistant", "hi there")
    messages = history.get_messages(wid, "system")
    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_append_to_unknown_window_is_a_noop():
    wid = _window_id()
    history.append(wid, "user", "no session yet")
    # No exception, and no session gets silently created.
    assert history.get_messages(wid, "fresh") == [{"role": "system", "content": "fresh"}]


def test_clear_removes_the_window_history():
    wid = _window_id()
    history.get_messages(wid, "system")
    history.append(wid, "user", "hello")
    history.clear(wid)
    # Re-initializes fresh on next access.
    assert history.get_messages(wid, "new system") == [
        {"role": "system", "content": "new system"}
    ]


def test_clear_unknown_window_does_not_raise():
    history.clear(_window_id())
