"""Input area view — the typing strip at the bottom of the right column."""
from __future__ import annotations

import sublime

from .view import _LAYOUT

NAME = "SublimeAssistant Input"


def get_or_create(window: sublime.Window) -> sublime.View | None:
    """Return the existing input view or create it in group 2 (bottom-right)."""
    if not window:
        return None

    for view in window.views():
        if view.name() == NAME:
            return view

    if window.num_groups() < 3:
        window.set_layout(_LAYOUT)

    window.focus_group(2)
    view = window.new_file()
    view.set_name(NAME)
    view.set_scratch(True)
    view.settings().set("word_wrap", True)
    view.settings().set("line_numbers", False)
    view.settings().set("gutter", False)
    view.settings().set("sublime_assistant_input", True)

    return view
