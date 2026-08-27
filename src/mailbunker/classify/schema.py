"""Structured classification schema (Sprint 04).

`ClassificationResult` is the single source of truth for what a successful classifier run
produces -- pydantic-validated so a malformed/hallucinated field from the model is caught before
it ever reaches storage or the vault, not "trusted because it parsed as JSON".
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator

SPAM_VERDICTS = ("ham", "spam", "phishing", "suspicious")

# "unknown" is a fallback-only value (never produced by a successful, schema-conforming model
# response) used when classification fails validation/parsing after retries but the mail must
# still be marked classified rather than silently lost forever -- see
# `classifier.py::classify_email`'s "Dauerfehler" fallback path.
CATEGORIES = (
    "personal",
    "business",
    "transactional",
    "notification",
    "newsletter",
    "marketing",
    "social",
    "automated",
    "unknown",
)

PRIORITIES = ("high", "normal", "low")

FALLBACK_SPAM_VERDICT = "suspicious"
FALLBACK_CATEGORY = "unknown"
FALLBACK_PRIORITY = "normal"

MAX_TAGS = 10
MAX_TAG_LEN = 64
MAX_SUMMARY_LEN = 500


class ClassificationResult(BaseModel):
    """One email's classifier verdict. See plans/v0.2.0/sprint-04-ollama-classifier.md for the
    schema this mirrors exactly."""

    spam_verdict: str = Field(default=FALLBACK_SPAM_VERDICT)
    category: str = Field(default=FALLBACK_CATEGORY)
    priority: str = Field(default=FALLBACK_PRIORITY)
    language: str = Field(default="unknown")
    tags: List[str] = Field(default_factory=list)
    summary: str = Field(default="")
    confidence: float = Field(default=0.0)

    @field_validator("spam_verdict")
    @classmethod
    def _validate_spam_verdict(cls, v: str) -> str:
        v_norm = (v or "").strip().lower()
        return v_norm if v_norm in SPAM_VERDICTS else FALLBACK_SPAM_VERDICT

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        v_norm = (v or "").strip().lower()
        return v_norm if v_norm in CATEGORIES else FALLBACK_CATEGORY

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: str) -> str:
        v_norm = (v or "").strip().lower()
        return v_norm if v_norm in PRIORITIES else FALLBACK_PRIORITY

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        v_norm = (v or "").strip().lower()
        return v_norm[:10] if v_norm else "unknown"

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @field_validator("tags")
    @classmethod
    def _limit_tags(cls, v: List[str]) -> List[str]:
        cleaned = [str(t).strip() for t in (v or []) if str(t).strip()]
        return cleaned[:MAX_TAGS]

    @field_validator("summary")
    @classmethod
    def _limit_summary(cls, v: str) -> str:
        return (v or "")[:MAX_SUMMARY_LEN]

    @classmethod
    def fallback(cls) -> "ClassificationResult":
        """Dauerfehler fallback (per plan's "Risks" section): never a silent loss. Used when the
        model's JSON output cannot be parsed/validated even after retries, or when the model
        response is empty/malformed in a way that isn't a plain connectivity failure."""
        return cls(
            spam_verdict=FALLBACK_SPAM_VERDICT,
            category=FALLBACK_CATEGORY,
            priority=FALLBACK_PRIORITY,
            language="unknown",
            tags=[],
            summary="",
            confidence=0.0,
        )
