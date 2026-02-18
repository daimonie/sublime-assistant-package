"""Chat panel UI helpers and constants."""
from __future__ import annotations

import sublime

NAME = "SublimeAssistant Chat"
PLACEHOLDER = "> _Thinking..._"

_LAYOUT = {
    "cols": [0.0, 0.7, 1.0],
    "rows": [0.0, 0.82, 1.0],
    "cells": [
        [0, 0, 1, 2],   # group 0: editor (full left column)
        [1, 0, 2, 1],   # group 1: chat view
        [1, 1, 2, 2],   # group 2: input area
    ],
}


def get_or_create(window: sublime.Window) -> sublime.View | None:
    """Return existing chat panel or open a new one in a right split."""
    if not window:
        return None

    for view in window.views():
        if view.name() == NAME:
            window.focus_view(view)
            return view

    if window.num_groups() < 3:
        window.set_layout(_LAYOUT)

    window.focus_group(1)
    view = window.new_file()
    view.set_name(NAME)
    view.set_scratch(True)
    view.settings().set("word_wrap", True)
    view.settings().set("line_numbers", False)
    view.settings().set("gutter", False)
    try:
        view.assign_syntax("Packages/Markdown/Markdown.sublime-syntax")
    except Exception:
        pass

    return view


def user_block(query: str, hints: list[str] | None = None) -> str:
    block = f"\n---\n\n## 👤 User\n{query}\n"
    if hints:
        items = "\n".join(f"- {h}" for h in hints)
        block += f"\nSending:\n{items}\n"
    return block + "\n"


def assistant_header() -> str:
    return f"## 🤖 Assistant\n{PLACEHOLDER}"
