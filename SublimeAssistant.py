"""SublimeAssistant – AI coding assistant for Sublime Text."""
from __future__ import annotations

import importlib
import itertools
import json
import os
import sys
import threading
import time

import sublime
import sublime_plugin

from .assistant import api, code_extractor, context, file_finder, history, input_view, summarizer
from .assistant import diff_view as diff_mgr
from .assistant import view as chat_view

# block_id -> (code_content, filepath | None, selection_region | None)
# selection_region is [start_line, end_line] (0-indexed, exclusive end)
_pending_blocks: dict[str, tuple[str, str | None, list[int] | None]] = {}
_block_counter = itertools.count()

# window_id -> (directory, timestamp, cached_summary) — refreshed every interval
_summary_state: dict[int, tuple[str, float, str]] = {}

_DEFAULT_SUMMARY_INTERVAL = 1800  # seconds (30 minutes)
_ENRICH_MAX_FILE_CHARS = 3000  # chars of each file sent to LLM for description
_SUMMARY_MODEL_OPENAI = "mistral-small-latest"  # fast model for summarization on non-Claude backends


def _find_git_root(path: str) -> str:
    """Walk up from path until a .git directory is found; return that directory or path itself."""
    current = path
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return path
        current = parent


def _get_active_dir(window: sublime.Window) -> str | None:
    """Return the directory to summarize: active file's dir, or first project folder."""
    editor = window.active_view_in_group(0)
    if editor:
        fp = editor.file_name()
        if fp:
            return os.path.dirname(fp)
    folders = window.folders()
    return folders[0] if folders else None


def _enrich_summary(win_id: int, target_dir: str, file_contents: dict[str, str]) -> None:
    """Blocking: call the LLM to generate per-file descriptions, then update _summary_state.""" 
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    url, api_key, model, _, backend = _get_api_config(settings)
    if backend != "claude":
        model = _SUMMARY_MODEL_OPENAI
    print(f"[SA] Enriching {len(file_contents)} files with model={model}...")
    request_timeout = max(1, int(settings.get("request_timeout") or 120))

    file_blocks = []
    for rel, content in file_contents.items():
        print(f"[SA]   summarizing: {rel}")
        file_blocks.append(f"--- {rel.replace(os.sep, '/')} ---\n{content[:_ENRICH_MAX_FILE_CHARS]}")
    prompt = (
        "You are given files from a software project. "
        "For each file write exactly 2-3 sentences describing its purpose. "
        "Include a list of classes and functions found in the file. "
        "Reply ONLY with lines in the exact format (one per file, no blank lines between):\n"
        "<filename>: <description>\n\n"
        "Files:\n" + "\n\n".join(file_blocks)
    )

    client = _make_client(url, api_key, model, request_timeout, backend)
    reply, success = client.call([{"role": "user", "content": prompt}])

    if not success:
        print(f"[SA] summary enrichment failed: {reply[:120]}")
        return

    print(f"[SA] enrichment reply ({len(reply)} chars): {reply[:300]}")

    descriptions: dict[str, str] = {}
    for line in reply.splitlines():
        if ": " in line:
            fname, desc = line.split(": ", 1)
            descriptions[fname.strip().replace(os.sep, '/')] = desc.strip()

    # Build basename → rel_path map for fallback matching
    basename_to_rel: dict[str, str] = {
        os.path.basename(rel): rel.replace(os.sep, '/')
        for rel in file_contents
    }

    enriched_lines = [f"# {os.path.basename(target_dir)}/"]
    for rel in file_contents:
        norm = rel.replace(os.sep, '/')
        desc = descriptions.get(norm, "")
        if not desc:
            # Fallback: match by basename alone (LLM may omit subdirectory)
            desc = descriptions.get(os.path.basename(norm), "")
        enriched_lines.append(f"{norm}: {desc}" if desc else norm)

    enriched = "--- DIRECTORY SUMMARY ---\n" + "\n".join(enriched_lines)

    state = _summary_state.get(win_id)
    if state and state[0] == target_dir:
        _summary_state[win_id] = (target_dir, state[1], enriched)
        print(f"[SA] dir-summary enriched: {len(descriptions)}/{len(file_contents)} files described")
        summary_file = os.path.join(target_dir, ".sublime_assistant_summary.md")
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(enriched)
            print(f"[SA] summary cached to {summary_file}")
            sublime.set_timeout(lambda: sublime.active_window().run_command("refresh_folder_list"), 0)
        except Exception as e:
            print(f"[SA] could not write summary cache: {e}")


def _auto_summary_context(window: sublime.Window) -> str:
    """Return the cached directory summary, re-crawling only when the interval has elapsed."""
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    interval = int(settings.get("summary_interval") or _DEFAULT_SUMMARY_INTERVAL)

    target_dir = _get_active_dir(window)
    if not target_dir:
        return ""

    git_root = _find_git_root(target_dir)
    win_id = window.id()
    now = time.time()
    last_dir, last_time, cached = _summary_state.get(win_id, ("", 0.0, ""))

    if git_root == last_dir and (now - last_time) < interval:
        return cached

    # Try persistent file cache before crawling
    summary_file = os.path.join(git_root, ".sublime_assistant_summary.md")
    if os.path.isfile(summary_file):
        try:
            with open(summary_file, encoding="utf-8") as f:
                cached = f.read()
            _summary_state[win_id] = (git_root, now, cached)
            print(f"[SA] loaded summary from {summary_file}")
            return cached
        except Exception:
            pass

    raw, file_contents = summarizer.crawl(git_root)
    cached = f"--- DIRECTORY SUMMARY ---\n{raw}"
    _summary_state[win_id] = (git_root, now, cached)
    if file_contents:
        threading.Thread(
            target=_enrich_summary,
            args=(win_id, git_root, file_contents),
            daemon=True,
        ).start()

    return cached


def plugin_unloaded() -> None:
    """Remove cached submodule entries so they are freshly imported on reload."""
    prefix = __package__ + ".assistant"
    for key in [k for k in sys.modules if k.startswith(prefix)]:
        del sys.modules[key]


def _load_default_presets() -> dict:
    """Read preset defaults from the package .sublime-settings file.

    Sublime Text does not deep-merge nested objects, so a User settings file that
    contains only {"presets": {"claude": {"api_key": "..."}}} would lose the
    package-defined backend/model for that preset.  Reading the package file
    directly lets us do the merge ourselves.
    """
    pkg_path = os.path.join(
        sublime.packages_path(), "SublimeAssistant", "SublimeAssistant.sublime-settings"
    )
    try:
        with open(pkg_path, encoding="utf-8") as f:
            return (json.load(f).get("presets") or {})
    except Exception:
        return {}


def _get_api_config(settings: sublime.Settings) -> tuple[str, str, str, str, str]:
    """Resolve api_url, api_key, model, system_prompt, backend from active_preset or top-level.

    Per-preset values are deep-merged: package defaults supply backend/model/url,
    user settings supply api_key (and can override anything else).
    """
    default_presets = _load_default_presets()
    user_presets: dict = settings.get("presets") or {}

    all_names = set(default_presets) | set(user_presets)
    merged_presets = {
        name: {**(default_presets.get(name) or {}), **(user_presets.get(name) or {})}
        for name in all_names
    }

    active = settings.get("active_preset")
    p = merged_presets.get(active) if active else None

    def _get(key: str, default: str) -> str:
        if p and key in p and p[key] is not None:
            return str(p[key])
        return settings.get(key, default)

    url = _get("api_url", "http://localhost:11434/v1/chat/completions")
    api_key = _get("api_key", "")
    model = _get("model", "devstral-small-2:latest")
    system_prompt = settings.get("system_prompt", "You are a helpful coding assistant.")
    backend = _get("backend", "openai")
    return url, api_key, model, system_prompt, backend


def _make_client(url: str, api_key: str, model: str, timeout: int, backend: str) -> api.APIClient:
    """Instantiate the correct APIClient subclass for the given backend."""
    if backend == "claude":
        return api.ClaudeClient(api_key, model, timeout_seconds=timeout)
    return api.OpenAIClient(url, api_key, model, timeout_seconds=timeout)


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
    url, api_key, model, system_prompt, backend = _get_api_config(settings)

    win_id = window.id()
    messages = history.get_messages(win_id, system_prompt) + [{"role": "user", "content": full_content}]
    tools = [api.FETCH_URL_TOOL, api.READ_FILE_TOOL]
    request_timeout = max(1, int(settings.get("request_timeout") or 120))

    file_requests: list[str] = []
    url_requests: list[str] = []

    def on_read_file(filename: str) -> str | None:
        file_requests.append(filename)
        return file_finder.find(window, filename)

    def on_tool_call(tool_name: str, url_or_args: str) -> None:
        if tool_name == "fetch_url":
            url_requests.append(url_or_args)
            display = url_or_args if len(url_or_args) <= 60 else url_or_args[:57] + "..."
            status = f"> _Fetching {display}..._"
        elif tool_name == "read_file":
            status = f"> _Reading {url_or_args}..._"
        else:
            return
        sublime.set_timeout(
            lambda: panel.run_command("sublime_assistant_update_placeholder", {"text": status}),
            0,
        )

    client = _make_client(url, api_key, model, request_timeout, backend)
    reply, success = client.call(messages, tools=tools, on_tool_call=on_tool_call, on_read_file=on_read_file)

    tool_log_parts: list[str] = []
    if file_requests:
        tool_log_parts.append("read " + ", ".join(f"`{f}`" for f in file_requests))
    if url_requests:
        tool_log_parts.append("fetched " + ", ".join(f"`{u}`" for u in url_requests))
    if tool_log_parts:
        reply = reply + "\n\n> **Tool calls:** " + " · ".join(tool_log_parts)

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
    pending_summary = window.settings().get("sa_pending_summary") or ""
    if pending_summary:
        window.settings().erase("sa_pending_summary")

    auto_summary = _auto_summary_context(window)
    extra = "\n\n".join(filter(None, [auto_summary, pending_summary]))

    result = context.build(window, query, active_file, active_filename, selection, extra_context=extra)
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    preset = settings.get("active_preset") or ""
    _, _, model, _, _ = _get_api_config(settings)
    panel.run_command("sublime_assistant_append", {
        "text": chat_view.user_block(query, result.hints) + chat_view.assistant_header(preset, model)
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
    """
    Handle navigation to an "Apply" phantom link in the chat panel.

    When a user clicks an "Apply" link next to a code block in the assistant's response,
    this function locates or opens the target file (if specified) or uses the active view,
    then opens a diff view showing the proposed changes.

    Args:
        href: The hyperlink href string, expected to start with "apply:" followed by a block ID.
        window: The Sublime Text window where the navigation occurred.

    Returns:
        None: The diff view is opened asynchronously; no return value.
    """
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


class SublimeAssistantUsePresetCommand(sublime_plugin.WindowCommand):
    """Switch the active API preset (e.g. local vs mistral)."""

    def run(self, preset: str = "") -> None:
        if not preset:
            return
        settings = sublime.load_settings("SublimeAssistant.sublime-settings")
        presets = settings.get("presets") or {}
        if preset not in presets:
            sublime.status_message(f"SublimeAssistant: unknown preset «{preset}»")
            return
        settings.set("active_preset", preset)
        sublime.save_settings("SublimeAssistant.sublime-settings")
        sublime.status_message(f"SublimeAssistant: using preset «{preset}»")


class SublimeAssistantSetMistralApiKeyCommand(sublime_plugin.WindowCommand):
    """Prompt for the Mistral API key and save it to User settings."""

    def run(self) -> None:
        settings = sublime.load_settings("SublimeAssistant.sublime-settings")
        presets = dict(settings.get("presets") or {})
        mistral = dict(presets.get("mistral") or {})
        initial = mistral.get("api_key") or ""

        def on_done(key: str) -> None:
            key = key.strip()
            mistral["api_key"] = key
            presets["mistral"] = mistral
            settings.set("presets", presets)
            sublime.save_settings("SublimeAssistant.sublime-settings")
            sublime.status_message("SublimeAssistant: Mistral API key saved to User settings.")

        self.window.show_input_panel(
            "Mistral API key:",
            initial,
            on_done,
            None,
            None,
        )


class SublimeAssistantSetClaudeApiKeyCommand(sublime_plugin.WindowCommand):
    """Prompt for the Claude API key and save it to User settings."""

    def run(self) -> None:
        settings = sublime.load_settings("SublimeAssistant.sublime-settings")
        presets = dict(settings.get("presets") or {})
        claude = dict(presets.get("claude") or {})
        initial = claude.get("api_key") or ""

        def on_done(key: str) -> None:
            key = key.strip()
            claude["api_key"] = key
            presets["claude"] = claude
            settings.set("presets", presets)
            sublime.save_settings("SublimeAssistant.sublime-settings")
            sublime.status_message("SublimeAssistant: Claude API key saved to User settings.")

        self.window.show_input_panel(
            "Claude API key:",
            initial,
            on_done,
            None,
            None,
        )


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
    """Append text to the chat panel and ensure it is visible."""

    def run(self, edit, text: str):
        """Insert text at the end of the view and scroll to show it.

        Args:
            edit: The edit object provided by Sublime Text.
            text: The text to append to the chat panel.
        """
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), text)
        self.view.show(self.view.size())
        self.view.set_read_only(True)


class SublimeAssistantReplacePlaceholderCommand(sublime_plugin.TextCommand):
    """Replace a placeholder text in the chat panel with assistant response and add application phantoms.

    This command handles two scenarios:
    1. Replaces the placeholder at the end of the view with the assistant's response text
    2. If no placeholder is found, simply appends the response text

    The command also triggers the addition of 'Apply' phantoms for any code blocks
    in the response, allowing users to apply the suggested changes to their code.

    Args:
        edit: The edit object provided by Sublime Text command system.
        text: The response text from the assistant to be inserted.
        selection_region: Optional [start_line, end_line] region for code placement precision.

    Returns:
        None: This command directly modifies the view through the edit object.
    """

    def run(self, edit, text: str, selection_region: list[int] | None = None):
        """Execute the replacement of placeholder with assistant response text."""
        self.view.set_read_only(False)
        file_size = self.view.size()
        region = chat_view.find_placeholder_region(self.view)
        if region is not None:
            insert_start = region.begin()
            self.view.replace(edit, region, text)
        else:
            insert_start = file_size
            self.view.insert(edit, file_size, text)

        _add_apply_phantoms(self.view, insert_start, text, selection_region)
        self.view.show(self.view.size())
        self.view.set_read_only(True)


class SublimeAssistantUpdatePlaceholderCommand(sublime_plugin.TextCommand):
    """Update the status placeholder (e.g. to show 'Fetching &lt;url&gt;...') during tool calls."""

    def run(self, edit, text: str):
        self.view.set_read_only(False)
        region = chat_view.find_placeholder_region(self.view)
        if region is not None:
            self.view.replace(edit, region, text)
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


class SublimeAssistantSelectModelCommand(sublime_plugin.WindowCommand):
    """Fetch available models from the active preset and let the user pick one."""

    def run(self) -> None:
        settings = sublime.load_settings("SublimeAssistant.sublime-settings")
        url, api_key, current_model, _, backend = _get_api_config(settings)
        sublime.status_message("SublimeAssistant: fetching models...")

        def fetch():
            client = _make_client(url, api_key, current_model, 10, backend)
            models, err = client.fetch_models()
            sublime.set_timeout(lambda: self._show(settings, models, err, current_model), 0)

        threading.Thread(target=fetch, daemon=True).start()

    def _show(self, settings: sublime.Settings, models: list[str], err: str, current_model: str) -> None:
        if err:
            sublime.error_message(f"SublimeAssistant: {err}")
            return

        try:
            selected_index = models.index(current_model)
        except ValueError:
            selected_index = 0

        def on_done(idx: int) -> None:
            if idx == -1:
                return
            chosen = models[idx]
            presets = dict(settings.get("presets") or {})
            active = settings.get("active_preset")
            if active and active in presets:
                preset = dict(presets[active])
                preset["model"] = chosen
                presets[active] = preset
                settings.set("presets", presets)
            else:
                settings.set("model", chosen)
            sublime.save_settings("SublimeAssistant.sublime-settings")
            sublime.status_message(f"SublimeAssistant: model set to «{chosen}»")

        self.window.show_quick_panel(models, on_done, selected_index=selected_index)


class SublimeAssistantSummarizeDirectoryCommand(sublime_plugin.WindowCommand):
    """Force-refresh the directory summary with LLM-generated descriptions."""

    def run(self) -> None:
        target_dir = _get_active_dir(self.window)
        if not target_dir:
            sublime.status_message("SublimeAssistant: no directory to summarize")
            return

        git_root = _find_git_root(target_dir)
        win_id = self.window.id()
        _summary_state.pop(win_id, None)
        # Remove stale file cache so enrichment rewrites it
        summary_file = os.path.join(git_root, ".sublime_assistant_summary.md")
        try:
            os.remove(summary_file)
        except OSError:
            pass
        sublime.status_message(f"SublimeAssistant: crawling {os.path.basename(git_root)}/...")

        def crawl_and_enrich() -> None:
            raw, file_contents = summarizer.crawl(git_root)
            cached = f"--- DIRECTORY SUMMARY ---\n{raw}"
            _summary_state[win_id] = (git_root, time.time(), cached)
            if file_contents:
                sublime.set_timeout(
                    lambda: sublime.status_message(
                        f"SublimeAssistant: enriching {len(file_contents)} files..."
                    ), 0,
                )
                _enrich_summary(win_id, git_root, file_contents)
            sublime.set_timeout(
                lambda: sublime.status_message(
                    f"SublimeAssistant: summary ready for {os.path.basename(git_root)}/"
                ), 0,
            )
            # Print the summary to the console
            print(cached)

        threading.Thread(target=crawl_and_enrich, daemon=True).start()


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
