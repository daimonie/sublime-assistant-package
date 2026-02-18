"""OpenAI-compatible HTTP API client."""
from __future__ import annotations

import json
import re
import socket
import traceback
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Callable

_TIMEOUT = 30
_FETCH_TIMEOUT = 30
_MAX_TOOL_ROUNDS = 5
# Fetched page content is truncated to this many characters. Devstral has 384k context; if you see
# "truncating input prompt" in Ollama logs, increase Ollama's context (e.g. OLLAMA_NUM_CTX=32768) or lower this.
_MAX_FETCH_CHARS = 80_000


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


def _strip_html(html: str) -> str:
    """Extract plain text from HTML using stdlib only."""
    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.text: list[str] = []

        def handle_data(self, data: str) -> None:
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


def _fetch_url(url: str) -> tuple[str, bool]:
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
        content, ok = _fetch_url(url)
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
    timeout_seconds: int | None = None,
) -> tuple[str, bool]:
    """Send messages to the API; if the model returns tool_calls, run them and continue. Returns (reply_text, success).
    If on_tool_call is set, it is called as on_tool_call(tool_name, url_or_args) before running fetch_url (so the UI can show 'Fetching ...').
    timeout_seconds: if set, overrides the default request timeout (useful for slow local models or long context)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = timeout_seconds if timeout_seconds is not None else _TIMEOUT
    request_info = _format_request_info(url, model, messages)
    current_messages = list(messages)
    rounds = 0
    tools_invoked: list[tuple[str, int]] = []  # (tool_name, result_size_chars)

    while rounds < _MAX_TOOL_ROUNDS:
        rounds += 1
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
                    url = (json.loads(args).get("url") or "").strip()
                    if url:
                        on_tool_call(name, url)
                except Exception:
                    on_tool_call(name, args)
            result_text = _run_tool(name, args)
            tools_invoked.append((name, len(result_text)))
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text,
            })

    summary = _format_tool_summary(tools_invoked)
    return "Error: Max tool rounds reached." + "\n\n" + summary, False
