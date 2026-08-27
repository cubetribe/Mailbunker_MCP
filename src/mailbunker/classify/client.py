"""Thin HTTP wrapper around a local Ollama instance (Sprint 04).

Deliberately built on `httpx` directly against Ollama's plain HTTP API (`/api/chat`), not the
`ollama` pip package -- that keeps the runtime dependency to a library already present
transitively (via `mcp`/`fastmcp`) instead of adding a new hard dependency for an optional
feature.

Every network/availability failure is caught here and re-raised as `OllamaUnavailableError`, a
single well-known exception type the caller (`classifier.py`) can catch to distinguish
"Ollama isn't there" (graceful degradation, mail stays unclassified) from "Ollama answered but
the JSON was garbage" (retry/fallback territory, mail still gets marked classified).
"""
from __future__ import annotations

import httpx


class OllamaUnavailableError(Exception):
    """Raised when Ollama cannot be reached, times out, or returns a request-level error
    (missing model, server error, ...). Never raised for a merely malformed/invalid JSON body in
    an otherwise-successful response -- that is the caller's parse/retry concern."""


class OllamaClient:
    """Minimal synchronous client for Ollama's `/api/chat` endpoint with `format=json`."""

    def __init__(self, host: str, model: str, timeout: float = 60.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate_json(self, system_prompt: str, user_prompt: str) -> str:
        """Call Ollama and return the raw JSON string produced by the model.

        Raises `OllamaUnavailableError` for any connectivity/timeout/HTTP-status/malformed-
        response-envelope failure. Does NOT validate that the returned string is itself valid
        JSON matching our schema -- that is `classifier.py`'s job (it needs to distinguish a
        parse failure worth retrying from an outright unavailable backend).
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            raise OllamaUnavailableError(f"Ollama request failed: {e}") from e
        except ValueError as e:  # response body wasn't valid JSON at the envelope level
            raise OllamaUnavailableError(f"Ollama returned a non-JSON envelope: {e}") from e

        content = (data.get("message") or {}).get("content")
        if not content:
            raise OllamaUnavailableError("Ollama response contained no message content")

        return content

    def is_reachable(self) -> bool:
        """Best-effort liveness check (used by the CLI for a clean early "Ollama offline"
        message before attempting a whole batch). Never raises."""
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
