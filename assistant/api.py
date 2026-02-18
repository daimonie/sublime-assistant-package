"""OpenAI-compatible HTTP API client."""
from __future__ import annotations

import json
import urllib.request

_TIMEOUT = 30


def call(url: str, api_key: str, model: str, messages: list[dict]) -> tuple[str, bool]:
    """Send messages to the API. Returns (reply_text, success)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
            result = json.loads(response.read().decode())

        if choices := result.get("choices"):
            return choices[0]["message"]["content"], True
        if msg := result.get("message"):
            return msg["content"], True
        return "Error: Unexpected API response format.", False

    except Exception as e:
        return f"Error connecting to AI: {e}", False
