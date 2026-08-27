"""Prompt-build -> Ollama call -> validate/retry -> neutralize pipeline (Sprint 04).

`classify_email()` is the single entry point `worker.py` calls per email. It returns:
- a `ClassificationResult` on success (including the "Dauerfehler" fallback verdict), or
- `None` if Ollama itself is unavailable (connectivity/timeout/model-missing) -- the caller must
  leave `classified_at` untouched in this case (graceful degradation, never a crash, never a
  false "classified" state).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import ValidationError

from ..security.sanitize import neutralize_untrusted
from ..storage.models import EmailMessage
from .client import OllamaClient, OllamaUnavailableError
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schema import MAX_SUMMARY_LEN, MAX_TAG_LEN, ClassificationResult

logger = logging.getLogger(__name__)

# JSON-parse/validation retries before giving up and returning the "Dauerfehler" fallback
# (per plan Risks: "JSON-Parsing kann fehlschlagen -> Validierung + Retry + bei Dauerfehler
# spam_verdict=unknown[->suspicious]"). Does NOT retry on `OllamaUnavailableError` -- a
# connectivity failure retrying in a tight loop would just hammer an already-down backend.
MAX_PARSE_RETRIES = 2


def _short_error_summary(err: Exception, max_len: int = 80) -> str:
    """
    Build a short, non-sensitive log summary for a parse/validation exception.

    Mirrors `sync_manager.py`/`idle_listener.py`'s `_short_error_reason` (Sprint 02): a
    `json.JSONDecodeError`/`pydantic.ValidationError`'s full `str()` can embed a fragment of the
    raw, untrusted model output -- which is itself derived from attacker-controlled email
    content -- so only the exception type plus a short, truncated message is ever logged, never
    the full text.
    """
    msg = " ".join(str(err).split())  # collapse whitespace/newlines
    if len(msg) > max_len:
        msg = msg[:max_len] + "..."
    return f"{type(err).__name__}: {msg}" if msg else type(err).__name__


def _extract_json_object(raw: str) -> str:
    """Best-effort extraction of the first top-level JSON object from a model response that may
    still be wrapped in markdown code fences despite `format=json`, e.g. some models emit
    ```json\n{...}\n```` even when told not to."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def classify_email(client: OllamaClient, email_msg: EmailMessage) -> Optional[ClassificationResult]:
    """Classify one email. See module docstring for the `None` (unavailable) contract."""
    user_prompt = build_user_prompt(
        subject=email_msg.subject,
        sender=email_msg.sender_str,
        body_markdown=email_msg.body_markdown or email_msg.body_text or "",
        hidden_text_present=bool(email_msg.hidden_text),
        trust_level=email_msg.trust_level,
        spam_score=email_msg.spam_score,
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_PARSE_RETRIES + 1):
        try:
            raw = client.generate_json(SYSTEM_PROMPT, user_prompt)
        except OllamaUnavailableError:
            # Connectivity/model-missing failure: propagate as "unavailable", never as a
            # fallback classification -- the mail must stay unclassified for a later retry once
            # Ollama is back, not get stuck with a permanent "suspicious/unknown" verdict.
            return None

        try:
            data = json.loads(_extract_json_object(raw))
            result = ClassificationResult.model_validate(data)
            break
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            last_error = e
            # Security review fix (Sprint 04 finalization, FIX A): never log the exception's
            # full message here -- a ValidationError's/JSONDecodeError's `str()` can embed a
            # fragment of the raw, untrusted model output (itself derived from attacker-
            # controlled email content). Log only the exception type + a short, truncated
            # summary, consistent with the `ingest_log` fix in Sprint 02 (`_short_error_reason`
            # in sync_manager.py/idle_listener.py).
            logger.warning(
                "Ollama classifier response failed validation (attempt %d/%d): %s",
                attempt,
                MAX_PARSE_RETRIES,
                _short_error_summary(e),
            )
            result = None
    else:
        result = None

    if result is None:
        # Dauerfehler: never a silent loss -- the mail still gets marked classified with a
        # conservative fallback verdict so it surfaces for human review instead of vanishing
        # from the classification queue forever.
        logger.error(
            "Ollama classifier giving up after %d attempts, using fallback verdict: %s",
            MAX_PARSE_RETRIES,
            _short_error_summary(last_error) if last_error else "unknown",
        )
        return ClassificationResult.fallback()

    # The model's own output is untrusted text too: it could have been steered (via prompt
    # injection in the source email) into emitting an injection payload inside `summary`/`tags`.
    # Neutralize before this ever reaches storage/vault/MCP.
    result.summary = neutralize_untrusted(result.summary, max_len=MAX_SUMMARY_LEN)
    result.tags = [neutralize_untrusted(t, max_len=MAX_TAG_LEN) for t in result.tags if t]

    return result
