"""SublimeAssistant – AI coding assistant for Sublime Text."""
from __future__ import annotations

import difflib
import importlib
import itertools
import json
import os
import sys
import threading
import time

import sublime
import sublime_plugin

from .assistant import api, code_extractor, context, file_finder, git, history, input_view, loop_runner, summarizer
from .assistant import project_rules, skills, slash_commands
from .assistant import diff_view as diff_mgr
from .assistant import view as chat_view

# block_id -> (code_content, filepath | None, selection_region | None)
# selection_region is [start_line, end_line] (0-indexed, exclusive end)
_pending_blocks: dict[str, tuple[str, str | None, list[int] | None]] = {}
_block_counter = itertools.count()

# window_id -> (directory, timestamp, cached_summary) — refreshed every interval
_summary_state: dict[int, tuple[str, float, str]] = {}

# view_id -> PhantomSet for the input area hint
_hint_sets: dict[int, sublime.PhantomSet] = {}

# block_id -> PhantomSet for inline editor suggestion phantom
_inline_phantom_sets: dict[str, sublime.PhantomSet] = {}

# window_id -> cancel event for the in-flight request (single call or /loop)
_active_requests: dict[int, threading.Event] = {}

# window_id -> current goal text, set by /goal and consumed/refreshed by /loop
_goal_state: dict[int, str] = {}

_HINT_HTML = (
    '<body id="sa-hint"><style>'
    'body { margin: 0; padding: 2px 0;'
    ' color: color(var(--foreground) alpha(0.35)); font-style: italic; }'
    '</style>Ask anything… <i>Ctrl+Enter</i> to send</body>'
)

# Shown in the (empty) input area in place of _HINT_HTML while a request is in flight.
# Anchored to the input pane rather than the chat panel so it doesn't scroll out of view
# as new content streams in.
_STOP_HINT_HTML = (
    '<body id="sa-hint"><style>'
    'body { margin: 0; padding: 2px 0; }'
    'a { color: #f85149; font-weight: bold; text-decoration: none; }'
    '.hint { color: color(var(--foreground) alpha(0.35)); font-style: italic; }'
    '</style><a href="interrupt:">&#9209; Stop generating</a> '
    '<span class="hint"><i>Ctrl+C</i> also works</span></body>'
)

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


def _find_input_view(window: sublime.Window) -> sublime.View | None:
    for view in window.views():
        if view.name() == input_view.NAME:
            return view
    return None


def _start_streaming(window: sublime.Window) -> threading.Event:
    """Register a new in-flight request for `window`, mark the input view as streaming
    (drives the Ctrl+C-in-input-view interrupt keybinding), and swap its idle hint for a
    clickable Stop control. The input pane is used rather than the chat panel because it
    stays put — a phantom anchored in the chat panel scrolls out of view as the reply
    streams in and the panel auto-scrolls to keep up."""
    event = threading.Event()
    _active_requests[window.id()] = event
    inp = _find_input_view(window)
    if inp:
        inp.settings().set("sa_streaming", True)
        inp.run_command("sublime_assistant_update_input_hint", {"show": True})
    return event


def _finish_streaming(window: sublime.Window) -> None:
    """Tear down the in-flight-request bookkeeping for `window`, restoring the input view's
    idle hint. Always run this once the request (or the whole /loop run) has finished,
    cancelled or not."""
    _active_requests.pop(window.id(), None)
    inp = _find_input_view(window)
    if inp:
        inp.settings().erase("sa_streaming")
        inp.run_command("sublime_assistant_update_input_hint", {"show": True})


def _append_and_wait(panel: sublime.View, text: str) -> None:
    """Append `text` to the chat panel and block until it has actually run.

    Called from background threads. panel.run_command must be marshaled onto the main
    thread via sublime.set_timeout; the streaming on_chunk callback used by _call_api_core
    needs the placeholder text to already be in the buffer before the request starts, so
    this can't be fire-and-forget the way most other UI updates in this file are.
    """
    done = threading.Event()

    def _do() -> None:
        panel.run_command("sublime_assistant_append", {"text": text})
        done.set()

    sublime.set_timeout(_do, 0)
    done.wait(5)


def _call_api_core(
    window: sublime.Window,
    panel: sublime.View,
    full_content: str,
    git_root: str,
    cancel_event: threading.Event | None,
) -> tuple[str, bool]:
    """
    Send one turn to the API (with tool-call support and streaming) and append it to history.

    This holds all the request/tool-call plumbing that used to live directly in _call_api.
    It's shared by the normal single-turn flow (_call_api) and the /loop iteration driver
    (_run_loop), so that plumbing is defined exactly once. Returns (reply_text, success);
    the caller decides how/where to post the reply in the UI.
    """
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    url, api_key, model, system_prompt, backend = _get_api_config(settings)

    win_id = window.id()

    # Prepend project rules (AGENTS.md) to system prompt once per window session
    rules = project_rules.load(git_root, win_id) if git_root else ""
    if rules:
        system_prompt = rules + "\n\n---\n\n" + system_prompt

    # Prepend the Agent Skills index (name + description only; full bodies load on
    # demand via the load_skill tool) once per window session
    skills_index = skills.build_index(git_root, win_id) if git_root else ""
    if skills_index:
        system_prompt = skills_index + "\n\n---\n\n" + system_prompt

    messages = history.get_messages(win_id, system_prompt) + [{"role": "user", "content": full_content}]
    request_timeout = max(1, int(settings.get("request_timeout") or 120))

    file_requests: list[str] = []
    url_requests: list[str] = []

    def on_read_file(filename: str) -> str | None:
        file_requests.append(filename)
        return file_finder.find(window, filename)

    # F6: lazy directory summary tool callbacks
    fetched_files: set[str] = set()

    def on_list_files() -> str:
        state = _summary_state.get(win_id)
        if state:
            return state[2]
        summary_file = os.path.join(git_root, ".sublime_assistant_summary.md") if git_root else ""
        if summary_file and os.path.isfile(summary_file):
            try:
                text = open(summary_file, encoding="utf-8").read()
                _summary_state[win_id] = (git_root, time.time(), text)
                return text
            except Exception:
                pass
        if not git_root:
            return "(no project directory found)"
        raw, file_contents = summarizer.crawl(git_root)
        cached = f"--- DIRECTORY SUMMARY ---\n{raw}"
        _summary_state[win_id] = (git_root, time.time(), cached)
        if file_contents:
            threading.Thread(
                target=_enrich_summary,
                args=(win_id, git_root, file_contents),
                daemon=True,
            ).start()
        return cached

    def on_get_file_summary(path: str) -> str:
        if path in fetched_files:
            return f"(content of {path} was already provided in this response)"
        content = file_finder.find(window, path)
        if content is None:
            return f"File not found: {path}"
        fetched_files.add(path)
        return content

    loaded_skills: list[str] = []

    def on_load_skill(name: str) -> str:
        loaded_skills.append(name)
        return skills.load_body(git_root, name) if git_root else f"Unknown skill: {name}"

    def on_tool_call(tool_name: str, url_or_args: str) -> None:
        if tool_name == "fetch_url":
            url_requests.append(url_or_args)
            display = url_or_args if len(url_or_args) <= 60 else url_or_args[:57] + "..."
            status = f"> _Fetching {display}..._"
        elif tool_name == "read_file":
            status = f"> _Reading {url_or_args}..._"
        elif tool_name == "list_project_files":
            status = "> _Listing project files..._"
        elif tool_name == "get_file_summary":
            status = f"> _Reading {url_or_args}..._"
        elif tool_name == "load_skill":
            status = f"> _Loading skill {url_or_args}..._"
        else:
            return
        sublime.set_timeout(
            lambda: panel.run_command("sublime_assistant_update_placeholder", {"text": status}),
            0,
        )

    tools = [api.FETCH_URL_TOOL, api.READ_FILE_TOOL,
             api.LIST_PROJECT_FILES_TOOL, api.GET_FILE_SUMMARY_TOOL, api.LOAD_SKILL_TOOL]

    def on_chunk(text: str) -> None:
        sublime.set_timeout(
            lambda t=text: panel.run_command("sublime_assistant_stream_update", {"text": t}),
            0,
        )

    client = _make_client(url, api_key, model, request_timeout, backend)
    reply, success = client.call(
        messages, tools=tools,
        on_tool_call=on_tool_call,
        on_read_file=on_read_file,
        on_list_files=on_list_files,
        on_get_file_summary=on_get_file_summary,
        on_load_skill=on_load_skill,
        on_chunk=on_chunk,
        cancel_event=cancel_event,
    )

    tool_log_parts: list[str] = []
    if file_requests:
        tool_log_parts.append("read " + ", ".join(f"`{f}`" for f in file_requests))
    if fetched_files:
        tool_log_parts.append("summarized " + ", ".join(f"`{f}`" for f in sorted(fetched_files)))
    if url_requests:
        tool_log_parts.append("fetched " + ", ".join(f"`{u}`" for u in url_requests))
    if loaded_skills:
        tool_log_parts.append("loaded skill " + ", ".join(f"`{s}`" for s in loaded_skills))
    if tool_log_parts:
        reply = reply + "\n\n> **Tool calls:** " + " · ".join(tool_log_parts)

    if success:
        history.append(win_id, "user", full_content)
        history.append(win_id, "assistant", reply)

    return reply, success


def _call_api(
    window: sublime.Window,
    panel: sublime.View,
    full_content: str,
    selection_region: list[int] | None,
    git_root: str = "",
) -> None:
    """
    Send `full_content` as one turn and post the reply into the chat panel.

    Wraps _call_api_core with the UI finalization step (replacing the placeholder,
    adding Apply phantoms) for the normal single-turn chat flow. All operations are
    performed asynchronously via callbacks.
    """
    cancel_event = _active_requests.get(window.id())
    reply, success = _call_api_core(window, panel, full_content, git_root, cancel_event)

    if cancel_event is not None and cancel_event.is_set():
        reply = reply + "\n\n_⏹ Stopped by user._"

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
    api_query: str = "",
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

    # Resolve git_root on the main thread before handing off to background thread
    target_dir = _get_active_dir(window) or ""
    git_root = _find_git_root(target_dir) if target_dir else ""

    extra = pending_summary

    effective_query = api_query or query
    result = context.build(window, effective_query, active_file, active_filename, selection, extra_context=extra)
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    preset = settings.get("active_preset") or ""
    _, _, model, _, _ = _get_api_config(settings)
    panel.run_command("sublime_assistant_append", {
        "text": chat_view.user_block(query, result.hints) + chat_view.assistant_header(preset, model)
    })
    _start_streaming(window)

    def _run() -> None:
        try:
            _call_api(window, panel, result.content, selection_region, git_root)
        finally:
            sublime.set_timeout(lambda: _finish_streaming(window), 0)

    threading.Thread(target=_run, daemon=True).start()


def _launch_loop(window: sublime.Window, panel: sublime.View, query: str, goal_text: str, git_root: str) -> None:
    """Echo the invoking slash command to chat, then run _run_loop on a background thread.
    Shared by the /loop and /research command handlers."""
    settings = sublime.load_settings("SublimeAssistant.sublime-settings")
    max_iterations = max(1, int(settings.get("loop_max_iterations") or loop_runner.DEFAULT_MAX_ITERATIONS))

    panel.run_command("sublime_assistant_append", {"text": f"\n---\n\n## 👤 User\n{query}\n"})
    cancel_event = _start_streaming(window)

    def _run() -> None:
        try:
            _run_loop(window, panel, goal_text, git_root, cancel_event, max_iterations)
        finally:
            sublime.set_timeout(lambda: _finish_streaming(window), 0)

    threading.Thread(target=_run, daemon=True).start()


def _run_loop(
    window: sublime.Window,
    panel: sublime.View,
    goal_text: str,
    git_root: str,
    cancel_event: threading.Event,
    max_iterations: int,
) -> None:
    """Iteratively pursue `goal_text`, one assistant turn per iteration, until the model
    reports it's done (a `LOOP_STATUS: complete` marker), the iteration cap is hit, the
    model stops reporting a status at all, or cancel_event is set. Backs /loop and /research.

    Each iteration reuses _call_api_core exactly like a normal chat turn — the model can
    only propose code changes via fenced code blocks that still require a manual Apply
    click; this loop never writes files on its own.
    """
    _append_and_wait(
        panel,
        f"\n---\n\n## 🎯 Goal\n{goal_text}\n\n_Running loop (max {max_iterations} iterations)…_\n",
    )

    for i in range(1, max_iterations + 1):
        if cancel_event.is_set():
            _append_and_wait(panel, f"\n_Loop interrupted before iteration {i}._\n")
            return

        _append_and_wait(
            panel,
            f"\n\n## 🤖 Assistant — iteration {i}/{max_iterations}\n{chat_view.PLACEHOLDER}",
        )

        prompt = loop_runner.build_iteration_prompt(goal_text, i, max_iterations)
        reply, success = _call_api_core(window, panel, prompt, git_root, cancel_event)

        was_cancelled = cancel_event.is_set()
        if was_cancelled:
            reply = reply + "\n\n_⏹ Stopped by user._"

        sublime.set_timeout(
            lambda r=reply: panel.run_command("sublime_assistant_replace_placeholder", {
                "text": r + "\n",
                "selection_region": None,
            }),
            0,
        )

        if was_cancelled:
            _append_and_wait(panel, f"\n_Loop interrupted after iteration {i}._\n")
            return
        if not success:
            _append_and_wait(panel, "\n_Loop stopped — request failed._\n")
            return
        if loop_runner.is_goal_complete(reply):
            plural = "" if i == 1 else "s"
            _append_and_wait(panel, f"\n**Loop finished** — goal achieved after {i} iteration{plural}.\n")
            return
        if not loop_runner.has_status_marker(reply):
            _append_and_wait(
                panel,
                "\n_Loop stopped — the model didn't report a loop status; treating this as "
                "finished. Run `/loop` again to keep going._\n",
            )
            return

    _append_and_wait(panel, f"\n_Loop stopped — reached the max iterations ({max_iterations})._\n")


def _dismiss_inline(block_id: str) -> None:
    ps = _inline_phantom_sets.pop(block_id, None)
    if ps:
        ps.update([])


def _resolve_hint_region(
    window: sublime.Window,
    filepath: str | None,
    sel_region: list[int] | None,
    target: sublime.View,
) -> tuple[int, int] | None:
    """Resolve the (start_line, end_line) hint for locating an edit in `target`.

    Shared by the inline phantom preview (_add_inline_phantom) and the actual
    Accept mechanism (_accept_inline) so both always compute the exact same
    proposed content — the preview must never show a different diff than
    what Accept applies.

    selection_region is only valid when it was captured from the same file as
    target: if the model suggests changes to README.md while a Python file
    was active, the selection line numbers refer to the Python file — don't
    use them for README.md. Any further localization (def/class match,
    window search) happens uniformly inside diff_mgr.compute_proposed.
    """
    active = window.active_view_in_group(0)
    active_path = active.file_name() if active else None
    target_path = target.file_name()
    selection_is_for_target = (
        filepath is None  # no explicit target → always the active file
        or (
            active_path and target_path
            and os.path.basename(active_path) == os.path.basename(target_path)
        )
    )
    if sel_region and selection_is_for_target:
        return (sel_region[0], sel_region[1])
    return None


def _accept_inline(block_id: str, window: sublime.Window) -> None:
    entry = _pending_blocks.pop(block_id, None)
    if not entry:
        _dismiss_inline(block_id)
        return
    code, filepath, sel_region = entry

    if filepath is not None:
        target = next(
            (v for v in window.views()
             if v.file_name() and os.path.basename(v.file_name()) == os.path.basename(filepath)),
            None,
        )
        if target is None and os.path.isfile(filepath):
            target = window.open_file(filepath)
    else:
        target = window.active_view_in_group(0)

    if target:
        hint = _resolve_hint_region(window, filepath, sel_region, target)
        orig = target.substr(sublime.Region(0, target.size()))
        full_proposed = diff_mgr.compute_proposed(orig, code, hint)
        window.focus_view(target)
        target.run_command("sublime_assistant_apply_code", {"code": full_proposed})

    _dismiss_inline(block_id)


def _on_inline_navigate(href: str, window: sublime.Window) -> None:
    if href.startswith("accept:"):
        _accept_inline(href[7:], window)
    elif href.startswith("diff:"):
        block_id = href[5:]
        _dismiss_inline(block_id)
        _on_apply_navigate(f"apply:{block_id}", window, dismiss_inline=False)
    elif href.startswith("dismiss:"):
        _dismiss_inline(href[8:])


def _make_inline_html(block_id: str, diff_lines: list[str]) -> str:
    """Render a colored diff phantom (green additions, red deletions)."""
    MAX_LINES = 20
    rows: list[str] = []
    extra = 0
    for line in diff_lines:
        if line.startswith(("--- ", "+++ ")):
            continue
        if len(rows) >= MAX_LINES:
            extra += 1
            continue
        text = line.rstrip("\n").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if line.startswith("+"):
            rows.append(f'<div class="add">{text}</div>')
        elif line.startswith("-"):
            rows.append(f'<div class="del">{text}</div>')
        elif line.startswith("@@"):
            rows.append(f'<div class="hunk">{text}</div>')
        else:
            rows.append(f'<div class="ctx">{text}</div>')

    if extra:
        rows.append(f'<div class="hunk">  … {extra} more lines</div>')

    return (
        '<body id="sa-inline"><style>'
        "body{margin:0;padding:2px 0;}"
        "div.diff{margin:0;font-size:0.9em;}"
        ".add,.del,.hunk,.ctx{white-space:pre-wrap;padding:0 8px;}"
        ".add{background:#0d2b14;color:#3fb950;}"
        ".del{background:#2b0d0d;color:#f85149;}"
        ".hunk{color:color(var(--foreground) alpha(0.5));}"
        ".ctx{color:color(var(--foreground) alpha(0.85));}"
        ".ctrl{padding:4px 0 2px 4px;}"
        "a{padding:2px 10px;border-radius:3px;text-decoration:none;font-size:0.9em;margin-right:6px;font-weight:bold;}"
        ".ok{background:#238636;color:#fff;}"
        ".df{background:#1f6feb;color:#fff;}"
        ".no{background:#da3633;color:#fff;}"
        "</style>"
        f'<div class="diff">{"".join(rows)}</div>'
        '<div class="ctrl">'
        f'<a class="ok" href="accept:{block_id}">&#10003; Accept</a>'
        f'<a class="df" href="diff:{block_id}">&#8771; Diff</a>'
        f'<a class="no" href="dismiss:{block_id}">&#10007; Dismiss</a>'
        "</div></body>"
    )


def _add_inline_phantom(
    window: sublime.Window,
    block_id: str,
    code: str,
    filepath: str | None,
    selection_region: list[int] | None,
) -> None:
    """Show a diff-style inline suggestion phantom in the editor."""
    if filepath is not None:
        target = next(
            (v for v in window.views()
             if v.file_name() and os.path.basename(v.file_name()) == os.path.basename(filepath)),
            None,
        )
    else:
        target = window.active_view_in_group(0)

    if target is None:
        return

    orig = target.substr(sublime.Region(0, target.size()))
    orig_lines = orig.splitlines(keepends=True)

    # Same resolver _accept_inline uses — the preview must always match what
    # Accept actually applies. Any further localization (def/class match,
    # window search) happens uniformly inside diff_mgr.compute_proposed.
    hint = _resolve_hint_region(window, filepath, selection_region, target)

    # Compute what the file would look like after applying the suggestion
    proposed = diff_mgr.compute_proposed(orig, code, hint)
    proposed_lines = proposed.splitlines(keepends=True)

    # Build unified diff (2 lines of context keeps the phantom compact)
    diff_lines = list(difflib.unified_diff(orig_lines, proposed_lines, n=2))

    # Find the first changed line from the diff for accurate phantom placement
    change_line: int | None = None
    for dl in diff_lines:
        if dl.startswith("@@"):
            # Header format: @@ -a,b +c,d @@  — use the +c value (0-indexed)
            try:
                plus_part = dl.split("+")[1].split(",")[0].split(" ")[0]
                change_line = max(0, int(plus_part) - 1)
            except (IndexError, ValueError):
                pass
            break

    if change_line is None:
        # No diff — snippet identical to original; fall back to cursor/end
        if target.sel():
            change_line = target.rowcol(target.sel()[0].begin())[0]
        else:
            change_line = target.rowcol(target.size())[0]

    pt = target.text_point(change_line, 0)
    html = _make_inline_html(block_id, diff_lines)

    ps = sublime.PhantomSet(target, f"sa_inline_{block_id}")
    _inline_phantom_sets[block_id] = ps
    ps.update([sublime.Phantom(
        sublime.Region(pt, pt), html, sublime.LAYOUT_BLOCK,
        on_navigate=lambda href, w=window: _on_inline_navigate(href, w),
    )])


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
        if block.language == "suggested-command":
            continue
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

        # Also show inline suggestion phantom in the editor
        _add_inline_phantom(window, block_id, block.content, block.filepath, selection_region)


def _on_apply_navigate(href: str, window: sublime.Window, dismiss_inline: bool = True) -> None:
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

    if dismiss_inline:
        _dismiss_inline(block_id)

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


class SublimeAssistantInterruptCommand(sublime_plugin.WindowCommand):
    """Cancel the in-flight request (a single call or an entire /loop run) for this window."""

    def run(self) -> None:
        event = _active_requests.get(self.window.id())
        if event is None:
            sublime.status_message("SublimeAssistant: nothing to interrupt")
            return
        event.set()
        sublime.status_message("SublimeAssistant: interrupting…")


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
        if window.id() in _active_requests:
            sublime.status_message("SublimeAssistant: a request is already in progress — interrupt it first.")
            return

        self.view.replace(edit, sublime.Region(0, self.view.size()), "")
        self.view.run_command("sublime_assistant_update_input_hint", {"show": True})

        display_q, api_q, cmd = slash_commands.parse(query)
        win_id = window.id()

        # Special commands — act immediately, no API call
        if cmd == "/init":
            panel = chat_view.get_or_create(window)
            if panel:
                panel.run_command("sublime_assistant_append",
                                  {"text": "\n---\n\n## 👤 User\n/init\n\n_Crawling project directory…_\n"})
            window.run_command("sublime_assistant_summarize_directory")
            return

        if cmd in ("/compact", "/clear"):
            history.clear(win_id)
            _goal_state.pop(win_id, None)
            panel = chat_view.get_or_create(window)
            if panel:
                panel.run_command("sublime_assistant_append",
                                  {"text": "\n---\n_Conversation history cleared._\n"})
            return

        if cmd == "/goal":
            goal_arg = query[len("/goal"):].strip()
            panel = chat_view.get_or_create(window)
            if panel:
                if goal_arg:
                    _goal_state[win_id] = goal_arg
                    panel.run_command("sublime_assistant_append", {
                        "text": f"\n---\n\n## 👤 User\n{query}\n\n_Goal set._\n"
                    })
                else:
                    shown = _goal_state.get(win_id) or "(no goal set)"
                    panel.run_command("sublime_assistant_append", {
                        "text": f"\n---\n\n## 👤 User\n{query}\n\n_Current goal: {shown}_\n"
                    })
            return

        if cmd == "/loop":
            arg = query[len("/loop"):].strip()
            goal_text = arg or _goal_state.get(win_id, "")
            panel = chat_view.get_or_create(window)
            if not panel:
                return
            if not goal_text:
                panel.run_command("sublime_assistant_append", {
                    "text": (f"\n---\n\n## 👤 User\n{query}\n\n_No goal set — use "
                             "`/goal <description>` or `/loop <description>`._\n")
                })
                return
            _goal_state[win_id] = goal_text
            target_dir = _get_active_dir(window) or ""
            g_root = _find_git_root(target_dir) if target_dir else ""
            _launch_loop(window, panel, query, goal_text, g_root)
            return

        if cmd == "/research":
            topic = query[len("/research"):].strip()
            panel = chat_view.get_or_create(window)
            if not panel:
                return
            if not topic:
                panel.run_command("sublime_assistant_append", {
                    "text": f"\n---\n\n## 👤 User\n{query}\n\n_Usage: `/research <topic>`._\n"
                })
                return
            goal_text = loop_runner.build_research_goal(topic)
            target_dir = _get_active_dir(window) or ""
            g_root = _find_git_root(target_dir) if target_dir else ""
            _launch_loop(window, panel, query, goal_text, g_root)
            return

        # Resolve git root for git-based commands
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
            active_dir = os.path.dirname(active_filename) if active_filename != "Untitled" else ""
        else:
            active_file, active_filename, selection, selection_region = "", "Untitled", "", None
            active_dir = ""

        g_root = _find_git_root(active_dir) if active_dir else ""

        if cmd == "/diff":
            diff = git.get_diff(g_root)
            api_q += f"\n\n--- GIT DIFF ---\n{diff or '(nothing changed)'}"

        panel = chat_view.get_or_create(window)
        if panel:
            _submit_query(window, panel, display_q, active_file, active_filename,
                          selection, selection_region, api_query=api_q)


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


class SublimeAssistantStreamUpdateCommand(sublime_plugin.TextCommand):
    """Incrementally update the assistant reply during streaming."""

    def run(self, edit, text: str):
        stream_start = self.view.settings().get("sa_stream_start")
        if stream_start is None:
            # Either not yet initialised or already finalised by replace_placeholder.
            # Distinguish by checking whether the placeholder still exists.
            region = chat_view.find_placeholder_region(self.view)
            if region is None:
                return  # Stale chunk after finalisation — discard.
            stream_start = region.begin()
            self.view.settings().set("sa_stream_start", stream_start)
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(stream_start, self.view.size()), text)
        self.view.show(self.view.size())
        self.view.set_read_only(True)


class SublimeAssistantReplacePlaceholderCommand(sublime_plugin.TextCommand):
    """Replace the placeholder in the chat panel with the assistant response and add Apply phantoms."""

    def run(self, edit, text: str, selection_region: list[int] | None = None):
        self.view.set_read_only(False)
        stream_start = self.view.settings().get("sa_stream_start")
        if stream_start is not None:
            insert_start = stream_start
            self.view.replace(edit, sublime.Region(stream_start, self.view.size()), text)
            self.view.settings().erase("sa_stream_start")
        else:
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

        window = self.window

        def _post(text: str) -> None:
            panel = chat_view.get_or_create(window)
            if panel:
                sublime.set_timeout(
                    lambda: panel.run_command("sublime_assistant_append", {"text": text}), 0
                )

        def crawl_and_enrich() -> None:
            raw, file_contents = summarizer.crawl(git_root)
            cached = f"--- DIRECTORY SUMMARY ---\n{raw}"
            _summary_state[win_id] = (git_root, time.time(), cached)
            n = len(file_contents)
            if file_contents:
                _post(f"_Found {n} file{'s' if n != 1 else ''}. Enriching with LLM descriptions…_\n")
                sublime.set_timeout(
                    lambda: sublime.status_message(
                        f"SublimeAssistant: enriching {n} files..."
                    ), 0,
                )
                _enrich_summary(win_id, git_root, file_contents)
            _post(
                f"\n_Directory summary ready for `{os.path.basename(git_root)}/` "
                f"({n} file{'s' if n != 1 else ''})._\n"
            )
            sublime.set_timeout(
                lambda: sublime.status_message(
                    f"SublimeAssistant: summary ready for {os.path.basename(git_root)}/"
                ), 0,
            )

        threading.Thread(target=crawl_and_enrich, daemon=True).start()


class SublimeAssistantUpdateInputHintCommand(sublime_plugin.TextCommand):
    """Show or hide the placeholder hint in the input area.

    While a request is streaming (the input view's "sa_streaming" setting), the idle
    "Ask anything…" hint is replaced with a clickable Stop control instead — the input
    pane doesn't scroll, so this is where an interrupt affordance stays reachable for the
    whole duration of a response or a multi-iteration /loop run.
    """

    def run(self, edit, show: bool = True):
        ps = _hint_sets.setdefault(self.view.id(), sublime.PhantomSet(self.view, "sa_hint"))
        if self.view.size() != 0:
            ps.update([])
            return
        if self.view.settings().get("sa_streaming"):
            window = self.view.window()
            ps.update([sublime.Phantom(
                sublime.Region(0, 0), _STOP_HINT_HTML, sublime.LAYOUT_INLINE,
                on_navigate=lambda href, w=window: w.run_command("sublime_assistant_interrupt") if w else None,
            )])
        elif show:
            ps.update([sublime.Phantom(
                sublime.Region(0, 0), _HINT_HTML, sublime.LAYOUT_INLINE
            )])
        else:
            ps.update([])


class SublimeAssistantInputListener(sublime_plugin.ViewEventListener):
    """Show/hide the input hint as the user types."""

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        return bool(settings.get("sublime_assistant_input", False))

    def on_activated(self) -> None:
        self.view.run_command("sublime_assistant_update_input_hint", {"show": True})

    def on_modified(self) -> None:
        self.view.run_command(
            "sublime_assistant_update_input_hint", {"show": self.view.size() == 0}
        )


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
