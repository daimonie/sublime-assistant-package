"""Minimal fake sublime.Window / sublime.View implementations shared across
tests that need more than the bare stub conftest.py installs for `sublime`.

Only implements the subset of the real API that assistant/*.py actually
calls — not a general-purpose Sublime Text simulator.
"""
from __future__ import annotations

import sublime


class FakeView:
    def __init__(
        self,
        file_name: str | None = None,
        name: str = "",
        content: str = "",
        view_id: int = 0,
    ) -> None:
        self._file_name = file_name
        self._name = name
        self._content = content
        self._id = view_id
        self._window: "FakeWindow | None" = None
        self._scratch = False
        self._read_only = False
        self._settings = FakeSettings()
        self._syntax = ""
        self.phantoms: list[tuple] = []
        self.closed = False

    def file_name(self) -> str | None:
        return self._file_name

    def name(self) -> str:
        return self._name

    def set_name(self, name: str) -> None:
        self._name = name

    def substr(self, region: sublime.Region) -> str:
        return self._content[region.a:region.b]

    def size(self) -> int:
        return len(self._content)

    def insert(self, edit, point: int, text: str) -> int:
        self._content = self._content[:point] + text + self._content[point:]
        return len(text)

    def replace(self, edit, region: sublime.Region, text: str) -> None:
        self._content = self._content[:region.a] + text + self._content[region.b:]

    def erase(self, edit, region: sublime.Region) -> None:
        self._content = self._content[:region.a] + self._content[region.b:]

    def id(self) -> int:
        return self._id

    def window(self) -> "FakeWindow | None":
        return self._window

    def set_scratch(self, value: bool) -> None:
        self._scratch = value

    def set_read_only(self, value: bool) -> None:
        self._read_only = value

    def settings(self) -> "FakeSettings":
        return self._settings

    def assign_syntax(self, syntax: str) -> None:
        self._syntax = syntax

    def run_command(self, name: str, args: dict | None = None) -> None:
        pass

    def add_phantom(self, key, region, html, layout, on_navigate=None) -> None:
        self.phantoms.append((key, region, html, layout, on_navigate))

    def sel(self):
        return []

    def show(self, point) -> None:
        pass

    def rowcol(self, point: int) -> tuple[int, int]:
        text = self._content[:point]
        row = text.count("\n")
        col = len(text) - (text.rfind("\n") + 1)
        return (row, col)

    def text_point(self, row: int, col: int) -> int:
        lines = self._content.splitlines(keepends=True)
        return sum(len(l) for l in lines[:row]) + col

    def close(self) -> None:
        self.closed = True


class FakeSettings:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value) -> None:
        self._data[key] = value

    def erase(self, key) -> None:
        self._data.pop(key, None)


class FakeWindow:
    def __init__(
        self,
        views: list[FakeView] | None = None,
        folders: list[str] | None = None,
        active_view: FakeView | None = None,
        window_id: int = 1,
    ) -> None:
        self._views = views or []
        self._folders = folders or []
        self._active = active_view
        self._id = window_id
        self._settings = FakeSettings()
        for v in self._views:
            v._window = self

    def active_view_in_group(self, group: int) -> FakeView | None:
        return self._active

    def views(self) -> list[FakeView]:
        return self._views

    def folders(self) -> list[str]:
        return self._folders

    def id(self) -> int:
        return self._id

    def settings(self) -> FakeSettings:
        return self._settings

    def focus_view(self, view: FakeView) -> None:
        pass

    def focus_group(self, group: int) -> None:
        pass

    def num_groups(self) -> int:
        return 1

    def set_layout(self, layout) -> None:
        pass

    def new_file(self) -> FakeView:
        v = FakeView()
        v._window = self
        self._views.append(v)
        return v

    def open_file(self, filepath: str) -> FakeView:
        v = FakeView(file_name=filepath)
        v._window = self
        self._views.append(v)
        return v

    def run_command(self, name: str, args: dict | None = None) -> None:
        pass
