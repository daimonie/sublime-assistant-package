"""Unit tests for SublimeAssistant.py — the plugin entry point / orchestration layer.

The module is loaded via conftest.load_sublime_assistant() since it lives outside
the `assistant` package and uses relative imports that only resolve inside Sublime
Text's package loader. These tests anchor the pure/near-pure helper functions that
drive git-root resolution, preset/config merging, backend selection, and the
inline-suggestion hint-region logic — the parts of this file most likely to
silently break something (wrong backend picked, wrong file edited, preview not
matching what Accept applies) without a test failing to say so.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from unittest.mock import patch

import pytest

from conftest import load_sublime_assistant
from fakes import FakeSettings, FakeView, FakeWindow

sa = load_sublime_assistant()


# ── _find_git_root ────────────────────────────────────────────────────────

class TestFindGitRoot:
    def test_finds_git_root_by_walking_up(self, tmp_path):
        root = tmp_path / "project"
        (root / ".git").mkdir(parents=True)
        nested = root / "src" / "deep"
        nested.mkdir(parents=True)
        assert sa._find_git_root(str(nested)) == str(root)

    def test_path_itself_is_git_root(self, tmp_path):
        root = tmp_path / "project"
        (root / ".git").mkdir(parents=True)
        assert sa._find_git_root(str(root)) == str(root)

    def test_returns_original_path_when_no_git_root_found(self, tmp_path):
        no_git = tmp_path / "not_a_repo"
        no_git.mkdir()
        assert sa._find_git_root(str(no_git)) == str(no_git)


# ── _get_active_dir ───────────────────────────────────────────────────────

class TestGetActiveDir:
    def test_uses_active_files_directory(self):
        view = FakeView(file_name="/proj/src/main.py")
        window = FakeWindow(active_view=view, folders=["/proj"])
        assert sa._get_active_dir(window) == "/proj/src"

    def test_falls_back_to_first_project_folder_when_no_active_view(self):
        window = FakeWindow(active_view=None, folders=["/proj/a", "/proj/b"])
        assert sa._get_active_dir(window) == "/proj/a"

    def test_falls_back_to_first_folder_when_active_view_has_no_file(self):
        view = FakeView(file_name=None)
        window = FakeWindow(active_view=view, folders=["/proj"])
        assert sa._get_active_dir(window) == "/proj"

    def test_returns_none_when_nothing_available(self):
        window = FakeWindow(active_view=None, folders=[])
        assert sa._get_active_dir(window) is None


# ── _load_default_presets / _get_api_config ──────────────────────────────

class TestGetApiConfig:
    def _patch_packages_path(self, tmp_path, presets: dict):
        pkg_dir = tmp_path / "Packages" / "SublimeAssistant"
        pkg_dir.mkdir(parents=True)
        settings_file = pkg_dir / "SublimeAssistant.sublime-settings"
        settings_file.write_text(json.dumps({"presets": presets}))
        return patch("sublime.packages_path", return_value=str(tmp_path / "Packages"))

    def test_defaults_when_no_preset_active(self, tmp_path):
        with self._patch_packages_path(tmp_path, {}):
            settings = FakeSettings({
                "active_preset": None,
                "system_prompt": "be helpful",
            })
            url, key, model, prompt, backend = sa._get_api_config(settings)
        assert url == "http://localhost:11434/v1/chat/completions"
        assert key == ""
        assert model == "devstral-small-2:latest"
        assert prompt == "be helpful"
        assert backend == "openai"

    def test_package_defaults_supply_backend_and_model_for_active_preset(self, tmp_path):
        with self._patch_packages_path(tmp_path, {
            "claude": {"backend": "claude", "model": "claude-sonnet-5", "api_url": "https://api.anthropic.com"}
        }):
            settings = FakeSettings({"active_preset": "claude", "presets": {}})
            url, key, model, prompt, backend = sa._get_api_config(settings)
        assert backend == "claude"
        assert model == "claude-sonnet-5"

    def test_user_api_key_merges_onto_package_preset_without_losing_backend(self, tmp_path):
        """Regression: Sublime Text does not deep-merge nested settings objects, so a
        User settings file containing only {"presets": {"claude": {"api_key": "..."}}}
        must not lose the package-defined backend/model for that preset."""
        with self._patch_packages_path(tmp_path, {
            "claude": {"backend": "claude", "model": "claude-sonnet-5"}
        }):
            settings = FakeSettings({
                "active_preset": "claude",
                "presets": {"claude": {"api_key": "user-secret-key"}},
            })
            url, key, model, prompt, backend = sa._get_api_config(settings)
        assert backend == "claude"
        assert model == "claude-sonnet-5"
        assert key == "user-secret-key"

    def test_user_settings_can_override_package_defaults(self, tmp_path):
        with self._patch_packages_path(tmp_path, {
            "mistral": {"backend": "openai", "model": "mistral-small"}
        }):
            settings = FakeSettings({
                "active_preset": "mistral",
                "presets": {"mistral": {"model": "mistral-large"}},
            })
            _, _, model, _, _ = sa._get_api_config(settings)
        assert model == "mistral-large"

    def test_user_only_preset_not_in_package_defaults(self, tmp_path):
        with self._patch_packages_path(tmp_path, {}):
            settings = FakeSettings({
                "active_preset": "custom",
                "presets": {"custom": {"backend": "openai", "model": "local-model", "api_url": "http://x"}},
            })
            url, _, model, _, backend = sa._get_api_config(settings)
        assert url == "http://x"
        assert model == "local-model"
        assert backend == "openai"

    def test_missing_package_settings_file_does_not_raise(self, tmp_path):
        # packages_path points somewhere with no SublimeAssistant.sublime-settings file
        with patch("sublime.packages_path", return_value=str(tmp_path / "nonexistent")):
            settings = FakeSettings({"active_preset": None})
            url, key, model, prompt, backend = sa._get_api_config(settings)
        assert backend == "openai"


# ── _make_client ──────────────────────────────────────────────────────────

class TestMakeClient:
    def test_claude_backend_returns_claude_client(self):
        client = sa._make_client("http://x", "key", "model", 60, "claude")
        assert isinstance(client, sa.api.ClaudeClient)
        assert client.model == "model"
        assert client.timeout_seconds == 60

    def test_openai_backend_returns_openai_client(self):
        client = sa._make_client("http://x", "key", "model", 60, "openai")
        assert isinstance(client, sa.api.OpenAIClient)
        assert client.url == "http://x"

    def test_unknown_backend_defaults_to_openai_client(self):
        client = sa._make_client("http://x", "key", "model", 60, "something-else")
        assert isinstance(client, sa.api.OpenAIClient)


# ── _find_input_view ──────────────────────────────────────────────────────

class TestFindInputView:
    def test_finds_view_by_name(self):
        from assistant import input_view
        target = FakeView(name=input_view.NAME)
        other = FakeView(name="something else")
        window = FakeWindow(views=[other, target])
        assert sa._find_input_view(window) is target

    def test_returns_none_when_not_present(self):
        window = FakeWindow(views=[FakeView(name="other")])
        assert sa._find_input_view(window) is None


# ── _resolve_hint_region ──────────────────────────────────────────────────

class TestResolveHintRegion:
    def test_no_filepath_always_uses_selection_region(self):
        active = FakeView(file_name="/proj/main.py")
        window = FakeWindow(active_view=active)
        target = FakeView(file_name="/proj/main.py")
        result = sa._resolve_hint_region(window, None, [3, 7], target)
        assert result == (3, 7)

    def test_filepath_matching_active_file_uses_selection_region(self):
        active = FakeView(file_name="/proj/main.py")
        window = FakeWindow(active_view=active)
        target = FakeView(file_name="/proj/main.py")
        result = sa._resolve_hint_region(window, "main.py", [3, 7], target)
        assert result == (3, 7)

    def test_filepath_targeting_a_different_file_ignores_selection_region(self):
        """The selection was captured while README.md was active; a suggestion
        targeting a different file (e.g. app.py) must not reuse those line numbers."""
        active = FakeView(file_name="/proj/README.md")
        window = FakeWindow(active_view=active)
        target = FakeView(file_name="/proj/app.py")
        result = sa._resolve_hint_region(window, "app.py", [3, 7], target)
        assert result is None

    def test_no_selection_region_returns_none(self):
        active = FakeView(file_name="/proj/main.py")
        window = FakeWindow(active_view=active)
        target = FakeView(file_name="/proj/main.py")
        result = sa._resolve_hint_region(window, None, None, target)
        assert result is None

    def test_no_active_view_and_filepath_set_returns_none(self):
        window = FakeWindow(active_view=None)
        target = FakeView(file_name="/proj/app.py")
        result = sa._resolve_hint_region(window, "app.py", [1, 2], target)
        assert result is None


# ── _make_inline_html ───────────────────────────────────────────────────

class TestMakeInlineHtml:
    def test_renders_additions_and_deletions_with_classes(self):
        diff_lines = ["--- a/f\n", "+++ b/f\n", "+added line\n", "-removed line\n", " context\n"]
        html = sa._make_inline_html("block_1", diff_lines)
        assert '<div class="add">+added line</div>' in html
        assert '<div class="del">-removed line</div>' in html
        assert '<div class="ctx"> context</div>' in html
        assert "--- a/f" not in html
        assert "+++ b/f" not in html

    def test_includes_accept_diff_dismiss_links_with_block_id(self):
        html = sa._make_inline_html("block_42", [])
        assert 'href="accept:block_42"' in html
        assert 'href="diff:block_42"' in html
        assert 'href="dismiss:block_42"' in html

    def test_escapes_html_special_characters(self):
        html = sa._make_inline_html("b1", ["+x = a < b && c > d\n"])
        assert "&lt;" in html
        assert "&gt;" in html
        assert "&amp;" in html

    def test_truncates_after_max_lines_and_reports_remainder(self):
        diff_lines = [f"+line {i}\n" for i in range(30)]
        html = sa._make_inline_html("b1", diff_lines)
        assert html.count('class="add"') == 20
        assert "10 more lines" in html

    def test_hunk_header_gets_hunk_class(self):
        html = sa._make_inline_html("b1", ["@@ -1,2 +1,3 @@\n"])
        assert '<div class="hunk">@@ -1,2 +1,3 @@</div>' in html


# ── plugin_unloaded ────────────────────────────────────────────────────────

class TestPluginUnloaded:
    def test_removes_cached_assistant_submodules(self):
        fake_name = f"{sa.__package__}.assistant.some_fake_submodule"
        sys.modules[fake_name] = object()
        try:
            sa.plugin_unloaded()
            assert fake_name not in sys.modules
        finally:
            sys.modules.pop(fake_name, None)

    def test_does_not_remove_unrelated_modules(self):
        assert "os" in sys.modules
        sa.plugin_unloaded()
        assert "os" in sys.modules


# ── _start_streaming / _finish_streaming ──────────────────────────────────

class TestStreamingLifecycle:
    def test_start_streaming_registers_cancel_event_and_marks_input_view(self):
        from assistant import input_view
        inp = FakeView(name=input_view.NAME)
        window = FakeWindow(views=[inp], window_id=999)
        event = sa._start_streaming(window)
        try:
            assert window.id() in sa._active_requests
            assert sa._active_requests[window.id()] is event
            assert inp.settings().get("sa_streaming") is True
        finally:
            sa._finish_streaming(window)

    def test_finish_streaming_clears_state(self):
        from assistant import input_view
        inp = FakeView(name=input_view.NAME)
        window = FakeWindow(views=[inp], window_id=1000)
        sa._start_streaming(window)
        sa._finish_streaming(window)
        assert window.id() not in sa._active_requests
        assert inp.settings().get("sa_streaming") is None

    def test_finish_streaming_on_window_with_no_input_view_does_not_raise(self):
        window = FakeWindow(views=[], window_id=1001)
        sa._active_requests[window.id()] = object()
        sa._finish_streaming(window)
        assert window.id() not in sa._active_requests


# ── Buffer-mutating commands: the actual "write to the user's file" surface ─
# Bugs here silently corrupt a user's open buffer, so these are anchored
# against a real (fake) view rather than just checked for not-raising.

class TestAppendCommand:
    def test_appends_text_at_end_and_restores_read_only(self):
        view = FakeView(content="existing\n")
        cmd = sa.SublimeAssistantAppendCommand(view)
        cmd.run(None, text="more\n")
        assert view._content == "existing\nmore\n"
        assert view._read_only is True

    def test_append_to_empty_view(self):
        view = FakeView(content="")
        cmd = sa.SublimeAssistantAppendCommand(view)
        cmd.run(None, text="first line\n")
        assert view._content == "first line\n"


class TestApplyCodeCommand:
    def test_replaces_entire_view_content_with_proposed_code(self):
        view = FakeView(content="old content\nline two\n")
        cmd = sa.SublimeAssistantApplyCodeCommand(view)
        cmd.run(None, code="brand new content\n")
        assert view._content == "brand new content\n"

    def test_replacing_with_empty_code_clears_the_view(self):
        view = FakeView(content="delete me\n")
        cmd = sa.SublimeAssistantApplyCodeCommand(view)
        cmd.run(None, code="")
        assert view._content == ""


class TestApplySnippetCommand:
    def test_replaces_a_middle_line_range(self):
        view = FakeView(content="line0\nline1\nline2\nline3\n")
        cmd = sa.SublimeAssistantApplySnippetCommand(view)
        cmd.run(None, code="REPLACED\n", start_line=1, end_line=2)
        assert view._content == "line0\nREPLACED\nline2\nline3\n"

    def test_end_line_beyond_last_row_extends_to_view_end(self):
        view = FakeView(content="line0\nline1\n")
        cmd = sa.SublimeAssistantApplySnippetCommand(view)
        cmd.run(None, code="TAIL", start_line=1, end_line=99)
        assert view._content == "line0\nTAIL\n"

    def test_code_without_trailing_newline_gets_one_added(self):
        view = FakeView(content="a\nb\nc\n")
        cmd = sa.SublimeAssistantApplySnippetCommand(view)
        cmd.run(None, code="REPLACED", start_line=1, end_line=2)
        assert view._content == "a\nREPLACED\nc\n"


class TestStreamUpdateAndReplacePlaceholder:
    def _panel(self, window: FakeWindow) -> FakeView:
        panel = window.new_file()
        panel._content = "## 🤖 Assistant\n> _Thinking..._"
        return panel

    def test_stream_update_replaces_from_placeholder_on_first_call(self):
        window = FakeWindow(window_id=2001)
        panel = self._panel(window)
        cmd = sa.SublimeAssistantStreamUpdateCommand(panel)
        cmd.run(None, text="Hello")
        assert panel._content == "## 🤖 Assistant\nHello"
        assert panel.settings().get("sa_stream_start") is not None

    def test_stream_update_accumulates_using_cached_stream_start(self):
        window = FakeWindow(window_id=2002)
        panel = self._panel(window)
        cmd = sa.SublimeAssistantStreamUpdateCommand(panel)
        cmd.run(None, text="Hel")
        cmd.run(None, text="Hello world")
        assert panel._content == "## 🤖 Assistant\nHello world"

    def test_stream_update_discards_stale_chunk_after_finalisation(self):
        window = FakeWindow(window_id=2003)
        panel = self._panel(window)
        # No placeholder present and no sa_stream_start set => already finalized.
        panel._content = "## 🤖 Assistant\nAlready finished."
        cmd = sa.SublimeAssistantStreamUpdateCommand(panel)
        cmd.run(None, text="late chunk")
        assert panel._content == "## 🤖 Assistant\nAlready finished."

    def test_replace_placeholder_finalizes_streamed_reply_and_adds_apply_phantom(self):
        window = FakeWindow(window_id=2004)
        panel = self._panel(window)
        stream_cmd = sa.SublimeAssistantStreamUpdateCommand(panel)
        stream_cmd.run(None, text="Here is code:\n```python\nprint(1)\n```\n")

        replace_cmd = sa.SublimeAssistantReplacePlaceholderCommand(panel)
        replace_cmd.run(None, text="Here is code:\n```python\nprint(1)\n```\n", selection_region=None)

        assert panel.settings().get("sa_stream_start") is None
        assert len(panel.phantoms) == 1
        assert "apply:block_" in panel.phantoms[0][2]

    def test_replace_placeholder_without_prior_streaming_replaces_placeholder_text(self):
        window = FakeWindow(window_id=2005)
        panel = self._panel(window)
        cmd = sa.SublimeAssistantReplacePlaceholderCommand(panel)
        cmd.run(None, text="plain reply, no code\n", selection_region=None)
        assert panel._content == "## 🤖 Assistant\nplain reply, no code\n"


# ── _call_api_core: the core request/response/history-recording pipeline ──

class TestCallApiCore:
    class _FakeClient:
        def __init__(self, reply: str = "the reply", success: bool = True, simulate_read_file: str | None = None):
            self.reply = reply
            self.success = success
            self.simulate_read_file = simulate_read_file
            self.received_kwargs: dict = {}

        def call(self, messages, **kwargs):
            self.received_kwargs = {"messages": messages, **kwargs}
            if self.simulate_read_file and kwargs.get("on_read_file"):
                kwargs["on_read_file"](self.simulate_read_file)
            return self.reply, self.success

    def _settings(self, **overrides):
        base = {
            "active_preset": None,
            "system_prompt": "You are helpful.",
            "request_timeout": 60,
            "presets": {},
        }
        base.update(overrides)
        return FakeSettings(base)

    def test_successful_call_records_history_and_returns_reply(self):
        window = FakeWindow(window_id=3001)
        panel = window.new_file()
        fake_client = self._FakeClient(reply="42 is the answer")

        with patch("sublime.load_settings", return_value=self._settings()), \
             patch.object(sa, "_make_client", return_value=fake_client):
            reply, success = sa._call_api_core(window, panel, "what is 6*7?", "", None)

        assert success is True
        assert reply == "42 is the answer"
        messages = sa.history.get_messages(window.id(), "You are helpful.")
        assert {"role": "user", "content": "what is 6*7?"} in messages
        assert {"role": "assistant", "content": "42 is the answer"} in messages

    def test_failed_call_does_not_record_history(self):
        window = FakeWindow(window_id=3002)
        panel = window.new_file()
        fake_client = self._FakeClient(reply="error text", success=False)

        with patch("sublime.load_settings", return_value=self._settings()), \
             patch.object(sa, "_make_client", return_value=fake_client):
            reply, success = sa._call_api_core(window, panel, "a question", "", None)

        assert success is False
        messages = sa.history.get_messages(window.id(), "You are helpful.")
        assert messages == [{"role": "system", "content": "You are helpful."}]

    def test_tool_usage_is_logged_in_the_reply(self):
        window = FakeWindow(window_id=3003)
        panel = window.new_file()
        fake_client = self._FakeClient(reply="done", simulate_read_file="notes.md")

        with patch("sublime.load_settings", return_value=self._settings()), \
             patch.object(sa, "_make_client", return_value=fake_client):
            reply, success = sa._call_api_core(window, panel, "read notes.md", "", None)

        assert success is True
        assert "Tool calls:" in reply
        assert "`notes.md`" in reply

    def test_backend_and_model_selection_passed_to_make_client(self):
        window = FakeWindow(window_id=3004)
        panel = window.new_file()
        fake_client = self._FakeClient()

        with patch("sublime.load_settings", return_value=self._settings(
            presets={"claude": {"backend": "claude", "model": "claude-x", "api_key": "k"}},
            active_preset="claude",
        )), patch.object(sa, "_make_client", return_value=fake_client) as mock_make_client:
            sa._call_api_core(window, panel, "hi", "", None)

        args = mock_make_client.call_args[0]
        assert args[4] == "claude"  # backend positional arg
        assert args[2] == "claude-x"  # model positional arg

    def test_wires_a_working_on_fetch_confirm_into_the_client(self):
        """_call_api_core must hand the client a real on_fetch_confirm callback (not
        None / omitted) — that's the harness-side enforcement point from api.py's
        fetch_url gate. A previously-approved domain should resolve through it
        without blocking."""
        window = FakeWindow(window_id=3005)
        panel = window.new_file()
        fake_client = self._FakeClient()
        sa._fetch_approvals[window.id()] = {"example.com"}
        try:
            with patch("sublime.load_settings", return_value=self._settings()), \
                 patch.object(sa, "_make_client", return_value=fake_client):
                sa._call_api_core(window, panel, "hi", "", None)

            confirm = fake_client.received_kwargs.get("on_fetch_confirm")
            assert callable(confirm)
            assert confirm("http://example.com/anything") is True
        finally:
            sa._fetch_approvals.pop(window.id(), None)


# ── True end-to-end: real _call_api_core + real api.call() tool loop + a
# simulated Allow click, with only the network (urlopen) and fetch_url mocked.
# Everything else in this test is the actual production code path — this is
# what proves an Allow click really does let the fetch happen and its content
# really does reach the model's next round, not just that the two halves work
# in isolation.

class _FakeStreamResponse:
    """SSE-style stand-in for urlopen()'s streaming response (matches api.py's
    _do_request_streaming, which reads `for raw in resp: ...`)."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


def _sse_lines(*events: dict) -> list[bytes]:
    lines = [f"data: {json.dumps(ev)}\n".encode() for ev in events]
    lines.append(b"data: [DONE]\n")
    return lines


class TestConfirmFetchUrlEndToEndThroughRealApiCall:
    def _settings(self):
        return FakeSettings({
            "active_preset": None,
            "system_prompt": "You are helpful.",
            "request_timeout": 60,
            "presets": {},
        })

    def _click_allow_when_prompted(self):
        for _ in range(200):
            if sa._pending_fetch_confirms:
                break
            time.sleep(0.005)
        fetch_id = next(iter(sa._pending_fetch_confirms))
        sa._resolve_fetch_confirm(fetch_id, True)

    def test_allowed_fetch_actually_runs_and_its_content_reaches_the_final_reply(self):
        window = FakeWindow(window_id=7001)
        panel = window.new_file()
        panel._content = "## 🤖 Assistant\n> _Thinking..._"

        tool_round = _sse_lines({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "1", "function": {
                "name": "fetch_url",
                "arguments": json.dumps({"url": "http://allowed-site.example.com/docs"}),
            }},
        ]}}]})
        final_round = _sse_lines({"choices": [{"delta": {"content": "Per the page: 42."}}]})
        responses = [_FakeStreamResponse(tool_round), _FakeStreamResponse(final_round)]

        t = threading.Thread(target=self._click_allow_when_prompted, daemon=True)
        t.start()
        try:
            with patch("sublime.load_settings", return_value=self._settings()), \
                 patch("urllib.request.urlopen", side_effect=responses), \
                 patch.object(sa.api, "fetch_url", return_value=("The answer is 42.", True)) as mock_fetch_url:
                reply, success = sa._call_api_core(window, panel, "what does the linked doc say?", "", None)
        finally:
            t.join(5)

        assert success is True
        mock_fetch_url.assert_called_once_with("http://allowed-site.example.com/docs")
        assert reply.startswith("Per the page: 42.")
        assert "fetched `http://allowed-site.example.com/docs`" in reply
        assert "allowed-site.example.com" in sa._fetch_approvals.get(window.id(), set())
        sa._fetch_approvals.pop(window.id(), None)

    def test_denied_fetch_never_runs_and_model_gets_a_denial_it_can_respond_to(self):
        window = FakeWindow(window_id=7002)
        panel = window.new_file()
        panel._content = "## 🤖 Assistant\n> _Thinking..._"

        tool_round = _sse_lines({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "1", "function": {
                "name": "fetch_url",
                "arguments": json.dumps({"url": "http://denied-site.example.com/docs"}),
            }},
        ]}}]})
        final_round = _sse_lines({"choices": [{"delta": {"content": "Okay, I won't fetch that."}}]})
        responses = [_FakeStreamResponse(tool_round), _FakeStreamResponse(final_round)]

        def _click_deny_when_prompted():
            for _ in range(200):
                if sa._pending_fetch_confirms:
                    break
                time.sleep(0.005)
            fetch_id = next(iter(sa._pending_fetch_confirms))
            sa._resolve_fetch_confirm(fetch_id, False)

        t = threading.Thread(target=_click_deny_when_prompted, daemon=True)
        t.start()
        try:
            with patch("sublime.load_settings", return_value=self._settings()), \
                 patch("urllib.request.urlopen", side_effect=responses), \
                 patch.object(sa.api, "fetch_url") as mock_fetch_url:
                reply, success = sa._call_api_core(window, panel, "fetch that link", "", None)
        finally:
            t.join(5)

        assert success is True
        mock_fetch_url.assert_not_called()
        assert reply.startswith("Okay, I won't fetch that.")
        assert "denied-site.example.com" not in sa._fetch_approvals.get(window.id(), set())


# ── fetch_url approval gate: the harness-side UI half of the injection fix ──

class TestConfirmFetchUrl:
    def _panel(self, window: FakeWindow) -> FakeView:
        panel = window.new_file()
        panel._content = "## 🤖 Assistant\n> _Thinking..._"
        return panel

    def _click_first_pending(self, allow: bool) -> None:
        for _ in range(200):
            if sa._pending_fetch_confirms:
                break
            time.sleep(0.005)
        fetch_id = next(iter(sa._pending_fetch_confirms))
        sa._resolve_fetch_confirm(fetch_id, allow)

    def test_already_approved_domain_returns_true_without_blocking(self):
        window = FakeWindow(window_id=4001)
        panel = self._panel(window)
        sa._fetch_approvals[window.id()] = {"example.com"}
        try:
            allowed = sa._confirm_fetch_url(window, panel, window.id(), "http://example.com/page")
        finally:
            sa._fetch_approvals.pop(window.id(), None)
        assert allowed is True
        assert sa._pending_fetch_confirms == {}

    def test_clicking_allow_unblocks_with_true_and_remembers_domain(self):
        window = FakeWindow(window_id=4002)
        panel = self._panel(window)
        t = threading.Thread(target=self._click_first_pending, args=(True,), daemon=True)
        t.start()
        try:
            allowed = sa._confirm_fetch_url(window, panel, window.id(), "http://new-site.example.com/docs")
        finally:
            t.join(5)
        assert allowed is True
        assert "new-site.example.com" in sa._fetch_approvals.get(window.id(), set())
        sa._fetch_approvals.pop(window.id(), None)

    def test_clicking_deny_unblocks_with_false_and_does_not_remember(self):
        window = FakeWindow(window_id=4003)
        panel = self._panel(window)
        t = threading.Thread(target=self._click_first_pending, args=(False,), daemon=True)
        t.start()
        try:
            allowed = sa._confirm_fetch_url(window, panel, window.id(), "http://untrusted.example.com")
        finally:
            t.join(5)
        assert allowed is False
        assert "untrusted.example.com" not in sa._fetch_approvals.get(window.id(), set())

    def test_no_response_times_out_to_deny(self):
        window = FakeWindow(window_id=4004)
        panel = self._panel(window)
        with patch.object(sa, "_FETCH_CONFIRM_TIMEOUT", 0.05):
            allowed = sa._confirm_fetch_url(window, panel, window.id(), "http://slow.example.com")
        assert allowed is False
        assert sa._pending_fetch_confirms == {}

    def test_cancel_event_set_mid_wait_denies_promptly_without_full_timeout(self):
        window = FakeWindow(window_id=4006)
        panel = self._panel(window)
        cancel = threading.Event()

        def _cancel_after_prompt_shown():
            for _ in range(200):
                if sa._pending_fetch_confirms:
                    break
                time.sleep(0.005)
            cancel.set()

        t = threading.Thread(target=_cancel_after_prompt_shown, daemon=True)
        start = time.time()
        t.start()
        with patch.object(sa, "_FETCH_CONFIRM_TIMEOUT", 30):
            allowed = sa._confirm_fetch_url(
                window, panel, window.id(), "http://slow-to-cancel.example.com", cancel_event=cancel
            )
        elapsed = time.time() - start
        t.join(5)
        assert allowed is False
        assert elapsed < 5  # nowhere near the 30s timeout — cancellation short-circuited it
        assert sa._pending_fetch_confirms == {}

    def test_second_prompt_for_same_domain_does_not_reuse_a_stale_pending_entry(self):
        """Sanity check that approvals are domain-scoped and independent prompts
        don't leak state into each other."""
        window = FakeWindow(window_id=4005)
        panel = self._panel(window)
        with patch.object(sa, "_FETCH_CONFIRM_TIMEOUT", 0.05):
            first = sa._confirm_fetch_url(window, panel, window.id(), "http://a.example.com")
            second = sa._confirm_fetch_url(window, panel, window.id(), "http://b.example.com")
        assert first is False and second is False
        assert sa._fetch_approvals.get(window.id(), set()) == set()


class TestFetchConfirmHtmlAndNavigation:
    def test_html_contains_allow_deny_links_and_escaped_url(self):
        html = sa._make_fetch_confirm_html("fetch_1", "http://example.com?a=1&b=2")
        assert 'href="fetch_allow:fetch_1"' in html
        assert 'href="fetch_deny:fetch_1"' in html
        assert "&amp;b=2" in html

    def test_navigate_allow_resolves_pending_confirm(self):
        event = threading.Event()
        result = {"allow": False}
        sa._pending_fetch_confirms["fetch_test_1"] = (event, result)
        sa._on_fetch_confirm_navigate("fetch_allow:fetch_test_1", window=None)
        assert result["allow"] is True
        assert event.is_set()
        assert "fetch_test_1" not in sa._pending_fetch_confirms

    def test_navigate_deny_resolves_pending_confirm(self):
        event = threading.Event()
        result = {"allow": True}
        sa._pending_fetch_confirms["fetch_test_2"] = (event, result)
        sa._on_fetch_confirm_navigate("fetch_deny:fetch_test_2", window=None)
        assert result["allow"] is False
        assert event.is_set()

    def test_navigate_unknown_fetch_id_does_not_raise(self):
        sa._on_fetch_confirm_navigate("fetch_allow:does_not_exist", window=None)

    def test_navigate_irrelevant_href_is_ignored(self):
        sa._on_fetch_confirm_navigate("something:else", window=None)


class TestCompactClearsFetchApprovals:
    def test_slash_compact_clears_fetch_approvals_for_the_window(self):
        input_box = FakeView(name=sa.input_view.NAME, content="/compact")
        window = FakeWindow(views=[input_box], window_id=5001)
        sa._fetch_approvals[window.id()] = {"example.com"}

        cmd = sa.SublimeAssistantSubmitCommand(input_box)
        cmd.run(None)

        assert window.id() not in sa._fetch_approvals
