"""Minimal chat-model client: a :class:`ChatClient` protocol and an OpenAI-compatible client.

:class:`OpenAICompatibleClient` POSTs to ``<base_url>/chat/completions`` with ``urllib`` (stdlib).
The default endpoint is a local Ollama server. Anything sent is sent to that endpoint, so callers
must pass already-redacted text. The API key, if any, is read from the environment variable the
caller names (never from a flag), sent only as an unredirected ``Authorization`` header, and is
never put in logs, ``repr`` or error messages.

Note: Ollama's default context window is small and can silently truncate an oversized prompt;
size windows (and the server's context setting) accordingly.
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

__all__ = ["ChatClient", "ModelClientError", "OpenAICompatibleClient"]

DEFAULT_BASE_URL = "http://localhost:11434/v1"


class ModelClientError(Exception):
    """A model call failed. The message holds a status and a fixed short reason only."""


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Send chat ``messages`` (``{"role", "content"}``) and return the reply text."""
        ...


class OpenAICompatibleClient:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str | None = None,
        timeout: float = 120.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model!r})"

    def chat(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.api_key_env:
            key = (os.environ.get(self.api_key_env) or "").strip()
            if not key:
                raise ModelClientError(f"environment variable {self.api_key_env} is not set")
            if any(ord(c) < 32 or ord(c) == 127 for c in key):
                raise ModelClientError(
                    f"environment variable {self.api_key_env} contains control characters"
                )
            # Unredirected: urllib's redirect handler must never copy the key to another host.
            request.add_unredirected_header("Authorization", f"Bearer {key}")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ModelClientError(f"HTTP {exc.code}: request failed") from None
        except TimeoutError:
            raise ModelClientError("request timed out") from None
        except (urllib.error.URLError, OSError):
            raise ModelClientError("could not reach the model endpoint") from None
        except (ValueError, http.client.HTTPException):
            # e.g. an invalid header value (its text would echo the key) or IncompleteRead.
            raise ModelClientError("request could not be completed") from None
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelClientError("response was not a chat completion") from None
        if not isinstance(content, str):
            raise ModelClientError("response had no text content")
        return content
