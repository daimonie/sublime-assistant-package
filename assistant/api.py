"""OpenAI-compatible HTTP API client."""
from __future__ import annotations

import json
import traceback
import urllib.error
import urllib.request

_TIMEOUT = 30


def _format_request_info(url: str, model: str, messages: list[dict]) -> str:
    """Summary of what we sent (no sensitive content)."""
    parts = [f"URL: {url}", f"Model: {model}", f"Messages: {len(messages)}"]
    if messages:
        roles = [m.get("role", "?") for m in messages]
        parts.append(f"Roles: {', '.join(roles)}")
    return "\n".join(parts)


def call(url: str, api_key: str, model: str, messages: list[dict]) -> tuple[str, bool]:
    """Send messages to the API. Returns (reply_text, success)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    request_info = _format_request_info(url, model, messages)

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
            result = json.loads(response.read().decode())

        if choices := result.get("choices"):
            return choices[0]["message"]["content"], True
        if msg := result.get("message"):
            return msg["content"], True
        return "Error: Unexpected API response format.", False

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
        return "\n".join(err), False

    except urllib.error.URLError as e:
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
        return "\n".join(err), False

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
        return "\n".join(err), False
