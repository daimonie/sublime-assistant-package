"""OpenAI-compatible HTTP API client."""
from __future__ import annotations

import abc
import json
import re
import socket
import traceback
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Callable

_TIMEOUT = 120
_FETCH_TIMEOUT = 30
_MAX_TOOL_ROUNDS = 5
# Fetched page content is truncated to this many characters. Devstral has 384k context; if you see
# "truncating input prompt" in Ollama logs, increase Ollama's context (e.g. OLLAMA_NUM_CTX=32768) or lower this.
_MAX_FETCH_CHARS = 80_000


READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the full content of a project file for additional context. "
            "Use when you need to inspect a file referenced by imports, @mentions, "
            "or described in the conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename or relative path (e.g. 'api.py' or 'assistant/api.py')",
                }
            },
            "required": ["filename"],
        },
    },
}

FETCH_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "Fetch the text content of a URL (e.g. a documentation page). Use this when the user asks to read, check, or use a web page or doc link.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch (e.g. https://docs.example.com/page)",
                }
            },
            "required": ["url"],
        },
    },
}


_SKIP_TAGS = {"script", "style", "nav", "header", "footer"}


def fetch_models(api_url: str, api_key: str) -> tuple[list[str], str]:
    """Fetch available models from the /v1/models endpoint. Returns (model_ids, error_message)."""
    parsed = urllib.parse.urlparse(api_url)
    # Replace path up to and including /v1/ with /v1/models
    base = parsed._replace(path=re.sub(r"/v1/.*$", "/v1/models", parsed.path))
    models_url = urllib.parse.urlunparse(base)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(models_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        models = [m["id"] for m in (data.get("data") or []) if m.get("id")]
        if not models:
            return [], f"No models returned by {models_url}"
        return sorted(models), ""
    except Exception as e:
        return [], f"Error fetching models from {models_url}: {e}"


def _strip_html(html: str) -> str:
    """Extract plain text from HTML using stdlib only."""
    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.text: list[str] = []
            self._skip_depth: int = 0

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag.lower() in _SKIP_TAGS:
                self._skip_depth += 1

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() in _SKIP_TAGS and self._skip_depth > 0:
                self._skip_depth -= 1

        def handle_data(self, data: str) -> None:
            if self._skip_depth == 0:
                self.text.append(data)

        def get_text(self) -> str:
            return " ".join(self.text).strip()

    try:
        parser = _TextExtractor()
        parser.feed(html)
        out = parser.get_text()
    except Exception:
        out = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", out).strip()


def fetch_url(url: str) -> tuple[str, bool]:
    """Fetch a URL and return (text_content_or_error, success)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SublimeAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            body = resp.read().decode(errors="replace")
            ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct:
            body = _strip_html(body)
        if len(body) > _MAX_FETCH_CHARS:
            body = body[:_MAX_FETCH_CHARS] + "\n\n[... content truncated to fit context window ...]"
        return body, True
    except TimeoutError:
        return f"Error fetching URL: request timed out after {_FETCH_TIMEOUT} seconds.", False
    except urllib.error.URLError as e:
        if "timed out" in str(e.reason).lower() or "timeout" in str(e.reason).lower():
            return f"Error fetching URL: request timed out after {_FETCH_TIMEOUT} seconds.", False
        return f"Error fetching URL: {e.reason}", False
    except Exception as e:
        return f"Error fetching URL: {e}", False


def _run_tool(name: str, arguments: str) -> str:
    """Execute a single tool by name and return the result string."""
    if name != "fetch_url":
        return f"Unknown tool: {name}"
    try:
        args = json.loads(arguments)
        url = (args.get("url") or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            return "Error: URL must start with http:// or https://"
        content, ok = fetch_url(url)
        return content
    except json.JSONDecodeError as e:
        return f"Invalid arguments JSON: {e}"
    except Exception as e:
        return f"Error: {e}"


def _format_request_info(url: str, model: str, messages: list[dict]) -> str:
    """Summary of what we sent (no sensitive content)."""
    parts = [f"URL: {url}", f"Model: {model}", f"Messages: {len(messages)}"]
    if messages:
        roles = [m.get("role", "?") for m in messages]
        parts.append(f"Roles: {', '.join(roles)}")
    return "\n".join(parts)


def _format_tool_summary(tools_invoked: list[tuple[str, int]]) -> str:
    """One-line summary of which tools were run and how much data was sent back (for error messages).
    Each tuple is (tool_name, result_size_chars)."""
    if not tools_invoked:
        return "**Tool calls this request:** none"
    # Group by name: (count, total_chars)
    by_name: dict[str, tuple[int, int]] = {}
    for name, size in tools_invoked:
        c, t = by_name.get(name, (0, 0))
        by_name[name] = (c + 1, t + size)
    parts = []
    for name in sorted(by_name.keys()):
        count, total_chars = by_name[name]
        approx_tokens = total_chars // 4
        size_str = f"{total_chars:,} chars, ~{approx_tokens:,} tokens"
        parts.append(f"{name} ({count} call{'s' if count != 1 else ''}, {size_str} sent to model)")
    return "**Tool calls this request:** " + "; ".join(parts)


def _do_request(
    url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    headers: dict,
    request_info: str,
    timeout_seconds: int = _TIMEOUT,
) -> tuple[dict | None, str, bool]:
    """Perform one HTTP request. Returns (result_dict, error_message, success)."""
    body = {"model": model, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
    payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode()), "", True
    except (socket.timeout, TimeoutError):
        err = [
            "**SublimeAssistant – request timed out**",
            "",
            "**Request:**",
            request_info,
            "",
            f"The request to the AI backend timed out after {timeout_seconds} seconds. The model may be slow, overloaded, or still loading. Increase \"request_timeout\" in settings for long inputs or slow token gen.",
        ]
        return None, "\n".join(err), False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except Exception:
            body = "(could not read response body)"
        err = [
            "**SublimeAssistant – HTTP error**",
            "",
            "**Request:**",
            request_info,
            "",
            f"**Status:** {e.code} {e.reason}",
            "",
            "**Response body:**",
            body,
        ]
        return None, "\n".join(err), False
    except urllib.error.URLError as e:
        reason = str(e.reason or "").lower()
        if "timed out" in reason or "timeout" in reason:
            err = [
                "**SublimeAssistant – request timed out**",
                "",
                "**Request:**",
                request_info,
                "",
                f"The request to the AI backend timed out after {timeout_seconds} seconds. The model may be slow, overloaded, or still loading. Increase \"request_timeout\" in settings for long inputs or slow token gen.",
        ]
        else:
            err = [
                "**SublimeAssistant – connection error**",
                "",
                "**Request:**",
                request_info,
                "",
                f"**Error:** {e.reason}",
            ]
            if e.args:
                err.append("")
                err.append(str(e.args))
        return None, "\n".join(err), False
    except Exception as e:
        err = [
            "**SublimeAssistant – unexpected error**",
            "",
            "**Request:**",
            request_info,
            "",
            f"**Exception:** {type(e).__name__}: {e}",
            "",
            "**Traceback:**",
            traceback.format_exc(),
        ]
        return None, "\n".join(err), False


def call(
    url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    on_tool_call: Callable[[str, str], None] | None = None,
    on_read_file: Callable[[str], str | None] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[str, bool]:
    """Send messages to the API; if the model returns tool_calls, run them and continue. Returns (reply_text, success).
    If on_tool_call is set, it is called as on_tool_call(tool_name, url_or_args) before running fetch_url (so the UI can show 'Fetching ...').
    timeout_seconds: if set, overrides the default request timeout (useful for slow local models or long context)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = timeout_seconds if timeout_seconds is not None else _TIMEOUT
    current_messages = list(messages)
    rounds = 0
    tools_invoked: list[tuple[str, int]] = []  # (tool_name, result_size_chars)

    while rounds < _MAX_TOOL_ROUNDS:
        rounds += 1
        request_info = _format_request_info(url, model, current_messages)
        result, err_msg, ok = _do_request(
            url, api_key, model, current_messages, tools, headers, request_info, timeout_seconds=timeout
        )
        if not ok:
            summary = _format_tool_summary(tools_invoked)
            return err_msg + "\n\n" + summary, False

        # OpenAI/Mistral: choices[0].message
        choices = result.get("choices") if result else None
        if not choices:
            if msg := result.get("message"):
                return (msg.get("content") or ""), True
            return "Error: Unexpected API response format.", False

        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls")
        content = msg.get("content")

        if not tool_calls:
            text = (content or "").strip()
            return text if text else "Error: Empty assistant response.", True

        # Model requested tool calls: run them and continue
        assistant_msg = {"role": "assistant", "content": content}
        assistant_msg["tool_calls"] = tool_calls
        current_messages.append(assistant_msg)

        for tc in tool_calls:
            tc_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            if on_tool_call and name == "fetch_url":
                try:
                    fetch_url_arg = (json.loads(args).get("url") or "").strip()
                    if fetch_url_arg:
                        on_tool_call(name, fetch_url_arg)
                except Exception:
                    on_tool_call(name, args)
            if name == "read_file" and on_read_file:
                try:
                    fname = (json.loads(args).get("filename") or "").strip()
                    if on_tool_call and fname:
                        on_tool_call(name, fname)
                    content = on_read_file(fname) if fname else None
                    result_text = content if content is not None else f"File not found: {fname}"
                except Exception as e:
                    result_text = f"Error reading file: {e}"
            else:
                result_text = _run_tool(name, args)
            tools_invoked.append((name, len(result_text)))
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text,
            })

    summary = _format_tool_summary(tools_invoked)
    return "Error: Max tool rounds reached." + "\n\n" + summary, False


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"


def _openai_tools_to_claude(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-format tool definitions to Anthropic format."""
    result = []
    for t in tools:
        fn = t.get("function") or {}
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return result


def _openai_messages_to_claude(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-format messages into (system_prompt, claude_messages).
    Converts tool messages and assistant tool_calls to Claude content-block format."""
    system = ""
    claude_msgs: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            system = msg.get("content") or ""
            continue
        if role == "tool":
            claude_msgs.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })
            continue
        if role == "assistant":
            blocks: list[dict] = []
            text = msg.get("content")
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    inp = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": inp,
                })
            claude_msgs.append({"role": "assistant", "content": blocks or (msg.get("content") or "")})
            continue
        claude_msgs.append({"role": role, "content": msg.get("content") or ""})
    return system, claude_msgs


class APIClient(abc.ABC):
    """Abstract base class for AI API clients.

    Subclass this to support new backends (e.g. native Anthropic, Gemini, etc.).
    The two methods every backend must implement are :meth:`fetch_models` and :meth:`call`.
    """

    @abc.abstractmethod
    def fetch_models(self) -> tuple[list[str], str]:
        """Return (sorted_model_ids, error_message). Empty error means success."""
        ...

    @abc.abstractmethod
    def call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_tool_call: Callable[[str, str], None] | None = None,
        on_read_file: Callable[[str], str | None] | None = None,
    ) -> tuple[str, bool]:
        """Send *messages* to the backend and return (reply_text, success)."""
        ...


class OpenAIClient(APIClient):
    """Client for OpenAI-compatible endpoints (Ollama, Mistral, LM Studio, …)."""

    def __init__(self, url: str, api_key: str, model: str, timeout_seconds: int = _TIMEOUT) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def fetch_models(self) -> tuple[list[str], str]:
        return fetch_models(self.url, self.api_key)

    def call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_tool_call: Callable[[str, str], None] | None = None,
        on_read_file: Callable[[str], str | None] | None = None,
    ) -> tuple[str, bool]:
        return call(
            self.url,
            self.api_key,
            self.model,
            messages,
            tools=tools,
            on_tool_call=on_tool_call,
            on_read_file=on_read_file,
            timeout_seconds=self.timeout_seconds,
        )


class ClaudeClient(APIClient):
    """Client for the Anthropic Claude Messages API."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = _TIMEOUT) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def fetch_models(self) -> tuple[list[str], str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        req = urllib.request.Request(_CLAUDE_MODELS_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            models = [m["id"] for m in (data.get("data") or []) if m.get("id")]
            if not models:
                return [], f"No models returned by {_CLAUDE_MODELS_URL}"
            return sorted(models), ""
        except Exception as e:
            return [], f"Error fetching models from {_CLAUDE_MODELS_URL}: {e}"

    def call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_tool_call: Callable[[str, str], None] | None = None,
        on_read_file: Callable[[str], str | None] | None = None,
    ) -> tuple[str, bool]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        system, current_messages = _openai_messages_to_claude(messages)
        claude_tools = _openai_tools_to_claude(tools) if tools else None
        tools_invoked: list[tuple[str, int]] = []
        rounds = 0
        request_info = f"URL: {CLAUDE_API_URL}\nModel: {self.model}\nMessages: {len(messages)}"

        while rounds < _MAX_TOOL_ROUNDS:
            rounds += 1
            body: dict = {
                "model": self.model,
                "max_tokens": 8096,
                "messages": current_messages,
            }
            if system:
                body["system"] = system
            if claude_tools:
                body["tools"] = claude_tools

            payload = json.dumps(body).encode()
            req = urllib.request.Request(CLAUDE_API_URL, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode())
            except (socket.timeout, TimeoutError):
                summary = _format_tool_summary(tools_invoked)
                err = "\n".join([
                    "**SublimeAssistant – request timed out**", "",
                    "**Request:**", request_info, "",
                    f"The request timed out after {self.timeout_seconds} seconds.",
                ])
                return err + "\n\n" + summary, False
            except urllib.error.HTTPError as e:
                body_text = ""
                try:
                    body_text = e.read().decode()
                except Exception:
                    body_text = "(could not read response body)"
                summary = _format_tool_summary(tools_invoked)
                err = "\n".join([
                    "**SublimeAssistant – HTTP error**", "",
                    "**Request:**", request_info, "",
                    f"**Status:** {e.code} {e.reason}", "",
                    "**Response body:**", body_text,
                ])
                return err + "\n\n" + summary, False
            except Exception as e:
                summary = _format_tool_summary(tools_invoked)
                err = "\n".join([
                    "**SublimeAssistant – unexpected error**", "",
                    "**Request:**", request_info, "",
                    f"**Exception:** {type(e).__name__}: {e}", "",
                    "**Traceback:**", traceback.format_exc(),
                ])
                return err + "\n\n" + summary, False

            stop_reason = result.get("stop_reason")
            content_blocks = result.get("content") or []

            if stop_reason != "tool_use":
                text = " ".join(
                    b.get("text", "") for b in content_blocks if b.get("type") == "text"
                ).strip()
                return text if text else "Error: Empty assistant response.", True

            # Tool use round: append assistant turn, then run tools and send results
            current_messages.append({"role": "assistant", "content": content_blocks})
            tool_results = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                tool_use_id = block.get("id", "")
                inp = block.get("input") or {}
                if on_tool_call and name == "fetch_url":
                    url_arg = (inp.get("url") or "").strip()
                    if url_arg:
                        on_tool_call(name, url_arg)
                if name == "read_file" and on_read_file:
                    fname = (inp.get("filename") or "").strip()
                    if on_tool_call and fname:
                        on_tool_call(name, fname)
                    content = on_read_file(fname) if fname else None
                    result_text = content if content is not None else f"File not found: {fname}"
                else:
                    result_text = _run_tool(name, json.dumps(inp))
                tools_invoked.append((name, len(result_text)))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_text,
                })
            current_messages.append({"role": "user", "content": tool_results})

        summary = _format_tool_summary(tools_invoked)
        return "Error: Max tool rounds reached.\n\n" + summary, False
