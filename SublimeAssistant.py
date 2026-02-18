"""SublimeAssistant – AI coding assistant for Sublime Text."""
from __future__ import annotations

import importlib
import itertools
import os
import sys
import threading

import sublime
import sublime_plugin

from .assistant import api, code_extractor, context, history, input_view
from .assistant import diff_view as diff_mgr
from .assistant import view as chat_view

# block_id -> (code_content, filepath | None, selection_region | None)
# selection_region is [start_line, end_line] (0-indexed, exclusive end)
_pending_blocks: dict[str, tuple[str, str | None, list[int] | None]] = {}
_block_counter = itertools.count()


def plugin_unloaded() -> None:
    """Remove cached submodule entries so they are freshly imported on reload."""
    prefix = __package__ + ".assistant"
    for key in [k for k in sys.modules if k.startswith(prefix)]:
        del sys.modules[key]


def _call_api(
    window: sublime.Window,
    panel: sublime.View,
    full_content: str,
    selection_region: list[int] | None,
) -> None:
    """
    Call the API with the constructed messages and handle the response.

    This function retrieves the API settings, constructs the message history including
    the current user query, sends it to the API, and processes the response by:
    - Updating the message history
    - Replacing the placeholder in the chat panel with the API response
    - Adding apply phantoms for any fenced code blocks in the response

    Args:
        window: The Sublime Text window containing the chat panel.
        panel: The chat panel view where responses will be displayed.
        full_content: The complete content to send to the API (query + context).
        selection_region: Optional line range [start, end] of the current selection,
                         used for precise code block application.

    Returns:
        None: All operations are performed asynchronously via callbacks.
    """
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    url = settings.get("api_url", "http://localhost:11434/v1/chat/completions")
    model = settings.get("model", "devstral-small-2:latest")
    system_prompt = settings.get("system_prompt", "You are a helpful coding assistant.")
    api_key = settings.get("api_key", "")

    win_id = window.id()
    messages = history.get_messages(win_id, system_prompt) + [{"role": "user", "content": full_content}]

    reply, success = api.call(url, api_key, model, messages)

    if success:
        history.append(win_id, "user", full_content)
        history.append(win_id, "assistant", reply)

    sublime.set_timeout(
        lambda: panel.run_command("sublime_assistant_replace_placeholder", {
            "text": reply + "\n",
            "selection_region": selection_region,
        }),
        0,
    )


def _submit_query(
    window: sublime.Window,
    panel: sublime.View,
    query: str,
    active_file: str,
    active_filename: str,
    selection: str,
    selection_region: list[int] | None,
) -> None:
    """
    Submit the user query to the assistant and initiate the API call workflow.

    This function builds context and constructs the chat panel message block,
    then starts an asynchronous thread to call the API and handle the response.

    Args:
        window: The Sublime Text window where the chat is occurring.
        panel: The chat panel view where messages are displayed.
        query: The user-submitted question or instruction.
        active_file: The full content of the currently active editor file.
        active_filename: The filename (or "Untitled") of the active file.
        selection: The text currently selected in the active editor.
        selection_region: Optional [start_line, end_line] for precise code applying.

    Returns:
        None: Operations are performed asynchronously via callbacks.
    """
    result = context.build(window, query, active_file, active_filename, selection)
    panel.run_command("sublime_assistant_append", {
        "text": chat_view.user_block(query, result.hints) + chat_view.assistant_header()
    })
    threading.Thread(
        target=_call_api,
        args=(window, panel, result.content, selection_region),
        daemon=True,
    ).start()


def _add_apply_phantoms(
    view: sublime.View,
    insert_start: int,
    reply_text: str,
    selection_region: list[int] | None,
) -> None:
    """Add an Apply phantom after each fenced code block in the just-inserted reply."""
    window = view.window()
    if not window:
        return

    for block in code_extractor.extract(reply_text):
        block_id = f"block_{next(_block_counter)}"
        _pending_blocks[block_id] = (block.content, block.filepath, selection_region)

        pos = insert_start + block.end_pos
        html = f'<body id="sa_apply"><a href="apply:{block_id}">Apply</a></body>'
        view.add_phantom(
            "assistant_apply",
            sublime.Region(pos, pos),
            html,
            sublime.LAYOUT_BLOCK,
            on_navigate=lambda href, w=window: _on_apply_navigate(href, w),
        )


def _on_apply_navigate(href: str, window: sublime.Window) -> None:
    if not href.startswith("apply:"):
        return

    block_id = href[6:]
    entry = _pending_blocks.get(block_id)
    if not entry:
        return

    code, filepath, sel_region = entry

    if filepath is not None:
        target = next(
            (v for v in window.views() if v.file_name() and
             os.path.basename(v.file_name()) == os.path.basename(filepath)),
            None,
        )
        if target is None and os.path.isfile(filepath):
            target = window.open_file(filepath)

        diff_id = f"diff_{block_id}"
        if target:
            diff_mgr.open_diff(window, diff_id, target, code, hint_region=None)
        else:
            diff_mgr.open_new_file_preview(window, diff_id, filepath, code)
    else:
        target = window.active_view_in_group(0)
        if target:
            # Use the selection region as a precise hint if available
            hint = tuple(sel_region) if sel_region else None
            diff_mgr.open_diff(window, f"diff_{block_id}", target, code, hint_region=hint)


# ── Commands ──────────────────────────────────────────────────────────────────

class SublimeAssistantAskCommand(sublime_plugin.TextCommand):
    """Open/focus the input area, creating the chat pane if needed."""

    def run(self, edit):
        window = self.view.window()
        if not window:
            return
        chat_view.get_or_create(window)
        inp = input_view.get_or_create(window)
        if inp:
            window.focus_view(inp)


class SublimeAssistantSubmitCommand(sublime_plugin.TextCommand):
    """Submit the input area content as a query (Ctrl+Enter in input view)."""

    def run(self, edit):
        window = self.view.window()
        query = self.view.substr(sublime.Region(0, self.view.size())).strip()
        if not window or not query:
            return

        self.view.replace(edit, sublime.Region(0, self.view.size()), "")

        editor = window.active_view_in_group(0)
        if editor:
            active_file     = editor.substr(sublime.Region(0, editor.size()))
            active_filename = editor.file_name() or "Untitled"
            non_empty       = [r for r in editor.sel() if not r.empty()]
            selection       = "\n".join(editor.substr(r) for r in non_empty)
            if non_empty:
                start_line = editor.rowcol(non_empty[0].begin())[0]
                end_line   = editor.rowcol(non_empty[-1].end())[0] + 1
                selection_region: list[int] | None = [start_line, end_line]
            else:
                selection_region = None
        else:
            active_file, active_filename, selection, selection_region = "", "Untitled", "", None

        panel = chat_view.get_or_create(window)
        if panel:
            _submit_query(window, panel, query, active_file, active_filename,
                          selection, selection_region)


class SublimeAssistantAppendCommand(sublime_plugin.TextCommand):
    def run(self, edit, text: str):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), text)
        self.view.show(self.view.size())
        self.view.set_read_only(True)


class SublimeAssistantReplacePlaceholderCommand(sublime_plugin.TextCommand):
    def run(self, edit, text: str, selection_region: list[int] | None = None):
        self.view.set_read_only(False)
        file_size = self.view.size()
        region = sublime.Region(file_size - len(chat_view.PLACEHOLDER), file_size)

        if self.view.substr(region) == chat_view.PLACEHOLDER:
            insert_start = region.begin()
            self.view.replace(edit, region, text)
        else:
            insert_start = file_size
            self.view.insert(edit, file_size, text)

        _add_apply_phantoms(self.view, insert_start, text, selection_region)
        self.view.show(self.view.size())
        self.view.set_read_only(True)


class SublimeAssistantApplyCodeCommand(sublime_plugin.TextCommand):
    """Replace the entire content of the target view with proposed code."""

    def run(self, edit, code: str):
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), code)


class SublimeAssistantApplySnippetCommand(sublime_plugin.TextCommand):
    """Replace a specific line range in the target view with a code snippet."""

    def run(self, edit, code: str, start_line: int, end_line: int):
        self.view.set_read_only(False)
        last_row = self.view.rowcol(self.view.size())[0]
        start_pt = self.view.text_point(start_line, 0)
        end_pt   = (self.view.size() if end_line > last_row
                    else self.view.text_point(end_line, 0))
        self.view.replace(edit, sublime.Region(start_pt, end_pt), code.rstrip('\n') + '\n')


class SublimeAssistantCreateFileCommand(sublime_plugin.WindowCommand):
    """Write a new file to disk and open it in group 0."""

    def run(self, filepath: str, code: str):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as e:
            sublime.error_message(f"SublimeAssistant: could not create file\n{e}")
            return
        self.window.focus_group(0)
        self.window.open_file(filepath)


class SublimeAssistantReloadListener(sublime_plugin.EventListener):
    """Auto-reload assistant submodules when their source files are saved."""

    def on_post_save(self, view: sublime.View) -> None:
        file_path = view.file_name() or ""
        pkg_path = os.path.dirname(os.path.abspath(__file__))
        assistant_path = os.path.join(pkg_path, "assistant")

        if not file_path.startswith(assistant_path) or not file_path.endswith(".py"):
            return

        rel = os.path.relpath(file_path, pkg_path)
        mod_name = __package__ + "." + rel.replace(os.sep, ".")[:-3]

        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
            sublime.status_message(f"SublimeAssistant: reloaded {mod_name}")
